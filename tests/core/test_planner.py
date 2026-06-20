"""Tests for plan compilation."""

import pytest

from acheron.core.errors import InvalidLanguagePathError
from acheron.core.models import (
    AudioRequest,
    EpubRequest,
    ExecutorStrategy,
    StepStatus,
    WorkerCapabilities,
    WorkerType,
)
from acheron.core.planner import compile_plan


def _tts_caps(lang: str = "es") -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TTS,
        supported_languages_in=frozenset({lang}),
        supported_languages_out=frozenset({lang}),
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"wav"}),
        max_payload_bytes=None,
        batch_capable=True,
        model_source=None,
    )


def _translation_caps(src: str = "en", dst: str = "es") -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TRANSLATION,
        supported_languages_in=frozenset({src}),
        supported_languages_out=frozenset({dst}),
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"text"}),
        max_payload_bytes=None,
        batch_capable=False,
        model_source=None,
    )


def _asr_caps(lang: str = "en") -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.ASR,
        supported_languages_in=frozenset({lang}),
        supported_languages_out=frozenset({lang}),
        supported_formats_in=frozenset({"mp3", "wav"}),
        supported_formats_out=frozenset({"text"}),
        max_payload_bytes=None,
        batch_capable=False,
        model_source=None,
    )


class TestCompilePlan:
    def test_epub_produces_correct_steps(self) -> None:
        caps = (_tts_caps(), _translation_caps())
        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es")
        plan = compile_plan(request, ExecutorStrategy.STREAMING, caps)
        step_ids = [s.step_id for s in plan.steps]
        assert step_ids == ["extract", "chunk", "translate", "synthesize", "package"]

    def test_audio_includes_asr_step(self) -> None:
        caps = (_tts_caps(), _translation_caps(), _asr_caps())
        request = AudioRequest(source_path="/input/podcast.mp3", source_language="en", target_language="es")
        plan = compile_plan(request, ExecutorStrategy.STREAMING, caps)
        step_ids = [s.step_id for s in plan.steps]
        assert step_ids == ["extract", "transcribe", "chunk", "translate", "synthesize", "package"]

    def test_epub_no_asr_step(self) -> None:
        caps = (_tts_caps(), _translation_caps())
        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es")
        plan = compile_plan(request, ExecutorStrategy.STREAMING, caps)
        types = [s.type for s in plan.steps]
        assert WorkerType.ASR not in types

    def test_steps_have_correct_dependencies(self) -> None:
        caps = (_tts_caps(), _translation_caps())
        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es")
        plan = compile_plan(request, ExecutorStrategy.STREAMING, caps)
        by_id = {s.step_id: s for s in plan.steps}
        assert by_id["extract"].depends_on == ()
        assert by_id["chunk"].depends_on == ("extract",)
        assert by_id["translate"].depends_on == ("chunk",)
        assert by_id["synthesize"].depends_on == ("translate",)
        assert by_id["package"].depends_on == ("synthesize",)

    def test_all_steps_pending(self) -> None:
        caps = (_tts_caps(), _translation_caps())
        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es")
        plan = compile_plan(request, ExecutorStrategy.STREAMING, caps)
        for step in plan.steps:
            assert step.status == StepStatus.PENDING

    def test_strategy_preserved(self) -> None:
        caps = (_tts_caps(), _translation_caps())
        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es")
        plan = compile_plan(request, ExecutorStrategy.SEQUENTIAL, caps)
        assert plan.executor_strategy == ExecutorStrategy.SEQUENTIAL

    def test_source_type_epub(self) -> None:
        caps = (_tts_caps(), _translation_caps())
        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es")
        plan = compile_plan(request, ExecutorStrategy.STREAMING, caps)
        assert plan.source_type == "epub"

    def test_source_type_audio(self) -> None:
        caps = (_tts_caps(), _translation_caps(), _asr_caps())
        request = AudioRequest(source_path="/input/podcast.mp3", source_language="en", target_language="es")
        plan = compile_plan(request, ExecutorStrategy.STREAMING, caps)
        assert plan.source_type == "audio"


class TestLanguagePathValidation:
    def test_no_tts_worker_raises(self) -> None:
        caps = (_translation_caps(),)
        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es")
        with pytest.raises(InvalidLanguagePathError, match="No TTS worker"):
            compile_plan(request, ExecutorStrategy.STREAMING, caps)

    def test_no_translation_worker_raises(self) -> None:
        caps = (_tts_caps(),)
        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es")
        with pytest.raises(InvalidLanguagePathError, match="No translation worker"):
            compile_plan(request, ExecutorStrategy.STREAMING, caps)

    def test_no_asr_worker_for_audio_raises(self) -> None:
        caps = (_tts_caps(), _translation_caps())
        request = AudioRequest(source_path="/input/podcast.mp3", source_language="en", target_language="es")
        with pytest.raises(InvalidLanguagePathError, match="No ASR worker"):
            compile_plan(request, ExecutorStrategy.STREAMING, caps)

    def test_valid_path_succeeds(self) -> None:
        caps = (_tts_caps(), _translation_caps())
        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es")
        plan = compile_plan(request, ExecutorStrategy.STREAMING, caps)
        assert plan is not None

    def test_asr_model_in_payload(self) -> None:
        caps = (_tts_caps(), _translation_caps(), _asr_caps())
        request = AudioRequest(
            source_path="/input/podcast.mp3",
            source_language="en",
            target_language="es",
            asr_model="whisper-v3",
        )
        plan = compile_plan(request, ExecutorStrategy.STREAMING, caps)
        by_id = {s.step_id: s for s in plan.steps}
        assert by_id["transcribe"].payload["asr_model"] == "whisper-v3"

    def test_asr_model_none_in_payload(self) -> None:
        caps = (_tts_caps(), _translation_caps(), _asr_caps())
        request = AudioRequest(source_path="/input/podcast.mp3", source_language="en", target_language="es")
        plan = compile_plan(request, ExecutorStrategy.STREAMING, caps)
        by_id = {s.step_id: s for s in plan.steps}
        assert by_id["transcribe"].payload["asr_model"] is None

    def test_same_language_skips_translation(self) -> None:
        caps = (_tts_caps("en"),)
        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="en")
        plan = compile_plan(request, ExecutorStrategy.STREAMING, caps)
        step_ids = [s.step_id for s in plan.steps]
        assert step_ids == ["extract", "chunk", "synthesize", "package"]

    def test_same_language_audio_skips_translation(self) -> None:
        caps = (_tts_caps("en"), _asr_caps("en"))
        request = AudioRequest(source_path="/input/podcast.mp3", source_language="en", target_language="en")
        plan = compile_plan(request, ExecutorStrategy.STREAMING, caps)
        step_ids = [s.step_id for s in plan.steps]
        assert step_ids == ["extract", "transcribe", "chunk", "synthesize", "package"]

    def test_same_language_synthesize_depends_on_chunk(self) -> None:
        caps = (_tts_caps("en"),)
        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="en")
        plan = compile_plan(request, ExecutorStrategy.STREAMING, caps)
        by_id = {s.step_id: s for s in plan.steps}
        assert by_id["synthesize"].depends_on == ("chunk",)
