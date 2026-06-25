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
from workers._shared_utils import parse_chunks_json, safe_chapter_id

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
_MODEL_ID_DEFAULT = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
_MAX_INPUT_TOKENS_DEFAULT = 2048


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
        model_id = self._settings.model_id or _MODEL_ID_DEFAULT
        max_input_tokens = self._settings.max_input_tokens or _MAX_INPUT_TOKENS_DEFAULT
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
            max_input_tokens=max_input_tokens,
            model_source=f"huggingface:{model_id}",
            metadata=metadata,
        )

    async def startup(self) -> None:
        """Eagerly load the model onto the GPU at container boot."""
        import torch  # noqa: PLC0415 - keep torch import out of test contexts

        def _load() -> None:
            from qwen_tts import Qwen3TTSModel  # noqa: PLC0415 - lazy, not always installed

            model_id = self._settings.model_id or _MODEL_ID_DEFAULT
            self._model = Qwen3TTSModel.from_pretrained(
                model_id,
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

    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:  # noqa: A002
        """Run batched custom-voice inference for all chunks in the job.

        Chunks arrive via the ``input`` parameter (8b's ``BytesInput`` Protocol):
        JSON-serialised ``chunks.json`` from the upstream chunking step. ``input``
        is required; ``job.payload["chunks"]`` is no longer a supported path.
        """
        if self._model is None:
            msg = "Qwen3-TTS model not loaded (startup() not run)"
            raise WorkerError(msg)
        if input is None:
            msg = "Qwen3-TTS requires a chunks.json input (multipart part)"
            raise WorkerError(msg)
        chunks = await parse_chunks_json(input)
        if not chunks:
            return []
        target_lang = self._validate_target_lang(job)
        qwen_lang = _LANG_MAP[target_lang]
        speaker = self._resolve_speaker(job, target_lang)

        texts = [c.text for c in chunks]
        languages = [qwen_lang] * len(chunks)
        speakers = [speaker] * len(chunks)
        instructs = [c.instruct for c in chunks]

        import soundfile as sf  # noqa: PLC0415 - lazy, not always installed

        def _generate() -> tuple[list[Any], int]:
            return self._model.generate_custom_voice(  # type: ignore[no-any-return]
                text=texts, language=languages, speaker=speakers, instruct=instructs
            )

        wavs, sr = await asyncio.to_thread(_generate)

        artifacts: list[Artifact] = []
        for wav, chunk in zip(wavs, chunks, strict=True):
            buf = io.BytesIO()
            sf.write(buf, wav, sr, format="WAV")
            chapter_id = safe_chapter_id(chunk.chapter_id)
            artifacts.append(
                BytesArtifact(
                    filename=f"{chapter_id}_{chunk.sequence_id:04d}.wav",
                    content_type="audio/wav",
                    data=buf.getvalue(),
                    metadata={
                        "sequence_id": chunk.sequence_id,
                        "chapter_id": chapter_id,
                        "sample_rate": sr,
                    },
                )
            )
        return artifacts

    def _validate_target_lang(self, job: Job) -> str:
        target_lang = job.payload.get("target_language")
        if not isinstance(target_lang, str) or target_lang not in _LANG_MAP:
            msg = f"Unsupported target language: {target_lang!r}"
            raise WorkerError(msg)
        return target_lang

    def _resolve_speaker(self, job: Job, target_lang: str) -> str:
        speaker = job.payload.get("speaker")
        if not isinstance(speaker, str) or not speaker:
            speaker = self._settings.per_language_defaults.get(target_lang, self._settings.default_speaker)
        if speaker not in _ALL_SPEAKERS:
            msg = f"Unknown speaker '{speaker}' in worker config"
            raise WorkerError(msg)
        return speaker
