"""Shared helpers used by both HttpWorker (multipart/mixed) and GrpcWorker (Artifact parts).

The orchestrator materializes received bytes into its own ``ACHERON_DATA_DIR``,
so a worker needs no shared filesystem with the orchestrator.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import aiofiles

from acheron.core.models import JobMetrics, JobResult, JobStatus, OutputFile

if TYPE_CHECKING:
    from pathlib import Path


async def _materialize_artifact(
    *,
    data: bytes,
    filename: str,
    content_type: str,
    dest_dir: Path,
) -> OutputFile:
    """Write ``bytes`` to ``dest_dir/filename`` and return an ``OutputFile``.

    ``checksum`` (SHA-256) and ``size_bytes`` are computed locally on the
    orchestrator side so the worker doesn't need to be trusted on them.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    out_path = dest_dir / filename
    async with aiofiles.open(out_path, "wb") as f:
        await f.write(data)
    checksum = hashlib.sha256(data).hexdigest()
    return OutputFile(
        path=str(out_path),
        filename=filename,
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
