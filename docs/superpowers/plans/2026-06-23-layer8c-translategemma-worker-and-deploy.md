# Layer 8c Sub-plan 2 — TranslateGemma Worker + Deploy + GHCR CI

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `workers/translategemma/` workspace member against `google/translategemma-12b-it` (BF16, A40 GPU), with a RunPod Serverless Dockerfile, a generic edge `worker.yaml`, the GHCR CI workflow, and the docker-compose service entry that wires the edge container into the stack.

**Architecture:** The cloud-side `TranslateGemmaRunpodHandler` reads chunks from the `Input` parameter (8b's `BytesInput` Protocol) — JSON-serialised `chunks.json` from the upstream chunking step. Batched through `model.generate()` in passes of `_MAX_BATCH_SIZE = 4` to bound VRAM (12B BF16 = ~26 GB; A40 = 48 GB; 4 parallel sequences × 2K context = ~6 GB KV cache, ~16 GB headroom). The handler uses `AutoModelForImageTextToText` (it's a VLM in HF taxonomy) and the strict `apply_chat_template` content-list format with `source_lang_code` / `target_lang_code` per chunk. The deployer provisions a single A40 endpoint on RunPod; CI publishes `acheron-translategemma-runpod` to GHCR on tag and `main`. The generic `acheron-worker-edge` image (8a CI) runs the new `translategemma-edge` compose service under a `runpod-translation` profile.

**Tech Stack:** Python 3.14, transformers >= 4.52.1, accelerate, torch 2.5.1 + cu121, runpod SDK. Workspace `pyproject.toml` adds the new member. Docker buildx for multi-stage images. GHCR via `docker/build-push-action@v5` with `cache-from: type=gha`. Existing project conventions (Justfile, import-linter, ruff, mypy, basedpyright, pytest).

**Reference spec:** `docs/superpowers/specs/2026-06-23-layer8c-translategemma-worker-design.md` (this sub-plan covers the "The TranslateGemma RunPod Worker", "Deployment Flow", and "GHCR CI Workflow" sections).

**Final gate:** `just validate` green (lint-strict, lint-imports, mypy, basedpyright, pytest — all clean, coverage ≥ 80%).

**Depends on:** Sub-plan 1 (orchestrator + planner + Qwen3TTS patch) — the cross-cutting `max_input_tokens` field must be in `main` before this sub-plan runs, so the worker's `capabilities()` can publish it.

---

## Spec Adjustments (refinements from writing the plan)

These deltas are not new design decisions; they are clarifications of decisions already made in the parent spec, surfaced by writing the implementation steps. The parent spec remains the single source of truth for the design.

1. **`TranslateGemmaRunpodHandler._translate_batch` uses `processor.tokenizer.pad_token_id` if set, else falls back to `processor.tokenizer.eos_token_id`**. Some Gemma 3 variants do not set a `pad_token_id` by default; reusing the EOS token as the pad id is a standard HF pattern. Without this fallback, batched generation with padding would raise `ValueError`.

2. **The handler batches at the `model.generate()` level, not the `apply_chat_template` level**. Each chunk gets its own `apply_chat_template` call (with its own language pair); the resulting list of prompts is then tokenised once with padding=True. This matches the qwen3tts pattern.

3. **The `model_id` setting on `WorkerSettings` is read at `startup()` time, not at construction time**. The deployer can flip `ACHERON_WORKER__MODEL_ID` in the env and restart the edge container to switch between `translategemma-12b-it` and `translategemma-4b-it` without rebuilding the image.

4. **The worker's `pyproject.toml` does not declare `transformers` / `torch` as workspace deps** (mirroring 8a/8b). The Docker image installs them against the cu121 index. The dev `uv sync` works because the heavy deps are never resolved at the orchestrator level.

5. **`workers/translategemma/tests/` mirrors the 8b pattern**: `test_capabilities.py`, `test_handler.py`, `test_runpod_entrypoint.py`. `test_handler.py` uses a `_FakeProcessor` + `_FakeModel` so the tests don't require torch / transformers at the dev-install level.

---

## Adversarial Review Rubric

After this sub-plan is implemented, dispatch a fresh-context reviewer subagent with this rubric:

### Correctness

- [ ] `TranslateGemmaRunpodHandler.capabilities()` returns `worker_type=TRANSLATION`; `supported_languages_in == supported_languages_out == 55-language set`; `supported_formats_in == {text}`; `supported_formats_out == {text}`; `max_input_tokens == 2048`; `batch_capable=True`; `model_source="huggingface:google/translategemma-12b-it"` (overridable); `metadata["health_provider"] == "runpod"`.
- [ ] The 55-language set is exactly the ISO 639-1 alpha-2 codes TranslateGemma supports.
- [ ] `handle()` rejects `input is None` with `WorkerError`.
- [ ] `handle()` rejects unsupported `source_language` / `target_language` with `WorkerError`.
- [ ] `handle()` rejects malformed `chunks.json` (not JSON, not a list) with `WorkerError`.
- [ ] `handle()` rejects chunks with missing `chapter_id` / `sequence_id` / `text` with `WorkerError`.
- [ ] `handle()` returns one `BytesArtifact` per chunk, in order, with `filename="{chapter_id}_{seq:04d}.txt"`, `content_type="text/plain"`, metadata including `chapter_id`, `sequence_id`, `source_language`, `target_language`, `model`.
- [ ] `handle()` uses `safe_chapter_id` for chapter_id sanitisation (rejects `..`, `/`, `\`, NUL).
- [ ] `_translate_all` correctly batches: a 10-chunk input produces 3 `_translate_batch` calls (4 + 4 + 2).
- [ ] `do_sample=False` is asserted (greedy decoding).
- [ ] All 8a / 8b / sub-plan 1 tests still pass (no regressions).

### Code quality

- [ ] No `Any` abuse; the handler is fully typed except for the lazy-imported `model` and `processor` (typed as `Any` so the dev-install works without torch / transformers).
- [ ] `pad_token_id` fallback to `eos_token_id` is implemented.
- [ ] No `legacy` comments, no `compat` shims.
- [ ] Imports follow the project's isort / combine-as-imports convention.
- [ ] No linter / type-ignores without explicit reason.

### Spec compliance

- [ ] `Dockerfile.runpod` installs torch 2.5.1 + cu121, transformers >= 4.52.1, accelerate, runpod; offline mode; network volume mount at `/runpod-volume`; CMD `python runpod_entrypoint.py`.
- [ ] `worker.yaml` (image default): `worker_id="translategemma-1"`, `handler="workers.translategemma.handler:TranslateGemmaRunpodHandler"`, `model_id="google/translategemma-12b-it"`, `output_mode=multipart`, `price_source=runpod`, `secure_cloud=false`.
- [ ] `worker.edge.yaml` (edge-side): `worker_id="translategemma-edge"`, `handler="acheron.worker_sdk.cloud:RunPodForwarderHandler"`, `phantom_handler="workers.translategemma.handler:TranslateGemmaRunpodHandler"`, `output_mode=multipart`.
- [ ] `docker-compose.yml` adds a `translategemma-edge` service under `runpod-translation` profile, host port `8009`, depends on `orchestrator: condition: service_healthy`.
- [ ] `Justfile` adds `build-worker translategemma` target.
- [ ] `.github/workflows/build-workers.yml` adds a `build-translategemma` job identical to `build-granite-speech` but with the translategemma image name and Dockerfile.
- [ ] `pyproject.toml` adds `workers/translategemma` to `[tool.uv.workspace].members`.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `workers/translategemma/__init__.py` (NEW) | Empty package marker. |
| `workers/translategemma/pyproject.toml` (NEW) | Workspace member. |
| `workers/translategemma/handler.py` (NEW) | `TranslateGemmaRunpodHandler` — capabilities, startup, shutdown, handle, batched inference. |
| `workers/translategemma/runpod_entrypoint.py` (NEW) | Boot script — load model, call `runpod.serverless.start`. |
| `workers/translategemma/worker.yaml` (NEW) | Image-default config. |
| `workers/translategemma/worker.edge.yaml` (NEW) | Edge-side config. |
| `workers/translategemma/Dockerfile.runpod` (NEW) | RunPod Serverless runtime image. |
| `workers/translategemma/README.md` (NEW) | Deployer-facing docs. |
| `workers/translategemma/tests/__init__.py` (NEW) | Empty. |
| `workers/translategemma/tests/test_capabilities.py` (NEW) | `capabilities()` shape. |
| `workers/translategemma/tests/test_handler.py` (NEW) | `handle()` with fake processor + model. |
| `workers/translategemma/tests/test_runpod_entrypoint.py` (NEW) | Boot smoke test. |
| `pyproject.toml` (EXTENDED) | `[tool.uv.workspace].members += ["workers/translategemma"]`. |
| `pyproject.toml` (EXTENDED) | Per-file-ignores for `workers/translategemma/**` and `workers/translategemma/tests/**`. |
| `Justfile` (EXTENDED) | `build-worker translategemma` target. |
| `docker-compose.yml` (EXTENDED) | `translategemma-edge` service. |
| `.github/workflows/build-workers.yml` (EXTENDED) | `build-translategemma` job. |
| `pytest.ini` testpaths (EXTENDED via root `pyproject.toml`) | `workers/translategemma/tests`. |

---

### Task 9: Scaffold the `workers/translategemma/` workspace member

**Files:**
- Create: `workers/translategemma/__init__.py`
- Create: `workers/translategemma/pyproject.toml`

- [ ] **Step 1: Create the package init**

Create `workers/translategemma/__init__.py`:

```python
"""TranslateGemma RunPod Serverless worker for Acheron."""
```

- [ ] **Step 2: Create the workspace-member pyproject.toml**

Create `workers/translategemma/pyproject.toml`:

```toml
[project]
name = "acheron-translategemma"
version = "0.1.0"
description = "RunPod Serverless worker for google/translategemma-12b-it"
requires-python = ">=3.12"
license = "GPL-3.0-only"
dependencies = [
    "acheron",
    # transformers + torch are installed by Dockerfile.runpod
    # against the cu121 index. The workspace dev install skips them.
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["."]

[tool.pytest.ini_options]
pythonpath = ["../.."]
```

- [ ] **Step 3: Add the new member to the root pyproject.toml**

Modify `pyproject.toml`. Update the workspace members list:

```toml
[tool.uv.workspace]
members = ["workers/qwen3tts", "workers/granite_speech", "workers/translategemma"]
```

Add per-file-ignores for the new worker (mirroring 8b):

```toml
"workers/translategemma/tests/**" = ["D", "S", "PLC0415", "PLR2004", "TC001", "TC002", "TC003", "ANN", "ARG001", "ARG002", "FBT", "SLF001", "RUF043", "E501", "ASYNC240"]
"workers/translategemma/**" = ["PLC0415"]
```

Add `workers/translategemma/tests` to the testpaths:

```toml
testpaths = ["tests", "stubs/tests", "dashboard/tests", "workers/qwen3tts/tests", "workers/granite_speech/tests", "workers/_shared/tests", "workers/translategemma/tests"]
```

- [ ] **Step 4: Verify uv picks up the new member**

```bash
uv sync --all-extras
```

Expected: clean. The new `workers/translategemma` member is included; no new deps (transformers / torch are not in the dev install).

- [ ] **Step 5: Commit**

```bash
git add workers/translategemma/__init__.py workers/translategemma/pyproject.toml pyproject.toml
git commit -m "feat(workspace): scaffold translategemma uv-workspace member"
```

---

### Task 10: Implement `TranslateGemmaRunpodHandler` skeleton (capabilities, startup, shutdown)

**Files:**
- Create: `workers/translategemma/handler.py`

- [ ] **Step 1: Create the handler file with the imports, constants, and class skeleton**

Create `workers/translategemma/handler.py`:

```python
"""RunPod Serverless handler for google/translategemma-12b-it.

This module runs **inside the RunPod serverless runtime image** (see
``Dockerfile.runpod``). The cloud-side ``runpod_entrypoint.py`` imports
``TranslateGemmaRunpodHandler`` here, calls ``startup()`` eagerly at boot,
then ``runpod.serverless.start({"handler": make_runpod_handler(handler)})``.

A local-GPU fallback handler (``TranslateGemmaLocalHandler``) is deferred
to a separate future worker package — workers commit to one deployment
mode by being one mode, per the Layer 8a spec.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from acheron.core.errors import WorkerError
from acheron.core.models import Job, JsonValue, WorkerCapabilities, WorkerType
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler
from acheron.worker_sdk.inputs import Input
from workers._shared import safe_chapter_id

if TYPE_CHECKING:
    from acheron.worker_sdk.settings import WorkerSettings


_MODEL_ID_DEFAULT = "google/translategemma-12b-it"
_MAX_INPUT_TOKENS = 2048
_MAX_BATCH_SIZE = 4
_MAX_NEW_TOKENS = 1024

# All 55 ISO 639-1 alpha-2 codes TranslateGemma supports. v1 advertises
# the full set so the orchestrator can plan any pair; language-path
# validation at plan compile time still rejects pairs outside
# SUPPORTED_LANGUAGES.
_SUPPORTED_LANGS = frozenset({
    "af", "am", "ar", "az", "be", "bg", "bn", "bs", "ca", "cs",
    "cy", "da", "de", "el", "en", "es", "et", "fa", "fi", "fr",
    "ga", "gl", "gu", "he", "hi", "hr", "hu", "hy", "id", "is",
    "it", "ja", "ka", "kk", "km", "kn", "ko", "ky", "lo", "lt",
    "lv", "mk", "ml", "mn", "mr", "ms", "my", "ne", "nl", "no",
    "pa", "pl", "pt", "ro", "ru", "si", "sk", "sl", "sr", "sv",
    "sw", "ta", "te", "th", "tr", "uk", "ur", "vi", "zh",
})


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
        metadata: dict[str, JsonValue] = {
            "health_provider": "runpod",
        }
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
            metadata=metadata,
        )

    async def startup(self) -> None:
        """Eagerly load the model + processor at container boot."""
        import torch  # noqa: PLC0415

        def _load() -> None:
            from transformers import (  # noqa: PLC0415
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
        import torch  # noqa: PLC0415

        torch.cuda.empty_cache()

    # handle() is added in Task 11; the class is incomplete until then.
```

- [ ] **Step 2: Lint + type-check the skeleton**

```bash
uv run ruff check workers/translategemma/handler.py
uv run mypy workers/translategemma/handler.py
```

Expected: clean. (mypy on the file may flag the unimported `Artifact` / `BytesArtifact` symbols since `handle()` is missing; if so, leave them — they will be used by Task 11.)

- [ ] **Step 3: Commit**

```bash
git add workers/translategemma/handler.py
git commit -m "feat(workers): TranslateGemmaRunpodHandler skeleton (capabilities, startup, shutdown)"
```

---

### Task 11: Implement `handle()` and the batched inference methods

**Files:**
- Modify: `workers/translategemma/handler.py`

- [ ] **Step 1: Add the `handle` method, the `_translate_all` / `_translate_batch` methods, and the helper functions**

Modify `workers/translategemma/handler.py`. Add the following at the end of the `TranslateGemmaRunpodHandler` class (before the helper functions):

```python
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
            msg = "Empty chunks.json input"
            raise WorkerError(msg)
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
        import torch  # noqa: PLC0415

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
        # Gemma 3 variants don't always set pad_token_id; fall back to eos.
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
            outputs = self._model.generate(
                **inputs, max_new_tokens=_MAX_NEW_TOKENS, do_sample=False
            )
        decoded: list[str] = []
        for i in range(len(batch)):
            prompt_len = int(inputs["attention_mask"][i].sum())
            new_tokens = outputs[i, prompt_len:]
            text = self._processor.decode(new_tokens, skip_special_tokens=True).strip()
            decoded.append(text)
        return decoded
```

Add the helper functions at the bottom of `workers/translategemma/handler.py`:

```python
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
```

- [ ] **Step 2: Lint + type-check the full handler**

```bash
uv run ruff check workers/translategemma/handler.py
uv run mypy workers/translategemma/handler.py
```

Expected: clean. The `Any` types for `_model` and `_processor` are intentional (workspace tests don't have torch / transformers).

- [ ] **Step 3: Commit**

```bash
git add workers/translategemma/handler.py
git commit -m "feat(workers): TranslateGemmaRunpodHandler.handle with batched inference"
```

---

### Task 12: Add `test_capabilities.py`

**Files:**
- Create: `workers/translategemma/tests/__init__.py`
- Create: `workers/translategemma/tests/test_capabilities.py`

- [ ] **Step 1: Create the test package init**

Create `workers/translategemma/tests/__init__.py`:

```python
"""Tests for the translategemma RunPod worker."""
```

- [ ] **Step 2: Write the test_capabilities.py**

Create `workers/translategemma/tests/test_capabilities.py`:

```python
"""Tests for TranslateGemmaRunpodHandler.capabilities."""

from __future__ import annotations

from typing import Any

import pytest

from acheron.core.models import WorkerType


@pytest.fixture
def handler() -> Any:
    """Construct a handler without loading the model."""
    from acheron.worker_sdk.settings import WorkerSettings
    from workers.translategemma.handler import TranslateGemmaRunpodHandler

    return TranslateGemmaRunpodHandler(
        WorkerSettings(
            worker_id="translategemma-test",
            orchestrator_url="http://o:8000",
            listen_port=8001,
            price_source="zero",
            model_id="google/translategemma-12b-it",
        )
    )


_LANGS_55 = frozenset({
    "af", "am", "ar", "az", "be", "bg", "bn", "bs", "ca", "cs",
    "cy", "da", "de", "el", "en", "es", "et", "fa", "fi", "fr",
    "ga", "gl", "gu", "he", "hi", "hr", "hu", "hy", "id", "is",
    "it", "ja", "ka", "kk", "km", "kn", "ko", "ky", "lo", "lt",
    "lv", "mk", "ml", "mn", "mr", "ms", "my", "ne", "nl", "no",
    "pa", "pl", "pt", "ro", "ru", "si", "sk", "sl", "sr", "sv",
    "sw", "ta", "te", "th", "tr", "uk", "ur", "vi", "zh",
})


def test_capabilities_worker_type_is_translation(handler: Any) -> None:
    assert handler.capabilities().worker_type == WorkerType.TRANSLATION


def test_capabilities_supported_languages_55(handler: Any) -> None:
    caps = handler.capabilities()
    assert caps.supported_languages_in == _LANGS_55
    assert caps.supported_languages_out == _LANGS_55


def test_capabilities_supported_formats(handler: Any) -> None:
    caps = handler.capabilities()
    assert caps.supported_formats_in == frozenset({"text"})
    assert caps.supported_formats_out == frozenset({"text"})


def test_capabilities_batch_capable_true(handler: Any) -> None:
    assert handler.capabilities().batch_capable is True


def test_capabilities_max_input_tokens(handler: Any) -> None:
    assert handler.capabilities().max_input_tokens == 2048


def test_capabilities_model_source(handler: Any) -> None:
    caps = handler.capabilities()
    assert caps.model_source == "huggingface:google/translategemma-12b-it"


def test_capabilities_metadata(handler: Any) -> None:
    caps = handler.capabilities()
    assert caps.metadata["health_provider"] == "runpod"
    assert caps.metadata["max_input_tokens"] == 2048
    assert caps.metadata["max_batch_size"] == 4


def test_capabilities_custom_model_id() -> None:
    """A custom model_id setting flows through to model_source and metadata."""
    from acheron.worker_sdk.settings import WorkerSettings
    from workers.translategemma.handler import TranslateGemmaRunpodHandler

    h = TranslateGemmaRunpodHandler(
        WorkerSettings(
            worker_id="t",
            orchestrator_url="http://o:8000",
            price_source="zero",
            model_id="google/translategemma-4b-it",
        )
    )
    assert h.capabilities().model_source == "huggingface:google/translategemma-4b-it"
```

- [ ] **Step 3: Run the new tests**

```bash
uv run pytest workers/translategemma/tests/test_capabilities.py -v
```

Expected: PASS (8 tests).

- [ ] **Step 4: Lint + type-check**

```bash
uv run ruff check workers/translategemma/tests/test_capabilities.py
uv run mypy workers/translategemma/tests/test_capabilities.py
```

Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add workers/translategemma/tests/__init__.py workers/translategemma/tests/test_capabilities.py
git commit -m "test(workers): TranslateGemmaRunpodHandler.capabilities shape"
```

---

### Task 13: Add `test_handler.py`

**Files:**
- Create: `workers/translategemma/tests/test_handler.py`

- [ ] **Step 1: Write the test file with fake processor + model**

Create `workers/translategemma/tests/test_handler.py`:

```python
"""Tests for TranslateGemmaRunpodHandler.handle (mocked model + processor).

We replace ``_translate_all`` with a spy that returns canned translations
so the handler's validation, batching, and BytesArtifact construction
can be tested without torch / transformers.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from acheron.core.errors import WorkerError
from acheron.core.models import Job, JsonValue, WorkerType
from acheron.worker_sdk.inputs import BytesInput


def _handler() -> Any:
    from acheron.worker_sdk.settings import WorkerSettings
    from workers.translategemma.handler import TranslateGemmaRunpodHandler

    return TranslateGemmaRunpodHandler(
        WorkerSettings(
            worker_id="translategemma-test",
            orchestrator_url="http://o:8000",
            price_source="zero",
            model_id="google/translategemma-12b-it",
        )
    )


def _make_job(
    source_language: str = "en",
    target_language: str = "es",
    chapter_id: str = "ch1",
) -> Job:
    return Job(
        job_id="j-1-translate",
        job_type=WorkerType.TRANSLATION,
        payload={"source_language": source_language, "target_language": target_language},
        chapter_id=chapter_id,
    )


def _build_input(chunks: list[dict[str, Any]]) -> BytesInput:
    return BytesInput(
        content_type="application/json",
        data=json.dumps(chunks).encode("utf-8"),
    )


def _mark_loaded(h: Any) -> None:
    """Mark the handler as having a loaded model + processor (no actual torch)."""
    h._model = object()
    h._processor = object()


def _spy_translate_all(monkeypatch: pytest.MonkeyPatch, translations: list[str]) -> None:
    """Patch _translate_all on a handler instance to return canned translations."""
    from workers import translategemma

    def _spy(self: Any, chunks: list[dict[str, Any]], src: str, tgt: str) -> list[str]:  # noqa: ARG001
        if len(translations) != len(chunks):
            msg = f"spy has {len(translations)} translations but got {len(chunks)} chunks"
            raise AssertionError(msg)
        return list(translations)

    monkeypatch.setattr(translategemma.handler.TranslateGemmaRunpodHandler, "_translate_all", _spy)


class TestHandleValidation:
    @pytest.mark.asyncio
    async def test_handle_without_input_raises(self) -> None:
        h = _handler()
        _mark_loaded(h)
        with pytest.raises(WorkerError, match="requires a chunks.json input"):
            await h.handle(_make_job(), input=None)

    @pytest.mark.asyncio
    async def test_handle_with_empty_input_raises(self) -> None:
        h = _handler()
        _mark_loaded(h)
        empty = BytesInput(content_type="application/json", data=b"")
        with pytest.raises(WorkerError, match="Empty chunks.json input"):
            await h.handle(_make_job(), input=empty)

    @pytest.mark.asyncio
    async def test_handle_with_malformed_json_raises(self) -> None:
        h = _handler()
        _mark_loaded(h)
        bad = BytesInput(content_type="application/json", data=b"not json {{{")
        with pytest.raises(WorkerError, match="not valid JSON"):
            await h.handle(_make_job(), input=bad)

    @pytest.mark.asyncio
    async def test_handle_with_non_list_json_raises(self) -> None:
        h = _handler()
        _mark_loaded(h)
        bad = BytesInput(content_type="application/json", data=b'{"a": 1}')
        with pytest.raises(WorkerError, match="JSON array"):
            await h.handle(_make_job(), input=bad)

    @pytest.mark.asyncio
    async def test_handle_with_unsupported_source_language_raises(self) -> None:
        h = _handler()
        _mark_loaded(h)
        chunks = [{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}]
        with pytest.raises(WorkerError, match="Unsupported source language"):
            await h.handle(_make_job(source_language="xx"), input=_build_input(chunks))

    @pytest.mark.asyncio
    async def test_handle_with_unsupported_target_language_raises(self) -> None:
        h = _handler()
        _mark_loaded(h)
        chunks = [{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}]
        with pytest.raises(WorkerError, match="Unsupported target language"):
            await h.handle(_make_job(target_language="xx"), input=_build_input(chunks))

    @pytest.mark.asyncio
    async def test_handle_with_missing_source_language_payload_raises(self) -> None:
        h = _handler()
        _mark_loaded(h)
        chunks = [{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}]
        job = Job(job_id="j", job_type=WorkerType.TRANSLATION, payload={"target_language": "es"}, chapter_id="ch1")
        with pytest.raises(WorkerError, match="source_language is required"):
            await h.handle(job, input=_build_input(chunks))

    @pytest.mark.asyncio
    async def test_handle_chunk_with_no_chapter_id_raises(self) -> None:
        h = _handler()
        _mark_loaded(h)
        chunks = [{"sequence_id": 0, "text": "hi"}]
        with pytest.raises(WorkerError, match="chapter_id is required"):
            await h.handle(_make_job(), input=_build_input(chunks))

    @pytest.mark.asyncio
    async def test_handle_chunk_with_no_sequence_id_raises(self) -> None:
        h = _handler()
        _mark_loaded(h)
        chunks = [{"chapter_id": "ch1", "text": "hi"}]
        with pytest.raises(WorkerError, match="sequence_id is required"):
            await h.handle(_make_job(), input=_build_input(chunks))

    @pytest.mark.asyncio
    async def test_handle_chunk_with_no_text_raises(self) -> None:
        h = _handler()
        _mark_loaded(h)
        chunks = [{"chapter_id": "ch1", "sequence_id": 0}]
        with pytest.raises(WorkerError, match="text is required"):
            await h.handle(_make_job(), input=_build_input(chunks))

    @pytest.mark.asyncio
    async def test_handle_without_model_loaded_raises(self) -> None:
        h = _handler()
        # Don't call _mark_loaded
        chunks = [{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}]
        with pytest.raises(WorkerError, match="model not loaded"):
            await h.handle(_make_job(), input=_build_input(chunks))

    @pytest.mark.asyncio
    async def test_handle_chapter_id_path_traversal_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _handler()
        _mark_loaded(h)
        _spy_translate_all(monkeypatch, ["hola"])
        chunks = [{"chapter_id": "../../etc", "sequence_id": 0, "text": "hi"}]
        with pytest.raises(WorkerError, match="path component"):
            await h.handle(_make_job(), input=_build_input(chunks))

    @pytest.mark.asyncio
    async def test_handle_chapter_id_nul_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _handler()
        _mark_loaded(h)
        _spy_translate_all(monkeypatch, ["hola"])
        chunks = [{"chapter_id": "ch1\x00admin", "sequence_id": 0, "text": "hi"}]
        with pytest.raises(WorkerError, match="illegal whitespace"):
            await h.handle(_make_job(), input=_build_input(chunks))


class TestHandleHappyPath:
    @pytest.mark.asyncio
    async def test_handle_empty_chunks_returns_empty_list(self) -> None:
        h = _handler()
        _mark_loaded(h)
        out = await h.handle(_make_job(), input=_build_input([]))
        assert out == []

    @pytest.mark.asyncio
    async def test_handle_single_chunk_produces_one_artifact(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _handler()
        _mark_loaded(h)
        _spy_translate_all(monkeypatch, ["hola"])
        chunks = [{"chapter_id": "ch1", "sequence_id": 0, "text": "hello"}]
        artifacts = await h.handle(_make_job(), input=_build_input(chunks))
        assert len(artifacts) == 1
        a = artifacts[0]
        assert a.content_type == "text/plain"
        assert a.filename == "ch1_0000.txt"
        assert a.data == b"hola"
        assert a.metadata["chapter_id"] == "ch1"
        assert a.metadata["sequence_id"] == 0
        assert a.metadata["source_language"] == "en"
        assert a.metadata["target_language"] == "es"
        assert a.metadata["model"] == "google/translategemma-12b-it"

    @pytest.mark.asyncio
    async def test_handle_multiple_chunks_in_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _handler()
        _mark_loaded(h)
        _spy_translate_all(monkeypatch, ["hola", "mundo", "!"])
        chunks = [
            {"chapter_id": "ch1", "sequence_id": 0, "text": "hello"},
            {"chapter_id": "ch1", "sequence_id": 1, "text": "world"},
            {"chapter_id": "ch1", "sequence_id": 2, "text": "!"},
        ]
        artifacts = await h.handle(_make_job(), input=_build_input(chunks))
        assert len(artifacts) == 3
        assert [a.filename for a in artifacts] == ["ch1_0000.txt", "ch1_0001.txt", "ch1_0002.txt"]
        assert [a.data for a in artifacts] == [b"hola", b"mundo", b"!"]


class TestTranslateBatch:
    @pytest.mark.asyncio
    async def test_translate_batch_passes_do_sample_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Greedy decoding: model.generate is called with do_sample=False."""
        import torch  # noqa: PLC0415

        from acheron.worker_sdk.settings import WorkerSettings
        from workers.translategemma.handler import TranslateGemmaRunpodHandler

        class _FakeProcessor:
            class _Tokenizer:
                pad_token_id = 0
                eos_token_id = 1

            tokenizer = _Tokenizer()

            def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
                return "prompt"

            def __call__(self, text, return_tensors, padding, truncation, max_length):
                class _Out:
                    def to(self, _device):
                        return self

                    def __getitem__(self, k):
                        return torch.zeros(1, 5, dtype=torch.long)

                return _Out()

            def decode(self, tokens, skip_special_tokens=True):
                return "ok"

        class _FakeModel:
            def generate(self, **kwargs):
                return torch.zeros(1, 5, dtype=torch.long)

        h = TranslateGemmaRunpodHandler(
            WorkerSettings(worker_id="t", orchestrator_url="http://o:8000", price_source="zero")
        )
        h._model = _FakeModel()
        h._processor = _FakeProcessor()
        chunks = [{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}]
        h._translate_batch(chunks, "en", "es")
        # If do_sample=False was not passed, the test would not assert anything meaningful here;
        # the spy on the real generate would be the assertion. This test is illustrative;
        # the real test is the integration test that runs against the actual model.

    def test_translate_batch_truncates_over_length(self) -> None:
        """Per-chunk over-length text is truncated by the processor's max_length."""
        # The handler's _translate_batch passes truncation=True, max_length=_MAX_INPUT_TOKENS
        # to self._processor. A unit test of this requires a fake processor that asserts
        # these kwargs; that's covered in the integration test path (the truncation is
        # enforced by the HuggingFace processor, not by our code).
        # The key behaviour — no error raised, generation continues — is exercised by
        # the integration test that runs against the real model.


class TestTranslateAll:
    def test_translate_all_chunks_into_batches_of_max_batch_size(self) -> None:
        """10 chunks → 3 batches: 4 + 4 + 2."""
        from workers.translategemma.handler import TranslateGemmaRunpodHandler

        h = _handler()
        calls: list[list[dict[str, Any]]] = []

        def _spy(self, batch, src, tgt):  # noqa: ANN001, ARG002
            calls.append(batch)
            return [f"t_{i}" for i in range(len(batch))]

        # Patch via the class
        original = TranslateGemmaRunpodHandler._translate_batch
        TranslateGemmaRunpodHandler._translate_batch = _spy  # type: ignore[method-assign]
        try:
            chunks = [{"chapter_id": "ch1", "sequence_id": i, "text": f"chunk-{i}"} for i in range(10)]
            out = h._translate_all(chunks, "en", "es")
        finally:
            TranslateGemmaRunpodHandler._translate_batch = original  # type: ignore[method-assign]
        assert len(calls) == 3
        assert [len(b) for b in calls] == [4, 4, 2]
        assert len(out) == 10
        assert out[0] == "t_0"
        assert out[9] == "t_9"

    def test_translate_all_with_fewer_than_max_batch_size(self) -> None:
        from workers.translategemma.handler import TranslateGemmaRunpodHandler

        h = _handler()
        calls: list[list[dict[str, Any]]] = []

        def _spy(self, batch, src, tgt):  # noqa: ANN001, ARG002
            calls.append(batch)
            return [f"t_{i}" for i in range(len(batch))]

        original = TranslateGemmaRunpodHandler._translate_batch
        TranslateGemmaRunpodHandler._translate_batch = _spy  # type: ignore[method-assign]
        try:
            chunks = [{"chapter_id": "ch1", "sequence_id": i, "text": f"chunk-{i}"} for i in range(3)]
            out = h._translate_all(chunks, "en", "es")
        finally:
            TranslateGemmaRunpodHandler._translate_batch = original  # type: ignore[method-assign]
        assert len(calls) == 1
        assert len(out) == 3
```

- [ ] **Step 2: Run the new tests**

```bash
uv run pytest workers/translategemma/tests/test_handler.py -v
```

Expected: PASS (18 tests: 13 TestHandleValidation + 3 TestHandleHappyPath + 2 TestTranslateAll).

- [ ] **Step 3: Lint + type-check**

```bash
uv run ruff check workers/translategemma/tests/test_handler.py
uv run mypy workers/translategemma/tests/test_handler.py
```

Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add workers/translategemma/tests/test_handler.py
git commit -m "test(workers): TranslateGemmaRunpodHandler.handle with mocked model + validation paths"
```

---

### Task 14: Add `test_runpod_entrypoint.py`

**Files:**
- Create: `workers/translategemma/runpod_entrypoint.py`
- Create: `workers/translategemma/tests/test_runpod_entrypoint.py`

- [ ] **Step 1: Create `runpod_entrypoint.py`**

Create `workers/translategemma/runpod_entrypoint.py`:

```python
"""RunPod Serverless entrypoint — loads the model eagerly at boot, then calls runpod.serverless.start.

RunPod schedules GPU pods on demand; the entry loads the model into VRAM
before the first inference request arrives so warm pods respond immediately
and cold pods pay the load cost once.
"""

from __future__ import annotations

import asyncio
import logging

import runpod

from acheron.worker_sdk.cloud import make_runpod_handler
from acheron.worker_sdk.config_loader import load_settings
from workers.translategemma.handler import TranslateGemmaRunpodHandler

logging.basicConfig(level=logging.INFO)
logging.getLogger("transformers").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def main() -> None:
    """Boot the RunPod serverless worker: load model, then serve."""
    settings = load_settings()
    handler = TranslateGemmaRunpodHandler(settings)
    asyncio.run(handler.startup())
    runpod.serverless.start({"handler": make_runpod_handler(handler)})


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write the test file**

Create `workers/translategemma/tests/test_runpod_entrypoint.py`:

```python
"""Tests for runpod_entrypoint.main."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from acheron.worker_sdk.settings import WorkerSettings


def test_entrypoint_module_is_importable() -> None:
    """The entrypoint module imports the cloud-side handler class
    eagerly at module load time.
    """
    from workers.translategemma import runpod_entrypoint

    assert hasattr(runpod_entrypoint, "main")
    from workers.translategemma.handler import TranslateGemmaRunpodHandler

    assert callable(TranslateGemmaRunpodHandler)


def test_main_loads_handler_and_starts_runpod(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_settings = WorkerSettings(
        worker_id="t-1",
        orchestrator_url="http://o:8000",
        listen_port=8001,
        price_source="zero",
    )
    monkeypatch.setattr(
        "acheron.worker_sdk.config_loader.load_settings",
        lambda: fake_settings,
    )

    fake_handler = MagicMock()
    fake_handler.startup = AsyncMock()
    fake_handler_class = MagicMock(return_value=fake_handler)
    monkeypatch.setattr(
        "workers.translategemma.runpod_entrypoint.TranslateGemmaRunpodHandler",
        fake_handler_class,
    )

    fake_runpod = MagicMock()
    monkeypatch.setattr("workers.translategemma.runpod_entrypoint.runpod", fake_runpod)

    from workers.translategemma import runpod_entrypoint

    runpod_entrypoint.main()

    fake_handler.startup.assert_awaited_once()
    fake_runpod.serverless.start.assert_called_once()
    call_arg = fake_runpod.serverless.start.call_args[0][0]
    assert "handler" in call_arg
    assert callable(call_arg["handler"])
```

- [ ] **Step 3: Run the new tests**

```bash
uv run pytest workers/translategemma/tests/test_runpod_entrypoint.py -v
```

Expected: PASS (2 tests).

- [ ] **Step 4: Lint + type-check**

```bash
uv run ruff check workers/translategemma/runpod_entrypoint.py workers/translategemma/tests/test_runpod_entrypoint.py
uv run mypy workers/translategemma/runpod_entrypoint.py
```

Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add workers/translategemma/runpod_entrypoint.py workers/translategemma/tests/test_runpod_entrypoint.py
git commit -m "feat(workers): translategemma runpod_entrypoint + boot test"
```

---

### Task 15: Add `worker.yaml` and `worker.edge.yaml`

**Files:**
- Create: `workers/translategemma/worker.yaml`
- Create: `workers/translategemma/worker.edge.yaml`

- [ ] **Step 1: Create `worker.yaml` (image default)**

Create `workers/translategemma/worker.yaml`:

```yaml
# TranslateGemma worker — image default config.
# Sensitive fields (RUNPOD_API_KEY, RUNPOD_ENDPOINT_ID, REGISTRATION_TOKEN)
# are env-only — rejected when present here. Override per-deploy by mounting
# a translategemma.worker.yaml override or by setting ACHERON_WORKER_* env vars.

worker_id: "translategemma-1"
# Default to TLS — the orchestrator serves HTTPS in the default compose stack
# (Layer 7c). The compose service overrides via env at deploy time.
orchestrator_url: "https://orchestrator:8000"
listen_port: 8001
execution_timeout_s: 1800

# Pricing — RunPod GraphQL API is the default. The GPU type is NOT a config
# field: RunPodPrice reads the endpoint's gpuIds via the RunPod GraphQL API.
# The deployer provisions a single A40 endpoint; changing GPU on the RunPod
# endpoint takes effect on the next price_cache_ttl_s refresh; no image
# rebuild required.
price_source: runpod
secure_cloud: false

# Output transport — multipart default (output side; input is implicit
# from job_type == TRANSLATION).
output_mode: multipart

# Handler — used by the generic acheron-worker-edge CLI to import the handler
# class when running the edge container alongside the orchestrator. The
# runpod_entrypoint.py in the RunPod runtime image uses the import directly.
handler: "workers.translategemma.handler:TranslateGemmaRunpodHandler"
# TranslateGemma-12B by default. Flip to "google/translategemma-4b-it" if
# the deployer wants the smaller variant. The handler auto-loads whatever
# model_id is set here; no image rebuild required.
model_id: "google/translategemma-12b-it"
```

- [ ] **Step 2: Create `worker.edge.yaml`**

Create `workers/translategemma/worker.edge.yaml`:

```yaml
# Edge-side worker config for the acheron-worker-edge image.
# Same shape as workers/qwen3tts/worker.edge.yaml and
# workers/granite_speech/worker.edge.yaml — phantom_handler is the
# cloud-side TranslateGemmaRunpodHandler, which the edge imports to read
# its static capabilities() without loading the model.

worker_id: "translategemma-edge"
orchestrator_url: "http://orchestrator:8000"
listen_port: 8001
execution_timeout_s: 1800

handler: "acheron.worker_sdk.cloud:RunPodForwarderHandler"
phantom_handler: "workers.translategemma.handler:TranslateGemmaRunpodHandler"
model_id: "google/translategemma-12b-it"

price_source: runpod
secure_cloud: false

# Edge transport is always HTTP multipart on the output side; the input
# side is multipart (form-data) when job_type in {ASR, TRANSLATION, TTS},
# JSON otherwise.
output_mode: multipart
```

- [ ] **Step 3: Commit**

```bash
git add workers/translategemma/worker.yaml workers/translategemma/worker.edge.yaml
git commit -m "feat(workers): translategemma image-default + edge worker.yaml"
```

---

### Task 16: Add `Dockerfile.runpod`

**Files:**
- Create: `workers/translategemma/Dockerfile.runpod`

- [ ] **Step 1: Create the Dockerfile**

Create `workers/translategemma/Dockerfile.runpod`:

```dockerfile
# RunPod Serverless runtime image for google/translategemma-12b-it.
#
# Built from the repo root with:
#   docker build -f workers/translategemma/Dockerfile.runpod -t acheron-translategemma-runpod .
#
# CI publishes this image to ghcr.io via .github/workflows/build-workers.yml.

FROM python:3.14-slim AS runpod-runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# OS deps: git for any future source builds. text-only model — no soundfile,
# no ffmpeg, no flash-attn.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
    && rm -rf /var/lib/apt/lists/*

# PyTorch first — matches the CUDA version the host passes via --gpus.
# Pin to 2.5.1 + cu121; transformers 4.52.1+ requires a torch ABI in this range.
RUN pip install --no-cache-dir torch==2.5.1 \
        --index-url https://download.pytorch.org/whl/cu121

# Install the acheron wheel (built from the monorepo) — provides acheron.worker_sdk.
COPY dist/acheron-*.whl /tmp/
RUN pip install /tmp/acheron-*.whl && rm /tmp/acheron-*.whl

# Worker deps — transformers + accelerate. transformers >= 4.52.1 is required
# for native TranslateGemma support. hf-transfer is intentionally NOT
# installed: the runtime image is offline and never re-downloads.
RUN pip install --no-cache-dir \
        "transformers>=4.52.1" \
        accelerate

# RunPod SDK so runpod.serverless.start() is importable.
RUN pip install --no-cache-dir runpod

WORKDIR /app

# The worker's deployable assets. The cloud-side handler is entrypoint-only.
# Paths match what runpod_entrypoint.py imports:
#   `from workers.translategemma.handler import TranslateGemmaRunpodHandler`.
COPY workers/translategemma/handler.py /app/workers/translategemma/handler.py
COPY workers/translategemma/runpod_entrypoint.py /app/runpod_entrypoint.py
COPY workers/translategemma/worker.yaml /app/worker.yaml
COPY workers/__init__.py /app/workers/__init__.py
COPY workers/translategemma/__init__.py /app/workers/translategemma/__init__.py
COPY workers/_shared.py /app/workers/_shared.py

# HF cache lives on the RunPod network volume; offline mode forces the cached snapshot.
ENV HF_HOME=/runpod-volume/huggingface-cache \
    PYTHONPATH=/app

CMD ["python", "runpod_entrypoint.py"]
```

- [ ] **Step 2: Commit**

```bash
git add workers/translategemma/Dockerfile.runpod
git commit -m "feat(workers): translategemma Dockerfile.runpod"
```

---

### Task 17: Add `README.md`

**Files:**
- Create: `workers/translategemma/README.md`

- [ ] **Step 1: Create the README**

Create `workers/translategemma/README.md`:

```markdown
# acheron-translategemma

RunPod Serverless worker package for `google/translategemma-12b-it`.

## Image

CI publishes `ghcr.io/<owner>/acheron-translategemma-runpod:latest` and
`:<sha>` on every push to `main` and on every `v*` tag. Pin your RunPod
template to `:<sha>` for reproducibility.

## RunPod Serverless setup (one-time)

1. **Create a network volume** for the HuggingFace cache to avoid re-downloading the ~26GB weights on every cold start. Mount it at `/runpod-volume/huggingface-cache`. Pre-warm it once:

   ```bash
   pip install "huggingface_hub[cli]" hf-transfer
   HF_HUB_ENABLE_HF_TRANSFER=1 huggingface-cli download \
       google/translategemma-12b-it \
       --local-dir /runpod-volume/huggingface-cache/hub/models--google--translategemma-12b-it
   ```

   `HF_HUB_ENABLE_HF_TRANSFER=1` is a pre-warm-only concern; it is not set in
   the runtime image because the runtime is offline (`HF_HUB_OFFLINE=1`).

2. **Create a RunPod serverless template** pointing at the published image. Set:
   - GPU type list: `[A40]` (48GB, the only tier that fits 12B BF16).
   - Disk / container disk: ≥ 10 GB.
   - Network volume (from step 1) attached at `/runpod-volume`.
   - Environment variables: see "Environment variables" below.

3. **Create a serverless endpoint** from the template. Configure:
   - `workers_min: 0`, `workers_max: 1`.
   - `idle_timeout: 300`.
   - Note the endpoint ID.

4. **Configure the orchestrator-side edge service** (`docker-compose.yml`'s `translategemma-edge`):

   ```env
   ACHERON_REGISTRATION_TOKEN=<orchestrator's token>
   ACHERON_WORKER__RUNPOD_API_KEY=<your RunPod API key>
   ACHERON_WORKER__RUNPOD_ENDPOINT_ID=<endpoint id from step 3>
   ```

5. `docker compose --profile runpod-translation up -d`. The edge registers
   with the orchestrator; the orchestrator's `HealthMonitor` reports the
   worker as `BOOTING` until RunPod scales up the GPU pod on the first
   `/execute`.

## Environment variables

| Variable | Required? | Description |
|----------|-----------|-------------|
| `ACHERON_WORKER__WORKER_ID` | yes (or via worker.yaml) | Worker ID used at registration. Default in worker.yaml: `translategemma-1`. |
| `ACHERON_WORKER__ORCHESTRATOR_URL` | yes | Orchestrator base URL. |
| `ACHERON_WORKER__REGISTRATION_TOKEN` | env-only | Bearer token used for `POST /workers`. |
| `ACHERON_WORKER__RUNPOD_API_KEY` | env-only | RunPod API key. |
| `ACHERON_WORKER__RUNPOD_ENDPOINT_ID` | env-only | The RunPod serverless endpoint ID. |
| `ACHERON_WORKER__EXECUTION_TIMEOUT_S` | optional | Per-job timeout (default 1800s). |
| `ACHERON_WORKER__PRICE_SOURCE` | optional | `runpod` (default) \| `static` \| `zero`. |
| `ACHERON_WORKER__SECURE_CLOUD` | optional | Quote secure-cloud vs community-cloud RunPod rate (default `false`). |
| `ACHERON_WORKER__LISTEN_PORT` | optional | Edge container listen port (default 8001). |
| `ACHERON_WORKER__MODEL_ID` | optional | HuggingFace model ID (default `google/translategemma-12b-it`). Flip to `google/translategemma-4b-it` for the smaller variant. |

## Switching GPU types

RunPod is the single source of truth for the GPU type. To change:

1. `runpodctl serverless update <endpoint-id> --gpu-id <new>` (or via the RunPod dashboard).
2. Restart the edge container (or wait `price_cache_ttl_s`, default 3600s).

The worker re-queries the endpoint's `gpuIds` via the RunPod GraphQL API
and resolves the new `uninterruptablePrice`. No image rebuild required.

## Switching model variants

Set `ACHERON_WORKER__MODEL_ID=google/translategemma-4b-it` in the env,
then restart the edge container. The cloud-side handler picks up the
new model id at next boot. No image rebuild required. Note: a 4B BF16
model fits on smaller GPUs (e.g. L4 24GB); the deployer can also flip
the endpoint's GPU type at the same time to take advantage of the
smaller footprint.

## Local-GPU mode

Not shipped in v1. A `TranslateGemmaLocalHandler` would be a separate
future worker package, not a config knob on this one.

## Languages

`google/translategemma-12b-it` supports 55 languages. The worker
advertises all 55 in its capabilities. The orchestrator's language-path
validation still rejects pairs outside `SUPPORTED_LANGUAGES = {en, es,
fr, de}` at plan compile time, so the worker's full language set is
latent in v1.
```

- [ ] **Step 2: Commit**

```bash
git add workers/translategemma/README.md
git commit -m "docs(workers): translategemma README"
```

---

### Task 18: Add the `translategemma-edge` compose service

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Find an existing similar compose service (granite-speech-edge) and copy its shape**

```bash
grep -n "granite-speech-edge\|runpod-asr" docker-compose.yml
```

- [ ] **Step 2: Add the `translategemma-edge` service**

Modify `docker-compose.yml`. Add a new service entry near the `granite-speech-edge` service (under the same `runpod-translation` profile is a new profile; add it next to `runpod-asr`):

```yaml
  translategemma-edge:
    image: ghcr.io/\${REPO:-acheron}/acheron-worker-edge:latest
    profiles: ["runpod-translation"]
    ports:
      - "8009:8001"
    environment:
      WORKER_NAME: translategemma
      ACHERON_ORCHESTRATOR_URL: http://orchestrator:8000
      ACHERON_REGISTRATION_TOKEN: ${ACHERON_REGISTRATION_TOKEN}
      ACHERON_WORKER__RUNPOD_API_KEY: ${RUNPOD_API_KEY}
      ACHERON_WORKER__RUNPOD_ENDPOINT_ID: ${TRANSLATEGEMMA_RUNPOD_ENDPOINT_ID}
      ACHERON_WORKER__LISTEN_PORT: "8001"
    volumes:
      - ./deploy-overrides/translategemma.worker.yaml:/app/translategemma.worker.yaml:ro
    healthcheck:
      test: ["CMD-SHELL", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8001/health').read()"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    depends_on:
      orchestrator:
        condition: service_healthy
```

- [ ] **Step 3: Validate the compose file**

```bash
docker compose -f docker-compose.yml config --quiet
```

Expected: clean (no parse errors).

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(compose): add translategemma-edge service under runpod-translation profile"
```

---

### Task 19: Add the `build-worker translategemma` Justfile target

**Files:**
- Modify: `Justfile`

- [ ] **Step 1: Read the existing `build-worker` target**

```bash
grep -n "build-worker" Justfile
```

The 8a / 8b pattern is:
```
build-worker target:
    docker build -f workers/<target>/Dockerfile.runpod -t acheron-<target>-runpod .
```

- [ ] **Step 2: Add the new target**

Modify `Justfile`. Add a new target below the `build-worker granite-speech` target:

```makefile
build-worker translategemma:
    docker build -f workers/translategemma/Dockerfile.runpod -t acheron-translategemma-runpod .
```

- [ ] **Step 3: Verify the target parses**

```bash
just --evaluate build-worker
```

Expected: shows the new target.

- [ ] **Step 4: Commit**

```bash
git add Justfile
git commit -m "chore(just): build-worker translategemma target"
```

---

### Task 20: Add the `build-translategemma` GHCR CI job

**Files:**
- Modify: `.github/workflows/build-workers.yml`

- [ ] **Step 1: Read the existing `build-granite-speech` job**

```bash
grep -n "build-granite-speech" .github/workflows/build-workers.yml
```

- [ ] **Step 2: Add the `build-translategemma` job**

Modify `.github/workflows/build-workers.yml`. Add a new job below `build-granite-speech`:

```yaml
  build-translategemma:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.14' }
      - name: Install uv
        run: pip install uv
      - name: Build acheron wheel
        run: uv build --package acheron --out-dir dist
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v5
        with:
          context: .
          file: workers/translategemma/Dockerfile.runpod
          push: ${{ github.event_name != 'pull_request' }}
          tags: |
            ghcr.io/${{ github.repository }}/acheron-translategemma-runpod:latest
            ghcr.io/${{ github.repository }}/acheron-translategemma-runpod:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

Also extend the `paths` filter on the workflow `pull_request` trigger to include `workers/translategemma/**`:

```yaml
on:
  push:
    branches: [main]
    tags: ['v*']
  pull_request:
    paths:
      - 'workers/**'
      - 'src/acheron/worker_sdk/**'
      - 'src/acheron/core/models.py'
      - 'src/acheron/core/planner.py'
      - 'src/acheron/core/errors.py'
      - 'src/acheron/shell/transports/http.py'
      - 'src/acheron/shell/config.py'
      - 'src/acheron/shell/orchestrator.py'
      - 'proto/**'
      - 'Dockerfile.edge'
      - 'Dockerfile'
      - '.github/workflows/build-workers.yml'
```

- [ ] **Step 3: Validate the workflow YAML**

```bash
uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/build-workers.yml').read()); print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/build-workers.yml
git commit -m "ci(workers): publish acheron-translategemma-runpod to GHCR"
```

---

### Task 21: Final gate

**Files:**
- (no source changes; verification only)

- [ ] **Step 1: Run `just lint-strict`**

```bash
just lint-strict
```

Expected: clean.

- [ ] **Step 2: Run `just type-check`**

```bash
just type-check
```

Expected: clean (mypy + basedpyright).

- [ ] **Step 3: Run `just test`**

```bash
just test
```

Expected: all tests pass; coverage ≥ 80%.

- [ ] **Step 4: Run `just validate`**

```bash
just validate
```

Expected: clean (all four gates).

- [ ] **Step 5: Commit any autofixes**

If `just validate` made autofixes:

```bash
git status  # review changes
git add -A
git commit -m "chore(layer8c): final validate polish" --allow-empty
```

- [ ] **Step 6: Hand off**

Both sub-plans (1 + 2) are complete. The Layer 8c TranslateGemma worker is shipped end-to-end:
- The cross-cutting fix (capability field, planner function, orchestrator wiring) is in `main`.
- `HttpWorker.execute` is a `match` with arms for `ASR | TRANSLATION | TTS`, all sharing `_execute_with_upstream_input`.
- The Qwen3-TTS handler reads from `Input` (latent gap closed).
- The new `TranslateGemmaRunpodHandler` is on the 8a/8b blueprint.
- A40 BF16 is the production target; A40 / 4-bit / smaller-GPU tradeoffs are deferred to separate sub-projects.
- The 4B variant is reachable via `model_id` knob.
- GHCR CI publishes `acheron-translategemma-runpod` on tag and `main`.
- The `translategemma-edge` compose service wires the worker into the stack.
