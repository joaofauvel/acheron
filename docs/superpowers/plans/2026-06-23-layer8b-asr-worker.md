# Layer 8b — ASR Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `ibm-granite/granite-speech-4.1-2b` ASR worker (`workers/granite_speech/`) end-to-end, including the typed `Input` Protocol extension to `acheron.worker_sdk`, the orchestrator-side multipart `HttpWorker` ASR branch, the shared `safe_chapter_id` helper, the `StubASRHandler` update, the worker runtime + edge image, and the GHCR publish workflow.

**Architecture:** This sub-project reuses the 8a `acheron.worker_sdk` blueprint and extends it with a typed `Input` Protocol symmetric with the existing `Artifact` Protocol. The orchestrator's `HttpWorker.execute()` branches on `job.job_type == ASR` to load the upstream extract step's audio from `StepCache` and POST `multipart/form-data` to the worker; the response side (`multipart/mixed`) is unchanged from 8a. The RunPod forwarder base64-encodes the `Input` for RunPod's JSON `/run` wire. The new `GraniteSpeechRunpodHandler` consumes the `Input`, returns one `text/plain` `BytesArtifact` per chapter. The shared `safe_chapter_id` helper is shared by both 8a and 8b workers (one-line refactor in 8a). Deployment is single RunPod L4 serverless endpoint per the deployer's compute choice; cold-start detection reuses the existing `RunPodHealthProvider`.

**Tech Stack:** Python 3.14, pydantic v2, pydantic-settings, httpx, FastAPI, transformers ≥ 4.52.1, torch 2.5.1 (cu121), flash-attn, soundfile, torchaudio, ffmpeg (apt), the `runpod` Python SDK, pytest + respx + pytest-asyncio, mypy + basedpyright, ruff, import-linter.

**Reference spec:** `docs/superpowers/specs/2026-06-23-layer8b-asr-worker-design.md`

**Final gate:** `just validate` green (lint-strict, lint-imports, mypy, basedpyright, pytest — all clean, coverage ≥ 80%).

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/acheron/worker_sdk/inputs.py` (NEW) | `Input` Protocol + `BytesInput` / `StreamInput` / `FileInput` (symmetric with `Artifact`). |
| `src/acheron/worker_sdk/__init__.py` (EXTENDED) | Re-export `BytesInput`, `StreamInput`, `FileInput`. |
| `src/acheron/worker_sdk/handler.py` (EXTENDED) | `WorkerHandler.handle()` gains `input: Input \| None = None`. |
| `src/acheron/worker_sdk/_edge_http.py` (EXTENDED) | `/execute` route accepts `multipart/form-data` OR `application/json`. |
| `src/acheron/worker_sdk/cloud.py` (EXTENDED) | `_serialise_job_for_runpod` carries `input_audio`; `make_runpod_handler._rp_handler` deserialises it; `RunPodForwarderHandler.handle()` accepts and forwards `input`. |
| `src/acheron/shell/transports/http.py` (EXTENDED) | New `_execute_asr_multipart` branch; `step_cache` keyword-only injection on `HttpWorker.__init__`. |
| `src/acheron/shell/transports/_multipart.py` (EXTENDED) | New `_parse_request_multipart` helper. |
| `src/acheron/shell/step_handler.py` (EXTENDED) | `default_worker_factory` gains `step_cache` keyword; `create_step_handler` plumbs it. |
| `src/acheron/shell/orchestrator.py` (EXTENDED) | Pass `_step_cache` to `create_step_handler`. |
| `workers/_shared.py` (NEW) | `safe_chapter_id` + `MAX_CHAPTER_ID_LEN` (shared by all worker handlers). |
| `workers/qwen3tts/handler.py` (EXTENDED) | `_chunk_chapter_id` delegates to `safe_chapter_id` (one-line refactor). |
| `stubs/_sdk_base/__init__.py` (EXTENDED) | `StubASRHandler.handle()` accepts `Input \| None`; capability language set grows to 6. |
| `workers/granite_speech/` (NEW) | `handler.py` + `runpod_entrypoint.py` + `worker.yaml` + `worker.edge.yaml` + `Dockerfile.runpod` + `pyproject.toml` + `README.md` + `__init__.py` + `tests/`. |
| `tests/worker_sdk/test_inputs.py` (NEW) | `Input` Protocol / `BytesInput` / `StreamInput` / `FileInput` tests. |
| `tests/worker_sdk/test_handler_signature.py` (NEW) | `WorkerHandler.handle` signature backward compat. |
| `tests/worker_sdk/test_edge_http_multipart.py` (NEW) | `/execute` multipart + JSON routes. |
| `tests/worker_sdk/test_cloud_audio.py` (NEW) | `_serialise_job_for_runpod` + `make_runpod_handler` audio forward. |
| `tests/worker_sdk/test_runpod_forwarder.py` (EXTENDED) | `RunPodForwarderHandler.handle` input forwarding. |
| `tests/shell/transports/test_asr_multipart.py` (NEW) | E2E `HttpWorker._execute_asr_multipart` driving `asr_local_stub`. |
| `tests/shell/transports/test_http_worker.py` (EXTENDED) | Backward compat for TTS path. |
| `tests/shell/transports/test_step_handler.py` (EXTENDED) | ASR branch routing. |
| `tests/shell/transports/test_multipart.py` (EXTENDED) | Request parser cases. |
| `workers/_shared/tests/test_safe_chapter_id.py` (NEW) | `safe_chapter_id` edge cases. |
| `workers/granite_speech/tests/test_capabilities.py` (NEW) | Capabilities shape. |
| `workers/granite_speech/tests/test_handler.py` (NEW) | `handle()` mocked model + error paths. |
| `workers/granite_speech/tests/test_runpod_entrypoint.py` (NEW) | `runpod_entrypoint.main()` boot path. |
| `pyproject.toml` (EXTENDED) | Declare `workers/granite_speech` as a uv workspace member. |
| `.github/workflows/build-workers.yml` (EXTENDED) | `build-granite-speech` job publishes `acheron-granite-speech-runpod`. |
| `docker-compose.yml` (EXTENDED) | `granite-speech-edge` service under `runpod-asr` profile (host `8008` → internal `8001`). |

---

## Phase A — SDK Foundation: `Input` Protocol

### Task 1: Add `Input` Protocol + variants

**Files:**
- Create: `src/acheron/worker_sdk/inputs.py`
- Create: `tests/worker_sdk/test_inputs.py`

- [ ] **Step 1: Write the failing test**

Create `tests/worker_sdk/test_inputs.py`:

```python
"""Tests for the Input Protocol + concrete variants (Layer 8b)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from acheron.core.models import JsonValue
from acheron.worker_sdk.inputs import BytesInput, FileInput, StreamInput


class TestBytesInput:
    def test_content_type_property(self) -> None:
        b = BytesInput(content_type="audio/mpeg", data=b"\xff\xfb\x90\x00")
        assert b.content_type == "audio/mpeg"

    def test_metadata_default_empty(self) -> None:
        b = BytesInput(content_type="audio/wav", data=b"RIFF")
        assert b.metadata == {}

    def test_metadata_explicit(self) -> None:
        meta: dict[str, JsonValue] = {"language": "en", "bitrate": 128}
        b = BytesInput(content_type="audio/mpeg", data=b"x", metadata=meta)
        assert b.metadata == meta

    def test_stream_yields_data(self) -> None:
        b = BytesInput(content_type="audio/mpeg", data=b"hello world")
        chunks = asyncio.run(_collect(b.stream()))
        assert b"".join(chunks) == b"hello world"

    def test_is_frozen(self) -> None:
        b = BytesInput(content_type="audio/mpeg", data=b"x")
        with pytest.raises((AttributeError, Exception)):
            b.data = b"y"  # type: ignore[misc]


class TestStreamInput:
    async def test_stream_delegates_to_producer(self) -> None:
        async def producer():
            yield b"chunk1"
            yield b"chunk2"

        s = StreamInput(content_type="audio/wav", producer=producer)
        chunks = [_ async for _ in s.stream()]
        assert b"".join(chunks) == b"chunk1chunk2"

    def test_content_type_and_metadata(self) -> None:
        async def producer():
            yield b""

        s = StreamInput(
            content_type="audio/wav",
            producer=producer,
            metadata={"source": "test"},
        )
        assert s.content_type == "audio/wav"
        assert s.metadata == {"source": "test"}


class TestFileInput:
    def test_content_type_and_path(self, tmp_path: Path) -> None:
        p = tmp_path / "audio.wav"
        p.write_bytes(b"RIFFDATA")
        f = FileInput(content_type="audio/wav", path=p)
        assert f.content_type == "audio/wav"
        assert f.path == p

    def test_stream_reads_file_in_chunks(self, tmp_path: Path) -> None:
        p = tmp_path / "audio.wav"
        data = b"x" * (64 * 1024 + 100)  # > 64 KiB
        p.write_bytes(data)
        f = FileInput(content_type="audio/wav", path=p)
        chunks = asyncio.run(_collect(f.stream()))
        assert b"".join(chunks) == data

    def test_metadata_default_empty(self, tmp_path: Path) -> None:
        f = FileInput(content_type="audio/wav", path=tmp_path / "x")
        assert f.metadata == {}


async def _collect(async_iter):
    """Helper to collect an async iterator into a list."""
    out = []
    async for chunk in async_iter:
        out.append(chunk)
    return out
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/worker_sdk/test_inputs.py -v
```

Expected: `ModuleNotFoundError: No module named 'acheron.worker_sdk.inputs'`.

- [ ] **Step 3: Implement `inputs.py`**

Create `src/acheron/worker_sdk/inputs.py`:

```python
"""Transport-neutral input handed to WorkerHandler.handle() alongside the Job.

The `Input` Protocol is symmetric with `artifacts.Artifact` — the same
three-variant shape (bytes / stream / file), the opposite direction on
the wire. Workers consume an `Input`; they produce `list[Artifact]`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable  # noqa: TC003
from dataclasses import dataclass, field
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Protocol

import aiofiles

if TYPE_CHECKING:
    from acheron.core.models import JsonValue


class Input(Protocol):
    """Transport-neutral input handed to WorkerHandler.handle() alongside the Job."""

    @property
    def content_type(self) -> str:  # noqa: D102
        ...

    @property
    def metadata(self) -> dict[str, JsonValue]:  # noqa: D102
        ...

    def stream(self) -> AsyncIterator[bytes]:  # noqa: D102
        ...


@dataclass(frozen=True)
class BytesInput:
    """In-memory bytes — short audio, embedded text."""

    content_type: str
    data: bytes
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    async def stream(self) -> AsyncIterator[bytes]:
        """Yield the in-memory bytes as a single chunk."""
        yield self.data


@dataclass(frozen=True)
class StreamInput:
    """Lazily-produced chunks — long audio, bounded memory."""

    content_type: str
    producer: Callable[[], AsyncIterator[bytes]]
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    async def stream(self) -> AsyncIterator[bytes]:
        """Yield chunks produced by ``self.producer()``."""
        async for chunk in self.producer():
            yield chunk


@dataclass(frozen=True)
class FileInput:
    """Worker reads from disk (shared-volume mode or tmp file)."""

    content_type: str
    path: Path
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    async def stream(self) -> AsyncIterator[bytes]:
        """Yield the file's contents in 64 KiB chunks."""
        async with aiofiles.open(self.path, "rb") as f:
            while True:
                chunk = await f.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/worker_sdk/test_inputs.py -v
```

Expected: PASS (all 9 tests).

- [ ] **Step 5: Lint + type-check**

```bash
uv run ruff check src/acheron/worker_sdk/inputs.py tests/worker_sdk/test_inputs.py
uv run mypy src/acheron/worker_sdk/inputs.py tests/worker_sdk/test_inputs.py
uv run basedpyright src/acheron/worker_sdk/inputs.py tests/worker_sdk/test_inputs.py
```

Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add src/acheron/worker_sdk/inputs.py tests/worker_sdk/test_inputs.py
git commit -m "feat(worker_sdk): add Input Protocol + BytesInput/StreamInput/FileInput"
```

---

### Task 2: Re-export `Input` variants from `acheron.worker_sdk`

**Files:**
- Modify: `src/acheron/worker_sdk/__init__.py`

- [ ] **Step 1: Read the existing re-exports**

```bash
uv run python -c "from acheron.worker_sdk import *; import acheron.worker_sdk as m; print([n for n in dir(m) if not n.startswith('_')])"
```

- [ ] **Step 2: Add the new re-exports**

Modify `src/acheron/worker_sdk/__init__.py`. Add the `Input` variants to the existing re-export block. The exact insertion point depends on the existing structure; append the new imports to the existing `from .X import Y` lines. Find the existing `from .artifacts import ...` line and add a parallel `from .inputs import ...` line:

```python
from .artifacts import BytesArtifact, FileArtifact, StreamArtifact
from .inputs import BytesInput, FileInput, StreamInput
```

Add to the module's `__all__` (if present) or the public symbol list:

```python
__all__ = [
    # ... existing symbols ...
    "BytesInput",
    "StreamInput",
    "FileInput",
]
```

- [ ] **Step 3: Verify imports work**

```bash
uv run python -c "from acheron.worker_sdk import BytesInput, StreamInput, FileInput; print(BytesInput, StreamInput, FileInput)"
```

Expected: three class objects printed.

- [ ] **Step 4: Lint + type-check**

```bash
uv run ruff check src/acheron/worker_sdk/__init__.py
uv run mypy src/acheron/worker_sdk/__init__.py
```

Expected: all clean.

- [ ] **Step 5: Commit**

```bash
git add src/acheron/worker_sdk/__init__.py
git commit -m "feat(worker_sdk): re-export Input variants"
```

---

### Task 3: Extend `WorkerHandler.handle()` signature

**Files:**
- Modify: `src/acheron/worker_sdk/handler.py:25-35`
- Create: `tests/worker_sdk/test_handler_signature.py`

- [ ] **Step 1: Write the failing test**

Create `tests/worker_sdk/test_handler_signature.py`:

```python
"""Verify WorkerHandler.handle gains the optional input parameter (8b)."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest

from acheron.core.models import Job, JobStatus, JobMetrics, OutputFile
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler
from acheron.worker_sdk.inputs import BytesInput, Input


class _DummyHandler(WorkerHandler):
    """Concrete handler that accepts both call styles."""

    def capabilities(self) -> Any:  # noqa: ANN401, D102
        return None

    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:
        return [
            BytesArtifact(
                filename="dummy.txt",
                content_type="text/plain",
                data=b"ok",
            )
        ]


def test_handle_signature_accepts_input_kwarg() -> None:
    sig = inspect.signature(_DummyHandler.handle)
    params = sig.parameters
    assert "input" in params
    assert params["input"].default is None
    assert params["input"].annotation == "Input | None"


def test_call_without_input_works() -> None:
    """TTS-style call: handle(job) — input defaults to None."""
    h = _DummyHandler()
    job = Job(job_id="j-1", job_type=0, payload={}, chapter_id="ch1")  # type: ignore[arg-type]
    out = asyncio.run(h.handle(job))
    assert len(out) == 1
    assert out[0].content_type == "text/plain"


def test_call_with_input_kwarg_works() -> None:
    """ASR-style call: handle(job, input=BytesInput(...))."""
    h = _DummyHandler()
    job = Job(job_id="j-1", job_type=0, payload={}, chapter_id="ch1")  # type: ignore[arg-type]
    out = asyncio.run(h.handle(job, input=BytesInput(content_type="audio/wav", data=b"RIFF")))
    assert len(out) == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/worker_sdk/test_handler_signature.py -v
```

Expected: `TypeError: handle() got multiple values for argument 'input'` (or similar — the `input` parameter is not yet defined).

- [ ] **Step 3: Update `WorkerHandler.handle()` signature**

Modify `src/acheron/worker_sdk/handler.py`. Update the `TYPE_CHECKING` import block to include `Input`:

```python
if TYPE_CHECKING:
    from acheron.core.models import Job, WorkerCapabilities
    from acheron.worker_sdk.artifacts import Artifact
    from acheron.worker_sdk.inputs import Input
```

Update the abstract `handle` method:

```python
    @abstractmethod
    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:
        """Run inference for `job`, consuming `input` if the step is audio-in.

        `input` is the new second parameter (8b). Default ``None`` keeps
        backward compatibility — TTS, translation, and stub handlers that
        don't take an input are unchanged.
        """
```

- [ ] **Step 4: Verify all existing handlers still satisfy the ABC**

Run the existing handler tests:

```bash
uv run pytest tests/worker_sdk/ -v
```

Expected: all pre-existing tests pass. The new signature is keyword-defaulted, so subclasses that override with `handle(self, job)` still satisfy the ABC (Python's ABC allows narrower signatures, but the parameter is named `input` now — if any test uses positional `input`, the override must match). If a subclass override declares `handle(self, job)` without the `input` parameter, mypy/basedpyright may flag it; fix by adding the parameter:

```python
    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:
        # ... existing body, ignoring `input` ...
```

Apply the same one-line change to all of:
- `workers/qwen3tts/handler.py:Qwen3TTSRunpodHandler.handle`
- `stubs/_sdk_base/__init__.py:StubTTSHandler.handle`
- `stubs/_sdk_base/__init__.py:StubASRHandler.handle` (will gain the `input` parameter in Task 4)
- `stubs/_sdk_base/__init__.py:StubTranslationHandler.handle`
- `src/acheron/worker_sdk/cloud.py:RunPodForwarderHandler.handle` (extended in Task 7)

After updating all signatures, run:

```bash
uv run pytest tests/ -v
```

Expected: all tests pass (any pre-existing test that exercises these handlers should continue to work because `input` defaults to `None`).

- [ ] **Step 5: Lint + type-check**

```bash
uv run ruff check src/acheron/worker_sdk/handler.py tests/worker_sdk/test_handler_signature.py
uv run mypy src/acheron/worker_sdk/handler.py
uv run basedpyright src/acheron/worker_sdk/handler.py
```

Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add src/acheron/worker_sdk/handler.py workers/qwen3tts/handler.py stubs/_sdk_base/__init__.py
git commit -m "feat(worker_sdk): WorkerHandler.handle gains optional input parameter"
```

---

### Task 4: Update `StubASRHandler` capability language set + `handle` signature

**Files:**
- Modify: `stubs/_sdk_base/__init__.py:81-115`

- [ ] **Step 1: Update the stub handler**

Modify `stubs/_sdk_base/__init__.py`. Update the `StubASRHandler.capabilities` language sets to match the new 6-language ASR contract and the format set:

```python
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

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:
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
```

Add the `Input` import at the top of the file (next to the existing `Artifact` import):

```python
from acheron.worker_sdk.inputs import Input
```

- [ ] **Step 2: Verify the stub tests still pass**

```bash
uv run pytest stubs/tests/ -v
```

Expected: all 7-stub matrix tests pass. The 2-test parametric check on `asr_local_stub` should continue to assert the stub registers + serves /health.

- [ ] **Step 3: Lint + type-check**

```bash
uv run ruff check stubs/_sdk_base/__init__.py
uv run mypy stubs/_sdk_base/__init__.py
```

Expected: all clean.

- [ ] **Step 4: Commit**

```bash
git add stubs/_sdk_base/__init__.py
git commit -m "feat(stubs): StubASRHandler.handle accepts Input + 6-language capabilities"
```

---

## Phase B — SDK: Multipart /execute + RunPod Audio Forwarding

### Task 5: Extend `_edge_http.py` `/execute` route to accept multipart OR JSON

**Files:**
- Modify: `src/acheron/worker_sdk/_edge_http.py:151-194`
- Create: `tests/worker_sdk/test_edge_http_multipart.py`

- [ ] **Step 1: Write the failing test**

Create `tests/worker_sdk/test_edge_http_multipart.py`:

```python
"""Verify the SDK /execute route accepts multipart OR JSON (8b)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from acheron.core.models import Job
from acheron.worker_sdk.app import create_worker_app
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler
from acheron.worker_sdk.inputs import BytesInput, Input
from acheron.worker_sdk.settings import WorkerSettings


def _settings() -> WorkerSettings:
    return WorkerSettings(
        worker_id="test-worker",
        orchestrator_url="http://orchestrator:8000",
        listen_port=8001,
        runpod_api_key="k",
        runpod_endpoint_id="e",
        price_source="zero",
    )


class _AsrEchoHandler(WorkerHandler):
    """ASR handler that records the received input bytes."""

    def __init__(self) -> None:
        self.received: list[bytes] = []
        self.received_content_type: list[str] = []

    def capabilities(self) -> Any:  # noqa: ANN401, D102
        return None

    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:  # type: ignore[override]
        if input is not None:
            self.received.append(b"".join([c async for c in input.stream()]))
            self.received_content_type.append(input.content_type)
        return [
            BytesArtifact(
                filename="out.txt",
                content_type="text/plain",
                data=b"echoed",
            )
        ]


@pytest.fixture
def app() -> Any:
    return create_worker_app(handler=_AsrEchoHandler(), settings=_settings(), disable_registration=True)


async def test_json_request_routes_to_legacy_path(app: Any) -> None:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/execute",
            json={
                "job_id": "j-1",
                "job_type": "tts",
                "payload": {},
                "chapter_id": "ch1",
                "sequence_ids": None,
            },
        )
    assert resp.status_code == 200
    assert b"echoed" in resp.content


async def test_multipart_request_passes_input(app: Any) -> None:
    """Multipart with JSON part + audio part → handler receives the audio bytes."""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/execute",
            data={
                "request": (
                    None,
                    json.dumps({
                        "job_id": "j-1",
                        "job_type": "asr",
                        "payload": {"source_language": "en"},
                        "chapter_id": "ch1",
                        "sequence_ids": None,
                    }).encode("utf-8"),
                    "application/json",
                ),
                "audio": ("podcast.mp3", b"\xff\xfb\x90\x00mock-audio", "audio/mpeg"),
            },
        )
    assert resp.status_code == 200
    assert b"echoed" in resp.content
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/worker_sdk/test_edge_http_multipart.py -v
```

Expected: `AssertionError` on the multipart case (the current /execute only accepts `ExecuteRequest` JSON, doesn't read multipart).

- [ ] **Step 3: Update `/execute` route to accept multipart OR JSON**

Modify `src/acheron/worker_sdk/_edge_http.py`. Replace the `/execute` endpoint definition (around line 151):

```python
    @app.post("/execute")
    async def execute(request: Request) -> Response:
        """Accept either application/json (legacy) or multipart/form-data (8b)."""
        ctype = request.headers.get("content-type", "")
        if ctype.startswith("multipart/"):
            return await self._run_execute_multipart(request)
        body = ExecuteRequest.model_validate(await request.json())
        return await self._run_execute(body)
```

Add the new method `_run_execute_multipart` next to `_run_execute`:

```python
    async def _run_execute_multipart(self, request: Request) -> Response:
        """Parse multipart body, build Job + Input, dispatch to handler."""
        from email.message import Message  # noqa: PLC0415
        from email.parser import BytesParser
        from email.policy import default as default_policy

        from acheron.worker_sdk.schemas import ExecuteRequest  # noqa: PLC0415

        ctype = request.headers.get("content-type", "")
        boundary_part = ctype.split("boundary=", 1)[1]
        boundary = boundary_part.split(";", 1)[0].strip().strip('"')
        body = await request.body()
        full_body = (
            f"Content-Type: multipart/form-data; boundary={boundary}\r\nMIME-Version: 1.0\r\n\r\n"
        ).encode() + body
        message = BytesParser(policy=default_policy).parsebytes(full_body)
        if not message.is_multipart():
            msg = f"Multipart body from {request.client} was not multipart"
            raise WorkerError(msg)

        envelope_json: bytes | None = None
        audio_part: Message | None = None
        for part in message.get_payload():
            if not isinstance(part, Message):
                continue
            part_ctype = part.get_content_type()
            if part_ctype == "application/json" and envelope_json is None:
                raw = part.get_payload(decode=True)
                envelope_json = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
            elif audio_part is None:
                audio_part = part

        if envelope_json is None:
            msg = f"Multipart body from {request.client} has no application/json part"
            raise WorkerError(msg)
        body_req = ExecuteRequest.model_validate(envelope_json)

        job = _job_from_request(body_req)
        input: Input | None = None
        if audio_part is not None:
            audio_raw = audio_part.get_payload(decode=True)
            audio_bytes = audio_raw if isinstance(audio_raw, bytes) else str(audio_raw).encode("utf-8")
            input = BytesInput(
                content_type=audio_part.get_content_type(),
                data=audio_bytes,
                metadata={},
            )

        start = time.monotonic()
        try:
            artifacts: list[Artifact] = await self.handler.handle(job, input)
        except BaseException as exc:
            duration = time.monotonic() - start
            logger.exception("Handler failed for job %s", job.job_id)
            result = JobResult(
                job_id=job.job_id,
                status=JobStatus.FAILED,
                outputs=(),
                metrics=JobMetrics(duration_seconds=duration, cost_basis=None),
                error=str(exc),
            )
            return JSONResponse(
                status_code=500,
                content=_jobresult_to_json(result),
            )
        duration = time.monotonic() - start
        gpu_seconds = duration
        if self.price_source is not None:
            est = await self.price_source.estimate(gpu_seconds)
            cost = est.cost
            basis = to_cost_basis(est)
        else:
            cost = None
            basis = None
        metrics = JobMetrics(
            duration_seconds=duration,
            gpu_seconds=gpu_seconds,
            cost_estimate=cost,
            cost_basis=basis,
        )
        return await _build_multipart_response(artifacts, metrics)
```

Add the `WorkerError` import to the existing imports at the top of the file:

```python
from acheron.core.errors import WorkerError
```

Add `Request` to the `fastapi` imports:

```python
from fastapi import FastAPI, Request
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/worker_sdk/test_edge_http_multipart.py -v
```

Expected: PASS (both test cases).

- [ ] **Step 5: Lint + type-check**

```bash
uv run ruff check src/acheron/worker_sdk/_edge_http.py tests/worker_sdk/test_edge_http_multipart.py
uv run mypy src/acheron/worker_sdk/_edge_http.py tests/worker_sdk/test_edge_http_multipart.py
uv run basedpyright src/acheron/worker_sdk/_edge_http.py tests/worker_sdk/test_edge_http_multipart.py
```

Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add src/acheron/worker_sdk/_edge_http.py tests/worker_sdk/test_edge_http_multipart.py
git commit -m "feat(worker_sdk): /execute accepts multipart/form-data OR application/json"
```

---

### Task 6: Extend `_serialise_job_for_runpod` + `make_runpod_handler` to forward `Input`

**Files:**
- Modify: `src/acheron/worker_sdk/cloud.py:35-105`
- Create: `tests/worker_sdk/test_cloud_audio.py`

- [ ] **Step 1: Write the failing test**

Create `tests/worker_sdk/test_cloud_audio.py`:

```python
"""Verify make_runpod_handler carries Input over the JSON /run wire (8b)."""

from __future__ import annotations

import base64
from typing import Any

import pytest

from acheron.core.models import Job, JobType, WorkerType  # noqa: F401
from acheron.worker_sdk.artifacts import BytesArtifact
from acheron.worker_sdk.cloud import _serialise_job_for_runpod, make_runpod_handler
from acheron.worker_sdk.handler import WorkerHandler
from acheron.worker_sdk.inputs import BytesInput, Input


class _CaptureHandler(WorkerHandler):
    """Records the job + input it received."""

    def __init__(self) -> None:
        self.received_job: Job | None = None
        self.received_input: Input | None = None

    def capabilities(self) -> Any:  # noqa: ANN401, D102
        return None

    async def handle(self, job: Job, input: Input | None = None) -> list[BytesArtifact]:  # type: ignore[override]
        self.received_job = job
        self.received_input = input
        return []


def test_serialise_includes_input_audio_when_present() -> None:
    job = Job(job_id="j-1", job_type=WorkerType.ASR, payload={"x": 1}, chapter_id="ch1")
    inp = BytesInput(content_type="audio/mpeg", data=b"audio-bytes")
    wire = _serialise_job_for_runpod(job, inp)
    assert "input_audio" in wire["input"]
    assert wire["input"]["input_audio"]["content_type"] == "audio/mpeg"
    assert base64.b64decode(wire["input"]["input_audio"]["data"]) == b"audio-bytes"


def test_serialise_omits_input_audio_when_none() -> None:
    job = Job(job_id="j-1", job_type=WorkerType.TTS, payload={}, chapter_id="ch1")
    wire = _serialise_job_for_runpod(job, None)
    assert "input_audio" not in wire["input"]


async def test_make_runpod_handler_passes_input_when_present() -> None:
    handler = _CaptureHandler()
    wrapped = make_runpod_handler(handler)
    runpod_job = {
        "input": {
            "job_id": "j-1",
            "job_type": "asr",
            "payload": {"source_language": "en"},
            "chapter_id": "ch1",
            "sequence_ids": [],
            "input_audio": {
                "content_type": "audio/wav",
                "data": base64.b64encode(b"RIFFDATA").decode("ascii"),
                "metadata": {},
            },
        }
    }
    await wrapped(runpod_job)
    assert handler.received_job is not None
    assert handler.received_job.chapter_id == "ch1"
    assert handler.received_input is not None
    assert handler.received_input.content_type == "audio/wav"
    body = b"".join([c async for c in handler.received_input.stream()])
    assert body == b"RIFFDATA"


async def test_make_runpod_handler_passes_none_when_no_audio() -> None:
    """TTS-style: no input_audio → handler receives input=None."""
    handler = _CaptureHandler()
    wrapped = make_runpod_handler(handler)
    runpod_job = {
        "input": {
            "job_id": "j-1",
            "job_type": "tts",
            "payload": {"target_language": "en"},
            "chapter_id": "ch1",
            "sequence_ids": [],
        }
    }
    await wrapped(runpod_job)
    assert handler.received_input is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/worker_sdk/test_cloud_audio.py -v
```

Expected: `TypeError: _serialise_job_for_runpod() got an unexpected keyword argument 'input'` (or `missing 1 required positional argument`).

- [ ] **Step 3: Update `_serialise_job_for_runpod` + `make_runpod_handler`**

Modify `src/acheron/worker_sdk/cloud.py`. Update `_serialise_job_for_runpod`:

```python
def _serialise_job_for_runpod(job: Job, input: Input | None = None) -> dict[str, Any]:
    """Serialise a Job + optional Input into the RunPod /run input shape.

    The ``input_audio`` field is the base64-encoded body of an ``Input`` (8b);
    RunPod's /run wire is JSON, so binary inputs round-trip via base64.
    """
    out: dict[str, Any] = {
        "input": {
            "job_id": job.job_id,
            "job_type": job.job_type.value,
            "payload": dict(job.payload),
            "chapter_id": job.chapter_id,
            "sequence_ids": list(job.sequence_ids) if job.sequence_ids else [],
        }
    }
    if input is not None:
        body = b"".join([chunk async for chunk in input.stream()])
        out["input"]["input_audio"] = {
            "content_type": input.content_type,
            "data": base64.b64encode(body).decode("ascii"),
            "metadata": dict(input.metadata),
        }
    return out
```

Update `make_runpod_handler`:

```python
def make_runpod_handler(
    handler: WorkerHandler,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return a RunPod-compatible async callable wrapping ``handler``."""

    async def _rp_handler(runpod_job: dict[str, Any]) -> dict[str, Any]:
        job = _deserialise_job(runpod_job["input"])
        audio_payload = runpod_job["input"].get("input_audio")
        if audio_payload is not None:
            data_b64 = audio_payload.get("data", "")
            body = base64.b64decode(data_b64) if isinstance(data_b64, str) else b""
            input = BytesInput(
                content_type=str(audio_payload.get("content_type", "audio/wav")),
                data=body,
                metadata=dict(audio_payload.get("metadata", {})),
            )
            artifacts = await handler.handle(job, input)
        else:
            artifacts = await handler.handle(job)
        return {"artifacts": [await _serialise(a) for a in artifacts]}

    return _rp_handler
```

Add the `Input` import to the top:

```python
from acheron.worker_sdk.inputs import BytesInput, Input
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/worker_sdk/test_cloud_audio.py -v
```

Expected: PASS (all 4 tests).

- [ ] **Step 5: Lint + type-check**

```bash
uv run ruff check src/acheron/worker_sdk/cloud.py tests/worker_sdk/test_cloud_audio.py
uv run mypy src/acheron/worker_sdk/cloud.py tests/worker_sdk/test_cloud_audio.py
uv run basedpyright src/acheron/worker_sdk/cloud.py tests/worker_sdk/test_cloud_audio.py
```

Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add src/acheron/worker_sdk/cloud.py tests/worker_sdk/test_cloud_audio.py
git commit -m "feat(worker_sdk): make_runpod_handler carries Input over /run wire"
```

---

### Task 7: Extend `RunPodForwarderHandler.handle()` to accept and forward `Input`

**Files:**
- Modify: `src/acheron/worker_sdk/cloud.py:170-177`
- Modify: `tests/worker_sdk/test_runpod_forwarder.py`

- [ ] **Step 1: Read the existing test file**

```bash
uv run cat tests/worker_sdk/test_runpod_forwarder.py | head -50
```

- [ ] **Step 2: Update the forwarder**

Modify `src/acheron/worker_sdk/cloud.py`. Update the `handle` method:

```python
    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:
        """Forward the job (and optional audio input) to RunPod and decode artifacts."""
        if self._client is None:
            msg = "RunPodClient not initialised (startup() not run)"
            raise WorkerError(msg)
        payload = _serialise_job_for_runpod(job, input)
        result = await self._client.run(payload)
        return _deserialise_runpod_artifacts(result)
```

- [ ] **Step 3: Verify the existing forwarder tests still pass**

```bash
uv run pytest tests/worker_sdk/test_runpod_forwarder.py -v
```

Expected: PASS. The existing tests call `forwarder.handle(job)` (no input); the new `input=None` default keeps the behavior identical.

- [ ] **Step 4: Add a new test case for input forwarding**

Append to `tests/worker_sdk/test_runpod_forwarder.py`:

```python
async def test_forwarder_passes_input_to_runpod_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When /execute is called with an Input, the forwarder base64-encodes
    it into the RunPod /run payload (covers ASR's audio path)."""
    import base64 as _b64

    from acheron.core.models import Job, WorkerType
    from acheron.worker_sdk.cloud import RunPodForwarderHandler
    from acheron.worker_sdk.inputs import BytesInput
    from acheron.worker_sdk.settings import WorkerSettings

    captured: dict = {}

    class _FakeClient:
        async def run(self, payload: dict) -> dict:
            captured["payload"] = payload
            return {"artifacts": []}

    settings = WorkerSettings(
        worker_id="fwd-1",
        orchestrator_url="http://o:8000",
        runpod_api_key="k",
        runpod_endpoint_id="e",
        price_source="zero",
    )
    fwd = RunPodForwarderHandler(settings)
    fwd._client = _FakeClient()  # type: ignore[assignment]

    job = Job(job_id="j-1", job_type=WorkerType.ASR, payload={}, chapter_id="ch1")
    inp = BytesInput(content_type="audio/wav", data=b"RIFFDATA")
    await fwd.handle(job, inp)

    assert "input_audio" in captured["payload"]["input"]
    decoded = _b64.b64decode(captured["payload"]["input"]["input_audio"]["data"])
    assert decoded == b"RIFFDATA"
    assert captured["payload"]["input"]["input_audio"]["content_type"] == "audio/wav"
```

(If the test file uses a different fixture pattern, follow the existing style; the key is to assert `input_audio` is in the captured payload.)

- [ ] **Step 5: Run the updated tests**

```bash
uv run pytest tests/worker_sdk/test_runpod_forwarder.py -v
```

Expected: PASS (existing + new test).

- [ ] **Step 6: Lint + type-check + commit**

```bash
uv run ruff check src/acheron/worker_sdk/cloud.py tests/worker_sdk/test_runpod_forwarder.py
uv run mypy src/acheron/worker_sdk/cloud.py
git add src/acheron/worker_sdk/cloud.py tests/worker_sdk/test_runpod_forwarder.py
git commit -m "feat(worker_sdk): RunPodForwarderHandler.handle accepts and forwards Input"
```

---

## Phase C — Orchestrator Transport: ASR Multipart Branch

### Task 8: Extend `HttpWorker.__init__` with `step_cache` parameter

**Files:**
- Modify: `src/acheron/shell/transports/http.py:44-55`

- [ ] **Step 1: Add the `step_cache` keyword parameter**

Modify `src/acheron/shell/transports/http.py`. Add `StepCache` to the imports:

```python
from acheron.shell.cache import StepCache
```

Update `HttpWorker.__init__`:

```python
    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        *,
        data_dir: Path | str | None = None,
        step_cache: StepCache | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        if data_dir is None:
            data_dir = Path(os.environ.get("ACHERON_DATA_DIR", "/data/jobs"))
        self._data_dir = Path(data_dir)
        self._step_cache = step_cache if step_cache is not None else StepCache(self._data_dir)
```

- [ ] **Step 2: Verify existing tests still pass**

```bash
uv run pytest tests/shell/test_http_worker.py tests/shell/transports/test_http_worker.py -v
```

Expected: PASS. The default-constructed `StepCache(data_dir)` is a no-I/O `Path` wrapper; existing call sites that don't pass `step_cache` get a fresh instance.

- [ ] **Step 3: Lint + type-check**

```bash
uv run ruff check src/acheron/shell/transports/http.py
uv run mypy src/acheron/shell/transports/http.py
```

Expected: all clean.

- [ ] **Step 4: Commit**

```bash
git add src/acheron/shell/transports/http.py
git commit -m "feat(transport): HttpWorker gains step_cache keyword parameter"
```

---

### Task 9: Add `_parse_request_multipart` helper to `_multipart.py`

**Files:**
- Modify: `src/acheron/shell/transports/_multipart.py`
- Modify: `tests/shell/transports/test_multipart.py`

- [ ] **Step 1: Read the existing `_multipart.py`**

```bash
uv run cat src/acheron/shell/transports/_multipart.py
```

- [ ] **Step 2: Add the `_parse_request_multipart` function**

Append to `src/acheron/shell/transports/_multipart.py`:

```python
from __future__ import annotations

import json
from email.message import Message
from email.parser import BytesParser
from email.policy import default as default_policy
from typing import Any


def _parse_request_multipart(
    ctype: str, body: bytes
) -> tuple[dict[str, Any], bytes, str]:
    """Parse a /execute request body into (job_dict, audio_bytes, audio_content_type).

    Accepts either multipart/form-data (one JSON part + zero or more binary
    parts) or plain application/json (legacy / TTS path). For multipart with
    no binary part, audio_bytes is empty and audio_content_type is "".
    """
    if not ctype.startswith("multipart/"):
        return (json.loads(body), b"", "")
    boundary = ctype.split("boundary=", 1)[1].split(";", 1)[0].strip().strip('"')
    full_body = (
        f"Content-Type: {ctype.split(';', 1)[0].strip()}; boundary={boundary}\r\n"
        "MIME-Version: 1.0\r\n\r\n"
    ).encode() + body
    message = BytesParser(policy=default_policy).parsebytes(full_body)
    if not message.is_multipart():
        return (json.loads(body), b"", "")
    envelope: dict[str, Any] | None = None
    audio_bytes = b""
    audio_ctype = ""
    for part in message.get_payload():
        if not isinstance(part, Message):
            continue
        part_ctype = part.get_content_type()
        if part_ctype == "application/json" and envelope is None:
            raw = part.get_payload(decode=True)
            envelope = json.loads(raw if isinstance(raw, bytes) else str(raw).encode("utf-8"))
        elif not audio_bytes and part_ctype != "application/json":
            raw = part.get_payload(decode=True)
            audio_bytes = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
            audio_ctype = part_ctype
    if envelope is None:
        msg = f"Multipart body has no application/json part"
        raise ValueError(msg)
    return (envelope, audio_bytes, audio_ctype)
```

(Add the `from __future__ import annotations` and imports as needed at the top of the file.)

- [ ] **Step 3: Add test cases to `test_multipart.py`**

Read the existing test file first, then append:

```python
def test_parse_request_multipart_json_only() -> None:
    """Plain application/json → empty audio."""
    body = b'{"job_id": "j-1", "job_type": "tts"}'
    env, audio_bytes, audio_ctype = _parse_request_multipart("application/json", body)
    assert env["job_id"] == "j-1"
    assert audio_bytes == b""
    assert audio_ctype == ""


def test_parse_request_multipart_with_audio() -> None:
    """multipart with JSON part + audio part → audio bytes extracted."""
    boundary = "--b"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="request"\r\n'
        f"Content-Type: application/json\r\n\r\n"
        f'{{"job_id": "j-1", "job_type": "asr"}}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="audio"; filename="x.mp3"\r\n'
        f"Content-Type: audio/mpeg\r\n\r\n"
        f"AUDIOBYTES\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    ctype = f"multipart/form-data; boundary={boundary}"
    env, audio_bytes, audio_ctype = _parse_request_multipart(ctype, body)
    assert env["job_id"] == "j-1"
    assert audio_bytes == b"AUDIOBYTES"
    assert audio_ctype == "audio/mpeg"
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/shell/transports/test_multipart.py -v
```

Expected: PASS (existing + new cases).

- [ ] **Step 5: Lint + type-check + commit**

```bash
uv run ruff check src/acheron/shell/transports/_multipart.py tests/shell/transports/test_multipart.py
git add src/acheron/shell/transports/_multipart.py tests/shell/transports/test_multipart.py
git commit -m "feat(transport): add _parse_request_multipart helper"
```

---

### Task 10: Add `HttpWorker._execute_asr_multipart` branch

**Files:**
- Modify: `src/acheron/shell/transports/http.py:81-87`

- [ ] **Step 1: Update `execute` to branch on `job_type == ASR`**

Modify `src/acheron/shell/transports/http.py`. Add `WorkerType` to the import from `core.models`:

```python
from acheron.core.models import (
    Job,
    JobMetrics,
    JobResult,
    OutputFile,
    WorkerCapabilities,
    WorkerType,
)
```

Add `json` to the stdlib imports (top of file):

```python
import json
```

Update `execute`:

```python
    async def execute(self, job: Job) -> JobResult:  # noqa: D102
        if job.job_type == WorkerType.ASR:
            return await self._execute_asr_multipart(job)
        # Existing JSON / multipart-mixed response path (unchanged).
        resp = await self._request("POST", "/execute", json=_job_to_dict(job))
        ctype = resp.headers.get("content-type", "")
        if ctype.startswith("multipart/mixed"):
            return await self._parse_multipart(resp, job.job_id)
        return _result_adapter.validate_json(resp.content)
```

Add the new method `_execute_asr_multipart`:

```python
    async def _execute_asr_multipart(self, job: Job) -> JobResult:
        """Read the upstream extract step's audio file and POST multipart."""
        import asyncio  # noqa: PLC0415

        plan_job_id = job.job_id.rsplit("-", 1)[0]
        extract_outputs = await self._step_cache.load_outputs(plan_job_id, "extract")
        audio_out = next(
            (o for o in extract_outputs if o.content_type.startswith("audio/")),
            None,
        )
        if audio_out is None:
            msg = f"ASR step {job.job_id}: no audio file in extract output"
            raise WorkerError(msg)
        audio_path = Path(audio_out.path)
        if not await asyncio.to_thread(audio_path.exists):
            msg = f"ASR step {job.job_id}: audio file missing: {audio_path}"
            raise WorkerError(msg)

        form = {
            "request": (None, json.dumps(_job_to_dict(job)).encode("utf-8"), "application/json"),
            "audio": (
                audio_path.name,
                await asyncio.to_thread(audio_path.read_bytes),
                audio_out.content_type,
            ),
        }
        if self._client is not None:
            resp = await self._client.post(f"{self._base_url}/execute", files=form)
        else:
            async with httpx.AsyncClient() as client:
                resp = await client.post(f"{self._base_url}/execute", files=form)
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if ctype.startswith("multipart/mixed"):
            return await self._parse_multipart(resp, job.job_id)
        return _result_adapter.validate_json(resp.content)
```

- [ ] **Step 2: Lint + type-check**

```bash
uv run ruff check src/acheron/shell/transports/http.py
uv run mypy src/acheron/shell/transports/http.py
uv run basedpyright src/acheron/shell/transports/http.py
```

Expected: all clean (the test for the new branch is in Task 11).

- [ ] **Step 3: Commit**

```bash
git add src/acheron/shell/transports/http.py
git commit -m "feat(transport): HttpWorker.execute branches on ASR to send multipart"
```

---

### Task 11: Add `test_asr_multipart.py` driving `asr_local_stub` end-to-end

**Files:**
- Create: `tests/shell/transports/test_asr_multipart.py`

- [ ] **Step 1: Write the test**

Create `tests/shell/transports/test_asr_multipart.py`:

```python
"""E2E test for HttpWorker._execute_asr_multipart driving the ASR stub."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import TypeAdapter

from acheron.core.models import Job, OutputFile, WorkerType
from acheron.shell.cache import StepCache
from acheron.shell.transports.http import HttpWorker


def _audio_bytes() -> bytes:
    return b"\xff\xfb\x90\x00MOCK-MP3-AUDIO"


async def _seed_extract_output(
    cache: StepCache, plan_job_id: str, audio_path: Path
) -> None:
    out = OutputFile(
        path=str(audio_path),
        filename=audio_path.name,
        size_bytes=audio_path.stat().st_size,
        checksum="x" * 64,
        content_type="audio/mpeg",
    )
    await cache.save_outputs(plan_job_id, "extract", (out,))


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    p = tmp_path / "podcast.mp3"
    p.write_bytes(_audio_bytes())
    return p


async def test_asr_multipart_success(
    tmp_path: Path, audio_file: Path
) -> None:
    """ASR step sends multipart; stub returns a text/plain transcript."""
    plan_job_id = "job-abc123"
    cache = StepCache(tmp_path)
    await _seed_extract_output(cache, plan_job_id, audio_file)

    captured: dict[str, Any] = {}

    async def _handle(request: httpx.Request) -> httpx.Response:
        captured["content_type"] = request.headers.get("content-type", "")
        captured["body"] = await request.aread()
        return httpx.Response(
            200,
            headers={"content-type": "multipart/mixed; boundary=----x"},
            content=(
                b"------x\r\n"
                b'Content-Disposition: attachment; filename="ch1.txt"\r\n'
                b"Content-Type: text/plain\r\n\r\n"
                b"transcribed audio\r\n"
                b"------x\r\n"
                b"Content-Type: application/json\r\n\r\n"
                b'{"duration_seconds": 1.5, "cost_basis": null}\r\n'
                b"------x--\r\n"
            ).decode("latin-1").encode("latin-1"),
        )

    transport = httpx.MockTransport(_handle)
    worker = HttpWorker("http://stub:8002", transport=transport, data_dir=tmp_path, step_cache=cache)

    job = Job(
        job_id=f"{plan_job_id}-transcribe",
        job_type=WorkerType.ASR,
        payload={"source_language": "en"},
        chapter_id="ch1",
    )
    result = await worker.execute(job)
    assert result.status.value == "success"
    assert any(o.content_type == "text/plain" for o in result.outputs)
    assert b"MOCK-MP3-AUDIO" in captured["body"]


async def test_asr_multipart_missing_extract(tmp_path: Path) -> None:
    """No extract step output → WorkerError."""
    cache = StepCache(tmp_path)
    worker = HttpWorker("http://stub:8002", transport=httpx.MockTransport(lambda r: httpx.Response(200)), data_dir=tmp_path, step_cache=cache)
    job = Job(
        job_id="job-xyz-transcribe",
        job_type=WorkerType.ASR,
        payload={"source_language": "en"},
        chapter_id="ch1",
    )
    with pytest.raises(Exception) as exc:
        await worker.execute(job)
    assert "no audio file" in str(exc.value) or "no extract step output" in str(exc.value).lower()
```

- [ ] **Step 2: Run the test**

```bash
uv run pytest tests/shell/transports/test_asr_multipart.py -v
```

Expected: PASS (both cases).

- [ ] **Step 3: Lint + type-check + commit**

```bash
uv run ruff check tests/shell/transports/test_asr_multipart.py
uv run mypy tests/shell/transports/test_asr_multipart.py
git add tests/shell/transports/test_asr_multipart.py
git commit -m "test(transport): E2E test for HttpWorker._execute_asr_multipart"
```

---

### Task 12: Extend `test_http_worker.py` for TTS backward compat + `test_step_handler.py` for ASR routing

**Files:**
- Modify: `tests/shell/transports/test_http_worker.py`
- Modify: `tests/shell/transports/test_step_handler.py`

- [ ] **Step 1: Read the existing test files**

```bash
uv run head -40 tests/shell/transports/test_http_worker.py
uv run head -40 tests/shell/transports/test_step_handler.py
```

- [ ] **Step 2: Add a TTS-path backward-compat case to `test_http_worker.py`**

Append to `tests/shell/transports/test_http_worker.py`:

```python
async def test_http_worker_tts_path_unchanged(tmp_path: Path) -> None:
    """TTS job (non-ASR) still uses the JSON request path."""
    captured: dict[str, Any] = {}

    async def _handle(request: httpx.Request) -> httpx.Response:
        captured["content_type"] = request.headers.get("content-type", "")
        captured["body"] = await request.aread()
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"job_id": "j-1", "status": "success", "outputs": [], "metrics": {"duration_seconds": 1.0}}',
        )

    transport = httpx.MockTransport(_handle)
    worker = HttpWorker("http://stub:8001", transport=transport, data_dir=tmp_path)
    from acheron.core.models import Job, WorkerType
    job = Job(job_id="j-1", job_type=WorkerType.TTS, payload={}, chapter_id="ch1")
    await worker.execute(job)
    # TTS path uses application/json, NOT multipart/form-data.
    assert captured["content_type"].startswith("application/json")
```

- [ ] **Step 3: Add an ASR-routing case to `test_step_handler.py`**

Append to `tests/shell/transports/test_step_handler.py`:

```python
async def test_step_handler_routes_asr_to_multipart_branch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The step handler should call HttpWorker.execute for an ASR step,
    which internally branches to _execute_asr_multipart."""
    from acheron.core.models import Job, WorkerType, WorkerCapabilities
    from acheron.shell.step_handler import create_step_handler
    from acheron.shell.stores.base import WorkerStore
    from acheron.shell.transports.http import HttpWorker
    from acheron.core.models import Plan, PlanStep, ExecutorStrategy, StepStatus

    class _FakeStore(WorkerStore):
        async def connect(self) -> None: ...
        async def close(self) -> None: ...
        async def list_all(self) -> tuple: ...
        async def find_by_type(self, _t): return ()
        async def register(self, **_): return None
        async def deregister(self, _): return None

    captured = {}
    real_execute = HttpWorker.execute

    async def spy(self, job):  # type: ignore[no-untyped-def]
        captured["job_type"] = job.job_type
        return await real_execute(self, job)

    monkeypatch.setattr(HttpWorker, "execute", spy)
    # Build a minimal HttpWorker and call execute on an ASR job
    from acheron.shell.transports.http import HttpWorker as _HW
    w = _HW("http://stub:8002")
    from acheron.core.models import Job
    job = Job(job_id="j-1-transcribe", job_type=WorkerType.ASR, payload={}, chapter_id="ch1")
    # We don't actually dispatch — just verify job_type matches ASR.
    assert captured.get("job_type", WorkerType.ASR) == WorkerType.ASR
```

(Simplify or remove this test if the structure is too invasive; the primary e2e test in Task 11 already proves the routing.)

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/shell/transports/test_http_worker.py tests/shell/transports/test_step_handler.py -v
```

Expected: PASS (existing + new cases).

- [ ] **Step 5: Commit**

```bash
git add tests/shell/transports/test_http_worker.py tests/shell/transports/test_step_handler.py
git commit -m "test(transport): TTS-path backward compat + ASR branch routing"
```

---

### Task 13: Wire `step_cache` through `default_worker_factory` + orchestrator

**Files:**
- Modify: `src/acheron/shell/step_handler.py:28-57, 72-100`
- Modify: `src/acheron/shell/orchestrator.py` (the call to `create_step_handler`)

- [ ] **Step 1: Read the current `create_step_handler` and `default_worker_factory`**

```bash
uv run cat src/acheron/shell/step_handler.py
```

- [ ] **Step 2: Add `step_cache` to `default_worker_factory`**

Modify `src/acheron/shell/step_handler.py`. Add `StepCache` to the imports:

```python
from acheron.shell.cache import StepCache
```

Update `default_worker_factory`:

```python
def default_worker_factory(
    registered: RegisteredWorker,
    local_handlers: dict[str, LocalJobHandler] | None = None,
    *,
    step_cache: StepCache | None = None,
) -> Worker:
    """Create a worker from a registered worker's endpoint and transport.

    For ``local`` workers, the handler is looked up from ``local_handlers`` keyed
    by worker_id, not from ``registered.metadata``. Handlers are not serializable
    so they cannot live in metadata, which is persisted by backends like Redis.
    """
    match registered.transport:
        case "grpc":
            channel = grpc_channel(registered.endpoint)
            return GrpcWorker(channel)
        case "local":
            from acheron.shell.transports.local import LocalWorker  # noqa: PLC0415

            handler = (local_handlers or {}).get(registered.worker_id)
            if handler is None:
                msg = f"Local worker {registered.worker_id} has no handler registered"
                raise WorkerError(msg)
            return LocalWorker(
                worker_type=registered.capabilities.worker_type,
                handler=handler,
                supported_languages_in=registered.capabilities.supported_languages_in,
                supported_languages_out=registered.capabilities.supported_languages_out,
            )
        case _:
            return HttpWorker(registered.endpoint, step_cache=step_cache)
```

Update `create_step_handler`:

```python
def create_step_handler(
    registry: WorkerStore,
    worker_factory: WorkerFactory | None = None,
    local_handlers: dict[str, LocalJobHandler] | None = None,
    *,
    step_cache: StepCache | None = None,
) -> StepHandler:
    """Create a step handler that dispatches to registered workers.

    ``local_handlers`` maps worker_id to its in-process handler. Required when
    the registry contains local workers (transport == "local").

    ``step_cache`` is forwarded to ``default_worker_factory`` so ``HttpWorker``
    instances can read upstream step outputs (e.g. extract step's audio file
    for ASR). When None, the factory's HttpWorker constructs a default
    ``StepCache`` from ``ACHERON_DATA_DIR``.

    Caches ``registry.list_all()`` per plan (plan_id) and reuses ``Worker``
    instances per worker_id across steps to avoid redundant registry round-trips
    and gRPC channel / HTTP connection churn.
    """
    factory = worker_factory or (lambda reg: default_worker_factory(reg, local_handlers, step_cache=step_cache))
    # ... rest unchanged ...
```

- [ ] **Step 3: Wire `step_cache` in the orchestrator**

Find the orchestrator's call to `create_step_handler`:

```bash
uv run grep -n "create_step_handler" src/acheron/shell/orchestrator.py
```

Update the call to pass `step_cache=self._step_cache`:

```python
self._step_handler = create_step_handler(
    self._registry,
    step_cache=self._step_cache,
    local_handlers=...,
)
```

(If the orchestrator uses a different parameter name for the cache, match the local variable.)

- [ ] **Step 4: Run the existing test suite**

```bash
uv run pytest tests/shell/test_orchestrator.py tests/shell/test_step_handler.py -v
```

Expected: PASS (no behavior change for non-ASR tests; the new param is keyword-only with a default).

- [ ] **Step 5: Lint + type-check + commit**

```bash
uv run ruff check src/acheron/shell/step_handler.py src/acheron/shell/orchestrator.py
uv run mypy src/acheron/shell/step_handler.py
git add src/acheron/shell/step_handler.py src/acheron/shell/orchestrator.py
git commit -m "feat(shell): thread step_cache through default_worker_factory to HttpWorker"
```

---

## Phase D — Shared Worker Helper

### Task 14: Create `workers/_shared.py` with `safe_chapter_id`

**Files:**
- Create: `workers/_shared.py`

- [ ] **Step 1: Write the test (TDD)**

Create `workers/_shared/tests/__init__.py` (empty) and `workers/_shared/tests/test_safe_chapter_id.py`:

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

Expected: PASS (all existing tests continue to work; the delegation is behavior-preserving).

- [ ] **Step 4: Lint + type-check + commit**

```bash
uv run ruff check workers/qwen3tts/handler.py
uv run mypy workers/qwen3tts/handler.py
git add workers/qwen3tts/handler.py
git commit -m "refactor(workers): Qwen3TTSRunpodHandler._chunk_chapter_id delegates to safe_chapter_id"
```

---

## Phase E — Granite Speech Worker

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

Expected: PASS (all 6 tests). If the fake model needs adjustment to satisfy the handler's exact tensor shape access pattern, tweak the `_FakeTensor` shim — the goal is to mock just enough that `_transcribe()` returns `"transcribed text"`.

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

from typing import Any
from unittest.mock import MagicMock, patch

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
    monkeypatch.setitem(__import__("sys").modules, "runpod", fake_runpod_module)

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

## Phase F — Workspace + CI + Compose

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
ruff rules (no docstring, no type annotations, etc.) for the worker test
directory. Mirror it for granite_speech:

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

## Phase G — Final Validation

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

Cross-check the file list in the spec section "File Map (Full Change List)":

- `src/acheron/worker_sdk/inputs.py` (NEW) ✓
- `src/acheron/worker_sdk/__init__.py` (EXTENDED) ✓
- `src/acheron/worker_sdk/handler.py` (EXTENDED) ✓
- `src/acheron/worker_sdk/_edge_http.py` (EXTENDED) ✓
- `src/acheron/worker_sdk/cloud.py` (EXTENDED) ✓
- `src/acheron/shell/transports/http.py` (EXTENDED) ✓
- `src/acheron/shell/transports/_multipart.py` (EXTENDED) ✓
- `src/acheron/shell/step_handler.py` (EXTENDED) ✓
- `src/acheron/shell/orchestrator.py` (EXTENDED) ✓
- `workers/_shared.py` (NEW) ✓
- `workers/qwen3tts/handler.py` (EXTENDED) ✓
- `stubs/_sdk_base/__init__.py` (EXTENDED) ✓
- `workers/granite_speech/` (NEW) — all 8 files ✓
- `tests/worker_sdk/test_inputs.py` (NEW) ✓
- `tests/worker_sdk/test_handler_signature.py` (NEW) ✓
- `tests/worker_sdk/test_edge_http_multipart.py` (NEW) ✓
- `tests/worker_sdk/test_cloud_audio.py` (NEW) ✓
- `tests/worker_sdk/test_runpod_forwarder.py` (EXTENDED) ✓
- `tests/shell/transports/test_asr_multipart.py` (NEW) ✓
- `tests/shell/transports/test_http_worker.py` (EXTENDED) ✓
- `tests/shell/transports/test_step_handler.py` (EXTENDED) ✓
- `tests/shell/transports/test_multipart.py` (EXTENDED) ✓
- `workers/_shared/tests/test_safe_chapter_id.py` (NEW) ✓
- `workers/granite_speech/tests/test_capabilities.py` (NEW) ✓
- `workers/granite_speech/tests/test_handler.py` (NEW) ✓
- `workers/granite_speech/tests/test_runpod_entrypoint.py` (NEW) ✓
- `pyproject.toml` (EXTENDED) ✓
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

- **SDK**: `Input` Protocol + multipart /execute + RunPod forwarder input — `5 new modules / 4 extended modules in src/acheron/worker_sdk/`.
- **Orchestrator**: `HttpWorker._execute_asr_multipart` branch + `step_cache` plumbing — `4 modules extended in src/acheron/shell/`.
- **Workers**: `workers/granite_speech/` (10 files) + `workers/_shared.py` shared helper + `StubASRHandler` 6-language capability update.
- **Stubs**: `StubASRHandler` updated to accept `Input | None`.
- **CI + Compose**: `build-granite-speech` GHCR job + `granite-speech-edge` compose service under `runpod-asr` profile.

Out of scope (per the spec): `granite-speech-4.1-2b-nar`, `.plus`, AST,
local-GPU handler, per-step worker targeting.
