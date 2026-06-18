"""Default in-process handlers for EXTRACTION, CHUNKING, and PACKAGING."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from acheron.core.models import Job, JobMetrics, JobResult, JobStatus, OutputFile

type LocalJobHandler = Callable[[Job], Awaitable[JobResult]]


async def extract_handler(job: Job) -> JobResult:
    """Stub extraction — returns the source path as a text output marker."""
    source_path = str(job.payload.get("source_path", ""))
    return JobResult(
        job_id=job.job_id,
        status=JobStatus.SUCCESS,
        outputs=(
            OutputFile(
                path=source_path,
                filename=source_path.rsplit("/", 1)[-1] if source_path else "",
                size_bytes=0,
                checksum="",
                content_type="text/plain",
            ),
        ),
        metrics=JobMetrics(duration_seconds=0.0),
    )


async def chunk_handler(job: Job) -> JobResult:
    """Stub chunking — produces a single chunk representing the input text."""
    return JobResult(
        job_id=job.job_id,
        status=JobStatus.SUCCESS,
        outputs=(
            OutputFile(
                path=f"{job.job_id}.chunks",
                filename=f"{job.job_id}.chunks",
                size_bytes=0,
                checksum="",
                content_type="application/json",
            ),
        ),
        metrics=JobMetrics(duration_seconds=0.0),
    )


async def package_handler(job: Job) -> JobResult:
    """Stub packaging — produces a placeholder audiobook file."""
    return JobResult(
        job_id=job.job_id,
        status=JobStatus.SUCCESS,
        outputs=(
            OutputFile(
                path=f"{job.job_id}.audiobook",
                filename=f"{job.job_id}.audiobook",
                size_bytes=0,
                checksum="",
                content_type="audio/mpeg",
            ),
        ),
        metrics=JobMetrics(duration_seconds=0.0),
    )
