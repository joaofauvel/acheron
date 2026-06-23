# Layer 8b Sub-plan 1 — SDK Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `acheron.worker_sdk` with the typed `Input` Protocol (symmetric with `Artifact`), the multipart `/execute` request contract, and the RunPod forwarder's audio base64-encoding, so the ASR worker (Sub-plan 3) can consume a typed audio input on the wire.

**Architecture:** A new `Input` Protocol in `acheron.worker_sdk.inputs` mirrors the existing `Artifact` Protocol — three variants (BytesInput / StreamInput / FileInput) with the same `@property` accessor pattern. `WorkerHandler.handle()` gains an optional second parameter `input: Input | None = None` so every existing handler (TTS, translation stubs, `RunPodForwarderHandler`) keeps compiling unchanged. The SDK's `/execute` route accepts either `application/json` (legacy / TTS) or `multipart/form-data` (8b ASR) on the request side; response side (`multipart/mixed`) is unchanged from 8a. The RunPod forwarder base64-encodes the `Input` into a new `input_audio` field of the RunPod `/run` input shape (RunPod's `/run` protocol is JSON-only); the cloud-side `make_runpod_handler._rp_handler` decodes it back into a `BytesInput`.

**Tech Stack:** Python 3.14, pydantic v2, pydantic-settings, httpx, FastAPI, uvicorn, the `runpod` Python SDK, pytest + pytest-asyncio, mypy + basedpyright, ruff, import-linter.

**Reference spec:** `docs/superpowers/specs/2026-06-23-layer8b-asr-worker-design.md` (the parent spec for all 3 sub-plans; this sub-plan covers the SDK section of the file map).

**Final gate:** `just validate` green (lint-strict, lint-imports, mypy, basedpyright, pytest — all clean, coverage ≥ 80%).

---

## Spec Adjustments (refinements from writing the plan)

These deltas are not new design decisions; they are clarifications of decisions already made in the parent spec, surfaced by writing the implementation steps. The parent spec remains the single source of truth for the design.

1. **Input Protocol `@property` accessors** mirror `Artifact`'s pattern intentionally — `@dataclass(frozen=True)` concrete implementations have read-only fields, and a Protocol declaring plain attributes is incompatible with frozen dataclasses under strict type-checkers. The spec's "Inputs" section already says "mirror `Artifact`"; this adjustment just makes the property pattern explicit.
2. **Multipart wire contract uses named parts on input**: `name="request"` (the `ExecuteRequest` envelope, `Content-Type: application/json`) + `name="audio"` (the audio bytes, `Content-Type: audio/mpeg` or `audio/wav`). Output side remains Content-Type-sniffed (the existing `multipart/mixed` contract from 8a is unchanged).
3. **RunPod `/run` wire carries `input_audio` as a base64 dict** with three fields: `content_type: str`, `data: str` (base64-encoded), `metadata: dict[str, JsonValue]`. RunPod's `/run` protocol is JSON-only — base64 is the only way to round-trip binary audio over the wire. Cloud-side `make_runpod_handler._rp_handler` decodes it back into a `BytesInput` and passes it to the handler.
4. **`RunPodForwarderHandler.handle()` accepts `input: Input | None = None`**; the TTS path with `input=None` is byte-for-byte identical to today's behavior. TTS jobs (no `input_audio` field on the wire) → `input=None` → handler dispatches exactly as 8a.
5. **`StubASRHandler` capability language set grows from 4 to 6** (adds `ja` and `pt`) to match the real worker's 6-language ASR contract. The format set also grows: `supported_formats_in = {mp3, wav}`, `supported_formats_out = {text}`. The stub `handle()` accepts `input: Input | None = None` and ignores the audio content (the user-confirmed stub policy from the spec's Stubs section).

---

## Adversarial Review Rubric

After this sub-plan is implemented (or before — at the user's option), dispatch a fresh-context reviewer subagent with this rubric:

### Correctness
- [ ] Multipart parser handles: (a) missing audio part (TTS-style multipart with only the JSON part), (b) missing JSON part (raises `WorkerError`), (c) malformed body (non-multipart, `raise_for_status` semantics).
- [ ] `BytesInput.stream()` yields the in-memory bytes as a single chunk (does not buffer beyond what's already buffered).
- [ ] `StreamInput.stream()` delegates to the producer exactly once per iteration (no double-consumption).
- [ ] `FileInput.stream()` reads the file in 64 KiB chunks and closes the file on exit.
- [ ] `RunPodForwarderHandler` round-trips audio byte-for-byte: edge sends `input_bytes` → cloud receives `input_bytes` (no padding, no truncation, no encoding mismatch).
- [ ] TTS jobs (no `input_audio` field on the wire) still work end-to-end — no behavior change vs 8a.
- [ ] All existing 8a tests still pass (no regressions in `_edge_http`, `cloud`, `app`, `runpod_forwarder`).

### Code quality
- [ ] `Input` Protocol shape is consistent with `Artifact`: same `@property` accessor pattern, same `frozen=True` dataclass variants, same `stream() -> AsyncIterator[bytes]` signature.
- [ ] No `Any` abuse; the wire contract fields (`content_type`, `data`, `metadata`) are typed end-to-end.
- [ ] No new env-var reads outside the existing `WorkerSettings` (CFG-006 from the open review still applies).
- [ ] No dead config knobs; every new field is consumed by code.
- [ ] The `_parse_request_multipart` helper is reusable by both the SDK and the orchestrator's response parser (no copy-paste).
- [ ] `WorkerHandler.handle` signature is the **only** abstract method signature change; everything else (`capabilities`, `startup`, `shutdown`) is unchanged.

### Spec compliance
- [ ] Every spec file in the spec's "File Map" SDK section is created or modified (cross-check `src/acheron/worker_sdk/inputs.py`, `__init__.py`, `handler.py`, `_edge_http.py`, `cloud.py` and the 5 test files).
- [ ] Multipart wire contract matches the spec's "Wire format on /execute" section exactly.
- [ ] `StubASRHandler` capability set matches the spec's "Stubs" section (6 ASR languages, `mp3`/`wav` in, `text/plain` out, `batch_capable=False`).
- [ ] The `Input` Protocol is **not** re-exported in `acheron.worker_sdk.__init__` (only the concrete variants are — the Protocol stays internal to the type-checker, matching the `Artifact` precedent).

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/acheron/worker_sdk/inputs.py` (NEW) | `Input` Protocol + `BytesInput` / `StreamInput` / `FileInput` (symmetric with `Artifact`). |
| `src/acheron/worker_sdk/__init__.py` (EXTENDED) | Re-export `BytesInput`, `StreamInput`, `FileInput`. |
| `src/acheron/worker_sdk/handler.py` (EXTENDED) | `WorkerHandler.handle()` gains `input: Input \| None = None`. |
| `src/acheron/worker_sdk/_edge_http.py` (EXTENDED) | `/execute` route accepts `multipart/form-data` OR `application/json`. |
| `src/acheron/worker_sdk/cloud.py` (EXTENDED) | `_serialise_job_for_runpod` carries `input_audio`; `make_runpod_handler._rp_handler` deserialises it; `RunPodForwarderHandler.handle()` accepts and forwards `input`. |
| `stubs/_sdk_base/__init__.py` (EXTENDED) | `StubASRHandler.handle()` accepts `Input \| None`; capability language set grows to 6. |
| `workers/qwen3tts/handler.py` (EXTENDED) | `Qwen3TTSRunpodHandler.handle()` accepts `input: Input \| None = None` (no behavior change). |
| `tests/worker_sdk/test_inputs.py` (NEW) | `Input` Protocol / `BytesInput` / `StreamInput` / `FileInput` tests. |
| `tests/worker_sdk/test_handler_signature.py` (NEW) | `WorkerHandler.handle` signature backward compat. |
| `tests/worker_sdk/test_edge_http_multipart.py` (NEW) | `/execute` multipart + JSON routes. |
| `tests/worker_sdk/test_cloud_audio.py` (NEW) | `_serialise_job_for_runpod` + `make_runpod_handler` audio forward. |
| `tests/worker_sdk/test_runpod_forwarder.py` (EXTENDED) | `RunPodForwarderHandler.handle` input forwarding. |

---

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

from acheron.core.models import Job
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

- [ ] **Step 4: Update existing handler subclasses to match the new signature**

Run the existing handler tests to identify any subclass overrides that
need updating:

```bash
uv run pytest tests/worker_sdk/ -v
```

The new signature is keyword-defaulted, so subclasses that override with
`handle(self, job)` still satisfy the ABC. **However**, to make the override
match the new contract explicitly (so mypy / basedpyright don't flag it
as Liskov-violating), update each subclass declaration to include the
`input` parameter. The body ignores `input` for TTS / translation / forwarder.

Apply the same one-line change to all of:

- `workers/qwen3tts/handler.py:Qwen3TTSRunpodHandler.handle`
- `stubs/_sdk_base/__init__.py:StubTTSHandler.handle`
- `stubs/_sdk_base/__init__.py:StubASRHandler.handle` (Task 4 also
  extends this with the 6-language capability set)
- `stubs/_sdk_base/__init__.py:StubTranslationHandler.handle`
- `src/acheron/worker_sdk/cloud.py:RunPodForwarderHandler.handle` (Task 7
  extends this to forward `input` to RunPod)

For each, change:

```python
    async def handle(self, job: Job) -> list[Artifact]:
        # ... existing body, ignoring `input` ...
```

to:

```python
    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:
        # ... existing body, ignoring `input` ...
```

and add the `Input` import to the existing imports.

After updating all signatures, run:

```bash
uv run pytest tests/ -v
```

Expected: all tests pass (any pre-existing test that exercises these
handlers should continue to work because `input` defaults to `None`).

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

Expected: all 7-stub matrix tests pass. The 2-test parametric check on
`asr_local_stub` should continue to assert the stub registers + serves
/health.

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
    return create_worker_app(
        handler=_AsrEchoHandler(),
        settings=_settings(),
        disable_registration=True,
    )


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
    # Handler should have received the audio bytes.
    handler = app.handler  # type: ignore[attr-defined]
    assert handler.received == [b"\xff\xfb\x90\x00mock-audio"]
    assert handler.received_content_type == ["audio/mpeg"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/worker_sdk/test_edge_http_multipart.py -v
```

Expected: `AssertionError` on the multipart case (the current /execute
only accepts `ExecuteRequest` JSON, doesn't read multipart).

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

        from acheron.core.errors import WorkerError  # noqa: PLC0415
        from acheron.worker_sdk.schemas import ExecuteRequest  # noqa: PLC0415

        ctype = request.headers.get("content-type", "")
        boundary = ctype.split("boundary=", 1)[1].split(";", 1)[0].strip().strip('"')
        body = await request.body()
        full_body = (
            f"Content-Type: {ctype.split(';', 1)[0].strip()}; boundary={boundary}\r\n"
            "MIME-Version: 1.0\r\n\r\n"
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
        input_obj: Input | None = None
        if audio_part is not None:
            audio_raw = audio_part.get_payload(decode=True)
            audio_bytes = audio_raw if isinstance(audio_raw, bytes) else str(audio_raw).encode("utf-8")
            input_obj = BytesInput(
                content_type=audio_part.get_content_type(),
                data=audio_bytes,
                metadata={},
            )

        start = time.monotonic()
        try:
            artifacts: list[Artifact] = await self.handler.handle(job, input_obj)
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

Add the `Request` to the `fastapi` imports (next to `FastAPI`):

```python
from fastapi import FastAPI, Request
```

(If `WorkerError` is already imported, skip the redundant import inside
the method.)

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

import pytest

from acheron.core.models import Job, WorkerType
from acheron.worker_sdk.artifacts import BytesArtifact
from acheron.worker_sdk.cloud import _serialise_job_for_runpod, make_runpod_handler
from acheron.worker_sdk.handler import WorkerHandler
from acheron.worker_sdk.inputs import BytesInput, Input


class _CaptureHandler(WorkerHandler):
    """Records the job + input it received."""

    def __init__(self) -> None:
        self.received_job: Job | None = None
        self.received_input: Input | None = None

    def capabilities(self):  # noqa: ANN001, D102
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

Expected: `TypeError: _serialise_job_for_runpod() got an unexpected keyword
argument 'input'` (or `missing 1 required positional argument`).

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
            input_obj = BytesInput(
                content_type=str(audio_payload.get("content_type", "audio/wav")),
                data=body,
                metadata=dict(audio_payload.get("metadata", {})),
            )
            artifacts = await handler.handle(job, input_obj)
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

Expected: PASS. The existing tests call `forwarder.handle(job)` (no input);
the new `input=None` default keeps the behavior identical.

- [ ] **Step 4: Add a new test case for input forwarding**

Append to `tests/worker_sdk/test_runpod_forwarder.py`:

```python
async def test_forwarder_passes_input_to_runpod_client() -> None:
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

(If the existing test file uses a different fixture pattern, follow the
existing style; the key is to assert `input_audio` is in the captured
payload.)

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

## Final Validation

After all 7 tasks are complete and committed:

```bash
just validate
```

Expected: all sub-targets (lint-strict, lint-imports, mypy, basedpyright,
pytest) green. The 8a test suite must still pass — no regressions in
`_edge_http`, `cloud`, `app`, `runpod_forwarder`, or the worker stubs.

## Adversarial Review (post-implementation)

Once `just validate` is green, dispatch a fresh-context reviewer subagent
with the rubric at the top of this sub-plan. The reviewer reads:

- This sub-plan
- The parent spec at `docs/superpowers/specs/2026-06-23-layer8b-asr-worker-design.md`
- The 8a SDK foundation plan at `docs/superpowers/plans/2026-06-22-layer8a-sdk-foundation.md`

…and produces findings in the same theme-keyed story format the 8a review
used (`docs/code_review/`). Fix any open CRITICAL / HIGH / MEDIUM findings
before starting Sub-plan 2.
