"""Acheron CLI — command-line interface for the audio-transformation pipeline."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import ssl
import sys
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
import httpx
from rich.console import Console
from rich.table import Table

from acheron.api_client import AcheronClient
from acheron.tls import resolve_ca_path

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

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
    The registration token from ``ACHERON_REGISTRATION_TOKEN`` is forwarded
    as a bearer header on mutating requests (uploads, job submission, resume).
    """
    base_url = os.environ.get("ACHERON_URL", _DEFAULT_BASE_URL)
    registration_token = os.environ.get("ACHERON_REGISTRATION_TOKEN")
    return AcheronClient(
        base_url,
        verify=_resolve_trust_store(),
        registration_token=registration_token,
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
    return detail if isinstance(detail, str) else str(detail)


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

    match error_type:
        case _RemoteErrorType.JOB_ALREADY_RUNNING:
            console.print(f"[red]Job {job_id} is already running.[/red]")
            console.print(f"Run [bold]acheron job status {job_id}[/bold] to monitor it.")
        case _RemoteErrorType.JOB_NOT_FOUND:
            console.print(f"[red]Job {job_id} was not found.[/red]")
            console.print("Run [bold]acheron jobs[/bold] to list available jobs.")
        case _:
            console.print(f"[red]Error {exc.response.status_code}: Job resume failed: {message}[/red]")
            console.print(f"Run [bold]acheron job status {job_id}[/bold] to inspect the job before retrying.")


def _detect_source_type(path: str) -> str | None:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return _SOURCE_TYPE_MAP.get(ext)


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


@job.command()
@click.argument("file", type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path))
@click.option("--src", required=True, help="Source language (ISO 639-1)")
@click.option("--dest", required=True, help="Target language (ISO 639-1)")
@click.option("--executor", default="streaming", show_default=True, help="Executor strategy")
@click.option("--asr", "asr_model", default=None, help="ASR model (for audio input)")
@click.option("--type", "source_type", default=None, help="Source type override (epub/audio)")
def submit(  # noqa: PLR0913
    file: Path,
    src: str,
    dest: str,
    executor: str,
    asr_model: str | None,
    source_type: str | None,
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
    if result.plan_id:
        console.print(f"Plan: {result.plan_id}")
    if result.total_steps:
        console.print(f"Steps: {result.completed_steps}/{result.total_steps}")
    if verbose and result.errors:
        for err in result.errors:
            console.print(f"[red]Error: {err}[/red]")


@job.command()
@click.argument("job_id")
@click.option("--force-fresh", is_flag=True, help="Delete cached step outputs before resuming")
def resume(job_id: str, force_fresh: bool) -> None:  # noqa: FBT001
    """Resume a job."""
    result = _run(
        _get_client().resume_job(job_id, force_fresh=force_fresh),
        on_http_error=lambda exc: _print_resume_http_error(exc, job_id=job_id),
    )
    console.print(f"Job resumed: [bold]{result.job_id}[/bold]")
    console.print(f"Status: {result.status.value}")


@main.command("jobs")
@click.option("--active", is_flag=True, help="Show only running jobs")
@click.option("--completed", is_flag=True, help="Show only completed/failed jobs")
def list_jobs(active: bool, completed: bool) -> None:  # noqa: FBT001
    """List all jobs."""
    jobs = _run(_get_client().list_jobs())
    if active:
        jobs = [j for j in jobs if j.status.value == "running"]
    elif completed:
        jobs = [j for j in jobs if j.status.value in ("completed", "failed")]
    if not jobs:
        console.print("No jobs found.")
        return
    table = Table(title="Jobs")
    table.add_column("Job ID")
    table.add_column("Status")
    table.add_column("Plan")
    table.add_column("Steps")
    for j in jobs:
        steps = f"{j.completed_steps}/{j.total_steps}" if j.total_steps else "-"
        table.add_row(j.job_id, j.status.value, j.plan_id or "-", steps)
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
