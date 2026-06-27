"""Tests for TranslateGemmaRunpodHandler.handle (mocked model + processor).

We replace ``_translate_all`` with a spy that returns canned translations
so the handler's validation, batching, and BytesArtifact construction
can be tested without torch / transformers.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from acheron.core.errors import WorkerError
from acheron.core.models import Job, WorkerType
from acheron.worker_sdk.inputs import BytesInput


def _handler() -> Any:
    from acheron.worker_sdk.settings import WorkerSettings
    from workers.translategemma.handler import TranslateGemmaRunpodHandler

    return TranslateGemmaRunpodHandler(
        WorkerSettings(
            worker_id="translategemma-test",
            orchestrator_url="http://o:8000",
            price_source="zero",
            model_id="google/translategemma-12b-it",
        )
    )


def _make_job(
    source_language: str = "en",
    target_language: str = "es",
    chapter_id: str = "ch1",
) -> Job:
    return Job(
        job_id="j-1-translate",
        job_type=WorkerType.TRANSLATION,
        payload={"source_language": source_language, "target_language": target_language},
        chapter_id=chapter_id,
    )


def _build_input(chunks: list[dict[str, Any]]) -> BytesInput:
    return BytesInput(
        content_type="application/json",
        data=json.dumps(chunks).encode("utf-8"),
    )


def _mark_loaded(h: Any) -> None:
    """Mark the handler as having a loaded model + processor (no actual torch)."""
    h._model = object()
    h._processor = object()


class _FakeModel:
    def generate(self, **kwargs: Any) -> Any:
        return None


class _FakeProcessor:
    def __init__(self) -> None:
        self.tokenizer = type("Tok", (), {"pad_token_id": None, "eos_token_id": 0})()

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return None

    def apply_chat_template(self, messages: Any, tokenize: bool = False, add_generation_prompt: bool = False) -> Any:
        return None

    def decode(self, token_ids: Any, skip_special_tokens: bool = True) -> str:
        return ""


def test_handler_protocols_are_runtime_checkable() -> None:
    from workers.translategemma.handler import _ModelProto, _ProcessorProto, _TokenizerProto

    assert isinstance(_FakeModel(), _ModelProto)
    assert isinstance(_FakeProcessor(), _ProcessorProto)
    assert isinstance(_FakeProcessor().tokenizer, _TokenizerProto)


def _spy_translate_all(monkeypatch: pytest.MonkeyPatch, translations: list[str]) -> None:
    """Patch _translate_all on a handler instance to return canned translations."""
    from workers.translategemma import handler as handler_module

    def _spy(self: Any, chunks: list[dict[str, Any]], src: str, tgt: str) -> list[str]:
        if len(translations) != len(chunks):
            msg = f"spy has {len(translations)} translations but got {len(chunks)} chunks"
            raise AssertionError(msg)
        return list(translations)

    monkeypatch.setattr(handler_module.TranslateGemmaRunpodHandler, "_translate_all", _spy)


class TestHandleValidation:
    @pytest.mark.asyncio
    async def test_handle_without_input_raises(self) -> None:
        h = _handler()
        _mark_loaded(h)
        with pytest.raises(WorkerError, match="requires a chunks.json input"):
            await h.handle(_make_job(), input=None)

    @pytest.mark.asyncio
    async def test_handle_with_malformed_json_raises(self) -> None:
        h = _handler()
        _mark_loaded(h)
        bad = BytesInput(content_type="application/json", data=b"not json {{{")
        with pytest.raises(WorkerError, match="not valid JSON"):
            await h.handle(_make_job(), input=bad)

    @pytest.mark.asyncio
    async def test_handle_with_non_list_json_raises(self) -> None:
        h = _handler()
        _mark_loaded(h)
        bad = BytesInput(content_type="application/json", data=b'{"a": 1}')
        with pytest.raises(WorkerError, match="JSON array"):
            await h.handle(_make_job(), input=bad)

    @pytest.mark.asyncio
    async def test_handle_with_unsupported_source_language_raises(self) -> None:
        h = _handler()
        _mark_loaded(h)
        chunks = [{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}]
        with pytest.raises(WorkerError, match="Unsupported source language"):
            await h.handle(_make_job(source_language="xx"), input=_build_input(chunks))

    @pytest.mark.asyncio
    async def test_handle_with_unsupported_target_language_raises(self) -> None:
        h = _handler()
        _mark_loaded(h)
        chunks = [{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}]
        with pytest.raises(WorkerError, match="Unsupported target language"):
            await h.handle(_make_job(target_language="xx"), input=_build_input(chunks))

    @pytest.mark.asyncio
    async def test_handle_with_missing_source_language_payload_raises(self) -> None:
        h = _handler()
        _mark_loaded(h)
        chunks = [{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}]
        job = Job(job_id="j", job_type=WorkerType.TRANSLATION, payload={"target_language": "es"}, chapter_id="ch1")
        with pytest.raises(WorkerError, match="source_language is required"):
            await h.handle(job, input=_build_input(chunks))

    @pytest.mark.asyncio
    async def test_handle_chunk_with_no_chapter_id_raises(self) -> None:
        h = _handler()
        _mark_loaded(h)
        chunks = [{"sequence_id": 0, "text": "hi"}]
        with pytest.raises(WorkerError, match="chapter_id is required"):
            await h.handle(_make_job(), input=_build_input(chunks))

    @pytest.mark.asyncio
    async def test_handle_chunk_with_no_sequence_id_raises(self) -> None:
        h = _handler()
        _mark_loaded(h)
        chunks = [{"chapter_id": "ch1", "text": "hi"}]
        with pytest.raises(WorkerError, match="sequence_id is required"):
            await h.handle(_make_job(), input=_build_input(chunks))

    @pytest.mark.asyncio
    async def test_handle_chunk_with_no_text_raises(self) -> None:
        h = _handler()
        _mark_loaded(h)
        chunks = [{"chapter_id": "ch1", "sequence_id": 0}]
        with pytest.raises(WorkerError, match="text is required"):
            await h.handle(_make_job(), input=_build_input(chunks))

    @pytest.mark.asyncio
    async def test_handle_without_model_loaded_raises(self) -> None:
        h = _handler()
        chunks = [{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}]
        with pytest.raises(WorkerError, match="model not loaded"):
            await h.handle(_make_job(), input=_build_input(chunks))

    @pytest.mark.asyncio
    async def test_handle_chapter_id_path_traversal_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _handler()
        _mark_loaded(h)
        _spy_translate_all(monkeypatch, ["hola"])
        chunks = [{"chapter_id": "../../etc", "sequence_id": 0, "text": "hi"}]
        with pytest.raises(WorkerError, match="path component"):
            await h.handle(_make_job(), input=_build_input(chunks))

    @pytest.mark.asyncio
    async def test_handle_chapter_id_nul_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _handler()
        _mark_loaded(h)
        _spy_translate_all(monkeypatch, ["hola"])
        chunks = [{"chapter_id": "ch1\x00admin", "sequence_id": 0, "text": "hi"}]
        with pytest.raises(WorkerError, match="illegal whitespace"):
            await h.handle(_make_job(), input=_build_input(chunks))


class TestHandleHappyPath:
    @pytest.mark.asyncio
    async def test_handle_empty_chunks_returns_empty_list(self) -> None:
        h = _handler()
        _mark_loaded(h)
        out = await h.handle(_make_job(), input=_build_input([]))
        assert out == []

    @pytest.mark.asyncio
    async def test_handle_empty_input_returns_empty_list(self) -> None:
        """Empty chunks.json body → no error, just no translations."""
        h = _handler()
        _mark_loaded(h)
        empty = BytesInput(content_type="application/json", data=b"")
        out = await h.handle(_make_job(), input=empty)
        assert out == []

    @pytest.mark.asyncio
    async def test_handle_single_chunk_produces_one_artifact(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _handler()
        _mark_loaded(h)
        _spy_translate_all(monkeypatch, ["hola"])
        chunks = [{"chapter_id": "ch1", "sequence_id": 0, "text": "hello"}]
        artifacts = await h.handle(_make_job(), input=_build_input(chunks))
        assert len(artifacts) == 1
        a = artifacts[0]
        assert a.content_type == "text/plain"
        assert a.filename == "ch1_0000.txt"
        assert a.data == b"hola"
        assert a.metadata["chapter_id"] == "ch1"
        assert a.metadata["sequence_id"] == 0
        assert a.metadata["source_language"] == "en"
        assert a.metadata["target_language"] == "es"
        assert a.metadata["model"] == "google/translategemma-12b-it"

    @pytest.mark.asyncio
    async def test_handle_multiple_chunks_in_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _handler()
        _mark_loaded(h)
        _spy_translate_all(monkeypatch, ["hola", "mundo", "!"])
        chunks = [
            {"chapter_id": "ch1", "sequence_id": 0, "text": "hello"},
            {"chapter_id": "ch1", "sequence_id": 1, "text": "world"},
            {"chapter_id": "ch1", "sequence_id": 2, "text": "!"},
        ]
        artifacts = await h.handle(_make_job(), input=_build_input(chunks))
        assert len(artifacts) == 3
        assert [a.filename for a in artifacts] == ["ch1_0000.txt", "ch1_0001.txt", "ch1_0002.txt"]
        assert [a.data for a in artifacts] == [b"hola", b"mundo", b"!"]


class TestTranslateAll:
    def test_translate_all_chunks_into_batches_of_max_batch_size(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """10 chunks → 3 batches: 4 + 4 + 2."""
        from workers._shared_utils import Chunk
        from workers.translategemma import handler as handler_module

        h = _handler()
        calls: list[list[Chunk]] = []

        def _spy(self: Any, batch: list[Chunk], src: str, tgt: str) -> list[str]:
            calls.append(batch)
            return [f"t_{i}" for i in range(len(batch))]

        original = handler_module.TranslateGemmaRunpodHandler._translate_batch
        monkeypatch.setattr(handler_module.TranslateGemmaRunpodHandler, "_translate_batch", _spy)
        chunks = [Chunk(chapter_id="ch1", sequence_id=i, text=f"chunk-{i}") for i in range(10)]
        out = h._translate_all(chunks, "en", "es")
        assert len(calls) == 3
        assert [len(b) for b in calls] == [4, 4, 2]
        assert len(out) == 10
        assert out[0] == "t_0"  # first batch
        assert out[3] == "t_3"  # last of first batch
        assert out[4] == "t_0"  # second batch starts fresh
        assert out[8] == "t_0"  # third batch (size 2) starts fresh
        assert out[9] == "t_1"
        monkeypatch.undo()
        assert handler_module.TranslateGemmaRunpodHandler._translate_batch is original

    def test_translate_all_with_fewer_than_max_batch_size(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from workers._shared_utils import Chunk
        from workers.translategemma import handler as handler_module

        h = _handler()
        calls: list[list[Chunk]] = []

        def _spy(self: Any, batch: list[Chunk], src: str, tgt: str) -> list[str]:
            calls.append(batch)
            return [f"t_{i}" for i in range(len(batch))]

        original = handler_module.TranslateGemmaRunpodHandler._translate_batch
        monkeypatch.setattr(handler_module.TranslateGemmaRunpodHandler, "_translate_batch", _spy)
        chunks = [Chunk(chapter_id="ch1", sequence_id=i, text=f"chunk-{i}") for i in range(3)]
        out = h._translate_all(chunks, "en", "es")
        assert len(calls) == 1
        assert len(out) == 3
        monkeypatch.undo()
        assert handler_module.TranslateGemmaRunpodHandler._translate_batch is original


class TestValidatePayload:
    def test_returns_src_and_tgt(self) -> None:
        h = _handler()
        _mark_loaded(h)
        src, tgt = h._validate_payload(
            _make_job(), _build_input([{"chapter_id": "ch1", "sequence_id": 0, "text": "x"}])
        )
        assert src == "en"
        assert tgt == "es"

    def test_raises_when_model_not_loaded(self) -> None:
        h = _handler()
        with pytest.raises(WorkerError, match="model not loaded"):
            h._validate_payload(_make_job(), _build_input([{"chapter_id": "ch1", "sequence_id": 0, "text": "x"}]))

    def test_raises_when_input_is_none(self) -> None:
        h = _handler()
        _mark_loaded(h)
        with pytest.raises(WorkerError, match="requires a chunks.json input"):
            h._validate_payload(_make_job(), None)

    def test_raises_when_target_language_unsupported(self) -> None:
        h = _handler()
        _mark_loaded(h)
        with pytest.raises(WorkerError, match="Unsupported target language"):
            h._validate_payload(
                _make_job(target_language="xx"), _build_input([{"chapter_id": "ch1", "sequence_id": 0, "text": "x"}])
            )


class TestParseChunks:
    @pytest.mark.asyncio
    async def test_returns_parsed_chunks(self) -> None:
        from workers._shared_utils import Chunk

        h = _handler()
        _mark_loaded(h)
        chunks_in = [{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}]
        out = await h._parse_chunks(_build_input(chunks_in))
        assert out == [Chunk(chapter_id="ch1", sequence_id=0, text="hi", instruct="")]

    @pytest.mark.asyncio
    async def test_empty_body_returns_empty_list(self) -> None:
        h = _handler()
        _mark_loaded(h)
        out = await h._parse_chunks(BytesInput(content_type="application/json", data=b""))
        assert out == []


class TestTranslateAndArtifact:
    @pytest.mark.asyncio
    async def test_builds_artifact_per_chunk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from workers._shared_utils import Chunk

        h = _handler()
        _mark_loaded(h)
        _spy_translate_all(monkeypatch, ["hola", "mundo"])
        chunks = [
            Chunk(chapter_id="ch1", sequence_id=0, text="hello"),
            Chunk(chapter_id="ch1", sequence_id=1, text="world"),
        ]
        out = await h._translate_and_artifact(chunks, "en", "es")
        assert [a.filename for a in out] == ["ch1_0000.txt", "ch1_0001.txt"]
        assert [a.data for a in out] == [b"hola", b"mundo"]
        assert all(a.metadata["source_language"] == "en" for a in out)
        assert all(a.metadata["target_language"] == "es" for a in out)


class TestTokenizerMutation:
    """CORR-033: the pad_token_id init must be a one-shot startup side-effect, not a per-call mutation."""

    def test_translate_batch_does_not_mutate_tokenizer(self) -> None:
        """_translate_batch's body must not assign pad_token_id; that init belongs in startup()."""
        import inspect

        from workers.translategemma import handler as handler_module

        source = inspect.getsource(handler_module.TranslateGemmaRunpodHandler._translate_batch)
        assert "pad_token_id" not in source

    def test_startup_initialises_pad_token_id_once(self) -> None:
        """The pad_token_id init lives in startup()'s loader, not _translate_batch."""
        import inspect

        from workers.translategemma import handler as handler_module

        source = inspect.getsource(handler_module.TranslateGemmaRunpodHandler.startup)
        assert "pad_token_id" in source


class TestPartialSuccess:
    """CORR-029: a per-batch failure must not discard previously translated chunks."""

    def test_translate_all_raises_worker_error_on_batch_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When one batch fails, _translate_all raises WorkerError with the failed batch info."""
        from workers._shared_utils import Chunk
        from workers.translategemma import handler as handler_module

        h = _handler()
        successful_calls: list[int] = []

        def _spy(self: Any, batch: list[Chunk], src: str, tgt: str) -> list[str]:
            if len(successful_calls) == 1:
                msg = "GPU OOM"
                raise RuntimeError(msg)
            successful_calls.append(len(batch))
            return [f"t_{i}" for i in range(len(batch))]

        monkeypatch.setattr(handler_module.TranslateGemmaRunpodHandler, "_translate_batch", _spy)
        chunks = [Chunk(chapter_id="ch1", sequence_id=i, text=f"chunk-{i}") for i in range(8)]
        with pytest.raises(WorkerError, match="partial success"):
            h._translate_all(chunks, "en", "es")
        assert successful_calls == [4]  # first batch succeeded, second raised

    def test_translate_all_logs_failed_batch_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The failed batch warning is logged with the chunk index range."""
        from workers._shared_utils import Chunk
        from workers.translategemma import handler as handler_module

        h = _handler()
        call_count = 0

        def _spy(self: Any, batch: list[Chunk], src: str, tgt: str) -> list[str]:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                msg = "simulated OOM"
                raise RuntimeError(msg)
            return [f"t_{i}" for i in range(len(batch))]

        monkeypatch.setattr(handler_module.TranslateGemmaRunpodHandler, "_translate_batch", _spy)
        chunks = [Chunk(chapter_id="ch1", sequence_id=i, text=f"chunk-{i}") for i in range(8)]
        with caplog.at_level("WARNING", logger="workers.translategemma.handler"), pytest.raises(WorkerError):
            h._translate_all(chunks, "en", "es")
        assert any("batch 1 (chunks 4-7) failed" in record.message for record in caplog.records)
