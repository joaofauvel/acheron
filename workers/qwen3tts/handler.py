"""RunPod Serverless handler for Qwen3-TTS-12Hz-1.7B-CustomVoice."""

from __future__ import annotations

import asyncio
import io
import re
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

from acheron.core.errors import WorkerError
from acheron.core.models import Job, JsonValue, WorkerCapabilities, WorkerType
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler
from workers._shared_utils import Chunk, parse_chunks_json, safe_chapter_id

if TYPE_CHECKING:
    from acheron.worker_sdk.inputs import Input
    from acheron.worker_sdk.settings import WorkerSettings


@runtime_checkable
class _Qwen3TTSModelProto(Protocol):
    """Surface the subset of the qwen-tts model API the handler uses."""

    def generate_custom_voice(
        self,
        text: list[str],
        language: list[str],
        speaker: list[str],
        instruct: list[str],
    ) -> tuple[list[Any], int]: ...


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
        self._model: _Qwen3TTSModelProto | None = None

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
                attn_implementation="sdpa",
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
        model = self._model
        chunks = await parse_chunks_json(input)
        if not chunks:
            return []
        target_lang = self._validate_target_lang(job)
        qwen_lang = _LANG_MAP[target_lang]
        speakers = [self._resolve_speaker_for_chunk(chunk, job, target_lang) for chunk in chunks]

        texts = [c.text for c in chunks]
        languages = [qwen_lang] * len(chunks)
        instructs = [c.instruct for c in chunks]

        import soundfile as sf  # noqa: PLC0415 - lazy, not always installed

        def _generate() -> tuple[list[Any], int]:
            return model.generate_custom_voice(text=texts, language=languages, speaker=speakers, instruct=instructs)

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
            msg = "Unsupported target language"
            raise WorkerError(msg)
        return target_lang

    def _resolve_speaker_for_chunk(self, chunk: Chunk, job: Job, target_lang: str) -> str:
        chapter = _chapter_number(chunk.chapter_id)
        has_voice = "voice" in job.payload
        has_voice_map = "voice_map" in job.payload

        default_voice: str | None = None
        if has_voice:
            raw_voice = job.payload["voice"]
            if not isinstance(raw_voice, str) or not raw_voice.strip():
                msg = "voice must be a non-empty string"
                raise WorkerError(msg)
            default_voice = self._validate_speaker(raw_voice)

        for start_chapter, end_chapter, voice in self._voice_ranges(job):
            if start_chapter <= chapter <= end_chapter:
                return voice

        if default_voice is not None:
            return default_voice
        if has_voice_map:
            msg = f"No voice configured for chapter {chapter}"
            raise WorkerError(msg)

        configured_voice = self._settings.per_language_defaults.get(target_lang, self._settings.default_speaker)
        if not configured_voice.strip():
            msg = f"No voice configured for chapter {chapter}"
            raise WorkerError(msg)
        return self._validate_speaker(configured_voice)

    def _voice_ranges(self, job: Job) -> list[tuple[int, int, str]]:
        raw_ranges = job.payload.get("voice_map")
        if raw_ranges is None and "voice_map" not in job.payload:
            return []
        if not isinstance(raw_ranges, list):
            msg = "voice_map must be a list"
            raise WorkerError(msg)

        ranges: list[tuple[int, int, str]] = []
        for index, raw_range in enumerate(raw_ranges):
            if not isinstance(raw_range, dict):
                msg = f"voice_map[{index}] must be an object"
                raise WorkerError(msg)
            start = raw_range.get("start_chapter")
            end = raw_range.get("end_chapter")
            voice = raw_range.get("voice")
            if (
                isinstance(start, bool)
                or not isinstance(start, int)
                or isinstance(end, bool)
                or not isinstance(end, int)
            ):
                msg = f"voice_map[{index}] chapter bounds must be integers"
                raise WorkerError(msg)
            if start < 1 or end < start:
                msg = f"voice_map[{index}] has an invalid chapter range"
                raise WorkerError(msg)
            if not isinstance(voice, str) or not voice.strip():
                msg = f"voice_map[{index}].voice is required"
                raise WorkerError(msg)
            ranges.append((start, end, self._validate_speaker(voice)))
        return ranges

    @staticmethod
    def _validate_speaker(voice: str) -> str:
        if voice not in _ALL_SPEAKERS:
            msg = "Unknown speaker"
            raise WorkerError(msg)
        return voice


def _chapter_number(chapter_id: str) -> int:
    safe_chapter_id(chapter_id)
    match = re.fullmatch(r"(?:ch|chapter_)?(\d+)", chapter_id)
    if match is None:
        msg = "Invalid chapter_id"
        raise WorkerError(msg)
    chapter = int(match.group(1))
    if chapter < 1:
        msg = "Invalid chapter_id"
        raise WorkerError(msg)
    return chapter
