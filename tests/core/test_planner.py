"""Tests for plan compilation."""

import pytest

from acheron.core.errors import ChunkingTooLongForWorkerError, InvalidLanguagePathError
from acheron.core.models import (
    AudioRequest,
    EpubRequest,
    ExecutorStrategy,
    StepStatus,
    WorkerCapabilities,
    WorkerType,
)
from acheron.core.planner import compile_plan, validate_chunking_fits_workers


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


def _text_input_tts_caps(max_input_tokens: int | None = 2048) -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TTS,
        supported_languages_in=frozenset({"en"}),
        supported_languages_out=frozenset({"en"}),
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"wav"}),
        max_payload_bytes=None,
        batch_capable=True,
        model_source=None,
        max_input_tokens=max_input_tokens,
    )


def _text_input_translation_caps(max_input_tokens: int | None = 2048) -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TRANSLATION,
        supported_languages_in=frozenset({"en"}),
        supported_languages_out=frozenset({"es"}),
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"text"}),
        max_payload_bytes=None,
        batch_capable=False,
        model_source=None,
        max_input_tokens=max_input_tokens,
    )


class TestValidateChunkingFitsWorkers:
    def test_passes_when_chunking_within_limit(self) -> None:
        caps = (_text_input_tts_caps(max_input_tokens=2048),)
        # 250 chars / 4 chars-per-token = 62 tokens, well under 2048.
        validate_chunking_fits_workers(caps, chunking_max_length=250, chars_per_token=4)

    def test_raises_when_chunking_exceeds_tts_limit(self) -> None:
        caps = (_text_input_tts_caps(max_input_tokens=2048),)
        # 9000 chars / 4 = 2250 tokens, exceeds 2048.
        with pytest.raises(ChunkingTooLongForWorkerError, match="max_input_tokens=2048"):
            validate_chunking_fits_workers(caps, chunking_max_length=9000, chars_per_token=4)

    def test_raises_when_chunking_exceeds_translation_limit(self) -> None:
        caps = (_text_input_translation_caps(max_input_tokens=2048),)
        with pytest.raises(ChunkingTooLongForWorkerError, match="translation"):
            validate_chunking_fits_workers(caps, chunking_max_length=9000, chars_per_token=4)

    def test_ignores_workers_with_unlimited_tokens(self) -> None:
        caps = (_text_input_tts_caps(max_input_tokens=None),)
        # Even with a huge chunk length, an unbounded worker is fine.
        validate_chunking_fits_workers(caps, chunking_max_length=10_000_000, chars_per_token=4)

    def test_ignores_non_text_input_worker_types(self) -> None:
        # ASR caps don't carry max_input_tokens (and the function should skip ASR entirely).
        caps = (
            WorkerCapabilities(
                worker_type=WorkerType.ASR,
                supported_languages_in=frozenset({"en"}),
                supported_languages_out=frozenset({"en"}),
                supported_formats_in=frozenset({"wav"}),
                supported_formats_out=frozenset({"text"}),
                max_payload_bytes=None,
                batch_capable=False,
                model_source=None,
                max_input_tokens=10,  # tiny, but should be ignored
            ),
        )
        # ASR is not in the text-input list; should not raise.
        validate_chunking_fits_workers(caps, chunking_max_length=10_000_000, chars_per_token=4)

    def test_smaller_chars_per_token_triggers_earlier(self) -> None:
        caps = (_text_input_tts_caps(max_input_tokens=2048),)
        # 1000 chars / 2 chars-per-token = 500 tokens, OK.
        validate_chunking_fits_workers(caps, chunking_max_length=1000, chars_per_token=2)
        # 1000 chars / 1 char-per-token = 1000 tokens, OK.
        validate_chunking_fits_workers(caps, chunking_max_length=1000, chars_per_token=1)
        with pytest.raises(ChunkingTooLongForWorkerError):
            validate_chunking_fits_workers(caps, chunking_max_length=10_000, chars_per_token=1)

    def test_error_message_includes_all_values(self) -> None:
        caps = (_text_input_tts_caps(max_input_tokens=512),)
        with pytest.raises(ChunkingTooLongForWorkerError) as excinfo:
            validate_chunking_fits_workers(caps, chunking_max_length=3000, chars_per_token=4)
        msg = str(excinfo.value)
        assert "max_chunk_length=3000" in msg
        assert "max_input_tokens=512" in msg
        assert "chars_per_token=4" in msg
        assert "tts" in msg

    def test_invalid_chars_per_token_raises(self) -> None:
        caps = (_text_input_tts_caps(max_input_tokens=2048),)
        with pytest.raises(ValueError, match="chars_per_token must be > 0"):
            validate_chunking_fits_workers(caps, chunking_max_length=100, chars_per_token=0)

    def test_cjk_conservative_bound_via_explicit_one(self) -> None:
        """At chars_per_token=1 (CJK worst case), 4000 chars against a 2048-token worker fails.

        4000 chars / 1 char-per-token = 4000 estimated tokens, which exceeds 2048.
        The orchestrator passes 1 by default (Settings.chars_per_token default); this
        test documents the CJK safety bound the orchestrator relies on.
        """
        caps = (_text_input_tts_caps(max_input_tokens=2048),)
        with pytest.raises(ChunkingTooLongForWorkerError, match="max_input_tokens=2048"):
            validate_chunking_fits_workers(caps, chunking_max_length=4000, chars_per_token=1)

    def test_cjk_conservative_bound_with_explicit_one(self) -> None:
        """CJK path: 1 char/token. max_chunk_length=1000 < 2048 → passes; 3000 → fails."""
        caps = (_text_input_tts_caps(max_input_tokens=2048),)
        validate_chunking_fits_workers(caps, chunking_max_length=1000, chars_per_token=1)
        with pytest.raises(ChunkingTooLongForWorkerError):
            validate_chunking_fits_workers(caps, chunking_max_length=3000, chars_per_token=1)

    def test_all_workers_checked_not_just_first(self) -> None:
        # Two TTS workers: the first has plenty of headroom, the second is too small.
        caps = (
            _text_input_tts_caps(max_input_tokens=2048),
            _text_input_tts_caps(max_input_tokens=10),
        )
        with pytest.raises(ChunkingTooLongForWorkerError, match="max_input_tokens=10"):
            validate_chunking_fits_workers(caps, chunking_max_length=1000, chars_per_token=4)

    def test_chars_per_token_is_required(self) -> None:
        """CFG-009: caller must pass chars_per_token explicitly; no function-level default."""
        caps = (_text_input_tts_caps(max_input_tokens=2048),)
        with pytest.raises(TypeError, match="chars_per_token"):
            validate_chunking_fits_workers(caps, chunking_max_length=100)  # type: ignore[call-arg]
