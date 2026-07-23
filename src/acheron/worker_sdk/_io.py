"""Shared stream helpers for worker inputs and artifacts."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable  # noqa: TC003
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import aiofiles

if TYPE_CHECKING:
    from acheron.core.models import JsonValue


@runtime_checkable
class Streamable(Protocol):
    """Anything with a ``content_type``, a ``metadata`` dict, and a byte stream."""

    @property
    def content_type(self) -> str: ...
    @property
    def metadata(self) -> dict[str, JsonValue]: ...
    def stream(self) -> AsyncIterator[bytes]: ...


async def stream_bytes(data: bytes) -> AsyncIterator[bytes]:
    """Yield the in-memory ``data`` as a single chunk."""
    yield data


async def stream_producer(producer: Callable[[], AsyncIterator[bytes]]) -> AsyncIterator[bytes]:
    """Yield chunks produced by ``producer()``."""
    async for chunk in producer():
        yield chunk


async def stream_file(path: Path) -> AsyncIterator[bytes]:
    """Yield the file's contents in 64 KiB chunks."""
    async with aiofiles.open(path, "rb") as f:
        while True:
            chunk = await f.read(64 * 1024)
            if not chunk:
                break
            yield chunk
