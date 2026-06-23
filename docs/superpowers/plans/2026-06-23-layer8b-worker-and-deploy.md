# Layer 8b Sub-plan 3 — Worker + Deploy

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `workers/granite_speech/` ASR worker against `ibm-granite/granite-speech-4.1-2b`, the `workers/_shared.py` chapter_id helper (8a one-line refactor), the GHCR publish workflow, and the `docker-compose.yml` `granite-speech-edge` service under the `runpod-asr` profile.

**Architecture:** `workers/granite_speech/handler.py` is a `WorkerHandler` subclass that loads the `transformers` `AutoModelForSpeechSeq2Seq` model at boot, consumes the `Input` (audio bytes) on `handle()`, runs batched inference, and returns one `text/plain` `BytesArtifact` per chapter. The Dockerfile installs the cu121 + transformers + flash-attn + soundfile + ffmpeg stack and pre-warms the HF cache on a RunPod network volume. The edge container reuses the existing generic `acheron-worker-edge` image (published by 8a CI) with a new `worker.edge.yaml` + a new `granite-speech-edge` compose service under the `runpod-asr` profile. The `build-workers.yml` workflow gains a `build-granite-speech` job that publishes `acheron-granite-speech-runpod:latest` and `:<sha>` on tag + main. The shared `safe_chapter_id` helper is extracted from the 8a inline function so both `qwen3tts/handler.py` and `granite_speech/handler.py` use the same defensive check.

**Tech Stack:** Python 3.14, transformers ≥ 4.52.1, torch 2.5.1 (cu121), flash-attn, soundfile, ffmpeg, the `runpod` Python SDK, pytest + pytest-asyncio, mypy + basedpyright, ruff, import-linter, GitHub Actions (`docker/build-push-action` + `docker/setup-buildx-action`), Docker.

**Reference spec:** `docs/superpowers/specs/2026-06-23-layer8b-asr-worker-design.md` (this sub-plan covers the "The Granite-Speech RunPod Worker", "Deployment Flow", "GHCR CI Workflow", and "`workers/_shared.py`" sections, plus the related entries in the "File Map").

**Final gate:** `just validate` green (lint-strict, lint-imports, mypy, basedpyright, pytest — all clean, coverage ≥ 80%). The `docker build -f workers/granite_speech/Dockerfile.runpod .` command runs locally (not gated by `just`, but exercised by `just build-worker granite-speech`).

**Depends on:** Sub-plan 1 (SDK Foundation) — `Input`, `BytesInput`, `WorkerHandler.handle(self, job, input=None)` are all in place.

---

## Spec Adjustments (refinements from writing the plan)

These deltas are not new design decisions; they are clarifications of decisions already made in the parent spec, surfaced by writing the implementation steps. The parent spec remains the single source of truth for the design.

1. **`workers/_shared.py` exports `safe_chapter_id` + `MAX_CHAPTER_ID_LEN`**. The 8a `Qwen3TTSRunpodHandler._chunk_chapter_id` is refactored to delegate to this helper (one-line change: replace the body, keep the function signature and docstring). Both 8a and 8b workers import it. The helper's tests cover both call sites.
2. **`pyproject.toml` workspace members gain `workers/granite_speech`**; the per-path ruff overrides mirror the existing `workers/qwen3tts/tests/**` block (relaxed `D` / `S` / `ANN` / etc. for the test directory; `PLC0415` for the worker source to allow lazy `torch` / `transformers` imports). The pytest `testpaths` also gains `workers/granite_speech/tests`.
3. **`Justfile` `type-check` target gains `workers/granite_speech/`** alongside the existing `workers/qwen3tts/`. The `just build-worker <name>` target is generic and works for `granite-speech` unchanged (8a already templated the recipe on `{{name}}`).
4. **Compose `granite-speech-edge` service uses host port `8008`** (next free in the existing matrix `8001`-`8007`; the qwen3tts edge uses host `8004`) and **internal container port `8001` (matches `qwen3tts-edge`)**. Each container has its own network namespace, so the internal port doesn't need to be unique across services; the host port is what the operator's environment sees.
5. **`granite-speech-edge` is under the `runpod-asr` profile** (parallel to `runpod-tts` for the qwen3tts edge). The operator enables the profile via `docker compose --profile runpod-asr up -d`.

---

## Adversarial Review Rubric

After this sub-plan is implemented (or before — at the user's option), dispatch a fresh-context reviewer subagent with this rubric:

### Correctness
- [ ] `GraniteSpeechRunpodHandler.handle` correctly handles all 5 error paths: (a) no `input` argument → `WorkerError("Granite-Speech requires an audio input")`, (b) empty audio bytes → `WorkerError("Empty audio input")`, (c) unsupported `source_language` → `WorkerError("Unsupported source language: ...")` with the value, (d) `_model is None` (startup not run) → `WorkerError("Granite-Speech model not loaded (startup() not run)")`, (e) path-traversal `chapter_id` → `WorkerError` via `safe_chapter_id`.
- [ ] `capabilities()` advertises the right shape: `worker_type=WorkerType.ASR`, 6 ASR languages (`en, fr, de, es, pt, ja`), `supported_formats_in={mp3, wav}`, `supported_formats_out={text}`, `batch_capable=False`, `model_source="huggingface:ibm-granite/granite-speech-4.1-2b"`, `metadata["asr_prompt"]` matches the default, `metadata["health_provider"]="runpod"`.
- [ ] Dockerfile `COPY` chain puts files in the right paths: `workers/granite_speech/handler.py → /app/handler.py`, `workers/granite_speech/runpod_entrypoint.py → /app/runpod_entrypoint.py`, `workers/granite_speech/worker.yaml → /app/worker.yaml`, `workers/__init__.py → /app/workers/__init__.py`, `workers/granite_speech/__init__.py → /app/workers/granite_speech/__init__.py`, `workers/_shared.py → /app/workers/_shared.py`.
- [ ] The CI job (`build-granite-speech`) mirrors `build-qwen3tts` exactly: same `actions/checkout@v4`, same `actions/setup-python@v5` with Python 3.14, same `uv build --package acheron --out-dir dist`, same `docker/setup-buildx-action@v3`, same `docker/login-action@v3` with `registry: ghcr.io`, same `docker/build-push-action@v5` with `cache-from: type=gha` and `cache-to: type=gha,mode=max`. Tags are `ghcr.io/${{ github.repository }}/acheron-granite-speech-runpod:latest` and `:<sha>`.
- [ ] The edge compose service correctly resolves `WORKER_NAME: granite_speech` to the `granite_speech.worker.yaml` mounted at `/app/granite_speech.worker.yaml` (per the SDK's WORKER_CONFIG discovery order from 8a).
- [ ] The 8a `Qwen3TTSRunpodHandler._chunk_chapter_id` still works after the refactor to delegate to `safe_chapter_id` (existing qwen3tts tests must pass unchanged).
- [ ] `safe_chapter_id` correctly rejects: blank string, NUL byte, newline, tab, `/`, `\`, `.`, `..`, double-dot component (`ch1/..`), oversize (> 128 chars). All 11+ edge cases in the test pass.

### Code quality
- [ ] The mocked-model test pattern in `test_handler.py` uses `monkeypatch.setattr(h, "_transcribe", _fake_transcribe)` to bypass the torch + transformers import (consistent with the qwen3tts pattern of mocking `Qwen3TTSModel`).
- [ ] The `_FakeModel` is minimal — no over-mocking, no real-tensor shims.
- [ ] The handler source code uses lazy imports (`import torch`, `from transformers import ...`) inside `startup` and `_transcribe` so the workspace dev install doesn't require torch or transformers.
- [ ] File structure is balanced: `handler.py` is the model code; `runpod_entrypoint.py` is the boot script; configs are in `worker.yaml` / `worker.edge.yaml`; tests are in `tests/`. No file does too much.
- [ ] The Dockerfile is minimal: only the OS deps + pip packages needed for the model to load and run.
- [ ] `pytest.skip` or `pytest.importorskip` is **not** used in the worker tests; the tests are designed to run without torch/transformers installed in the workspace.

### Spec compliance
- [ ] Every file in the spec's "File Map" Workers section is created or modified (cross-check `workers/_shared.py`, `workers/_shared/tests/test_safe_chapter_id.py`, `workers/qwen3tts/handler.py`, `workers/granite_speech/__init__.py`, `pyproject.toml`, `handler.py`, `runpod_entrypoint.py`, `worker.yaml`, `worker.edge.yaml`, `Dockerfile.runpod`, `README.md`, `tests/__init__.py`, `test_capabilities.py`, `test_handler.py`, `test_runpod_entrypoint.py`).
- [ ] The `granite-speech-edge` compose service matches the spec's "Run the edge container" section exactly (host 8008, internal 8001, `runpod-asr` profile, `WORKER_NAME: granite_speech`, `ACHERON_WORKER__RUNPOD_API_KEY`, `ACHERON_WORKER__RUNPOD_ENDPOINT_ID`, `ACHERON_REGISTRATION_TOKEN`).
- [ ] The `runpod_entrypoint.py` matches the spec's `runpod_entrypoint.py` (model load at boot, `runpod.serverless.start({"handler": make_runpod_handler(handler)})`).
- [ ] The `worker.yaml` (image default) and `worker.edge.yaml` (edge config) match the spec's templates exactly.
- [ ] The `README.md` documents: image tags, RunPod setup steps, env var table, GPU switching instructions, language list, out-of-scope variants (NAR excluded, plus deferred).

---

## File Structure

| File | Responsibility |
|------|---------------|
| `workers/_shared.py` (NEW) | `safe_chapter_id` + `MAX_CHAPTER_ID_LEN` (shared by 8a + 8b workers). |
| `workers/qwen3tts/handler.py` (EXTENDED) | `_chunk_chapter_id` delegates to `safe_chapter_id` (one-line refactor). |
| `workers/granite_speech/__init__.py` (NEW) | Public re-exports. |
| `workers/granite_speech/pyproject.toml` (NEW) | Workspace member metadata. |
| `workers/granite_speech/handler.py` (NEW) | `GraniteSpeechRunpodHandler` — capabilities, startup, shutdown, handle, _transcribe. |
| `workers/granite_speech/runpod_entrypoint.py` (NEW) | Boot: load settings → load model → `runpod.serverless.start(...)`. |
| `workers/granite_speech/worker.yaml` (NEW) | Image default config (RunPod runtime). |
| `workers/granite_speech/worker.edge.yaml` (NEW) | Edge-side config (RunPodForwarderHandler + phantom). |
| `workers/granite_speech/Dockerfile.runpod` (NEW) | Cloud-side runtime image. |
| `workers/granite_speech/README.md` (NEW) | Operator setup guide. |
| `workers/granite_speech/tests/__init__.py` (NEW) | Empty package init. |
| `workers/granite_speech/tests/test_capabilities.py` (NEW) | `capabilities()` shape. |
| `workers/granite_speech/tests/test_handler.py` (NEW) | `handle()` mocked model + 5 error paths. |
| `workers/granite_speech/tests/test_runpod_entrypoint.py` (NEW) | `runpod_entrypoint.main()` boot path. |
| `workers/_shared/tests/__init__.py` (NEW) | Empty package init. |
| `workers/_shared/tests/test_safe_chapter_id.py` (NEW) | 11+ edge cases. |
| `pyproject.toml` (EXTENDED) | Workspace members + per-path ruff overrides + pytest testpaths. |
| `Justfile` (EXTENDED) | `type-check` target adds `workers/granite_speech/`. |
| `.github/workflows/build-workers.yml` (EXTENDED) | `build-granite-speech` job. |
| `docker-compose.yml` (EXTENDED) | `granite-speech-edge` service under `runpod-asr` profile. |

---

### Task 14: Create `workers/_shared.py` with `safe_chapter_id`

**Files:**
- Create: `workers/_shared.py`
- Create: `workers/_shared/tests/__init__.py`
- Create: `workers/_shared/tests/test_safe_chapter_id.py`

- [ ] **Step 1: Write the test (TDD)**

Create `workers/_shared/tests/__init__.py`:

```python
"""Tests for workers._shared."""
```

Create `workers/_shared/tests/test_safe_chapter_id.py`:

```python
"""Tests for workers._shared.safe_chapter_id (8b)."""

from __future__ import annotations

import pytest

from acheron.core.errors import WorkerError
from workers._shared import safe_chapter_id


class TestSafeChapterId:
    def test_plain_chapter_id_passes(self) -> None:
        assert safe_chapter_id("ch1") == "ch1"
        assert safe_chapter_id("chapter_001") == "chapter_001"

    def test_blank_raises(self) -> None:
        with pytest.raises(WorkerError):
            safe_chapter_id("")

    def test_nul_byte_raises(self) -> None:
        with pytest.raises(WorkerError):
            safe_chapter_id("ch\x001")

    def test_newline_raises(self) -> None:
        with pytest.raises(WorkerError):
            safe_chapter_id("ch1\n")

    def test_tab_raises(self) -> None:
        with pytest.raises(WorkerError):
            safe_chapter_id("ch1\t")

    @pytest.mark.parametrize("sep", ["/", "\\"])
    def test_path_separator_raises(self, sep: str) -> None:
        with pytest.raises(WorkerError):
            safe_chapter_id(f"ch{sep}1")

    def test_dot_raises(self) -> None:
        with pytest.raises(WorkerError):
            safe_chapter_id(".")

    def test_double_dot_raises(self) -> None:
        with pytest.raises(WorkerError):
            safe_chapter_id("..")

    def test_double_dot_component_raises(self) -> None:
        with pytest.raises(WorkerError):
            safe_chapter_id("ch1/..")

    def test_too_long_raises(self) -> None:
        with pytest.raises(WorkerError):
            safe_chapter_id("a" * 200)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest workers/_shared/tests/test_safe_chapter_id.py -v
```

Expected: `ModuleNotFoundError: No module named 'workers._shared'`.

- [ ] **Step 3: Create `workers/_shared.py`**

```python
"""Shared utilities for all worker handlers (8a TTS, 8b ASR, future 8c)."""

from __future__ import annotations

from acheron.core.errors import WorkerError

MAX_CHAPTER_ID_LEN = 128


def safe_chapter_id(cid: str) -> str:
    """Sanitise a chapter_id for use as a filename component.

    Rejects blank, NUL-byte, newline, tab, path-separator (``/`` / ``\\``),
    absolute-path, and ``..``-component values. The orchestrator's
    ``_safe_join`` defends the orchestrator boundary; this is
    defense-in-depth so the worker also fails fast on malicious input.
    """
    if not cid or "\x00" in cid or "\n" in cid or "\r" in cid or "\t" in cid:
        msg = f"chapter_id contains illegal whitespace/NUL: {cid!r}"
        raise WorkerError(msg)
    if len(cid) > MAX_CHAPTER_ID_LEN:
        msg = f"chapter_id too long ({len(cid)} > {MAX_CHAPTER_ID_LEN}): {cid!r}"
        raise WorkerError(msg)
    if "/" in cid or "\\" in cid or cid in {".", ".."} or ".." in cid.split("/") or ".." in cid.split("\\"):
        msg = f"chapter_id contains a path component: {cid!r}"
        raise WorkerError(msg)
    return cid
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest workers/_shared/tests/test_safe_chapter_id.py -v
```

Expected: PASS (all 11 cases).

- [ ] **Step 5: Lint + type-check + commit**

```bash
uv run ruff check workers/_shared.py workers/_shared/tests/test_safe_chapter_id.py
uv run mypy workers/_shared.py workers/_shared/tests/test_safe_chapter_id.py
git add workers/_shared.py workers/_shared/tests/
git commit -m "feat(workers): add shared safe_chapter_id helper"
```

---

### Task 15: Refactor `Qwen3TTSRunpodHandler._chunk_chapter_id` to use `safe_chapter_id`

**Files:**
- Modify: `workers/qwen3tts/handler.py:55-88`

- [ ] **Step 1: Read the current `_chunk_chapter_id`**

```bash
uv run sed -n '55,88p' workers/qwen3tts/handler.py
```

- [ ] **Step 2: Replace the function body with a delegation**

In `workers/qwen3tts/handler.py`, replace the body of `_chunk_chapter_id` (keep the function signature and docstring):

```python
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
```

Add the import at the top:

```python
from workers._shared import safe_chapter_id
```

- [ ] **Step 3: Run the qwen3tts test suite**

```bash
uv run pytest workers/qwen3tts/tests/ -v
```

Expected: PASS (all existing tests continue to work; the delegation is
behavior-preserving).

- [ ] **Step 4: Lint + type-check + commit**

```bash
uv run ruff check workers/qwen3tts/handler.py
uv run mypy workers/qwen3tts/handler.py
git add workers/qwen3tts/handler.py
git commit -m "refactor(workers): Qwen3TTSRunpodHandler._chunk_chapter_id delegates to safe_chapter_id"
```

---

### Task 16: Create `workers/granite_speech/` package skeleton

**Files:**
- Create: `workers/granite_speech/__init__.py`
- Create: `workers/granite_speech/pyproject.toml`
- Create: `workers/granite_speech/tests/__init__.py`

- [ ] **Step 1: Create the package directories**

```bash
mkdir -p workers/granite_speech/tests
```

- [ ] **Step 2: Create `workers/granite_speech/__init__.py`**

```python
"""RunPod Serverless worker package for ibm-granite/granite-speech-4.1-2b."""

__all__ = ["GraniteSpeechRunpodHandler"]
```

- [ ] **Step 3: Create `workers/granite_speech/pyproject.toml`**

```toml
[project]
name = "acheron-granite-speech"
version = "0.1.0"
description = "RunPod Serverless worker for ibm-granite/granite-speech-4.1-2b"
requires-python = ">=3.12"
license = "GPL-3.0-only"
dependencies = [
    "acheron",
    # transformers + torch + flash-attn are installed by Dockerfile.runpod
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

- [ ] **Step 4: Create `workers/granite_speech/tests/__init__.py`**

```python
"""Tests for the granite-speech RunPod worker."""
```

- [ ] **Step 5: Commit**

```bash
git add workers/granite_speech/
git commit -m "feat(workers): scaffold granite_speech uv-workspace member"
```

---

### Task 17: Create `GraniteSpeechRunpodHandler` capabilities test

**Files:**
- Create: `workers/granite_speech/tests/test_capabilities.py`

- [ ] **Step 1: Write the test**

```python
"""Tests for GraniteSpeechRunpodHandler.capabilities."""

from __future__ import annotations

from typing import Any

import pytest

from acheron.core.models import WorkerType
from acheron.worker_sdk.settings import WorkerSettings


@pytest.fixture
def handler() -> Any:
    """Construct a handler without loading the model."""
    from workers.granite_speech.handler import GraniteSpeechRunpodHandler

    return GraniteSpeechRunpodHandler(
        WorkerSettings(
            worker_id="granite-speech-test",
            orchestrator_url="http://o:8000",
            listen_port=8001,
            price_source="zero",
        )
    )


def test_capabilities_worker_type_is_asr(handler: Any) -> None:
    caps = handler.capabilities()
    assert caps.worker_type == WorkerType.ASR


def test_capabilities_supported_languages(handler: Any) -> None:
    caps = handler.capabilities()
    assert caps.supported_languages_in == frozenset({"en", "fr", "de", "es", "pt", "ja"})
    assert caps.supported_languages_out == caps.supported_languages_in


def test_capabilities_supported_formats(handler: Any) -> None:
    caps = handler.capabilities()
    assert caps.supported_formats_in == frozenset({"mp3", "wav"})
    assert caps.supported_formats_out == frozenset({"text"})


def test_capabilities_batch_capable_false(handler: Any) -> None:
    assert handler.capabilities().batch_capable is False


def test_capabilities_model_source(handler: Any) -> None:
    caps = handler.capabilities()
    assert caps.model_source == "huggingface:ibm-granite/granite-speech-4.1-2b"


def test_capabilities_metadata(handler: Any) -> None:
    caps = handler.capabilities()
    assert caps.metadata["asr_prompt"] == "transcribe the speech with proper punctuation and capitalization."
    assert caps.metadata["health_provider"] == "runpod"
```

- [ ] **Step 2: Run the test (expected to fail because `handler.py` doesn't exist yet)**

```bash
uv run pytest workers/granite_speech/tests/test_capabilities.py -v
```

Expected: `ModuleNotFoundError: No module named 'workers.granite_speech.handler'`.

- [ ] **Step 3: Commit the test only (red)**

```bash
git add workers/granite_speech/tests/test_capabilities.py
git commit -m "test(workers): GraniteSpeechRunpodHandler.capabilities shape"
```

---

### Task 18: Implement `GraniteSpeechRunpodHandler`

**Files:**
- Create: `workers/granite_speech/handler.py`

- [ ] **Step 1: Implement the handler**

```python
"""RunPod Serverless handler for ibm-granite/granite-speech-4.1-2b.

This module runs **inside the RunPod serverless runtime image** (see
``Dockerfile.runpod``). The cloud-side ``runpod_entrypoint.py`` imports
``GraniteSpeechRunpodHandler`` here, calls ``startup()`` eagerly at boot,
then ``runpod.serverless.start({"handler": make_runpod_handler(handler)})``.

A local-GPU fallback handler (``GraniteSpeechLocalHandler``) is deferred
to a separate future worker package — workers commit to one deployment
mode by being one mode, per the Layer 8a spec.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from acheron.core.errors import WorkerError
from acheron.core.models import Job, JsonValue, WorkerCapabilities, WorkerType
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler
from acheron.worker_sdk.inputs import Input
from workers._shared import safe_chapter_id

if TYPE_CHECKING:
    from acheron.worker_sdk.settings import WorkerSettings


_SUPPORTED_LANGS = frozenset({"en", "fr", "de", "es", "pt", "ja"})
_MODEL_ID = "ibm-granite/granite-speech-4.1-2b"
_DEFAULT_PROMPT = (
    "transcribe the speech with proper punctuation and capitalization."
)


class GraniteSpeechRunpodHandler(WorkerHandler):
    """Cloud-side handler run inside the RunPod serverless runtime image."""

    def __init__(self, settings: WorkerSettings) -> None:
        self._settings = settings
        self._model: Any = None
        self._processor: Any = None

    def capabilities(self) -> WorkerCapabilities:
        """Return the worker's static description. No I/O — sync."""
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
            model_source=f"huggingface:{_MODEL_ID}",
            metadata=metadata,
        )

    async def startup(self) -> None:
        """Eagerly load the model + processor at container boot."""
        import torch  # noqa: PLC0415 - keep torch import out of test contexts

        def _load() -> None:
            from transformers import (  # noqa: PLC0415 - lazy, not always installed
                AutoModelForSpeechSeq2Seq,
                AutoProcessor,
            )

            self._processor = AutoProcessor.from_pretrained(_MODEL_ID)
            self._model = AutoModelForSpeechSeq2Seq.from_pretrained(
                _MODEL_ID,
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
        import torch  # noqa: PLC0415 - keep torch import out of test contexts

        torch.cuda.empty_cache()

    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:
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
        return [
            BytesArtifact(
                filename=f"{chapter_id}.txt",
                content_type="text/plain",
                data=transcript.encode("utf-8"),
                metadata={
                    "chapter_id": chapter_id,
                    "model": _MODEL_ID,
                    "language": source_lang,
                },
            )
        ]

    def _transcribe(self, audio_bytes: bytes) -> str:
        """Run transformers inference; returns the transcript string."""
        import torch  # noqa: PLC0415

        chat = [{"role": "user", "content": f"<|audio|>{_DEFAULT_PROMPT}"}]
        prompt_text = self._processor.tokenizer.apply_chat_template(
            chat, tokenize=False, add_generation_prompt=True
        )
        model_inputs = self._processor(
            prompt_text,
            audio_bytes,
            device="cuda:0",
            return_tensors="pt",
        ).to("cuda:0")
        with torch.inference_mode():
            model_outputs = self._model.generate(
                **model_inputs, max_new_tokens=4096, do_sample=False, num_beams=1
            )
        num_input_tokens = model_inputs["input_ids"].shape[-1]
        new_tokens = model_outputs[0, num_input_tokens:].unsqueeze(0)
        text = self._processor.tokenizer.batch_decode(
            new_tokens, add_special_tokens=False, skip_special_tokens=True
        )
        return text[0].strip()
```

- [ ] **Step 2: Run the capabilities test**

```bash
uv run pytest workers/granite_speech/tests/test_capabilities.py -v
```

Expected: PASS (all 6 tests).

- [ ] **Step 3: Lint + type-check**

```bash
uv run ruff check workers/granite_speech/handler.py
uv run mypy workers/granite_speech/handler.py
uv run basedpyright workers/granite_speech/handler.py
```

Expected: all clean.

- [ ] **Step 4: Commit**

```bash
git add workers/granite_speech/handler.py
git commit -m "feat(workers): GraniteSpeechRunpodHandler with capabilities + start/shutdown/handle"
```

---

### Task 19: Add `GraniteSpeechRunpodHandler.handle` tests (mocked model + error paths)

**Files:**
- Create: `workers/granite_speech/tests/test_handler.py`

- [ ] **Step 1: Write the test**

```python
"""Tests for GraniteSpeechRunpodHandler.handle (mocked model).

We monkey-patch ``_transcribe`` to return a canned string. This
exercises the handler's validation (input presence, language check,
chapter_id safety, empty audio) and BytesArtifact construction
without importing torch or transformers.
"""

from __future__ import annotations

from typing import Any

import pytest

from acheron.core.errors import WorkerError
from acheron.core.models import Job, WorkerType
from acheron.worker_sdk.inputs import BytesInput
from acheron.worker_sdk.settings import WorkerSettings


def _handler() -> Any:
    from workers.granite_speech.handler import GraniteSpeechRunpodHandler

    return GraniteSpeechRunpodHandler(
        WorkerSettings(
            worker_id="granite-speech-test",
            orchestrator_url="http://o:8000",
            listen_port=8001,
            price_source="zero",
        )
    )


def _make_job(source_language: str = "en", chapter_id: str = "ch1") -> Job:
    return Job(
        job_id="j-1-transcribe",
        job_type=WorkerType.ASR,
        payload={"source_language": source_language},
        chapter_id=chapter_id,
    )


async def _fake_transcribe(_audio_bytes: bytes) -> str:
    return "transcribed text"


class TestHandle:
    async def test_handle_with_bytes_input_produces_text_artifact(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _handler()
        h._model = object()  # mark loaded
        h._processor = object()
        monkeypatch.setattr(h, "_transcribe", _fake_transcribe)
        job = _make_job()
        inp = BytesInput(content_type="audio/mpeg", data=b"\xff\xfb\x90\x00mock-audio")
        artifacts = await h.handle(job, inp)
        assert len(artifacts) == 1
        a = artifacts[0]
        assert a.content_type == "text/plain"
        assert a.filename == "ch1.txt"
        assert a.data == b"transcribed text"
        assert a.metadata["chapter_id"] == "ch1"
        assert a.metadata["model"] == "ibm-granite/granite-speech-4.1-2b"
        assert a.metadata["language"] == "en"

    async def test_handle_without_input_raises(self) -> None:
        h = _handler()
        h._model = object()
        h._processor = object()
        job = _make_job()
        with pytest.raises(WorkerError, match="requires an audio input"):
            await h.handle(job, None)

    async def test_handle_with_empty_audio_raises(self) -> None:
        h = _handler()
        h._model = object()
        h._processor = object()
        job = _make_job()
        inp = BytesInput(content_type="audio/wav", data=b"")
        with pytest.raises(WorkerError, match="Empty audio input"):
            await h.handle(job, inp)

    async def test_handle_with_unsupported_language_raises(self) -> None:
        h = _handler()
        h._model = object()
        h._processor = object()
        job = _make_job(source_language="zh")  # not in _SUPPORTED_LANGS
        inp = BytesInput(content_type="audio/wav", data=b"x")
        with pytest.raises(WorkerError, match="Unsupported source language"):
            await h.handle(job, inp)

    async def test_handle_without_model_loaded_raises(self) -> None:
        h = _handler()
        h._model = None
        h._processor = None
        job = _make_job()
        inp = BytesInput(content_type="audio/wav", data=b"x")
        with pytest.raises(WorkerError, match="model not loaded"):
            await h.handle(job, inp)

    async def test_handle_with_path_traversal_chapter_id_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _handler()
        h._model = object()
        h._processor = object()
        monkeypatch.setattr(h, "_transcribe", _fake_transcribe)
        job = _make_job(chapter_id="../../../etc/passwd")
        inp = BytesInput(content_type="audio/wav", data=b"x")
        with pytest.raises(WorkerError, match="path component"):
            await h.handle(job, inp)
```

- [ ] **Step 2: Run the test**

```bash
uv run pytest workers/granite_speech/tests/test_handler.py -v
```

Expected: PASS (all 6 tests).

- [ ] **Step 3: Lint + type-check + commit**

```bash
uv run ruff check workers/granite_speech/tests/test_handler.py
uv run mypy workers/granite_speech/tests/test_handler.py
git add workers/granite_speech/tests/test_handler.py
git commit -m "test(workers): GraniteSpeechRunpodHandler.handle mocked model + error paths"
```

---

### Task 20: Create `runpod_entrypoint.py` + test

**Files:**
- Create: `workers/granite_speech/runpod_entrypoint.py`
- Create: `workers/granite_speech/tests/test_runpod_entrypoint.py`

- [ ] **Step 1: Implement the entrypoint**

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
from workers.granite_speech.handler import GraniteSpeechRunpodHandler

logging.basicConfig(level=logging.INFO)
logging.getLogger("transformers").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def main() -> None:
    """Boot the RunPod serverless worker: load model, then serve."""
    settings = load_settings()
    handler = GraniteSpeechRunpodHandler(settings)
    asyncio.run(handler.startup())
    runpod.serverless.start({"handler": make_runpod_handler(handler)})


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write the entrypoint test**

```python
"""Tests for runpod_entrypoint.main."""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

from acheron.worker_sdk.settings import WorkerSettings


def test_main_loads_handler_and_starts_runpod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_settings = WorkerSettings(
        worker_id="g-1",
        orchestrator_url="http://o:8000",
        listen_port=8001,
        price_source="zero",
    )
    monkeypatch.setattr(
        "acheron.worker_sdk.config_loader.load_settings",
        lambda: fake_settings,
    )

    fake_handler = MagicMock()
    fake_handler_class = MagicMock(return_value=fake_handler)
    monkeypatch.setattr(
        "workers.granite_speech.runpod_entrypoint.GraniteSpeechRunpodHandler",
        fake_handler_class,
    )

    fake_runpod_module = MagicMock()
    monkeypatch.setitem(sys.modules, "runpod", fake_runpod_module)

    from workers.granite_speech import runpod_entrypoint

    runpod_entrypoint.main()

    fake_handler.startup.assert_awaited_once()
    fake_runpod_module.serverless.start.assert_called_once()
    call_arg = fake_runpod_module.serverless.start.call_args[0][0]
    assert "handler" in call_arg
    assert callable(call_arg["handler"])
```

- [ ] **Step 3: Run the test**

```bash
uv run pytest workers/granite_speech/tests/test_runpod_entrypoint.py -v
```

Expected: PASS.

- [ ] **Step 4: Lint + type-check + commit**

```bash
uv run ruff check workers/granite_speech/runpod_entrypoint.py workers/granite_speech/tests/test_runpod_entrypoint.py
git add workers/granite_speech/runpod_entrypoint.py workers/granite_speech/tests/test_runpod_entrypoint.py
git commit -m "feat(workers): runpod_entrypoint + boot test"
```

---

### Task 21: Create `worker.yaml` (image default) + `worker.edge.yaml`

**Files:**
- Create: `workers/granite_speech/worker.yaml`
- Create: `workers/granite_speech/worker.edge.yaml`

- [ ] **Step 1: Create `worker.yaml`**

```yaml
# Granite-Speech worker — image default config.
# Sensitive fields (RUNPOD_API_KEY, RUNPOD_ENDPOINT_ID, REGISTRATION_TOKEN)
# are env-only — rejected when present here. Override per-deploy by mounting
# a granite_speech.worker.yaml override or by setting ACHERON_WORKER_* env vars.

worker_id: "granite-speech-1"
orchestrator_url: "http://orchestrator:8000"
listen_port: 8001
execution_timeout_s: 1800

# Pricing — RunPod GraphQL API is the default. The GPU type is NOT a config
# field: RunPodPrice reads the endpoint's gpuIds via the RunPod GraphQL API.
# The deployer provisions a single L4 endpoint (cheapest 24GB tier); changing
# GPU on the RunPod endpoint takes effect on the next price_cache_ttl_s
# refresh; no image rebuild required.
price_source: runpod
secure_cloud: false
# price_cache_ttl_s: 3600.0

# Output transport — multipart default (output side; input is implicit
# from job_type == ASR).
output_mode: multipart

# Handler — used by the generic acheron-worker-edge CLI to import the handler
# class when running the edge container alongside the orchestrator. The
# runpod_entrypoint.py in the RunPod runtime image uses the import directly.
handler: "workers.granite_speech.handler:GraniteSpeechRunpodHandler"
model_id: "ibm-granite/granite-speech-4.1-2b"
```

- [ ] **Step 2: Create `worker.edge.yaml`**

```yaml
# Edge-side worker config for the acheron-worker-edge image.
# Identical shape to workers/qwen3tts/worker.edge.yaml — phantom_handler
# is the cloud-side GraniteSpeechRunpodHandler, which the edge imports
# to read its static capabilities() without loading the model.

worker_id: "granite-speech-edge"
orchestrator_url: "http://orchestrator:8000"
listen_port: 8001
execution_timeout_s: 1800

handler: "acheron.worker_sdk.cloud:RunPodForwarderHandler"
phantom_handler: "workers.granite_speech.handler:GraniteSpeechRunpodHandler"
model_id: "ibm-granite/granite-speech-4.1-2b"

price_source: runpod
secure_cloud: false

# Edge transport is always HTTP multipart on the output side; the input
# side is multipart (form-data) when job_type == ASR, JSON otherwise.
output_mode: multipart
```

- [ ] **Step 3: Commit**

```bash
git add workers/granite_speech/worker.yaml workers/granite_speech/worker.edge.yaml
git commit -m "feat(workers): granite_speech image-default + edge worker.yaml"
```

---

### Task 22: Create `Dockerfile.runpod`

**Files:**
- Create: `workers/granite_speech/Dockerfile.runpod`

- [ ] **Step 1: Create the Dockerfile**

```dockerfile
# RunPod Serverless runtime image for ibm-granite/granite-speech-4.1-2b.
#
# Built from the repo root with:
#   docker build -f workers/granite_speech/Dockerfile.runpod -t acheron-granite-speech-runpod .
#
# CI publishes this image to ghcr.io via .github/workflows/build-workers.yml.

FROM python:3.12-slim AS runpod-runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# OS deps for soundfile (libsndfile) + ffmpeg fallback (mp3/ogg) +
# flash-attn build (git + build-essential).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libsndfile1 \
        ffmpeg \
        git \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# PyTorch first — matches the CUDA version the host passes via --gpus.
# Pin to 2.5.1 + cu121; flash-attn must match the torch ABI.
RUN pip install --no-cache-dir torch==2.5.1 torchaudio==2.5.1 \
        --index-url https://download.pytorch.org/whl/cu121

# Install the acheron wheel (built from the monorepo) — provides acheron.worker_sdk.
COPY dist/acheron-*.whl /tmp/
RUN pip install /tmp/acheron-*.whl && rm /tmp/acheron-*.whl

# Worker deps — transformers + accelerate + soundfile + flash-attn.
# transformers >= 4.52.1 is required for native Granite-Speech support.
# hf-transfer is intentionally NOT installed: the runtime image is offline
# (HF_HUB_OFFLINE=1) and never re-downloads; hf-transfer is a pre-warm-only
# concern documented in the README.
RUN pip install --no-cache-dir \
        "transformers>=4.52.1" \
        accelerate \
        soundfile
RUN pip install --no-cache-dir flash-attn==2.5.9.post1 --no-build-isolation

# RunPod SDK so runpod.serverless.start() is importable.
RUN pip install --no-cache-dir runpod

WORKDIR /app

# The worker's deployable assets. The cloud-side handler is entrypoint-only.
COPY workers/granite_speech/handler.py /app/handler.py
COPY workers/granite_speech/runpod_entrypoint.py /app/runpod_entrypoint.py
COPY workers/granite_speech/worker.yaml /app/worker.yaml
COPY workers/__init__.py /app/workers/__init__.py
COPY workers/granite_speech/__init__.py /app/workers/granite_speech/__init__.py
COPY workers/_shared.py /app/workers/_shared.py

# HF cache lives on the RunPod network volume; offline mode forces the cached snapshot.
ENV HF_HOME=/runpod-volume/huggingface-cache \
    PYTHONPATH=/app

CMD ["python", "runpod_entrypoint.py"]
```

- [ ] **Step 2: Commit**

```bash
git add workers/granite_speech/Dockerfile.runpod
git commit -m "feat(workers): granite_speech Dockerfile.runpod"
```

---

### Task 23: Create `README.md`

**Files:**
- Create: `workers/granite_speech/README.md`

- [ ] **Step 1: Create the README**

```markdown
# acheron-granite-speech

RunPod Serverless worker package for `ibm-granite/granite-speech-4.1-2b`.

## Image

CI publishes `ghcr.io/<owner>/acheron-granite-speech-runpod:latest` and
`:<sha>` on every push to `main` and on every `v*` tag. Pin your RunPod
template to `:<sha>` for reproducibility.

## RunPod Serverless setup (one-time)

1. **Create a network volume** for the HuggingFace cache to avoid re-downloading the ~4GB weights on every cold start. Mount it at `/runpod-volume/huggingface-cache`. Pre-warm it once:

   ```bash
   pip install "huggingface_hub[cli]" hf-transfer
   HF_HUB_ENABLE_HF_TRANSFER=1 huggingface-cli download \
       ibm-granite/granite-speech-4.1-2b \
       --local-dir /runpod-volume/huggingface-cache/hub/models--ibm-granite--granite-speech-4.1-2b
   ```

   `HF_HUB_ENABLE_HF_TRANSFER=1` is a pre-warm-only concern; it is not set in
   the runtime image because the runtime is offline (`HF_HUB_OFFLINE=1`).

2. **Create a RunPod serverless template** pointing at the published image. Set:
   - GPU type list: `[L4]` (24GB, the cheapest 24GB tier per the deployer's
     compute choice; single GPU per deployment).
   - Disk / container disk: ≥ 10 GB.
   - Network volume (from step 1) attached at `/runpod-volume`.
   - Environment variables: see "Environment variables" below.

3. **Create a serverless endpoint** from the template. Configure:
   - `workers_min: 0`, `workers_max: 1`.
   - `idle_timeout: 300`.
   - Note the endpoint ID.

4. **Configure the orchestrator-side edge service** (`docker-compose.yml`'s `granite-speech-edge`):

   ```env
   ACHERON_REGISTRATION_TOKEN=<orchestrator's token>
   ACHERON_WORKER__RUNPOD_API_KEY=<your RunPod API key>
   ACHERON_WORKER__RUNPOD_ENDPOINT_ID=<endpoint id from step 3>
   ```

5. `docker compose --profile runpod-asr up -d`. The edge registers with the
   orchestrator; the orchestrator's `HealthMonitor` reports the worker as
   `BOOTING` until RunPod scales up the GPU pod on the first `/execute`.

## Environment variables

| Variable | Required? | Description |
|----------|-----------|-------------|
| `ACHERON_WORKER__WORKER_ID` | yes (or via worker.yaml) | Worker ID used at registration. Default in worker.yaml: `granite-speech-1`. |
| `ACHERON_WORKER__ORCHESTRATOR_URL` | yes | Orchestrator base URL. |
| `ACHERON_WORKER__REGISTRATION_TOKEN` | env-only | Bearer token used for `POST /workers`. |
| `ACHERON_WORKER__RUNPOD_API_KEY` | env-only | RunPod API key. |
| `ACHERON_WORKER__RUNPOD_ENDPOINT_ID` | env-only | The RunPod serverless endpoint ID. |
| `ACHERON_WORKER__EXECUTION_TIMEOUT_S` | optional | Per-job timeout (default 1800s). |
| `ACHERON_WORKER__PRICE_SOURCE` | optional | `runpod` (default) | `static` | `zero`. |
| `ACHERON_WORKER__SECURE_CLOUD` | optional | Quote secure-cloud vs community-cloud RunPod rate (default `false`). |
| `ACHERON_WORKER__LISTEN_PORT` | optional | Edge container listen port (default 8001). |

## Switching GPU types

RunPod is the single source of truth for the GPU type. To change:

1. `runpodctl serverless update <endpoint-id> --gpu-id <new>` (or via the RunPod dashboard).
2. Restart the edge container (or wait `price_cache_ttl_s`, default 3600s).

The worker re-queries the endpoint's `gpuIds` via the RunPod GraphQL API and
resolves the new `uninterruptablePrice`. No image rebuild required.

## Local-GPU mode

Not shipped in v1. A `GraniteSpeechLocalHandler` would be a separate future
worker package, not a config knob on this one.

## Languages and variants

`ibm-granite/granite-speech-4.1-2b` supports 6 ASR languages:
`en, fr, de, es, pt, ja`. Punctuation and truecasing for all 6 with the
hardcoded prompt ("transcribe the speech with proper punctuation and
capitalization.").

`granite-speech-4.1-2b-nar` (non-autoregressive) is explicitly excluded by
the deployer's choice. `granite-speech-4.1-2b-plus` (speaker-attributed
ASR + word-level timestamps) is deferred to a separate future sub-project.
```

- [ ] **Step 2: Commit**

```bash
git add workers/granite_speech/README.md
git commit -m "docs(workers): granite_speech README"
```

---

### Task 24: Add `workers/granite_speech` to top-level `pyproject.toml` workspace

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Read the existing workspace section + ruff per-path overrides + pytest testpaths**

```bash
uv run grep -n "workspace\|members\|qwen3tts/tests\|testpaths" pyproject.toml
```

- [ ] **Step 2: Add `workers/granite_speech` to the workspace members**

In `pyproject.toml`, find the `[tool.uv.workspace]` section and add
`"workers/granite_speech"` to the `members` list:

```toml
[tool.uv.workspace]
members = [
    "workers/qwen3tts",
    "workers/granite_speech",
]
```

- [ ] **Step 3: Add the per-path ruff overrides for `workers/granite_speech/tests`**

The existing `workers/qwen3tts/tests/**` block defines a set of relaxed
ruff rules for the worker test directory. Mirror it for granite_speech:

```toml
"workers/qwen3tts/tests/**" = ["D", "S", "PLC0415", "PLR2004", "TC001", "TC002", "TC003", "ANN", "ARG001", "ARG002", "FBT", "SLF001", "RUF043", "E501", "ASYNC240"]
"workers/granite_speech/tests/**" = ["D", "S", "PLC0415", "PLR2004", "TC001", "TC002", "TC003", "ANN", "ARG001", "ARG002", "FBT", "SLF001", "RUF043", "E501", "ASYNC240"]
"workers/granite_speech/**" = ["PLC0415"]  # lazy torch/transformers imports
```

- [ ] **Step 4: Add `workers/granite_speech/tests` to pytest testpaths**

```toml
testpaths = ["tests", "stubs/tests", "dashboard/tests", "workers/qwen3tts/tests", "workers/granite_speech/tests"]
```

- [ ] **Step 5: Verify the workspace syncs**

```bash
uv sync
```

Expected: `Installed 2 packages` (or similar; no errors).

- [ ] **Step 6: Run the worker tests**

```bash
uv run pytest workers/granite_speech/tests/ -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat(workspace): declare workers/granite_speech as uv workspace member"
```

---

### Task 24b: Update `Justfile` to type-check the new worker

**Files:**
- Modify: `Justfile:12`

- [ ] **Step 1: Add `workers/granite_speech/` to the `type-check` target**

Modify `Justfile:12` (the `type-check` recipe). The current target only
type-checks `workers/qwen3tts/`. Add the new worker:

```make
type-check:
    uv run mypy src/ tests/ workers/qwen3tts/ workers/granite_speech/
```

- [ ] **Step 2: Run the type-check target**

```bash
just type-check
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add Justfile
git commit -m "chore(justfile): type-check workers/granite_speech"
```

---

### Task 25: Extend `.github/workflows/build-workers.yml` with `build-granite-speech` job

**Files:**
- Modify: `.github/workflows/build-workers.yml`

- [ ] **Step 1: Read the current workflow**

```bash
uv run cat .github/workflows/build-workers.yml
```

- [ ] **Step 2: Add the new job**

Add a new `build-granite-speech` job after the existing `build-qwen3tts` job
(before `build-edge`):

```yaml
  build-granite-speech:
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
          file: workers/granite_speech/Dockerfile.runpod
          push: ${{ github.event_name != 'pull_request' }}
          tags: |
            ghcr.io/${{ github.repository }}/acheron-granite-speech-runpod:latest
            ghcr.io/${{ github.repository }}/acheron-granite-speech-runpod:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

- [ ] **Step 3: Validate the YAML**

```bash
uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/build-workers.yml'))"
```

Expected: silent (no parse error).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/build-workers.yml
git commit -m "ci(workers): publish acheron-granite-speech-runpod to GHCR"
```

---

### Task 26: Update `docker-compose.yml` with `granite-speech-edge` service

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Find the `qwen3tts-edge` service**

```bash
uv run grep -n "qwen3tts-edge" docker-compose.yml
```

- [ ] **Step 2: Add the new service after `qwen3tts-edge`**

```yaml
  granite-speech-edge:
    build:
      context: .
      dockerfile: Dockerfile.edge
    ports:
      - "8008:8001"
    environment:
      WORKER_NAME: granite_speech
      ACHERON_WORKER__ORCHESTRATOR_URL: https://orchestrator:8000
      ACHERON_WORKER__REGISTRATION_TOKEN: ${ACHERON_REGISTRATION_TOKEN:-dev-registration-token}
      ACHERON_WORKER__RUNPOD_API_KEY: ${RUNPOD_API_KEY:-}
      ACHERON_WORKER__RUNPOD_ENDPOINT_ID: ${GRANITE_SPEECH_RUNPOD_ENDPOINT_ID:-}
      ACHERON_WORKER__PRICE_SOURCE: ${GRANITE_SPEECH_PRICE_SOURCE:-runpod}
      ACHERON_WORKER__SECURE_CLOUD: "false"
      ACHERON_WORKER__LISTEN_PORT: "8001"
      SSL_CERT_FILE: /certs/acheron-ca.crt
    volumes:
      - ./certs:/certs:ro
    healthcheck:
      test:
        - "CMD-SHELL"
        - "python"
        - "-c"
        - "import urllib.request; urllib.request.urlopen('http://localhost:8001/health').read()"
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    depends_on:
      orchestrator:
        condition: service_healthy
    profiles: ["runpod-asr"]
```

Host port `8008` is the next free port in the existing matrix (stubs use
`8001`-`8007`; qwen3tts edge uses host `8004`). Internal container port is
`8001` (matches qwen3tts-edge).

- [ ] **Step 3: Validate the compose file**

```bash
uv run docker compose config 2>&1 | head -20 || echo "docker compose not available in dev shell — skip"
```

If `docker compose` is available, the output should include the new
`granite-speech-edge` service. If not, skip this step.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(compose): add granite-speech-edge service under runpod-asr profile"
```

---

### Task 27: Run `just validate` end-to-end

- [ ] **Step 1: Run the full validation gate**

```bash
just validate
```

Expected: all sub-targets (lint-strict, lint-imports, mypy, basedpyright,
pytest) green. Coverage ≥ 80% (the new modules add 200-400 LOC and tests
that exercise them).

If any target fails, fix the issue and re-run.

- [ ] **Step 2: Spot-check the file map against the spec**

Verify the spec's "File Map (Full Change List)" matches the actual
diff:

```bash
git diff --name-only master
```

Cross-check the spec's file map covers:

- `workers/_shared.py` (NEW) ✓
- `workers/_shared/tests/test_safe_chapter_id.py` (NEW) ✓
- `workers/qwen3tts/handler.py` (EXTENDED) ✓
- `workers/granite_speech/__init__.py` (NEW) ✓
- `workers/granite_speech/pyproject.toml` (NEW) ✓
- `workers/granite_speech/handler.py` (NEW) ✓
- `workers/granite_speech/runpod_entrypoint.py` (NEW) ✓
- `workers/granite_speech/worker.yaml` (NEW) ✓
- `workers/granite_speech/worker.edge.yaml` (NEW) ✓
- `workers/granite_speech/Dockerfile.runpod` (NEW) ✓
- `workers/granite_speech/README.md` (NEW) ✓
- `workers/granite_speech/tests/test_capabilities.py` (NEW) ✓
- `workers/granite_speech/tests/test_handler.py` (NEW) ✓
- `workers/granite_speech/tests/test_runpod_entrypoint.py` (NEW) ✓
- `pyproject.toml` (EXTENDED) ✓
- `Justfile` (EXTENDED) ✓
- `.github/workflows/build-workers.yml` (EXTENDED) ✓
- `docker-compose.yml` (EXTENDED) ✓

- [ ] **Step 3: Final commit (if there are any lint auto-fixes)**

```bash
git add -u
git commit -m "chore: apply lint autofixes from just validate"
```

Only commit if there are staged changes; otherwise skip.

- [ ] **Step 4: Notify completion**

The Layer 8b sub-project is complete. Summary:

- **Workers**: `workers/granite_speech/` (10 files) + `workers/_shared.py` shared helper.
- **8a refactor**: `Qwen3TTSRunpodHandler._chunk_chapter_id` delegates to `safe_chapter_id`.
- **CI + Compose**: `build-granite-speech` GHCR job + `granite-speech-edge` compose service under `runpod-asr` profile.

Out of scope (per the spec): `granite-speech-4.1-2b-nar`, `.plus`, AST,
local-GPU handler, per-step worker targeting.

---

## Final Validation

After all 14 tasks are complete and committed:

```bash
just validate
```

Expected: all sub-targets (lint-strict, lint-imports, mypy, basedpyright,
pytest) green. The 8a test suite + Sub-plan 1's tests + Sub-plan 2's
tests must still pass — no regressions.

## Adversarial Review (post-implementation)

Once `just validate` is green, dispatch a fresh-context reviewer subagent
with the rubric at the top of this sub-plan. The reviewer reads:

- This sub-plan
- The parent spec at `docs/superpowers/specs/2026-06-23-layer8b-asr-worker-design.md`
- The 8a qwen3tts plan at `docs/superpowers/plans/2026-06-22-layer8a-qwen3tts-worker-and-deploy.md` (for parallel structure validation)

…and produces findings in the same theme-keyed story format the 8a review
used (`docs/code_review/`). Fix any open CRITICAL / HIGH / MEDIUM findings
before declaring the Layer 8b sub-project done.
