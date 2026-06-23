"""Tests for the Input Protocol + concrete variants (Layer 8b)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from acheron.core.models import JsonValue
from acheron.worker_sdk.inputs import BytesInput, FileInput, StreamInput


async def _collect(async_iter: AsyncIterator[bytes]) -> list[bytes]:
    """Helper to collect an async iterator into a list."""
    return [chunk async for chunk in async_iter]


class TestBytesInput:
    def test_content_type_property(self) -> None:
        b = BytesInput(content_type="audio/mpeg", data=b"\xff\xfb\x90\x00")
        assert b.content_type == "audio/mpeg"

    def test_metadata_default_empty(self) -> None:
        b = BytesInput(content_type="audio/wav", data=b"RIFF")
        assert b.metadata == {}

    def test_metadata_explicit(self) -> None:
        meta: dict[str, JsonValue] = {"language": "en", "bitrate": 128}
        b = BytesInput(content_type="audio/mpeg", data=b"x", metadata=meta)
        assert b.metadata == meta

    def test_stream_yields_data(self) -> None:
        b = BytesInput(content_type="audio/mpeg", data=b"hello world")
        chunks = asyncio.run(_collect(b.stream()))
        assert b"".join(chunks) == b"hello world"

    def test_is_frozen(self) -> None:
        b = BytesInput(content_type="audio/mpeg", data=b"x")
        with pytest.raises((AttributeError, Exception)):
            b.data = b"y"  # type: ignore[misc]


class TestStreamInput:
    def test_stream_delegates_to_producer(self) -> None:
        async def producer():
            yield b"chunk1"
            yield b"chunk2"

        s = StreamInput(content_type="audio/wav", producer=producer)
        chunks = asyncio.run(_collect(s.stream()))
        assert b"".join(chunks) == b"chunk1chunk2"

    def test_content_type_and_metadata(self) -> None:
        async def producer():
            yield b""

        s = StreamInput(
            content_type="audio/wav",
            producer=producer,
            metadata={"source": "test"},
        )
        assert s.content_type == "audio/wav"
        assert s.metadata == {"source": "test"}


class TestFileInput:
    def test_content_type_and_path(self, tmp_path: Path) -> None:
        p = tmp_path / "audio.wav"
        p.write_bytes(b"RIFFDATA")
        f = FileInput(content_type="audio/wav", path=p)
        assert f.content_type == "audio/wav"
        assert f.path == p

    def test_stream_reads_file_in_chunks(self, tmp_path: Path) -> None:
        p = tmp_path / "audio.wav"
        data = b"x" * (64 * 1024 + 100)  # > 64 KiB
        p.write_bytes(data)
        f = FileInput(content_type="audio/wav", path=p)
        chunks = asyncio.run(_collect(f.stream()))
        assert b"".join(chunks) == data

    def test_metadata_default_empty(self, tmp_path: Path) -> None:
        f = FileInput(content_type="audio/wav", path=tmp_path / "x")
        assert f.metadata == {}
