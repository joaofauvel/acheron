# Layer 9b-ii — Redis async backend

## Goal

Replace the synchronous `redis.Redis` client inside `RedisWorkerStore` and `RedisJobStore` with `redis.asyncio.Redis`, so all Redis I/O is genuinely non-blocking. Layer 9b-i made the methods `async def` but the underlying client was still sync — calls blocked the event loop. 9b-ii is the actual non-blocking migration.

## Scope

In scope:
- `src/acheron/shell/stores/redis.py`: switch to `redis.asyncio.Redis`; await every call; `aclose()` on shutdown.
- `src/acheron/shell/stores/base.py`: add concrete `async def connect(self) -> None` to both ABCs (no-op default).
- `src/acheron/shell/orchestrator.py`: `start()` awaits `connect()` on both stores.
- `tests/shell/stores/conftest.py`: `redis_url` fixture uses `redis.asyncio.Redis` for `FLUSHDB`.
- `tests/shell/stores/test_redis_worker_store.py`, `test_redis_job_store.py`: `store` fixture becomes async, awaits `connect()` and `close()`. Add `test_unreachable_redis_raises_on_connect`, `test_connect_is_idempotent`, `test_close_then_get_raises`.
- `docs/superpowers/specs/pipeline-streaming.md`: update the 9b-ii entry to match the actual implementation.

Out of scope (handled in other layers):
- The `StreamingExecutor` (9a).
- Real `redis.asyncio` connection-pool tuning (out of band; defaults are fine for the in-cluster use case).
- Health-check concurrency — `HealthMonitor` is already async-correct in 9b-i.

## Architecture

### Constructor + `connect()` pattern

`__init__` does no I/O. `async def connect()` is the canonical startup check.

```python
class RedisWorkerStore(WorkerStore):
    def __init__(self, redis_url: str) -> None:
        self._redis = redis.asyncio.Redis.from_url(redis_url, decode_responses=True)

    async def connect(self) -> None:
        """Verify the Redis server is reachable. Idempotent."""
        await self._redis.ping()

    async def close(self) -> None:
        """Close the connection pool."""
        await self._redis.aclose()
```

Why an instance method, not a classmethod:
- The factory stays sync (it just constructs; `__init__` no longer pings).
- The orchestrator's `start()` is the natural async driver.
- Matches the pattern used in the rest of the project (`Orchestrator.start()` awaits everything that needs awaiting).

Why `from_url` is safe in `__init__`: `redis.asyncio.Redis.from_url` does not perform network I/O. The connection pool is lazy, so the first operation will connect. Calling `connect()` explicitly is what gives us the fail-fast guarantee during `Orchestrator.start()` — without it, an unreachable Redis only surfaces on the first `register()` / `get()` / `put()` call.

### ABC default

`connect()` is concrete with a no-op body in the base class:

```python
class WorkerStore(ABC):
    async def connect(self) -> None:
        """Verify the backend is reachable. No-op for stores without a remote backend."""
```

`InMemoryWorkerStore` and `InMemoryJobStore` inherit this. The orchestrator calls `connect()` on both stores unconditionally; the InMemory no-op is essentially free.

`close()` is still abstract — both stores must release their resources explicitly.

## Lifecycle

### Orchestrator

```python
async def start(self) -> None:
    if self._started:
        return
    self._started = True
    await self._register_built_in_local_workers()
    await self._worker_store.connect()   # NEW — no-op for InMemory
    await self._job_store.connect()      # NEW — no-op for InMemory
    if self._health_monitor is not None:
        await self._health_monitor.start()
```

`close()` is unchanged; it already awaits both stores' `close()`.

### App lifespan

No change. The FastAPI lifespan already calls `start()` / `shutdown()` / `close()` in order. The Redis ping now happens during `start()` instead of during `create_worker_store()`.

### Factory

`create_worker_store()` and `create_job_store()` stay sync. They construct; they don't connect. Fail-fast on unreachable Redis is preserved via `connect()` from `start()`.

### Error semantics

If Redis is unreachable, the user sees `redis.exceptions.ConnectionError` (or a subclass thereof) — same exception class as before, just thrown from `start()` instead of from `create_worker_store()`. The existing `test_unreachable_redis_raises_on_init` is renamed to `test_unreachable_redis_raises_on_connect` and made `async def`.

## Test Plan

### `tests/shell/stores/conftest.py`

`redis_url` switches to `redis.asyncio.Redis` for `FLUSHDB`:

```python
@pytest.fixture
def redis_url(redis_container: RedisContainer) -> str:
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

`redis.asyncio` is imported at the top.

### `test_redis_worker_store.py` / `test_redis_job_store.py`

`store` fixture becomes async, awaits `connect()` and `close()`:

```python
@pytest_asyncio.fixture
async def store(redis_url: str) -> RedisWorkerStore:
    store = RedisWorkerStore(redis_url)
    await store.connect()
    try:
        yield store
    finally:
        await store.close()
```

All existing tests in those files are already `@pytest.mark.asyncio` and `await` the store methods — they just need the new fixture.

### New tests

- `test_unreachable_redis_raises_on_connect` (in both Redis test files): replaces the old `test_unreachable_redis_raises_on_init`. `async def`, asserts `await RedisWorkerStore("redis://localhost:1").connect()` raises `redis.exceptions.ConnectionError`.
- `test_connect_is_idempotent` (in `test_redis_worker_store.py`): two consecutive `connect()` calls succeed.
- `test_close_then_get_raises` (in `test_redis_worker_store.py`): after `close()`, `get()` raises `redis.exceptions.ConnectionError`. Verifies the pool is actually released.

### Tests that don't change

- `test_stores_factory.py` — only asserts InMemory; the Redis path is never constructed.
- `test_stores_async.py` — ABC contracts; the new `connect()` default works the same way.
- `tests/integration/test_worker_integration.py::test_orchestrator_works_with_redis_backend` — uses InMemory backend.
- `tests/integration/conftest.py` — `wired_app` doesn't construct Redis stores.

## Type/Import Changes

### `src/acheron/shell/stores/redis.py`

- `import redis` → `import redis.asyncio`.
- `self._redis = redis.Redis.from_url(...)` → `self._redis = redis.asyncio.Redis.from_url(...)`.
- `self._redis.ping()` → `await self._redis.ping()`.
- `self._redis.close()` → `await self._redis.aclose()`.
- All `pipe.execute()` → `await pipe.execute()`.
- All single-call sites (`hset`, `hgetall`, `hincrby`, `smembers`, `sadd`, `srem`, `delete`, `exists`, `get`, `set`) → `await`-prefixed.
- Keep `# type: ignore[misc]` on each `await self._redis.<method>(...)` call — the redis-py async stubs type methods as `Awaitable[T] | T` and the `T` branch is unreachable in our async call sites.
- Class docstring: drop the "transitional state" note. New wording: `"Redis-backed worker store. Survives orchestrator restarts. Requires awaiting connect() before use."`

### `src/acheron/shell/stores/base.py`

Add a concrete `async def connect(self) -> None` to both `WorkerStore` and `JobStore`:

```python
async def connect(self) -> None:
    """Verify the backend is reachable. No-op for stores without a remote backend."""
```

### No new dependency

`redis.asyncio` ships with `redis~=7.0`, already pinned in `pyproject.toml`.

## File Map

| File | Change |
|---|---|
| `src/acheron/shell/stores/redis.py` | `redis.asyncio` client, await every call, `aclose()` |
| `src/acheron/shell/stores/base.py` | Add concrete `connect()` no-op to both ABCs |
| `src/acheron/shell/orchestrator.py` | `start()` awaits `connect()` on both stores |
| `tests/shell/stores/conftest.py` | `redis_url` uses `redis.asyncio` for FLUSHDB |
| `tests/shell/stores/test_redis_worker_store.py` | async `store` fixture; 2 new tests; 1 renamed test |
| `tests/shell/stores/test_redis_job_store.py` | async `store` fixture; 1 renamed test |
| `docs/superpowers/specs/pipeline-streaming.md` | Update 9b-ii entry to match implementation |

## Validation

`just validate` — lint, mypy, basedpyright, full test suite, 80% coverage floor.
