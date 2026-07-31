"""Acheron CLI — command-line interface for the audio-transformation pipeline."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import math
import os
import re
import ssl
import sys
import time
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

import click
import httpx
from rich.console import Console
from rich.live import Live
from rich.table import Table

from acheron.api_client import AcheronClient
from acheron.core.models import CostBasis, PlanStatus
from acheron.tls import resolve_ca_path

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from acheron.core.schemas import PlanResponse

console = Console()
err_console = Console(stderr=True)

_SOURCE_TYPE_MAP: dict[str, str] = {
    "epub": "epub",
    "mp3": "audio",
    "wav": "audio",
    "flac": "audio",
    "ogg": "audio",
    "m4a": "audio",
}

_DEFAULT_BASE_URL = "https://localhost:8000"
_SECONDS_PER_MINUTE = 60
_MINUTES_PER_HOUR = 60
_HOURS_PER_DAY = 24


class _RemoteErrorType(StrEnum):
    """Domain errors returned by the orchestrator API."""

    INVALID_LANGUAGE_PATH = "InvalidLanguagePathError"
    CHUNKING_TOO_LONG = "ChunkingTooLongForWorkerError"
    JOB_ALREADY_RUNNING = "JobAlreadyRunningError"
    JOB_NOT_CANCELLABLE = "JobNotCancellableError"
    JOB_NOT_FOUND = "JobNotFoundError"


def _resolve_trust_store() -> bool | str:
    """Pick the trust store for httpx.

    Order: Acheron-specific env var, then the standard ``SSL_CERT_FILE``
    honored by httpx/stdlib ssl, then the dev CA at ``./certs/acheron-ca.crt``
    (host-side dev convenience), then the system trust store (``True``).
    """
    explicit = resolve_ca_path()
    if explicit:
        return explicit
    dev_ca = Path.cwd() / "certs" / "acheron-ca.crt"
    if dev_ca.is_file():
        return str(dev_ca)
    return True


def _get_client() -> AcheronClient:
    """Build the orchestrator HTTP client.

    The default scheme is HTTPS to match the dev/HTTPS orchestrator (compose
    sets ``ACHERON_TLS_CERT_FILE``). Callers can override the URL with
    ``ACHERON_URL``. Trust store resolution lives in :func:`_resolve_trust_store`.
    ``ACHERON_REGISTRATION_TOKEN`` authorizes normal mutating requests, while
    ``ACHERON_ADMIN_TOKEN`` authorizes operator-only administrative requests.
    """
    base_url = os.environ.get("ACHERON_URL", _DEFAULT_BASE_URL)
    registration_token = os.environ.get("ACHERON_REGISTRATION_TOKEN")
    admin_token = os.environ.get("ACHERON_ADMIN_TOKEN")
    return AcheronClient(
        base_url,
        verify=_resolve_trust_store(),
        registration_token=registration_token,
        admin_token=admin_token,
    )


def _sanitize_attempted_url(url: str | httpx.URL | None) -> str:
    """Return a display-safe URL containing only scheme, authority, and path."""
    if url is None:
        return "<unknown>"
    try:
        parsed = httpx.URL(str(url))
    except TypeError, ValueError:
        return "<unknown>"
    if not parsed.scheme or not parsed.host:
        return "<unknown>"
    authority = parsed.host
    if parsed.port is not None:
        authority = f"{authority}:{parsed.port}"
    return f"{parsed.scheme}://{authority}{parsed.path or '/'}"


_URL_PATTERN = re.compile(r"""https?://[^\s'"]+""")


def _sanitize_exception_text(text: str) -> str:
    return _URL_PATTERN.sub(lambda match: _sanitize_attempted_url(match.group(0)), text)


def _client_base_url(client: AcheronClient | None) -> str:
    candidate = getattr(client, "_base_url", None)
    if isinstance(candidate, str):
        return candidate
    return os.environ.get("ACHERON_URL", _DEFAULT_BASE_URL)


def _exception_url(exc: BaseException) -> str | httpx.URL | None:
    try:
        request = object.__getattribute__(exc, "request")
    except AttributeError, RuntimeError:
        return None
    try:
        url = request.url
    except AttributeError, RuntimeError:
        return None
    return url if isinstance(url, (str, httpx.URL)) else None


def _request_id(client: AcheronClient | None) -> str | None:
    value = getattr(client, "last_request_id", None)
    return value if isinstance(value, str) else None


def _print_request_id(client: AcheronClient | None) -> None:
    request_id = _request_id(client)
    if request_id is not None:
        err_console.print(f"request_id={request_id}")


def _http_error_suffix(exc: httpx.HTTPStatusError) -> str:
    attempted = _exception_url(exc)
    if attempted is None:
        try:
            attempted = exc.response.url
        except RuntimeError:
            attempted = None
    return f" (from {_sanitize_attempted_url(attempted)}) — verify ACHERON_URL"


def _run[T](
    coro: Coroutine[Any, Any, T],
    *,
    client: AcheronClient | None = None,
    on_http_error: Callable[[httpx.HTTPStatusError], None] | None = None,
) -> T:
    # When called from an async test (a loop is already running), ``asyncio.run``
    # would fail because it creates a new loop. We run the coroutine in a
    # worker thread that has its own loop. Note: background tasks the
    # coroutine schedules (e.g. orchestrator._execute via submit_job) live on
    # the worker's loop and are cancelled when ``asyncio.run`` returns. That
    # is acceptable here because the CLI is sync; end-to-end execution is
    # verified separately by async tests against the orchestrator API.
    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    except httpx.ConnectError as exc:
        url = _sanitize_attempted_url(_exception_url(exc) or _client_base_url(client))
        if _is_ssl_error(exc):
            console.print(f"[red]TLS verification failed connecting to {url}[/red]")
            console.print("Trust the dev CA via SSL_CERT_FILE=$PWD/certs/acheron-ca.crt")
            console.print("or set ACHERON_TLS_CA_FILE=/path/to/ca.crt (or http:// URL for plain HTTP).")
        else:
            console.print(f"[red]Cannot connect to Acheron at {url}[/red]")
            console.print("Is the server running? Check with: [bold]docker compose ps[/bold]")
        _print_request_id(client)
        raise SystemExit(1) from None
    except httpx.TimeoutException as exc:
        url = _sanitize_attempted_url(_exception_url(exc) or _client_base_url(client))
        console.print(f"[red]Request to Acheron timed out at {url}[/red]")
        console.print("Is the server running? Check with: [bold]docker compose ps[/bold]")
        _print_request_id(client)
        raise SystemExit(1) from None
    except httpx.HTTPStatusError as exc:
        if on_http_error is None:
            _print_http_error(exc)
        else:
            on_http_error(exc)
        _print_request_id(client)
        raise SystemExit(1) from exc
    except Exception as exc:  # noqa: BLE001
        url = _sanitize_attempted_url(_exception_url(exc) or _client_base_url(client))
        detail = _sanitize_exception_text(str(exc)) or "request failed"
        console.print(f"[red]Request failed at {url}: {detail}[/red]")
        _print_request_id(client)
        raise SystemExit(1) from None


def _drain_sync_generator[T](
    next_event: Callable[[], T],
    *,
    client: AcheronClient | None,
    request_id_printed: Callable[[], bool] | None,
) -> Iterator[T]:
    while True:
        try:
            event = next_event()
        except StopAsyncIteration:
            return
        except Exception as exc:  # noqa: BLE001
            _print_stream_error(
                exc,
                client=client,
                print_request_id=request_id_printed is None or not request_id_printed(),
            )
            raise SystemExit(1) from None
        yield event


def _run_sync_generator[T](
    async_gen: AsyncIterator[T],
    *,
    client: AcheronClient | None = None,
    request_id_printed: Callable[[], bool] | None = None,
) -> Iterator[T]:
    """Drain an async generator synchronously via a thread pool."""

    def _next(ait: AsyncIterator[T]) -> T:
        coro = ait.__anext__()
        return asyncio.run(coro)  # type: ignore[arg-type]

    async_iter = async_gen.__aiter__()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            yield from _drain_sync_generator(
                lambda: loop.run_until_complete(async_iter.__anext__()),
                client=client,
                request_id_printed=request_id_printed,
            )
        finally:
            loop.close()
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            yield from _drain_sync_generator(
                lambda: pool.submit(_next, async_iter).result(),
                client=client,
                request_id_printed=request_id_printed,
            )


def _print_stream_error(
    exc: BaseException,
    *,
    client: AcheronClient | None,
    print_request_id: bool = True,
) -> None:
    if isinstance(exc, httpx.HTTPStatusError):
        _print_http_error(exc)
    elif isinstance(exc, httpx.ConnectError):
        url = _sanitize_attempted_url(_exception_url(exc) or _client_base_url(client))
        console.print(f"[red]Cannot connect to Acheron at {url}[/red]")
    elif isinstance(exc, httpx.TimeoutException):
        url = _sanitize_attempted_url(_exception_url(exc) or _client_base_url(client))
        console.print(f"[red]Request to Acheron timed out at {url}[/red]")
    else:
        url = _sanitize_attempted_url(_exception_url(exc) or _client_base_url(client))
        detail = _sanitize_exception_text(str(exc)) or "stream protocol failure"
        console.print(f"[red]Streaming request failed at {url}: {detail}[/red]")
    if print_request_id:
        _print_request_id(client)


def _http_error_detail(exc: httpx.HTTPStatusError) -> str:
    fallback = _sanitize_exception_text(str(exc))
    if not exc.response.headers.get("content-type", "").startswith("application/json"):
        return fallback
    try:
        payload = exc.response.json()
    except ValueError:
        return fallback
    if not isinstance(payload, dict):
        return fallback
    detail = payload.get("detail", fallback)
    if isinstance(detail, dict):
        error_type = detail.get("type")
        message = detail.get("message")
        if isinstance(error_type, str) and isinstance(message, str):
            return _sanitize_exception_text(f"{error_type}: {message}")
        if isinstance(message, str):
            return _sanitize_exception_text(message)
    return _sanitize_exception_text(detail if isinstance(detail, str) else str(detail))


def _http_error_remediation(exc: httpx.HTTPStatusError) -> str | None:
    """Extract an optional structured remediation from an HTTP error."""
    try:
        payload = exc.response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("detail"), dict):
        return None
    remediation = payload["detail"].get("remediation")
    return _sanitize_exception_text(remediation) if isinstance(remediation, str) else None


def _parse_remote_error(detail: str) -> tuple[_RemoteErrorType | None, str]:
    error_name, separator, message = detail.partition(": ")
    try:
        error_type = _RemoteErrorType(error_name) if separator else None
    except ValueError:
        if separator and error_name.endswith("Error"):
            return None, message
        if error_name.endswith("Error"):
            return None, "The orchestrator returned an unspecified domain error."
        return None, detail
    if not separator and error_name.endswith("Error"):
        return None, "The orchestrator returned an unspecified domain error."
    return error_type, message if separator else detail


def _format_estimated_cost(cost: float | None, basis: CostBasis | None) -> str:
    """Render an execution-time estimate without implying invoice precision."""
    if cost is None or basis is None or basis is CostBasis.UNKNOWN:
        return "unknown"
    return f"${cost:.2f}"


def _print_http_error(exc: httpx.HTTPStatusError) -> None:
    console.print(f"[red]Error {exc.response.status_code}: {_http_error_detail(exc)}{_http_error_suffix(exc)}[/red]")


def _print_submit_http_error(
    exc: httpx.HTTPStatusError,
    *,
    client: AcheronClient,
    source_language: str,
    target_language: str,
) -> None:
    error_type, message = _parse_remote_error(_http_error_detail(exc))

    match error_type:
        case _RemoteErrorType.INVALID_LANGUAGE_PATH:
            console.print(
                f"[red]No worker can translate {source_language}→{target_language}{_http_error_suffix(exc)}[/red]"
            )
            try:
                pairs = _run(client.get_capabilities(src=source_language), client=client)
            except SystemExit, ValueError:
                console.print(f"Run [bold]acheron capabilities --src {source_language}[/bold] to see the full list.")
                return
            targets = sorted({pair.dst for pair in pairs if pair.src == source_language})
            supported = ", ".join(targets) or "none"
            console.print(f"Supported targets from {source_language}: {supported}")
            console.print(f"Run [bold]acheron capabilities --src {source_language}[/bold] to see the full list.")
        case _RemoteErrorType.CHUNKING_TOO_LONG:
            console.print(f"[red]Job cannot be submitted: {message}{_http_error_suffix(exc)}[/red]")
            console.print("Reduce the input size or configure a worker with a larger token limit.")
        case _:
            console.print(
                f"[red]Error {exc.response.status_code}: Job submission failed: {message}"
                f"{_http_error_suffix(exc)}[/red]"
            )
            console.print("Check the input and worker capabilities, then retry.")


def _print_resume_http_error(exc: httpx.HTTPStatusError, *, job_id: str) -> None:
    error_type, message = _parse_remote_error(_http_error_detail(exc))
    remediation = _http_error_remediation(exc)

    match error_type:
        case _RemoteErrorType.JOB_ALREADY_RUNNING:
            console.print(f"[red]Job {job_id} is already running.{_http_error_suffix(exc)}[/red]")
        case _RemoteErrorType.JOB_NOT_FOUND:
            console.print(f"[red]Job {job_id} was not found.{_http_error_suffix(exc)}[/red]")
        case _:
            console.print(
                f"[red]Error {exc.response.status_code}: Job resume failed: {message}{_http_error_suffix(exc)}[/red]"
            )
    if remediation:
        console.print(f"Try: {remediation}")
    elif error_type is _RemoteErrorType.JOB_ALREADY_RUNNING:
        console.print(f"Try: acheron job cancel {job_id}")
    elif error_type is _RemoteErrorType.JOB_NOT_FOUND:
        console.print("Try: acheron jobs")
    else:
        console.print(f"Run [bold]acheron job status {job_id}[/bold] to inspect the job before retrying.")


def _print_cancel_http_error(exc: httpx.HTTPStatusError, *, job_id: str) -> None:
    """Render a structured cancellation error and its remediation."""
    message = _http_error_detail(exc)
    remediation: str | None = None
    try:
        payload = exc.response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("detail"), dict):
        detail = payload["detail"]
        message = _sanitize_exception_text(str(detail.get("message", message)))
        candidate = detail.get("remediation")
        remediation = _sanitize_exception_text(candidate) if isinstance(candidate, str) else None
    console.print(
        f"[red]Error {exc.response.status_code}: Job cancellation failed for {job_id}: "
        f"{message}{_http_error_suffix(exc)}[/red]"
    )
    if remediation:
        console.print(f"Try: {remediation}")


def _print_retry_http_error(exc: httpx.HTTPStatusError, *, job_id: str) -> None:
    """Render a structured retry error and its remediation."""
    message = _http_error_detail(exc)
    remediation: str | None = None
    try:
        payload = exc.response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("detail"), dict):
        detail = payload["detail"]
        message = _sanitize_exception_text(str(detail.get("message", message)))
        candidate = detail.get("remediation")
        remediation = _sanitize_exception_text(candidate) if isinstance(candidate, str) else None
    console.print(
        f"[red]Error {exc.response.status_code}: Job retry failed for {job_id}: "
        f"{message}{_http_error_suffix(exc)}[/red]"
    )
    if remediation:
        console.print(f"Try: {remediation}")


def _parse_datetime_or_duration(value: str) -> datetime:
    """Parse an ISO-8601 timestamp or a relative duration from now."""
    text = value.strip()
    try:
        seconds = _parse_duration_seconds(text)
    except click.BadParameter:
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise click.BadParameter("expected an ISO-8601 timestamp or duration such as 24h") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise click.BadParameter("ISO-8601 timestamps must include a timezone") from None
        return parsed.astimezone(UTC)
    return datetime.now(UTC) - timedelta(seconds=seconds)


def _format_age(timestamp: datetime) -> str:
    """Render a bounded human-readable age from a lifecycle timestamp."""
    seconds = max(0.0, (datetime.now(UTC) - timestamp).total_seconds())
    if seconds < _SECONDS_PER_MINUTE:
        return f"{seconds:.0f}s"
    minutes = seconds / _SECONDS_PER_MINUTE
    if minutes < _MINUTES_PER_HOUR:
        return f"{minutes:.0f}m"
    hours = minutes / _MINUTES_PER_HOUR
    if hours < _HOURS_PER_DAY:
        return f"{hours:.1f}h"
    return f"{hours / _HOURS_PER_DAY:.1f}d"


def _parse_duration_seconds(value: str) -> float:
    """Parse an operator duration such as ``60s`` or ``5m``."""
    text = value.strip().lower()
    if not text:
        raise click.BadParameter("duration must not be empty")
    multiplier = 1.0
    if text[-1] in {"s", "m", "h", "d"}:
        multiplier = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}[text[-1]]
        text = text[:-1]
    try:
        seconds = float(text) * multiplier
    except ValueError as exc:
        raise click.BadParameter("duration must be a number with optional s/m/h/d suffix") from exc
    if seconds < 0 or not math.isfinite(seconds):
        raise click.BadParameter("duration must be finite and non-negative")
    return seconds


def _require_admin_token() -> None:
    if not os.environ.get("ACHERON_ADMIN_TOKEN"):
        raise click.ClickException(
            "ACHERON_ADMIN_TOKEN is required for this command; set it to the configured operator token."
        )


def _detect_source_type(path: str) -> str | None:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return _SOURCE_TYPE_MAP.get(ext)


def _print_plan(plan: PlanResponse, *, dry_run: bool = False) -> None:
    """Render a PlanResponse via Rich. Uses only public fields; never prints step payloads."""
    title = "Plan preview" if dry_run else "Plan"
    console.print(f"{title}: [bold]{plan.plan_id}[/bold]")
    console.print(f"Job: {plan.job_id}")
    console.print(f"Input: {plan.source_type} ({plan.source_language} → {plan.target_language})")
    console.print(f"Strategy: {plan.executor_strategy.value}")
    table = Table(title="Steps")
    table.add_column("Step")
    table.add_column("Worker type")
    table.add_column("Depends on")
    table.add_column("Status")
    for step in plan.steps:
        table.add_row(step.step_id, step.worker_type.value, ", ".join(step.depends_on) or "-", step.status.value)
    console.print(table)
    if dry_run:
        console.print("Dry run complete; no job submitted.")


def _is_ssl_error(exc: BaseException) -> bool:
    """True if the failure happened during TLS verification.

    httpx wraps both transport-level and TLS-level failures as
    ``ConnectError``; we walk the exception chain looking for any
    ``ssl.SSLError`` to tell the user the right thing.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLError):
            return True
        current = current.__cause__ or current.__context__
    return False


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging to stderr")
def main(verbose: bool) -> None:  # noqa: FBT001
    """Acheron — distributed audio-transformation pipeline."""
    if verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
            stream=sys.stderr,
        )
    else:
        logging.basicConfig(level=logging.WARNING)


@main.group()
def job() -> None:
    """Manage jobs."""


@job.command("archive")
@click.argument("job_ids", nargs=-1, required=True)
def archive_jobs(job_ids: tuple[str, ...]) -> None:
    """Archive terminal jobs without deleting their records."""
    _require_admin_token()
    client = _get_client()
    for job_id in job_ids:
        result = _run(client.archive_job(job_id), client=client, on_http_error=_print_http_error)
        archived_at = result.archived_at.isoformat() if result.archived_at is not None else "unknown"
        input_data = f"{result.source_type} {result.source_language}->{result.target_language}"
        if result.asr_model:
            input_data += f" asr={result.asr_model}"
        output_data = (
            ", ".join(
                f"{output.filename} ({output.size_bytes} bytes, {output.content_type})" for output in result.outputs
            )
            or "-"
        )
        cost_basis = result.total_cost_basis.value if result.total_cost_basis is not None else "unknown"
        console.print(
            f"job={result.job_id} status={result.status.value} archived at={archived_at} "
            f"record preserved (plan={result.plan_id or '-'}, input={input_data}, "
            f"outputs={len(result.outputs)} ({output_data}), "
            f"cost={_format_estimated_cost(result.total_cost, result.total_cost_basis)} basis={cost_basis})"
        )


@main.command()
@click.option("--keep-successful", required=True)
@click.option("--keep-failed", required=True)
@click.option("--apply", is_flag=True)
def cleanup(keep_successful: str, keep_failed: str, apply: bool) -> None:  # noqa: FBT001
    """Preview or apply terminal-job retention cleanup."""
    _require_admin_token()
    client = _get_client()
    result = _run(
        client.cleanup(
            keep_successful_seconds=_parse_duration_seconds(keep_successful),
            keep_failed_seconds=_parse_duration_seconds(keep_failed),
            apply=apply,
        ),
        client=client,
        on_http_error=_print_http_error,
    )
    mode = "applied" if result.apply else "preview"
    console.print(
        f"cleanup={mode} candidates={len(result.candidates)} deleted={result.deleted_count} "
        f"deleted_bytes={result.deleted_bytes} reclaimable_bytes={result.reclaimable_bytes}"
    )
    for candidate in result.candidates:
        console.print(
            f"job={candidate.job_id} status={candidate.status} "
            f"bytes={candidate.reclaimable_bytes} paths={','.join(candidate.relative_paths) or '-'}"
        )
    for failure in result.failures:
        console.print(f"failure job={failure.job_id}: {failure.message}")


@main.group()
def admin() -> None:
    """Perform operator-only recovery actions."""


@admin.command("reap-stuck")
@click.option("--older-than", required=True)
@click.option("--reason", required=True)
def reap_stuck(older_than: str, reason: str) -> None:
    """Mark persisted running jobs stale and failed."""
    _require_admin_token()
    seconds = _parse_duration_seconds(older_than)
    client = _get_client()
    result = _run(
        client.reap_stale_jobs(older_than_seconds=seconds, reason=reason),
        client=client,
        on_http_error=_print_http_error,
    )
    console.print(f"reaped={result.reaped}")
    for job_id in result.job_ids:
        console.print(job_id)


@job.command()
@click.argument("file", type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path))
@click.option("--src", required=True, help="Source language (ISO 639-1)")
@click.option("--dest", required=True, help="Target language (ISO 639-1)")
@click.option("--executor", default="streaming", show_default=True, help="Executor strategy")
@click.option("--asr", "asr_model", default=None, help="ASR model (for audio input)")
@click.option("--type", "source_type", default=None, help="Source type override (epub/audio)")
@click.option("--dry-run", is_flag=True, help="Preview the plan without submitting a job")
@click.option("--follow", is_flag=True, help="Watch the job after submission")
def submit(  # noqa: PLR0913
    file: Path,
    src: str,
    dest: str,
    executor: str,
    asr_model: str | None,
    source_type: str | None,
    dry_run: bool,  # noqa: FBT001
    follow: bool,  # noqa: FBT001
) -> None:
    """Submit a new job for processing."""
    file_str = str(file)
    if source_type is None:
        source_type = _detect_source_type(file_str)
        if source_type is None:
            console.print(f"[red]Cannot detect source type from '{file}'. Use --type.[/red]")
            raise SystemExit(1)

    # Upload the local source first; the orchestrator only knows about
    # server-relative paths. The original filename is preserved by the
    # upload so the orchestrator picks the right content type / suffix.
    client = _get_client()
    uploaded = _run(
        client.upload_input(file),
        client=client,
        on_http_error=_print_http_error,
    )

    if dry_run:
        preview = _run(
            client.preview_job(
                source_type=source_type,
                source_path=uploaded.source_path,
                source_language=src,
                target_language=dest,
                executor_strategy=executor,
                asr_model=asr_model,
            ),
            client=client,
            on_http_error=lambda exc: _print_submit_http_error(
                exc,
                client=client,
                source_language=src,
                target_language=dest,
            ),
        )
        _print_plan(preview, dry_run=True)
        return

    result = _run(
        client.submit_job(
            source_type=source_type,
            source_path=uploaded.source_path,
            source_language=src,
            target_language=dest,
            executor_strategy=executor,
            asr_model=asr_model,
        ),
        client=client,
        on_http_error=lambda exc: _print_submit_http_error(
            exc,
            client=client,
            source_language=src,
            target_language=dest,
        ),
    )
    console.print(f"Job submitted: [bold]{result.job_id}[/bold]")
    console.print(f"Status: {result.status.value}")
    if result.plan_id:
        console.print(f"Plan: {result.plan_id}")
    for warning in result.warnings:
        console.print(f"[yellow]Warning: {warning}[/yellow]")
    if follow:
        exit_code = _watch_job(client, result.job_id)
        raise SystemExit(exit_code)


@main.command()
def status() -> None:
    """Show orchestrator service status."""
    client = _get_client()
    health = _run(client.get_health(), client=client)
    workers_list = _run(client.list_workers(), client=client)
    pairs = _run(client.get_capabilities(), client=client)
    active_workers: dict[str, int] = {}
    for worker in workers_list:
        worker_type = str(worker.worker_type)
        active_workers[worker_type] = active_workers.get(worker_type, 0) + 1

    console.print(f"Orchestrator: [bold]{health.get('status', 'unknown')}[/bold]")
    table = Table(title="Workers")
    table.add_column("Type")
    table.add_column("Count")
    for worker_type, count in sorted(active_workers.items()):
        table.add_row(worker_type, str(count))
    console.print(table)
    console.print(f"Capabilities: {len(pairs)}")


@job.command("status")
@click.argument("job_id")
@click.option("--verbose", "-v", is_flag=True, help="Show step details")
def job_status(job_id: str, verbose: bool) -> None:  # noqa: FBT001
    """Check job status."""
    client = _get_client()
    result = _run(client.get_job(job_id), client=client)
    console.print(f"Job: [bold]{result.job_id}[/bold]")
    console.print(f"Status: {result.status.value}")
    console.print(f"Label: {result.label or '-'}")
    console.print(f"Plan: {result.plan_id or '-'}")
    console.print(f"Retries from: {result.retries_from or '-'}")
    console.print(f"Source type: {result.source_type}")
    console.print(f"Source language: {result.source_language}")
    console.print(f"Target language: {result.target_language}")
    console.print(f"ASR model: {result.asr_model or '-'}")
    console.print(f"Executor strategy: {result.executor_strategy.value}")
    console.print(f"Created: {result.created_at.isoformat()}")
    console.print(f"Last persisted: {result.last_persisted_at.isoformat()}")
    progress = result.progress
    console.print(f"Progress: {progress.completed_steps}/{progress.total_steps}")
    console.print(f"Current step: {progress.current_step_id or '-'}")
    console.print(
        "Current worker type: "
        f"{progress.current_worker_type.value if progress.current_worker_type is not None else '-'}"
    )
    console.print(f"Current worker ID: {progress.current_worker_id or '-'}")
    eta = f"{progress.eta_seconds:.1f}s" if progress.eta_seconds is not None else "Unknown"
    console.print(f"ETA: {eta}")
    console.print(
        f"Estimated cost (execution-time estimate): "
        f"{_format_estimated_cost(result.total_cost, result.total_cost_basis)}"
    )
    console.print(f"Duration: {result.total_duration_seconds:.1f}s")
    for output in result.outputs:
        console.print(f"Download URL: {output.download_url} ({output.size_bytes} bytes, {output.content_type})")
    if verbose:
        for error in result.errors:
            console.print(
                f"Error [step={error.step_id}, worker_type={error.worker_type}, "
                f"worker_id={error.worker_id}]: {error.message}",
                markup=False,
                style="red",
            )


@job.command("cost")
@click.argument("job_id")
@click.option("--explain", is_flag=True, help="Show pricing provenance for every cost item")
def job_cost(job_id: str, explain: bool) -> None:  # noqa: FBT001
    """Explain a job's execution-time cost estimate."""
    client = _get_client()
    result = _run(client.get_job_cost(job_id), client=client)
    console.print(f"Job: [bold]{result.job_id}[/bold]")
    console.print(
        f"Estimated cost (execution-time estimate): "
        f"{_format_estimated_cost(result.total_cost, result.total_cost_basis)}"
    )
    console.print(f"Cost basis: {result.total_cost_basis.value if result.total_cost_basis else 'unknown'}")
    if explain:
        console.print("Estimates are execution-time evidence, not invoice amounts.")
    table = Table(title="Cost breakdown")
    table.add_column("Step")
    table.add_column("Worker")
    table.add_column("Cost")
    table.add_column("Basis")
    table.add_column("GPU")
    if explain:
        table.add_column("Rate/hour")
        table.add_column("Secure cloud")
        table.add_column("Queried")
        table.add_column("Cache age")
    for item in result.cost_breakdown:
        row = [
            item.step_id,
            f"{item.worker_type.value} ({item.worker_id or 'unknown'})",
            _format_estimated_cost(item.cost, item.basis),
            item.basis.value,
            item.gpu_type or "unknown",
        ]
        if explain:
            row.extend(
                [
                    f"${item.rate_per_hour:.2f}" if item.rate_per_hour is not None else "unknown",
                    str(item.secure_cloud).lower() if item.secure_cloud is not None else "unknown",
                    item.queried_at.isoformat() if item.queried_at is not None else "unknown",
                    f"{item.cache_age_seconds:.1f}s" if item.cache_age_seconds is not None else "unknown",
                ]
            )
        table.add_row(*row)
    console.print(table)


@job.command("cancel")
@click.argument("job_id")
def cancel(job_id: str) -> None:
    """Cancel an active job."""
    client = _get_client()
    result = _run(
        client.cancel_job(job_id),
        client=client,
        on_http_error=lambda exc: _print_cancel_http_error(exc, job_id=job_id),
    )
    console.print(f"Job cancelled: [bold]{result.job_id}[/bold]")
    console.print(f"Status: {result.status.value}")


@job.command("retry")
@click.argument("job_id")
@click.option("--src", default=None, help="Replacement source language")
@click.option("--dest", default=None, help="Replacement target language")
@click.option("--asr", "asr_model", default=None, help="Replacement ASR model")
@click.option("--label", default=None, help="Replacement job label")
def retry(
    job_id: str,
    src: str | None,
    dest: str | None,
    asr_model: str | None,
    label: str | None,
) -> None:
    """Create a fresh job from an earlier submission."""
    client = _get_client()
    result = _run(
        client.retry_job(
            job_id,
            source_language=src,
            target_language=dest,
            asr_model=asr_model,
            label=label,
        ),
        client=client,
        on_http_error=lambda exc: _print_retry_http_error(exc, job_id=job_id),
    )
    console.print(f"Job retried: [bold]{result.job_id}[/bold]")
    console.print(f"Retries from: {result.retries_from or job_id}")
    console.print(f"Status: {result.status.value}")


@job.command("plan")
@click.argument("plan_id", required=False)
@click.option("--job", "job_id", default=None, help="Resolve the plan ID from a job")
def show_plan(plan_id: str | None, job_id: str | None) -> None:
    """Show a compiled plan."""
    client = _get_client()
    if plan_id is not None and job_id is None:
        resolved: str = plan_id
    elif plan_id is None and job_id is not None:
        job_response = _run(client.get_job(job_id), client=client)
        if job_response.plan_id is None:
            console.print(f"[red]Job {job_id} has no plan ID.[/red]")
            raise SystemExit(1)
        resolved = job_response.plan_id
    else:
        raise click.UsageError("provide exactly one plan ID or --job JOB_ID")
    _print_plan(_run(client.get_plan(resolved), client=client))


@job.command()
@click.argument("job_id")
@click.option("--invalidate-step", "invalidate_steps", multiple=True, help="Invalidate a step cache entry; repeatable")
@click.option(
    "--invalidate-chapter",
    "invalidate_chapters",
    type=int,
    multiple=True,
    help="Invalidate a chapter cache entry; repeatable",
)
def resume(job_id: str, invalidate_steps: tuple[str, ...], invalidate_chapters: tuple[int, ...]) -> None:
    """Resume a job with selected cache invalidation."""
    client = _get_client()
    result = _run(
        client.resume_job(
            job_id,
            invalidate_steps=invalidate_steps,
            invalidate_chapters=invalidate_chapters,
        ),
        client=client,
        on_http_error=lambda exc: _print_resume_http_error(exc, job_id=job_id),
    )
    console.print(f"Job resumed: [bold]{result.job_id}[/bold]")
    console.print(f"Status: {result.status.value}")


def _watch_job(client: AcheronClient, job_id: str, *, poll_interval: float = 2.0) -> int:
    """Poll job status and render progress until terminal. Returns exit code."""
    with Live(console=console, refresh_per_second=4) as live:
        while True:
            job = _run(client.get_job(job_id), client=client)
            progress = job.progress
            parts = [
                f"[bold]{job.job_id}[/bold]",
                f"Status: {job.status.value}",
                f"Progress: {progress.completed_steps}/{progress.total_steps}",
            ]
            if progress.current_step_id:
                parts.append(f"Step: {progress.current_step_id}")
            if progress.eta_seconds is not None:
                parts.append(f"ETA: {progress.eta_seconds:.1f}s")
            if job.errors:
                parts.append(f"[red]Error: {job.errors[0].message}[/red]")
            live.update(" | ".join(parts))
            if job.status == PlanStatus.COMPLETED:
                return 0
            if job.status in {PlanStatus.FAILED, PlanStatus.PARTIAL}:
                return 1
            time.sleep(poll_interval)


@job.command()
@click.argument("job_id")
def watch(job_id: str) -> None:
    """Watch a job's progress until completion."""
    client = _get_client()
    exit_code = _watch_job(client, job_id)
    raise SystemExit(exit_code)


@job.command()
@click.argument("job_id")
def tail(job_id: str) -> None:
    """Stream live progress events for a job."""
    client = _get_client()
    request_id_printed = False

    def _on_open() -> None:
        nonlocal request_id_printed
        request_id_printed = True
        _print_request_id(client)

    try:
        for event in _run_sync_generator(
            client.tail_job(job_id, on_open=_on_open),
            client=client,
            request_id_printed=lambda: request_id_printed,
        ):
            console.print(f"{event.status.value}: {event.message}", markup=False)
    except KeyboardInterrupt:
        pass
    raise SystemExit(0)


@main.command("jobs")
@click.option("--active", is_flag=True, help="Show only running jobs")
@click.option("--completed", is_flag=True, help="Show only completed/failed jobs")
@click.option("--label", default=None, help="Filter labels using a glob pattern")
@click.option("--status", type=click.Choice([status.value for status in PlanStatus]), default=None)
@click.option("--since", default=None, help="Only jobs since an ISO-8601 timestamp or duration such as 24h")
@click.option("--before", default=None, help="Only jobs before an ISO-8601 timestamp")
@click.option("--older-than", default=None, help="Only jobs not persisted within this duration")
@click.option("--include-archived", is_flag=True, help="Include archived job records")
def list_jobs(  # noqa: PLR0913
    active: bool,  # noqa: FBT001
    completed: bool,  # noqa: FBT001
    label: str | None,
    status: str | None,
    since: str | None,
    before: str | None,
    older_than: str | None,
    include_archived: bool,  # noqa: FBT001
) -> None:
    """List jobs with optional lifecycle and retention filters."""
    if active and (completed or status is not None):
        raise click.UsageError("--active cannot be combined with --completed or --status")
    if completed and status is not None:
        raise click.UsageError("--completed cannot be combined with --status")
    since_at = _parse_datetime_or_duration(since) if since is not None else None
    before_at = _parse_datetime_or_duration(before) if before is not None else None
    stale_after = _parse_duration_seconds(older_than) if older_than is not None else None
    request_status = "running" if active else status
    client = _get_client()
    if (
        request_status is None
        and since_at is None
        and before_at is None
        and stale_after is None
        and not include_archived
    ):
        jobs = _run(client.list_jobs(label=label), client=client)
    else:
        jobs = _run(
            client.list_jobs(
                label=label,
                status=request_status,
                since=since_at,
                before=before_at,
                older_than_seconds=stale_after,
                include_archived=include_archived,
            ),
            client=client,
        )
    if active:
        jobs = [j for j in jobs if j.status.value == "running"]
    elif completed:
        jobs = [j for j in jobs if j.status.value in ("completed", "failed")]
    if not jobs:
        console.print("No jobs found.")
        return
    table = Table(title="Jobs")
    table.add_column("Job ID")
    table.add_column("Label")
    table.add_column("Status")
    table.add_column("Archived at")
    table.add_column("Stale age")
    table.add_column("Plan")
    table.add_column("Steps")
    for j in jobs:
        progress = j.progress
        steps = f"{progress.completed_steps}/{progress.total_steps}" if progress.total_steps else "-"
        status_label = f"{j.status.value} (archived)" if j.archived_at is not None else j.status.value
        archived_at = j.archived_at.isoformat() if j.archived_at is not None else "-"
        table.add_row(
            j.job_id,
            j.label or "-",
            status_label,
            archived_at,
            _format_age(j.last_persisted_at),
            j.plan_id or "-",
            steps,
        )
    console.print(table)
    for job in jobs:
        if job.archived_at is not None:
            console.print(
                f"job={job.job_id} archived_at={job.archived_at.isoformat()} "
                f"stale_age={_format_age(job.last_persisted_at)}"
            )


@main.command()
def workers() -> None:
    """List registered workers."""
    client = _get_client()
    workers_list = _run(client.list_workers(), client=client)
    if not workers_list:
        console.print("No workers registered.")
        return
    table = Table(title="Workers")
    table.add_column("Worker ID")
    table.add_column("Type")
    table.add_column("Endpoint")
    table.add_column("Transport")
    table.add_column("Failures")
    for w in workers_list:
        table.add_row(
            w.worker_id,
            w.worker_type,
            w.endpoint,
            w.transport,
            str(w.consecutive_failures),
        )
    console.print(table)


@main.command()
@click.option("--src", default=None, help="Filter by source language")
@click.option("--dest", default=None, help="Filter by target language")
@click.option(
    "--type",
    "worker_type",
    type=click.Choice(("tts", "asr", "translation")),
    default=None,
    help="Show workers of a given type instead of language pairs.",
)
def capabilities(src: str | None, dest: str | None, worker_type: str | None) -> None:
    """Show supported language pairs or typed worker capabilities."""
    if worker_type is not None and (src is not None or dest is not None):
        raise click.UsageError("--type cannot be combined with --src/--dest")

    client = _get_client()
    if worker_type is not None:
        workers = _run(client.get_worker_capabilities(worker_type), client=client)
        if not workers:
            console.print(f"No {worker_type} workers available.")
            return
        table = Table(title=f"{worker_type.upper()} Workers")
        table.add_column("Worker ID")
        table.add_column("Model")
        table.add_column("Voice")
        for w in workers:
            model = w.model_source if w.model_source is not None else "-"
            voice_raw = w.metadata.get("voice")
            voice = voice_raw if isinstance(voice_raw, str) else "-"
            table.add_row(w.worker_id, model, voice)
        console.print(table)
        return

    pairs = _run(client.get_capabilities(src=src, dest=dest), client=client)
    if not pairs:
        console.print("No language pairs available.")
        return
    table = Table(title="Capabilities")
    table.add_column("Source")
    table.add_column("Target")
    table.add_column("Workers")
    for p in pairs:
        table.add_row(p.src, p.dst, ", ".join(p.workers))
    console.print(table)
