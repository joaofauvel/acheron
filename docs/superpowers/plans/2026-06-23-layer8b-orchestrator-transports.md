# Layer 8b Sub-plan 2 — Orchestrator Transports

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the SDK's `Input` Protocol into the orchestrator's HTTP transport so ASR steps load the upstream extract step's audio file and POST `multipart/form-data` to the worker. TTS / translation / non-audio workers continue to use the existing JSON path with no behavior change.

**Architecture:** `HttpWorker.__init__` gains a `step_cache: StepCache | None = None` keyword-only injection. `HttpWorker.execute()` branches on `job.job_type == WorkerType.ASR` to call the new `_execute_asr_multipart(job)` method, which reads the upstream extract step's output from `StepCache.load_outputs(plan_job_id, "extract")`, opens the audio file, and POSTs `multipart/form-data` (one `application/json` part for the `ExecuteRequest` envelope + one binary part for the audio). The response side is unchanged: `multipart/mixed` is parsed by the existing `_parse_multipart`, `application/json` by the existing `_result_adapter`. The `_parse_request_multipart` helper in `_multipart.py` is the shared multipart-body parser (reusable by the SDK and any future transport). `default_worker_factory` and `create_step_handler` thread the orchestrator's existing `StepCache` through to `HttpWorker` so production wiring is clean; tests pass an explicit `StepCache(tmp_path)`.

**Tech Stack:** Python 3.14, httpx, pydantic v2, the existing `StepCache` from `acheron.shell.cache`, pytest + pytest-asyncio, mypy + basedpyright, ruff, import-linter.

**Reference spec:** `docs/superpowers/specs/2026-06-23-layer8b-asr-worker-design.md` (this sub-plan covers the "Orchestrator-Side Changes" section).

**Final gate:** `just validate` green (lint-strict, lint-imports, mypy, basedpyright, pytest — all clean, coverage ≥ 80%).

**Depends on:** Sub-plan 1 (SDK Foundation) — `Input`, `BytesInput`, `WorkerHandler.handle(self, job, input=None)` are all in place.

---

## Spec Adjustments (refinements from writing the plan)

These deltas are not new design decisions; they are clarifications of decisions already made in the parent spec, surfaced by writing the implementation steps. The parent spec remains the single source of truth for the design.

1. **`HttpWorker.__init__` `step_cache` is keyword-only**, defaulting to `StepCache(self._data_dir)` for backward compat. Pre-existing tests that construct `HttpWorker(endpoint)` (no `step_cache`) get a fresh instance backed by the default data dir; no test changes are required for non-ASR tests.
2. **`_execute_asr_multipart` reads the upstream extract via `StepCache.load_outputs(plan_job_id, "extract")`**. The `plan_job_id` is the `job.job_id` minus the `-transcribe` suffix (e.g., `job_id="job-abc123-transcribe"` → `plan_job_id="job-abc123"`). This matches the convention used by `ChunkingHandler` and `PackagingHandler` for the same kind of cross-step output lookup.
3. **`default_worker_factory` gains a `step_cache: StepCache | None = None` keyword-only parameter**; the orchestrator passes its own `StepCache` instance via `create_step_handler(..., step_cache=self._step_cache)`. The `lambda` factory closure in `create_step_handler` forwards the parameter so `HttpWorker(registered.endpoint, step_cache=step_cache)` is constructed with the orchestrator's cache in production.
4. **`_parse_request_multipart` is the shared multipart-body parser**. It returns `(envelope_dict, audio_bytes, audio_content_type)`; legacy JSON body returns `(dict, b"", "")` for direct passthrough. The SDK (`_edge_http.py`) and the orchestrator (`http.py`) parse independently — the wire contract is symmetric but each side does different things with the result, so a single helper is the right level of sharing.
5. **TTS path is unchanged (no new code path)**. The existing `HttpWorker.execute()` body — for non-ASR jobs — continues to use httpx's `json=` parameter. The `if job.job_type == WorkerType.ASR:` branch is the only new code on the request side. The new `step_cache` parameter is the only new constructor argument.

---

## Adversarial Review Rubric

After this sub-plan is implemented (or before — at the user's option), dispatch a fresh-context reviewer subagent with this rubric:

### Correctness
- [ ] `StepCache.load_outputs(plan_job_id, "extract")` returns the audio file the local `ExtractionHandler` produced (content_type `audio/mpeg` for `.mp3` or `audio/wav` for `.wav`). The `next((o for o in extract_outputs if o.content_type.startswith("audio/")), None)` selector correctly handles the single-audio-file case.
- [ ] The `plan_job_id` extraction (`job.job_id.rsplit("-", 1)[0]`) matches the chunking / packaging handlers' convention; cross-step lookups work uniformly.
- [ ] Missing extract step's output (no audio file) raises `WorkerError("ASR step ... no audio file in extract output")` with a clear message.
- [ ] Missing audio file on disk (extracted, then deleted) raises `WorkerError("ASR step ... audio file missing: ...")` with the path included.
- [ ] The multipart response from the worker is parsed correctly: `multipart/mixed` → `OutputFile`s materialized into `ACHERON_DATA_DIR`; `application/json` → legacy `JobResult` JSON.
- [ ] The TTS path (TTS job dispatched) uses `httpx.AsyncClient.post(..., json=_job_to_dict(job))` exactly as before — no new behavior.
- [ ] The orchestrator-side multipart body wraps the JSON envelope as `(None, json.dumps(...).encode("utf-8"), "application/json")` and the audio as `(audio_path.name, audio_bytes, audio_out.content_type)`; the SDK's `_edge_http.py` multipart parser must extract both parts correctly.
- [ ] All existing 8a / Sub-plan 1 tests still pass (no regressions in `HttpWorker`, `step_handler`, `Orchestrator`).

### Code quality
- [ ] `step_cache` injection is clean: keyword-only, no module-level globals, no `ACHERON_*` env-var reads in the transport (CFG-006 from the open review still applies).
- [ ] `_execute_asr_multipart` exception handling is consistent with `_request`: clear `WorkerError` messages, no swallowed exceptions.
- [ ] No duplication of the multipart body parser — the `_parse_request_multipart` helper is the single source of truth; both the SDK and the orchestrator transport could call it if needed (the orchestrator transport doesn't today because the response side and request side have different shapes).
- [ ] No `Any` abuse; the new code is fully typed.
- [ ] The `asyncio.to_thread(audio_path.read_bytes)` call is non-blocking on the event loop; the file read happens in a worker thread.

### Spec compliance
- [ ] `HttpWorker._execute_asr_multipart` is the only new method; the request body uses `multipart/form-data` with one `application/json` part and one binary part, matching the parent spec's "Wire format on /execute" section.
- [ ] `tests/shell/transports/test_asr_multipart.py` covers: (a) successful end-to-end round trip (extract output → multipart → stub → text/plain response), (b) missing extract output (raises WorkerError), (c) extract output without audio file (raises WorkerError).
- [ ] `tests/shell/transports/test_http_worker.py` is extended with a 1-test backward-compat case for the TTS path: with `job.job_type = TTS`, the transport uses `json=` and the response is parsed as `multipart/mixed` or `JobResult` JSON without entering `_execute_asr_multipart`.
- [ ] The orchestrator-side change is the only orchestrator-side code change in the spec's "File Map" Orchestrator Transports section.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/acheron/shell/transports/http.py` (EXTENDED) | `__init__` gains `step_cache` keyword; `execute` branches on `WorkerType.ASR` to call new `_execute_asr_multipart` method. |
| `src/acheron/shell/transports/_multipart.py` (EXTENDED) | New `_parse_request_multipart(ctype, body) -> (envelope, audio_bytes, audio_content_type)` helper. |
| `src/acheron/shell/step_handler.py` (EXTENDED) | `default_worker_factory` gains `step_cache` keyword; `create_step_handler` plumbs `step_cache` through to the factory. |
| `src/acheron/shell/orchestrator.py` (EXTENDED) | Pass `self._step_cache` to `create_step_handler`. |
| `tests/shell/transports/test_asr_multipart.py` (NEW) | E2E test for `_execute_asr_multipart` driving `asr_local_stub`. |
| `tests/shell/transports/test_http_worker.py` (EXTENDED) | Backward-compat case for TTS path. |
| `tests/shell/transports/test_step_handler.py` (EXTENDED) | ASR branch routing case. |
| `tests/shell/transports/test_multipart.py` (EXTENDED) | `_parse_request_multipart` test cases (multipart with audio, multipart without audio, legacy JSON body). |

---

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

Expected: PASS. The default-constructed `StepCache(data_dir)` is a no-I/O
`Path` wrapper; existing call sites that don't pass `step_cache` get a
fresh instance.

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

Append to `src/acheron/shell/transports/_multipart.py` (add `from __future__ import annotations` and the stdlib imports at the top of the file if not already present):

```python
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

- [ ] **Step 3: Read the existing test file to find the insertion point**

```bash
uv run head -30 tests/shell/transports/test_multipart.py
```

- [ ] **Step 4: Add test cases to `test_multipart.py`**

Append to `tests/shell/transports/test_multipart.py` (add the import at the top):

```python
from acheron.shell.transports._multipart import _parse_request_multipart


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


def test_parse_request_multipart_no_audio_part() -> None:
    """Multipart with only the JSON part → empty audio (TTS-style)."""
    boundary = "--b"
    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json\r\n\r\n"
        f'{{"job_id": "j-1"}}\r\n'
        f"--{boundary}--\r\n"
    ).encode()
    ctype = f"multipart/form-data; boundary={boundary}"
    env, audio_bytes, audio_ctype = _parse_request_multipart(ctype, body)
    assert env["job_id"] == "j-1"
    assert audio_bytes == b""
    assert audio_ctype == ""


def test_parse_request_multipart_missing_json_part_raises() -> None:
    """Multipart without application/json part → ValueError."""
    boundary = "--b"
    body = (
        f"--{boundary}\r\n"
        f"Content-Type: audio/mpeg\r\n\r\n"
        f"AUDIOBYTES\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    ctype = f"multipart/form-data; boundary={boundary}"
    with pytest.raises(ValueError, match="no application/json part"):
        _parse_request_multipart(ctype, body)
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/shell/transports/test_multipart.py -v
```

Expected: PASS (existing + new cases).

- [ ] **Step 6: Lint + type-check + commit**

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

from pathlib import Path

import httpx
import pytest

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

    captured: dict = {}

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
            ),
        )

    transport = httpx.MockTransport(_handle)
    worker = HttpWorker(
        "http://stub:8002",
        transport=transport,
        data_dir=tmp_path,
        step_cache=cache,
    )

    job = Job(
        job_id=f"{plan_job_id}-transcribe",
        job_type=WorkerType.ASR,
        payload={"source_language": "en"},
        chapter_id="ch1",
    )
    result = await worker.execute(job)
    assert result.status.value == "success"
    assert any(o.content_type == "text/plain" for o in result.outputs)
    # The bytes that reached the stub include the audio file's contents.
    assert b"MOCK-MP3-AUDIO" in captured["body"]


async def test_asr_multipart_missing_extract(tmp_path: Path) -> None:
    """No extract step output → WorkerError."""
    cache = StepCache(tmp_path)
    worker = HttpWorker(
        "http://stub:8002",
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
        data_dir=tmp_path,
        step_cache=cache,
    )
    job = Job(
        job_id="job-xyz-transcribe",
        job_type=WorkerType.ASR,
        payload={"source_language": "en"},
        chapter_id="ch1",
    )
    with pytest.raises(Exception) as exc:
        await worker.execute(job)
    msg = str(exc.value).lower()
    assert "no audio file" in msg or "no extract step output" in msg


async def test_asr_multipart_no_audio_in_extract(tmp_path: Path) -> None:
    """Extract step produced only text (not audio) → WorkerError."""
    plan_job_id = "job-abc123"
    cache = StepCache(tmp_path)
    text_out = OutputFile(
        path=str(tmp_path / "chapter.txt"),
        filename="chapter.txt",
        size_bytes=0,
        checksum="x" * 64,
        content_type="text/plain",
    )
    await cache.save_outputs(plan_job_id, "extract", (text_out,))
    worker = HttpWorker(
        "http://stub:8002",
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
        data_dir=tmp_path,
        step_cache=cache,
    )
    job = Job(
        job_id=f"{plan_job_id}-transcribe",
        job_type=WorkerType.ASR,
        payload={"source_language": "en"},
        chapter_id="ch1",
    )
    with pytest.raises(Exception, match="no audio file"):
        await worker.execute(job)


async def test_asr_multipart_missing_audio_file_on_disk(
    tmp_path: Path, audio_file: Path
) -> None:
    """Extract step's audio file is recorded but missing from disk → WorkerError."""
    plan_job_id = "job-abc123"
    cache = StepCache(tmp_path)
    await _seed_extract_output(cache, plan_job_id, audio_file)
    # Delete the audio file from disk; the manifest still references it.
    audio_file.unlink()
    worker = HttpWorker(
        "http://stub:8002",
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
        data_dir=tmp_path,
        step_cache=cache,
    )
    job = Job(
        job_id=f"{plan_job_id}-transcribe",
        job_type=WorkerType.ASR,
        payload={"source_language": "en"},
        chapter_id="ch1",
    )
    with pytest.raises(Exception, match="audio file missing"):
        await worker.execute(job)
```

- [ ] **Step 2: Run the test**

```bash
uv run pytest tests/shell/transports/test_asr_multipart.py -v
```

Expected: PASS (4 cases).

- [ ] **Step 3: Lint + type-check + commit**

```bash
uv run ruff check tests/shell/transports/test_asr_multipart.py
uv run mypy tests/shell/transports/test_asr_multipart.py
git add tests/shell/transports/test_asr_multipart.py
git commit -m "test(transport): E2E test for HttpWorker._execute_asr_multipart"
```

---

### Task 12: Extend `test_http_worker.py` for TTS backward compat

**Files:**
- Modify: `tests/shell/transports/test_http_worker.py`

- [ ] **Step 1: Read the existing test file**

```bash
uv run head -40 tests/shell/transports/test_http_worker.py
```

- [ ] **Step 2: Add a TTS-path backward-compat case**

Append to `tests/shell/transports/test_http_worker.py`:

```python
async def test_http_worker_tts_path_uses_json_request(tmp_path: Path) -> None:
    """TTS job (non-ASR) still uses the JSON request path — no multipart."""
    captured: dict = {}

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

(Adjust the import line if `Job` and `WorkerType` are already imported at
the top of the test file.)

- [ ] **Step 3: Run the tests**

```bash
uv run pytest tests/shell/transports/test_http_worker.py -v
```

Expected: PASS (existing + new case).

- [ ] **Step 4: Commit**

```bash
git add tests/shell/transports/test_http_worker.py
git commit -m "test(transport): TTS-path backward compat for HttpWorker.execute"
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
    factory = worker_factory or (
        lambda reg: default_worker_factory(reg, local_handlers, step_cache=step_cache)
    )
    _cached_workers: tuple[RegisteredWorker, ...] | None = None
    _cached_plan_id: str | None = None
    _worker_instances: dict[str, Worker] = {}

    async def handler(step: PlanStep, plan: Plan) -> JobResult:
        nonlocal _cached_workers, _cached_plan_id
        src = plan.source_language
        dst = plan.target_language

        if _cached_workers is None or plan.plan_id != _cached_plan_id:
            _cached_workers = await registry.list_all()
            _cached_plan_id = plan.plan_id
        workers = _cached_workers

        selected: RegisteredWorker | None = None
        for w in workers:
            caps = w.capabilities
            if caps.worker_type != step.type:
                continue
            if not _language_matches(step.type, caps, src, dst):
                continue
            selected = w
            break

        if selected is None:
            msg = f"No worker for {step.type.value} ({src} → {dst})"
            raise WorkerError(msg)

        chapter_id = step.payload.get("chapter_id", "")
        job = Job(
            job_id=f"{plan.job_id}-{step.step_id}",
            job_type=step.type,
            payload=step.payload,
            chapter_id=str(chapter_id) if chapter_id is not None else "",
        )

        logger.info("Dispatching %s to %s", step.step_id, selected.worker_id)
        worker_instance = _worker_instances.get(selected.worker_id)
        if worker_instance is None:
            worker_instance = factory(selected)
            _worker_instances[selected.worker_id] = worker_instance
        return await worker_instance.execute(job)

    return handler
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
    local_handlers=self._local_handlers,
)
```

(If the orchestrator uses different parameter names, match the local
variable — the contract is `step_cache=<StepCache instance>`.)

- [ ] **Step 4: Run the existing test suite**

```bash
uv run pytest tests/shell/test_orchestrator.py tests/shell/test_step_handler.py -v
```

Expected: PASS (no behavior change for non-ASR tests; the new param is
keyword-only with a default).

- [ ] **Step 5: Lint + type-check + commit**

```bash
uv run ruff check src/acheron/shell/step_handler.py src/acheron/shell/orchestrator.py
uv run mypy src/acheron/shell/step_handler.py
git add src/acheron/shell/step_handler.py src/acheron/shell/orchestrator.py
git commit -m "feat(shell): thread step_cache through default_worker_factory to HttpWorker"
```

---

## Final Validation

After all 6 tasks are complete and committed:

```bash
just validate
```

Expected: all sub-targets (lint-strict, lint-imports, mypy, basedpyright,
pytest) green. The 8a test suite + Sub-plan 1's tests must still pass.

## Adversarial Review (post-implementation)

Once `just validate` is green, dispatch a fresh-context reviewer subagent
with the rubric at the top of this sub-plan. The reviewer reads:

- This sub-plan
- The parent spec at `docs/superpowers/specs/2026-06-23-layer8b-asr-worker-design.md`
- The 8a orchestrator-transports plan at `docs/superpowers/plans/2026-06-22-layer8a-orchestrator-transports.md`

…and produces findings in the same theme-keyed story format the 8a review
used (`docs/code_review/`). Fix any open CRITICAL / HIGH / MEDIUM findings
before starting Sub-plan 3.
