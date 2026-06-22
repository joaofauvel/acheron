# Layer 8a — Qwen3-TTS Worker + Stubs + Deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `workers/qwen3tts/` worker package against `Qwen3-TTS-12Hz-1.7B-CustomVoice` (RunPod Serverless deployment mode only), replace the existing 4 stubs with the 7-stub SDK matrix, publish both the worker image + the generic `acheron-worker-edge` image to GHCR via CI, and wire the edge service into the main `docker-compose.yml`.

**Architecture:** `workers/qwen3tts/` is a workspace package outside the `acheron` import tree that depends on `acheron` (for the SDK + core types only — import-linter enforces `workers.* -/-> acheron.shell`). The worker ships a `Qwen3TTSRunpodHandler` implementing `acheron.worker_sdk.WorkerHandler` plus a `runpod_entrypoint.py` that calls `runpod.serverless.start({"handler": make_runpod_handler(handler)})`. The 7-stub matrix under `stubs/` replaces the existing 4 stubs and exercises the SDK across HTTP/gRPC × local/runpod × volume/multipart. CI publishes `acheron-qwen3tts-runpod` and `acheron-worker-edge` images to GHCR on tag + `main`.

**Tech Stack:** Python 3.12 (worker), `qwen-tts` PyPI package, PyTorch + CUDA 12.1, FlashAttention 2, soundfile, `acheron.worker_sdk` (from Plan 1), FastAPI + httpx (stubs), Flask stubs for testing.

**Prerequisites:**
- Plan 1 (`2026-06-22-layer8a-sdk-foundation.md`) merged — full `acheron.worker_sdk` package present.
- Plan 2 (`2026-06-22-layer8a-orchestrator-transports.md`) merged — `HttpWorker` multipart parser, `GrpcWorker` Artifact mode, `CostBasis` rendering on the dashboard.

**Reference spec:** `docs/superpowers/specs/2026-06-22-layer8a-tts-worker-design.md` (sections "The Qwen3-TTS RunPod Worker", "Stubs — SDK Test Matrix", "Deployment Flow", "GHCR CI Workflow").

**Final gate:** `just validate` green (tests pass without GPU; CI builds the images).

---

## File Structure

| File | Responsibility |
|------|---------------|
| `workers/qwen3tts/handler.py` | `Qwen3TTSRunpodHandler` — the cloud-side `WorkerHandler`. |
| `workers/qwen3tts/runpod_entrypoint.py` | Boot script — loads model, calls `runpod.serverless.start`. |
| `workers/qwen3tts/worker.yaml` | Image default config (no secrets). |
| `workers/qwen3tts/Dockerfile.runpod` | RunPod Serverless runtime image with CUDA + qwen-tts + flash-attn. |
| `workers/qwen3tts/pyproject.toml` | uv workspace member; deps lock via uv. |
| `workers/qwen3tts/README.md` | Deployer notes (RunPod template, env vars, cold start, network volume). |
| `workers/qwen3tts/tests/test_handler.py` | Handler unit tests with mocked `Qwen3TTSModel`. |
| `workers/qwen3tts/tests/test_capabilities.py` | Capabilities metadata validation. |
| `workers/qwen3tts/tests/__init__.py` | Empty package marker. |
| `stubs/_sdk_base/` | Shared SDK-backed stub helpers (singular handler factory). |
| `stubs/tts_local_stub/`, `tts_volume_stub/`, `tts_runpod_stub/`, `tts_grpc_stub/`, `asr_local_stub/`, `translation_local_stub/`, `translation_runpod_stub/` | The 7-stub matrix. |
| `stubs/tests/` | Replaces existing stub tests; SDK scaffold-driven. |
| `Dockerfile` | New `worker-stub-base` stage; per-stub `CMD` overrides via compose. |
| `Dockerfile.edge` | NEW — `acheron-worker-edge` image (runs the SDK CLI). |
| `docker-compose.yml` | New `qwen3tts-edge` service + updated stub services. |
| `pyproject.toml` | uv workspace members + `workers.* -/-> acheron.shell` import-linter contract. |
| `Justfile` | New `build-worker <name>` target. |
| `.github/workflows/build-workers.yml` | NEW — publish worker images to GHCR. |

---

### Task 1: Workspace scaffolding + import-linter contract for `workers.*`

**Files:**
- Create: `workers/qwen3tts/__init__.py` (empty)
- Create: `workers/qwen3tts/pyproject.toml`
- Modify: `pyproject.toml` (root — workspace members + import-linter contract)

- [ ] **Step 1: Declare the uv workspace**

Add to `pyproject.toml` (root) just below `[dependency-groups]` (after line 158):

```toml
[tool.uv.workspace]
members = ["workers/qwen3tts"]

[tool.uv.sources]
acheron = { workspace = true }
```

Add a new import-linter contract after `worker-sdk-no-shell`:

```toml
[[tool.importlinter.contracts]]
name = "workers-no-shell"
type = "forbidden"
source_modules = ["workers"]
forbidden_modules = ["acheron.shell"]
```

Add `workers.*` to `mypy_path` (currently `src:stubs`):

```toml
mypy_path = "src:stubs:workers"
```

And basedpyright:

```toml
[tool.basedpyright]
...
extraPaths = ["src", "stubs", "workers"]
```

Add `workers/qwen3tts/tests` to `testpaths`:

```toml
testpaths = ["tests", "stubs/tests", "dashboard/tests", "workers/qwen3tts/tests"]
```

- [ ] **Step 2: Create the worker package skeleton**

Create `workers/qwen3tts/__init__.py`:

```python
"""Acheron Qwen3-TTS RunPod serverless worker package."""
```

Create `workers/qwen3tts/pyproject.toml`:

```toml
[project]
name = "acheron-qwen3tts"
version = "0.1.0"
description = "RunPod Serverless worker package for Qwen3-TTS-12Hz-1.7B-CustomVoice"
requires-python = ">=3.12"
license = "GPL-3.0-only"
dependencies = [
    "acheron",
    "qwen-tts",
    "soundfile",
    # torch / flash-attn are pinned in the Docker image's RUN line
    # so the workspace tests don't try to install GPU-only wheels on dev machines.
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["."]
```

The package lives outside `acheron/` so it's not part of the orchestrator's wheel.

- [ ] **Step 3: Verify import-linter still passes**

```bash
uv sync --all-extras --all-packages
uv run lint-imports
```
Expected: exit 0 (no forbidden import attempted yet).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml workers/qwen3tts/__init__.py workers/qwen3tts/pyproject.toml
git commit -m "feat(workers): scaffold qwen3tts uv-workspace member + import-linter boundary"
```

---

### Task 2: `Qwen3TTSRunpodHandler` — `capabilities()` + `startup()`/`shutdown()`

**Files:**
- Create: `workers/qwen3tts/handler.py`
- Create: `workers/qwen3tts/tests/__init__.py` (empty)
- Create: `workers/qwen3tts/tests/test_capabilities.py`

- [ ] **Step 1: Write the failing test**

Create `workers/qwen3tts/tests/__init__.py` (empty) and `workers/qwen3tts/tests/test_capabilities.py`:

```python
"""Capability-shape tests for Qwen3TTSRunpodHandler."""

import pytest

from acheron.core.models import WorkerType
from acheron.worker_sdk.settings import WorkerSettings


def _settings(**overrides) -> WorkerSettings:
    base = {"worker_id": "w", "orchestrator_url": "http://o:8000", "price_source": "zero", "default_speaker": "Ryan"}
    base.update(overrides)
    return WorkerSettings(**base)  # type: ignore[arg-type]


def test_capabilities_shape() -> None:
    from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

    h = Qwen3TTSRunpodHandler(_settings())
    caps = h.capabilities()
    assert caps.worker_type == WorkerType.TTS
    assert caps.supported_formats_in == frozenset({"text"})
    assert caps.supported_formats_out == frozenset({"wav"})
    assert caps.batch_capable is True
    assert caps.model_source == "huggingface:Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"


def test_capabilities_languages_match_qwen3_tts_supported() -> None:
    from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

    h = Qwen3TTSRunpodHandler(_settings())
    caps = h.capabilities()
    expected = frozenset({"en", "zh", "ja", "ko", "de", "fr", "ru", "pt", "es", "it"})
    assert caps.supported_languages_in == expected
    assert caps.supported_languages_out == expected


def test_capabilities_metadata_lists_speakers_and_default() -> None:
    from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

    h = Qwen3TTSRunpodHandler(_settings(default_speaker="Ryan"))
    caps = h.capabilities()
    speakers = caps.metadata["speakers"]
    assert "Ryan" in speakers
    assert "Vivian" in speakers
    assert caps.metadata["default_speaker"] == "Ryan"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest workers/qwen3tts/tests/test_capabilities.py -v
```
Expected: `ImportError: No module named 'workers.qwen3tts.handler'`.

- [ ] **Step 3: Implement `handler.py` — first the capabilities + lifecycle**

Create `workers/qwen3tts/handler.py`:

```python
"""RunPod Serverless handler for Qwen3-TTS-12Hz-1.7B-CustomVoice.

This module runs **inside the RunPod serverless runtime image** (see
``Dockerfile.runpod``). The cloud-side ``runpod_entrypoint.py`` imports
``Qwen3TTSRunpodHandler`` here, calls ``startup()`` eagerly at boot, then
``runpod.serverless.start({"handler": make_runpod_handler(handler)})``. The
same handler import path is also used by the (optional) local edge container
in a future worker package.

A local-GPU fallback handler (``Qwen3TTSLocalHandler``) is deferred to a
separate future worker package — workers commit to one deployment mode by
being one mode, per the Layer 8a spec.
"""

from __future__ import annotations

import asyncio
import io
from typing import TYPE_CHECKING

from acheron.core.errors import WorkerError
from acheron.core.models import Job, WorkerCapabilities, WorkerType
from acheron.worker_sdk.artifacts import BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler

if TYPE_CHECKING:
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
_ALL_SPEAKERS = frozenset({
    "Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric",
    "Ryan", "Aiden", "Ono_Anna", "Sohee",
})
_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"


class Qwen3TTSRunpodHandler(WorkerHandler):
    """Cloud-side handler run inside the RunPod serverless runtime image.

    Loads the model eagerly at boot (runpod_entrypoint.py calls startup()), then
    serve via runpod.serverless.start(...). The SDK's make_runpod_handler
    adapter invokes ``handle()`` for each incoming RunPod job.
    """

    def __init__(self, settings: WorkerSettings) -> None:
        self._settings = settings
        self._model = None  # Qwen3TTSModel — typed loose to keep torch import out of test contexts.

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset(_LANG_MAP),
            supported_languages_out=frozenset(_LANG_MAP),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav"}),
            max_payload_bytes=None,
            batch_capable=True,
            model_source=f"huggingface:{_MODEL_ID}",
            metadata={
                "speakers": sorted(_ALL_SPEAKERS),
                "default_speaker": self._settings.default_speaker,
            },
        )

    async def startup(self) -> None:
        """Eagerly load the model onto the GPU at container boot."""
        import torch  # imported lazily so the workspace tests don't need torch.

        def _load() -> None:
            from qwen_tts import Qwen3TTSModel

            self._model = Qwen3TTSModel.from_pretrained(  # type: ignore[assignment]
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
            import torch

            torch.cuda.empty_cache()

    async def handle(self, job: Job) -> list[BytesArtifact]:
        """Run batched custom-voice inference for all chunks in the job."""
        if self._model is None:
            raise WorkerError("Qwen3-TTS model not loaded (startup() not run)")
        chunks = job.payload.get("chunks", [])
        if not chunks:
            return []
        target_lang = job.payload["target_language"]
        if target_lang not in _LANG_MAP:
            raise WorkerError(f"Unsupported target language: {target_lang}")
        qwen_lang = _LANG_MAP[target_lang]

        speaker = job.payload.get("speaker") or self._settings.per_language_defaults.get(
            target_lang, self._settings.default_speaker
        )
        if speaker not in _ALL_SPEAKERS:
            raise WorkerError(f"Unknown speaker '{speaker}' in worker config")

        texts = [c["text"] for c in chunks]
        languages = [qwen_lang] * len(chunks)
        speakers = [speaker] * len(chunks)
        instructs = [c.get("instruct", "") for c in chunks]

        import soundfile as sf

        def _generate():
            return self._model.generate_custom_voice(
                text=texts, language=languages, speaker=speakers, instruct=instructs
            )

        wavs, sr = await asyncio.to_thread(_generate)

        artifacts: list[BytesArtifact] = []
        for i, (wav, chunk) in enumerate(zip(wavs, chunks)):
            buf = io.BytesIO()
            sf.write(buf, wav, sr, format="WAV")
            seq = chunk.get("sequence_id", i)
            artifacts.append(
                BytesArtifact(
                    filename=f"{chunk['chapter_id']}_{seq:04d}.wav",
                    content_type="audio/wav",
                    data=buf.getvalue(),
                    metadata={
                        "sequence_id": seq,
                        "chapter_id": chunk["chapter_id"],
                        "sample_rate": sr,
                    },
                )
            )
        return artifacts
```

- [ ] **Step 4: Run test + type-check**

```bash
uv run pytest workers/qwen3tts/tests/test_capabilities.py -v
uv run mypy workers/qwen3tts/handler.py
uv run basedpyright workers/qwen3tts/handler.py
```
Expected: tests pass; type-checkers clean. (If mypy warns about the missing `qwen_tts` module, add a `[[tool.mypy.overrides]]` block listing `qwen_tts.*` with `ignore_missing_imports = true` to `pyproject.toml`.)

- [ ] **Step 5: Commit**

```bash
git add workers/qwen3tts/handler.py workers/qwen3tts/tests/__init__.py workers/qwen3tts/tests/test_capabilities.py pyproject.toml
git commit -m "feat(qwen3tts): add Qwen3TTSRunpodHandler capabilities + startup/shutdown"
```

---

### Task 3: Handler `handle()` unit test with mocked `Qwen3TTSModel`

**Files:**
- Create: `workers/qwen3tts/tests/test_handler.py`

- [ ] **Step 1: Write the failing test**

Create `workers/qwen3tts/tests/test_handler.py`:

```python
"""Unit tests for Qwen3TTSRunpodHandler.handle() with the model mocked."""

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from acheron.core.models import Job, WorkerType
from acheron.worker_sdk.artifacts import BytesArtifact
from acheron.worker_sdk.settings import WorkerSettings


def _settings(**overrides: Any) -> WorkerSettings:
    base = {"worker_id": "w", "orchestrator_url": "http://o:8000", "price_source": "zero", "default_speaker": "Ryan"}
    base.update(overrides)
    return WorkerSettings(**base)  # type: ignore[arg-type]


def _build_job(chunks: list[dict[str, Any]], target_language: str = "en") -> Job:
    return Job(
        job_id="job-xyz-synth-ch1",
        job_type=WorkerType.TTS,
        payload={"chapter_id": "ch1", "target_language": target_language, "chunks": chunks},
        chapter_id="ch1",
    )


class _FakeModel:
    def __init__(self, wavs: list[np.ndarray], sr: int) -> None:
        self._wavs = wavs
        self._sr = sr

    def generate_custom_voice(self, text, language, speaker, instruct):  # noqa: ANN001
        return self._wavs, self._sr


def _patch_model(monkeypatch: pytest.MonkeyPatch, wavs: list[np.ndarray], sr: int = 22050) -> _FakeModel:
    from workers.qwen3tts import handler as mod

    fake = _FakeModel(wavs, sr)
    # Patch the import site: `from qwen_tts import Qwen3TTSModel` inside startup.
    monkeypatch.setattr(mod, "_MODEL_ID", mod._MODEL_ID)  # ensure attribute exists
    # Easier — set `self._model` directly after construction.
    return fake


class TestHandle:
    @pytest.mark.asyncio
    async def test_handle_returns_bytes_artifacts_in_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        fake = _FakeModel(
            wavs=[np.zeros(100, dtype=np.float32), np.zeros(200, dtype=np.float32)],
            sr=22050,
        )
        h._model = fake  # bypass startup()
        job = _build_job([
            {"chapter_id": "ch1", "sequence_id": 0, "text": "hello"},
            {"chapter_id": "ch1", "sequence_id": 1, "text": "world"},
        ])
        out = await h.handle(job)
        assert len(out) == 2
        assert all(isinstance(a, BytesArtifact) for a in out)
        assert out[0].filename == "ch1_0000.wav"
        assert out[1].filename == "ch1_0001.wav"
        assert out[0].content_type == "audio/wav"
        assert out[0].metadata["sequence_id"] == 0
        assert out[1].metadata["sequence_id"] == 1
        # WAV sizes should differ (different sample counts)
        assert len(out[0].data) != len(out[1].data)

    @pytest.mark.asyncio
    async def test_handle_no_chunks_returns_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([], 22050)
        job = _build_job([])
        out = await h.handle(job)
        assert out == []

    @pytest.mark.asyncio
    async def test_handle_unknown_language_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler
        from acheron.core.errors import WorkerError

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([], 22050)
        job = _build_job(
            [{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}],
            target_language="xx",  # not in _LANG_MAP
        )
        with pytest.raises(WorkerError, match="Unsupported target language"):
            await h.handle(job)

    @pytest.mark.asyncio
    async def test_handle_unknown_speaker_in_config_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler
        from acheron.core.errors import WorkerError

        h = Qwen3TTSRunpodHandler(_settings(default_speaker="Bogus"))  # invalid default
        h._model = _FakeModel([np.zeros(50, dtype=np.float32)], 22050)
        job = _build_job([{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}])
        with pytest.raises(WorkerError, match="Unknown speaker"):
            await h.handle(job)

    @pytest.mark.asyncio
    async def test_handle_without_startup_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler
        from acheron.core.errors import WorkerError

        h = Qwen3TTSRunpodHandler(_settings())
        # Don't set _model
        job = _build_job([{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}])
        with pytest.raises(WorkerError, match="model not loaded"):
            await h.handle(job)

    @pytest.mark.asyncio
    async def test_handle_per_language_default_overrides_global_default(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        settings = _settings(
            default_speaker="Ryan",
            per_language_defaults={"zh": "Vivian"},
        )
        h = Qwen3TTSRunpodHandler(settings)
        h._model = _FakeModel([np.zeros(50, dtype=np.float32)], 22050)

        captured: dict[str, Any] = {}

        def _spy(text, language, speaker, instruct):  # noqa: ANN001
            captured["speaker"] = speaker
            return [np.zeros(50, dtype=np.float32)], 22050

        h._model.generate_custom_voice = _spy  # type: ignore[assignment]
        job = _build_job([{"chapter_id": "ch1", "sequence_id": 0, "text": "你好"}], target_language="zh")
        await h.handle(job)
        assert captured["speaker"] == ["Vivian"]
```

Add `numpy` as a dev dependency via uv (if not already in dev deps — it's pulled in by `qwen_tts` but workspace tests run without that):

```bash
uv add --group dev numpy --package workers-qwen3tts
# or root:
uv add --group dev numpy
```

Pick whichever uv syntax works in this repo's uv version; pin with `~=` per AGENTS.md (e.g., `numpy~=2.0`).

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest workers/qwen3tts/tests/test_handler.py -v
```
Expected: import errors or assertion failures until handler's `handle()` lands.

- [ ] **Step 3: Run test + type-check**

The handler's `handle()` body was included in Task 2 step 3 — confirm by reading `workers/qwen3tts/handler.py` that `handle()` is complete.

```bash
uv run pytest workers/qwen3tts/tests/test_handler.py -v
uv run mypy workers/qwen3tts/handler.py
uv run basedpyright workers/qwen3tts/handler.py
```
Expected: tests pass; type-checkers clean.

- [ ] **Step 4: Commit**

```bash
git add workers/qwen3tts/tests/test_handler.py pyproject.toml uv.lock
git commit -m "test(qwen3tts): cover handle() with mocked model — batch inference, error paths, per-language default"
```

---

### Task 4: `worker.yaml` + `runpod_entrypoint.py` + README

**Files:**
- Create: `workers/qwen3tts/worker.yaml`
- Create: `workers/qwen3tts/runpod_entrypoint.py`
- Create: `workers/qwen3tts/README.md`

- [ ] **Step 1: Create the image default config**

Create `workers/qwen3tts/worker.yaml`:

```yaml
# Qwen3-TTS worker — image default config.
# Sensitive fields (RUNPOD_API_KEY, RUNPOD_ENDPOINT_ID, REGISTRATION_TOKEN)
# are env-only — rejected when present here. Override per-deploy by mounting
# a qwen3tts.worker.yaml override or by setting ACHERON_WORKER_* env vars.

worker_id: "qwen3tts-1"
orchestrator_url: "http://orchestrator:8000"
listen_port: 8001
execution_timeout_s: 1800

# Pricing — RunPod GraphQL API is the default. The GPU type is NOT a config
# field: RunPodPrice reads the endpoint's gpuIds via the RunPod GraphQL API.
# Changing the GPU on the RunPod endpoint takes effect on the next
# price_cache_ttl_s refresh; no image rebuild required.
price_source: runpod
secure_cloud: false                  # quote community-cloud rate
# price_cache_ttl_s: 3600.0

# Single-speaker v1 — one speaker per job (consistent across all chunks).
# The planner never emits a 'speaker' in the plan payload; this worker picks it.
default_speaker: "Ryan"
per_language_defaults:
  en: "Ryan"
  zh: "Vivian"
  ja: "Ono_Anna"
  ko: "Sohee"

# Output transport — multipart default
output_mode: multipart

# Handler — used by the generic acheron-worker-edge CLI to import the handler
# class when running the edge container alongside the orchestrator. The
# runpod_entrypoint.py in the RunPod runtime image uses the import directly.
handler: "workers.qwen3tts.handler:Qwen3TTSRunpodHandler"
model_id: "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
```

- [ ] **Step 2: Create the cloud-side boot script**

Create `workers/qwen3tts/runpod_entrypoint.py`:

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
from acheron.worker_sdk.settings import WorkerSettings
from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    # The RunPod runtime image has no mounted worker.yaml; env drives config.
    # The CLI's discovery falls back to env vars when no YAML is present.
    from acheron.worker_sdk.config_loader import load_settings

    settings = load_settings()
    handler = Qwen3TTSRunpodHandler(settings)
    asyncio.run(handler.startup())  # eager model load
    runpod.serverless.start({"handler": make_runpod_handler(handler)})


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Write the deployer README**

Create `workers/qwen3tts/README.md`:

```markdown
# acheron-qwen3tts

RunPod Serverless worker package for `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`.

## Image

CI publishes `ghcr.io/<owner>/acheron-qwen3tts-runpod:latest` and
`:<sha>` on every push to `main` and on every `v*` tag. Pin your RunPod
template to `:<sha>` for reproducibility.

## RunPod Serverless setup (one-time)

1. **Create a network volume** for the HuggingFace cache to avoid re-downloading the ~3.4GB weights on every cold start. Mount it at `/runpod-volume/huggingface-cache`. Pre-warm it once:

   ```bash
   huggingface-cli download Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice \
       --local-dir /runpod-volume/huggingface-cache/hub/models--Qwen--Qwen3-TTS-12Hz-1.7B-CustomVoice
   ```

2. **Create a RunPod serverless template** pointing at the published image. Set:
   - GPU type list: `[L4, A5000, RTX 3090]` (24GB minimum; the worker's pricing auto-discovers which GPU the endpoint is actually using — no image rebuild required to switch).
   - Disk/container disk: ≥ 10 GB.
   - Network volume (from step 1) attached at `/runpod-volume`.
   - Environment variables: see "Environment variables" below.

3. **Create a serverless endpoint** from the template. Configure:
   - `workers_min: 0`, `workers_max: 1` (sufficient for one book at a time; bump for concurrent books).
   - `idle_timeout: 300` (matches the existing cost-containment strategy).
   - Note the endpoint ID.

4. **Configure the orchestrator-side edge service** (`docker-compose.yml`'s `qwen3tts-edge`):

   ```env
   ACHERON_REGISTRATION_TOKEN=<orchestrator's token>
   ACHERON_WORKER__RUNPOD_API_KEY=<your RunPod API key>
   ACHERON_WORKER__RUNPOD_ENDPOINT_ID=<endpoint id from step 3>
   ```

5. `docker compose up -d`. The edge registers with the orchestrator; the
   orchestrator's `HealthMonitor` reports the worker as `BOOTING` until
   RunPod scales up the GPU pod on the first `/execute`.

## Environment variables

| Variable | Required? | Description |
|----------|-----------|-------------|
| `ACHERON_WORKER_WORKER_ID` | yes (or via worker.yaml) | Worker ID used at registration. Default in worker.yaml: `qwen3tts-1`. |
| `ACHERON_WORKER_ORCHESTRATOR_URL` | yes | Orchestrator base URL. |
| `ACHERON_WORKER_REGISTRATION_TOKEN` | env-only | Bearer token used for `POST /workers`. |
| `ACHERON_WORKER_RUNPOD_API_KEY` | env-only | RunPod API key (used by the edge forwarder and by the RunPod price source). |
| `ACHERON_WORKER_RUNPOD_ENDPOINT_ID` | env-only | The RunPod serverless endpoint ID created in step 3 above. |
| `ACHERON_WORKER_EXECUTION_TIMEOUT_S` | optional | Per-job timeout (default 1800s). |
| `ACHERON_WORKER_PRICE_SOURCE` | optional | `runpod` (default) | `static` | `zero`. |
| `ACHERON_WORKER_SECURE_CLOUD` | optional | Quote secure-cloud vs community-cloud RunPod rate (default `false`). |
| `ACHERON_WORKER_DEFAULT_SPEAKER` | optional | Speaker used when job payload doesn't set one (default `Ryan`). |
| `ACHERON_WORKER_LISTEN_PORT` | optional | Edge container listen port (default 8001). |

## Switching GPU types

RunPod is the single source of truth for the GPU type. To change the GPU:

1. `runpodctl serverless update <endpoint-id> --gpu-id <new>` (or via the RunPod dashboard).
2. Restart the edge container (or wait `price_cache_ttl_s`, default 3600s).

The worker re-queries the endpoint's `gpuIds` via the RunPod GraphQL API and
resolves the new `uninterruptablePrice`. No image rebuild required.

## Local-GPU mode

Not shipped in v1. A `Qwen3TTSLocalHandler` would be a separate future worker
package, not a config knob on this one.

## Languages and speakers

`Qwen3-TTS-12Hz-1.7B-CustomVoice` supports 10 languages:
`en zh ja ko de fr ru pt es it`.

Built-in premium speakers:
`Vivian, Serena, Uncle_Fu, Dylan, Eric, Ryan, Aiden, Ono_Anna, Sohee`.

Voice cloning (via `Qwen3-TTS-12Hz-1.7B-Base`) is deferred to a separate
sub-project.
```

- [ ] **Step 4: Lint + type-check**

```bash
uv run ruff check workers/qwen3tts/runpod_entrypoint.py
uv run mypy workers/qwen3tts/runpod_entrypoint.py
```
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add workers/qwen3tts/worker.yaml workers/qwen3tts/runpod_entrypoint.py workers/qwen3tts/README.md
git commit -m "feat(qwen3tts): add worker.yaml image default + runpod_entrypoint.py + deployer README"
```

---

### Task 5: `Dockerfile.runpod` — RunPod Serverless runtime image

**Files:**
- Create: `workers/qwen3tts/Dockerfile.runpod`

- [ ] **Step 1: Write the Dockerfile**

Create `workers/qwen3tts/Dockerfile.runpod`:

```dockerfile
# RunPod Serverless runtime image for Qwen3-TTS-12Hz-1.7B-CustomVoice.
#
# Built from the repo root with:
#   docker build -f workers/qwen3tts/Dockerfile.runpod -t acheron-qwen3tts-runpod .
#
# CI publishes this image to ghcr.io via .github/workflows/build-workers.yml.

FROM python:3.12-slim AS runpod-runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# OS deps for soundfile (libsndfile) + flash-attn build (git + build-essential)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libsndfile1 \
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

# Worker deps — qwen-tts (model + transformers) + soundfile + flash-attn.
RUN pip install --no-cache-dir qwen-tts soundfile
RUN pip install --no-cache-dir flash-attn==2.5.9.post1 --no-build-isolation

# RunPod SDK so runpod.serverless.start() is importable.
RUN pip install --no-cache-dir runpod

WORKDIR /app

# The worker's deployable assets. The cloud-side handler is entrypoint-only.
COPY workers/qwen3tts/handler.py /app/handler.py
COPY workers/qwen3tts/runpod_entrypoint.py /app/runpod_entrypoint.py
COPY workers/qwen3tts/worker.yaml /app/worker.yaml

# HF cache lives on the RunPod network volume; offline mode forces the cached snapshot.
ENV HF_HOME=/runpod-volume/huggingface-cache \
    PYTHONPATH=/app

CMD ["python", "runpod_entrypoint.py"]
```

Context for the build is the **repo root** so `dist/acheron-*.whl` (built by the CI job before this step) and `workers/qwen3tts/` files are reachable.

- [ ] **Step 2: Smoke the build locally (without pushing)**

```bash
uv build --package acheron --out-dir dist
docker build -f workers/qwen3tts/Dockerfile.runpod -t acheron-qwen3tts-runpod:dev .
docker run --rm acheron-qwen3tts-runpod:dev python -c "import handler; print(handler.Qwen3TTSRunpodHandler)"
```
Expected: `python:3.12-slim` base; wheels install; `python -c` prints the class repr without trying to load the model (lazy import).

If the `flash-attn` build fails on your machine (no compatible GPU), xfail-local the build check — the CI workflow uses `cache-from: type=gha` and a runner with sufficient RAM. Don't fail the task locally on flash-attn alone.

- [ ] **Step 3: Add a `just build-worker` target**

Update `Justfile` to add (after the existing `certs:` target):

```makefile
# Build a worker image locally for dev iteration. CI does the real publish.
build-worker name:
    uv build --package acheron --out-dir dist
    docker build -f workers/{{name}}/Dockerfile.runpod -t acheron-{{name}}-runpod:dev .

# Build the generic edge image (acheron-worker-edge).
build-edge:
    docker build -f Dockerfile.edge -t acheron-worker-edge:dev .
```

- [ ] **Step 4: Lint the Dockerfile (hadolint if installed, else skip)**

```bash
hadolint workers/qwen3tts/Dockerfile.runpod 2>&1 | head -30 || echo "hadolint not installed; skipping"
```
Don't fail the task on hadolint not installed — it's a nice-to-have.

- [ ] **Step 5: Commit**

```bash
git add workers/qwen3tts/Dockerfile.runpod Justfile
git commit -m "feat(qwen3tts): add RunPod serverless runtime Dockerfile + just build-worker target"
```

---

### Task 6: `Dockerfile.edge` — generic `acheron-worker-edge` image

**Files:**
- Create: `Dockerfile.edge`

- [ ] **Step 1: Write the Dockerfile**

Create `Dockerfile.edge` (at repo root — the same level as the existing `Dockerfile`):

```dockerfile
# acheron-worker-edge — generic RunPod-edge container.
#
# GPU-less. Runs alongside the orchestrator in docker-compose.yml, registers
# with the orchestrator, and forwards `/execute` calls to a RunPod serverless
# endpoint via the runpod SDK. The same image serves all RunPod workers
# (TTS/ASR/translation); per-worker config is `WORKER_NAME` + `worker.yaml`
# + env. The deployer never invokes the CLI directly — this image's CMD is
# the entrypoint.

FROM python:3.14-slim AS edge

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# The acheron wheel provides the worker_sdk + console script.
COPY dist/acheron-*.whl /tmp/
RUN pip install /tmp/acheron-*.whl && rm /tmp/acheron-*.whl

# The handler module is invisible here — the edge resolves it from the worker.yaml
# `handler:` field via the SDK's config loader at boot. For the qwen3tts edge
# container, we bundle the handler.py from the workspace package so the import
# path `workers.qwen3tts.handler:Qwen3TTSRunpodHandler` resolves.
#
# Future worker packages (asr, translation) mirror this COPY with their own
# handler.py.
COPY workers/qwen3tts/handler.py /app/workers/qwen3tts/handler.py
COPY workers/qwen3tts/worker.yaml /app/qwen3tts.worker.yaml
RUN touch /app/workers/__init__.py /app/workers/qwen3tts/__init__.py

ENV WORKER_NAME=qwen3tts \
    PYTHONPATH=/app

EXPOSE 8001
CMD ["acheron-worker-edge"]
```

The command `acheron-worker-edge` resolves to the console script installed by the wheel's `[project.scripts]` entry. The CLI defaults `--config` to the discovery flow; `WORKER_NAME=qwen3tts` tells the config loader to pick `qwen3tts.worker.yaml` from `/app`. Deployers override via env (`ACHERON_WORKER_*`) for RunPod endpoint id, API key, registration token.

- [ ] **Step 2: Smoke the build**

```bash
uv build --package acheron --out-dir dist
docker build -f Dockerfile.edge -t acheron-worker-edge:dev .
docker run --rm -e ACHERON_WORKER_ORCHESTRATOR_URL=http://localhost:9999 \
                -e ACHERON_WORKER_WORKER_ID=qwen3tts-edge \
                -e ACHERON_WORKER_PRICE_SOURCE=zero \
    acheron-worker-edge:dev --help
```
Expected: `--help` prints and exits, showing the `--handler` flag.

- [ ] **Step 3: Commit**

```bash
git add Dockerfile.edge
git commit -m "feat(deploy): add acheron-worker-edge Dockerfile for the RunPod edge container"
```

---

### Task 7: `docker-compose.yml` — add the `qwen3tts-edge` service

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Append the service**

Add to `docker-compose.yml` (under `services:`, after `translation-stub:` or at the bottom of the `services:` block):

```yaml
  qwen3tts-edge:
    build:
      context: .
      dockerfile: Dockerfile.edge
    ports:
      - "8004:8001"
    environment:
      WORKER_NAME: qwen3tts
      ACHERON_WORKER_ORCHESTRATOR_URL: https://orchestrator:8000
      ACHERON_WORKER_REGISTRATION_TOKEN: ${ACHERON_REGISTRATION_TOKEN:-dev-registration-token}
      ACHERON_WORKER_RUNPOD_API_KEY: ${RUNPOD_API_KEY:-}
      ACHERON_WORKER_RUNPOD_ENDPOINT_ID: ${QWEN3TTS_RUNPOD_ENDPOINT_ID:-}
      ACHERON_WORKER_PRICE_SOURCE: ${QWEN3TTS_PRICE_SOURCE:-runpod}
      ACHERON_WORKER_SECURE_CLOUD: "false"
      ACHERON_WORKER_DEFAULT_SPEAKER: "Ryan"
      ACHERON_WORKER_LISTEN_PORT: "8001"
      SSL_CERT_FILE: /certs/acheron-ca.crt
    volumes:
      - ./certs:/certs:ro
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8001/health').read()\""]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    depends_on:
      orchestrator:
        condition: service_healthy
    profiles: ["runpod-tts"]
```

The `profiles: ["runpod-tts"]` makes the service opt-in via `docker compose --profile runpod-tts up`. By default (no profile), only the stub services come up — keeping `docker compose up` working without RunPod credentials for local dev.

- [ ] **Step 2: Verify compose config is valid**

```bash
docker compose config --profiles
docker compose --profile runpod-tts config >/dev/null
```
Expected: exit 0 for both. The second command should print the rendered `qwen3tts-edge` service with all env vars resolved.

- [ ] **Step 3: Smoke-raise the edge container standalone**

```bash
docker build -f Dockerfile.edge -t acheron-worker-edge:dev .
docker run --rm -e ACHERON_WORKER_ORCHESTRATOR_URL=http://localhost:9999 \
                -e ACHERON_WORKER_WORKER_ID=qwen3tts-edge \
                -e ACHERON_WORKER_PRICE_SOURCE=zero \
    acheron-worker-edge:dev
```
Expected: the container boots, tries to register with `localhost:9999` (which isn't there → retries with backoff), exits on Ctrl-C. Confirm the FastAPI lifespan ran startup (no exception in logs about missing model — because `price_source=zero` doesn't refresh RunPod).

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(compose): add qwen3tts-edge service under runpod-tts profile"
```

---

### Task 8: Stub matrix — SDK-backed stubs replacing the existing 4 stubs

**Files:**
- Create: `stubs/_sdk_base/__init__.py` (shared handlers + config factory)
- Create: `stubs/tts_local_stub/main.py`, `worker.yaml`
- Create: `stubs/tts_volume_stub/main.py`, `worker.yaml`
- Create: `stubs/tts_runpod_stub/main.py`, `worker.yaml`, `mock_runpod.py`
- Create: `stubs/tts_grpc_stub/main.py`, `worker.yaml`
- Create: `stubs/asr_local_stub/main.py`, `worker.yaml`
- Create: `stubs/translation_local_stub/main.py`, `worker.yaml`
- Create: `stubs/translation_runpod_stub/main.py`, `worker.yaml`, `mock_runpod.py`
- Delete: `stubs/worker_stub.py`, `stubs/grpc_worker_stub.py`, `stubs/translation_stub.py`, old `stubs/tests/`
- Modify: `stubs/tests/` (replace with SDK scaffold tests)
- Modify: `Dockerfile` (one `worker-stub-base` stage + per-stub `CMD` overrides via compose)
- Modify: `docker-compose.yml` (update stub services' `command:` per-stub)

This is the largest task — break it into smaller commits per stub.

**Step 1: Write a shared "deterministic-stub" handler factory**

Create `stubs/_sdk_base/__init__.py`:

```python
"""Shared SDK-backed stub handlers for the SDK matrix.

The stubs exercise the SDK across local/runpod, http/grpc, volume/multipart.
Each stub is a 30-line `main.py` calling ``create_worker_app`` from the SDK;
per-stub variance comes from ``worker.yaml`` + the handler class passed.
"""

from __future__ import annotations

import asyncio
import io
import struct
from typing import Any

from acheron.core.models import Job, WorkerCapabilities, WorkerType
from acheron.worker_sdk.artifacts import BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler


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

    async def startup(self) -> None: pass
    async def shutdown(self) -> None: pass

    async def handle(self, job: Job) -> list[BytesArtifact]:
        chunks = job.payload.get("chunks", [])
        return [
            BytesArtifact(
                filename=f"{c.get('chapter_id', 'ch')}_{i:04d}.wav",
                content_type="audio/wav",
                data=_silent_wav(),
                metadata={"sequence_id": c.get("sequence_id", i)},
            )
            for i, c in enumerate(chunks)
        ] or [BytesArtifact(filename="out.wav", content_type="audio/wav", data=_silent_wav(), metadata={})]


class StubASRHandler(WorkerHandler):
    """Deterministic ASR stub — returns canned whispered-transcript text."""

    def __init__(self, _settings: Any) -> None:
        self._settings = _settings

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            worker_type=WorkerType.ASR,
            supported_languages_in=frozenset({"en", "es", "fr", "de"}),
            supported_languages_out=frozenset({"en", "es", "fr", "de"}),
            supported_formats_in=frozenset({"mp3", "wav"}),
            supported_formats_out=frozenset({"text"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
            metadata={"stub": True},
        )

    async def startup(self) -> None: pass
    async def shutdown(self) -> None: pass

    async def handle(self, job: Job) -> list[BytesArtifact]:
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

    async def startup(self) -> None: pass
    async def shutdown(self) -> None: pass

    async def handle(self, job: Job) -> list[BytesArtifact]:
        chunks = job.payload.get("chunks", [])
        translated = [c.get("text", "") for c in chunks]  # identity
        body = "\n\n".join(translated).encode("utf-8")
        return [
            BytesArtifact(
                filename=f"{job.chapter_id}.txt",
                content_type="text/plain",
                data=body,
                metadata={"chapter_id": job.chapter_id},
            )
        ]
```

- [ ] **Step 2: Stub `main.py` template**

Create `stubs/tts_local_stub/main.py`:

```python
"""TTS stub — HTTP edge, multipart output, local price=zero."""

import uvicorn

from acheron.worker_sdk import WorkerSettings, create_worker_app
from stubs._sdk_base import StubTTSHandler


def main() -> None:
    from acheron.worker_sdk.config_loader import load_settings

    settings = load_settings()
    handler = StubTTSHandler(settings)
    app = create_worker_app(handler=handler, settings=settings)
    uvicorn.run(app, host=settings.listen_host, port=settings.listen_port)


if __name__ == "__main__":
    main()
```

Create `stubs/tts_local_stub/worker.yaml`:

```yaml
worker_id: "tts-local-stub"
orchestrator_url: "http://orchestrator:8000"
listen_port: 8001
price_source: zero
output_mode: multipart
```

- [ ] **Step 3: Duplicate for the other 6 stubs**

For each stub below, create `stubs/<stub_name>/main.py` and `stubs/<stub_name>/worker.yaml`. The `main.py` body is identical across all local/HTTP stubs except for the port and the handler import name; the `worker.yaml` differs per row. Local stubs (HTTP, multipart or volume) all use this exact `main.py`:

```python
"""<stub_name> — SDK-backed stub."""

import uvicorn

from acheron.worker_sdk.config_loader import load_settings
from stubs._sdk_base import <HandlerClass>  # see matrix below


def main() -> None:
    settings = load_settings()
    handler = <HandlerClass>(settings)
    from acheron.worker_sdk import create_worker_app

    app = create_worker_app(handler=handler, settings=settings)
    uvicorn.run(app, host=settings.listen_host, port=settings.listen_port)


if __name__ == "__main__":
    main()
```

**Stub matrix** (substitute `<HandlerClass>` + per-stub `worker.yaml`):

| Stub | `<HandlerClass>` | `worker_id` | `output_mode` | `price_source` | Notes |
|---|---|---|---|---|---|
| `tts_volume_stub` | `StubTTSHandler` | `tts-volume-stub` | `volume` | `zero` | `output_volume_dir: /data` in yaml. |
| `tts_runpod_stub` | `StubTTSHandler` | `tts-runpod-stub` | `multipart` | `static` | `dollars_per_hour: 0.69` in yaml. main.py also starts a mock RunPod server — see RunPod-stub body below. |
| `tts_grpc_stub` | `StubTTSHandler` | `tts-grpc-stub` | `multipart` | `zero` | Pure-HTTP edge stub (the existing in-process gRPC server is replaced by Plan 2's `_FakeSynthesisServicer` test pattern; the stub stays HTTP-edge because v1 workers ship HTTP only). main.py matches the local template above. |
| `asr_local_stub` | `StubASRHandler` | `asr-local-stub` | `multipart` | `zero` | Plain local template. |
| `translation_local_stub` | `StubTranslationHandler` | `translation-local-stub` | `multipart` | `zero` | Plain local template. |
| `translation_runpod_stub` | `StubTranslationHandler` | `translation-runpod-stub` | `multipart` | `static` | `dollars_per_hour: 0.69`. main.py also starts a mock RunPod server — same body as `tts_runpod_stub`'s. |

The full `worker.yaml` for each row (substitute the file path per stub):

`stubs/tts_volume_stub/worker.yaml`:
```yaml
worker_id: "tts-volume-stub"
orchestrator_url: "http://orchestrator:8000"
listen_port: 8002
price_source: zero
output_mode: volume
output_volume_dir: /data
```

`stubs/tts_runpod_stub/worker.yaml`:
```yaml
worker_id: "tts-runpod-stub"
orchestrator_url: "http://orchestrator:8000"
listen_port: 8003
price_source: static
dollars_per_hour: 0.69
output_mode: multipart
```

`stubs/tts_grpc_stub/worker.yaml`:
```yaml
# The gRPC stub's worker-edge half stays HTTP — Plan 2 ships Artifact-mode
# OutputChunk on the proto side and the existing test_grpc_worker.py covers
# the gRPC path. This stub keeps the HTTP-edge side alive for compose-level
# healthcheck / registration parity.
worker_id: "tts-grpc-stub"
orchestrator_url: "http://orchestrator:8000"
listen_port: 9002
price_source: zero
output_mode: multipart
```

`stubs/asr_local_stub/worker.yaml`:
```yaml
worker_id: "asr-local-stub"
orchestrator_url: "http://orchestrator:8000"
listen_port: 8004
price_source: zero
output_mode: multipart
```

`stubs/translation_local_stub/worker.yaml`:
```yaml
worker_id: "translation-local-stub"
orchestrator_url: "http://orchestrator:8000"
listen_port: 8005
price_source: zero
output_mode: multipart
```

`stubs/translation_runpod_stub/worker.yaml`:
```yaml
worker_id: "translation-runpod-stub"
orchestrator_url: "http://orchestrator:8000"
listen_port: 8006
price_source: static
dollars_per_hour: 0.69
output_mode: multipart
```

**RunPod-mock stub body** (used by `tts_runpod_stub/main.py` and `translation_runpod_stub/main.py`). The mock RunPod server runs on `127.0.0.1:8999`; the SDK's `_runpod_client` honors the `ACHERON_WORKER_RUNPOD_BASE_URL` env hook in tests. For the stub compose service, the mock starts in-process on the same container:

`stubs/tts_runpod_stub/main.py`:
```python
"""TTS stub — RunPod edge with in-process mocked RunPod endpoint."""

import os

import uvicorn

from acheron.worker_sdk import create_worker_app
from acheron.worker_sdk.config_loader import load_settings
from stubs._sdk_base import StubTTSHandler
from stubs._sdk_base.mock_runpod import start_mock_runpod_in_thread


def main() -> None:
    # Start a mock server that speaks RunPod's /run + /status protocol.
    start_mock_runpod_in_thread(
        port=8999,
        artifacts_response={"artifacts": [{"filename": "out.wav", "data": "AAEC"}]},
    )
    os.environ.setdefault("ACHERON_WORKER_RUNPOD_BASE_URL", "http://127.0.0.1:8999")
    settings = load_settings()
    handler = StubTTSHandler(settings)
    app = create_worker_app(handler=handler, settings=settings)
    uvicorn.run(app, host=settings.listen_host, port=settings.listen_port)


if __name__ == "__main__":
    main()
```

`stubs/translation_runpod_stub/main.py`: identical except `from stubs._sdk_base import StubTranslationHandler` and the mock returns a text artifact instead of WAV.

For the RunPod-mock variants, create `stubs/_sdk_base/mock_runpod.py`:

```python
"""In-process HTTP server that speaks RunPod's /run + /status protocol for stubs."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from fastapi import FastAPI


def make_mock_runpod_app(artifacts_response: dict[str, Any]) -> FastAPI:
    app = FastAPI()

    @app.post("/run")
    async def run(body: dict[str, Any]) -> dict[str, Any]:
        return {"id": "stub-job-1", "status": "COMPLETED", "output": artifacts_response}

    @app.get("/status/{job_id}")
    async def status(job_id: str) -> dict[str, Any]:
        return {"status": "COMPLETED"}

    return app


def start_mock_runpod_in_thread(port: int, artifacts_response: dict[str, Any]) -> Any:
    import uvicorn

    app = make_mock_runpod_app(artifacts_response)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server
```

The `tts_runpod_stub/main.py`:

```python
"""TTS stub — RunPod edge with mocked RunPod endpoint."""

import os

import uvicorn

from acheron.worker_sdk import WorkerSettings, create_worker_app
from acheron.worker_sdk.config_loader import load_settings
from stubs._sdk_base import StubTTSHandler
from stubs._sdk_base.mock_runpod import start_mock_runpod_in_thread


def main() -> None:
    os.environ.setdefault("ACHERON_WORKER_RUNPOD_BASE_URL", "http://127.0.0.1:8999")
    # Start the mock RunPod endpoint once before the edge registers.
    start_mock_runpod_in_thread(
        port=8999,
        artifacts_response={"artifacts": [{"filename": "out.wav", "data": "AAEC"}]},
    )
    settings = load_settings()
    handler = StubTTSHandler(settings)
    app = create_worker_app(handler=handler, settings=settings)
    uvicorn.run(app, host=settings.listen_host, port=settings.listen_port)


if __name__ == "__main__":
    main()
```

Note: the SDK's `_runpod_client` honors `ACHERON_WORKER_RUNPOD_BASE_URL` when set (test hook). If the SDK doesn't yet expose that, add a small `RUNPOD_BASE_URL` reading on `_runpod_client._open_endpoint`:

```python
def _open_endpoint(endpoint_id: str, *, api_key: str) -> _Endpoint:
    import runpod

    runpod.api_key = api_key
    base_url = os.environ.get("ACHERON_WORKER_RUNPOD_BASE_URL")
    if base_url:
        # Point the SDK at the mock — depends on runpod API; in practice we
        # monkeypatch via the test harness. The stub's lazily-imported variant
        # of this function can patch runpod.Endpoint to return a fake stub.
        ...
    return runpod.Endpoint(endpoint_id)
```

If the real `runpod` SDK doesn't expose `base_url` plumbing, fall back to `monkeypatch.setattr` in `tests/shell/transports/test_runpod_backend.py` (Plan 1 covers `_runpod_client` with a fake `runpod.Endpoint` injected via `monkeypatch.setattr(mod, "_open_endpoint", _factory)`). The `BASE_URL` env hook in the stub's `main.py` is documented as test-helper.

For each of the 7 stubs, follow the same `main.py` template — only the handler class, `worker_id`, and `output_mode`/`price_source` env-config differ.

- [ ] **Step 4: Delete the old stubs + their tests**

```bash
git rm stubs/worker_stub.py stubs/grpc_worker_stub.py stubs/translation_stub.py
rm -rf stubs/tests/
```

Create a fresh `stubs/tests/__init__.py` and replace the old tests with SDK-stub tests. The fresh tests:

```python
# stubs/tests/test_stubs_healthy.py
"""Smoke tests that each SDK stub's create_worker_app returns 200 on /health."""

import httpx
import pytest
from httpx import ASGITransport

from acheron.worker_sdk import WorkerSettings
from stubs._sdk_base import StubTTSHandler, StubASRHandler, StubTranslationHandler


@pytest.mark.parametrize(
    ("handler_cls", "worker_id"),
    [
        (StubTTSHandler, "tts-local-stub"),
        (StubASRHandler, "asr-local-stub"),
        (StubTranslationHandler, "translation-local-stub"),
    ],
)
@pytest.mark.asyncio
async def test_stub_health(handler_cls, worker_id, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACHERON_WORKER_WORKER_ID", worker_id)
    monkeypatch.setenv("ACHERON_WORKER_ORCHESTRATOR_URL", "http://orch:8000")
    monkeypatch.setenv("ACHERON_WORKER_PRICE_SOURCE", "zero")
    settings = WorkerSettings()
    h = handler_cls(settings)
    from acheron.worker_sdk.app import create_worker_app

    app = create_worker_app(handler=h, settings=settings, disable_registration=True)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
```

Also test that each stub's `/execute` returns multipart:

```python
@pytest.mark.asyncio
async def test_tts_stub_execute_returns_multipart(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACHERON_WORKER_WORKER_ID", "tts-local-stub")
    monkeypatch.setenv("ACHERON_WORKER_ORCHESTRATOR_URL", "http://orch:8000")
    monkeypatch.setenv("ACHERON_WORKER_PRICE_SOURCE", "zero")
    from acheron.worker_sdk.app import create_worker_app

    settings = WorkerSettings()
    h = StubTTSHandler(settings)
    app = create_worker_app(handler=h, settings=settings, disable_registration=True)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/execute",
            json={
                "job_id": "j1",
                "job_type": "tts",
                "payload": {"chapter_id": "ch1", "chunks": [{"text": "hi", "chapter_id": "ch1", "sequence_id": 0}], "target_language": "en"},
                "chapter_id": "ch1",
            },
        )
    assert r.status_code == 200
    assert "multipart/mixed" in r.headers["content-type"]
```

- [ ] **Step 5: Update `Dockerfile`**

In `Dockerfile` at the repo root, replace the existing `worker-stub` and `grpc-stub` stages with a single `worker-stub-base` stage:

```dockerfile
FROM python:3.14-slim AS worker-stub-base

WORKDIR /app
COPY --from=builder /app/dist/*.whl ./
COPY stubs/ ./stubs/
RUN pip install --no-cache-dir ./*.whl && rm ./*.whl
ENV PYTHONPATH=/app
# Per-stub CMD specified in docker-compose.yml. Override via `command:` field.
```

Remove the existing `FROM python:3.14-slim AS grpc-stub` stage entirely — `tts_grpc_stub` is now `python -m stubs.tts_grpc_stub.main` running on the same base.

- [ ] **Step 6: Update `docker-compose.yml` stub services**

Replace each `tts-stub`, `asr-stub`, `translation-stub`, `tts-grpc-stub` service with the new SDK-based ones. Anchor + override pattern keeps the YAML DRY:

```yaml
  tts-local-stub:
    build:
      context: .
      target: worker-stub-base
    ports: ["8001:8001"]
    environment: &stub_env
      WORKER_NAME: tts-local-stub
      ACHERON_WORKER_ORCHESTRATOR_URL: https://orchestrator:8000
      ACHERON_WORKER_WORKER_ID: tts-local-stub
      ACHERON_WORKER_REGISTRATION_TOKEN: ${ACHERON_REGISTRATION_TOKEN:-dev-registration-token}
      ACHERON_WORKER_PRICE_SOURCE: zero
      ACHERON_WORKER_LISTEN_PORT: "8001"
      SSL_CERT_FILE: /certs/acheron-ca.crt
    volumes:
      - ./certs:/certs:ro
    command: ["python", "-m", "stubs.tts_local_stub.main"]
    healthcheck: &stub_healthcheck
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8001/health').read()\""]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    depends_on:
      orchestrator: { condition: service_healthy }
```

Then the remaining six stub services, each using YAML merge keys against the anchors:

```yaml
  tts-volume-stub:
    build: { context: ., target: worker-stub-base }
    ports: ["8002:8002"]
    environment:
      <<: *stub_env
      WORKER_NAME: tts-volume-stub
      ACHERON_WORKER_WORKER_ID: tts-volume-stub
      ACHERON_WORKER_LISTEN_PORT: "8002"
      ACHERON_WORKER_OUTPUT_MODE: volume
      ACHERON_WORKER_OUTPUT_VOLUME_DIR: /data
    volumes:
      - ./certs:/certs:ro
      - acheron-data:/data
    command: ["python", "-m", "stubs.tts_volume_stub.main"]
    healthcheck:
      <<: *stub_healthcheck
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8002/health').read()\""]
    depends_on: { orchestrator: { condition: service_healthy } }

  tts-runpod-stub:
    build: { context: ., target: worker-stub-base }
    ports: ["8003:8003"]
    environment:
      <<: *stub_env
      WORKER_NAME: tts-runpod-stub
      ACHERON_WORKER_WORKER_ID: tts-runpod-stub
      ACHERON_WORKER_LISTEN_PORT: "8003"
      ACHERON_WORKER_PRICE_SOURCE: static
      ACHERON_WORKER_DOLLARS_PER_HOUR: "0.69"
    volumes: [ "./certs:/certs:ro" ]
    command: ["python", "-m", "stubs.tts_runpod_stub.main"]
    healthcheck:
      <<: *stub_healthcheck
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8003/health').read()\""]
    depends_on: { orchestrator: { condition: service_healthy } }

  tts-grpc-stub:
    build: { context: ., target: worker-stub-base }
    ports: ["9002:9002"]
    environment:
      <<: *stub_env
      WORKER_NAME: tts-grpc-stub
      ACHERON_WORKER_WORKER_ID: tts-grpc-stub
      ACHERON_WORKER_LISTEN_PORT: "9002"
    volumes: [ "./certs:/certs:ro" ]
    command: ["python", "-m", "stubs.tts_grpc_stub.main"]
    healthcheck:
      <<: *stub_healthcheck
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:9002/health').read()\""]
    depends_on: { orchestrator: { condition: service_healthy } }

  asr-local-stub:
    build: { context: ., target: worker-stub-base }
    ports: ["8004:8004"]
    environment:
      <<: *stub_env
      WORKER_NAME: asr-local-stub
      ACHERON_WORKER_WORKER_ID: asr-local-stub
      ACHERON_WORKER_LISTEN_PORT: "8004"
    volumes: [ "./certs:/certs:ro" ]
    command: ["python", "-m", "stubs.asr_local_stub.main"]
    healthcheck:
      <<: *stub_healthcheck
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8004/health').read()\""]
    depends_on: { orchestrator: { condition: service_healthy } }

  translation-local-stub:
    build: { context: ., target: worker-stub-base }
    ports: ["8005:8005"]
    environment:
      <<: *stub_env
      WORKER_NAME: translation-local-stub
      ACHERON_WORKER_WORKER_ID: translation-local-stub
      ACHERON_WORKER_LISTEN_PORT: "8005"
    volumes: [ "./certs:/certs:ro" ]
    command: ["python", "-m", "stubs.translation_local_stub.main"]
    healthcheck:
      <<: *stub_healthcheck
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8005/health').read()\""]
    depends_on: { orchestrator: { condition: service_healthy } }

  translation-runpod-stub:
    build: { context: ., target: worker-stub-base }
    ports: ["8006:8006"]
    environment:
      <<: *stub_env
      WORKER_NAME: translation-runpod-stub
      ACHERON_WORKER_WORKER_ID: translation-runpod-stub
      ACHERON_WORKER_LISTEN_PORT: "8006"
      ACHERON_WORKER_PRICE_SOURCE: static
      ACHERON_WORKER_DOLLARS_PER_HOUR: "0.69"
    volumes: [ "./certs:/certs:ro" ]
    command: ["python", "-m", "stubs.translation_runpod_stub.main"]
    healthcheck:
      <<: *stub_healthcheck
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8006/health').read()\""]
    depends_on: { orchestrator: { condition: service_healthy } }
```

Keep the existing gRPC stub working by updating it to the new `OutputChunk.artifact` contract — the gRPC stub's `main.py` uses the SDK's `create_worker_app` (HTTP) for its registration side-car; the actual audio streaming is handled by the SDK's `_edge_http` returning multipart, and the gRPC portion of the stub still uses the existing in-process server but emits `OutputChunk` with `artifact` parts.

For the gRPC stub, keep the existing in-process gRPC `SynthesisServicer` proto implementation but migrate it to emit `OutputChunk(artifact=...)` parts instead of `AudioChunk(pcm_data=...)` parts. The existing `_FakeSynthesisServicer` from `tests/shell/test_grpc_worker.py` (Plan 2 Task 5) is the reference.

- [ ] **Step 7: Run the new stub tests + full validate**

```bash
uv run pytest stubs/tests/ -v
just validate
```
Expected: tests pass; validation green.

- [ ] **Step 8: Commit (split into per-stub commits if manageable)**

```bash
git add stubs/_sdk_base/ stubs/tts_local_stub/ stubs/tts_volume_stub/ stubs/tts_runpod_stub/ stubs/tts_grpc_stub/ stubs/asr_local_stub/ stubs/translation_local_stub/ stubs/translation_runpod_stub/
git rm stubs/worker_stub.py stubs/grpc_worker_stub.py stubs/translation_stub.py
git add stubs/tests/ Dockerfile docker-compose.yml pyproject.toml
git commit -m "feat(stubs): replace 4 legacy stubs with SDK-backed 7-stub matrix"
```

---

### Task 9: `.github/workflows/build-workers.yml` — GHCR CI

**Files:**
- Create: `.github/workflows/build-workers.yml`

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/build-workers.yml`:

```yaml
name: Build and publish worker images

on:
  push:
    branches: [main]
    tags: ['v*']
  pull_request:
    paths:
      - 'workers/**'
      - 'src/acheron/worker_sdk/**'
      - 'src/acheron/core/models.py'
      - 'proto/**'
      - 'Dockerfile.edge'
      - '.github/workflows/build-workers.yml'

jobs:
  build-qwen3tts:
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
          file: workers/qwen3tts/Dockerfile.runpod
          push: ${{ github.event_name != 'pull_request' }}
          tags: |
            ghcr.io/${{ github.repository }}/acheron-qwen3tts-runpod:latest
            ghcr.io/${{ github.repository }}/acheron-qwen3tts-runpod:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

  build-edge:
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
          file: Dockerfile.edge
          push: ${{ github.event_name != 'pull_request' }}
          tags: |
            ghcr.io/${{ github.repository }}/acheron-worker-edge:latest
            ghcr.io/${{ github.repository }}/acheron-worker-edge:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

Notes:
- Two jobs publish independently. Matrix can grow with `8b` (whisperv3large) and `8c` (translategemma).
- On PR, jobs build (cache warming for the slow torch/qwen-tts layer) but do not push.
- `cache-from/to: type=gha` caches the heavy layers across runs.
- `secrets.GITHUB_TOKEN` is provided by GitHub Actions by default.

- [ ] **Step 2: Lint the workflow**

```bash
# Optionally use actionlint if installed:
actionlint .github/workflows/build-workers.yml || echo "actionlint not installed; skipping"
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/build-workers.yml
git commit -m "ci(workers): publish acheron-qwen3tts-runpod + acheron-worker-edge to GHCR"
```

---

### Task 10: Final-gate `just validate`

- [ ] **Step 1: Run full validation**

```bash
uv sync --all-extras --all-packages
just validate
```
Expected: `lint-strict`, `lint-imports`, `type-check`, `type-check-pyright`, `test` all pass; coverage ≥ 80%.

- [ ] **Step 2: Smoke the local build once more**

```bash
just build-worker qwen3tts
just build-edge
```
Expected: both Docker builds succeed.

- [ ] **Step 3: Final commit if anything was polished**

```bash
git add -A
git commit -m "chore(layer8a): final validate polish" --allow-empty
```

(If no changes, skip.)

---

## Spec Coverage Map

- `workers/qwen3tts/` package (`Qwen3TTSRunpodHandler`, `runpod_entrypoint.py`, `worker.yaml`, `Dockerfile.runpod`, README, tests) — Tasks 1-5.
- 7-stub SDK matrix replacing the 4 legacy stubs — Task 8.
- `acheron-worker-edge` generic Dockerfile — Task 6.
- `docker-compose.yml` `qwen3tts-edge` service under `runpod-tts` profile — Task 7.
- `workers.* -/-> acheron.shell` import-linter contract — Task 1.
- uv workspace declaration for `workers/qwen3tts` — Task 1.
- GHCR CI workflow publishing both images on tag + main — Task 9.
- Justfile `build-worker` + `build-edge` targets — Task 5.
- `.env.example` RunPod worker vars — already added during spec write (committed with the design doc).

**Final post-condition:** all three Layer 8a plans (foundation → transports → worker+stubs+deploy) merged.orchestrator + dashboard + stubs run on `docker compose up`; with `--profile runpod-tts up` and RunPod credentials, the qwen3tts edge registers and forwards to the serverless endpoint. The first GHCR-built image is available at `ghcr.io/<owner>/acheron-qwen3tts-runpod:<sha>` for the RunPod template.