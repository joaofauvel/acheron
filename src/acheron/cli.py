"""Acheron CLI — command-line interface for the audio-transformation pipeline."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import sys
from typing import TYPE_CHECKING, Any

import click
import httpx
from rich.console import Console
from rich.table import Table

from acheron.api_client import AcheronClient

if TYPE_CHECKING:
    from collections.abc import Coroutine

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


def _get_client() -> AcheronClient:
    return AcheronClient(os.environ.get("ACHERON_URL", "http://localhost:8000"))


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
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
    except httpx.ConnectError:
        url = os.environ.get("ACHERON_URL", "http://localhost:8000")
        console.print(f"[red]Cannot connect to Acheron at {url}[/red]")
        console.print("Is the server running? Check with: [bold]docker compose ps[/bold]")
        raise SystemExit(1) from None
    except httpx.HTTPStatusError as exc:
        detail = (
            exc.response.json().get("detail", str(exc))
            if exc.response.headers.get("content-type", "").startswith("application/json")
            else str(exc)
        )
        console.print(f"[red]Error {exc.response.status_code}: {detail}[/red]")
        raise SystemExit(1) from exc


def _detect_source_type(path: str) -> str | None:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return _SOURCE_TYPE_MAP.get(ext)


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


@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.option("--src", required=True, help="Source language (ISO 639-1)")
@click.option("--dest", required=True, help="Target language (ISO 639-1)")
@click.option("--executor", default="batch_async", show_default=True, help="Executor strategy")
@click.option("--asr", "asr_model", default=None, help="ASR model (for audio input)")
@click.option("--type", "source_type", default=None, help="Source type override (epub/audio)")
def submit(  # noqa: PLR0913
    file: str,
    src: str,
    dest: str,
    executor: str,
    asr_model: str | None,
    source_type: str | None,
) -> None:
    """Submit a new job for processing."""
    if source_type is None:
        source_type = _detect_source_type(file)
        if source_type is None:
            console.print(f"[red]Cannot detect source type from '{file}'. Use --type.[/red]")
            raise SystemExit(1)

    result = _run(
        _get_client().submit_job(
            source_type=source_type,
            source_path=file,
            source_language=src,
            target_language=dest,
            executor_strategy=executor,
            asr_model=asr_model,
        )
    )
    console.print(f"Job submitted: [bold]{result['job_id']}[/bold]")
    console.print(f"Status: {result['status']}")
    if result.get("plan_id"):
        console.print(f"Plan: {result['plan_id']}")


@main.command()
@click.argument("job_id")
@click.option("--verbose", "-v", is_flag=True, help="Show step details")
def status(job_id: str, verbose: bool) -> None:  # noqa: FBT001
    """Check job status."""
    result = _run(_get_client().get_job(job_id))
    console.print(f"Job: [bold]{result['job_id']}[/bold]")
    console.print(f"Status: {result['status']}")
    if result.get("plan_id"):
        console.print(f"Plan: {result['plan_id']}")
    if result.get("total_steps"):
        console.print(f"Steps: {result['completed_steps']}/{result['total_steps']}")
    if verbose and result.get("errors"):
        for err in result["errors"]:
            console.print(f"[red]Error: {err}[/red]")


@main.command("jobs")
@click.option("--active", is_flag=True, help="Show only running jobs")
@click.option("--completed", is_flag=True, help="Show only completed/failed jobs")
def list_jobs(active: bool, completed: bool) -> None:  # noqa: FBT001
    """List all jobs."""
    jobs = _run(_get_client().list_jobs())
    if active:
        jobs = [j for j in jobs if j["status"] == "running"]
    elif completed:
        jobs = [j for j in jobs if j["status"] in ("completed", "failed")]
    if not jobs:
        console.print("No jobs found.")
        return
    table = Table(title="Jobs")
    table.add_column("Job ID")
    table.add_column("Status")
    table.add_column("Plan")
    table.add_column("Steps")
    for j in jobs:
        steps = f"{j.get('completed_steps', 0)}/{j.get('total_steps', 0)}" if j.get("total_steps") else "-"
        table.add_row(j["job_id"], j["status"], j.get("plan_id") or "-", steps)
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
            w["worker_id"],
            w["worker_type"],
            w["endpoint"],
            w["transport"],
            str(w["consecutive_failures"]),
        )
    console.print(table)


@main.command()
@click.option("--src", default=None, help="Filter by source language")
@click.option("--dest", default=None, help="Filter by target language")
def capabilities(src: str | None, dest: str | None) -> None:
    """Show supported language pairs."""
    pairs = _run(_get_client().get_capabilities(src=src, dest=dest))
    if not pairs:
        console.print("No language pairs available.")
        return
    table = Table(title="Capabilities")
    table.add_column("Source")
    table.add_column("Target")
    table.add_column("Workers")
    for p in pairs:
        table.add_row(p["src"], p["dst"], ", ".join(p["workers"]))
    console.print(table)
