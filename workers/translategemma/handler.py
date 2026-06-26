"""RunPod Serverless handler for google/translategemma-12b-it.

This module runs **inside the RunPod serverless runtime image** (see
``Dockerfile.runpod``). The cloud-side ``runpod_entrypoint.py`` imports
``TranslateGemmaRunpodHandler`` here, calls ``startup()`` eagerly at boot,
then ``runpod.serverless.start({"handler": make_runpod_handler(handler)})``.

A local-GPU fallback handler (``TranslateGemmaLocalHandler``) is deferred
to a separate future worker package — workers commit to one deployment mode
by being one mode, per the Layer 8a spec.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, cast

from acheron.core.errors import WorkerError
from acheron.core.models import Job, JsonValue, WorkerCapabilities, WorkerType
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler
from workers._shared_utils import Chunk, parse_chunks_json, safe_chapter_id

if TYPE_CHECKING:
    from acheron.worker_sdk.inputs import Input
    from acheron.worker_sdk.settings import WorkerSettings

logger = logging.getLogger(__name__)

_MODEL_ID_DEFAULT = "google/translategemma-12b-it"
_MAX_INPUT_TOKENS_DEFAULT = 2048
_MAX_BATCH_SIZE = 4
_MAX_NEW_TOKENS = 1024

# All 55 ISO 639-1 alpha-2 codes TranslateGemma supports. v1 advertises
# the full set so the orchestrator can plan any pair; language-path
# validation at plan compile time still rejects pairs outside
# SUPPORTED_LANGUAGES.
_SUPPORTED_LANGS = frozenset(
    {
        "af",
        "am",
        "ar",
        "az",
        "be",
        "bg",
        "bn",
        "bs",
        "ca",
        "cs",
        "cy",
        "da",
        "de",
        "el",
        "en",
        "es",
        "et",
        "fa",
        "fi",
        "fr",
        "ga",
        "gl",
        "gu",
        "he",
        "hi",
        "hr",
        "hu",
        "hy",
        "id",
        "is",
        "it",
        "ja",
        "ka",
        "kk",
        "km",
        "kn",
        "ko",
        "ky",
        "lo",
        "lt",
        "lv",
        "mk",
        "ml",
        "mn",
        "mr",
        "ms",
        "my",
        "ne",
        "nl",
        "no",
        "pa",
        "pl",
        "pt",
        "ro",
        "ru",
        "si",
        "sk",
        "sl",
        "sr",
        "sv",
        "sw",
        "ta",
        "te",
        "th",
        "tr",
        "uk",
        "ur",
        "vi",
        "zh",
    }
)


class TranslateGemmaRunpodHandler(WorkerHandler):
    """Cloud-side handler run inside the RunPod serverless runtime image."""

    def __init__(self, settings: WorkerSettings) -> None:
        self._settings = settings
        # The model + processor are typed loosely so the workspace tests
        # don't need torch or transformers installed.
        self._model: Any = None
        self._processor: Any = None

    def capabilities(self) -> WorkerCapabilities:
        """Return the worker's static description. No I/O — sync."""
        model_id = self._settings.model_id or _MODEL_ID_DEFAULT
        max_input_tokens = self._settings.max_input_tokens or _MAX_INPUT_TOKENS_DEFAULT
        return WorkerCapabilities(
            worker_type=WorkerType.TRANSLATION,
            supported_languages_in=_SUPPORTED_LANGS,
            supported_languages_out=_SUPPORTED_LANGS,
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"text"}),
            max_payload_bytes=None,
            batch_capable=True,
            max_input_tokens=max_input_tokens,
            model_source=f"huggingface:{model_id}",
        )

    async def startup(self) -> None:
        """Eagerly load the model + processor at container boot."""
        import torch

        def _load() -> None:
            from transformers import (
                AutoModelForImageTextToText,
                AutoProcessor,
            )

            model_id = self._settings.model_id or _MODEL_ID_DEFAULT
            self._processor = AutoProcessor.from_pretrained(model_id)
            self._model = AutoModelForImageTextToText.from_pretrained(
                model_id,
                device_map="cuda:0",
                torch_dtype=torch.bfloat16,
            )
            tokenizer = self._processor.tokenizer
            if tokenizer.pad_token_id is None and tokenizer.eos_token_id is not None:
                tokenizer.pad_token_id = tokenizer.eos_token_id

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
        """Translate the chunks from ``input`` (chunks.json as a multipart part).

        The RunPod forwarder always delivers chunks.json as a :class:`BytesInput`
        (base64-wrapped); for very long chapters (>10 MB) switch the cloud wrapper
        to a shared-volume :class:`FileInput` and update ``parse_chunks_json`` to
        stream the JSON array.
        """
        src, tgt = self._validate_payload(job, input)
        # _validate_payload raised if input is None; mypy needs the explicit cast.
        chunks = await self._parse_chunks(cast("Input", input))
        if not chunks:
            return []
        return await self._translate_and_artifact(chunks, src, tgt)

    def _validate_payload(self, job: Job, input: Input | None) -> tuple[str, str]:  # noqa: A002
        """Validate model-loaded, input-present, and src/tgt constraints; return (src, tgt)."""
        if self._model is None or self._processor is None:
            msg = "TranslateGemma model not loaded (startup() not run)"
            raise WorkerError(msg)
        if input is None:
            msg = "TranslateGemma requires a chunks.json input (multipart part)"
            raise WorkerError(msg)
        src = _require_str(job.payload, "source_language")
        tgt = _require_str(job.payload, "target_language")
        if src not in _SUPPORTED_LANGS:
            msg = f"Unsupported source language: {src!r}"
            raise WorkerError(msg)
        if tgt not in _SUPPORTED_LANGS:
            msg = f"Unsupported target language: {tgt!r}"
            raise WorkerError(msg)
        return src, tgt

    @staticmethod
    async def _parse_chunks(input: Input) -> list[Chunk]:  # noqa: A002
        """Parse the JSON-serialised ``chunks.json`` body from ``input`` into :class:`Chunk` objects."""
        return await parse_chunks_json(input)

    async def _translate_and_artifact(
        self,
        chunks: list[Chunk],
        src: str,
        tgt: str,
    ) -> list[Artifact]:
        """Run batched translation and build one :class:`BytesArtifact` per chunk."""
        translated = await asyncio.to_thread(self._translate_all, chunks, src, tgt)
        model_id = self._settings.model_id or _MODEL_ID_DEFAULT
        artifacts: list[Artifact] = []
        for c, t in zip(chunks, translated, strict=True):
            chapter_id = safe_chapter_id(c.chapter_id)
            artifacts.append(
                BytesArtifact(
                    filename=f"{chapter_id}_{c.sequence_id:04d}.txt",
                    content_type="text/plain",
                    data=t.encode("utf-8"),
                    metadata={
                        "chapter_id": chapter_id,
                        "sequence_id": c.sequence_id,
                        "source_language": src,
                        "target_language": tgt,
                        "model": model_id,
                    },
                )
            )
        return artifacts

    def _translate_all(
        self,
        chunks: list[Chunk],
        src: str,
        tgt: str,
    ) -> list[str]:
        """Run TranslateGemma in passes of _MAX_BATCH_SIZE; return translated strings in order.

        On per-batch failure (OOM, NaN, GPU fault), the successful translations are
        preserved and a :class:`WorkerError` is raised listing the failed batch
        indices. The operator can then retry — the previously translated chunks
        are recoverable from the partial ``out`` list logged before the raise.
        """
        out: list[str] = []
        failed_batches: list[tuple[int, int, int]] = []  # (batch_idx, start, end)
        for batch_idx, start in enumerate(range(0, len(chunks), _MAX_BATCH_SIZE)):
            batch = chunks[start : start + _MAX_BATCH_SIZE]
            try:
                out.extend(self._translate_batch(batch, src, tgt))
            except (RuntimeError, ValueError) as exc:
                end = start + len(batch) - 1
                logger.warning("batch %d (chunks %d-%d) failed: %s", batch_idx, start, end, exc)
                failed_batches.append((batch_idx, start, end))
        if failed_batches:
            translated_count = len(out)
            failed_count = sum(end - start + 1 for _, start, end in failed_batches)
            msg = (
                f"partial success: {translated_count}/{translated_count + failed_count} "
                f"chunks translated; failed batches: {failed_batches}"
            )
            raise WorkerError(msg) from None
        return out

    def _translate_batch(
        self,
        batch: list[Chunk],
        src: str,
        tgt: str,
    ) -> list[str]:
        """Translate one batch (up to _MAX_BATCH_SIZE chunks) in a single model.generate call."""
        import torch

        max_input_tokens = self._settings.max_input_tokens or _MAX_INPUT_TOKENS_DEFAULT
        messages_per_chunk = [
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "source_lang_code": src,
                            "target_lang_code": tgt,
                            "text": c.text,
                        }
                    ],
                }
            ]
            for c in batch
        ]
        prompts = [
            self._processor.apply_chat_template(m, tokenize=False, add_generation_prompt=True)
            for m in messages_per_chunk
        ]
        inputs = self._processor(
            text=prompts,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=max_input_tokens,
        ).to("cuda:0")
        with torch.inference_mode():
            outputs = self._model.generate(**inputs, max_new_tokens=_MAX_NEW_TOKENS, do_sample=False)
        decoded: list[str] = []
        for i in range(len(batch)):
            prompt_len = int(inputs["attention_mask"][i].sum())
            new_tokens = outputs[i, prompt_len:]
            text = self._processor.decode(new_tokens, skip_special_tokens=True).strip()
            decoded.append(text)
        return decoded


def _require_str(payload: dict[str, JsonValue], key: str) -> str:
    """Read a required string field from a job payload; raise WorkerError on missing/wrong type."""
    v = payload.get(key)
    if not isinstance(v, str):
        msg = f"{key} is required and must be a str (got {type(v).__name__ if v is not None else 'missing'})"
        raise WorkerError(msg)
    return v
