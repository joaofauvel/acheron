"""Tests for the Artifact composition primitives."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from acheron.worker_sdk.artifacts import (
    BytesArtifact,
    FileArtifact,
    StreamArtifact,
)


async def _collect(artifact: object) -> bytes:
    stream = artifact.stream  # type: ignore[attr-defined]
    return b"".join([chunk async for chunk in stream()])


class TestBytesArtifact:
    @pytest.mark.asyncio
    async def test_stream_yields_data_once(self) -> None:
        a = BytesArtifact(filename="x.wav", content_type="audio/wav", data=b"hello")
        out = await _collect(a)
        assert out == b"hello"

    @pytest.mark.asyncio
    async def test_metadata_default_empty(self) -> None:
        a = BytesArtifact(filename="x.wav", content_type="audio/wav", data=b"")
        assert a.metadata == {}


class TestStreamArtifact:
    @pytest.mark.asyncio
    async def test_stream_yields_each_chunk(self) -> None:
        async def gen() -> AsyncIterator[bytes]:
            yield b"chunk1"
            yield b"chunk2"

        a = StreamArtifact(filename="long.wav", content_type="audio/wav", producer=gen)
        out = await _collect(a)
        assert out == b"chunk1chunk2"


class TestFileArtifact:
    @pytest.mark.asyncio
    async def test_metadata_defaults_to_empty(self, tmp_path: Path) -> None:
        a = FileArtifact(filename="blob.bin", content_type="application/octet-stream", path=tmp_path / "blob.bin")
        assert a.metadata == {}

    @pytest.mark.asyncio
    async def test_empty_file_streams_nothing(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.bin"
        path.touch()
        a = FileArtifact(filename="empty.bin", content_type="application/octet-stream", path=path)
        assert await _collect(a) == b""

    @pytest.mark.asyncio
    async def test_missing_path_raises(self, tmp_path: Path) -> None:
        a = FileArtifact(filename="missing.bin", content_type="application/octet-stream", path=tmp_path / "missing.bin")
        with pytest.raises(FileNotFoundError):
            await _collect(a)

    @pytest.mark.asyncio
    async def test_stream_reads_from_disk_in_chunks(self, tmp_path: Path) -> None:
        path = tmp_path / "blob.bin"
        path.write_bytes(b"x" * 200_000)  # larger than the 64kb read window
        a = FileArtifact(filename="blob.bin", content_type="application/octet-stream", path=path)
        out = await _collect(a)
        assert len(out) == 200_000
        assert out == b"x" * 200_000
