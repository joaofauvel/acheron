"""RunPod Serverless handler for Qwen3-TTS-12Hz-1.7B-CustomVoice.

This module runs **inside the RunPod serverless runtime image** (see
``Dockerfile.runpod``). The cloud-side ``runpod_entrypoint.py`` imports
``Qwen3TTSRunpodHandler`` here, calls ``startup()`` eagerly at boot, then
``runpod.serverless.start({"handler": make_runpod_handler(handler)})``.

A local-GPU fallback handler (``Qwen3TTSLocalHandler``) is deferred to a
separate future worker package — workers commit to one deployment mode by
being one mode, per the Layer 8a spec.
"""

from __future__ import annotations

import asyncio
import io
from typing import TYPE_CHECKING, Any, cast

from acheron.core.errors import WorkerError
from acheron.core.models import Job, JsonValue, WorkerCapabilities, WorkerType
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler
from workers._shared import safe_chapter_id

if TYPE_CHECKING:
    from acheron.worker_sdk.inputs import Input
    from acheron.worker_sdk.settings import WorkerSettings

_LANG_MAP = {
    "en": "English",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "de": "German",
    "fr": "French",
    "ru": "Russian",
    "pt": "Portuguese",
    "es": "Spanish",
    "it": "Italian",
}
_ALL_SPEAKERS = frozenset(
    {
        "Vivian",
        "Serena",
        "Uncle_Fu",
        "Dylan",
        "Eric",
        "Ryan",
        "Aiden",
        "Ono_Anna",
        "Sohee",
    }
)
_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"


def _chunk_text(c: dict[str, Any]) -> str:
    """Read the text field from a chunk dict."""
    if "text" not in c:
        msg = "chunk.text is required"
        raise WorkerError(msg)
    text = c["text"]
    if not isinstance(text, str):
        msg = f"chunk.text must be a str, got {type(text).__name__}"
        raise WorkerError(msg)
    return text


def _chunk_chapter_id(c: dict[str, Any]) -> str:
    r"""Read and sanitise the chapter_id field from a chunk dict.

    Delegates to ``workers._shared.safe_chapter_id``; the defensive checks
    against NUL bytes / path separators / ``..`` are shared with the
    granite-speech handler.
    """
    if "chapter_id" not in c:
        msg = "chunk.chapter_id is required"
        raise WorkerError(msg)
    cid = c["chapter_id"]
    if not isinstance(cid, str):
        msg = f"chunk.chapter_id must be a str, got {type(cid).__name__}"
        raise WorkerError(msg)
    return safe_chapter_id(cid)


class Qwen3TTSRunpodHandler(WorkerHandler):
    """Cloud-side handler run inside the RunPod serverless runtime image.

    Loads the model eagerly at boot (runpod_entrypoint.py calls startup()),
    then serve via runpod.serverless.start(...). The SDK's make_runpod_handler
    adapter invokes ``handle()`` for each incoming RunPod job.
    """

    def __init__(self, settings: WorkerSettings) -> None:
        self._settings = settings
        # The model is typed loosely so the workspace tests don't need torch.
        self._model: Any = None

    def capabilities(self) -> WorkerCapabilities:
        """Return the worker's static capabilities (no I/O, sync)."""
        metadata: dict[str, JsonValue] = {
            "speakers": cast("list[JsonValue]", sorted(_ALL_SPEAKERS)),
            "default_speaker": self._settings.default_speaker,
        }
        return WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset(_LANG_MAP),
            supported_languages_out=frozenset(_LANG_MAP),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav"}),
            max_payload_bytes=None,
            batch_capable=True,
            model_source=f"huggingface:{_MODEL_ID}",
            metadata=metadata,
        )

    async def startup(self) -> None:
        """Eagerly load the model onto the GPU at container boot."""
        import torch  # noqa: PLC0415 - keep torch import out of test contexts

        def _load() -> None:
            from qwen_tts import Qwen3TTSModel  # noqa: PLC0415 - lazy, not always installed

            self._model = Qwen3TTSModel.from_pretrained(
                _MODEL_ID,
                device_map="cuda:0",
                dtype=torch.bfloat16,
                attn_implementation="flash_attention_2",
            )

        await asyncio.to_thread(_load)

    async def shutdown(self) -> None:
        """Release GPU memory on edge-shutdown."""
        if self._model is not None:
            del self._model
            self._model = None
            import torch  # noqa: PLC0415 - keep torch import out of test contexts

            torch.cuda.empty_cache()

    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:  # noqa: A002, ARG002
        """Run batched custom-voice inference for all chunks in the job."""
        if self._model is None:
            msg = "Qwen3-TTS model not loaded (startup() not run)"
            raise WorkerError(msg)
        raw_chunks = job.payload.get("chunks", [])
        if not isinstance(raw_chunks, list) or not raw_chunks:
            return []
        chunks: list[dict[str, Any]] = [c for c in raw_chunks if isinstance(c, dict)]
        target_lang = job.payload.get("target_language")
        if not isinstance(target_lang, str) or target_lang not in _LANG_MAP:
            msg = f"Unsupported target language: {target_lang!r}"
            raise WorkerError(msg)
        qwen_lang = _LANG_MAP[target_lang]

        speaker = job.payload.get("speaker")
        if not isinstance(speaker, str) or not speaker:
            speaker = self._settings.per_language_defaults.get(target_lang, self._settings.default_speaker)
        if speaker not in _ALL_SPEAKERS:
            msg = f"Unknown speaker '{speaker}' in worker config"
            raise WorkerError(msg)

        texts = [_chunk_text(c) for c in chunks]
        languages = [qwen_lang] * len(chunks)
        speakers = [speaker] * len(chunks)
        instructs = [c.get("instruct", "") for c in chunks]

        import soundfile as sf  # noqa: PLC0415 - lazy, not always installed

        def _generate() -> tuple[list[Any], int]:
            return self._model.generate_custom_voice(  # type: ignore[no-any-return]
                text=texts, language=languages, speaker=speakers, instruct=instructs
            )

        wavs, sr = await asyncio.to_thread(_generate)

        artifacts: list[Artifact] = []
        for i, (wav, chunk) in enumerate(zip(wavs, chunks, strict=True)):
            buf = io.BytesIO()
            sf.write(buf, wav, sr, format="WAV")
            seq = chunk.get("sequence_id", i)
            artifacts.append(
                BytesArtifact(
                    filename=f"{_chunk_chapter_id(chunk)}_{seq:04d}.wav",
                    content_type="audio/wav",
                    data=buf.getvalue(),
                    metadata={
                        "sequence_id": seq,
                        "chapter_id": _chunk_chapter_id(chunk),
                        "sample_rate": sr,
                    },
                )
            )
        return artifacts
