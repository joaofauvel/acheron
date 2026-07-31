"""Acheron CLI — command-line interface for the audio-transformation pipeline."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import math
import os
import ssl
import sys
import time
from collections.abc import Callable, Coroutine
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


def _run[T](
    coro: Coroutine[Any, Any, T],
    *,
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
        url = os.environ.get("ACHERON_URL", _DEFAULT_BASE_URL)
        if _is_ssl_error(exc):
            console.print(f"[red]TLS verification failed connecting to {url}[/red]")
            console.print("Trust the dev CA via SSL_CERT_FILE=$PWD/certs/acheron-ca.crt")
            console.print("or set ACHERON_TLS_CA_FILE=/path/to/ca.crt (or http:// URL for plain HTTP).")
        else:
            console.print(f"[red]Cannot connect to Acheron at {url}[/red]")
            console.print("Is the server running? Check with: [bold]docker compose ps[/bold]")
        raise SystemExit(1) from None
    except httpx.TimeoutException:
        url = os.environ.get("ACHERON_URL", _DEFAULT_BASE_URL)
        console.print(f"[red]Request to Acheron timed out at {url}[/red]")
        console.print("Is the server running? Check with: [bold]docker compose ps[/bold]")
        raise SystemExit(1) from None
    except httpx.HTTPStatusError as exc:
        if on_http_error is None:
            _print_http_error(exc)
        else:
            on_http_error(exc)
        raise SystemExit(1) from exc


def _run_sync_generator[T](async_gen: AsyncIterator[T]) -> Iterator[T]:
    """Drain an async generator synchronously via a thread pool."""

    def _next(ait: AsyncIterator[T]) -> T:
        coro = ait.__anext__()
        return asyncio.run(coro)  # type: ignore[arg-type]

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            async_iter = async_gen.__aiter__()
            while True:
                try:
                    yield loop.run_until_complete(async_iter.__anext__())
                except StopAsyncIteration:
                    return
        finally:
            loop.close()
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            async_iter = async_gen.__aiter__()
            while True:
                try:
                    yield pool.submit(_next, async_iter).result()
                except StopAsyncIteration:
                    return


def _http_error_detail(exc: httpx.HTTPStatusError) -> str:
    if not exc.response.headers.get("content-type", "").startswith("application/json"):
        return str(exc)
    try:
        payload = exc.response.json()
    except ValueError:
        return str(exc)
    if not isinstance(payload, dict):
        return str(exc)
    detail = payload.get("detail", str(exc))
    if isinstance(detail, dict):
        error_type = detail.get("type")
        message = detail.get("message")
        if isinstance(error_type, str) and isinstance(message, str):
            return f"{error_type}: {message}"
        if isinstance(message, str):
            return message
    return detail if isinstance(detail, str) else str(detail)


def _http_error_remediation(exc: httpx.HTTPStatusError) -> str | None:
    """Extract an optional structured remediation from an HTTP error."""
    try:
        payload = exc.response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("detail"), dict):
        return None
    remediation = payload["detail"].get("remediation")
    return remediation if isinstance(remediation, str) else None


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
    console.print(f"[red]Error {exc.response.status_code}: {_http_error_detail(exc)}[/red]")


def _print_submit_http_error(
    exc: httpx.HTTPStatusError,
    *,
    source_language: str,
    target_language: str,
) -> None:
    error_type, message = _parse_remote_error(_http_error_detail(exc))

    match error_type:
        case _RemoteErrorType.INVALID_LANGUAGE_PATH:
            console.print(f"[red]No worker can translate {source_language}→{target_language}[/red]")
            try:
                pairs = _run(_get_client().get_capabilities(src=source_language))
            except SystemExit, ValueError:
                console.print(f"Run [bold]acheron capabilities --src {source_language}[/bold] to see the full list.")
                return
            targets = sorted({pair.dst for pair in pairs if pair.src == source_language})
            supported = ", ".join(targets) or "none"
            console.print(f"Supported targets from {source_language}: {supported}")
            console.print(f"Run [bold]acheron capabilities --src {source_language}[/bold] to see the full list.")
        case _RemoteErrorType.CHUNKING_TOO_LONG:
            console.print(f"[red]Job cannot be submitted: {message}[/red]")
            console.print("Reduce the input size or configure a worker with a larger token limit.")
        case _:
            console.print(f"[red]Error {exc.response.status_code}: Job submission failed: {message}[/red]")
            console.print("Check the input and worker capabilities, then retry.")


def _print_resume_http_error(exc: httpx.HTTPStatusError, *, job_id: str) -> None:
    error_type, message = _parse_remote_error(_http_error_detail(exc))
    remediation = _http_error_remediation(exc)

    match error_type:
        case _RemoteErrorType.JOB_ALREADY_RUNNING:
            console.print(f"[red]Job {job_id} is already running.[/red]")
        case _RemoteErrorType.JOB_NOT_FOUND:
            console.print(f"[red]Job {job_id} was not found.[/red]")
        case _:
            console.print(f"[red]Error {exc.response.status_code}: Job resume failed: {message}[/red]")
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
        message = str(detail.get("message", message))
        candidate = detail.get("remediation")
        remediation = candidate if isinstance(candidate, str) else None
    console.print(f"[red]Error {exc.response.status_code}: Job cancellation failed for {job_id}: {message}[/red]")
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
        message = str(detail.get("message", message))
        candidate = detail.get("remediation")
        remediation = candidate if isinstance(candidate, str) else None
    console.print(f"[red]Error {exc.response.status_code}: Job retry failed for {job_id}: {message}[/red]")
    if remediation:
        console.print(f"Try: {remediation}")


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
        result = _run(client.archive_job(job_id), on_http_error=_print_http_error)
        console.print(f"job={result.job_id} status={result.status.value} archived={result.archived_at is not None}")


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
    result = _run(
        _get_client().reap_stale_jobs(older_than_seconds=seconds, reason=reason),
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
    uploaded = _run(
        _get_client().upload_input(file),
        on_http_error=_print_http_error,
    )

    if dry_run:
        preview = _run(
            _get_client().preview_job(
                source_type=source_type,
                source_path=uploaded.source_path,
                source_language=src,
                target_language=dest,
                executor_strategy=executor,
                asr_model=asr_model,
            ),
            on_http_error=lambda exc: _print_submit_http_error(
                exc,
                source_language=src,
                target_language=dest,
            ),
        )
        _print_plan(preview, dry_run=True)
        return

    result = _run(
        _get_client().submit_job(
            source_type=source_type,
            source_path=uploaded.source_path,
            source_language=src,
            target_language=dest,
            executor_strategy=executor,
            asr_model=asr_model,
        ),
        on_http_error=lambda exc: _print_submit_http_error(
            exc,
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
        exit_code = _watch_job(_get_client(), result.job_id)
        raise SystemExit(exit_code)


@main.command()
def status() -> None:
    """Show orchestrator service status."""
    health = _run(_get_client().get_health())
    workers_list = _run(_get_client().list_workers())
    pairs = _run(_get_client().get_capabilities())
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
    result = _run(_get_client().get_job(job_id))
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
    result = _run(_get_client().get_job_cost(job_id))
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
    result = _run(
        _get_client().cancel_job(job_id),
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
    result = _run(
        _get_client().retry_job(
            job_id,
            source_language=src,
            target_language=dest,
            asr_model=asr_model,
            label=label,
        ),
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
    if plan_id is not None and job_id is None:
        resolved: str = plan_id
    elif plan_id is None and job_id is not None:
        job_response = _run(_get_client().get_job(job_id))
        if job_response.plan_id is None:
            console.print(f"[red]Job {job_id} has no plan ID.[/red]")
            raise SystemExit(1)
        resolved = job_response.plan_id
    else:
        raise click.UsageError("provide exactly one plan ID or --job JOB_ID")
    _print_plan(_run(_get_client().get_plan(resolved)))


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
    result = _run(
        _get_client().resume_job(
            job_id,
            invalidate_steps=invalidate_steps,
            invalidate_chapters=invalidate_chapters,
        ),
        on_http_error=lambda exc: _print_resume_http_error(exc, job_id=job_id),
    )
    console.print(f"Job resumed: [bold]{result.job_id}[/bold]")
    console.print(f"Status: {result.status.value}")


def _watch_job(client: AcheronClient, job_id: str, *, poll_interval: float = 2.0) -> int:
    """Poll job status and render progress until terminal. Returns exit code."""
    with Live(console=console, refresh_per_second=4) as live:
        while True:
            job = _run(client.get_job(job_id))
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
    exit_code = _watch_job(_get_client(), job_id)
    raise SystemExit(exit_code)


@job.command()
@click.argument("job_id")
def tail(job_id: str) -> None:
    """Stream live progress events for a job."""
    try:
        for event in _run_sync_generator(_get_client().tail_job(job_id)):
            console.print(f"{event.status.value}: {event.message}", markup=False)
    except KeyboardInterrupt:
        pass
    raise SystemExit(0)


@main.command("jobs")
@click.option("--active", is_flag=True, help="Show only running jobs")
@click.option("--completed", is_flag=True, help="Show only completed/failed jobs")
@click.option("--label", default=None, help="Filter labels using a glob pattern")
def list_jobs(active: bool, completed: bool, label: str | None) -> None:  # noqa: FBT001
    """List all jobs."""
    jobs = _run(_get_client().list_jobs(label=label))
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
    table.add_column("Plan")
    table.add_column("Steps")
    for j in jobs:
        progress = j.progress
        steps = f"{progress.completed_steps}/{progress.total_steps}" if progress.total_steps else "-"
        table.add_row(j.job_id, j.label or "-", j.status.value, j.plan_id or "-", steps)
    console.print(table)


@main.command()
def workers() -> None:
    """List registered workers."""
    workers_list = _run(_get_client().list_workers())
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

    if worker_type is not None:
        workers = _run(_get_client().get_worker_capabilities(worker_type))
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

    pairs = _run(_get_client().get_capabilities(src=src, dest=dest))
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
