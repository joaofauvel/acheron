"""Direct unit tests for the worker_sdk stream Protocol and helpers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from acheron.worker_sdk._io import (
    Streamable,
    stream_bytes,
    stream_file,
    stream_producer,
)
from acheron.worker_sdk.artifacts import BytesArtifact
from acheron.worker_sdk.inputs import BytesInput, FileInput, StreamInput


async def _collect(async_iter: AsyncIterator[bytes]) -> list[bytes]:
    return [chunk async for chunk in async_iter]


async def _empty_producer() -> AsyncIterator[bytes]:
    # The `yield` keeps this an async generator so calling it returns an
    # AsyncIterator rather than a coroutine. The guard is never taken at
    # runtime; mypy constant-folds it, so the `yield` needs an unreachable ignore.
    if False:  # pragma: no cover
        yield b""  # type: ignore[unreachable]


class TestStreamableProtocol:
    def test_streamable_protocol_isinstance_on_each_variant(self, tmp_path: Path) -> None:
        assert isinstance(BytesInput(content_type="text/plain", data=b"x"), Streamable)
        assert isinstance(BytesArtifact(filename="a", content_type="text/plain", data=b"x"), Streamable)
        assert isinstance(
            StreamInput(content_type="text/plain", producer=_empty_producer),
            Streamable,
        )
        assert isinstance(FileInput(content_type="text/plain", path=tmp_path / "f"), Streamable)


class TestStreamBytes:
    def test_stream_bytes_yields_data_in_one_chunk(self) -> None:
        assert asyncio.run(_collect(stream_bytes(b"hello"))) == [b"hello"]


class TestStreamProducer:
    def test_stream_producer_empty_producer_yields_nothing(self) -> None:
        assert asyncio.run(_collect(stream_producer(_empty_producer))) == []

    def test_stream_producer_propagates_exception(self) -> None:
        async def faulty() -> AsyncIterator[bytes]:
            yield b""
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            asyncio.run(_collect(stream_producer(faulty)))


class TestStreamFile:
    def test_stream_file_empty_file_yields_nothing(self, tmp_path: Path) -> None:
        p = tmp_path / "empty"
        p.write_bytes(b"")
        assert asyncio.run(_collect(stream_file(p))) == []

    def test_stream_file_missing_path_raises_filenotfounderror(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            asyncio.run(_collect(stream_file(tmp_path / "missing")))
