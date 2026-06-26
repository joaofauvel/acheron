"""RunPod Serverless handler for ibm-granite/granite-speech-4.1-2b."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol

from acheron.core.errors import WorkerError
from acheron.core.models import Job, JsonValue, WorkerCapabilities, WorkerType
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler
from workers._shared_utils import safe_chapter_id

if TYPE_CHECKING:
    from acheron.worker_sdk.inputs import Input
    from acheron.worker_sdk.settings import WorkerSettings


class _ModelProto(Protocol):
    """Surface the subset of the transformers model API the handler uses."""

    def generate(self, **kwargs: Any) -> Any: ...  # noqa: ANN401


class _ProcessorProto(Protocol):
    """Surface the subset of the transformers processor API the handler uses."""

    tokenizer: Any

    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...  # noqa: ANN401


_SUPPORTED_LANGS = frozenset({"en", "fr", "de", "es", "pt", "ja"})
_MODEL_ID_DEFAULT = "ibm-granite/granite-speech-4.1-2b"
_DEFAULT_PROMPT = "transcribe the speech with proper punctuation and capitalization."


class GraniteSpeechRunpodHandler(WorkerHandler):
    """Cloud-side handler run inside the RunPod serverless runtime image."""

    def __init__(self, settings: WorkerSettings) -> None:
        self._settings = settings
        self._model: _ModelProto | None = None
        self._processor: _ProcessorProto | None = None

    def capabilities(self) -> WorkerCapabilities:
        """Return the worker's static description. No I/O — sync."""
        model_id = self._settings.model_id or _MODEL_ID_DEFAULT
        metadata: dict[str, JsonValue] = {
            "asr_prompt": _DEFAULT_PROMPT,
            "health_provider": "runpod",
        }
        return WorkerCapabilities(
            worker_type=WorkerType.ASR,
            supported_languages_in=_SUPPORTED_LANGS,
            supported_languages_out=_SUPPORTED_LANGS,
            supported_formats_in=frozenset({"mp3", "wav"}),
            supported_formats_out=frozenset({"text"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=f"huggingface:{model_id}",
            metadata=metadata,
        )

    async def startup(self) -> None:
        """Eagerly load the model + processor at container boot."""
        import torch

        def _load() -> None:
            from transformers import (
                AutoModelForSpeechSeq2Seq,
                AutoProcessor,
            )

            model_id = self._settings.model_id or _MODEL_ID_DEFAULT
            self._processor = AutoProcessor.from_pretrained(model_id)
            self._model = AutoModelForSpeechSeq2Seq.from_pretrained(
                model_id,
                device_map="cuda:0",
                torch_dtype=torch.bfloat16,
                attn_implementation="flash_attention_2",
            )

        await asyncio.to_thread(_load)

    async def shutdown(self) -> None:
        """Release GPU memory on edge-shutdown."""
        if self._model is not None:
            del self._model
            self._model = None
        if self._processor is not None:
            del self._processor
            self._processor = None
        import torch

        torch.cuda.empty_cache()

    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:  # noqa: A002
        """Run ASR inference for the audio input. Returns a text/plain transcript per chapter."""
        if self._model is None or self._processor is None:
            msg = "Granite-Speech model not loaded (startup() not run)"
            raise WorkerError(msg)
        if input is None:
            msg = "Granite-Speech requires an audio input"
            raise WorkerError(msg)
        source_lang = job.payload.get("source_language")
        if not isinstance(source_lang, str) or source_lang not in _SUPPORTED_LANGS:
            msg = f"Unsupported source language: {source_lang!r}"
            raise WorkerError(msg)

        audio_bytes = b"".join([chunk async for chunk in input.stream()])
        if not audio_bytes:
            msg = "Empty audio input"
            raise WorkerError(msg)

        transcript = await asyncio.to_thread(self._transcribe, audio_bytes)
        chapter_id = safe_chapter_id(job.chapter_id)
        model_id = self._settings.model_id or _MODEL_ID_DEFAULT
        return [
            BytesArtifact(
                filename=f"{chapter_id}.txt",
                content_type="text/plain",
                data=transcript.encode("utf-8"),
                metadata={
                    "chapter_id": chapter_id,
                    "model": model_id,
                    "language": source_lang,
                },
            )
        ]

    def _transcribe(self, audio_bytes: bytes) -> str:
        """Run transformers inference; returns the transcript string."""
        import torch

        if self._model is None or self._processor is None:
            msg = "Granite-Speech model not loaded"
            raise WorkerError(msg)
        processor = self._processor
        model = self._model

        chat = [{"role": "user", "content": f"<|audio|>{_DEFAULT_PROMPT}"}]
        prompt_text = processor.tokenizer.apply_chat_template(chat, tokenize=False, add_generation_prompt=True)
        model_inputs = processor(
            prompt_text,
            audio_bytes,
            device="cuda:0",
            return_tensors="pt",
        ).to("cuda:0")
        with torch.inference_mode():
            model_outputs = model.generate(**model_inputs, max_new_tokens=4096, do_sample=False, num_beams=1)
        num_input_tokens = model_inputs["input_ids"].shape[-1]
        new_tokens = model_outputs[0, num_input_tokens:].unsqueeze(0)
        text: list[str] = processor.tokenizer.batch_decode(
            new_tokens, add_special_tokens=False, skip_special_tokens=True
        )
        return text[0].strip()
