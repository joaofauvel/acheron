"""Tests for TranslateGemmaRunpodHandler.capabilities."""

from __future__ import annotations

from typing import Any

import pytest

from acheron.core.models import WorkerType


@pytest.fixture
def handler() -> Any:
    """Construct a handler without loading the model."""
    from acheron.worker_sdk.settings import WorkerSettings
    from workers.translategemma.handler import TranslateGemmaRunpodHandler

    return TranslateGemmaRunpodHandler(
        WorkerSettings(
            worker_id="translategemma-test",
            orchestrator_url="http://o:8000",
            listen_port=8001,
            price_source="zero",
            model_id="google/translategemma-12b-it",
        )
    )


_LANGS_69 = frozenset(
    {
        "af",
        "am",
        "ar",
        "az",
        "be",
        "bg",
        "bn",
        "bs",
        "ca",
        "cs",
        "cy",
        "da",
        "de",
        "el",
        "en",
        "es",
        "et",
        "fa",
        "fi",
        "fr",
        "ga",
        "gl",
        "gu",
        "he",
        "hi",
        "hr",
        "hu",
        "hy",
        "id",
        "is",
        "it",
        "ja",
        "ka",
        "kk",
        "km",
        "kn",
        "ko",
        "ky",
        "lo",
        "lt",
        "lv",
        "mk",
        "ml",
        "mn",
        "mr",
        "ms",
        "my",
        "ne",
        "nl",
        "no",
        "pa",
        "pl",
        "pt",
        "ro",
        "ru",
        "si",
        "sk",
        "sl",
        "sr",
        "sv",
        "sw",
        "ta",
        "te",
        "th",
        "tr",
        "uk",
        "ur",
        "vi",
        "zh",
    }
)


def test_capabilities_worker_type_is_translation(handler: Any) -> None:
    assert handler.capabilities().worker_type == WorkerType.TRANSLATION


def test_capabilities_supported_languages_69(handler: Any) -> None:
    caps = handler.capabilities()
    assert caps.supported_languages_in == _LANGS_69
    assert caps.supported_languages_out == _LANGS_69


def test_capabilities_supported_formats(handler: Any) -> None:
    caps = handler.capabilities()
    assert caps.supported_formats_in == frozenset({"text"})
    assert caps.supported_formats_out == frozenset({"text"})


def test_capabilities_batch_capable_true(handler: Any) -> None:
    assert handler.capabilities().batch_capable is True


def test_capabilities_max_input_tokens(handler: Any) -> None:
    assert handler.capabilities().max_input_tokens == 2048


def test_capabilities_custom_max_input_tokens_from_settings() -> None:
    """A custom max_input_tokens setting flows through to the published cap (CFG-011)."""
    from acheron.worker_sdk.settings import WorkerSettings
    from workers.translategemma.handler import TranslateGemmaRunpodHandler

    h = TranslateGemmaRunpodHandler(
        WorkerSettings(
            worker_id="t",
            orchestrator_url="http://o:8000",
            price_source="zero",
            model_id="google/translategemma-12b-it",
            max_input_tokens=4096,
        )
    )
    assert h.capabilities().max_input_tokens == 4096


def test_capabilities_model_source(handler: Any) -> None:
    caps = handler.capabilities()
    assert caps.model_source == "huggingface:google/translategemma-12b-it"


def test_capabilities_metadata_defaults_empty(handler: Any) -> None:
    """Static capabilities() does not include health_provider; the SDK's
    `_registration_caps` enriches it from settings at registration time
    (matches the qwen3tts pattern).
    """
    assert handler.capabilities().metadata == {}


def test_capabilities_custom_model_id() -> None:
    """A custom model_id setting flows through to model_source."""
    from acheron.worker_sdk.settings import WorkerSettings
    from workers.translategemma.handler import TranslateGemmaRunpodHandler

    h = TranslateGemmaRunpodHandler(
        WorkerSettings(
            worker_id="t",
            orchestrator_url="http://o:8000",
            price_source="zero",
            model_id="google/translategemma-4b-it",
        )
    )
    assert h.capabilities().model_source == "huggingface:google/translategemma-4b-it"
