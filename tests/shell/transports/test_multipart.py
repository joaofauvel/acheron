"""Standalone tests for the shared _materialize_artifact + _build_result helpers."""

from pathlib import Path

import pytest

from acheron.core.models import CostBasis, JobMetrics, JobStatus, OutputFile
from acheron.shell.transports._multipart import _build_result, _materialize_artifact


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
