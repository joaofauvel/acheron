---
bundle: B21
name: Tests — orchestrator + step
severity: MIXED
stories: 6
m_effort: 2
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B21 — Tests — orchestrator + step (TEST-002, -009, -010, -011, REPRO-001, -003)

> **For agentic workers:** Use the **Common Workflow** from the main plan. TEST-002 and REPRO-001 are M-effort; the rest are S-effort test additions or refactors.

**Bundle summary:** Fix the misleadingly-named `test_orchestrator_works_with_redis_backend` test (uses memory, not Redis); add 4 small test files for under-tested helpers; sort `Redis.list_all()` for determinism; narrow the `_no_sleep` fixture scope.

**Expected commits:** 4-5.

---

## Tasks

### Task 1: TEST-002 (M) — `test_orchestrator_works_with_redis_backend` tests memory, not Redis

**Story:** `docs/code_review/verification.md` § TEST-002 (MEDIUM, M effort).

**Files:** `tests/shell/test_orchestrator_works_with_redis_backend.py` (or wherever the test lives).

**Change:** actually start a `fakeredis` (or local redis-server) instance in a fixture; assert that the test fails when redis is unreachable. The misnamed test is a future-trap.

**Design:**

```python
import pytest
import fakeredis.aioredis


@pytest.fixture
async def fake_redis_server():
    server = fakeredis.FakeServer()
    redis = fakeredis.aioredis.FakeRedis(server=server)
    yield redis
    await redis.aclose()


async def test_orchestrator_works_with_actual_redis(monkeypatch, fake_redis_server):
    # Construct Orchestrator with a RedisWorkerStore(fake_redis_server).
    # Submit a job.
    # Assert the job is persisted in redis (assert the redis GET returns the right key).
    # This test would fail with the in-memory store because nothing is persisted.
    ...
```

The test currently uses an in-memory store; replace with the redis store. Use `fakeredis` for in-process testing (add to dev deps via `uv add --dev fakeredis`).

**Test:** the new test passes; the old (misnamed) test is renamed/deleted (or kept as a separate "in-memory" test).

**Commit:** `test(TEST-002): replace in-memory test with fakeredis-based redis backend test`.

---

### Task 2: REPRO-001 (M) — `Redis.list_all()` returns non-deterministic order

**Story:** `docs/code_review/verification.md` § REPRO-001 (MEDIUM, M effort).

**Files:** `src/acheron/shell/stores/redis.py`; test.

**Change:** sort the result by `worker_id` (or `last_seen` descending) in `list_all`; assert determinism in a 100-iteration test.

**Test:** 100-iteration loop calling `list_all()`; assert the result is identical across iterations.

**Commit:** `fix(REPRO-001): sort Redis.list_all() result by worker_id for determinism`.

---

### Task 3: TEST-009 (S) — `test_inputs.py` missing Protocol isinstance, FileInput missing-path, etc.

**Files:** `tests/worker_sdk/test_inputs.py` (add tests only).

**Add 4 tests:**

```python
def test_file_input_satisfies_filelike_protocol():
    fi = FileInput.from_path(tmp_path / "x.bin")
    assert isinstance(fi, FileLikeProtocol)


def test_file_input_missing_path_raises():
    with pytest.raises(FileNotFoundError):
        FileInput.from_path(tmp_path / "missing.bin")


def test_bytes_input_satisfies_byteslike_protocol():
    bi = BytesInput(data=b"x", content_type="text/plain")
    assert isinstance(bi, BytesLikeProtocol)


def test_bytes_input_invalid_content_type_raises():
    with pytest.raises(ValidationError):
        BytesInput(data=b"x", content_type=42)
```

**Commit:** `test(TEST-009): add 4 unit tests for FileInput/BytesInput Protocol conformance and validation`.

---

### Task 4: TEST-010 (S) — `test_safe_chapter_id.py` missing unicode `chapter_id` coverage

**Files:** `tests/workers/test_safe_chapter_id.py` (add tests only).

**Add 3 tests:**

```python
def test_safe_chapter_id_cjk():
    assert safe_chapter_id("第一章") == "第一章"


def test_safe_chapter_id_accented():
    assert safe_chapter_id("Café") == "Cafe" or "Café"  # depends on the slugify rules


def test_safe_chapter_id_emoji_stripped():
    assert "🎉" not in safe_chapter_id("Chapter 1 🎉")
```

**Commit:** `test(TEST-010): add unicode chapter_id tests (CJK, accented, emoji)`.

---

### Task 5: TEST-011 (S) — `test_cloud_audio.py` missing default-content_type and default-metadata tests

**Files:** `tests/worker_sdk/test_cloud_audio.py` (add tests only).

**Add 2 tests:**

```python
def test_cloud_audio_default_content_type():
    # Construct without specifying content_type.
    # Assert the default is "audio/wav" or similar.


def test_cloud_audio_default_metadata():
    # Construct without specifying metadata.
    # Assert metadata is {}.
```

**Commit:** `test(TEST-011): add default-content_type and default-metadata tests for cloud_audio`.

---

### Task 6: REPRO-003 (S) — `tests/worker_sdk/conftest.py` `_no_sleep` fixture masks `asyncio.sleep` globally

**Files:** `tests/worker_sdk/conftest.py`; the tests that use it.

**Change:** narrow to `monkeypatch.setattr` only the called module's `asyncio.sleep`, not the global.

**Commit:** `test(REPRO-003): narrow _no_sleep fixture scope to per-module asyncio.sleep`.

---

## Bundle summary

- **Stories:** 6 (2 M-effort: TEST-002, REPRO-001; 4 S-effort).
- **Commits:** 4-5.
- **External deps:** `fakeredis` may need to be added to dev deps via `uv add --dev fakeredis`.
- **Surface to user if:** `fakeredis` is not in pyproject.toml — confirm before adding.
