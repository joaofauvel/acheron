"""Unit tests for Qwen3TTSRunpodHandler.handle() with the model mocked."""

from __future__ import annotations

import json
from typing import Any, cast

import numpy as np
import pytest

from acheron.core.errors import WorkerError
from acheron.core.models import Job, JsonValue, WorkerType
from acheron.worker_sdk.artifacts import BytesArtifact
from acheron.worker_sdk.inputs import BytesInput
from acheron.worker_sdk.settings import WorkerSettings


def _settings(**overrides: Any) -> WorkerSettings:
    base: dict[str, Any] = {
        "worker_id": "w",
        "orchestrator_url": "http://o:8000",
        "price_source": "zero",
        "default_speaker": "Ryan",
    }
    base.update(overrides)
    return WorkerSettings(**base)


def _build_input(chunks: list[dict[str, Any]]) -> BytesInput:
    return BytesInput(
        content_type="application/json",
        data=json.dumps(chunks).encode("utf-8"),
    )


def _build_job(target_language: str = "en") -> Job:
    payload: dict[str, JsonValue] = {
        "chapter_id": "ch1",
        "target_language": target_language,
    }
    return Job(
        job_id="job-xyz-synth-ch1",
        job_type=WorkerType.TTS,
        payload=payload,
        chapter_id="ch1",
    )


class _FakeModel:
    def __init__(self, wavs: list[np.ndarray], sr: int) -> None:
        self._wavs = wavs
        self._sr = sr

    def generate_custom_voice(
        self, text: list[str], language: list[str], speaker: list[str], instruct: list[str]
    ) -> tuple[list[np.ndarray], int]:
        return self._wavs, self._sr


class _SpyingModel(_FakeModel):
    """_FakeModel that records the speaker list it was called with."""

    def __init__(self, wavs: list[np.ndarray], sr: int) -> None:
        super().__init__(wavs, sr)
        self.captured_speaker: list[str] = []

    def generate_custom_voice(
        self, text: list[str], language: list[str], speaker: list[str], instruct: list[str]
    ) -> tuple[list[np.ndarray], int]:
        self.captured_speaker = speaker
        return self._wavs, self._sr


def _job_with_voice_payload(**payload: Any) -> Job:
    base: dict[str, JsonValue] = {"chapter_id": "ch1", "target_language": "en"}
    base.update(cast("dict[str, JsonValue]", payload))
    return Job(job_id="j1", job_type=WorkerType.TTS, payload=base, chapter_id="ch1")


class TestHandle:
    @pytest.mark.asyncio
    async def test_handle_returns_bytes_artifacts_in_order(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel(
            wavs=[np.zeros(100, dtype=np.float32), np.zeros(200, dtype=np.float32)],
            sr=22050,
        )
        chunks = [
            {"chapter_id": "ch1", "sequence_id": 0, "text": "hello"},
            {"chapter_id": "ch1", "sequence_id": 1, "text": "world"},
        ]
        out = await h.handle(_build_job(), input=_build_input(chunks))
        assert len(out) == 2
        bytes_arts = cast("list[BytesArtifact]", out)
        assert all(isinstance(a, BytesArtifact) for a in bytes_arts)
        assert bytes_arts[0].filename == "ch1_0000.wav"
        assert bytes_arts[1].filename == "ch1_0001.wav"
        assert bytes_arts[0].content_type == "audio/wav"
        assert bytes_arts[0].metadata["sequence_id"] == 0
        assert bytes_arts[1].metadata["sequence_id"] == 1
        # WAV sizes should differ (different sample counts).
        assert len(bytes_arts[0].data) != len(bytes_arts[1].data)

    @pytest.mark.asyncio
    async def test_handle_empty_chunks_returns_empty_list(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([], 22050)
        out = await h.handle(_build_job(), input=_build_input([]))
        assert out == []

    @pytest.mark.asyncio
    async def test_handle_no_input_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([], 22050)
        with pytest.raises(WorkerError, match="requires a chunks.json input"):
            await h.handle(_build_job(), input=None)

    @pytest.mark.asyncio
    async def test_handle_unknown_language_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([], 22050)
        with pytest.raises(WorkerError, match="Unsupported target language"):
            await h.handle(
                _build_job(target_language="xx"),
                input=_build_input([{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}]),
            )

    @pytest.mark.asyncio
    async def test_handle_unknown_speaker_in_config_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings(default_speaker="Bogus"))
        h._model = _FakeModel([np.zeros(50, dtype=np.float32)], 22050)
        with pytest.raises(WorkerError, match="Unknown speaker") as exc_info:
            await h.handle(_build_job(), input=_build_input([{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}]))
        assert "Bogus" not in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handle_without_startup_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        with pytest.raises(WorkerError, match="model not loaded"):
            await h.handle(
                _build_job(),
                input=_build_input([{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}]),
            )

    @pytest.mark.asyncio
    async def test_handle_per_language_default_overrides_global_default(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        settings = _settings(default_speaker="Ryan", per_language_defaults={"zh": "Vivian"})
        h = Qwen3TTSRunpodHandler(settings)
        h._model = _SpyingModel([np.zeros(50, dtype=np.float32)], 22050)

        await h.handle(
            _build_job(target_language="zh"),
            input=_build_input([{"chapter_id": "ch1", "sequence_id": 0, "text": "你好"}]),
        )
        assert h._model.captured_speaker == ["Vivian"]

    @pytest.mark.asyncio
    async def test_handle_ignores_legacy_speaker_payload_key(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings(default_speaker="Ryan"))
        h._model = _SpyingModel([np.zeros(50, dtype=np.float32)], 22050)

        job = Job(
            job_id="j1",
            job_type=WorkerType.TTS,
            payload={"chapter_id": "ch1", "target_language": "en", "speaker": "Dylan"},
            chapter_id="ch1",
        )
        await h.handle(job, input=_build_input([{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}]))
        assert h._model.captured_speaker == ["Ryan"]

    @pytest.mark.asyncio
    async def test_handle_applies_voice_map_per_chunk(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _SpyingModel(
            [np.zeros(50, dtype=np.float32), np.zeros(50, dtype=np.float32)],
            22050,
        )
        job = _job_with_voice_payload(
            voice="Ryan",
            voice_map=[
                {"start_chapter": 1, "end_chapter": 3, "voice": "Vivian"},
                {"start_chapter": 4, "end_chapter": 100, "voice": "Ryan"},
            ],
        )

        await h.handle(
            job,
            input=_build_input(
                [
                    {"chapter_id": "ch1", "sequence_id": 0, "text": "one"},
                    {"chapter_id": "chapter_004", "sequence_id": 1, "text": "four"},
                ]
            ),
        )

        assert h._model.captured_speaker == ["Vivian", "Ryan"]

    @pytest.mark.asyncio
    async def test_handle_uses_default_voice_without_voice_map(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _SpyingModel([np.zeros(50, dtype=np.float32)], 22050)

        await h.handle(
            _job_with_voice_payload(voice="Vivian"),
            input=_build_input([{"chapter_id": "ch4", "sequence_id": 0, "text": "four"}]),
        )

        assert h._model.captured_speaker == ["Vivian"]

    @pytest.mark.asyncio
    async def test_handle_falls_back_to_default_for_uncovered_chapter(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings(default_speaker="Ryan"))
        h._model = _SpyingModel([np.zeros(50, dtype=np.float32)], 22050)

        await h.handle(
            _job_with_voice_payload(
                voice="Ryan",
                voice_map=[{"start_chapter": 1, "end_chapter": 3, "voice": "Vivian"}],
            ),
            input=_build_input([{"chapter_id": "ch4", "sequence_id": 0, "text": "four"}]),
        )

        assert h._model.captured_speaker == ["Ryan"]

    @pytest.mark.asyncio
    async def test_handle_rejects_uncovered_chapter_without_explicit_default(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings(default_speaker="Ryan"))
        h._model = _SpyingModel([np.zeros(50, dtype=np.float32)], 22050)

        with pytest.raises(WorkerError, match="No voice configured for chapter 4"):
            await h.handle(
                _job_with_voice_payload(
                    voice_map=[{"start_chapter": 1, "end_chapter": 3, "voice": "Vivian"}],
                ),
                input=_build_input([{"chapter_id": "ch4", "sequence_id": 0, "text": "four"}]),
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("voice", [None, "", 42])
    async def test_handle_rejects_explicit_invalid_voice(self, voice: object) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _SpyingModel([np.zeros(50, dtype=np.float32)], 22050)

        with pytest.raises(WorkerError, match="voice must be a non-empty string"):
            await h.handle(
                _job_with_voice_payload(voice=voice),
                input=_build_input([{"chapter_id": "ch1", "sequence_id": 0, "text": "one"}]),
            )
        assert h._model.captured_speaker == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "chapter_id",
        ["chapter-four", "https://evil.test/token=secret", "../../etc/passwd", r"C:\\Users\\secret\\token"],
    )
    async def test_handle_rejects_chapter_id_without_echoing_input(self, chapter_id: str) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _SpyingModel([np.zeros(50, dtype=np.float32)], 22050)

        with pytest.raises(WorkerError, match="Invalid chapter_id") as exc_info:
            await h.handle(
                _job_with_voice_payload(voice="Ryan"),
                input=_build_input([{"chapter_id": chapter_id, "sequence_id": 0, "text": "four"}]),
            )
        assert chapter_id not in str(exc_info.value)
        assert h._model.captured_speaker == []

    @pytest.mark.asyncio
    async def test_handle_rejects_voice_absent_from_advertised_set(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _SpyingModel([np.zeros(50, dtype=np.float32)], 22050)

        with pytest.raises(WorkerError, match="Unknown speaker") as exc_info:
            await h.handle(
                _job_with_voice_payload(voice="NotAdvertised"),
                input=_build_input([{"chapter_id": "ch1", "sequence_id": 0, "text": "one"}]),
            )
        assert "NotAdvertised" not in str(exc_info.value)
        assert h._model.captured_speaker == []

    @pytest.mark.asyncio
    async def test_handle_rejects_missing_voice_in_voice_map(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _SpyingModel([np.zeros(50, dtype=np.float32)], 22050)

        with pytest.raises(WorkerError, match=r"voice_map\[0\]\.voice"):
            await h.handle(
                _job_with_voice_payload(
                    voice="Ryan",
                    voice_map=[{"start_chapter": 1, "end_chapter": 3}],
                ),
                input=_build_input([{"chapter_id": "ch1", "sequence_id": 0, "text": "one"}]),
            )
        assert h._model.captured_speaker == []

    @pytest.mark.asyncio
    async def test_handle_chunks_with_no_chapter_id_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([np.zeros(50, dtype=np.float32)], 22050)
        with pytest.raises(WorkerError, match="chapter_id"):
            await h.handle(
                _build_job(),
                input=_build_input([{"sequence_id": 0, "text": "hi"}]),
            )

    @pytest.mark.asyncio
    async def test_handle_chunks_with_no_text_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([np.zeros(50, dtype=np.float32)], 22050)
        with pytest.raises(WorkerError, match="chunk.text"):
            await h.handle(
                _build_job(),
                input=_build_input([{"chapter_id": "ch1", "sequence_id": 0}]),
            )

    @pytest.mark.asyncio
    async def test_handle_chapter_id_with_slash_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([np.zeros(50, dtype=np.float32)], 22050)
        with pytest.raises(WorkerError, match="path component"):
            await h.handle(
                _build_job(),
                input=_build_input([{"chapter_id": "../../etc", "sequence_id": 0, "text": "x"}]),
            )

    @pytest.mark.asyncio
    async def test_handle_chapter_id_dotdot_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([np.zeros(50, dtype=np.float32)], 22050)
        with pytest.raises(WorkerError, match="path component"):
            await h.handle(
                _build_job(),
                input=_build_input([{"chapter_id": "..", "sequence_id": 0, "text": "x"}]),
            )

    @pytest.mark.asyncio
    async def test_handle_chapter_id_with_nul_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([np.zeros(50, dtype=np.float32)], 22050)
        with pytest.raises(WorkerError, match="illegal whitespace"):
            await h.handle(
                _build_job(),
                input=_build_input([{"chapter_id": "ch1\x00admin", "sequence_id": 0, "text": "x"}]),
            )

    @pytest.mark.asyncio
    async def test_handle_malformed_chunks_json_raises(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([], 22050)
        bad = BytesInput(content_type="application/json", data=b"not json {{{")
        with pytest.raises(WorkerError, match="not valid JSON"):
            await h.handle(_build_job(), input=bad)

    @pytest.mark.asyncio
    async def test_handle_chunks_json_not_list_raises(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([], 22050)
        bad = BytesInput(content_type="application/json", data=b'{"a": 1}')
        with pytest.raises(WorkerError, match="JSON array"):
            await h.handle(_build_job(), input=bad)

    def test_fake_model_satisfies_protocol(self) -> None:
        from workers.qwen3tts.handler import _Qwen3TTSModelProto

        assert isinstance(_FakeModel([], 22050), _Qwen3TTSModelProto)
