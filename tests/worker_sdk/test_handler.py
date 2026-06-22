"""Tests for the WorkerHandler ABC."""

import asyncio

import pytest

from acheron.core.models import Job, WorkerCapabilities, WorkerType
from acheron.worker_sdk.artifacts import BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler


class _Echo(WorkerHandler):
    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"en"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
        )

    async def handle(self, job: Job) -> list[BytesArtifact]:
        return [BytesArtifact(filename="out.wav", content_type="audio/wav", data=b"echo")]


class TestWorkerHandlerContract:
    def test_subclass_can_be_instantiated(self) -> None:
        h = _Echo()
        assert isinstance(h, WorkerHandler)

    def test_startup_default_is_noop(self) -> None:
        h = _Echo()
        asyncio.run(h.startup())

    def test_shutdown_default_is_noop(self) -> None:
        h = _Echo()
        asyncio.run(h.shutdown())

    @pytest.mark.asyncio
    async def test_handle_returns_artifacts(self) -> None:
        h = _Echo()
        job = Job(job_id="j1", job_type=WorkerType.TTS, payload={}, chapter_id="ch1")
        out = await h.handle(job)
        assert len(out) == 1
        assert out[0].filename == "out.wav"


class TestAbstractEnforcement:
    def test_cannot_instantiate_bare_abc(self) -> None:
        with pytest.raises(TypeError):
            WorkerHandler()  # type: ignore[abstract]
