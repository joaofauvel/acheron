# Layer 7a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent storage to the orchestrator (Redis-backed) behind a shared ABC abstraction, so worker registry and job state survive orchestrator restarts.

**Architecture:** New `src/acheron/shell/stores/` subpackage defines `WorkerStore` and `JobStore` ABCs with **synchronous** methods. In-memory implementations are renames of the current `WorkerRegistry` and `JobStore`. Redis implementations are new and use the **sync** `redis.Redis` client. A factory in `stores/__init__.py` reads `ACHERON_STORE_BACKEND=memory|redis` to pick the implementation. The orchestrator and FastAPI app use the factories by default; tests pass in-memory stores explicitly.

**Tech Stack:** Python 3.14, redis~=5.3 (sync `Redis` client), testcontainers[redis]~=4.8 (dev), existing pytest/httpx/FastAPI stack.

**Design note on sync:** The project is async-first, so sync Redis is a deliberate trade-off — sync store calls from an async context block the event loop briefly. This is acceptable for v1 because Redis calls are fast (~1ms LAN) and infrequent. If profiling shows event-loop pressure, swap `redis.Redis` for `redis.asyncio.Redis` and migrate the ABCs to async in a follow-up. Documented here so a future engineer knows the trade-off.

---

## File Structure

### New

- `src/acheron/shell/stores/__init__.py` — factory functions
- `src/acheron/shell/stores/base.py` — `WorkerStore`, `JobStore` ABCs
- `src/acheron/shell/stores/memory.py` — `InMemoryWorkerStore`, `InMemoryJobStore`
- `src/acheron/shell/stores/redis.py` — `RedisWorkerStore`, `RedisJobStore`
- `tests/shell/stores/__init__.py` — empty
- `tests/shell/stores/test_base.py` — abstract instantiation tests
- `tests/shell/stores/test_memory_worker_store.py` — moved from `test_registry.py`
- `tests/shell/stores/test_memory_job_store.py` — moved from `test_job_store.py`
- `tests/shell/stores/test_redis_worker_store.py` — new, uses testcontainers
- `tests/shell/stores/test_redis_job_store.py` — new, uses testcontainers
- `tests/shell/stores/test_stores_factory.py` — new
- `tests/shell/stores/conftest.py` — `redis_url` fixture

### Modified

- `src/acheron/shell/registry.py` — keep `RegisteredWorker` only, drop `WorkerRegistry` class
- `src/acheron/shell/job_store.py` — keep `TrackedJob` only, drop `JobStore` class
- `src/acheron/shell/orchestrator.py` — `Orchestrator.__init__` takes `WorkerStore` and `JobStore` (ABCs); calls `create_worker_store()` / `create_job_store()` factories by default
- `src/acheron/shell/api/app.py` — same
- `src/acheron/shell/health.py` — `HealthMonitor` constructor takes `WorkerStore` (ABC)
- `src/acheron/shell/step_handler.py` — `create_step_handler` takes `WorkerStore` (ABC)
- 8 test files — import updates from `WorkerRegistry` → `InMemoryWorkerStore` (and the same for `JobStore`)
- `pyproject.toml` — add `testcontainers[redis]~=4.8` to dev deps

### Deleted

- `tests/shell/test_registry.py` — moved
- `tests/shell/test_job_store.py` — moved

---

## Task 1: Add testcontainers dev dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add testcontainers[redis] to dev deps**

In `pyproject.toml`, in the `[dependency-groups]` `dev` list, add `"testcontainers[redis]~=4.8"`. Alphabetical order. The block should look like:

```toml
[dependency-groups]
dev = [
    "basedpyright~=1.39",
    "grpcio-tools~=1.81",
    "import-linter~=2.11",
    "inline-snapshot~=0.34",
    "mypy~=2.1",
    "pytest~=9.1",
    "pytest-asyncio~=1.4",
    "pytest-cov~=7.1",
    "pytest-xdist~=3.8",
    "respx~=0.23",
    "ruff~=0.15",
    "testcontainers[redis]~=4.8",
    "types-grpcio~=1.0",
    "types-grpcio-health-checking~=1.0",
    "types-protobuf~=5.29",
]
```

- [ ] **Step 2: Sync the lockfile**

Run: `cd /home/julia/devel/acheron && uv sync --all-extras`
Expected: installs `testcontainers[redis]`, lockfile updates.

- [ ] **Step 3: Commit**

```bash
cd /home/julia/devel/acheron && git add pyproject.toml uv.lock && git commit -m "chore(deps): add testcontainers[redis] for integration tests"
```

---

## Task 2: Create stores subpackage with ABCs

**Files:**
- Create: `src/acheron/shell/stores/__init__.py`
- Create: `src/acheron/shell/stores/base.py`
- Create: `tests/shell/stores/__init__.py`
- Create: `tests/shell/stores/test_base.py`

- [ ] **Step 1: Write the failing test for ABC instantiation**

Create `tests/shell/stores/test_base.py`:

```python
"""Tests for the store ABCs."""

import pytest

from acheron.shell.stores.base import JobStore, WorkerStore


class TestWorkerStoreAbstract:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError, match="abstract"):
            WorkerStore()  # type: ignore[abstract]


class TestJobStoreAbstract:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError, match="abstract"):
            JobStore()  # type: ignore[abstract]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/julia/devel/acheron && uv run pytest tests/shell/stores/test_base.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'acheron.shell.stores'`

- [ ] **Step 3: Create the stores subpackage files**

Create `src/acheron/shell/stores/__init__.py`:

```python
"""Storage backends for the orchestrator."""
```

Create `src/acheron/shell/stores/base.py`:

```python
"""Abstract base classes for orchestrator state storage."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from acheron.core.models import WorkerCapabilities, WorkerType
    from acheron.shell.job_store import TrackedJob
    from acheron.shell.registry import RegisteredWorker


class WorkerStore(ABC):
    """Persistent or in-memory store of registered workers and their health state."""

    max_failures: int = 3

    @abstractmethod
    def register(
        self,
        worker_id: str,
        endpoint: str,
        transport: str,
        capabilities: WorkerCapabilities,
        metadata: dict[str, object] | None = None,
    ) -> None: ...

    @abstractmethod
    def unregister(self, worker_id: str) -> None: ...

    @abstractmethod
    def get(self, worker_id: str) -> RegisteredWorker | None: ...

    @abstractmethod
    def list_all(self) -> tuple[RegisteredWorker, ...]: ...

    @abstractmethod
    def find_by_type(self, worker_type: WorkerType) -> tuple[RegisteredWorker, ...]: ...

    @abstractmethod
    def find_by_language(self, src: str, dst: str) -> tuple[RegisteredWorker, ...]: ...

    @abstractmethod
    def record_health_failure(self, worker_id: str) -> bool:
        """Record a failed health check. Returns True if the worker was removed."""
        ...

    @abstractmethod
    def record_health_success(self, worker_id: str) -> None: ...

    @abstractmethod
    def close(self) -> None:
        """Release any resources held by the store (Redis pools, file handles)."""
        ...


class JobStore(ABC):
    """Persistent or in-memory store of tracked jobs."""

    @abstractmethod
    def put(self, job: TrackedJob) -> None: ...

    @abstractmethod
    def get(self, job_id: str) -> TrackedJob | None: ...

    @abstractmethod
    def list_all(self) -> tuple[TrackedJob, ...]: ...

    @abstractmethod
    def close(self) -> None: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/julia/devel/acheron && uv run pytest tests/shell/stores/test_base.py -v --no-cov`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/julia/devel/acheron && git add src/acheron/shell/stores/ tests/shell/stores/test_base.py tests/shell/stores/__init__.py && git commit -m "feat(stores): add WorkerStore and JobStore ABCs"
```

---

## Task 3: Move in-memory implementations to new subpackage

**Files:**
- Create: `src/acheron/shell/stores/memory.py`
- Create: `tests/shell/stores/test_memory_worker_store.py`
- Create: `tests/shell/stores/test_memory_job_store.py`
- Delete: `tests/shell/test_registry.py`
- Delete: `tests/shell/test_job_store.py`
- Modify: `src/acheron/shell/registry.py` (remove `WorkerRegistry` class)
- Modify: `src/acheron/shell/job_store.py` (remove `JobStore` class)
- Modify: 8 test files + 5 src files (imports only)

- [ ] **Step 1: Create the new in-memory worker store test (will fail to import)**

Create `tests/shell/stores/test_memory_worker_store.py`. Copy the contents of `tests/shell/test_registry.py` and:
- Change the import from `from acheron.shell.registry import WorkerRegistry` to `from acheron.shell.stores.memory import InMemoryWorkerStore`
- Replace all `WorkerRegistry` with `InMemoryWorkerStore` (class names in test bodies)

The full file:

```python
"""Tests for the in-memory worker store."""

from acheron.core.models import WorkerCapabilities, WorkerType
from acheron.shell.stores.memory import InMemoryWorkerStore


def _tts_caps(
    langs_in: frozenset[str] = frozenset({"en"}), langs_out: frozenset[str] = frozenset({"es"})
) -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TTS,
        supported_languages_in=langs_in,
        supported_languages_out=langs_out,
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"wav"}),
        max_payload_bytes=None,
        batch_capable=True,
        model_source=None,
    )


def _asr_caps() -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.ASR,
        supported_languages_in=frozenset({"en", "es"}),
        supported_languages_out=frozenset({"en", "es"}),
        supported_formats_in=frozenset({"mp3", "wav"}),
        supported_formats_out=frozenset({"text"}),
        max_payload_bytes=None,
        batch_capable=False,
        model_source=None,
    )


class TestInMemoryWorkerStore:
    def test_register_and_get(self) -> None:
        store = InMemoryWorkerStore()
        store.register("w-1", "http://localhost:8001", "http", _tts_caps())
        w = store.get("w-1")
        assert w is not None
        assert w.worker_id == "w-1"
        assert w.endpoint == "http://localhost:8001"
        assert w.transport == "http"

    def test_get_nonexistent(self) -> None:
        store = InMemoryWorkerStore()
        assert store.get("nope") is None

    def test_unregister(self) -> None:
        store = InMemoryWorkerStore()
        store.register("w-1", "http://localhost:8001", "http", _tts_caps())
        store.unregister("w-1")
        assert store.get("w-1") is None

    def test_unregister_nonexistent(self) -> None:
        store = InMemoryWorkerStore()
        store.unregister("nope")

    def test_list_all(self) -> None:
        store = InMemoryWorkerStore()
        store.register("w-1", "http://a", "http", _tts_caps())
        store.register("w-2", "http://b", "http", _asr_caps())
        workers = store.list_all()
        assert len(workers) == 2
        ids = {w.worker_id for w in workers}
        assert ids == {"w-1", "w-2"}

    def test_reregistration_overwrites(self) -> None:
        store = InMemoryWorkerStore()
        store.register("w-1", "http://old", "http", _tts_caps())
        store.register("w-1", "http://new", "http", _tts_caps())
        w = store.get("w-1")
        assert w is not None
        assert w.endpoint == "http://new"

    def test_find_by_type(self) -> None:
        store = InMemoryWorkerStore()
        store.register("tts-1", "http://a", "http", _tts_caps())
        store.register("asr-1", "http://b", "http", _asr_caps())
        store.register("tts-2", "http://c", "http", _tts_caps())
        tts_workers = store.find_by_type(WorkerType.TTS)
        assert len(tts_workers) == 2
        asr_workers = store.find_by_type(WorkerType.ASR)
        assert len(asr_workers) == 1

    def test_find_by_language(self) -> None:
        store = InMemoryWorkerStore()
        store.register("w-1", "http://a", "http", _tts_caps(frozenset({"en"}), frozenset({"es"})))
        store.register("w-2", "http://b", "http", _tts_caps(frozenset({"en"}), frozenset({"fr"})))
        store.register("w-3", "http://c", "http", _tts_caps(frozenset({"es"}), frozenset({"en"})))
        en_to_es = store.find_by_language("en", "es")
        assert len(en_to_es) == 1
        assert en_to_es[0].worker_id == "w-1"

    def test_find_by_language_no_match(self) -> None:
        store = InMemoryWorkerStore()
        store.register("w-1", "http://a", "http", _tts_caps(frozenset({"en"}), frozenset({"es"})))
        result = store.find_by_language("ja", "ko")
        assert len(result) == 0


class TestHealthTracking:
    def test_health_success_resets_counter(self) -> None:
        store = InMemoryWorkerStore()
        store.register("w-1", "http://a", "http", _tts_caps())
        store.record_health_failure("w-1")
        store.record_health_failure("w-1")
        w = store.get("w-1")
        assert w is not None
        assert w.consecutive_failures == 2
        store.record_health_success("w-1")
        w = store.get("w-1")
        assert w is not None
        assert w.consecutive_failures == 0

    def test_health_failure_increments(self) -> None:
        store = InMemoryWorkerStore()
        store.register("w-1", "http://a", "http", _tts_caps())
        removed = store.record_health_failure("w-1")
        assert not removed
        w = store.get("w-1")
        assert w is not None
        assert w.consecutive_failures == 1

    def test_removed_after_max_failures(self) -> None:
        store = InMemoryWorkerStore()
        store.register("w-1", "http://a", "http", _tts_caps())
        store.record_health_failure("w-1")
        store.record_health_failure("w-1")
        removed = store.record_health_failure("w-1")
        assert removed
        assert store.get("w-1") is None

    def test_health_failure_nonexistent(self) -> None:
        store = InMemoryWorkerStore()
        removed = store.record_health_failure("nope")
        assert not removed

    def test_health_success_nonexistent(self) -> None:
        store = InMemoryWorkerStore()
        store.record_health_success("nope")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/julia/devel/acheron && uv run pytest tests/shell/stores/test_memory_worker_store.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'acheron.shell.stores.memory'`

- [ ] **Step 3: Create the in-memory implementation**

Create `src/acheron/shell/stores/memory.py`:

```python
"""In-memory implementations of the store ABCs."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from acheron.shell.stores.base import JobStore, WorkerStore

if TYPE_CHECKING:
    from acheron.core.models import WorkerCapabilities, WorkerType
    from acheron.shell.job_store import TrackedJob
    from acheron.shell.registry import RegisteredWorker


class InMemoryWorkerStore(WorkerStore):
    """In-memory store of registered workers. State is lost on process restart."""

    def __init__(self) -> None:
        self._workers: dict[str, RegisteredWorker] = {}

    def register(
        self,
        worker_id: str,
        endpoint: str,
        transport: str,
        capabilities: WorkerCapabilities,
        metadata: dict[str, object] | None = None,
    ) -> None:
        from acheron.shell.registry import RegisteredWorker  # noqa: PLC0415

        self._workers[worker_id] = RegisteredWorker(
            worker_id=worker_id,
            endpoint=endpoint,
            transport=transport,
            capabilities=capabilities,
            consecutive_failures=0,
            last_health_check=time.time(),
            metadata=metadata or {},
        )

    def unregister(self, worker_id: str) -> None:
        self._workers.pop(worker_id, None)

    def get(self, worker_id: str) -> RegisteredWorker | None:
        return self._workers.get(worker_id)

    def list_all(self) -> tuple[RegisteredWorker, ...]:
        return tuple(self._workers.values())

    def find_by_type(self, worker_type: WorkerType) -> tuple[RegisteredWorker, ...]:
        return tuple(w for w in self._workers.values() if w.capabilities.worker_type == worker_type)

    def find_by_language(self, src: str, dst: str) -> tuple[RegisteredWorker, ...]:
        return tuple(
            w
            for w in self._workers.values()
            if src in w.capabilities.supported_languages_in and dst in w.capabilities.supported_languages_out
        )

    def record_health_failure(self, worker_id: str) -> bool:
        worker = self._workers.get(worker_id)
        if worker is None:
            return False
        worker.consecutive_failures += 1
        worker.last_health_check = time.time()
        if worker.consecutive_failures >= self.max_failures:
            self.unregister(worker_id)
            return True
        return False

    def record_health_success(self, worker_id: str) -> None:
        worker = self._workers.get(worker_id)
        if worker is not None:
            worker.consecutive_failures = 0
            worker.last_health_check = time.time()

    def close(self) -> None:
        return None


class InMemoryJobStore(JobStore):
    """In-memory store of tracked jobs. State is lost on process restart."""

    def __init__(self) -> None:
        self._jobs: dict[str, TrackedJob] = {}

    def put(self, job: TrackedJob) -> None:
        self._jobs[job.job_id] = job

    def get(self, job_id: str) -> TrackedJob | None:
        return self._jobs.get(job_id)

    def list_all(self) -> tuple[TrackedJob, ...]:
        return tuple(self._jobs.values())

    def close(self) -> None:
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/julia/devel/acheron && uv run pytest tests/shell/stores/test_memory_worker_store.py -v --no-cov`
Expected: PASS

- [ ] **Step 5: Create the in-memory job store test**

Create `tests/shell/stores/test_memory_job_store.py`. Copy the contents of `tests/shell/test_job_store.py` and:
- Change the import from `from acheron.shell.job_store import JobStore, TrackedJob` to `from acheron.shell.job_store import TrackedJob` and add `from acheron.shell.stores.memory import InMemoryJobStore`
- Replace all `JobStore()` with `InMemoryJobStore()`

The full file:

```python
"""Tests for the in-memory job store."""

from acheron.core.models import EpubRequest, ExecutorStrategy
from acheron.shell.job_store import TrackedJob
from acheron.shell.stores.memory import InMemoryJobStore


def _tracked(job_id: str = "job-1") -> TrackedJob:
    return TrackedJob(
        job_id=job_id,
        request=EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
        strategy=ExecutorStrategy.BATCH_ASYNC,
    )


class TestInMemoryJobStore:
    def test_put_and_get(self) -> None:
        store = InMemoryJobStore()
        job = _tracked()
        store.put(job)
        assert store.get("job-1") is job

    def test_get_nonexistent(self) -> None:
        store = InMemoryJobStore()
        assert store.get("nope") is None

    def test_list_all(self) -> None:
        store = InMemoryJobStore()
        store.put(_tracked("j-1"))
        store.put(_tracked("j-2"))
        store.put(_tracked("j-3"))
        assert len(store.list_all()) == 3

    def test_list_empty(self) -> None:
        store = InMemoryJobStore()
        assert store.list_all() == ()

    def test_put_overwrites(self) -> None:
        store = InMemoryJobStore()
        job1 = _tracked("j-1")
        job2 = _tracked("j-1")
        store.put(job1)
        store.put(job2)
        assert store.get("j-1") is job2

    def test_status_update(self) -> None:
        store = InMemoryJobStore()
        job = _tracked()
        store.put(job)
        job.status = "running"
        store.put(job)
        stored = store.get("job-1")
        assert stored is not None
        assert stored.status == "running"
```

Run: `cd /home/julia/devel/acheron && uv run pytest tests/shell/stores/test_memory_job_store.py -v --no-cov`
Expected: PASS

- [ ] **Step 6: Update src/ imports to use `WorkerStore` (ABC) instead of `WorkerRegistry`**

In `src/acheron/shell/orchestrator.py`:
- Change the TYPE_CHECKING block: `from acheron.shell.registry import RegisteredWorker, WorkerRegistry` → `from acheron.shell.registry import RegisteredWorker` and `from acheron.shell.stores.base import WorkerStore`
- Add `from acheron.shell.stores import create_job_store, create_worker_store` and `from acheron.shell.stores.base import JobStore, WorkerStore` to the regular imports
- Change the `__init__` parameter type: `registry: WorkerRegistry` → `registry: WorkerStore`
- Change `JobStore()` default → `create_job_store()` factory call

In `src/acheron/shell/health.py`:
- Change the TYPE_CHECKING block: `from acheron.shell.registry import WorkerRegistry` → `from acheron.shell.stores.base import WorkerStore`
- Change the `__init__` parameter type: `registry: WorkerRegistry` → `registry: WorkerStore`

In `src/acheron/shell/step_handler.py`:
- Change the TYPE_CHECKING block: `from acheron.shell.registry import RegisteredWorker, WorkerRegistry` → `from acheron.shell.registry import RegisteredWorker` and `from acheron.shell.stores.base import WorkerStore`
- Change the `create_step_handler` parameter type: `registry: WorkerRegistry` → `registry: WorkerStore`

In `src/acheron/shell/api/app.py`:
- Remove the import: `from acheron.shell.registry import WorkerRegistry`
- Add the import: `from acheron.shell.stores import create_worker_store`
- Change `WorkerRegistry()` → `create_worker_store()` in `create_app`

In `src/acheron/shell/api/routes/jobs.py`:
- The TYPE_CHECKING import `from acheron.shell.job_store import TrackedJob` stays unchanged. Remove the `JobStore` import if it exists.

- [ ] **Step 7: Update test imports — `WorkerRegistry` → `InMemoryWorkerStore` and `JobStore` → `InMemoryJobStore`**

For all test files that use `WorkerRegistry` or `JobStore` (the old classes), change to the new in-memory classes. Files to update:
- `tests/shell/conftest.py` — import + usage
- `tests/shell/test_orchestrator.py` — import + usage
- `tests/shell/test_step_handler.py` — import + usage
- `tests/shell/test_health_monitor.py` — import + usage
- `tests/shell/api/test_jobs.py` — import + usage
- `tests/integration/conftest.py` — import + usage
- `tests/integration/test_worker_registration.py` — import + usage
- `tests/integration/test_worker_integration.py` — import + usage

Specifically:
- `from acheron.shell.registry import WorkerRegistry` → `from acheron.shell.stores.memory import InMemoryWorkerStore`
- `from acheron.shell.job_store import JobStore, TrackedJob` → `from acheron.shell.job_store import TrackedJob` and `from acheron.shell.stores.memory import InMemoryJobStore`
- All `WorkerRegistry()` calls → `InMemoryWorkerStore()`
- All `JobStore()` calls → `InMemoryJobStore()`

A single shell session to do this:

```bash
cd /home/julia/devel/acheron
sed -i 's/from acheron.shell.registry import WorkerRegistry/from acheron.shell.stores.memory import InMemoryWorkerStore/g' \
    tests/shell/conftest.py \
    tests/shell/test_orchestrator.py \
    tests/shell/test_step_handler.py \
    tests/shell/test_health_monitor.py \
    tests/shell/api/test_jobs.py \
    tests/integration/conftest.py \
    tests/integration/test_worker_registration.py \
    tests/integration/test_worker_integration.py
sed -i 's/WorkerRegistry()/InMemoryWorkerStore()/g' \
    tests/shell/conftest.py \
    tests/shell/test_orchestrator.py \
    tests/shell/test_step_handler.py \
    tests/shell/test_health_monitor.py \
    tests/shell/api/test_jobs.py \
    tests/integration/conftest.py \
    tests/integration/test_worker_registration.py \
    tests/integration/test_worker_integration.py
```

- [ ] **Step 8: Remove the old `WorkerRegistry` and `JobStore` classes from their files**

Edit `src/acheron/shell/registry.py` to keep only the `RegisteredWorker` dataclass. The file should look like:

```python
"""Worker record type used by the registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from acheron.core.models import WorkerCapabilities


@dataclass
class RegisteredWorker:
    """A worker tracked by the registry."""

    worker_id: str
    endpoint: str
    transport: str
    capabilities: WorkerCapabilities
    consecutive_failures: int = 0
    last_health_check: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

Edit `src/acheron/shell/job_store.py` to keep only the `TrackedJob` dataclass. The file should look like:

```python
"""Tracked job record used by the job store."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from acheron.core.models import ExecutorStrategy, JobRequest, Plan, PlanResult


@dataclass
class TrackedJob:
    """A job tracked through its lifecycle."""

    job_id: str
    request: JobRequest
    strategy: ExecutorStrategy
    plan: Plan | None = None
    result: PlanResult | None = None
    status: str = "pending"
```

- [ ] **Step 9: Delete the old test files**

```bash
cd /home/julia/devel/acheron && rm tests/shell/test_registry.py tests/shell/test_job_store.py
```

- [ ] **Step 10: Run all tests to verify nothing broke**

Run: `cd /home/julia/devel/acheron && just validate 2>&1 | tail -8`
Expected: all checks pass, no import errors.

- [ ] **Step 11: Commit**

```bash
cd /home/julia/devel/acheron && git add -A && git commit -m "refactor(stores): move in-memory worker and job stores to stores subpackage"
```

---

## Task 4: Add factory functions

**Files:**
- Modify: `src/acheron/shell/stores/__init__.py`
- Create: `tests/shell/stores/test_stores_factory.py`

- [ ] **Step 1: Write the failing test**

Create `tests/shell/stores/test_stores_factory.py`:

```python
"""Tests for the store factory functions."""

import pytest

from acheron.shell.stores import create_job_store, create_worker_store
from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore


class TestCreateWorkerStore:
    def test_defaults_to_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ACHERON_STORE_BACKEND", raising=False)
        store = create_worker_store()
        assert isinstance(store, InMemoryWorkerStore)

    def test_explicit_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_STORE_BACKEND", "memory")
        store = create_worker_store()
        assert isinstance(store, InMemoryWorkerStore)

    def test_unknown_backend_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_STORE_BACKEND", "cassandra")
        with pytest.raises(ValueError, match="Unknown ACHERON_STORE_BACKEND"):
            create_worker_store()


class TestCreateJobStore:
    def test_defaults_to_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ACHERON_STORE_BACKEND", raising=False)
        store = create_job_store()
        assert isinstance(store, InMemoryJobStore)

    def test_explicit_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_STORE_BACKEND", "memory")
        store = create_job_store()
        assert isinstance(store, InMemoryJobStore)

    def test_unknown_backend_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_STORE_BACKEND", "cassandra")
        with pytest.raises(ValueError, match="Unknown ACHERON_STORE_BACKEND"):
            create_job_store()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/julia/devel/acheron && uv run pytest tests/shell/stores/test_stores_factory.py -v --no-cov`
Expected: FAIL with `ImportError: cannot import name 'create_worker_store'`

- [ ] **Step 3: Implement the factory functions**

Edit `src/acheron/shell/stores/__init__.py` to:

```python
"""Storage backends for the orchestrator."""

from __future__ import annotations

import os
from typing import NoReturn

from acheron.shell.stores.base import JobStore, WorkerStore


def _unknown_backend(backend: str) -> NoReturn:
    msg = f"Unknown ACHERON_STORE_BACKEND: {backend}"
    raise ValueError(msg)


def create_worker_store() -> WorkerStore:
    """Create a worker store based on the ``ACHERON_STORE_BACKEND`` env var.

    Returns an in-memory store when ``ACHERON_STORE_BACKEND`` is unset or
    ``"memory"``, a Redis-backed store when ``"redis"``. Other values raise
    ``ValueError``. The Redis backend fails fast on unreachable Redis.
    """
    from acheron.shell.stores.memory import InMemoryWorkerStore  # noqa: PLC0415

    backend = os.environ.get("ACHERON_STORE_BACKEND", "memory")
    match backend:
        case "memory":
            return InMemoryWorkerStore()
        case "redis":
            from acheron.shell.stores.redis import RedisWorkerStore  # noqa: PLC0415

            redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
            return RedisWorkerStore(redis_url)
        case _:
            _unknown_backend(backend)


def create_job_store() -> JobStore:
    """Create a job store based on the ``ACHERON_STORE_BACKEND`` env var."""
    from acheron.shell.stores.memory import InMemoryJobStore  # noqa: PLC0415

    backend = os.environ.get("ACHERON_STORE_BACKEND", "memory")
    match backend:
        case "memory":
            return InMemoryJobStore()
        case "redis":
            from acheron.shell.stores.redis import RedisJobStore  # noqa: PLC0415

            redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
            return RedisJobStore(redis_url)
        case _:
            _unknown_backend(backend)
```

- [ ] **Step 4: Run test to verify the memory cases pass**

Run: `cd /home/julia/devel/acheron && uv run pytest tests/shell/stores/test_stores_factory.py -v --no-cov`
Expected: all 6 tests pass. The Redis cases are covered in Task 5.

- [ ] **Step 5: Commit**

```bash
cd /home/julia/devel/acheron && git add src/acheron/shell/stores/__init__.py tests/shell/stores/test_stores_factory.py && git commit -m "feat(stores): add create_worker_store and create_job_store factories"
```

---

## Task 5: Add Redis worker store

**Files:**
- Create: `src/acheron/shell/stores/redis.py`
- Create: `tests/shell/stores/conftest.py`
- Create: `tests/shell/stores/test_redis_worker_store.py`
- Create: `tests/shell/stores/test_redis_job_store.py`

- [ ] **Step 1: Create the conftest with redis_url fixture**

Create `tests/shell/stores/conftest.py`:

```python
"""Shared fixtures for store tests."""

from __future__ import annotations

import redis
import pytest
from testcontainers.redis import RedisContainer


@pytest.fixture(scope="session")
def redis_container() -> RedisContainer:
    container = RedisContainer("redis:7-alpine")
    container.start()
    return container


@pytest.fixture
def redis_url(redis_container: RedisContainer) -> str:
    """Yield a Redis URL and FLUSHDB the database before each test."""
    url = redis_container.get_connection_url()
    client = redis.Redis.from_url(url)
    client.flushdb()
    client.close()
    return url
```

- [ ] **Step 2: Write the failing test for `RedisWorkerStore`**

Create `tests/shell/stores/test_redis_worker_store.py`:

```python
"""Integration tests for the Redis worker store."""

import pytest
import redis

from acheron.core.models import WorkerCapabilities, WorkerType
from acheron.shell.stores.redis import RedisWorkerStore


def _tts_caps() -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TTS,
        supported_languages_in=frozenset({"en"}),
        supported_languages_out=frozenset({"es"}),
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"wav"}),
        max_payload_bytes=None,
        batch_capable=True,
        model_source=None,
    )


@pytest.fixture
def store(redis_url: str) -> RedisWorkerStore:
    return RedisWorkerStore(redis_url)


class TestRegister:
    def test_register_and_get(self, store: RedisWorkerStore) -> None:
        store.register("w-1", "http://host:8001", "http", _tts_caps())
        w = store.get("w-1")
        assert w is not None
        assert w.worker_id == "w-1"
        assert w.endpoint == "http://host:8001"
        assert w.transport == "http"
        assert w.capabilities.worker_type == WorkerType.TTS
        assert w.capabilities.supported_languages_in == frozenset({"en"})
        assert w.capabilities.supported_languages_out == frozenset({"es"})

    def test_get_nonexistent(self, store: RedisWorkerStore) -> None:
        result = store.get("nope")
        assert result is None

    def test_unregister(self, store: RedisWorkerStore) -> None:
        store.register("w-1", "http://a", "http", _tts_caps())
        store.unregister("w-1")
        result = store.get("w-1")
        assert result is None

    def test_reregistration_overwrites(self, store: RedisWorkerStore) -> None:
        store.register("w-1", "http://old", "http", _tts_caps())
        store.register("w-1", "http://new", "http", _tts_caps())
        w = store.get("w-1")
        assert w is not None
        assert w.endpoint == "http://new"


class TestListing:
    def test_list_all(self, store: RedisWorkerStore) -> None:
        store.register("w-1", "http://a", "http", _tts_caps())
        store.register("w-2", "http://b", "http", _tts_caps())
        workers = store.list_all()
        ids = {w.worker_id for w in workers}
        assert ids == {"w-1", "w-2"}

    def test_find_by_type(self, store: RedisWorkerStore) -> None:
        store.register("tts-1", "http://a", "http", _tts_caps())
        asr = WorkerCapabilities(
            worker_type=WorkerType.ASR,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"en"}),
            supported_formats_in=frozenset({"mp3"}),
            supported_formats_out=frozenset({"text"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
        )
        store.register("asr-1", "http://b", "http", asr)
        tts_workers = store.find_by_type(WorkerType.TTS)
        assert len(tts_workers) == 1
        assert tts_workers[0].worker_id == "tts-1"


class TestHealthTracking:
    def test_failure_increments_and_removes(self, store: RedisWorkerStore) -> None:
        store.register("w-1", "http://a", "http", _tts_caps())
        assert not store.record_health_failure("w-1")
        assert not store.record_health_failure("w-1")
        assert store.record_health_failure("w-1")
        assert store.get("w-1") is None

    def test_success_resets_counter(self, store: RedisWorkerStore) -> None:
        store.register("w-1", "http://a", "http", _tts_caps())
        store.record_health_failure("w-1")
        store.record_health_failure("w-1")
        store.record_health_success("w-1")
        w = store.get("w-1")
        assert w is not None
        assert w.consecutive_failures == 0


class TestFailFast:
    def test_unreachable_redis_raises_on_init(self) -> None:
        with pytest.raises(redis.exceptions.RedisError):
            RedisWorkerStore("redis://localhost:1")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /home/julia/devel/acheron && uv run pytest tests/shell/stores/test_redis_worker_store.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'acheron.shell.stores.redis'`

- [ ] **Step 4: Implement `RedisWorkerStore` and `RedisJobStore`**

Create `src/acheron/shell/stores/redis.py`:

```python
"""Redis-backed implementations of the store ABCs."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import redis

from acheron.shell.stores.base import JobStore, WorkerStore

if TYPE_CHECKING:
    from acheron.core.models import WorkerCapabilities, WorkerType
    from acheron.shell.job_store import TrackedJob
    from acheron.shell.registry import RegisteredWorker


_WORKER_KEY = "worker:{worker_id}"
_WORKERS_SET = "workers"
_JOB_KEY = "job:{job_id}"
_JOBS_SET = "jobs"


def _serialize_capabilities(cap: WorkerCapabilities) -> str:
    return json.dumps(
        {
            "worker_type": cap.worker_type.value,
            "supported_languages_in": sorted(cap.supported_languages_in),
            "supported_languages_out": sorted(cap.supported_languages_out),
            "supported_formats_in": sorted(cap.supported_formats_in),
            "supported_formats_out": sorted(cap.supported_formats_out),
            "max_payload_bytes": cap.max_payload_bytes,
            "batch_capable": cap.batch_capable,
            "model_source": cap.model_source,
            "metadata": cap.metadata,
        },
        sort_keys=True,
    )


def _deserialize_capabilities(blob: str) -> WorkerCapabilities:
    from acheron.core.models import WorkerCapabilities, WorkerType  # noqa: PLC0415

    data = json.loads(blob)
    return WorkerCapabilities(
        worker_type=WorkerType(data["worker_type"]),
        supported_languages_in=frozenset(data["supported_languages_in"]),
        supported_languages_out=frozenset(data["supported_languages_out"]),
        supported_formats_in=frozenset(data["supported_formats_in"]),
        supported_formats_out=frozenset(data["supported_formats_out"]),
        max_payload_bytes=data["max_payload_bytes"],
        batch_capable=data["batch_capable"],
        model_source=data["model_source"],
        metadata=data["metadata"],
    )


def _serialize_worker_fields(
    endpoint: str,
    transport: str,
    capabilities: WorkerCapabilities,
    metadata: dict[str, Any],
    consecutive_failures: int,
    last_health_check: float | None,
) -> dict[str, str]:
    return {
        "endpoint": endpoint,
        "transport": transport,
        "consecutive_failures": str(consecutive_failures),
        "last_health_check": str(last_health_check) if last_health_check is not None else "",
        "capabilities_json": _serialize_capabilities(capabilities),
        "metadata_json": json.dumps(metadata, sort_keys=True),
    }


def _deserialize_worker(worker_id: str, fields: dict[str, str]) -> RegisteredWorker:
    from acheron.shell.registry import RegisteredWorker  # noqa: PLC0415

    last_hc = fields.get("last_health_check") or ""
    return RegisteredWorker(
        worker_id=worker_id,
        endpoint=fields["endpoint"],
        transport=fields["transport"],
        capabilities=_deserialize_capabilities(fields["capabilities_json"]),
        consecutive_failures=int(fields.get("consecutive_failures", "0")),
        last_health_check=float(last_hc) if last_hc else None,
        metadata=json.loads(fields.get("metadata_json", "{}")),
    )


def _serialize_job(job: TrackedJob) -> str:
    plan_dict = None
    if job.plan is not None:
        plan_dict = {
            "plan_id": job.plan.plan_id,
            "job_id": job.plan.job_id,
            "source_type": job.plan.source_type,
            "source_language": job.plan.source_language,
            "target_language": job.plan.target_language,
            "executor_strategy": job.plan.executor_strategy.value,
            "steps": [
                {
                    "step_id": s.step_id,
                    "type": s.type.value,
                    "depends_on": list(s.depends_on),
                    "status": s.status.value,
                    "payload": s.payload,
                    "batch": s.batch,
                }
                for s in job.plan.steps
            ],
        }
    return json.dumps(
        {
            "job_id": job.job_id,
            "source_type": job.request.__class__.__name__,
            "request": {
                "source_path": job.request.source_path,
                "source_language": job.request.source_language,
                "target_language": job.request.target_language,
                **(
                    {"asr_model": job.request.asr_model}
                    if hasattr(job.request, "asr_model") and job.request.asr_model is not None
                    else {}
                ),
            },
            "strategy": job.strategy.value,
            "status": job.status,
            "plan": plan_dict,
            "result": None,
        },
        sort_keys=True,
    )


def _deserialize_job(blob: str) -> TrackedJob:
    from acheron.core.models import (  # noqa: PLC0415
        AudioRequest,
        EpubRequest,
        ExecutorStrategy,
        Plan,
        PlanStep,
        StepStatus,
        WorkerType,
    )
    from acheron.shell.job_store import TrackedJob  # noqa: PLC0415

    data = json.loads(blob)
    request_cls = EpubRequest if data["source_type"] == "EpubRequest" else AudioRequest
    request = request_cls(
        source_path=data["request"]["source_path"],
        source_language=data["request"]["source_language"],
        target_language=data["request"]["target_language"],
        asr_model=data["request"].get("asr_model"),
    )
    plan = None
    if data["plan"] is not None:
        plan = Plan(
            plan_id=data["plan"]["plan_id"],
            job_id=data["plan"]["job_id"],
            source_type=data["plan"]["source_type"],
            source_language=data["plan"]["source_language"],
            target_language=data["plan"]["target_language"],
            executor_strategy=ExecutorStrategy(data["plan"]["executor_strategy"]),
            steps=tuple(
                PlanStep(
                    step_id=s["step_id"],
                    type=WorkerType(s["type"]),
                    depends_on=tuple(s["depends_on"]),
                    status=StepStatus(s["status"]),
                    payload=s["payload"],
                    batch=s["batch"],
                )
                for s in data["plan"]["steps"]
            ),
        )
    return TrackedJob(
        job_id=data["job_id"],
        request=request,
        strategy=ExecutorStrategy(data["strategy"]),
        plan=plan,
        status=data["status"],
    )


class RedisWorkerStore(WorkerStore):
    """Redis-backed worker store. Survives orchestrator restarts.

    Uses the synchronous ``redis.Redis`` client. Sync calls from an async
    context block the event loop briefly; acceptable for v1 because Redis
    calls are fast and infrequent. If this becomes a bottleneck, migrate the
    ABCs to async and switch to ``redis.asyncio.Redis``.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
        # Fail fast on unreachable Redis at construction.
        self._redis.ping()

    def close(self) -> None:
        self._redis.close()

    def register(
        self,
        worker_id: str,
        endpoint: str,
        transport: str,
        capabilities: WorkerCapabilities,
        metadata: dict[str, object] | None = None,
    ) -> None:
        fields = _serialize_worker_fields(
            endpoint=endpoint,
            transport=transport,
            capabilities=capabilities,
            metadata=dict(metadata or {}),
            consecutive_failures=0,
            last_health_check=time.time(),
        )
        pipe = self._redis.pipeline(transaction=True)
        pipe.hset(_WORKER_KEY.format(worker_id=worker_id), mapping=fields)
        pipe.sadd(_WORKERS_SET, worker_id)
        pipe.execute()

    def unregister(self, worker_id: str) -> None:
        pipe = self._redis.pipeline(transaction=True)
        pipe.srem(_WORKERS_SET, worker_id)
        pipe.delete(_WORKER_KEY.format(worker_id=worker_id))
        pipe.execute()

    def get(self, worker_id: str) -> RegisteredWorker | None:
        fields = self._redis.hgetall(_WORKER_KEY.format(worker_id=worker_id))
        if not fields:
            return None
        return _deserialize_worker(worker_id, fields)

    def list_all(self) -> tuple[RegisteredWorker, ...]:
        ids = self._redis.smembers(_WORKERS_SET)
        if not ids:
            return ()
        pipe = self._redis.pipeline(transaction=False)
        for wid in ids:
            pipe.hgetall(_WORKER_KEY.format(worker_id=wid))
        results = pipe.execute()
        workers: list[RegisteredWorker] = []
        for wid, fields in zip(ids, results, strict=True):
            if fields:
                workers.append(_deserialize_worker(wid, fields))
        return tuple(workers)

    def find_by_type(self, worker_type: WorkerType) -> tuple[RegisteredWorker, ...]:
        return tuple(w for w in self.list_all() if w.capabilities.worker_type == worker_type)

    def find_by_language(self, src: str, dst: str) -> tuple[RegisteredWorker, ...]:
        return tuple(
            w
            for w in self.list_all()
            if src in w.capabilities.supported_languages_in and dst in w.capabilities.supported_languages_out
        )

    def record_health_failure(self, worker_id: str) -> bool:
        key = _WORKER_KEY.format(worker_id=worker_id)
        if not self._redis.exists(key):
            return False
        new_count = self._redis.hincrby(key, "consecutive_failures", 1)
        self._redis.hset(key, "last_health_check", str(time.time()))
        if new_count >= self.max_failures:
            self.unregister(worker_id)
            return True
        return False

    def record_health_success(self, worker_id: str) -> None:
        key = _WORKER_KEY.format(worker_id=worker_id)
        pipe = self._redis.pipeline(transaction=True)
        pipe.hset(key, "consecutive_failures", "0")
        pipe.hset(key, "last_health_check", str(time.time()))
        pipe.execute()


class RedisJobStore(JobStore):
    """Redis-backed job store. Survives orchestrator restarts.

    Sync client for the same reasons as ``RedisWorkerStore``.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
        self._redis.ping()

    def close(self) -> None:
        self._redis.close()

    def put(self, job: TrackedJob) -> None:
        pipe = self._redis.pipeline(transaction=True)
        pipe.set(_JOB_KEY.format(job_id=job.job_id), _serialize_job(job))
        pipe.sadd(_JOBS_SET, job.job_id)
        pipe.execute()

    def get(self, job_id: str) -> TrackedJob | None:
        blob = self._redis.get(_JOB_KEY.format(job_id=job_id))
        if blob is None:
            return None
        return _deserialize_job(blob)

    def list_all(self) -> tuple[TrackedJob, ...]:
        ids = self._redis.smembers(_JOBS_SET)
        if not ids:
            return ()
        pipe = self._redis.pipeline(transaction=False)
        for jid in ids:
            pipe.get(_JOB_KEY.format(job_id=jid))
        results = pipe.execute()
        jobs: list[TrackedJob] = []
        for blob in results:
            if blob is not None:
                jobs.append(_deserialize_job(blob))
        return tuple(jobs)
```

- [ ] **Step 5: Run the worker store tests to verify they pass**

Run: `cd /home/julia/devel/acheron && uv run pytest tests/shell/stores/test_redis_worker_store.py -v --no-cov`
Expected: all 9 tests pass. Requires Docker to be running for testcontainers.

- [ ] **Step 6: Write the failing test for `RedisJobStore`**

Create `tests/shell/stores/test_redis_job_store.py`:

```python
"""Integration tests for the Redis job store."""

import pytest
import redis

from acheron.core.models import EpubRequest, ExecutorStrategy
from acheron.shell.job_store import TrackedJob
from acheron.shell.stores.redis import RedisJobStore


def _tracked(job_id: str = "job-1") -> TrackedJob:
    return TrackedJob(
        job_id=job_id,
        request=EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
        strategy=ExecutorStrategy.BATCH_ASYNC,
    )


@pytest.fixture
def store(redis_url: str) -> RedisJobStore:
    return RedisJobStore(redis_url)


class TestPut:
    def test_put_and_get(self, store: RedisJobStore) -> None:
        job = _tracked()
        store.put(job)
        loaded = store.get("job-1")
        assert loaded is not None
        assert loaded.job_id == "job-1"
        assert loaded.status == "pending"
        assert loaded.request.source_path == "/input/book.epub"
        assert loaded.request.source_language == "en"
        assert loaded.request.target_language == "es"
        assert loaded.strategy == ExecutorStrategy.BATCH_ASYNC

    def test_get_nonexistent(self, store: RedisJobStore) -> None:
        result = store.get("nope")
        assert result is None

    def test_put_overwrites(self, store: RedisJobStore) -> None:
        store.put(_tracked("j-1"))
        job2 = _tracked("j-1")
        job2.status = "running"
        store.put(job2)
        loaded = store.get("j-1")
        assert loaded is not None
        assert loaded.status == "running"


class TestList:
    def test_list_all(self, store: RedisJobStore) -> None:
        store.put(_tracked("j-1"))
        store.put(_tracked("j-2"))
        store.put(_tracked("j-3"))
        jobs = store.list_all()
        assert {j.job_id for j in jobs} == {"j-1", "j-2", "j-3"}

    def test_list_empty(self, store: RedisJobStore) -> None:
        jobs = store.list_all()
        assert jobs == ()


class TestFailFast:
    def test_unreachable_redis_raises_on_init(self) -> None:
        with pytest.raises(redis.exceptions.RedisError):
            RedisJobStore("redis://localhost:1")
```

- [ ] **Step 7: Run test to verify it passes**

Run: `cd /home/julia/devel/acheron && uv run pytest tests/shell/stores/test_redis_job_store.py -v --no-cov`
Expected: all 7 tests pass.

- [ ] **Step 8: Run all factory tests including the redis backend (smoke test)**

Run: `cd /home/julia/devel/acheron && uv run pytest tests/shell/stores/test_stores_factory.py -v --no-cov`
Expected: PASS. The factory's `redis` case wasn't in the test file (we tested only memory + unknown in Task 4). The Redis factories get exercised via the Redis store tests instead.

- [ ] **Step 9: Commit**

```bash
cd /home/julia/devel/acheron && git add src/acheron/shell/stores/redis.py tests/shell/stores/conftest.py tests/shell/stores/test_redis_worker_store.py tests/shell/stores/test_redis_job_store.py && git commit -m "feat(stores): add Redis-backed WorkerStore and JobStore"
```

---

## Task 6: Wire orchestrator defaults and lifespan shutdown

**Files:**
- Modify: `src/acheron/shell/orchestrator.py`
- Modify: `src/acheron/shell/api/app.py`

- [ ] **Step 1: Update `Orchestrator.__init__` to use factories by default and accept a `job_store`**

In `src/acheron/shell/orchestrator.py`, find the `Orchestrator.__init__` and replace it with:

```python
    def __init__(
        self,
        registry: WorkerStore,
        cache: PlanCache,
        handler: StepHandler | None = None,
        *,
        job_store: JobStore | None = None,
    ) -> None:
        self._registry = registry
        self._cache = cache
        self._register_built_in_local_workers()
        self._handler = handler or create_step_handler(registry)
        self._job_store = job_store if job_store is not None else create_job_store()
        self._tasks: set[asyncio.Task[None]] = set()
        self._health_monitor = HealthMonitor(registry)
```

The full signature takes `WorkerStore` (the ABC) and `JobStore | None` (the ABC, optional with factory default).

- [ ] **Step 2: Update `app.py` to use the factory and close stores on shutdown**

In `src/acheron/shell/api/app.py`:

Replace the `from acheron.shell.registry import WorkerRegistry` import with:
```python
from acheron.shell.stores import create_worker_store
```

Update the `create_app` body:
```python
def create_app(
    registry: WorkerStore | None = None,
    cache: PlanCache | None = None,
    data_dir: Path = Path("/data/jobs"),
) -> FastAPI:
    """Create and configure the FastAPI application."""
    if registry is None:
        registry = create_worker_store()
    if cache is None:
        cache = PlanCache(data_dir)
    ...
```

Add the type import for `WorkerStore`:
```python
from acheron.shell.stores.base import WorkerStore
```

Update the lifespan to close the registry on shutdown:
```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage orchestrator lifecycle — start on startup, stop on shutdown."""
    orch: Orchestrator = app.state.orchestrator
    await orch.start()
    try:
        yield
    finally:
        await orch.shutdown()
        orch._registry.close()  # noqa: SLF001
        orch._job_store.close()  # noqa: SLF001
```

- [ ] **Step 3: Run all tests to verify nothing broke**

Run: `cd /home/julia/devel/acheron && just validate 2>&1 | tail -8`
Expected: all checks pass.

- [ ] **Step 4: Commit**

```bash
cd /home/julia/devel/acheron && git add src/acheron/shell/orchestrator.py src/acheron/shell/api/app.py && git commit -m "refactor: orchestrator and create_app use factory stores by default"
```

---

## Task 7: Final validation

- [ ] **Step 1: Run full validation**

Run: `cd /home/julia/devel/acheron && just validate 2>&1 | tail -20`
Expected: all checks pass. Redis tests will be skipped if Docker isn't running — that's OK.

- [ ] **Step 2: Verify the plan tasks are complete**

Confirm:
- [ ] `testcontainers[redis]` added to dev deps
- [ ] `WorkerStore` and `JobStore` ABCs exist in `src/acheron/shell/stores/base.py`
- [ ] `InMemoryWorkerStore` and `InMemoryJobStore` exist in `src/acheron/shell/stores/memory.py`
- [ ] `RedisWorkerStore` and `RedisJobStore` exist in `src/acheron/shell/stores/redis.py`
- [ ] `create_worker_store()` and `create_job_store()` factory functions read `ACHERON_STORE_BACKEND`
- [ ] Orchestrator and `create_app` use the factories by default
- [ ] Lifespan closes stores on shutdown
- [ ] All in-memory tests pass
- [ ] All Redis tests pass (requires Docker)
- [ ] All existing tests still pass
