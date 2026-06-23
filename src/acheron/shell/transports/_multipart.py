"""Shared helpers used by both HttpWorker (multipart/mixed) and GrpcWorker (Artifact parts).

The orchestrator materializes received bytes into its own ``ACHERON_DATA_DIR``,
so a worker needs no shared filesystem with the orchestrator.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import aiofiles

from acheron.core.errors import WorkerError
from acheron.core.models import JobMetrics, JobResult, JobStatus, OutputFile


def _safe_join(dest_dir: Path, filename: str) -> Path:
    """Join ``dest_dir / filename`` and reject any escape from ``dest_dir``.

    A worker-supplied ``filename`` of ``../../etc/passwd`` (or any absolute
    path) would otherwise let the worker write anywhere on the orchestrator
    host. This helper resolves the candidate path and refuses it if the
    resolved location is not under ``dest_dir``.
    """
    if not filename or filename != filename.strip():
        msg = f"Refusing artifact with blank or padded filename: {filename!r}"
        raise WorkerError(msg)
    if filename != Path(filename).name:
        msg = f"Refusing artifact with path-traversal filename: {filename!r}"
        raise WorkerError(msg)
    if "\x00" in filename:
        msg = f"Refusing artifact with NUL byte in filename: {filename!r}"
        raise WorkerError(msg)
    dest_dir_resolved = dest_dir.resolve()
    candidate = (dest_dir / filename).resolve()
    try:
        candidate.relative_to(dest_dir_resolved)
    except ValueError as exc:
        msg = f"Refusing artifact that escapes dest_dir: {filename!r} -> {candidate}"
        raise WorkerError(msg) from exc
    return candidate


async def _materialize_artifact(
    *,
    data: bytes,
    filename: str,
    content_type: str,
    dest_dir: Path,
) -> OutputFile:
    """Write ``bytes`` to ``dest_dir/filename`` and return an ``OutputFile``.

    ``filename`` is validated against path-traversal attacks — see
    :func:`_safe_join`. ``checksum`` (SHA-256) and ``size_bytes`` are
    computed locally on the orchestrator side so the worker doesn't need
    to be trusted on them.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    out_path = _safe_join(dest_dir, filename)
    async with aiofiles.open(out_path, "wb") as f:
        await f.write(data)
    checksum = hashlib.sha256(data).hexdigest()
    return OutputFile(
        path=str(out_path),
        filename=Path(filename).name,
        size_bytes=len(data),
        checksum=checksum,
        content_type=content_type,
    )


def _build_result(
    *,
    job_id: str,
    outputs: tuple[OutputFile, ...],
    metrics: JobMetrics,
) -> JobResult:
    """Assemble a success ``JobResult`` from materialized outputs + metrics."""
    return JobResult(
        job_id=job_id,
        status=JobStatus.SUCCESS,
        outputs=outputs,
        metrics=metrics,
        error=None,
    )
