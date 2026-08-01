"""Standalone tests for the shared _materialize_artifact + _build_result helpers."""

from pathlib import Path

import pytest

from acheron.core.errors import WorkerError
from acheron.core.models import CostBasis, CostEstimate, JobMetrics, JobStatus, OutputFile
from acheron.shell.transports._multipart import _build_result, _materialize_artifact, _parse_multipart_parts


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
            cost_estimate=CostEstimate(cost=0.042, basis=CostBasis.MEASURED),
        )
        result = _build_result(
            job_id="job-xyz-step",
            outputs=(art1, art2),
            metrics=metrics,
        )
        assert result.job_id == "job-xyz-step"
        assert result.status == JobStatus.SUCCESS
        assert len(result.outputs) == 2
        assert result.metrics.cost_estimate is not None
        assert result.metrics.cost_estimate.cost == 0.042
        assert result.metrics.cost_estimate.basis == CostBasis.MEASURED
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
    async def test_rejects_symlinked_destination(self, tmp_path: Path) -> None:
        target = tmp_path / "target"
        target.mkdir()
        job = tmp_path / "job"
        job.symlink_to(target, target_is_directory=True)
        linked = job / "step"
        with pytest.raises(WorkerError, match="destination symlink"):
            await _materialize_artifact(
                data=b"x",
                filename="audio.wav",
                content_type="audio/wav",
                dest_dir=linked,
                root_dir=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_rejects_unsafe_multipart_content_type(self) -> None:
        boundary = "unsafe"
        body = (
            f'--{boundary}\r\nContent-Disposition: attachment; filename="x.wav"\r\n'
            "Content-Type: audio/wav\r\nX-Acheron-Metadata: {}\r\n\r\nx\r\n"
            f"--{boundary}--\r\n"
        ).replace("audio/wav", "audio/wav\r\nX-Injected: yes")
        with pytest.raises(WorkerError, match=r"unsupported multipart headers|invalid artifact content type"):
            _parse_multipart_parts(f"multipart/mixed; boundary={boundary}", body.encode())

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
