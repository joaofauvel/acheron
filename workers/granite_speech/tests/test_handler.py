"""Tests for GraniteSpeechRunpodHandler.handle (mocked model).

We monkey-patch ``_transcribe`` to return a canned string. This
exercises the handler's validation (input presence, language check,
chapter_id safety, empty audio) and BytesArtifact construction
without importing torch or transformers.
"""

from __future__ import annotations

from typing import Any

import pytest

from acheron.core.errors import WorkerError
from acheron.core.models import Job, WorkerType
from acheron.worker_sdk.inputs import BytesInput


def _handler() -> Any:
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


def _make_job(source_language: str = "en", chapter_id: str = "ch1") -> Job:
    return Job(
        job_id="j-1-transcribe",
        job_type=WorkerType.ASR,
        payload={"source_language": source_language},
        chapter_id=chapter_id,
    )


def _fake_transcribe(_audio_bytes: bytes) -> str:
    return "transcribed text"


class TestHandle:
    @pytest.mark.asyncio
    async def test_handle_with_bytes_input_produces_text_artifact(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _handler()
        h._model = object()  # mark loaded
        h._processor = object()
        monkeypatch.setattr(h, "_transcribe", _fake_transcribe)
        job = _make_job()
        inp = BytesInput(content_type="audio/mpeg", data=b"\xff\xfb\x90\x00mock-audio")
        artifacts = await h.handle(job, inp)
        assert len(artifacts) == 1
        a = artifacts[0]
        assert a.content_type == "text/plain"
        assert a.filename == "ch1.txt"
        assert a.data == b"transcribed text"
        assert a.metadata["chapter_id"] == "ch1"
        assert a.metadata["model"] == "ibm-granite/granite-speech-4.1-2b"
        assert a.metadata["language"] == "en"

    @pytest.mark.asyncio
    async def test_handle_without_input_raises(self) -> None:
        h = _handler()
        h._model = object()
        h._processor = object()
        job = _make_job()
        with pytest.raises(WorkerError, match="requires an audio input"):
            await h.handle(job, None)

    @pytest.mark.asyncio
    async def test_handle_with_empty_audio_raises(self) -> None:
        h = _handler()
        h._model = object()
        h._processor = object()
        job = _make_job()
        inp = BytesInput(content_type="audio/wav", data=b"")
        with pytest.raises(WorkerError, match="Empty audio input"):
            await h.handle(job, inp)

    @pytest.mark.asyncio
    async def test_handle_with_unsupported_language_raises(self) -> None:
        h = _handler()
        h._model = object()
        h._processor = object()
        job = _make_job(source_language="zh")
        inp = BytesInput(content_type="audio/wav", data=b"x")
        with pytest.raises(WorkerError, match="Unsupported source language"):
            await h.handle(job, inp)

    @pytest.mark.asyncio
    async def test_handle_without_model_loaded_raises(self) -> None:
        h = _handler()
        h._model = None
        h._processor = None
        job = _make_job()
        inp = BytesInput(content_type="audio/wav", data=b"x")
        with pytest.raises(WorkerError, match="model not loaded"):
            await h.handle(job, inp)

    @pytest.mark.asyncio
    async def test_handle_with_path_traversal_chapter_id_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _handler()
        h._model = object()
        h._processor = object()
        monkeypatch.setattr(h, "_transcribe", _fake_transcribe)
        job = _make_job(chapter_id="../../../etc/passwd")
        inp = BytesInput(content_type="audio/wav", data=b"x")
        with pytest.raises(WorkerError, match="path component"):
            await h.handle(job, inp)
