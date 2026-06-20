"""Direct unit tests for the built-in local handlers."""

from __future__ import annotations

import pytest

from acheron.core.models import Job, JobStatus, WorkerType
from acheron.shell.local_handlers import (
    chunk_handler,
    extract_handler,
    package_handler,
)


def _make_job(job_type: WorkerType, **payload: str) -> Job:
    return Job(
        job_id="job-123",
        job_type=job_type,
        payload=dict(payload),
        chapter_id="ch-1",
    )


@pytest.mark.asyncio
async def test_extract_handler_with_nested_path() -> None:
    job = _make_job(WorkerType.EXTRACTION, source_path="/data/books/war-and-peace.epub")
    result = await extract_handler(job)
    assert result.job_id == "job-123"
    assert result.status is JobStatus.SUCCESS
    assert len(result.outputs) == 1
    out = result.outputs[0]
    assert out.path == "/data/books/war-and-peace.epub"
    assert out.filename == "war-and-peace.epub"
    assert out.content_type == "text/plain"
    assert out.size_bytes == 0
    assert out.checksum == ""


@pytest.mark.asyncio
async def test_extract_handler_with_flat_path() -> None:
    job = _make_job(WorkerType.EXTRACTION, source_path="book.epub")
    result = await extract_handler(job)
    assert result.outputs[0].filename == "book.epub"
    assert result.outputs[0].path == "book.epub"


@pytest.mark.asyncio
async def test_extract_handler_with_empty_source_path() -> None:
    job = _make_job(WorkerType.EXTRACTION, source_path="")
    result = await extract_handler(job)
    out = result.outputs[0]
    assert out.path == ""
    assert out.filename == ""


@pytest.mark.asyncio
async def test_extract_handler_without_source_path_key() -> None:
    job = _make_job(WorkerType.EXTRACTION)
    result = await extract_handler(job)
    out = result.outputs[0]
    assert out.path == ""
    assert out.filename == ""


@pytest.mark.asyncio
async def test_chunk_handler_emits_chunks_artifact() -> None:
    job = _make_job(WorkerType.CHUNKING)
    result = await chunk_handler(job)
    assert result.job_id == "job-123"
    assert result.status is JobStatus.SUCCESS
    assert len(result.outputs) == 1
    out = result.outputs[0]
    assert out.path == "job-123.chunks"
    assert out.filename == "job-123.chunks"
    assert out.content_type == "application/json"
    assert out.size_bytes == 0


@pytest.mark.asyncio
async def test_chunk_handler_uses_job_id_for_filename() -> None:
    job = Job(
        job_id="abc-def",
        job_type=WorkerType.CHUNKING,
        payload={},
        chapter_id="ch-2",
    )
    result = await chunk_handler(job)
    assert result.outputs[0].filename == "abc-def.chunks"


@pytest.mark.asyncio
async def test_package_handler_emits_audiobook_artifact() -> None:
    job = _make_job(WorkerType.PACKAGING)
    result = await package_handler(job)
    assert result.job_id == "job-123"
    assert result.status is JobStatus.SUCCESS
    assert len(result.outputs) == 1
    out = result.outputs[0]
    assert out.path == "job-123.audiobook"
    assert out.filename == "job-123.audiobook"
    assert out.content_type == "audio/mpeg"
    assert out.size_bytes == 0
    assert out.checksum == ""


@pytest.mark.asyncio
async def test_package_handler_uses_job_id_for_filename() -> None:
    job = Job(
        job_id="xyz",
        job_type=WorkerType.PACKAGING,
        payload={},
        chapter_id="ch-3",
    )
    result = await package_handler(job)
    assert result.outputs[0].filename == "xyz.audiobook"
