"""Tests for the make_runpod_handler cloud adapter and RunPodForwarderHandler."""

import base64
from typing import Any

import pytest

from acheron.core.errors import WorkerError
from acheron.core.models import Job, WorkerCapabilities, WorkerType
from acheron.worker_sdk._runpod_client import RunPodJobResult
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact
from acheron.worker_sdk.cloud import RunPodForwarderHandler, make_runpod_handler
from acheron.worker_sdk.handler import WorkerHandler
from acheron.worker_sdk.inputs import Input
from acheron.worker_sdk.settings import WorkerSettings


class _Stub(WorkerHandler):
    def __init__(self, settings: WorkerSettings | None = None) -> None:
        self.last_input: dict[str, Any] = {}
        self._settings = settings

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

    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:
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


class _FakeRunPodClient:
    """In-memory stand-in for RunPodClient — returns a preset result."""

    def __init__(self, result: RunPodJobResult) -> None:
        self._result = result
        self.calls: list[dict[str, object]] = []

    async def run(self, payload: dict[str, object]) -> RunPodJobResult:
        self.calls.append(payload)
        return self._result


def _forwarder_settings(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> WorkerSettings:
    monkeypatch.setenv("ACHERON_WORKER__RUNPOD_API_KEY", "k")
    monkeypatch.setenv("ACHERON_WORKER__RUNPOD_ENDPOINT_ID", "e")
    base: dict[str, Any] = {
        "worker_id": "qwen3tts-edge",
        "orchestrator_url": "http://orch:8000",
    }
    base.update(overrides)
    return WorkerSettings(**base)


class TestRunPodForwarderHandler:
    def test_capabilities_delegate_to_phantom(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = RunPodForwarderHandler(_forwarder_settings(monkeypatch), phantom_handler=_Stub)
        caps = h.capabilities()
        assert caps.worker_type == WorkerType.TTS

    def test_capabilities_without_phantom_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = RunPodForwarderHandler(_forwarder_settings(monkeypatch))
        with pytest.raises(WorkerError, match="phantom_handler"):
            h.capabilities()

    @pytest.mark.asyncio
    async def test_startup_requires_runpod_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ACHERON_WORKER__RUNPOD_API_KEY", raising=False)
        monkeypatch.delenv("ACHERON_WORKER__RUNPOD_ENDPOINT_ID", raising=False)
        h = RunPodForwarderHandler(WorkerSettings(worker_id="w", orchestrator_url="http://o:8000"))
        with pytest.raises(WorkerError, match="runpod_api_key"):
            await h.startup()

    @pytest.mark.asyncio
    async def test_handle_forwards_to_runpod_and_decodes_artifacts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeRunPodClient(
            RunPodJobResult(
                artifacts=[
                    {
                        "filename": "ch1_0000.wav",
                        "content_type": "audio/wav",
                        "data": base64.b64encode(b"audio-bytes").decode("ascii"),
                        "metadata": {"sequence_id": 0},
                    },
                ],
                gpu_seconds=1.5,
            )
        )
        monkeypatch.setattr("acheron.worker_sdk.cloud.RunPodClient", lambda **_: fake)
        h = RunPodForwarderHandler(_forwarder_settings(monkeypatch))
        await h.startup()
        job = Job(
            job_id="j-1",
            job_type=WorkerType.TTS,
            payload={"chapter_id": "ch1", "chunks": []},
            chapter_id="ch1",
        )
        out = await h.handle(job)
        assert len(out) == 1
        a = out[0]
        assert isinstance(a, BytesArtifact)
        assert a.filename == "ch1_0000.wav"
        assert a.content_type == "audio/wav"
        assert a.data == b"audio-bytes"
        assert a.metadata == {"sequence_id": 0}
        # The serialised job was passed to the client.
        assert len(fake.calls) == 1
        sent = fake.calls[0]
        assert sent["input"]["job_id"] == "j-1"  # type: ignore[index]

    @pytest.mark.asyncio
    async def test_handle_without_startup_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = RunPodForwarderHandler(_forwarder_settings(monkeypatch))
        job = Job(job_id="j-1", job_type=WorkerType.TTS, payload={}, chapter_id="ch1")
        with pytest.raises(WorkerError, match="startup"):
            await h.handle(job)

    @pytest.mark.asyncio
    async def test_handle_rejects_malformed_artifact_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeRunPodClient(
            RunPodJobResult(
                artifacts=[{"filename": "x"}],  # missing content_type + data
                gpu_seconds=0.0,
            )
        )
        monkeypatch.setattr("acheron.worker_sdk.cloud.RunPodClient", lambda **_: fake)
        h = RunPodForwarderHandler(_forwarder_settings(monkeypatch))
        await h.startup()
        job = Job(job_id="j-1", job_type=WorkerType.TTS, payload={}, chapter_id="ch1")
        with pytest.raises(WorkerError, match="missing required str fields"):
            await h.handle(job)
