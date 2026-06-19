# Layer 9b-ii Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the synchronous `redis.Redis` client inside `RedisWorkerStore` and `RedisJobStore` with `redis.asyncio.Redis`, so all Redis I/O is genuinely non-blocking.

**Architecture:** `__init__` does no I/O; `async def connect()` is the canonical startup check called from `Orchestrator.start()`. The ABC exposes `connect()` as a concrete no-op default; InMemory stores inherit it for free, Redis stores override it with an `await ping()`. `close()` calls `await self._redis.aclose()`. The factory stays sync.

**Tech Stack:** Python 3.14, `redis~=7.0` (asyncio client), pytest-asyncio (strict mode).

---

## File Map

| File | Responsibility |
|---|---|
| `src/acheron/shell/stores/base.py` | ABCs with new concrete `connect()` default |
| `src/acheron/shell/stores/redis.py` | Both stores switched to `redis.asyncio.Redis`; `connect()` + `aclose()` |
| `src/acheron/shell/orchestrator.py` | `start()` awaits `connect()` on both stores |
| `tests/shell/stores/conftest.py` | `redis_url` fixture uses `redis.asyncio` for FLUSHDB |
| `tests/shell/stores/test_redis_worker_store.py` | Async `store` fixture; new tests; renamed fail-fast test |
| `tests/shell/stores/test_redis_job_store.py` | Async `store` fixture; renamed fail-fast test |
| `docs/superpowers/specs/2026-06-18-pipeline-streaming-design.md` | Sync the 9b-ii entry with the actual implementation |

---

## Task 1: Add `connect()` to the store ABCs

**Files:**
- Modify: `src/acheron/shell/stores/base.py`

- [ ] **Step 1: Add concrete `connect()` to `WorkerStore`**

In `src/acheron/shell/stores/base.py`, add a concrete `connect()` method to `WorkerStore` immediately after the `max_failures` class attribute (around line 17):

```python
class WorkerStore(ABC):
    """Persistent or in-memory store of registered workers and their health state."""

    max_failures: int = 3

    async def connect(self) -> None:
        """Verify the backend is reachable. No-op for stores without a remote backend."""

    @abstractmethod
    async def register(
        self,
        worker_id: str,
        endpoint: str,
        transport: str,
        capabilities: WorkerCapabilities,
        metadata: dict[str, object] | None = None,
    ) -> None:
        ...
```

- [ ] **Step 2: Add concrete `connect()` to `JobStore`**

Add a matching `connect()` to `JobStore` (after the class docstring, before the first `@abstractmethod put`):

```python
class JobStore(ABC):
    """Persistent or in-memory store of tracked jobs."""

    async def connect(self) -> None:
        """Verify the backend is reachable. No-op for stores without a remote backend."""

    @abstractmethod
    async def put(self, job: TrackedJob) -> None:
        ...
```

- [ ] **Step 3: Run the ABC contract tests to confirm no regressions**

Run: `uv run pytest tests/shell/stores/test_stores_async.py -v --no-cov`
Expected: PASS (no behavior change for InMemory — it inherits the no-op).

- [ ] **Step 4: Commit**

```bash
git add src/acheron/shell/stores/base.py
git commit -m "feat(stores): add concrete connect() default to store ABCs"
```

---

## Task 2: Convert `RedisWorkerStore` and `RedisJobStore` to `redis.asyncio`

**Files:**
- Modify: `src/acheron/shell/stores/redis.py`

- [ ] **Step 1: Switch the import**

In `src/acheron/shell/stores/redis.py`, line 9:
```python
import redis
```
becomes:
```python
import redis.asyncio
```

- [ ] **Step 2: Rewrite `RedisWorkerStore.__init__` to do no I/O**

Replace the existing `__init__` (lines 266-268):

```python
    def __init__(self, redis_url: str) -> None:
        self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
        self._redis.ping()
```

with:

```python
    def __init__(self, redis_url: str) -> None:
        self._redis = redis.asyncio.Redis.from_url(redis_url, decode_responses=True)
```

(Constructor must not perform network I/O. The pool is lazy.)

- [ ] **Step 3: Add `connect()` and switch `close()` to `aclose()` on `RedisWorkerStore`**

Replace the `close()` method (lines 270-272):

```python
    async def close(self) -> None:
        """Close the underlying Redis connection pool."""
        self._redis.close()
```

with:

```python
    async def connect(self) -> None:
        """Verify the Redis server is reachable. Idempotent."""
        await self._redis.ping()

    async def close(self) -> None:
        """Close the underlying Redis connection pool."""
        await self._redis.aclose()
```

Also update the class docstring (lines 258-264):

```python
class RedisWorkerStore(WorkerStore):
    """Redis-backed worker store. Survives orchestrator restarts.

    Requires awaiting connect() before use.
    """
```

- [ ] **Step 4: Await every call in `RedisWorkerStore`**

Update the body of each method to use `await`. Concrete edits:

`register` (lines 274-287):
```python
    async def register(
        self,
        worker_id: str,
        endpoint: str,
        transport: str,
        capabilities: WorkerCapabilities,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Register a new worker or re-register an existing one."""
        fields = _worker_fields(endpoint, transport, capabilities, dict(metadata or {}))
        async with self._redis.pipeline(transaction=True) as pipe:
            await pipe.hset(_WORKER_KEY.format(worker_id=worker_id), mapping=fields)
            await pipe.sadd(_WORKERS_SET, worker_id)
            await pipe.execute()
```

`unregister` (lines 289-294):
```python
    async def unregister(self, worker_id: str) -> None:
        """Remove a worker from the store."""
        async with self._redis.pipeline(transaction=True) as pipe:
            await pipe.srem(_WORKERS_SET, worker_id)
            await pipe.delete(_WORKER_KEY.format(worker_id=worker_id))
            await pipe.execute()
```

`get` (lines 296-301):
```python
    async def get(self, worker_id: str) -> RegisteredWorker | None:
        """Look up a worker by ID."""
        fields: dict[str, str] = await self._redis.hgetall(_WORKER_KEY.format(worker_id=worker_id))
        if not fields:
            return None
        return _deserialize_worker(worker_id, fields)
```

`list_all` (lines 303-313):
```python
    async def list_all(self) -> tuple[RegisteredWorker, ...]:
        """Return all registered workers."""
        ids: set[str] = await self._redis.smembers(_WORKERS_SET)
        if not ids:
            return ()
        async with self._redis.pipeline(transaction=False) as pipe:
            for wid in ids:
                await pipe.hgetall(_WORKER_KEY.format(worker_id=wid))
            results = await pipe.execute()
        return tuple(_deserialize_worker(wid, fields) for wid, fields in zip(ids, results, strict=True) if fields)
```

(`find_by_type` and `find_by_language` already `await self.list_all()` — no change.)

`record_health_failure` (lines 328-338):
```python
    async def record_health_failure(self, worker_id: str) -> bool:
        """Record a failed health check. Returns True if the worker was removed."""
        key = _WORKER_KEY.format(worker_id=worker_id)
        if not await self._redis.exists(key):
            return False
        new_count: int = await self._redis.hincrby(key, "consecutive_failures", 1)
        await self._redis.hset(key, "last_health_check", str(time.time()))
        if new_count >= self.max_failures:
            await self.unregister(worker_id)
            return True
        return False
```

`record_health_success` (lines 340-346):
```python
    async def record_health_success(self, worker_id: str) -> None:
        """Record a successful health check, resetting the failure counter."""
        key = _WORKER_KEY.format(worker_id=worker_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            await pipe.hset(key, "consecutive_failures", "0")
            await pipe.hset(key, "last_health_check", str(time.time()))
            await pipe.execute()
```

- [ ] **Step 5: Apply the same changes to `RedisJobStore`**

Replace the `RedisJobStore` class (lines 349-386) with:

```python
class RedisJobStore(JobStore):
    """Redis-backed job store. Survives orchestrator restarts.

    Requires awaiting connect() before use.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis = redis.asyncio.Redis.from_url(redis_url, decode_responses=True)

    async def connect(self) -> None:
        """Verify the Redis server is reachable. Idempotent."""
        await self._redis.ping()

    async def close(self) -> None:
        """Close the underlying Redis connection pool."""
        await self._redis.aclose()

    async def put(self, job: TrackedJob) -> None:
        """Store or update a tracked job."""
        async with self._redis.pipeline(transaction=True) as pipe:
            await pipe.set(_JOB_KEY.format(job_id=job.job_id), _serialize_job(job))
            await pipe.sadd(_JOBS_SET, job.job_id)
            await pipe.execute()

    async def get(self, job_id: str) -> TrackedJob | None:
        """Retrieve a tracked job by ID."""
        blob: str | None = await self._redis.get(_JOB_KEY.format(job_id=job_id))
        if blob is None:
            return None
        return _deserialize_job(blob)

    async def list_all(self) -> tuple[TrackedJob, ...]:
        """Return all tracked jobs."""
        ids: set[str] = await self._redis.smembers(_JOBS_SET)
        if not ids:
            return ()
        async with self._redis.pipeline(transaction=False) as pipe:
            for jid in ids:
                await pipe.get(_JOB_KEY.format(job_id=jid))
            results = await pipe.execute()
        return tuple(_deserialize_job(blob) for blob in results if blob is not None)
```

- [ ] **Step 6: Run mypy to confirm there are no type errors**

Run: `just type-check 2>&1 | tail -30`
Expected: PASS. If any `# type: ignore` comments remain after this change, remove them — `redis.asyncio.Redis` types are correct.

- [ ] **Step 7: Commit**

```bash
git add src/acheron/shell/stores/redis.py
git commit -m "feat(stores): switch Redis stores to redis.asyncio.Redis"
```

---

## Task 3: Update the testcontainers `redis_url` fixture to use `redis.asyncio`

**Files:**
- Modify: `tests/shell/stores/conftest.py`

- [ ] **Step 1: Switch the conftest imports and fixture**

Replace the contents of `tests/shell/stores/conftest.py` with:

```python
"""Shared fixtures for store tests."""

from __future__ import annotations

import asyncio

import pytest
import redis.asyncio
from testcontainers.redis import RedisContainer


@pytest.fixture(scope="session")
def redis_container() -> RedisContainer:
    container = RedisContainer("redis:7-alpine")
    container.start()
    return container


@pytest.fixture
def redis_url(redis_container: RedisContainer) -> str:
    """Yield a Redis URL and FLUSHDB the database before each test."""
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    url = f"redis://{host}:{port}"

    async def _flush() -> None:
        client = redis.asyncio.Redis.from_url(url, decode_responses=True)
        try:
            await client.flushdb()
        finally:
            await client.aclose()

    asyncio.run(_flush())
    return url
```

(We use `asyncio.run` because the fixture is sync and the testcontainers container needs to be flushed before the test loop starts; an async fixture would work too but the FLUSHDB is a one-shot prep, so sync is simpler.)

- [ ] **Step 2: Run an existing Redis test to confirm the fixture still works**

Run: `uv run pytest tests/shell/stores/test_redis_worker_store.py::TestRegister::test_register_and_get --no-cov -v 2>&1 | tail -20`
Expected: this will fail because the `store` fixture in the test file is still sync and constructs `RedisWorkerStore(redis_url)` which no longer pings. That's expected — we fix the test fixtures in the next task.

- [ ] **Step 3: Commit**

```bash
git add tests/shell/stores/conftest.py
git commit -m "test(stores): use redis.asyncio for FLUSHDB in testcontainers fixture"
```

---

## Task 4: Update `test_redis_worker_store.py` to async fixtures and rename the fail-fast test

**Files:**
- Modify: `tests/shell/stores/test_redis_worker_store.py`

- [ ] **Step 1: Add `pytest_asyncio` import and convert the `store` fixture to async**

Add `import pytest_asyncio` after the `import pytest` line at the top. Replace the `store` fixture (lines 23-25):

```python
@pytest.fixture
def store(redis_url: str) -> RedisWorkerStore:
    return RedisWorkerStore(redis_url)
```

with:

```python
@pytest_asyncio.fixture
async def store(redis_url: str) -> RedisWorkerStore:
    s = RedisWorkerStore(redis_url)
    await s.connect()
    try:
        yield s
    finally:
        await s.close()
```

- [ ] **Step 2: Replace the `TestFailFast` class with an async version**

Replace lines 110-113:

```python
class TestFailFast:
    def test_unreachable_redis_raises_on_init(self) -> None:
        with pytest.raises(redis.RedisError):
            RedisWorkerStore("redis://localhost:1")
```

with:

```python
class TestFailFast:
    @pytest.mark.asyncio
    async def test_unreachable_redis_raises_on_connect(self) -> None:
        from redis.exceptions import ConnectionError as RedisConnectionError

        store = RedisWorkerStore("redis://localhost:1")
        with pytest.raises((RedisConnectionError, redis.RedisError)):
            await store.connect()
```

(`redis.exceptions.ConnectionError` is the specific exception, but we accept the broader `redis.RedisError` to avoid flakiness across redis-py versions.)

- [ ] **Step 3: Run the test file to confirm**

Run: `uv run pytest tests/shell/stores/test_redis_worker_store.py --no-cov -v 2>&1 | tail -30`
Expected: all tests PASS (the existing tests already use `await` on store methods).

- [ ] **Step 4: Commit**

```bash
git add tests/shell/stores/test_redis_worker_store.py
git commit -m "test(stores): async fixtures and renamed fail-fast test for RedisWorkerStore"
```

---

## Task 5: Update `test_redis_job_store.py` to async fixtures and rename the fail-fast test

**Files:**
- Modify: `tests/shell/stores/test_redis_job_store.py`

- [ ] **Step 1: Add `pytest_asyncio` import and convert the `store` fixture to async**

Add `import pytest_asyncio` after the `import pytest` line at the top. Replace the `store` fixture (lines 77-79):

```python
@pytest.fixture
def store(redis_url: str) -> RedisJobStore:
    return RedisJobStore(redis_url)
```

with:

```python
@pytest_asyncio.fixture
async def store(redis_url: str) -> RedisJobStore:
    s = RedisJobStore(redis_url)
    await s.connect()
    try:
        yield s
    finally:
        await s.close()
```

- [ ] **Step 2: Replace the `TestFailFast` class with an async version**

Replace lines 185-188:

```python
class TestFailFast:
    def test_unreachable_redis_raises_on_init(self) -> None:
        with pytest.raises(redis.RedisError):
            RedisJobStore("redis://localhost:1")
```

with:

```python
class TestFailFast:
    @pytest.mark.asyncio
    async def test_unreachable_redis_raises_on_connect(self) -> None:
        from redis.exceptions import ConnectionError as RedisConnectionError

        store = RedisJobStore("redis://localhost:1")
        with pytest.raises((RedisConnectionError, redis.RedisError)):
            await store.connect()
```

- [ ] **Step 3: Run the test file to confirm**

Run: `uv run pytest tests/shell/stores/test_redis_job_store.py --no-cov -v 2>&1 | tail -30`
Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/shell/stores/test_redis_job_store.py
git commit -m "test(stores): async fixtures and renamed fail-fast test for RedisJobStore"
```

---

## Task 6: Wire `connect()` into `Orchestrator.start()` (TDD)

**Files:**
- Modify: `src/acheron/shell/orchestrator.py`
- Modify: `tests/shell/test_orchestrator.py`

- [ ] **Step 1: Write the failing test**

In `tests/shell/test_orchestrator.py`, add a new test class method to `TestOrchestratorStart` (or as a standalone test). Use a fake store that records whether `connect()` was awaited:

```python
@pytest.mark.asyncio
async def test_start_awaits_store_connect(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Orchestrator.start() must await connect() on both stores before returning."""
    from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore
    from acheron.shell.cache import PlanCache
    from acheron.shell.orchestrator import Orchestrator

    connect_calls: list[str] = []

    class _SpyWorkerStore(InMemoryWorkerStore):
        async def connect(self) -> None:
            connect_calls.append("worker")
            await super().connect()

    class _SpyJobStore(InMemoryJobStore):
        async def connect(self) -> None:
            connect_calls.append("job")
            await super().connect()

    reg = _SpyWorkerStore()
    jobs = _SpyJobStore()
    orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler, job_store=jobs)
    await orch.start()

    assert "worker" in connect_calls
    assert "job" in connect_calls
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/shell/test_orchestrator.py::test_start_awaits_store_connect --no-cov -v 2>&1 | tail -15`
Expected: FAIL with `AssertionError: 'worker' not in []` — orchestrator doesn't call connect yet.

- [ ] **Step 3: Wire the calls in `Orchestrator.start()`**

In `src/acheron/shell/orchestrator.py`, modify `start()` (lines 174-184):

```python
    async def start(self) -> None:
        """Start background tasks and register built-in local workers.

        Idempotent: calling start() more than once is a no-op so the FastAPI
        lifespan path and explicit callers can both be safe.
        """
        if self._started:
            return
        self._started = True
        await self._registry.connect()
        await self._job_store.connect()
        await self._register_built_in_local_workers()
        await self._health_monitor.start()
```

(Order: `connect()` before `register_built_in_local_workers()` so unreachable Redis fails before we do work; `_register_built_in_local_workers` then writes into the verified-OK store.)

- [ ] **Step 4: Run the new test to verify it passes**

Run: `uv run pytest tests/shell/test_orchestrator.py::test_start_awaits_store_connect --no-cov -v 2>&1 | tail -10`
Expected: PASS.

- [ ] **Step 5: Run the full orchestrator test file to confirm no regressions**

Run: `uv run pytest tests/shell/test_orchestrator.py --no-cov -v 2>&1 | tail -20`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/acheron/shell/orchestrator.py tests/shell/test_orchestrator.py
git commit -m "feat(orchestrator): await store connect() in start()"
```

---

## Task 7: Add tests for `connect()` idempotency and `close()` semantics (TDD)

**Files:**
- Modify: `tests/shell/stores/test_redis_worker_store.py`

- [ ] **Step 1: Write the failing test for idempotency**

Append to `tests/shell/stores/test_redis_worker_store.py`:

```python
class TestConnectIdempotency:
    @pytest.mark.asyncio
    async def test_connect_is_idempotent(self, store: RedisWorkerStore) -> None:
        """Calling connect() twice does not raise."""
        await store.connect()
        await store.connect()


class TestCloseSemantics:
    @pytest.mark.asyncio
    async def test_close_then_get_raises(self, redis_url: str) -> None:
        """After close(), operations on the pool raise ConnectionError."""
        from redis.exceptions import ConnectionError as RedisConnectionError

        s = RedisWorkerStore(redis_url)
        await s.connect()
        await s.close()
        with pytest.raises((RedisConnectionError, redis.RedisError)):
            await s.get("any-id")
```

- [ ] **Step 2: Run the new tests to verify they pass**

Run: `uv run pytest tests/shell/stores/test_redis_worker_store.py::TestConnectIdempotency tests/shell/stores/test_redis_worker_store.py::TestCloseSemantics --no-cov -v 2>&1 | tail -15`
Expected: PASS (both should pass on the current implementation — these are regression-locks, not new behavior).

- [ ] **Step 3: Commit**

```bash
git add tests/shell/stores/test_redis_worker_store.py
git commit -m "test(stores): lock in connect() idempotency and close() semantics"
```

---

## Task 8: Update the original 9b-ii spec entry

**Files:**
- Modify: `docs/superpowers/specs/2026-06-18-pipeline-streaming-design.md`

- [ ] **Step 1: Update the 9b-ii description**

In `docs/superpowers/specs/2026-06-18-pipeline-streaming-design.md`, replace line 147:

```markdown
- **9b-ii — Redis async backend:** Swap `redis.Redis` → `redis.asyncio.Redis` in both Redis stores. Integration tests via testcontainers (same pattern as 7a).
```

with:

```markdown
- **9b-ii — Redis async backend:** Swap `redis.Redis` → `redis.asyncio.Redis` in both Redis stores. `__init__` does no I/O; a concrete `async def connect()` is added to the store ABCs (no-op default) and overridden by Redis stores to `await self._redis.ping()`. `Orchestrator.start()` awaits `connect()` on both stores. `close()` becomes `async def` and calls `await self._redis.aclose()`. See `docs/superpowers/specs/2026-06-19-layer9b-ii-redis-async-design.md` for full design.
```

Also update line 161 in the file map:

```markdown
| `shell/stores/redis.py` | All methods → `async def`; `redis.asyncio.Redis`; `connect()` classmethod; `close()` → `async def` |
```

to:

```markdown
| `shell/stores/redis.py` | All methods → `async def`; `redis.asyncio.Redis`; `connect()` instance method (called from `Orchestrator.start()`); `close()` → `async def` (`aclose()`) |
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-06-18-pipeline-streaming-design.md
git commit -m "docs(specs): sync 9b-ii entry with actual implementation"
```

---

## Task 9: Final validation

**Files:** (none — validation only)

- [ ] **Step 1: Run the full validation gate**

Run: `just validate 2>&1 | tail -30`
Expected: all checks pass. The full suite (currently 393 tests at ~8s) should grow by ~3 new tests with no regressions. Coverage stays at 95%+.

If anything fails, fix it and re-run. Do not skip.

- [ ] **Step 2: Confirm git state**

Run: `git log --oneline -10`
Expected: 9 new commits stacked on top of the spec commit, all on master.

---

## Spec Coverage Check

| Spec section | Task |
|---|---|
| `redis.py` import + client swap | Task 2 |
| `redis.py` await every call | Task 2 |
| `redis.py` `aclose()` | Task 2 |
| `redis.py` class docstring | Task 2 |
| `base.py` concrete `connect()` | Task 1 |
| `orchestrator.py` `start()` awaits `connect()` | Task 6 |
| `tests/shell/stores/conftest.py` async FLUSHDB | Task 3 |
| `test_redis_worker_store.py` async fixture | Task 4 |
| `test_redis_job_store.py` async fixture | Task 5 |
| `test_unreachable_redis_raises_on_connect` | Tasks 4 & 5 |
| `test_connect_is_idempotent` | Task 7 |
| `test_close_then_get_raises` | Task 7 |
| `tests/shell/stores/test_stores_factory.py` | (no change — only InMemory path) |
| `tests/shell/stores/test_stores_async.py` | (no change — ABC contract) |
| `tests/integration/test_worker_integration.py` | (no change — InMemory backend) |
| `2026-06-18-pipeline-streaming-design.md` doc update | Task 8 |
| `just validate` final gate | Task 9 |
