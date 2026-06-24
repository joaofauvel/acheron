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
import json
from typing import TYPE_CHECKING, Any

from acheron.core.errors import WorkerError
from acheron.core.models import Job, JsonValue, WorkerCapabilities, WorkerType
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler
from workers._shared import safe_chapter_id

if TYPE_CHECKING:
    from acheron.worker_sdk.inputs import Input
    from acheron.worker_sdk.settings import WorkerSettings

_MODEL_ID_DEFAULT = "google/translategemma-12b-it"
_MAX_INPUT_TOKENS = 2048
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
        return WorkerCapabilities(
            worker_type=WorkerType.TRANSLATION,
            supported_languages_in=_SUPPORTED_LANGS,
            supported_languages_out=_SUPPORTED_LANGS,
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"text"}),
            max_payload_bytes=None,
            batch_capable=True,
            max_input_tokens=_MAX_INPUT_TOKENS,
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
        """Translate the chunks from ``input`` (chunks.json as a multipart part)."""
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

        chunks_json_bytes = b"".join([chunk async for chunk in input.stream()])
        if not chunks_json_bytes:
            return []
        try:
            chunks_raw = json.loads(chunks_json_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            msg = f"chunks.json is not valid JSON: {exc}"
            raise WorkerError(msg) from exc
        if not isinstance(chunks_raw, list):
            msg = "chunks.json must be a JSON array of chunk dicts"
            raise WorkerError(msg)

        chunks = [_normalize_chunk(c) for c in chunks_raw]
        if not chunks:
            return []

        translated = await asyncio.to_thread(self._translate_all, chunks, src, tgt)

        model_id = self._settings.model_id or _MODEL_ID_DEFAULT
        artifacts: list[Artifact] = []
        for c, t in zip(chunks, translated, strict=True):
            chapter_id = safe_chapter_id(c["chapter_id"])
            artifacts.append(
                BytesArtifact(
                    filename=f"{chapter_id}_{c['sequence_id']:04d}.txt",
                    content_type="text/plain",
                    data=t.encode("utf-8"),
                    metadata={
                        "chapter_id": chapter_id,
                        "sequence_id": c["sequence_id"],
                        "source_language": src,
                        "target_language": tgt,
                        "model": model_id,
                    },
                )
            )
        return artifacts

    def _translate_all(
        self,
        chunks: list[dict[str, Any]],
        src: str,
        tgt: str,
    ) -> list[str]:
        """Run TranslateGemma in passes of _MAX_BATCH_SIZE; return translated strings in order."""
        out: list[str] = []
        for start in range(0, len(chunks), _MAX_BATCH_SIZE):
            batch = chunks[start : start + _MAX_BATCH_SIZE]
            out.extend(self._translate_batch(batch, src, tgt))
        return out

    def _translate_batch(
        self,
        batch: list[dict[str, Any]],
        src: str,
        tgt: str,
    ) -> list[str]:
        """Translate one batch (up to _MAX_BATCH_SIZE chunks) in a single model.generate call."""
        import torch

        messages_per_chunk = [
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "source_lang_code": src,
                            "target_lang_code": tgt,
                            "text": c["text"],
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
        tokenizer = self._processor.tokenizer
        if tokenizer.pad_token_id is None and tokenizer.eos_token_id is not None:
            tokenizer.pad_token_id = tokenizer.eos_token_id
        inputs = self._processor(
            text=prompts,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=_MAX_INPUT_TOKENS,
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


def _normalize_chunk(c: object) -> dict[str, Any]:
    """Validate and normalise a single chunk dict from chunks.json.

    Requires: ``chapter_id`` (str), ``sequence_id`` (int), ``text`` (str).
    Returns a plain dict usable by ``_translate_batch``.
    """
    if not isinstance(c, dict):
        msg = f"chunk must be a dict, got {type(c).__name__}"
        raise WorkerError(msg)
    if "chapter_id" not in c or not isinstance(c["chapter_id"], str):
        msg = "chunk.chapter_id is required (str)"
        raise WorkerError(msg)
    if "sequence_id" not in c or not isinstance(c["sequence_id"], int):
        msg = "chunk.sequence_id is required (int)"
        raise WorkerError(msg)
    if "text" not in c or not isinstance(c["text"], str):
        msg = "chunk.text is required (str)"
        raise WorkerError(msg)
    return {"chapter_id": c["chapter_id"], "sequence_id": c["sequence_id"], "text": c["text"]}
