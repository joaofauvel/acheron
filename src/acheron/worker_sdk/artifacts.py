"""Composable output artifact primitives for WorkerHandler.handle() returns.

The multipart encoder / volume writer treat these uniformly via the
`Artifact` Protocol so workers mix-and-match the variant their model's API
naturally produces — no forced buffering.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from typing import ReadOnly

    from acheron.core.models import JsonValue


class Artifact(Protocol):
    """Transport-neutral output produced by `WorkerHandler.handle()`."""

    @property
    def filename(self) -> str: ...
    @property
    def content_type(self) -> str: ...
    @property
    def metadata(self) -> dict[str, JsonValue]: ...
    def stream(self) -> AsyncIterator[bytes]: ...


@dataclass(frozen=True)
class BytesArtifact:
    """In-memory bytes — chapter-level WAV, short text."""

    filename: str
    content_type: str
    data: bytes
    metadata: dict[str, "JsonValue"] = field(default_factory=dict)

    async def stream(self) -> AsyncIterator[bytes]:
        yield self.data


@dataclass(frozen=True)
class StreamArtifact:
    """Lazily-produced chunks — long audio, batched generation."""

    filename: str
    content_type: str
    producer: Callable[[], AsyncIterator[bytes]]
    metadata: dict[str, "JsonValue"] = field(default_factory=dict)

    async def stream(self) -> AsyncIterator[bytes]:
        async for chunk in self.producer():
            yield chunk


@dataclass(frozen=True)
class FileArtifact:
    """Worker wrote to disk (shared-volume mode or a tmp file)."""

    filename: str
    content_type: str
    path: Path
    metadata: dict[str, "JsonValue"] = field(default_factory=dict)

    async def stream(self) -> AsyncIterator[bytes]:
        import aiofiles

        async with aiofiles.open(self.path, "rb") as f:
            while True:
                chunk = await f.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
