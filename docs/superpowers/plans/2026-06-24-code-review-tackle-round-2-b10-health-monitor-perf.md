---
bundle: B10
name: Health monitor & perf
severity: MIXED
stories: 7
m_effort: 3
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B10 — Health monitor & perf (PERF-004, -005, -007, -008, OBS-001, TEST-007, CORR-012)

> **For agentic workers:** Use the **Common Workflow** from the main plan. **Tackle in this order: OBS-001 (drain) → TEST-007 + CORR-012 (transitions) → PERF-* (concurrency).** OBS-001 and the TEST-007/CORR-012 pair are M-effort and need full TDD; PERF-* are S-effort.

**Bundle summary:** Drain in-flight `_execute` tasks on shutdown (M); add unit tests for health-monitor state transitions (M); bound the BOOTING grace period (M); make health probes and HTTP workers reuse an `AsyncClient`. The M-effort stories are the bundle anchors; PERF-* are smaller wins.

**Expected commits:** 5-6.

---

## Tasks (tackle in order)

### Task 1: OBS-001 (M) — Shutdown doesn't drain in-flight `_execute` tasks

**Story:** `docs/code_review/operations.md` § OBS-001 (MEDIUM, M effort).

**Files:**
- Modify: `src/acheron/shell/orchestrator.py` (the `Orchestrator` class — `_execute`, `stop`, and any task-management code).
- Test: `tests/shell/test_orchestrator.py` (add `TestOrchestratorShutdownDrain` class).

#### Step 1: Write the failing tests

```python
import asyncio
import pytest
from acheron.shell.orchestrator import Orchestrator


class TestOrchestratorShutdownDrain:
    """OBS-001: in-flight _execute tasks must be drained or cancelled on stop()."""

    @pytest.mark.asyncio
    async def test_in_flight_execute_finishes_on_stop(self, tmp_path, monkeypatch):
        # Register a slow handler that sleeps 0.5s before returning SUCCESS.
        from acheron.shell.stores.memory import InMemoryWorkerStore
        from acheron.shell.cache import PlanCache
        from acheron.shell.config import Settings
        from acheron.core.models import EpubRequest, ExecutorStrategy

        slow_done = asyncio.Event()
        async def slow_handler(*args, **kwargs):
            await asyncio.sleep(0.5)
            slow_done.set()
            return {"outputs": []}

        reg = InMemoryWorkerStore()
        await reg.register("w", "http://w", "http", _caps())  # _caps helper
        settings = Settings()
        settings.orchestrator.data_dir = tmp_path
        orch = Orchestrator(reg, PlanCache(tmp_path), slow_handler, settings=settings)
        await orch.start()

        request = EpubRequest(source_path="/in.epub", source_language="en", target_language="es")
        submit_task = asyncio.create_task(orch.submit_job(request, ExecutorStrategy.STREAMING))
        # Give the submit a chance to start _execute
        await asyncio.sleep(0.05)
        # Now stop the orchestrator
        stop_task = asyncio.create_task(orch.stop())
        # The stop should wait for the in-flight _execute to finish
        await asyncio.wait_for(stop_task, timeout=2.0)
        # The handler should have been awaited
        assert slow_done.is_set()
        # And submit should also have finished
        await submit_task

    @pytest.mark.asyncio
    async def test_in_flight_execute_cancelled_after_grace(self, tmp_path):
        # Same setup, but the handler sleeps 10s. stop() should cancel after a 1s grace.
        ...
        # After stop, assert the handler's CancelledError was raised.
```

The exact test setup depends on the `Orchestrator` constructor and `_execute` interface. Inspect the current code first. The 2 tests above are the contract: in-flight tasks finish if quick, or are cancelled after a grace period.

#### Step 2: Run tests to verify they fail

```bash
uv run pytest tests/shell/test_orchestrator.py::TestOrchestratorShutdownDrain -xvs
```

Expected: 2 tests FAIL.

#### Step 3: Implement the drain

In `src/acheron/shell/orchestrator.py`:

1. Add `_in_flight: set[asyncio.Task]` to `__init__`.
2. In `_execute`, after the handler is awaited, `task.add_done_callback(self._in_flight.discard)` and `self._in_flight.add(task)`.
3. In `stop` (or `shutdown`):

```python
async def stop(self) -> None:
    self._shutting_down = True
    if self._in_flight:
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._in_flight, return_exceptions=True),
                timeout=5.0,
            )
        except TimeoutError:
            for task in self._in_flight:
                task.cancel()
            await asyncio.gather(*self._in_flight, return_exceptions=True)
    # ... existing shutdown logic
```

Add a `Settings.orchestrator.shutdown_grace_seconds: float = 5.0` (configurable) and use it.

#### Step 4: Run tests, verify gate, subagent passes, commit

Standard TDD + per-story cycle.

**Commit:** `fix(OBS-001): drain in-flight _execute tasks on shutdown with 5s grace`.

---

### Task 2: TEST-007 (M) — `HealthMonitor._handle_failure` BOOTING→OFFLINE and OFFLINE→HEALTHY transitions untested

**Story:** `docs/code_review/verification.md` § TEST-007 (MEDIUM, M effort).

**Files:**
- Test: `tests/shell/test_health.py` (add `TestHealthMonitorTransitions` class).

#### Step 1: Write the failing tests

```python
import pytest
from acheron.shell.health import HealthMonitor
from acheron.shell.stores.memory import InMemoryWorkerStore


class TestHealthMonitorTransitions:
    """TEST-007: HealthMonitor state-machine transitions."""

    def _monitor(self, store):
        return HealthMonitor(store=store, providers={}, settings=_settings())

    @pytest.mark.asyncio
    async def test_booting_to_offline_after_n_failures(self):
        # Provider that returns BOOTING; after N consecutive failures, status flips to OFFLINE.
        ...

    @pytest.mark.asyncio
    async def test_offline_to_healthy_on_success(self):
        # Provider returns OFFLINE; one successful check flips to HEALTHY.
        ...

    @pytest.mark.asyncio
    async def test_booting_to_offline_after_grace_period(self):
        # Provider returns BOOTING; after the grace period (set in CORR-012), flip to OFFLINE.
        # This test exercises BOTH TEST-007 and CORR-012; it lands after CORR-012's fix.
        ...

    @pytest.mark.asyncio
    async def test_healthy_to_offline_on_failure(self):
        # Provider returns HEALTHY; one failed check flips to OFFLINE.
        ...
```

4 tests. The exact provider interface depends on the existing test setup; inspect the test file for `_health_provider_double` or similar helper.

#### Step 2-5: Run tests, implement if needed, verify gate, subagent passes, commit

**Important:** if the BOOTING→OFFLINE transition after grace period is needed, this test depends on Task 3 (CORR-012). Land CORR-012 first or in the same commit.

**Commit:** `test(TEST-007): add HealthMonitor state-transition tests (BOOTING→OFFLINE, OFFLINE→HEALTHY, etc.)`.

---

### Task 3: CORR-012 (M) — Health monitor trusts provider BOOTING status without bounding duration

**Story:** `docs/code_review/correctness.md` § CORR-012 (LOW, M effort).

**Files:**
- Modify: `src/acheron/shell/health.py` (the `HealthMonitor` class — track `booting_since: float | None` per worker).
- Modify: `src/acheron/shell/config.py` (add `Settings.orchestrator.health.boot_grace_seconds: float = 60.0`).
- Test: `tests/shell/test_health.py` (add a test that asserts BOOTING > grace flips to OFFLINE).

#### Step 1: Write the failing test

```python
@pytest.mark.asyncio
async def test_booting_flips_to_offline_after_grace(self, monkeypatch):
    # Provider always returns BOOTING.
    # Mock time.monotonic to advance past the grace period.
    # Assert the worker's status is OFFLINE.
    ...
```

#### Step 2-5: Run test, implement, verify gate, subagent passes, commit

**Implementation sketch:**

```python
# In HealthMonitor:
def _handle_failure(self, worker_id: str, provider_name: str, exc: Exception) -> None:
    worker = self._store.get(worker_id)
    if worker.status == WorkerStatus.BOOTING:
        if worker.booting_since is None:
            worker.booting_since = time.monotonic()
        elapsed = time.monotonic() - worker.booting_since
        if elapsed > self._settings.orchestrator.health.boot_grace_seconds:
            worker.status = WorkerStatus.OFFLINE
            worker.booting_since = None
    # ... existing logic
```

(Add `booting_since: float | None = None` to `WorkerStatus` or the store's worker model.)

**Commit:** `fix(CORR-012): bound BOOTING status with configurable boot_grace_seconds`.

---

### Task 4: PERF-004 — `HealthMonitor._check_all` processes worker results sequentially with W Redis round-trips

**Files:** `src/acheron/shell/health.py`; test.

**Change:** replace the sequential `for worker in workers: ...` with `await asyncio.gather(*(self._check_one(w) for w in workers), return_exceptions=True)`. Also batch the Redis writes with a pipeline.

**Test:** add a test with 10 workers; assert the total wall time is < 100ms (calibrate to current overhead). Existing tests should still pass.

**Commit:** `perf(PERF-004): gather HealthMonitor worker checks + batch Redis writes`.

---

### Task 5: PERF-005 — provider status checks in `_handle_failure` run sequentially and can starve the loop

**Files:** same as Task 4.

**Change:** same `asyncio.gather` pattern + a `asyncio.Semaphore(10)` to bound concurrency.

**Test:** existing tests should still pass.

**Commit:** `perf(PERF-005): gather provider checks in _handle_failure with bounded concurrency`.

---

### Task 6: PERF-007 — per-call `httpx.AsyncClient` in health probes and pricing refresh

**Files:** `src/acheron/shell/health_providers.py`; `src/acheron/worker_sdk/pricing.py`.

**Change:** introduce a single module-level `httpx.AsyncClient` initialised in the provider's `__init__` (or via a context manager); close it in a `close()` method called from `Orchestrator.stop()`.

**Test:** existing tests should still pass; add 1 test asserting only 1 client is created per provider.

**Commit:** `perf(PERF-007): reuse httpx.AsyncClient in health probes and pricing refresh`.

---

### Task 7: PERF-008 — `HttpWorker._post_multipart` constructs new `httpx.AsyncClient` per call

**Files:** `src/acheron/shell/transports/http.py`; test.

**Change:** same as Task 6 — single `AsyncClient` per `HttpWorker` instance, closed on shutdown.

**Test:** assert only 1 client is created per `HttpWorker`.

**Commit:** `perf(PERF-008): reuse httpx.AsyncClient in HttpWorker`.

---

## Bundle summary

- **Stories:** 7 (3 M-effort: OBS-001, TEST-007, CORR-012; 4 S-effort: PERF-004, -005, -007, -008).
- **Commits:** 5-6 (Tasks 1-3 are M-effort and 1 each; Tasks 4-7 are S-effort and may share 2 commits).
- **Order matters:** OBS-001 first (sets the drain pattern that other shutdown logic may reuse). TEST-007 second (may depend on CORR-012's grace-period logic). PERF-* last.
- **Surface to user if:** the drain grace period needs to be configurable per-job (out of scope for this story), or the new `_in_flight` set conflicts with the existing task management.
