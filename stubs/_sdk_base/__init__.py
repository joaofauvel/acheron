"""Shared SDK-backed stub handlers for the SDK matrix.

The stubs exercise the SDK across local/runpod, http/grpc.
Each stub is a 30-line ``main.py`` calling ``create_worker_app`` from the SDK;
per-stub variance comes from ``worker.yaml`` + the handler class passed.
"""

from __future__ import annotations

import struct
from typing import Any

from acheron.core.models import Job, WorkerCapabilities, WorkerType
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler
from acheron.worker_sdk.inputs import Input


def _silent_wav(duration_ms: int = 100, sample_rate: int = 22050) -> bytes:
    num_samples = int(sample_rate * duration_ms / 1000)
    data_size = num_samples * 2
    return (
        b"RIFF"
        + struct.pack("<I", 36 + data_size)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
        + b"data"
        + struct.pack("<I", data_size)
        + b"\x00" * data_size
    )


class StubTTSHandler(WorkerHandler):
    """Deterministic TTS stub — emits a short silent WAV per chunk."""

    def __init__(self, _settings: Any) -> None:
        self._settings = _settings

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset({"en", "es", "fr", "de"}),
            supported_languages_out=frozenset({"en", "es", "fr", "de"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav"}),
            max_payload_bytes=None,
            batch_capable=True,
            model_source=None,
            metadata={"stub": True},
        )

    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:  # noqa: A002
        chunks = job.payload.get("chunks", [])
        if not isinstance(chunks, list) or not chunks:
            return [
                BytesArtifact(
                    filename="out.wav",
                    content_type="audio/wav",
                    data=_silent_wav(),
                    metadata={},
                )
            ]
        return [
            BytesArtifact(
                filename=f"{c.get('chapter_id', 'ch')}_{i:04d}.wav",
                content_type="audio/wav",
                data=_silent_wav(),
                metadata={"sequence_id": c.get("sequence_id", i) if isinstance(c, dict) else i},
            )
            for i, c in enumerate(chunks)
            if isinstance(c, dict)
        ]


class StubASRHandler(WorkerHandler):
    """Deterministic ASR stub — returns canned transcribed text."""

    def __init__(self, _settings: Any) -> None:
        self._settings = _settings

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            worker_type=WorkerType.ASR,
            supported_languages_in=frozenset({"en", "es", "fr", "de", "ja", "pt"}),
            supported_languages_out=frozenset({"en", "es", "fr", "de", "ja", "pt"}),
            supported_formats_in=frozenset({"mp3", "wav"}),
            supported_formats_out=frozenset({"text"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
            metadata={"stub": True},
        )

    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:  # noqa: A002
        # `input` is accepted and ignored — the stub proves the multipart
        # contract end-to-end without GPU.
        text = "mock transcription"
        return [
            BytesArtifact(
                filename=f"{job.chapter_id}.txt",
                content_type="text/plain",
                data=text.encode("utf-8"),
                metadata={"chapter_id": job.chapter_id},
            )
        ]


class StubTranslationHandler(WorkerHandler):
    """Deterministic translation stub — identity passthrough."""

    def __init__(self, _settings: Any) -> None:
        self._settings = _settings

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            worker_type=WorkerType.TRANSLATION,
            supported_languages_in=frozenset({"en", "es", "fr", "de"}),
            supported_languages_out=frozenset({"en", "es", "fr", "de"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"text"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
            metadata={"stub": True},
        )

    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:  # noqa: A002
        chunks = job.payload.get("chunks", [])
        translated: list[str] = []
        if isinstance(chunks, list):
            for c in chunks:
                if isinstance(c, dict):
                    text = c.get("text")
                    if isinstance(text, str):
                        translated.append(text)
        body = "\n\n".join(translated).encode("utf-8") if translated else b"mock translated text"
        return [
            BytesArtifact(
                filename=f"{job.chapter_id}.txt",
                content_type="text/plain",
                data=body,
                metadata={"chapter_id": job.chapter_id},
            )
        ]


__all__ = ["StubASRHandler", "StubTTSHandler", "StubTranslationHandler", "_silent_wav"]
