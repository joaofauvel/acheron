"""Verify make_runpod_handler carries Input over the JSON /run wire (8b)."""

from __future__ import annotations

import base64

import pytest

from acheron.core.models import Job, WorkerCapabilities, WorkerType
from acheron.worker_sdk.artifacts import Artifact
from acheron.worker_sdk.cloud import _serialise_job_for_runpod, make_runpod_handler
from acheron.worker_sdk.handler import WorkerHandler
from acheron.worker_sdk.inputs import BytesInput, Input


class _CaptureHandler(WorkerHandler):
    """Records the job + input it received."""

    def __init__(self) -> None:
        self.received_job: Job | None = None
        self.received_input: Input | None = None

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            worker_type=WorkerType.ASR,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"en"}),
            supported_formats_in=frozenset({"mp3", "wav"}),
            supported_formats_out=frozenset({"text"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
        )

    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:  # noqa: A002
        self.received_job = job
        self.received_input = input
        return []


class TestSerialiseJobForRunpod:
    @pytest.mark.asyncio
    async def test_serialise_includes_input_audio_when_present(self) -> None:
        job = Job(job_id="j-1", job_type=WorkerType.ASR, payload={"x": 1}, chapter_id="ch1")
        inp = BytesInput(content_type="audio/mpeg", data=b"audio-bytes")
        wire = await _serialise_job_for_runpod(job, inp)
        assert "input_audio" in wire["input"]
        assert wire["input"]["input_audio"]["content_type"] == "audio/mpeg"
        assert base64.b64decode(wire["input"]["input_audio"]["data"]) == b"audio-bytes"

    @pytest.mark.asyncio
    async def test_serialise_omits_input_audio_when_none(self) -> None:
        job = Job(job_id="j-1", job_type=WorkerType.TTS, payload={}, chapter_id="ch1")
        wire = await _serialise_job_for_runpod(job, None)
        assert "input_audio" not in wire["input"]


class TestMakeRunpodHandlerAudioForward:
    @pytest.mark.asyncio
    async def test_passes_input_when_input_audio_present(self) -> None:
        handler = _CaptureHandler()
        wrapped = make_runpod_handler(handler)
        runpod_job = {
            "input": {
                "job_id": "j-1",
                "job_type": "asr",
                "payload": {"source_language": "en"},
                "chapter_id": "ch1",
                "sequence_ids": [],
                "input_audio": {
                    "content_type": "audio/wav",
                    "data": base64.b64encode(b"RIFFDATA").decode("ascii"),
                    "metadata": {},
                },
            }
        }
        await wrapped(runpod_job)
        assert handler.received_job is not None
        assert handler.received_job.chapter_id == "ch1"
        assert handler.received_input is not None
        assert handler.received_input.content_type == "audio/wav"
        body = b"".join([c async for c in handler.received_input.stream()])
        assert body == b"RIFFDATA"

    @pytest.mark.asyncio
    async def test_passes_none_when_no_audio(self) -> None:
        """TTS-style: no input_audio → handler receives input=None."""
        handler = _CaptureHandler()
        wrapped = make_runpod_handler(handler)
        runpod_job = {
            "input": {
                "job_id": "j-1",
                "job_type": "tts",
                "payload": {"target_language": "en"},
                "chapter_id": "ch1",
                "sequence_ids": [],
            }
        }
        await wrapped(runpod_job)
        assert handler.received_input is None

    @pytest.mark.asyncio
    async def test_rejects_non_str_data_field(self) -> None:
        """The cloud-side decoder must raise WorkerError on malformed input_audio
        rather than silently coerce (matches _deserialise_runpod_artifacts)."""
        from acheron.core.errors import WorkerError

        handler = _CaptureHandler()
        wrapped = make_runpod_handler(handler)
        runpod_job = {
            "input": {
                "job_id": "j-1",
                "job_type": "asr",
                "payload": {},
                "chapter_id": "ch1",
                "input_audio": {
                    "content_type": "audio/wav",
                    "data": 42,  # wrong type
                    "metadata": {},
                },
            }
        }
        with pytest.raises(WorkerError, match=r"input_audio\.data must be a str"):
            await wrapped(runpod_job)

    @pytest.mark.asyncio
    async def test_rejects_non_dict_metadata_field(self) -> None:
        from acheron.core.errors import WorkerError

        handler = _CaptureHandler()
        wrapped = make_runpod_handler(handler)
        runpod_job = {
            "input": {
                "job_id": "j-1",
                "job_type": "asr",
                "payload": {},
                "chapter_id": "ch1",
                "input_audio": {
                    "content_type": "audio/wav",
                    "data": "AAAAAA==",
                    "metadata": ["not", "a", "dict"],  # wrong type
                },
            }
        }
        with pytest.raises(WorkerError, match=r"input_audio\.metadata must be a dict"):
            await wrapped(runpod_job)

    @pytest.mark.asyncio
    async def test_rejects_non_dict_input_audio(self) -> None:
        """CORR-021: a non-dict input_audio (e.g. raw bytes) must raise WorkerError
        rather than AttributeError from .get().
        """
        from acheron.core.errors import WorkerError

        handler = _CaptureHandler()
        wrapped = make_runpod_handler(handler)
        runpod_job = {
            "input": {
                "job_id": "j-1",
                "job_type": "asr",
                "payload": {},
                "chapter_id": "ch1",
                "input_audio": "not-a-dict",
            }
        }
        with pytest.raises(WorkerError, match=r"input_audio must be a dict"):
            await wrapped(runpod_job)

    @pytest.mark.asyncio
    async def test_rejects_non_str_content_type(self) -> None:
        """CORR-022: a non-str content_type must raise WorkerError rather than
        being silently coerced via str().
        """
        from acheron.core.errors import WorkerError

        handler = _CaptureHandler()
        wrapped = make_runpod_handler(handler)
        runpod_job = {
            "input": {
                "job_id": "j-1",
                "job_type": "asr",
                "payload": {},
                "chapter_id": "ch1",
                "input_audio": {
                    "content_type": 42,
                    "data": "AAAAAA==",
                    "metadata": {},
                },
            }
        }
        with pytest.raises(WorkerError, match=r"input_audio\.content_type must be a str"):
            await wrapped(runpod_job)
