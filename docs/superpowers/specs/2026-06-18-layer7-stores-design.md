# Acheron — Layer 7a Design Spec

**Storage Abstraction + Redis Backend**

This is a sub-project of [Acheron design spec](./2026-06-16-acheron-design.md) and [implementation roadmap](./2026-06-16-implementation-roadmap.md). It adds persistent storage to the orchestrator so worker registry and job state survive restarts.

## Goal

Make the orchestrator's state (registered workers, health tracking, tracked jobs) survive restarts. Currently everything is in-process `dict`s in `WorkerRegistry` and `JobStore`; a restart wipes all state. The fix is a shared storage abstraction with an in-memory implementation (preserved for dev and tests) and a Redis implementation (for production).

## Architecture

### Shared Abstraction

Both stores are defined as ABCs in a new subpackage `src/acheron/shell/stores/`. ABCs match the existing convention in `src/acheron/core/interfaces.py` (where `Worker`, `StreamingWorker`, `Executor` are all ABCs, not Protocols).

```python
# src/acheron/shell/stores/base.py
from abc import ABC, abstractmethod

class WorkerStore(ABC):
    @abstractmethod
    def register(self, worker_id, endpoint, transport, capabilities, metadata=None) -> None: ...
    @abstractmethod
    def unregister(self, worker_id) -> None: ...
    @abstractmethod
    def get(self, worker_id) -> RegisteredWorker | None: ...
    @abstractmethod
    def list_all(self) -> tuple[RegisteredWorker, ...]: ...
    @abstractmethod
    def find_by_type(self, worker_type) -> tuple[RegisteredWorker, ...]: ...
    @abstractmethod
    def find_by_language(self, src, dst) -> tuple[RegisteredWorker, ...]: ...
    @abstractmethod
    def record_health_failure(self, worker_id) -> bool: ...
    @abstractmethod
    def record_health_success(self, worker_id) -> None: ...
    @abstractmethod
    def close(self) -> None: ...

class JobStore(ABC):
    @abstractmethod
    def put(self, job) -> None: ...
    @abstractmethod
    def get(self, job_id) -> TrackedJob | None: ...
    @abstractmethod
    def list_all(self) -> tuple[TrackedJob, ...]: ...
    @abstractmethod
    def close(self) -> None: ...
```

The `RegisteredWorker` and `TrackedJob` dataclasses stay in their current locations (`shell/registry.py` and `shell/job_store.py`).

### Implementations

- **`InMemoryWorkerStore`** — renames and moves the current `WorkerRegistry`. Zero behavior change, same internal `_workers` dict. Lives at `src/acheron/shell/stores/memory.py`.
- **`InMemoryJobStore`** — renames and moves the current `JobStore`. Same internal `_jobs` dict. Lives at the same file.
- **`RedisWorkerStore`** — uses the synchronous `redis.Redis` client (not `redis.asyncio.Redis`). Sync calls from an async context block the event loop briefly, but Redis calls are fast (~1ms LAN) and infrequent. If profiling shows event-loop pressure, migrate the ABCs to `async def` and switch to `redis.asyncio.Redis`. Uses `redis~=7.0`. Lives at `src/acheron/shell/stores/redis.py`.
- **`RedisJobStore`** — same module.

### Backend Selection

Env var `ACHERON_STORE_BACKEND=memory|redis` (default `memory`). Read once at startup in the factory functions in `src/acheron/shell/stores/__init__.py`:

```python
def create_worker_store() -> WorkerStore:
    backend = os.environ.get("ACHERON_STORE_BACKEND", "memory")
    match backend:
        case "memory":
            return InMemoryWorkerStore()
        case "redis":
            return RedisWorkerStore(redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379"))
        case _:
            msg = f"Unknown ACHERON_STORE_BACKEND: {backend}"
            raise ValueError(msg)
```

The orchestrator and `create_app` use the factories by default; tests pass the in-memory store directly.

### Fail-Fast on Redis Error

If `ACHERON_STORE_BACKEND=redis` but Redis is unreachable at startup, the orchestrator crashes immediately with a clear error. The same applies to any operation that finds Redis unreachable. We do not silently degrade to in-memory — that would defeat the persistence guarantee and mask real problems.

- `RedisWorkerStore.__init__` calls `await redis_client.ping()` and raises `RedisConnectionError` on failure
- All other operations propagate Redis errors as-is

### Redis Data Layout

**Worker state:**

```
HASH worker:{worker_id} → {
    endpoint: "host:port",
    transport: "http" | "grpc" | "local",
    consecutive_failures: "0",
    last_health_check: "1234567890.123",
    capabilities_json: "{...JSON-serialized WorkerCapabilities...",
    metadata_json: "{...}"
}
SET workers → ["worker-id-1", "worker-id-2", ...]
```

`find_by_type` and `find_by_language` use a Redis pipeline that fetches all members of `workers` and filters client-side. The expected registry size is small (dozens of workers, not thousands), so this is fast enough without secondary indexes. If it ever becomes a bottleneck, add `workers:type:{type}` and `workers:lang:{src}:{dst}` SETs with atomic maintenance in `register()`.

**Job state:**

```
STRING job:{job_id} → "{...JSON-serialized TrackedJob...}"
SET jobs → ["job-id-1", "job-id-2", ...]
```

`TrackedJob` and its nested `Plan`, `PlanStep`, `JobRequest` are all dataclasses with primitive fields — JSON serialization is straightforward. `frozenset` fields on `WorkerCapabilities` serialize as sorted lists.

**Atomicity:**

- `register()` writes the HASH, then `SADD`s the worker ID. If the HASH write fails, the SET is unchanged.
- `unregister()` reverses: `SREM` first, then `DEL` the HASH. If the DEL fails, the worker is invisible to `list_all` but the HASH is orphaned and will be overwritten on next `register()`. Acceptable.
- `record_health_failure()` / `record_health_success()` use `HINCRBY` / `HSET` (atomic on a single field). The "remove after 3 failures" check is read-then-write — safe enough because only the health monitor mutates this counter.

### Orchestrator Wiring

`Orchestrator.__init__` switches from `WorkerRegistry` and `JobStore` to `WorkerStore` and `JobStore` (the new ABC types). The built-in local workers, `_register_built_in_local_workers`, keep working unchanged. The `HealthMonitor` keeps its constructor and just uses the new `WorkerStore` type.

The `create_app` factory in `src/acheron/shell/api/app.py` calls the new factories:

```python
def create_app(...):
    registry = create_worker_store()  # was WorkerRegistry()
    cache = PlanCache(data_dir)
    orchestrator = Orchestrator(registry=registry, cache=cache)
    ...
```

On FastAPI shutdown, the lifespan calls `registry.close()` and `job_store.close()` to drain Redis connection pools cleanly. The orchestrator exposes a `close()` method that delegates to both stores with exception isolation, so the lifespan stays a one-liner.

## Test Strategy

- **Existing tests** keep working without changes — they construct `Orchestrator` with explicit `WorkerRegistry`/`JobStore`, which become `InMemoryWorkerStore`/`InMemoryJobStore` under the hood. Same API surface.
- **New tests** in `tests/shell/stores/`:
  - `test_memory_worker_store.py` — copied from existing `test_registry.py`, then the original deleted
  - `test_memory_job_store.py` — copied from existing `test_job_store.py`, then the original deleted
  - `test_redis_worker_store.py` — integration tests against real Redis via `testcontainers[redis]`
  - `test_redis_job_store.py` — same
- **testcontainers** spins up a real Redis container per test session (or per test, depending on isolation needs). `REDIS_URL` is set from the testcontainer.
- **Backend selection tests** in `test_stores_factory.py` — verify `ACHERON_STORE_BACKEND` is read correctly and an unknown value raises.

Per AGENTS.md: tests don't depend on hardcoded paths. Redis URL is set via env var from the testcontainer fixture.

## Files

### New

- `src/acheron/shell/stores/__init__.py` — factory functions
- `src/acheron/shell/stores/base.py` — ABCs
- `src/acheron/shell/stores/memory.py` — in-memory implementations
- `src/acheron/shell/stores/redis.py` — Redis implementations
- `tests/shell/stores/__init__.py`
- `tests/shell/stores/test_memory_worker_store.py`
- `tests/shell/stores/test_memory_job_store.py`
- `tests/shell/stores/test_redis_worker_store.py`
- `tests/shell/stores/test_redis_job_store.py`
- `tests/shell/stores/test_stores_factory.py`
- `tests/shell/stores/conftest.py` — testcontainers fixture

### Modified

- `src/acheron/shell/registry.py` — keep `RegisteredWorker` dataclass, delete the `WorkerRegistry` class (moved to `stores/memory.py`)
- `src/acheron/shell/job_store.py` — keep `TrackedJob` dataclass, delete the `JobStore` class (moved to `stores/memory.py`)
- `src/acheron/shell/orchestrator.py` — switch type annotations to the new ABCs, call `create_worker_store()` and `create_job_store()` factories by default
- `src/acheron/shell/api/app.py` — same, plus lifespan shutdown calls `close()`
- `src/acheron/shell/health.py` — `HealthMonitor` takes the new `WorkerStore` type
- `tests/shell/test_registry.py` — delete (moved to `tests/shell/stores/test_memory_worker_store.py`)
- `tests/shell/test_job_store.py` — delete (moved to `tests/shell/stores/test_memory_job_store.py`)
- `pyproject.toml` — add `testcontainers[redis]~=` to dev deps

### Unchanged

- `src/acheron/shell/transports/*.py` — workers don't care about the store
- `stubs/*.py` — stubs don't care
- `dashboard/*` — dashboard doesn't care

## Migration

- No data migration. Existing in-memory state is ephemeral; on first start with the Redis backend, the registry is empty until workers re-register.
- Workers already retry registration until success (existing behavior in stubs), so a Redis-backed orchestrator will naturally re-populate on first start.
- Existing CLI and API users see no change — they hit the same endpoints, get the same responses.

## Out of Scope (Future Sub-projects)

- **Layer 7b** — Docker healthchecks, resource limits, persistent volumes in `docker-compose.yml`
- **Layer 7c** — TLS via reverse proxy

## Dependencies

- `redis~=7.0` (bumped from `~5.3` because `testcontainers[redis]>=4.14` requires `redis>=7`; backward compatible with our sync usage)
- `testcontainers[redis]~=` (new dev dep)
