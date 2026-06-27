"""Composable output artifact primitives for WorkerHandler.handle() returns."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable  # noqa: TC003
from dataclasses import dataclass, field
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from acheron.worker_sdk._io import Streamable, stream_bytes, stream_file, stream_producer

if TYPE_CHECKING:
    from acheron.core.models import JsonValue


@runtime_checkable
class Artifact(Streamable, Protocol):
    """Transport-neutral output produced by `WorkerHandler.handle()`."""

    @property
    def filename(self) -> str: ...  # noqa: D102


@dataclass(frozen=True)
class BytesArtifact:
    """In-memory bytes — chapter-level WAV, short text."""

    filename: str
    content_type: str
    data: bytes
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    async def stream(self) -> AsyncIterator[bytes]:
        """Yield the in-memory bytes as a single chunk."""
        async for chunk in stream_bytes(self.data):
            yield chunk


@dataclass(frozen=True)
class StreamArtifact:
    """Lazily-produced chunks — long audio, batched generation."""

    filename: str
    content_type: str
    producer: Callable[[], AsyncIterator[bytes]]
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    async def stream(self) -> AsyncIterator[bytes]:
        """Yield chunks produced by ``self.producer()``."""
        async for chunk in stream_producer(self.producer):
            yield chunk


@dataclass(frozen=True)
class FileArtifact:
    """Worker wrote to disk (shared-volume mode or a tmp file)."""

    filename: str
    content_type: str
    path: Path
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    async def stream(self) -> AsyncIterator[bytes]:
        """Yield the file's contents in 64 KiB chunks."""
        async for chunk in stream_file(self.path):
            yield chunk
