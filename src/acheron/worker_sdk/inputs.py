"""Transport-neutral input handed to WorkerHandler.handle() alongside the Job.

The `Input` Protocol is symmetric with `artifacts.Artifact` — the same
three-variant shape (bytes / stream / file), the opposite direction on
the wire. Workers consume an `Input`; they produce `list[Artifact]`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable  # noqa: TC003
from dataclasses import dataclass, field
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Protocol

import aiofiles

if TYPE_CHECKING:
    from acheron.core.models import JsonValue


class Input(Protocol):
    """Transport-neutral input handed to WorkerHandler.handle() alongside the Job."""

    @property
    def content_type(self) -> str:  # noqa: D102
        ...

    @property
    def metadata(self) -> dict[str, JsonValue]:  # noqa: D102
        ...

    def stream(self) -> AsyncIterator[bytes]:  # noqa: D102
        ...


@dataclass(frozen=True)
class BytesInput:
    """In-memory bytes — short audio, embedded text."""

    content_type: str
    data: bytes
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    async def stream(self) -> AsyncIterator[bytes]:
        """Yield the in-memory bytes as a single chunk."""
        yield self.data


@dataclass(frozen=True)
class StreamInput:
    """Lazily-produced chunks — long audio, bounded memory."""

    content_type: str
    producer: Callable[[], AsyncIterator[bytes]]
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    async def stream(self) -> AsyncIterator[bytes]:
        """Yield chunks produced by ``self.producer()``."""
        async for chunk in self.producer():
            yield chunk


@dataclass(frozen=True)
class FileInput:
    """Worker reads from disk (shared-volume mode or tmp file)."""

    content_type: str
    path: Path
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    async def stream(self) -> AsyncIterator[bytes]:
        """Yield the file's contents in 64 KiB chunks."""
        async with aiofiles.open(self.path, "rb") as f:
            while True:
                chunk = await f.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
