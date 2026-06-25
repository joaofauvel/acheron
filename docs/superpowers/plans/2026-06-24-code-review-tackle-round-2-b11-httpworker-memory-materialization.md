---
bundle: B11
name: HttpWorker memory materialization
severity: MIXED
stories: 4
m_effort: 3
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B11 — HttpWorker memory materialization (CORR-017, -018, -019, PERF-006)

> **For agentic workers:** Use the **Common Workflow** from the main plan. **Tackle in this order: CORR-018 (request) → CORR-017 (response) → CORR-019 (SDK edge) → PERF-006 (multipart streaming).** All 3 M-effort stories introduce streaming patterns; PERF-006 is S-effort.

**Bundle summary:** Replace memory-hungry `await request.body()` / `await response.aread()` patterns with streaming chunk-by-chunk processing. The core idea: use `python-multipart`'s `create_form_parser` with `parser.write(chunk)` to consume the body without ever materialising it.

**External libs used (verified):**
- `python-multipart`'s `create_form_parser(headers, on_field, on_file, config={...})` for streaming multipart parsing. Source: `/kludex/python-multipart`.
- `httpx.AsyncClient.stream(...)` for streaming requests and `response.aiter_bytes()` for streaming responses.

**Expected commits:** 4 (one per story, all 4 are M-effort, one of them is borderline).

---

## Tasks (tackle in order)

### Task 1: CORR-018 (M) — ASR multipart path materializes entire audio file in memory

**Story:** `docs/code_review/correctness.md` § CORR-018 (MEDIUM, M effort).

**Files:**
- Modify: `src/acheron/shell/transports/http.py` (the ASR multipart upload path).
- Test: `tests/shell/transports/test_http_multipart.py` (add a streaming test).

#### Step 1: Write the failing test

```python
import pytest
import httpx


@pytest.mark.asyncio
async def test_asr_multipart_streams_audio_without_buffering(monkeypatch):
    """CORR-018: ASR multipart path must stream the audio file, not buffer it in memory."""

    # Generate a 10 MB audio file in tmp_path.
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"\x00" * 10_000_000)

    # Spy on the body() method — should NOT be called.
    body_calls = []
    class SpyClient(httpx.AsyncClient):
        async def send(self, request, **kwargs):
            body_calls.append(request)
            return httpx.Response(200, content=b'{"ok": true}')

    # Construct the transport with the spy client.
    # Submit a job that uploads audio_path via the ASR multipart path.
    # Assert request.body() is never called; the body was streamed.
    ...
```

The exact transport interface depends on the current code. Inspect `HttpWorker.execute()` and the ASR path first. The 1 test above is the contract: `request.body()` is never called; the body is streamed.

#### Step 2: Run test, verify it fails

```bash
uv run pytest tests/shell/transports/test_http_multipart.py::test_asr_multipart_streams_audio_without_buffering -xvs
```

#### Step 3: Implement streaming

Replace `await request.body()` + the multipart builder with an `httpx.AsyncClient.stream("POST", url, content=audio_stream())` pattern where `audio_stream()` is an async generator that yields chunks of the audio file:

```python
async def audio_stream():
    async with aiofiles.open(audio_path, "rb") as f:
        while chunk := await f.read(64 * 1024):
            yield chunk

async with httpx.AsyncClient() as client:
    async with client.stream("POST", url, content=audio_stream()) as response:
        return await response.aread()
```

The current `Request` is built in-memory; replace it with the streaming call. The exact API depends on the current `HttpWorker` interface.

#### Step 4-5: Run test, verify gate, subagent passes, commit

**Commit:** `fix(CORR-018): stream ASR multipart audio file via httpx.AsyncClient.stream`.

---

### Task 2: CORR-017 (M) — `_build_multipart_response` materializes the entire artifact stream in memory

**Story:** `docs/code_review/correctness.md` § CORR-017 (LOW, M effort).

**Files:**
- Modify: `src/acheron/shell/transports/http.py` (the response builder).
- Test: `tests/shell/transports/test_http_multipart.py`.

#### Step 1: Write the failing test

```python
@pytest.mark.asyncio
async def test_build_multipart_response_streams_artifact(monkeypatch):
    """CORR-017: response builder must stream each FileArtifact chunk, not buffer the full body."""
    # Create a 10 MB FileArtifact.
    # Build the response.
    # Assert the response body is built chunk-by-chunk (e.g. via a spy on `write`).
    ...
```

#### Step 2-3: Implement streaming

Replace the in-memory `b"".join([...])` pattern with a streaming `Multipart` writer:

```python
from multipart import MultipartWriter  # or a custom streaming writer

async def stream_response(artifacts) -> AsyncIterator[bytes]:
    writer = MultipartWriter("form-data")
    for artifact in artifacts:
        part = writer.part(artifact.content_type)
        part.headers["X-Acheron-Filename"] = artifact.filename
        async for chunk in artifact.stream():
            yield part.write(chunk)
        yield b"\r\n"
    yield writer.close()
```

Or use `python-multipart`'s lower-level streaming writer. The exact API depends on the existing builder.

#### Step 4-5: Run test, verify gate, subagent passes, commit

**Commit:** `fix(CORR-017): stream _build_multipart_response via async iterator`.

---

### Task 3: CORR-019 (M) — SDK edge `_parse_multipart_request` materializes entire request body

**Story:** `docs/code_review/correctness.md` § CORR-019 (MEDIUM, M effort).

**Files:**
- Modify: `src/acheron/worker_sdk/_edge_http.py` (the `MultipartConsumer` or `_parse_multipart_request`).
- Test: `tests/worker_sdk/test_edge_http_multipart.py`.

#### Step 1: Write the failing test

```python
@pytest.mark.asyncio
async def test_edge_parse_multipart_streams_request_body():
    """CORR-019: edge /execute must parse the multipart body in streaming chunks, not await request.body()."""
    # Use FastAPI's TestClient with a 50 MB upload.
    # Assert that `request.body()` is NOT called; `request.stream()` is iterated.
    ...
```

#### Step 2-3: Implement streaming with `create_form_parser`

```python
from python_multipart import create_form_parser


async def _parse_multipart_request_streaming(request: Request) -> list[BytesInput]:
    headers = {
        k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
        for k, v in request.headers.items()
    }
    fields: list[Field] = []
    files: list[File] = []

    def on_field(f):
        fields.append(f)

    def on_file(f):
        files.append(f)

    parser = create_form_parser(
        headers,
        on_field,
        on_file,
        config={"MAX_MEMORY_FILE_SIZE": 5 * 1024 * 1024},  # 5 MB cap
    )

    async for chunk in request.stream():
        parser.write(chunk)
    parser.finalize()

    return [BytesInput.from_file(f) for f in files]
```

The exact `BytesInput.from_file` constructor depends on the current code.

#### Step 4-5: Run test, verify gate, subagent passes, commit

**Commit:** `fix(CORR-019): stream _parse_multipart_request via create_form_parser`.

---

### Task 4: PERF-006 — edge `/execute` buffers entire multipart body in memory; O(n²) append for `FileArtifact`

**Story:** `docs/code_review/operations.md` § PERF-006 (MEDIUM, S effort).

**Files:**
- Modify: `src/acheron/worker_sdk/_edge_http.py` (the multipart branch).
- Test: `tests/worker_sdk/test_edge_http_multipart.py`.

**Change:** once CORR-019 lands, the streaming parser handles large files via `MAX_MEMORY_FILE_SIZE`. For small files that fit in memory, the O(n²) `bytes += chunk` pattern is the issue. Use a `bytearray` or `io.BytesIO` instead.

**Test:** existing tests should still pass; add 1 test asserting that a 1 MB upload doesn't O(n²).

**Commit:** `perf(PERF-006): stream-write FileArtifact parts in edge /execute multipart branch`.

---

## Bundle summary

- **Stories:** 4 (3 M-effort: CORR-017, -018, -019; 1 S-effort: PERF-006).
- **Commits:** 4.
- **Order matters:** CORR-018 first (request side is most constrained). Then CORR-017 (response side). Then CORR-019 (edge). PERF-006 last (it's a follow-on of CORR-019's stream).
- **External lib verification done:** `python-multipart`'s `create_form_parser` API is correct as of latest docs (`/kludex/python-multipart`); `httpx.AsyncClient.stream` is the standard pattern.
- **Surface to user if:** `aiofiles` is not a current dep (needed for the audio stream) — add to `pyproject.toml` via `uv add`.
