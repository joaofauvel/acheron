"""Standalone tests for the shared _materialize_artifact + _build_result helpers."""

from pathlib import Path

import pytest

from acheron.core.errors import WorkerError
from acheron.core.models import CostBasis, JobMetrics, JobStatus, OutputFile
from acheron.shell.transports._multipart import _build_result, _materialize_artifact, _parse_request_multipart


class TestMaterializeArtifact:
    @pytest.mark.asyncio
    async def test_writes_bytes_and_computes_checksum_size(self, tmp_path: Path) -> None:
        data = b"hello world"
        out = await _materialize_artifact(
            data=data,
            filename="ch1_0000.wav",
            content_type="audio/wav",
            dest_dir=tmp_path,
        )
        assert isinstance(out, OutputFile)
        assert out.filename == "ch1_0000.wav"
        assert out.size_bytes == len(data)
        assert out.content_type == "audio/wav"
        # SHA-256 of "hello world"
        assert out.checksum == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        assert Path(out.path).read_bytes() == data
        assert Path(out.path).parent == tmp_path

    @pytest.mark.asyncio
    async def test_dest_dir_created_if_missing(self, tmp_path: Path) -> None:
        dest = tmp_path / "sub" / "deeper"
        out = await _materialize_artifact(
            data=b"x",
            filename="f.txt",
            content_type="text/plain",
            dest_dir=dest,
        )
        assert Path(out.path).exists()


class TestBuildResult:
    @pytest.mark.asyncio
    async def test_assembles_job_result_with_metrics(self, tmp_path: Path) -> None:
        art1 = await _materialize_artifact(
            data=b"a",
            filename="a.wav",
            content_type="audio/wav",
            dest_dir=tmp_path,
        )
        art2 = await _materialize_artifact(
            data=b"b",
            filename="b.wav",
            content_type="audio/wav",
            dest_dir=tmp_path,
        )
        metrics = JobMetrics(
            duration_seconds=1.5,
            gpu_seconds=1.0,
            cost_estimate=0.042,
            cost_basis=CostBasis.MEASURED,
        )
        result = _build_result(
            job_id="job-xyz-step",
            outputs=(art1, art2),
            metrics=metrics,
        )
        assert result.job_id == "job-xyz-step"
        assert result.status == JobStatus.SUCCESS
        assert len(result.outputs) == 2
        assert result.metrics.cost_estimate == 0.042
        assert result.metrics.cost_basis == CostBasis.MEASURED
        assert result.error is None


class TestSafeJoin:
    @pytest.mark.asyncio
    async def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        with pytest.raises(WorkerError, match="path-traversal"):
            await _materialize_artifact(
                data=b"x",
                filename="../../etc/passwd",
                content_type="text/plain",
                dest_dir=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_rejects_absolute_filename(self, tmp_path: Path) -> None:
        with pytest.raises(WorkerError, match="path-traversal"):
            await _materialize_artifact(
                data=b"x",
                filename="/etc/passwd",
                content_type="text/plain",
                dest_dir=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_rejects_nul_byte(self, tmp_path: Path) -> None:
        with pytest.raises(WorkerError, match="NUL"):
            await _materialize_artifact(
                data=b"x",
                filename="good\x00bad",
                content_type="text/plain",
                dest_dir=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_rejects_blank_filename(self, tmp_path: Path) -> None:
        with pytest.raises(WorkerError, match="blank"):
            await _materialize_artifact(
                data=b"x",
                filename="",
                content_type="text/plain",
                dest_dir=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_returns_basename_not_raw(self, tmp_path: Path) -> None:
        """Even when the safe path includes subdirs, the returned filename is just the basename."""
        out = await _materialize_artifact(
            data=b"x",
            filename="audio.wav",
            content_type="audio/wav",
            dest_dir=tmp_path,
        )
        assert out.filename == "audio.wav"
        assert Path(out.path).parent == tmp_path


class TestParseRequestMultipart:
    def test_json_only_returns_empty_audio(self) -> None:
        """Plain ``application/json`` → empty audio (TTS / non-audio path)."""
        body = b'{"job_id": "j-1", "job_type": "tts"}'
        envelope, audio_bytes, audio_ctype = _parse_request_multipart("application/json", body)
        assert envelope["job_id"] == "j-1"
        assert envelope["job_type"] == "tts"
        assert audio_bytes == b""
        assert audio_ctype == ""

    def test_multipart_with_audio_extracts_both_parts(self) -> None:
        """Multipart with JSON part + audio part → envelope dict + audio bytes + audio content_type."""
        boundary = "bnd-x"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="request"\r\n'
            f"Content-Type: application/json\r\n\r\n"
            f'{{"job_id": "j-1", "job_type": "asr"}}\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="audio"; filename="x.mp3"\r\n'
            f"Content-Type: audio/mpeg\r\n\r\n"
            f"AUDIOBYTES\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        ctype = f"multipart/form-data; boundary={boundary}"
        envelope, audio_bytes, audio_ctype = _parse_request_multipart(ctype, body)
        assert envelope["job_id"] == "j-1"
        assert envelope["job_type"] == "asr"
        assert audio_bytes == b"AUDIOBYTES"
        assert audio_ctype == "audio/mpeg"

    def test_multipart_without_audio_part_returns_empty_audio(self) -> None:
        """Multipart with only the JSON part → empty audio (multipart shell, no audio file)."""
        boundary = "bnd-y"
        body = (
            f'--{boundary}\r\nContent-Type: application/json\r\n\r\n{{"job_id": "j-1"}}\r\n--{boundary}--\r\n'
        ).encode()
        ctype = f"multipart/form-data; boundary={boundary}"
        envelope, audio_bytes, audio_ctype = _parse_request_multipart(ctype, body)
        assert envelope["job_id"] == "j-1"
        assert audio_bytes == b""
        assert audio_ctype == ""

    def test_multipart_missing_json_part_raises(self) -> None:
        """Multipart with only an audio part (no ``application/json`` part) → ValueError."""
        boundary = "bnd-z"
        body = (f"--{boundary}\r\nContent-Type: audio/mpeg\r\n\r\nAUDIOBYTES\r\n--{boundary}--\r\n").encode()
        ctype = f"multipart/form-data; boundary={boundary}"
        with pytest.raises(ValueError, match="no application/json part"):
            _parse_request_multipart(ctype, body)
