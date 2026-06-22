# Layer 11 — Decoupled Health Checks & Dashboard Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add platform-aware health checks (RunPod/HF) that distinguish booting vs offline workers, surface `last_error` and `WorkerStatus` through the API, and update the dashboard with a backend-connection indicator and per-worker status badges + error viewer.

**Architecture:** A new `HealthProvider` plugin layer in `shell/health_providers.py` queries platform APIs when the orchestrator's HTTP/gRPC probe fails. The `HealthMonitor` calls the provider named in `WorkerCapabilities.metadata["health_provider"]` and writes `WorkerStatus` + `last_error` back through a new `WorkerStore.set_worker_status` method. The `/workers` API and dashboard partials consume the two new fields. A new orchestrator-owned `/partials/status` HTML endpoint feeds a dashboard status circle.

**Tech Stack:** Python 3.14, FastAPI, httpx, pydantic-settings, Jinja2/HTMX, respx (HTTP mocking), pytest-asyncio.

## Scope

This plan implements **Sections 2 and 3** of the [deployment-and-dashboard design spec](../specs/2026-06-20-deployment-and-dashboard-design.md):
- §2 Decoupled Provider Health Checks (WorkerStatus enum, HealthProvider ABC, RunPod + HF providers, `last_error`, config, probing).
- §3 Dashboard Updates (backend status circle, worker status badges, "View Error" viewer).

**Deferred to a separate plan:** §1 Decoupled, Model-Specific Workers + CI/CD publish to GHCR. That subsystem requires Docker/CUDA build context and GPU worker skeletons to validate, and is independent of the health-check/dashboard work. It will get its own spec → plan → implementation cycle.

## Design Decisions (finalized)

1. **`/partials/status` ownership.** The spec says the endpoint lives in the orchestrator API, "not the dashboard server itself." The orchestrator serves `GET /partials/status` (HTML snippet = the status logic). The dashboard proxies it via its own same-origin `/partials/status` route (fetches the orchestrator's partial, returns red "Disconnected" HTML on failure). This is the only way the browser can reach it in the compose setup (the dashboard container's `orchestrator_url` is a Docker-internal hostname the browser cannot resolve), and it keeps the status *logic* in the orchestrator as the spec requires. The dashboard route is a thin pass-through, not logic.

2. **`health_provider` / `health_endpoint_id` live in `WorkerCapabilities.metadata`.** Per spec. They are JSON-serializable and already round-trip through Redis via `_serialize_capabilities`. Typos are silently ignored (best-effort read in `HealthMonitor`). `health_endpoint_id` is provider-specific: for RunPod it is the serverless endpoint id; for HuggingFace it is `namespace/name`.

3. **Booting workers are not removed.** When the platform API reports the instance is initializing/running-but-unreachable, the worker is marked `BOOTING` and the failure counter is *not* incremented. OFFLINE (or no provider) workers follow the existing 3-strike removal. A boot timeout is a future extension (YAGNI for now).

4. **`HealthProbeResult` replaces `bool` return from `HealthCheckFn`.** Captures the error string for `last_error`. This is a refactor of the existing `HealthCheckFn` signature and its tests (greenfield — no legacy fallbacks).

5. **`${VAR}` env-var expansion in YAML.** `acheron.yaml` values like `api_key: "${RUNPOD_API_KEY}"` are expanded by a recursive helper in the YAML settings source. Unset vars expand to empty string (falsy → provider not created).

## File Structure

**Create:**
- `src/acheron/shell/health_providers.py` — `HealthProvider` ABC, `RunPodHealthProvider`, `HuggingFaceHealthProvider`, `HealthProviders` container, `create_health_providers` factory.
- `src/acheron/shell/api/routes/partials.py` — orchestrator `GET /partials/status` HTML endpoint.
- `tests/shell/test_health_providers.py` — unit tests for providers + factory.
- `tests/shell/api/test_partials.py` — tests for the orchestrator status partial.

**Modify:**
- `src/acheron/core/models.py` — add `WorkerStatus` enum.
- `src/acheron/shell/registry.py` — add `status` + `last_error` to `RegisteredWorker`.
- `src/acheron/shell/stores/base.py` — add `set_worker_status` to `WorkerStore` ABC; extend `record_health_success` contract.
- `src/acheron/shell/stores/memory.py` — implement `set_worker_status`; reset status/last_error in `record_health_success`.
- `src/acheron/shell/stores/redis.py` — serialize `status`/`last_error`; implement `set_worker_status`; reset in `record_health_success`.
- `src/acheron/shell/health.py` — `HealthProbeResult`; update `HealthCheckFn`, probe functions, `HealthMonitor` (providers + status/last_error logic).
- `src/acheron/shell/config.py` — add `ProvidersSettings`; `${VAR}` expansion in YAML source.
- `src/acheron/shell/api/schemas.py` — add `status` + `last_error` to `WorkerResponse`.
- `src/acheron/shell/api/routes/workers.py` — map `status` + `last_error` in responses.
- `src/acheron/shell/api/app.py` — register partials router.
- `src/acheron/shell/orchestrator.py` — create `HealthProviders` from settings, pass to `HealthMonitor`.
- `dashboard/app.py` — add `/partials/status` proxy route.
- `dashboard/templates/index.html` — status circle + new badge CSS.
- `dashboard/templates/partials/workers.html` — status badges + "View Error" `<details>`.
- `acheron.yaml.example` — add `providers:` section.
- `tests/core/test_models.py` — `WorkerStatus` enum tests.
- `tests/shell/stores/test_stores_async.py` — add `set_worker_status` to coroutine contract.
- `tests/shell/stores/test_memory_worker_store.py` — `set_worker_status` + success-resets-status tests.
- `tests/shell/stores/test_redis_worker_store.py` — `status`/`last_error` round-trip + `set_worker_status` tests.
- `tests/shell/test_health_monitor.py` — update for `HealthProbeResult`; add provider/BOOTING/OFFLINE tests.
- `tests/shell/api/test_workers.py` — assert `status` + `last_error` fields.
- `dashboard/tests/test_dashboard.py` — status partial tests + workers partial status/error assertions.
- `docs/superpowers/specs/2026-06-16-implementation-roadmap.md` — mark Layer 11 (§2/§3) done.
- `docs/superpowers/specs/2026-06-16-acheron-design.md` — document WorkerStatus, HealthProvider, last_error, /partials/status.
- `docs/superpowers/specs/2026-06-20-deployment-and-dashboard-design.md` — record finalized design decisions.

---

### Task 1: WorkerStatus Enum + RegisteredWorker Fields

**Files:**
- Modify: `src/acheron/core/models.py`
- Modify: `src/acheron/shell/registry.py`
- Modify: `tests/core/test_models.py`

- [ ] **Step 1: Write the failing test for WorkerStatus**

Add to `tests/core/test_models.py` — import `WorkerStatus` in the existing import block and add a parametrized test. First update the import:

```python
from acheron.core.models import (
    ExecutorStrategy,
    Job,
    JobMetrics,
    JobResult,
    JobStatus,
    OutputFile,
    Plan,
    PlanResult,
    PlanStatus,
    PlanStep,
    StepStatus,
    WorkerCapabilities,
    WorkerStatus,
    WorkerType,
)
```

Then add to the `TestEnums` class (after the existing `test_worker_type_values`):

```python
    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (WorkerStatus.HEALTHY, "healthy"),
            (WorkerStatus.BOOTING, "booting"),
            (WorkerStatus.OFFLINE, "offline"),
        ],
    )
    def test_worker_status_values(self, member: WorkerStatus, value: str) -> None:
        assert member.value == value
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_models.py::TestEnums::test_worker_status_values -v`
Expected: FAIL with `ImportError: cannot import name 'WorkerStatus'`

- [ ] **Step 3: Add WorkerStatus to core/models.py**

Add after the `ExecutorStrategy` enum (after line 55):

```python
class WorkerStatus(Enum):
    """Health status of a registered worker."""

    HEALTHY = "healthy"
    BOOTING = "booting"
    OFFLINE = "offline"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_models.py::TestEnums::test_worker_status_values -v`
Expected: PASS

- [ ] **Step 5: Add `status` + `last_error` to RegisteredWorker**

Replace the body of `src/acheron/shell/registry.py` with:

```python
"""Worker record type used by the registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from acheron.core.models import WorkerStatus

if TYPE_CHECKING:
    from acheron.core.models import JsonValue, WorkerCapabilities


@dataclass
class RegisteredWorker:
    """A worker tracked by the registry.

    ``metadata`` holds JSON-serializable values only. In-process callables
    (e.g. local worker handlers) must NOT be stored here; use a side dict on
    the orchestrator instead.
    """

    worker_id: str
    endpoint: str
    transport: str
    capabilities: WorkerCapabilities
    consecutive_failures: int = 0
    last_health_check: float | None = None
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    last_error: str | None = None
    status: WorkerStatus = WorkerStatus.HEALTHY
```

- [ ] **Step 6: Run the full unit suite to confirm no regressions**

Run: `uv run pytest tests/core/ tests/shell/stores/ -v`
Expected: PASS (existing store tests still pass — new fields have defaults)

- [ ] **Step 7: Commit**

```bash
git add src/acheron/core/models.py src/acheron/shell/registry.py tests/core/test_models.py
git commit -m "feat(models): add WorkerStatus enum and last_error/status on RegisteredWorker"
```

---

### Task 2: WorkerStore ABC + InMemory Implementation

**Files:**
- Modify: `src/acheron/shell/stores/base.py`
- Modify: `src/acheron/shell/stores/memory.py`
- Modify: `tests/shell/stores/test_stores_async.py`
- Modify: `tests/shell/stores/test_memory_worker_store.py`

- [ ] **Step 1: Write the failing test for set_worker_status + success-resets-status**

Add to `tests/shell/stores/test_memory_worker_store.py`. First add the import:

```python
from acheron.core.models import WorkerCapabilities, WorkerStatus, WorkerType
```

Then add a new test class at the end of the file:

```python
class TestWorkerStatusTracking:
    @pytest.mark.asyncio
    async def test_set_worker_status_updates_fields(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w-1", "http://a", "http", _tts_caps())
        await reg.set_worker_status("w-1", WorkerStatus.BOOTING, "cold start")
        w = await reg.get("w-1")
        assert w is not None
        assert w.status == WorkerStatus.BOOTING
        assert w.last_error == "cold start"

    @pytest.mark.asyncio
    async def test_set_worker_status_nonexistent_is_noop(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.set_worker_status("nope", WorkerStatus.OFFLINE, "err")

    @pytest.mark.asyncio
    async def test_record_health_success_resets_status_and_error(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w-1", "http://a", "http", _tts_caps())
        await reg.set_worker_status("w-1", WorkerStatus.OFFLINE, "boom")
        await reg.record_health_success("w-1")
        w = await reg.get("w-1")
        assert w is not None
        assert w.status == WorkerStatus.HEALTHY
        assert w.last_error is None

    @pytest.mark.asyncio
    async def test_new_worker_defaults_to_healthy(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w-1", "http://a", "http", _tts_caps())
        w = await reg.get("w-1")
        assert w is not None
        assert w.status == WorkerStatus.HEALTHY
        assert w.last_error is None
```

Also update `tests/shell/stores/test_stores_async.py` — add `"set_worker_status"` to the `TestWorkerStoreABCCoroutineContract` parametrize list (after `"record_health_success"`):

```python
        [
            "register",
            "unregister",
            "get",
            "list_all",
            "find_by_type",
            "find_by_language",
            "record_health_failure",
            "record_health_success",
            "set_worker_status",
            "close",
        ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/shell/stores/test_memory_worker_store.py::TestWorkerStatusTracking -v`
Expected: FAIL with `AttributeError: 'InMemoryWorkerStore' object has no attribute 'set_worker_status'`

- [ ] **Step 3: Add set_worker_status to the WorkerStore ABC**

In `src/acheron/shell/stores/base.py`, add the import and new abstract method. Update the `TYPE_CHECKING` import block to include `WorkerStatus`:

```python
if TYPE_CHECKING:
    from acheron.core.models import JsonValue, WorkerCapabilities, WorkerStatus, WorkerType
    from acheron.shell.job_store import TrackedJob
    from acheron.shell.registry import RegisteredWorker
```

Add the new abstract method after `record_health_success` (before `close`):

```python
    @abstractmethod
    async def set_worker_status(
        self,
        worker_id: str,
        status: WorkerStatus,
        last_error: str | None,
    ) -> None:
        """Update the worker's status and last_error without touching the failure counter."""
        ...
```

Update the `record_health_success` docstring to document the new contract:

```python
    @abstractmethod
    async def record_health_success(self, worker_id: str) -> None:
        """Record a successful health check.

        Resets the failure counter to 0, sets status to HEALTHY, and clears
        last_error.
        """
        ...
```

- [ ] **Step 4: Implement in InMemoryWorkerStore**

In `src/acheron/shell/stores/memory.py`, add the `WorkerStatus` import to the `TYPE_CHECKING` block:

```python
if TYPE_CHECKING:
    from acheron.core.models import JsonValue, WorkerCapabilities, WorkerStatus, WorkerType
    from acheron.shell.job_store import TrackedJob
    from acheron.shell.registry import RegisteredWorker
```

Update `record_health_success` to reset status and last_error:

```python
    async def record_health_success(self, worker_id: str) -> None:
        """Record a successful health check, resetting the failure counter and status."""
        worker = self._workers.get(worker_id)
        if worker is not None:
            worker.consecutive_failures = 0
            worker.last_health_check = time.time()
            worker.status = WorkerStatus.HEALTHY
            worker.last_error = None
```

Add `set_worker_status` after `record_health_success`:

```python
    async def set_worker_status(
        self,
        worker_id: str,
        status: WorkerStatus,
        last_error: str | None,
    ) -> None:
        """Update the worker's status and last_error."""
        worker = self._workers.get(worker_id)
        if worker is not None:
            worker.status = status
            worker.last_error = last_error
```

Note: `WorkerStatus` is only needed at runtime here, so add a top-level import (not under `TYPE_CHECKING`) since the implementation references it directly. Replace the `TYPE_CHECKING` `WorkerStatus` addition above with a real import at the top of the file:

```python
from acheron.core.models import WorkerStatus
from acheron.shell.stores.base import JobStore, WorkerStore
```

(Remove `WorkerStatus` from the `TYPE_CHECKING` block to avoid duplication.)

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/shell/stores/test_memory_worker_store.py tests/shell/stores/test_stores_async.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/acheron/shell/stores/base.py src/acheron/shell/stores/memory.py tests/shell/stores/test_memory_worker_store.py tests/shell/stores/test_stores_async.py
git commit -m "feat(stores): add set_worker_status and reset status on health success"
```

---

### Task 3: Redis Store Serialization for status + last_error

**Files:**
- Modify: `src/acheron/shell/stores/redis.py`
- Modify: `tests/shell/stores/test_redis_worker_store.py`

- [ ] **Step 1: Write the failing test for status/last_error round-trip**

Add to `tests/shell/stores/test_redis_worker_store.py`. First update the import:

```python
from acheron.core.models import JsonValue, WorkerCapabilities, WorkerStatus, WorkerType
```

Add a new test class at the end of the file (before `TestFailFast` or at the end):

```python
class TestStatusAndErrorRoundTrip:
    @pytest.mark.asyncio
    async def test_set_worker_status_round_trips(self, store: RedisWorkerStore) -> None:
        await store.register("w-1", "http://a", "http", _tts_caps())
        await store.set_worker_status("w-1", WorkerStatus.BOOTING, "cold start")
        w = await store.get("w-1")
        assert w is not None
        assert w.status == WorkerStatus.BOOTING
        assert w.last_error == "cold start"

    @pytest.mark.asyncio
    async def test_set_worker_status_nonexistent_is_noop(self, store: RedisWorkerStore) -> None:
        await store.set_worker_status("nope", WorkerStatus.OFFLINE, "err")

    @pytest.mark.asyncio
    async def test_record_health_success_resets_status_and_error(self, store: RedisWorkerStore) -> None:
        await store.register("w-1", "http://a", "http", _tts_caps())
        await store.set_worker_status("w-1", WorkerStatus.OFFLINE, "boom")
        await store.record_health_success("w-1")
        w = await store.get("w-1")
        assert w is not None
        assert w.status == WorkerStatus.HEALTHY
        assert w.last_error is None

    @pytest.mark.asyncio
    async def test_new_worker_defaults_to_healthy(self, store: RedisWorkerStore) -> None:
        await store.register("w-1", "http://a", "http", _tts_caps())
        w = await store.get("w-1")
        assert w is not None
        assert w.status == WorkerStatus.HEALTHY
        assert w.last_error is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/shell/stores/test_redis_worker_store.py::TestStatusAndErrorRoundTrip -v`
Expected: FAIL with `AttributeError: 'RedisWorkerStore' object has no attribute 'set_worker_status'`

- [ ] **Step 3: Update _worker_fields and _deserialize_worker**

In `src/acheron/shell/stores/redis.py`, add `WorkerStatus` to the real imports (top of file, after the `acheron.core.models` import line):

```python
from acheron.core.models import AudioRequest, EpubRequest, WorkerStatus
```

Update `_worker_fields` to include `status` and `last_error`:

```python
def _worker_fields(
    endpoint: str,
    transport: str,
    capabilities: WorkerCapabilities,
    metadata: dict[str, JsonValue],
) -> dict[str, str]:
    return {
        "endpoint": endpoint,
        "transport": transport,
        "consecutive_failures": "0",
        "last_health_check": str(time.time()),
        "capabilities_json": _serialize_capabilities(capabilities),
        "metadata_json": json.dumps(metadata, sort_keys=True),
        "status": WorkerStatus.HEALTHY.value,
        "last_error": "",
    }
```

Update `_deserialize_worker` to read the new fields. Replace the function body with:

```python
def _deserialize_worker(worker_id: str, fields: dict[str, str]) -> RegisteredWorker:
    from acheron.core.errors import CacheCorruptedError  # noqa: PLC0415
    from acheron.shell.registry import RegisteredWorker  # noqa: PLC0415

    last_hc = fields.get("last_health_check") or ""
    try:
        metadata = json.loads(fields.get("metadata_json", "{}"))
    except json.JSONDecodeError as exc:
        msg = f"Worker {worker_id} metadata is not valid JSON: {exc}"
        raise CacheCorruptedError(msg) from exc
    status_str = fields.get("status") or WorkerStatus.HEALTHY.value
    try:
        status = WorkerStatus(status_str)
    except ValueError as exc:
        msg = f"Worker {worker_id} has invalid status: {status_str}"
        raise CacheCorruptedError(msg) from exc
    last_error = fields.get("last_error") or None
    return RegisteredWorker(
        worker_id=worker_id,
        endpoint=fields["endpoint"],
        transport=fields["transport"],
        capabilities=_deserialize_capabilities(fields["capabilities_json"]),
        consecutive_failures=int(fields.get("consecutive_failures", "0")),
        last_health_check=float(last_hc) if last_hc else None,
        metadata=metadata,
        status=status,
        last_error=last_error,
    )
```

- [ ] **Step 4: Update record_health_success and add set_worker_status on RedisWorkerStore**

Update `record_health_success` in `RedisWorkerStore`:

```python
    async def record_health_success(self, worker_id: str) -> None:
        """Record a successful health check, resetting status and clearing last_error."""
        key = _WORKER_KEY.format(worker_id=worker_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.hset(key, "consecutive_failures", "0")
            pipe.hset(key, "last_health_check", str(time.time()))
            pipe.hset(key, "status", WorkerStatus.HEALTHY.value)
            pipe.hset(key, "last_error", "")
            await pipe.execute()
```

Add `set_worker_status` after `record_health_success`:

```python
    async def set_worker_status(
        self,
        worker_id: str,
        status: WorkerStatus,
        last_error: str | None,
    ) -> None:
        """Update the worker's status and last_error without touching the failure counter."""
        key = _WORKER_KEY.format(worker_id=worker_id)
        if not await self._redis.exists(key):
            return
        await self._redis.hset(  # type: ignore[misc]
            key,
            mapping={"status": status.value, "last_error": last_error or ""},
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/shell/stores/test_redis_worker_store.py -v`
Expected: PASS (requires Redis via testcontainers — the existing `redis_url` fixture handles this)

- [ ] **Step 6: Commit**

```bash
git add src/acheron/shell/stores/redis.py tests/shell/stores/test_redis_worker_store.py
git commit -m "feat(stores/redis): persist status and last_error on workers"
```

---

### Task 4: HealthProvider ABC + Provider Config

**Files:**
- Create: `src/acheron/shell/health_providers.py`
- Modify: `src/acheron/shell/config.py`
- Modify: `tests/shell/test_config.py`

- [ ] **Step 1: Write the failing test for provider config + env var expansion**

Add to `tests/shell/test_config.py`:

```python
def test_providers_default_empty() -> None:
    settings = Settings()
    assert settings.providers.runpod.api_key is None
    assert settings.providers.huggingface.api_key is None


def test_providers_from_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    yaml_content = """
providers:
  runpod:
    api_key: "rp-secret"
  huggingface:
    api_key: "hf-secret"
"""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    monkeypatch.setenv("ACHERON_CONFIG_PATH", str(config_file))
    settings = load_settings()
    assert settings.providers.runpod.api_key == "rp-secret"
    assert settings.providers.huggingface.api_key == "hf-secret"


def test_yaml_env_var_expansion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "expanded-rp-key")
    yaml_content = """
providers:
  runpod:
    api_key: "${RUNPOD_API_KEY}"
"""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    monkeypatch.setenv("ACHERON_CONFIG_PATH", str(config_file))
    settings = load_settings()
    assert settings.providers.runpod.api_key == "expanded-rp-key"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/shell/test_config.py::test_providers_default_empty -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'providers'`

- [ ] **Step 3: Add ProvidersSettings to config.py**

In `src/acheron/shell/config.py`, add `import re` to the top imports, then add the provider settings classes after `PackagingSettings`:

```python
class RunPodProviderSettings(BaseModel):
    """RunPod API credentials for platform health checks."""

    api_key: str | None = None


class HuggingFaceProviderSettings(BaseModel):
    """Hugging Face API credentials for platform health checks."""

    api_key: str | None = None


class ProvidersSettings(BaseModel):
    """Platform provider credentials for decoupled health checks."""

    runpod: RunPodProviderSettings = Field(default_factory=RunPodProviderSettings)
    huggingface: HuggingFaceProviderSettings = Field(default_factory=HuggingFaceProviderSettings)
```

Add the `providers` field to `Settings` (after `workers`):

```python
    orchestrator: OrchestratorSettings = Field(default_factory=OrchestratorSettings)
    workers: WorkerSettings = Field(default_factory=WorkerSettings)
    providers: ProvidersSettings = Field(default_factory=ProvidersSettings)
```

- [ ] **Step 4: Add ${VAR} env-var expansion to the YAML source**

In `src/acheron/shell/config.py`, add the regex and helper near the top (after the `_logger` line):

```python
_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand_env_vars(value: Any) -> Any:  # noqa: ANN401
    """Recursively expand ${VAR} references in string values from os.environ."""
    if isinstance(value, str):
        return _ENV_VAR_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(v) for v in value]
    return value
```

Update `_YamlConfigSettingsSource.__call__` to expand env vars in the loaded YAML. Replace the `return yaml.safe_load(f) or {}` line inside the `try` block with:

```python
                    raw = yaml.safe_load(f) or {}
                    return _expand_env_vars(raw)
```

- [ ] **Step 5: Run config test to verify it passes**

Run: `uv run pytest tests/shell/test_config.py -v`
Expected: PASS

- [ ] **Step 6: Create the HealthProvider ABC**

Create `src/acheron/shell/health_providers.py`:

```python
"""Platform-specific health provider plugins for cold-start detection."""

from __future__ import annotations

from abc import ABC, abstractmethod

from acheron.core.models import WorkerStatus


class HealthProvider(ABC):
    """Query a hosting platform API to determine if a worker is booting or offline."""

    @abstractmethod
    async def check_status(self, endpoint_id: str) -> WorkerStatus:
        """Query the platform to verify if the container is booting vs offline."""
        ...
```

- [ ] **Step 7: Commit**

```bash
git add src/acheron/shell/health_providers.py src/acheron/shell/config.py tests/shell/test_config.py
git commit -m "feat(config): add provider settings, env-var expansion, HealthProvider ABC"
```

---

### Task 5: RunPodHealthProvider

**Files:**
- Modify: `src/acheron/shell/health_providers.py`
- Modify: `tests/shell/test_health_providers.py` (create)

- [ ] **Step 1: Write the failing test for RunPodHealthProvider**

Create `tests/shell/test_health_providers.py`:

```python
"""Tests for platform health providers."""

from __future__ import annotations

import httpx
import pytest
import respx

from acheron.core.models import WorkerStatus
from acheron.shell.health_providers import RunPodHealthProvider

_RUNPOD_BASE = "https://rest.runpod.io/v1"


class TestRunPodHealthProvider:
    @respx.mock
    @pytest.mark.asyncio
    async def test_endpoint_exists_returns_booting(self) -> None:
        respx.get(f"{_RUNPOD_BASE}/endpoints/ep-1").mock(return_value=httpx.Response(200, json={"id": "ep-1"}))
        provider = RunPodHealthProvider(api_key="rp-key")
        status = await provider.check_status("ep-1")
        assert status == WorkerStatus.BOOTING

    @respx.mock
    @pytest.mark.asyncio
    async def test_endpoint_not_found_returns_offline(self) -> None:
        respx.get(f"{_RUNPOD_BASE}/endpoints/missing").mock(return_value=httpx.Response(404))
        provider = RunPodHealthProvider(api_key="rp-key")
        status = await provider.check_status("missing")
        assert status == WorkerStatus.OFFLINE

    @respx.mock
    @pytest.mark.asyncio
    async def test_network_error_returns_offline(self) -> None:
        respx.get(f"{_RUNPOD_BASE}/endpoints/ep-1").mock(side_effect=httpx.ConnectError("refused"))
        provider = RunPodHealthProvider(api_key="rp-key")
        status = await provider.check_status("ep-1")
        assert status == WorkerStatus.OFFLINE

    @respx.mock
    @pytest.mark.asyncio
    async def test_authorization_header_sent(self) -> None:
        route = respx.get(f"{_RUNPOD_BASE}/endpoints/ep-1").mock(return_value=httpx.Response(200, json={}))
        provider = RunPodHealthProvider(api_key="rp-key")
        await provider.check_status("ep-1")
        assert route.calls.last.request.headers["authorization"] == "Bearer rp-key"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/shell/test_health_providers.py::TestRunPodHealthProvider -v`
Expected: FAIL with `ImportError: cannot import name 'RunPodHealthProvider'`

- [ ] **Step 3: Implement RunPodHealthProvider**

Add to `src/acheron/shell/health_providers.py` (after the `HealthProvider` ABC):

```python
import httpx


class RunPodHealthProvider(HealthProvider):
    """RunPod Serverless health provider.

    ``endpoint_id`` is the RunPod serverless endpoint id. If the endpoint
    exists, the worker is treated as cold-starting (HTTP probe failed but the
    platform still knows about it). If the endpoint is gone, the worker is
    offline.
    """

    _BASE_URL = "https://rest.runpod.io/v1"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def check_status(self, endpoint_id: str) -> WorkerStatus:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self._BASE_URL}/endpoints/{endpoint_id}",
                    headers=headers,
                    timeout=10.0,
                )
        except (httpx.HTTPError, OSError):
            return WorkerStatus.OFFLINE
        if resp.status_code == 200:
            return WorkerStatus.BOOTING
        return WorkerStatus.OFFLINE
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/shell/test_health_providers.py::TestRunPodHealthProvider -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/acheron/shell/health_providers.py tests/shell/test_health_providers.py
git commit -m "feat(health): add RunPodHealthProvider for serverless cold-start detection"
```

---

### Task 6: HuggingFaceHealthProvider

**Files:**
- Modify: `src/acheron/shell/health_providers.py`
- Modify: `tests/shell/test_health_providers.py`

- [ ] **Step 1: Write the failing test for HuggingFaceHealthProvider**

Add to `tests/shell/test_health_providers.py`:

```python
from acheron.shell.health_providers import HuggingFaceHealthProvider

_HF_BASE = "https://api.endpoints.huggingface.cloud/v2/endpoints"


class TestHuggingFaceHealthProvider:
    @respx.mock
    @pytest.mark.asyncio
    async def test_initializing_returns_booting(self) -> None:
        respx.get(f"{_HF_BASE}/my-ns/ep-1").mock(
            return_value=httpx.Response(200, json={"status": {"state": "initializing"}})
        )
        provider = HuggingFaceHealthProvider(api_key="hf-key")
        status = await provider.check_status("my-ns/ep-1")
        assert status == WorkerStatus.BOOTING

    @respx.mock
    @pytest.mark.asyncio
    async def test_starting_returns_booting(self) -> None:
        respx.get(f"{_HF_BASE}/my-ns/ep-1").mock(
            return_value=httpx.Response(200, json={"status": {"state": "starting"}})
        )
        provider = HuggingFaceHealthProvider(api_key="hf-key")
        status = await provider.check_status("my-ns/ep-1")
        assert status == WorkerStatus.BOOTING

    @respx.mock
    @pytest.mark.asyncio
    async def test_running_returns_booting(self) -> None:
        """Platform says running but HTTP probe failed → cold start."""
        respx.get(f"{_HF_BASE}/my-ns/ep-1").mock(
            return_value=httpx.Response(200, json={"status": {"state": "running"}})
        )
        provider = HuggingFaceHealthProvider(api_key="hf-key")
        status = await provider.check_status("my-ns/ep-1")
        assert status == WorkerStatus.BOOTING

    @respx.mock
    @pytest.mark.asyncio
    async def test_paused_returns_offline(self) -> None:
        respx.get(f"{_HF_BASE}/my-ns/ep-1").mock(
            return_value=httpx.Response(200, json={"status": {"state": "paused"}})
        )
        provider = HuggingFaceHealthProvider(api_key="hf-key")
        status = await provider.check_status("my-ns/ep-1")
        assert status == WorkerStatus.OFFLINE

    @respx.mock
    @pytest.mark.asyncio
    async def test_failed_returns_offline(self) -> None:
        respx.get(f"{_HF_BASE}/my-ns/ep-1").mock(
            return_value=httpx.Response(200, json={"status": {"state": "failed"}})
        )
        provider = HuggingFaceHealthProvider(api_key="hf-key")
        status = await provider.check_status("my-ns/ep-1")
        assert status == WorkerStatus.OFFLINE

    @respx.mock
    @pytest.mark.asyncio
    async def test_not_found_returns_offline(self) -> None:
        respx.get(f"{_HF_BASE}/my-ns/missing").mock(return_value=httpx.Response(404))
        provider = HuggingFaceHealthProvider(api_key="hf-key")
        status = await provider.check_status("my-ns/missing")
        assert status == WorkerStatus.OFFLINE

    @respx.mock
    @pytest.mark.asyncio
    async def test_network_error_returns_offline(self) -> None:
        respx.get(f"{_HF_BASE}/my-ns/ep-1").mock(side_effect=httpx.ConnectError("refused"))
        provider = HuggingFaceHealthProvider(api_key="hf-key")
        status = await provider.check_status("my-ns/ep-1")
        assert status == WorkerStatus.OFFLINE

    @respx.mock
    @pytest.mark.asyncio
    async def test_authorization_header_sent(self) -> None:
        route = respx.get(f"{_HF_BASE}/my-ns/ep-1").mock(
            return_value=httpx.Response(200, json={"status": {"state": "running"}})
        )
        provider = HuggingFaceHealthProvider(api_key="hf-key")
        await provider.check_status("my-ns/ep-1")
        assert route.calls.last.request.headers["authorization"] == "Bearer hf-key"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/shell/test_health_providers.py::TestHuggingFaceHealthProvider -v`
Expected: FAIL with `ImportError: cannot import name 'HuggingFaceHealthProvider'`

- [ ] **Step 3: Implement HuggingFaceHealthProvider**

Add to `src/acheron/shell/health_providers.py` (after `RunPodHealthProvider`):

```python
class HuggingFaceHealthProvider(HealthProvider):
    """Hugging Face Inference Endpoints health provider.

    ``endpoint_id`` is ``namespace/name``. The HF API returns a ``status.state``
    field. Initializing/starting/running states (when the HTTP probe failed)
    indicate a cold start. Paused/failed or missing endpoints are offline.
    """

    _BASE_URL = "https://api.endpoints.huggingface.cloud/v2/endpoints"
    _BOOTING_STATES = frozenset({"pending", "initializing", "starting", "running"})

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def check_status(self, endpoint_id: str) -> WorkerStatus:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self._BASE_URL}/{endpoint_id}",
                    headers=headers,
                    timeout=10.0,
                )
        except (httpx.HTTPError, OSError):
            return WorkerStatus.OFFLINE
        if resp.status_code != 200:
            return WorkerStatus.OFFLINE
        data = resp.json()
        status_raw = data.get("status")
        if isinstance(status_raw, dict):
            state = status_raw.get("state", "")
        elif isinstance(status_raw, str):
            state = status_raw
        else:
            state = ""
        if state in self._BOOTING_STATES:
            return WorkerStatus.BOOTING
        return WorkerStatus.OFFLINE
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/shell/test_health_providers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/acheron/shell/health_providers.py tests/shell/test_health_providers.py
git commit -m "feat(health): add HuggingFaceHealthProvider for inference endpoint cold-start detection"
```

---

### Task 7: HealthProviders Container + Factory

**Files:**
- Modify: `src/acheron/shell/health_providers.py`
- Modify: `tests/shell/test_health_providers.py`

- [ ] **Step 1: Write the failing test for the factory**

Add to `tests/shell/test_health_providers.py`:

```python
from acheron.shell.config import Settings
from acheron.shell.health_providers import (
    HealthProviders,
    create_health_providers,
)


class TestHealthProvidersContainer:
    def test_get_returns_provider_by_name(self) -> None:
        providers = HealthProviders({"runpod": RunPodHealthProvider(api_key="k")})
        assert isinstance(providers.get("runpod"), RunPodHealthProvider)

    def test_get_unknown_returns_none(self) -> None:
        providers = HealthProviders({})
        assert providers.get("runpod") is None


class TestCreateHealthProviders:
    def test_creates_runpod_when_api_key_set(self) -> None:
        settings = Settings()
        settings.providers.runpod.api_key = "rp-key"
        providers = create_health_providers(settings)
        assert isinstance(providers.get("runpod"), RunPodHealthProvider)

    def test_creates_huggingface_when_api_key_set(self) -> None:
        settings = Settings()
        settings.providers.huggingface.api_key = "hf-key"
        providers = create_health_providers(settings)
        assert isinstance(providers.get("huggingface"), HuggingFaceHealthProvider)

    def test_empty_when_no_api_keys(self) -> None:
        providers = create_health_providers(Settings())
        assert providers.get("runpod") is None
        assert providers.get("huggingface") is None

    def test_empty_string_api_key_creates_nothing(self) -> None:
        settings = Settings()
        settings.providers.runpod.api_key = ""
        providers = create_health_providers(settings)
        assert providers.get("runpod") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/shell/test_health_providers.py::TestCreateHealthProviders -v`
Expected: FAIL with `ImportError: cannot import name 'HealthProviders'`

- [ ] **Step 3: Implement HealthProviders + factory**

Add to `src/acheron/shell/health_providers.py` (at the end). Add the `TYPE_CHECKING` import block and `Settings` reference:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from acheron.shell.config import Settings


class HealthProviders:
    """Container mapping provider names to HealthProvider instances."""

    def __init__(self, providers: dict[str, HealthProvider]) -> None:
        self._providers = providers

    def get(self, name: str) -> HealthProvider | None:
        """Return the provider for ``name`` or None if not configured."""
        return self._providers.get(name)


def create_health_providers(settings: Settings) -> HealthProviders:
    """Build a HealthProviders container from provider API keys in settings."""
    providers: dict[str, HealthProvider] = {}
    if settings.providers.runpod.api_key:
        providers["runpod"] = RunPodHealthProvider(settings.providers.runpod.api_key)
    if settings.providers.huggingface.api_key:
        providers["huggingface"] = HuggingFaceHealthProvider(settings.providers.huggingface.api_key)
    return HealthProviders(providers)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/shell/test_health_providers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/acheron/shell/health_providers.py tests/shell/test_health_providers.py
git commit -m "feat(health): add HealthProviders container and factory"
```

---

### Task 8: HealthMonitor Integration with Providers

**Files:**
- Modify: `src/acheron/shell/health.py`
- Modify: `tests/shell/test_health_monitor.py`

- [ ] **Step 1: Write the failing tests for provider-aware monitoring**

Add to `tests/shell/test_health_monitor.py`. First update the imports:

```python
from acheron.core.models import WorkerCapabilities, WorkerStatus, WorkerType
from acheron.shell.health import HealthMonitor, HealthProbeResult, _default_health_check
from acheron.shell.health_providers import HealthProvider, HealthProviders
from acheron.shell.stores.memory import InMemoryWorkerStore
```

Add a helper for caps with health-provider metadata:

```python
def _tts_caps_with_provider(provider: str, endpoint_id: str) -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TTS,
        supported_languages_in=frozenset({"es"}),
        supported_languages_out=frozenset({"es"}),
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"wav"}),
        max_payload_bytes=None,
        batch_capable=True,
        model_source=None,
        metadata={"health_provider": provider, "health_endpoint_id": endpoint_id},
    )
```

Add a fake provider class and new tests at the end of the file:

```python
class _FakeProvider(HealthProvider):
    """Fake HealthProvider returning a configured status."""

    def __init__(self, status: WorkerStatus) -> None:
        self._status = status
        self.called_with: str | None = None

    async def check_status(self, endpoint_id: str) -> WorkerStatus:
        self.called_with = endpoint_id
        return self._status


class TestHealthMonitorProviderIntegration:
    @pytest.mark.asyncio
    async def test_booting_worker_not_removed(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w1", "http://down", "http", _tts_caps_with_provider("runpod", "ep-1"))
        fake = _FakeProvider(WorkerStatus.BOOTING)
        providers = HealthProviders({"runpod": fake})
        health_check = AsyncMock(return_value=HealthProbeResult(healthy=False, error="conn refused"))
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check, providers=providers)
        await monitor.start()

        async def _booting() -> bool:
            w = await reg.get("w1")
            return w is not None and w.status == WorkerStatus.BOOTING

        await _poll_for(_booting)
        await monitor.stop()
        w = await reg.get("w1")
        assert w is not None
        assert w.status == WorkerStatus.BOOTING
        assert w.consecutive_failures == 0
        assert "conn refused" in (w.last_error or "")
        assert fake.called_with == "ep-1"

    @pytest.mark.asyncio
    async def test_offline_provider_increments_failures(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w1", "http://down", "http", _tts_caps_with_provider("runpod", "ep-1"))
        fake = _FakeProvider(WorkerStatus.OFFLINE)
        providers = HealthProviders({"runpod": fake})
        health_check = AsyncMock(return_value=HealthProbeResult(healthy=False, error="down"))
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check, providers=providers)
        await monitor.start()

        async def _removed() -> bool:
            return await reg.get("w1") is None

        await _poll_for(_removed)
        await monitor.stop()
        assert await reg.get("w1") is None

    @pytest.mark.asyncio
    async def test_no_provider_falls_back_to_offline(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w1", "http://down", "http", _tts_caps())
        health_check = AsyncMock(return_value=HealthProbeResult(healthy=False, error="down"))
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check)
        await monitor.start()

        async def _offline() -> bool:
            w = await reg.get("w1")
            return w is not None and w.status == WorkerStatus.OFFLINE

        await _poll_for(_offline)
        await monitor.stop()
        w = await reg.get("w1")
        assert w is not None
        assert w.status == WorkerStatus.OFFLINE

    @pytest.mark.asyncio
    async def test_success_resets_to_healthy(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w1", "http://up", "http", _tts_caps_with_provider("runpod", "ep-1"))
        await reg.set_worker_status("w1", WorkerStatus.BOOTING, "cold")
        health_check = AsyncMock(return_value=HealthProbeResult(healthy=True))
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check)
        await monitor.start()

        async def _healthy() -> bool:
            w = await reg.get("w1")
            return w is not None and w.status == WorkerStatus.HEALTHY and w.last_error is None

        await _poll_for(_healthy)
        await monitor.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/shell/test_health_monitor.py::TestHealthMonitorProviderIntegration -v`
Expected: FAIL with `ImportError: cannot import name 'HealthProbeResult'`

- [ ] **Step 3: Rewrite health.py with HealthProbeResult + provider integration**

Replace the full contents of `src/acheron/shell/health.py` with:

```python
"""Background health monitoring for registered workers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import grpc
import grpc.aio
import httpx
from grpc.health.v1 import health_pb2, health_pb2_grpc

from acheron.core.models import WorkerStatus
from acheron.shell.tls import grpc_channel

if TYPE_CHECKING:
    from acheron.shell.health_providers import HealthProviders
    from acheron.shell.registry import RegisteredWorker
    from acheron.shell.stores.base import WorkerStore

logger = logging.getLogger(__name__)

type HealthCheckFn = Callable[[str, str], Awaitable[HealthProbeResult]]


@dataclass(frozen=True)
class HealthProbeResult:
    """Result of a single worker health probe."""

    healthy: bool
    error: str | None = None


def _metadata_str(worker: RegisteredWorker, key: str) -> str:
    """Read a string value from worker capabilities metadata, or "" if absent."""
    value = worker.capabilities.metadata.get(key)
    return value if isinstance(value, str) else ""


async def _check_http_health(endpoint: str) -> HealthProbeResult:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{endpoint}/health", timeout=5.0)
            if resp.status_code == httpx.codes.OK:
                return HealthProbeResult(healthy=True)
            return HealthProbeResult(healthy=False, error=f"HTTP {resp.status_code}")
    except (httpx.HTTPError, OSError) as exc:
        return HealthProbeResult(healthy=False, error=f"{type(exc).__name__}: {exc}")


async def _check_grpc_health(endpoint: str) -> HealthProbeResult:
    try:
        async with grpc_channel(endpoint) as channel:
            stub = health_pb2_grpc.HealthStub(channel)
            resp = await stub.Check(health_pb2.HealthCheckRequest())
            if resp.status == health_pb2.HealthCheckResponse.SERVING:  # type: ignore[no-any-return]
                return HealthProbeResult(healthy=True)
            return HealthProbeResult(healthy=False, error=f"gRPC status {resp.status}")
    except (grpc.aio.AioRpcError, OSError) as exc:
        return HealthProbeResult(healthy=False, error=f"{type(exc).__name__}: {exc}")


async def _default_health_check(endpoint: str, transport: str) -> HealthProbeResult:
    match transport:
        case "grpc":
            return await _check_grpc_health(endpoint)
        case "local":
            return HealthProbeResult(healthy=True)
        case _:
            return await _check_http_health(endpoint)


class HealthMonitor:
    """Periodic background task checking worker health."""

    def __init__(
        self,
        registry: WorkerStore,
        interval: float = 30.0,
        health_check: HealthCheckFn | None = None,
        providers: HealthProviders | None = None,
    ) -> None:
        self._registry = registry
        self._interval = interval
        self._health_check = health_check or _default_health_check
        self._providers = providers
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the health check background task. Idempotent."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Stop the health check background task."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        """Run health checks in a loop."""
        await self._check_all()
        while True:
            await asyncio.sleep(self._interval)
            await self._check_all()

    async def _check_all(self) -> None:
        """Check health of all registered workers concurrently."""
        workers = list(await self._registry.list_all())
        if not workers:
            return
        results = await asyncio.gather(
            *(self._health_check(w.endpoint, w.transport) for w in workers),
            return_exceptions=True,
        )
        for worker, result in zip(workers, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning("Health check for %s raised: %s", worker.worker_id, result)
                result = HealthProbeResult(healthy=False, error=f"{type(result).__name__}: {result}")
            if result.healthy:
                await self._registry.record_health_success(worker.worker_id)
            else:
                await self._handle_failure(worker, result.error or "health check failed")

    async def _handle_failure(self, worker: RegisteredWorker, error: str) -> None:
        """On probe failure, consult the platform provider then update status."""
        provider_name = _metadata_str(worker, "health_provider")
        endpoint_id = _metadata_str(worker, "health_endpoint_id")
        provider = self._providers.get(provider_name) if self._providers and provider_name else None
        if provider is not None and endpoint_id:
            try:
                platform_status = await provider.check_status(endpoint_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Health provider %s raised for %s: %s", provider_name, worker.worker_id, exc)
                platform_status = WorkerStatus.OFFLINE
                error = f"{error}; provider {provider_name} error: {exc}"
            if platform_status == WorkerStatus.BOOTING:
                await self._registry.set_worker_status(worker.worker_id, WorkerStatus.BOOTING, error)
                logger.info("Worker %s marked BOOTING via %s", worker.worker_id, provider_name)
                return
        await self._registry.set_worker_status(worker.worker_id, WorkerStatus.OFFLINE, error)
        removed = await self._registry.record_health_failure(worker.worker_id)
        if removed:
            logger.warning("Removed unhealthy worker %s after 3 failures", worker.worker_id)
```

- [ ] **Step 4: Update the existing HealthMonitor tests for HealthProbeResult**

In `tests/shell/test_health_monitor.py`, update the existing mocks that return `bool` to return `HealthProbeResult`:

- `test_records_success_for_healthy_worker`: change `health_check = AsyncMock(return_value=True)` to:
  ```python
  health_check = AsyncMock(return_value=HealthProbeResult(healthy=True))
  ```
- `test_records_failure_for_unhealthy_worker`: change `AsyncMock(return_value=False)` to:
  ```python
  health_check = AsyncMock(return_value=HealthProbeResult(healthy=False, error="down"))
  ```
- `test_removes_worker_after_max_failures`: change `AsyncMock(return_value=False)` to:
  ```python
  health_check = AsyncMock(return_value=HealthProbeResult(healthy=False, error="down"))
  ```

The `TestDefaultHealthCheck` tests assert `result is True` — update them to assert `result.healthy is True` / `result.healthy is False`:

- `test_grpc_worker_uses_grpc_health_check`: `assert result.healthy is True`
- `test_grpc_unhealthy_worker_returns_false`: `assert result.healthy is False`
- `test_grpc_does_not_attempt_http`: `assert result.healthy is True`

The `TestHealthMonitorTransportAware` test (`test_grpc_worker_not_removed_when_healthy`) uses the default health check (no mock) — no change needed since `_default_health_check` now returns `HealthProbeResult` and the monitor reads `.healthy`.

- [ ] **Step 5: Run all health monitor tests**

Run: `uv run pytest tests/shell/test_health_monitor.py -v`
Expected: PASS

- [ ] **Step 6: Run the full test suite to catch any other breakage**

Run: `uv run pytest -v`
Expected: PASS (all tests green)

- [ ] **Step 7: Commit**

```bash
git add src/acheron/shell/health.py tests/shell/test_health_monitor.py
git commit -m "feat(health): provider-aware HealthMonitor with WorkerStatus + last_error"
```

---

### Task 9: API WorkerResponse with status + last_error

**Files:**
- Modify: `src/acheron/shell/api/schemas.py`
- Modify: `src/acheron/shell/api/routes/workers.py`
- Modify: `tests/shell/api/test_workers.py`

- [ ] **Step 1: Write the failing test for status + last_error in the response**

Add to `tests/shell/api/test_workers.py`. Update the existing `test_list_workers` to assert the new fields, and add a dedicated test. Add to `TestWorkerRoutes`:

```python
    @pytest.mark.asyncio
    async def test_list_workers_includes_status_and_last_error(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/workers")
        assert response.status_code == 200
        for w in response.json()["workers"]:
            assert "status" in w
            assert "last_error" in w

    @pytest.mark.asyncio
    async def test_registered_worker_defaults_to_healthy(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post("/workers", json=_WORKER_PAYLOAD)
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "healthy"
        assert data["last_error"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/shell/api/test_workers.py::TestWorkerRoutes::test_list_workers_includes_status_and_last_error -v`
Expected: FAIL (response missing `status` field)

- [ ] **Step 3: Update WorkerResponse schema**

In `src/acheron/shell/api/schemas.py`, update `WorkerResponse`:

```python
class WorkerResponse(BaseModel):
    """Response for a single worker."""

    worker_id: str
    endpoint: str
    transport: str
    worker_type: str
    consecutive_failures: int
    status: str = "healthy"
    last_error: str | None = None
```

- [ ] **Step 4: Update routes/workers.py to map the new fields**

In `src/acheron/shell/api/routes/workers.py`, update the `register_worker` return:

```python
    return WorkerResponse(
        worker_id=body.worker_id,
        endpoint=body.endpoint,
        transport=body.transport,
        worker_type=body.capabilities.worker_type,
        consecutive_failures=0,
        status="healthy",
        last_error=None,
    )
```

Update `list_workers`:

```python
@router.get("", response_model=WorkerListResponse)
async def list_workers(orch: OrchestratorDep) -> WorkerListResponse:
    """List all registered workers."""
    workers = await orch.list_workers()
    return WorkerListResponse(
        workers=[
            WorkerResponse(
                worker_id=w.worker_id,
                endpoint=w.endpoint,
                transport=w.transport,
                worker_type=w.capabilities.worker_type.value,
                consecutive_failures=w.consecutive_failures,
                status=w.status.value,
                last_error=w.last_error,
            )
            for w in workers
        ]
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/shell/api/test_workers.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/acheron/shell/api/schemas.py src/acheron/shell/api/routes/workers.py tests/shell/api/test_workers.py
git commit -m "feat(api): expose status and last_error on /workers responses"
```

---

### Task 10: Orchestrator /partials/status Endpoint

**Files:**
- Create: `src/acheron/shell/api/routes/partials.py`
- Modify: `src/acheron/shell/api/app.py`
- Create: `tests/shell/api/test_partials.py`

- [ ] **Step 1: Write the failing test**

Create `tests/shell/api/test_partials.py`:

```python
"""Tests for the orchestrator HTML partial endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from acheron.shell.api.app import create_app
from acheron.shell.cache import PlanCache
from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    app = create_app(
        registry=InMemoryWorkerStore(),
        job_store=InMemoryJobStore(),
        cache=PlanCache(tmp_path),
        data_dir=tmp_path,
    )
    await app.state.orchestrator.start()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
        await app.state.orchestrator.shutdown()


class TestStatusPartial:
    @pytest.mark.asyncio
    async def test_returns_connected_html(self, client: AsyncClient) -> None:
        resp = await client.get("/partials/status")
        assert resp.status_code == 200
        assert "Connected" in resp.text
        assert "dot-green" in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/shell/api/test_partials.py -v`
Expected: FAIL with 404 (route not registered)

- [ ] **Step 3: Create the partials route module**

Create `src/acheron/shell/api/routes/partials.py`:

```python
"""HTML partial endpoints served by the orchestrator for HTMX dashboard polling."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/partials/status", response_class=HTMLResponse)
async def status_partial() -> HTMLResponse:
    """Return a green 'Connected' badge.

    Reachability is the signal: if the dashboard can fetch this, the
    orchestrator is up. The dashboard renders a red 'Disconnected' badge
    when this endpoint is unreachable.
    """
    return HTMLResponse('<span class="dot dot-green"></span> Connected')
```

- [ ] **Step 4: Register the router in app.py**

In `src/acheron/shell/api/app.py`, update the route import and registration. Change the import line:

```python
from acheron.shell.api.routes import capabilities, jobs, partials, workers
```

Add the router registration after the `capabilities` router:

```python
    app.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
    app.include_router(workers.router, prefix="/workers", tags=["workers"])
    app.include_router(capabilities.router, tags=["capabilities"])
    app.include_router(partials.router, tags=["partials"])
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/shell/api/test_partials.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/acheron/shell/api/routes/partials.py src/acheron/shell/api/app.py tests/shell/api/test_partials.py
git commit -m "feat(api): add orchestrator /partials/status endpoint for dashboard"
```

---

### Task 11: Dashboard Backend Status Circle

**Files:**
- Modify: `dashboard/app.py`
- Modify: `dashboard/templates/index.html`
- Modify: `dashboard/tests/test_dashboard.py`

- [ ] **Step 1: Write the failing test for the status partial**

Add to `dashboard/tests/test_dashboard.py`:

```python
class TestStatusPartial:
    @respx.mock
    @pytest.mark.asyncio
    async def test_status_connected_when_orchestrator_up(self, client):
        respx.get(f"{_ORCH_URL}/partials/status").mock(
            return_value=httpx.Response(200, text='<span class="dot dot-green"></span> Connected')
        )
        resp = await client.get("/partials/status")
        assert resp.status_code == 200
        assert "Connected" in resp.text
        assert "dot-green" in resp.text

    @respx.mock
    @pytest.mark.asyncio
    async def test_status_disconnected_when_orchestrator_down(self, client):
        respx.get(f"{_ORCH_URL}/partials/status").mock(side_effect=httpx.ConnectError("refused"))
        resp = await client.get("/partials/status")
        assert resp.status_code == 200
        assert "Disconnected" in resp.text
        assert "dot-red" in resp.text
```

Also add a test that the index page contains the status indicator:

```python
    @pytest.mark.asyncio
    async def test_index_contains_status_indicator(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert 'id="status"' in resp.text
        assert "/partials/status" in resp.text
```

Add this to the `TestIndexPage` class.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest dashboard/tests/test_dashboard.py::TestStatusPartial -v`
Expected: FAIL with 404 (no `/partials/status` route on dashboard)

- [ ] **Step 3: Add the status proxy route to dashboard/app.py**

In `dashboard/app.py`, add `HTMLResponse` to the fastapi imports (it is already imported) and add the route after the `cost_partial` route:

```python
    @app.get("/partials/status", response_class=HTMLResponse)
    async def status_partial(request: Request) -> HTMLResponse:  # noqa: ARG001
        """Proxy the orchestrator's status partial; show Disconnected on failure."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{orchestrator_url}/partials/status", timeout=5.0)
                resp.raise_for_status()
                return HTMLResponse(resp.text)
        except (httpx.HTTPError, OSError):
            return HTMLResponse('<span class="dot dot-red"></span> Disconnected')
```

- [ ] **Step 4: Add the status circle to index.html**

In `dashboard/templates/index.html`, update the `<h1>` line to include the status indicator:

```html
  <h1>Acheron <span id="status" hx-get="/partials/status" hx-trigger="load, every 2s" hx-swap="innerHTML" style="font-size:1rem"><span style="color:#8b949e">…</span></span></h1>
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest dashboard/tests/test_dashboard.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add dashboard/app.py dashboard/templates/index.html dashboard/tests/test_dashboard.py
git commit -m "feat(dashboard): add backend connection status circle"
```

---

### Task 12: Dashboard Worker Status Badges + View Error

**Files:**
- Modify: `dashboard/templates/index.html` (CSS)
- Modify: `dashboard/templates/partials/workers.html`
- Modify: `dashboard/tests/test_dashboard.py`

- [ ] **Step 1: Write the failing test for status badges + view error**

Add to `dashboard/tests/test_dashboard.py`. Update the existing `TestWorkersPartial.test_workers_partial_returns_table` to include `status` and `last_error` in the mocked response, and add new tests:

```python
class TestWorkersPartialStatus:
    @respx.mock
    @pytest.mark.asyncio
    async def test_healthy_worker_shows_healthy_badge(self, client):
        respx.get(f"{_ORCH_URL}/workers").mock(
            return_value=httpx.Response(
                200,
                json={
                    "workers": [
                        {
                            "worker_id": "tts-1",
                            "worker_type": "tts",
                            "endpoint": "http://tts:8000",
                            "transport": "http",
                            "consecutive_failures": 0,
                            "status": "healthy",
                            "last_error": None,
                        },
                    ]
                },
            )
        )
        resp = await client.get("/partials/workers")
        assert resp.status_code == 200
        assert "badge-healthy" in resp.text

    @respx.mock
    @pytest.mark.asyncio
    async def test_booting_worker_shows_booting_badge_and_error(self, client):
        respx.get(f"{_ORCH_URL}/workers").mock(
            return_value=httpx.Response(
                200,
                json={
                    "workers": [
                        {
                            "worker_id": "tts-2",
                            "worker_type": "tts",
                            "endpoint": "http://tts:8000",
                            "transport": "http",
                            "consecutive_failures": 0,
                            "status": "booting",
                            "last_error": "cold start: connection refused",
                        },
                    ]
                },
            )
        )
        resp = await client.get("/partials/workers")
        assert resp.status_code == 200
        assert "badge-booting" in resp.text
        assert "View Error" in resp.text
        assert "cold start: connection refused" in resp.text

    @respx.mock
    @pytest.mark.asyncio
    async def test_offline_worker_shows_offline_badge(self, client):
        respx.get(f"{_ORCH_URL}/workers").mock(
            return_value=httpx.Response(
                200,
                json={
                    "workers": [
                        {
                            "worker_id": "tts-3",
                            "worker_type": "tts",
                            "endpoint": "http://tts:8000",
                            "transport": "http",
                            "consecutive_failures": 2,
                            "status": "offline",
                            "last_error": "HTTP 503",
                        },
                    ]
                },
            )
        )
        resp = await client.get("/partials/workers")
        assert resp.status_code == 200
        assert "badge-offline" in resp.text
        assert "View Error" in resp.text
```

Also update the existing `test_workers_partial_returns_table` mock to include `"status": "healthy"` and `"last_error": None` so it still passes with the new template.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest dashboard/tests/test_dashboard.py::TestWorkersPartialStatus -v`
Expected: FAIL (template doesn't render badges / View Error)

- [ ] **Step 3: Add badge CSS to index.html**

In `dashboard/templates/index.html`, add the new badge styles inside the `<style>` block (after the existing `.badge-failed` line):

```css
    .badge-healthy { background: #23863633; color: #3fb950; }
    .badge-booting { background: #d2992233; color: #d29922; }
    .badge-offline { background: #da363433; color: #f85149; }
```

- [ ] **Step 4: Update workers.html partial with status badges + View Error**

Replace the full contents of `dashboard/templates/partials/workers.html` with:

```html
{% if workers %}
<table>
  <thead>
    <tr><th>Worker ID</th><th>Type</th><th>Endpoint</th><th>Transport</th><th>Status</th><th>Failures</th><th>Error</th></tr>
  </thead>
  <tbody>
    {% for w in workers %}
    <tr>
      <td>{{ w.worker_id }}</td>
      <td>{{ w.worker_type }}</td>
      <td>{{ w.endpoint }}</td>
      <td>{{ w.transport }}</td>
      <td><span class="badge badge-{{ w.status }}">{{ w.status }}</span></td>
      <td>{{ w.consecutive_failures }}</td>
      <td>
        {% if w.last_error %}
        <details><summary style="color:#f85149;cursor:pointer">View Error</summary><pre style="color:#8b949e;white-space:pre-wrap;margin:0.5rem 0 0">{{ w.last_error }}</pre></details>
        {% else %}-{% endif %}
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% else %}
<p style="color:#8b949e">No workers registered.</p>
{% endif %}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest dashboard/tests/test_dashboard.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add dashboard/templates/index.html dashboard/templates/partials/workers.html dashboard/tests/test_dashboard.py
git commit -m "feat(dashboard): worker status badges and inline error viewer"
```

---

### Task 13: Wire HealthProviders into the Orchestrator

**Files:**
- Modify: `src/acheron/shell/orchestrator.py`
- Modify: `tests/shell/test_orchestrator.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/shell/test_orchestrator.py` (or create a focused test). Check the existing test file for the import pattern, then add:

```python
@pytest.mark.asyncio
async def test_orchestrator_constructs_health_providers_from_settings(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from acheron.shell.config import Settings
    from acheron.shell.orchestrator import Orchestrator
    from acheron.shell.cache import PlanCache
    from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

    settings = Settings()
    settings.providers.runpod.api_key = "rp-key"
    orch = Orchestrator(
        registry=InMemoryWorkerStore(),
        cache=PlanCache(tmp_path),
        job_store=InMemoryJobStore(),
        settings=settings,
    )
    assert orch._health_monitor._providers is not None  # noqa: SLF001
    assert orch._health_monitor._providers.get("runpod") is not None  # noqa: SLF001
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/shell/test_orchestrator.py::test_orchestrator_constructs_health_providers_from_settings -v`
Expected: FAIL (`_providers` is None — not wired yet)

- [ ] **Step 3: Wire create_health_providers into Orchestrator**

In `src/acheron/shell/orchestrator.py`, add the import:

```python
from acheron.shell.health_providers import create_health_providers
```

In `Orchestrator.__init__`, after `self._settings = settings or load_settings()` and before constructing `HealthMonitor`, add:

```python
        self._health_providers = create_health_providers(self._settings)
```

Then update the `HealthMonitor` construction to pass `providers`:

```python
        self._health_monitor = HealthMonitor(
            registry,
            interval=float(self._settings.orchestrator.health_check_interval_seconds),
            providers=self._health_providers,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/shell/test_orchestrator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/acheron/shell/orchestrator.py tests/shell/test_orchestrator.py
git commit -m "feat(orchestrator): wire HealthProviders from settings into HealthMonitor"
```

---

### Task 14: Update acheron.yaml.example

**Files:**
- Modify: `acheron.yaml.example`

- [ ] **Step 1: Add the providers section**

Append to `acheron.yaml.example` (after the `workers:` block):

```yaml

# Platform provider credentials for decoupled health checks.
# API keys can be set here or via environment variables using ${VAR} expansion.
# Workers reference a provider by name via the `health_provider` key in their
# capabilities metadata, and a platform-specific `health_endpoint_id`.
providers:
  runpod:
    # RunPod API key (https://www.runpod.io/get-started/api-keys).
    # Used to query serverless endpoint status when a worker's HTTP probe fails.
    api_key: "${RUNPOD_API_KEY}"
  huggingface:
    # Hugging Face token (https://huggingface.co/settings/tokens).
    # Used to query Inference Endpoint status when a worker's HTTP probe fails.
    api_key: "${HF_API_KEY}"
```

- [ ] **Step 2: Commit**

```bash
git add acheron.yaml.example
git commit -m "docs(config): document providers section in acheron.yaml.example"
```

---

### Task 15: Update Specs to Match Finalized Design

**Files:**
- Modify: `docs/superpowers/specs/2026-06-16-implementation-roadmap.md`
- Modify: `docs/superpowers/specs/2026-06-16-acheron-design.md`
- Modify: `docs/superpowers/specs/2026-06-20-deployment-and-dashboard-design.md`

- [ ] **Step 1: Update the roadmap Layer 11 status**

In `docs/superpowers/specs/2026-06-16-implementation-roadmap.md`, update the status table row for Layer 11 (change `planned` to `partial`):

```markdown
| 11 | partial | Decoupled health checks (RunPod/HF), dashboard error & status updates. Worker packaging + CI/CD deferred to a separate plan. |
```

Update the Layer 11 section at the bottom of the file to reflect the split:

```markdown
## Layer 11 — Decoupled Platform Health Checks & Dashboard Integration

Implement decoupled provider health checks, modular container image compilation, and dashboard updates. See [Layer 11 design spec](./2026-06-20-deployment-and-dashboard-design.md).

- **Decoupled health checks**: Abstract `HealthProvider` class configuration mapping platform-specific endpoints (RunPod/HF) using API keys defined in `acheron.yaml`. ✅
- **Dashboard Updates**: Backend status endpoint (green/red dot) and worker status badges + error viewer. ✅
- **Modular Workers + CI/CD**: Isolated worker packages and GHCR publish workflow. Deferred to a separate plan (requires Docker/CUDA build context).
```

- [ ] **Step 2: Update the design spec with WorkerStatus + HealthProvider + last_error**

In `docs/superpowers/specs/2026-06-16-acheron-design.md`, add a new subsection under "Worker Registry" documenting the health provider layer. After the "Health monitoring:" paragraph, add:

```markdown
**Platform health checks (Layer 11):** When the orchestrator's HTTP/gRPC probe fails, the `HealthMonitor` consults a `HealthProvider` plugin (configured in `acheron.yaml` under `providers:`) named by the worker's `capabilities.metadata["health_provider"]`. The provider queries the platform API (RunPod Serverless endpoints, Hugging Face Inference Endpoints) using `capabilities.metadata["health_endpoint_id"]` and returns a `WorkerStatus` (`HEALTHY` | `BOOTING` | `OFFLINE`). Booting workers are not removed from the registry; offline workers follow the existing 3-strike removal. The worker's `status` and `last_error` are persisted by the store and surfaced on the `/workers` API response.

**Backend status partial:** The orchestrator serves `GET /partials/status` (an HTML snippet) that the dashboard proxies to render a green "Connected" / red "Disconnected" indicator next to the heading.
```

- [ ] **Step 3: Update the deployment-and-dashboard design spec with finalized decisions**

Append a "Finalized Design Decisions" section to `docs/superpowers/specs/2026-06-20-deployment-and-dashboard-design.md`:

```markdown
## 4. Finalized Design Decisions (Implementation)

- **Scope split:** Sections 2 and 3 (health checks + dashboard) are implemented first. Section 1 (decoupled worker packaging + CI/CD) is deferred to a separate plan — it requires Docker/CUDA build context and GPU worker skeletons to validate.
- **`/partials/status` proxy:** The orchestrator owns the status partial logic (`GET /partials/status` → green "Connected" HTML). The dashboard proxies it via its own same-origin `/partials/status` route, returning red "Disconnected" HTML when the orchestrator is unreachable. This keeps the logic in the orchestrator (per spec) while working in the compose setup where the browser cannot resolve the orchestrator's internal hostname.
- **`health_endpoint_id` is provider-specific:** RunPod → serverless endpoint id (`GET /endpoints/{id}`); HuggingFace → `namespace/name` (`GET /v2/endpoints/{namespace}/{name}`).
- **RunPod mapping:** endpoint exists → `BOOTING` (cold start); 404/error → `OFFLINE`.
- **HuggingFace mapping:** `status.state` in `{pending, initializing, starting, running}` → `BOOTING`; `{paused, failed}` or 404/error → `OFFLINE`.
- **Booting workers are not removed** — the failure counter is not incremented while a platform reports booting. A boot timeout is a future extension.
- **`${VAR}` env-var expansion** is applied to all `acheron.yaml` string values (not just provider keys) by the YAML settings source.
- **`HealthProbeResult`** (healthy + error) replaces the prior `bool` return from `HealthCheckFn` so `last_error` captures the actual probe failure reason.
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-06-16-implementation-roadmap.md docs/superpowers/specs/2026-06-16-acheron-design.md docs/superpowers/specs/2026-06-20-deployment-and-dashboard-design.md
git commit -m "docs(specs): update Layer 11 status and record finalized design decisions"
```

---

### Task 16: Adversarial Subagent Review

**Files:** None (review pass; fixes applied to files from Tasks 1-15)

- [ ] **Step 1: Dispatch a fresh adversarial review subagent**

Dispatch a general subagent with this prompt (adapt the diff base commit):

> You are an adversarial code reviewer. Review the uncommitted/unpushed changes on this branch for **code quality, correctness, and spec compliance**. Be critical and specific.
>
> **Spec to check against:** `docs/superpowers/specs/2026-06-20-deployment-and-dashboard-design.md` (Sections 2 and 3) and the finalized decisions in its Section 4.
>
> Focus on:
> 1. **Spec compliance:** Every requirement in §2 (WorkerStatus enum, HealthProvider ABC, config, self-registration via `capabilities.metadata`, `last_error` field, probing flow) and §3 (status circle via orchestrator `/partials/status`, worker status badges, View Error) is implemented. Flag any missed requirement or deviation not documented in §4.
> 2. **Correctness:** Store serialization round-trips (Redis `status`/`last_error`), the `HealthMonitor._handle_failure` state transitions (HEALTHY on success, BOOTING skips counter, OFFLINE increments + removes), provider error handling (provider raising → OFFLINE), and the dashboard proxy (green on 2xx, red on error).
> 3. **Code quality:** No `Any` abuse, no unnecessary ignores, import boundaries (`core` never imports `shell`), no dead config knobs, no placeholder stubs, ruff/mypy clean, tests don't depend on hardcoded repo paths.
> 4. **Test quality:** Tests cover the booting-not-removed path, offline-removal path, no-provider fallback, success-resets-status, provider-raises-offline, and the dashboard connected/disconnected states.
>
> Read the diff (`git diff main...HEAD`), the spec, and the relevant source files. Produce a numbered list of findings with severity (blocker / major / minor) and the exact file:line and a concrete fix for each. Do NOT fix anything — only report.

- [ ] **Step 2: Triage findings**

Review each finding. For blockers and majors, either apply a fix or document why the finding is invalid (receiving-code-review skill: verify before implementing). Minors: apply if quick.

- [ ] **Step 3: Apply fixes**

Apply the agreed fixes. Re-run `just validate` after each substantive fix.

- [ ] **Step 4: Commit fixes**

```bash
git add -A
git commit -m "fix(layer-11): address adversarial review findings"
```

---

### Task 17: Final Validation

- [ ] **Step 1: Run the full validation gate**

Run: `just validate`
Expected: lint-strict, lint-imports, type-check, type-check-pyright, and test all pass.

- [ ] **Step 2: Fix any remaining issues**

If any step fails, fix and re-run `just validate` until green.

- [ ] **Step 3: Verify the status table in the roadmap is accurate**

Confirm `docs/superpowers/specs/2026-06-16-implementation-roadmap.md` Layer 11 row says `partial` with the correct notes.

- [ ] **Step 4: Final commit (if any spec/validation fixes)**

```bash
git add -A
git commit -m "chore(layer-11): final validation pass"
```

---

## Self-Review

**1. Spec coverage (§2 Decoupled Provider Health Checks):**
- `WorkerStatus` enum → Task 1 ✓
- `HealthProvider` ABC → Task 4 ✓
- `acheron.yaml` provider config → Task 4 ✓
- Self-registration via `capabilities.metadata` (reserved keys) → Task 8 (reads `health_provider`/`health_endpoint_id` from `capabilities.metadata`) ✓
- `last_error` field on `RegisteredWorker` + store serialization → Tasks 1, 2, 3 ✓
- Probing flow (HTTP fail → provider → BOOTING/OFFLINE) → Task 8 ✓

**2. Spec coverage (§3 Dashboard Updates):**
- Connected/Disconnected circle next to heading → Tasks 10, 11 ✓
- `/partials/status` in orchestrator API → Task 10 ✓
- Workers table status badges (Healthy/Booting/Offline) → Task 12 ✓
- "View Error" toggle for Booting/Offline → Task 12 ✓
- `/workers` response includes `status` + `last_error` → Task 9 ✓
- No job submission / capabilities stubs → none added ✓

**3. Placeholder scan:** No "TBD", "TODO", "implement later", or placeholder steps. Each code step contains complete code.

**4. Type consistency:** `WorkerStatus` used consistently. `set_worker_status(worker_id, status, last_error)` signature matches across ABC, InMemory, Redis, and the `HealthMonitor._handle_failure` call site. `HealthProbeResult(healthy, error)` matches the `HealthCheckFn` type and all test mocks. `HealthProviders.get(name) -> HealthProvider | None` matches usage in `HealthMonitor`.

**5. Deferred (§1):** Worker packaging + CI/CD explicitly deferred and documented in the roadmap + deployment spec. Not partially stubbed.
