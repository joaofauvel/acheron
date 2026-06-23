"""Unit tests for Qwen3TTSRunpodHandler.handle() with the model mocked."""

from typing import Any, cast

import numpy as np
import pytest

from acheron.core.errors import WorkerError
from acheron.core.models import Job, JsonValue, WorkerType
from acheron.worker_sdk.artifacts import BytesArtifact
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


def _build_job(chunks: list[dict[str, Any]], target_language: str = "en") -> Job:
    payload: dict[str, JsonValue] = {
        "chapter_id": "ch1",
        "target_language": target_language,
        "chunks": cast("list[JsonValue]", chunks),
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


class TestHandle:
    @pytest.mark.asyncio
    async def test_handle_returns_bytes_artifacts_in_order(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel(
            wavs=[np.zeros(100, dtype=np.float32), np.zeros(200, dtype=np.float32)],
            sr=22050,
        )
        job = _build_job(
            [
                {"chapter_id": "ch1", "sequence_id": 0, "text": "hello"},
                {"chapter_id": "ch1", "sequence_id": 1, "text": "world"},
            ]
        )
        out = await h.handle(job)
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
    async def test_handle_no_chunks_returns_empty_list(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([], 22050)
        job = _build_job([])
        out = await h.handle(job)
        assert out == []

    @pytest.mark.asyncio
    async def test_handle_unknown_language_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([], 22050)
        job = _build_job(
            [{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}],
            target_language="xx",  # not in _LANG_MAP
        )
        with pytest.raises(WorkerError, match="Unsupported target language"):
            await h.handle(job)

    @pytest.mark.asyncio
    async def test_handle_unknown_speaker_in_config_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings(default_speaker="Bogus"))  # invalid default
        h._model = _FakeModel([np.zeros(50, dtype=np.float32)], 22050)
        job = _build_job([{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}])
        with pytest.raises(WorkerError, match="Unknown speaker"):
            await h.handle(job)

    @pytest.mark.asyncio
    async def test_handle_without_startup_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        # Don't set _model
        job = _build_job([{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}])
        with pytest.raises(WorkerError, match="model not loaded"):
            await h.handle(job)

    @pytest.mark.asyncio
    async def test_handle_per_language_default_overrides_global_default(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        settings = _settings(
            default_speaker="Ryan",
            per_language_defaults={"zh": "Vivian"},
        )
        h = Qwen3TTSRunpodHandler(settings)
        h._model = _FakeModel([np.zeros(50, dtype=np.float32)], 22050)

        captured: dict[str, Any] = {}

        def _spy(
            text: list[str], language: list[str], speaker: list[str], instruct: list[str]
        ) -> tuple[list[np.ndarray], int]:
            captured["speaker"] = speaker
            return [np.zeros(50, dtype=np.float32)], 22050

        h._model.generate_custom_voice = _spy
        job = _build_job([{"chapter_id": "ch1", "sequence_id": 0, "text": "你好"}], target_language="zh")
        await h.handle(job)
        assert captured["speaker"] == ["Vivian"]

    @pytest.mark.asyncio
    async def test_handle_uses_job_speaker_when_provided(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings(default_speaker="Ryan"))
        h._model = _FakeModel([np.zeros(50, dtype=np.float32)], 22050)

        captured: dict[str, Any] = {}

        def _spy(
            text: list[str], language: list[str], speaker: list[str], instruct: list[str]
        ) -> tuple[list[np.ndarray], int]:
            captured["speaker"] = speaker
            return [np.zeros(50, dtype=np.float32)], 22050

        h._model.generate_custom_voice = _spy
        job = Job(
            job_id="j1",
            job_type=WorkerType.TTS,
            payload={
                "chapter_id": "ch1",
                "target_language": "en",
                "chunks": [{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}],
                "speaker": "Dylan",  # job-level override
            },
            chapter_id="ch1",
        )
        await h.handle(job)
        assert captured["speaker"] == ["Dylan"]

    @pytest.mark.asyncio
    async def test_handle_chunks_with_no_chapter_id_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([np.zeros(50, dtype=np.float32)], 22050)
        job = _build_job([{"sequence_id": 0, "text": "hi"}])  # no chapter_id
        with pytest.raises(WorkerError, match="chapter_id"):
            await h.handle(job)

    @pytest.mark.asyncio
    async def test_handle_chunks_with_no_text_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([np.zeros(50, dtype=np.float32)], 22050)
        job = _build_job([{"chapter_id": "ch1", "sequence_id": 0}])  # no text
        with pytest.raises(WorkerError, match="chunk.text"):
            await h.handle(job)

    @pytest.mark.asyncio
    async def test_handle_chapter_id_with_slash_raises_worker_error(self) -> None:
        """Defense-in-depth: reject path-traversal in chapter_id even if the orchestrator would catch it."""
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([np.zeros(50, dtype=np.float32)], 22050)
        job = _build_job([{"chapter_id": "../../etc", "sequence_id": 0, "text": "x"}])
        with pytest.raises(WorkerError, match="path component"):
            await h.handle(job)

    @pytest.mark.asyncio
    async def test_handle_chapter_id_dotdot_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([np.zeros(50, dtype=np.float32)], 22050)
        job = _build_job([{"chapter_id": "..", "sequence_id": 0, "text": "x"}])
        with pytest.raises(WorkerError, match="path component"):
            await h.handle(job)

    @pytest.mark.asyncio
    async def test_handle_chapter_id_with_nul_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([np.zeros(50, dtype=np.float32)], 22050)
        job = _build_job([{"chapter_id": "ch1\x00admin", "sequence_id": 0, "text": "x"}])
        with pytest.raises(WorkerError, match="illegal whitespace"):
            await h.handle(job)
