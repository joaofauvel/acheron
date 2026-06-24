"""Tests for GraniteSpeechRunpodHandler.capabilities."""

from __future__ import annotations

from typing import Any

import pytest

from acheron.core.models import WorkerType


@pytest.fixture
def handler() -> Any:
    """Construct a handler without loading the model."""
    from acheron.worker_sdk.settings import WorkerSettings
    from workers.granite_speech.handler import GraniteSpeechRunpodHandler

    return GraniteSpeechRunpodHandler(
        WorkerSettings(
            worker_id="granite-speech-test",
            orchestrator_url="http://o:8000",
            listen_port=8001,
            price_source="zero",
        )
    )


def test_capabilities_worker_type_is_asr(handler: Any) -> None:
    caps = handler.capabilities()
    assert caps.worker_type == WorkerType.ASR


def test_capabilities_supported_languages(handler: Any) -> None:
    caps = handler.capabilities()
    assert caps.supported_languages_in == frozenset({"en", "fr", "de", "es", "pt", "ja"})
    assert caps.supported_languages_out == caps.supported_languages_in


def test_capabilities_supported_formats(handler: Any) -> None:
    caps = handler.capabilities()
    assert caps.supported_formats_in == frozenset({"mp3", "wav"})
    assert caps.supported_formats_out == frozenset({"text"})


def test_capabilities_batch_capable_false(handler: Any) -> None:
    assert handler.capabilities().batch_capable is False


def test_capabilities_model_source(handler: Any) -> None:
    caps = handler.capabilities()
    assert caps.model_source == "huggingface:ibm-granite/granite-speech-4.1-2b"


def test_capabilities_metadata(handler: Any) -> None:
    caps = handler.capabilities()
    assert caps.metadata["asr_prompt"] == "transcribe the speech with proper punctuation and capitalization."
    assert caps.metadata["health_provider"] == "runpod"
