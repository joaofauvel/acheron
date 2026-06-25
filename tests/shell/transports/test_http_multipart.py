"""Direct unit tests for the multipart parser used by ``HttpWorker._parse_multipart``."""

from __future__ import annotations

import json

import pytest

from acheron.core.errors import WorkerError
from acheron.shell.transports._multipart import _parse_multipart_parts

_BOUNDARY = "acheron-test"


def _build_body(parts: list[tuple[str, str, bytes, dict[str, str] | None]]) -> bytes:
    """Build a ``multipart/mixed`` body from a list of ``(filename, content_type, data, metadata)`` tuples."""
    out = b""
    for filename, ctype, data, metadata in parts:
        meta_header = ""
        if metadata is not None:
            meta_header = f"X-Acheron-Metadata: {json.dumps(metadata, separators=(',', ':'))}\r\n"
        out += (
            (
                f"--{_BOUNDARY}\r\n"
                f'Content-Disposition: attachment; filename="{filename}"\r\n'
                f"Content-Type: {ctype}\r\n"
                f"{meta_header}\r\n"
            ).encode()
            + data
            + b"\r\n"
        )
    out += f"--{_BOUNDARY}--\r\n".encode()
    return out


class TestParseMultipartPartsMetadata:
    """CORR-013 — per-part ``X-Acheron-Metadata`` header is preserved on the parsed part."""

    def test_parses_x_acheron_metadata_header(self) -> None:
        body = _build_body(
            [
                ("ch1.txt", "text/plain", b"hello", {"key": "value"}),
            ]
        )
        parts, _ = _parse_multipart_parts(
            f"multipart/mixed; boundary={_BOUNDARY}",
            body,
        )
        assert len(parts) == 1
        assert parts[0].metadata == {"key": "value"}
        assert parts[0].filename == "ch1.txt"
        assert parts[0].content_type == "text/plain"
        assert parts[0].data == b"hello"

    def test_missing_metadata_header_yields_empty_dict(self) -> None:
        body = _build_body(
            [
                ("ch1.txt", "text/plain", b"hello", None),
            ]
        )
        parts, _ = _parse_multipart_parts(
            f"multipart/mixed; boundary={_BOUNDARY}",
            body,
        )
        assert len(parts) == 1
        assert parts[0].metadata == {}


class TestParseMultipartPartsMetricsSelection:
    """CORR-030 — metrics part is identified by ``X-Acheron-Part-Name: metrics`` header."""

    def test_selects_metrics_part_by_part_name_header(self) -> None:
        body = (
            f"--{_BOUNDARY}\r\n"
            f'Content-Disposition: attachment; filename="ch1.txt"\r\n'
            f"Content-Type: text/plain\r\n\r\n"
            f"transcript\r\n"
            f"--{_BOUNDARY}\r\n"
            f"Content-Type: application/json\r\n\r\n"
            f'{{"chapter_id":"ch1"}}\r\n'
            f"--{_BOUNDARY}\r\n"
            f"Content-Type: application/json\r\n"
            f"X-Acheron-Part-Name: metrics\r\n\r\n"
            f'{{"duration_seconds": 1.5}}\r\n'
            f"--{_BOUNDARY}--\r\n"
        ).encode()
        parts, metrics = _parse_multipart_parts(
            f"multipart/mixed; boundary={_BOUNDARY}",
            body,
        )
        assert len(parts) == 1
        assert parts[0].content_type == "text/plain"
        assert metrics.duration_seconds == 1.5

    def test_falls_back_to_first_json_when_no_part_name_header(self) -> None:
        body = (
            f'--{_BOUNDARY}\r\nContent-Type: application/json\r\n\r\n{{"duration_seconds": 2.0}}\r\n--{_BOUNDARY}--\r\n'
        ).encode()
        _, metrics = _parse_multipart_parts(
            f"multipart/mixed; boundary={_BOUNDARY}",
            body,
        )
        assert metrics.duration_seconds == 2.0

    def test_multiple_metrics_parts_with_header_raises(self) -> None:
        body = (
            f"--{_BOUNDARY}\r\n"
            f"Content-Type: application/json\r\n"
            f"X-Acheron-Part-Name: metrics\r\n\r\n"
            f'{{"duration_seconds": 1.0}}\r\n'
            f"--{_BOUNDARY}\r\n"
            f"Content-Type: application/json\r\n"
            f"X-Acheron-Part-Name: metrics\r\n\r\n"
            f'{{"duration_seconds": 2.0}}\r\n'
            f"--{_BOUNDARY}--\r\n"
        ).encode()
        with pytest.raises(WorkerError, match="metrics"):
            _parse_multipart_parts(
                f"multipart/mixed; boundary={_BOUNDARY}",
                body,
            )


class TestParseMultipartPartsBoundary:
    """CORR-028 — missing ``boundary=`` raises ``WorkerError``."""

    def test_missing_boundary_raises_worker_error(self) -> None:
        with pytest.raises(WorkerError, match="missing boundary"):
            _parse_multipart_parts(
                "multipart/mixed; charset=utf-8",
                b"--x\r\nContent-Type: text/plain\r\n\r\nhello\r\n--x--\r\n",
            )


class TestExecuteWithUpstreamInputDuplicateFile:
    """CORR-027 — multi-file upstream outputs raise ``WorkerError`` instead of silent truncation."""

    @pytest.mark.asyncio
    async def test_two_audio_files_in_extract_raises_worker_error(self, tmp_path: object) -> None:
        from pathlib import Path

        import httpx

        from acheron.core.models import Job, OutputFile, WorkerType
        from acheron.shell.cache import StepCache
        from acheron.shell.transports.http import HttpWorker

        plan_job_id = "job-abc123"
        cache = StepCache(Path(str(tmp_path)))
        audio1 = Path(str(tmp_path)) / "ch1.mp3"
        audio1.write_bytes(b"MOCK-1")
        audio2 = Path(str(tmp_path)) / "ch2.mp3"
        audio2.write_bytes(b"MOCK-2")
        await cache.save_outputs(
            plan_job_id,
            "extract",
            (
                OutputFile(
                    path=str(audio1),
                    filename=audio1.name,
                    size_bytes=audio1.stat().st_size,
                    checksum="x" * 64,
                    content_type="audio/mpeg",
                ),
                OutputFile(
                    path=str(audio2),
                    filename=audio2.name,
                    size_bytes=audio2.stat().st_size,
                    checksum="x" * 64,
                    content_type="audio/mpeg",
                ),
            ),
        )
        transport = httpx.MockTransport(lambda _r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport, base_url="http://stub:8002") as client:
            worker = HttpWorker(
                "http://stub:8002",
                client=client,
                data_dir=Path(str(tmp_path)),
                step_cache=cache,
            )
            job = Job(
                job_id=f"{plan_job_id}-transcribe",
                job_type=WorkerType.ASR,
                payload={"source_language": "en"},
                chapter_id="ch1",
            )
            with pytest.raises(WorkerError, match="multiple matching"):
                await worker.execute(job)

    @pytest.mark.asyncio
    async def test_single_audio_file_in_extract_still_works(self, tmp_path: object) -> None:
        from pathlib import Path

        import httpx

        from acheron.core.models import Job, JobStatus, OutputFile, WorkerType
        from acheron.shell.cache import StepCache
        from acheron.shell.transports.http import HttpWorker

        plan_job_id = "job-abc123"
        cache = StepCache(Path(str(tmp_path)))
        audio = Path(str(tmp_path)) / "podcast.mp3"
        audio.write_bytes(b"MOCK-AUDIO")
        await cache.save_outputs(
            plan_job_id,
            "extract",
            (
                OutputFile(
                    path=str(audio),
                    filename=audio.name,
                    size_bytes=audio.stat().st_size,
                    checksum="x" * 64,
                    content_type="audio/mpeg",
                ),
            ),
        )

        async def _handle(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=(
                    b'{"job_id": "j", "status": "success", "outputs": [], '
                    b'"metrics": {"duration_seconds": 1.0}, "error": null}'
                ),
            )

        transport = httpx.MockTransport(_handle)
        async with httpx.AsyncClient(transport=transport, base_url="http://stub:8002") as client:
            worker = HttpWorker(
                "http://stub:8002",
                client=client,
                data_dir=Path(str(tmp_path)),
                step_cache=cache,
            )
            job = Job(
                job_id=f"{plan_job_id}-transcribe",
                job_type=WorkerType.ASR,
                payload={"source_language": "en"},
                chapter_id="ch1",
            )
            result = await worker.execute(job)
        assert result.status == JobStatus.SUCCESS
