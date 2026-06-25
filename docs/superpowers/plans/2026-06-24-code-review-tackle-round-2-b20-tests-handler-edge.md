---
bundle: B20
name: Tests — handler + edge coverage
severity: MIXED
stories: 5
m_effort: 2
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B20 — Tests — handler + edge coverage (TEST-005, -006, -014, -015, DATA-007)

> **For agentic workers:** Use the **Common Workflow** from the main plan. TEST-014 and TEST-015 are M-effort and need full TDD with the per-test design below; TEST-005, -006, DATA-007 are S-effort test-only additions.

**Bundle summary:** Add unit tests for under-tested helper functions, the translategemma handler's edge cases, and the new `tls.py` module.

**Expected commits:** 3-4.

---

## Tasks

### Task 1: TEST-014 (M) — `workers/translategemma/tests/test_handler.py` does not cover the model.generate error path, partial-success, or pad_token_id init

**Story:** `docs/code_review/verification.md` § TEST-014 (MEDIUM, M effort).

**Files:** `workers/translategemma/tests/test_handler.py`.

**Add 4 tests:**

```python
def test_handler_oom_in_generate_raises(monkeypatch):
    """When model.generate raises torch.cuda.OutOfMemoryError, _translate_batch should propagate it (or surface as WorkerError if B15's CORR-029 is in)."""
    # Mock model.generate to raise torch.cuda.OutOfMemoryError("CUDA OOM").
    # Call _translate_batch with 1 chunk.
    # Assert the right exception is raised.


def test_handler_partial_success_returns_completed_chunks(monkeypatch):
    """B15's CORR-029 partial-success behaviour: 1 chunk fails, others succeed; assert the success rate and the failed chunk index."""
    # Mock model.generate to raise on the 2nd chunk.
    # Call _translate_batch with 3 chunks.
    # Assert: 2 succeeded, 1 failed (chunk id + error message), success_rate > threshold.


def test_handler_pad_token_id_init_when_missing(monkeypatch):
    """When the tokenizer has no pad_token, _translate_batch should set pad_token = eos_token (or raise if the model needs it)."""
    # Mock the tokenizer with pad_token=None.
    # Call _translate_batch.
    # Assert pad_token_id is set on the tokenizer (or the right error is raised).


def test_handler_empty_input_returns_empty(monkeypatch):
    """When the chunks list is empty, _translate_batch should return an empty result without calling model.generate."""
    # Mock model.generate to track calls.
    # Call _translate_batch with no chunks.
    # Assert model.generate was NOT called and the result has no outputs.
```

**Test setup:** each test mocks `self._model` and `self._processor` with simple fakes that satisfy the B17's `_ModelProto` and `_ProcessorProto` Protocols.

**Commit:** `test(TEST-014): add 4 edge-case tests for translategemma handler (OOM, partial-success, pad_token init, empty input)`.

---

### Task 2: TEST-015 (M) — `src/acheron/tls.py` (114 lines) has no direct unit tests

**Story:** `docs/code_review/verification.md` § TEST-015 (MEDIUM, M effort).

**Files:** `tests/test_tls_unit.py` (new).

**Add 8 unit tests:**

```python
import ssl
import pytest
from pathlib import Path
from acheron.tls import (
    _require_pair, uvicorn_ssl_kwargs, resolve_ca_path,
    grpc_server_credentials, grpc_channel, _is_https_url, _parse_tls_env,
)


class TestTLSUnit:
    """TEST-015: direct unit tests for acheron.tls module."""

    def test_require_pair_passes_when_both_present(self, tmp_path):
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        cert.write_text("-----BEGIN CERTIFICATE-----\n...")
        key.write_text("-----BEGIN PRIVATE KEY-----\n...")
        ctx = _require_pair(cert, key)  # returns ssl.SSLContext
        assert isinstance(ctx, ssl.SSLContext)

    def test_require_pair_raises_on_missing_cert(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="cert"):
            _require_pair(tmp_path / "missing.pem", tmp_path / "key.pem")

    def test_require_pair_raises_on_missing_key(self, tmp_path):
        cert = tmp_path / "cert.pem"
        cert.write_text("dummy")
        with pytest.raises(FileNotFoundError, match="key"):
            _require_pair(cert, tmp_path / "missing.pem")

    def test_uvicorn_ssl_kwargs_returns_correct_keys(self, tmp_path):
        # Set up cert + key.
        ctx = _require_pair(cert, key)
        kwargs = uvicorn_ssl_kwargs(cert, key)
        assert "ssl_certfile" in kwargs
        assert "ssl_keyfile" in kwargs

    def test_resolve_ca_path_returns_absolute(self, tmp_path, monkeypatch):
        ca = tmp_path / "ca.pem"
        ca.write_text("dummy")
        monkeypatch.setenv("ACHERON_TLS_CA", str(ca))
        assert resolve_ca_path() == ca

    def test_grpc_server_credentials_creates_object(self, tmp_path):
        # ... uses grpc from grpc-tools
        creds = grpc_server_credentials(cert, key)
        assert creds is not None

    def test_grpc_channel_for_https_url(self):
        channel = grpc_channel("https://localhost:50051")
        assert channel is not None

    def test_grpc_channel_for_http_url_uses_insecure(self):
        channel = grpc_channel("http://localhost:50051")
        assert channel is not None  # insecure channel
```

The exact API of `acheron.tls` may differ; inspect the module first. Adjust the tests to match the actual function signatures.

**Commit:** `test(TEST-015): add 8 direct unit tests for acheron.tls module`.

---

### Task 3: TEST-005 (S) — `_metadata_str` helper in `health.py` has no direct unit tests

**Files:** `tests/shell/test_health.py` (add tests only).

**Add 3 unit tests:**

```python
def test_metadata_str_empty():
    assert _metadata_str({}) == ""


def test_metadata_str_single_pair():
    assert _metadata_str({"key": "value"}) == "key=value"


def test_metadata_str_multi_pair():
    result = _metadata_str({"a": "1", "b": "2"})
    # Order is not guaranteed; check both pairs are present
    assert "a=1" in result
    assert "b=2" in result
```

**Commit:** `test(TEST-005): add 3 direct unit tests for _metadata_str`.

---

### Task 4: TEST-006 (S) — `HuggingFaceHealthProvider.check_status` has untested `str` and `else` branches

**Files:** `tests/shell/test_health_providers.py` (add tests only).

**Add 2 unit tests:**

```python
def test_hf_provider_str_branch():
    # Mock the response to be a JSON string (str, not dict).
    # Assert the provider returns the right status.


def test_hf_provider_else_branch():
    # Mock the response to be neither dict nor str.
    # Assert the provider returns OFFLINE with a log line.
```

**Commit:** `test(TEST-006): add 2 direct unit tests for HuggingFaceHealthProvider str and else branches`.

---

### Task 5: DATA-007 (S) — `_runpod_client` output.artifacts-not-list path and `FileArtifact` stream edge cases lack direct tests

**Files:** `tests/worker_sdk/test_runpod_client.py`; `tests/worker_sdk/test_artifacts.py` (add tests only).

**Add 5 unit tests:**

```python
# In test_runpod_client.py:
def test_runpod_artifacts_not_list_raises(monkeypatch):
    """The 'artifacts' field is not a list → WorkerError."""
    fake = _FakeEndpoints(output={"artifacts": "not-a-list"})
    ...
    with pytest.raises(WorkerError, match="must be a list"):
        await client.run(payload={})


# In test_artifacts.py:
def test_file_artifact_stream_empty_file(tmp_path):
    # Empty file → stream yields nothing.
    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    fa = FileArtifact.from_path(empty)
    chunks = [c async for c in fa.stream()]
    assert chunks == []


def test_file_artifact_stream_one_byte_file(tmp_path):
    # 1-byte file → one read < 64 KiB.
    small = tmp_path / "small.bin"
    small.write_bytes(b"x")
    fa = FileArtifact.from_path(small)
    chunks = [c async for c in fa.stream()]
    assert chunks == [b"x"]


def test_file_artifact_stream_missing_path(tmp_path):
    # Non-existent path → FileNotFoundError propagates.
    missing = tmp_path / "missing.bin"
    fa = FileArtifact.from_path(missing)
    with pytest.raises(FileNotFoundError):
        async for _ in fa.stream():
            pass


def test_runpod_artifacts_list_with_missing_key(monkeypatch):
    """The output dict has an 'artifacts' key but no 'status' key → OK (back-compat)."""
    fake = _FakeEndpoints(output={"artifacts": [{"filename": "x.wav", "data": "..."}]})
    ...
    result = await client.run(payload={})
    assert len(result.artifacts) == 1
```

**Commit:** `test(DATA-007): add 5 direct unit tests for _runpod_client artifacts-not-list path and FileArtifact stream edge cases`.

---

## Bundle summary

- **Stories:** 5 (2 M-effort: TEST-014, TEST-015; 3 S-effort).
- **Commits:** 3-4 (TEST-014, TEST-015 are M-effort and 1 each; the 3 S-effort test additions can share a commit if they're in related test files).
- **Order matters:** TEST-014 may depend on B15's CORR-029 (partial-success). Land B15 first, OR land TEST-014 with the B15 design in mind.
- **Surface to user if:** `acheron.tls`'s function signatures differ from the test sketch (high likelihood).
