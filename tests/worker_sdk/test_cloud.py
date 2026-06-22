"""Tests for the make_runpod_handler cloud adapter."""

import base64
from typing import Any

import pytest

from acheron.core.models import Job, WorkerCapabilities, WorkerType
from acheron.worker_sdk.artifacts import BytesArtifact
from acheron.worker_sdk.cloud import make_runpod_handler
from acheron.worker_sdk.handler import WorkerHandler


class _Stub(WorkerHandler):
    def __init__(self) -> None:
        self.last_input: dict[str, Any] = {}

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset(),
            supported_languages_out=frozenset(),
            supported_formats_in=frozenset(),
            supported_formats_out=frozenset(),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
        )

    async def handle(self, job: Job) -> list[BytesArtifact]:
        self.last_input = dict(job.payload)
        return [BytesArtifact(filename="out.wav", content_type="audio/wav", data=b"audio")]


class TestMakeRunpodHandler:
    @pytest.mark.asyncio
    async def test_adapter_returns_runpod_payload_dict(self) -> None:
        h = _Stub()
        adapter = make_runpod_handler(h)
        raw = {
            "input": {
                "job_id": "j1",
                "job_type": "tts",
                "payload": {"text": "hi"},
                "chapter_id": "ch1",
            }
        }
        out = await adapter(raw)
        assert "artifacts" in out
        assert len(out["artifacts"]) == 1
        a = out["artifacts"][0]
        assert a["filename"] == "out.wav"
        assert a["content_type"] == "audio/wav"
        assert a["data"] == base64.b64encode(b"audio").decode("ascii")

    @pytest.mark.asyncio
    async def test_adapter_propagates_input_payload_to_handler(self) -> None:
        h = _Stub()
        adapter = make_runpod_handler(h)
        raw = {
            "input": {
                "job_id": "j1",
                "job_type": "tts",
                "payload": {"text": "hi"},
                "chapter_id": "ch1",
            }
        }
        await adapter(raw)
        assert h.last_input == {"text": "hi"}
