"""Capability-shape tests for Qwen3TTSRunpodHandler."""

from typing import Any, cast

from acheron.core.models import WorkerType
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


def test_capabilities_shape() -> None:
    from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

    h = Qwen3TTSRunpodHandler(_settings())
    caps = h.capabilities()
    assert caps.worker_type == WorkerType.TTS
    assert caps.supported_formats_in == frozenset({"text"})
    assert caps.supported_formats_out == frozenset({"wav"})
    assert caps.batch_capable is True
    assert caps.max_input_tokens == 2048
    assert caps.model_source == "huggingface:Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"


def test_capabilities_languages_match_qwen3_tts_supported() -> None:
    from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

    h = Qwen3TTSRunpodHandler(_settings())
    caps = h.capabilities()
    expected = frozenset({"en", "zh", "ja", "ko", "de", "fr", "ru", "pt", "es", "it"})
    assert caps.supported_languages_in == expected
    assert caps.supported_languages_out == expected


def test_capabilities_metadata_lists_speakers_and_default() -> None:
    from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

    h = Qwen3TTSRunpodHandler(_settings(default_speaker="Ryan"))
    caps = h.capabilities()
    speakers = cast("list[str]", caps.metadata["speakers"])
    assert "Ryan" in speakers
    assert "Vivian" in speakers
    assert caps.metadata["default_speaker"] == "Ryan"


def test_handler_inherits_worker_handler() -> None:
    from acheron.worker_sdk.handler import WorkerHandler
    from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

    assert issubclass(Qwen3TTSRunpodHandler, WorkerHandler)


def test_handler_initial_state_model_is_none() -> None:
    from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

    h = Qwen3TTSRunpodHandler(_settings())
    assert h._model is None
