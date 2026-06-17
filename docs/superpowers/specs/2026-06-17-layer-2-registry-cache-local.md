# Layer 2 — Worker Registry + Caching + LocalWorker

First shell/ layer with I/O. WorkerRegistry, file-based plan/step caching, and LocalWorker transport.

## Module Layout

```
src/acheron/shell/registry.py
src/acheron/shell/cache.py
src/acheron/shell/transports/__init__.py
src/acheron/shell/transports/local.py

tests/shell/__init__.py
tests/shell/test_registry.py
tests/shell/test_cache.py
tests/shell/test_local_worker.py
```

## Design Decision: Redis Deferred

The spec says WorkerRegistry is "in-memory store backed by Redis". For Layer 2 we use
a plain dict. Redis is deferred to Layer 5 (when multi-process sync via Docker Compose
is needed). The registry interface is unchanged — swapping to Redis is an implementation detail.

## `shell/registry.py`

```python
@dataclass
class RegisteredWorker:
    worker_id: str
    endpoint: str
    transport: str          # "http" | "grpc" | "local"
    capabilities: WorkerCapabilities
    consecutive_failures: int = 0
    last_health_check: float | None = None

class WorkerRegistry:
    def register(self, worker_id: str, endpoint: str, transport: str, capabilities: WorkerCapabilities) -> None: ...
    def unregister(self, worker_id: str) -> None: ...
    def get(self, worker_id: str) -> RegisteredWorker | None: ...
    def list_all(self) -> tuple[RegisteredWorker, ...]: ...
    def find_by_type(self, worker_type: WorkerType) -> tuple[RegisteredWorker, ...]: ...
    def find_by_language(self, src: str, dst: str) -> tuple[RegisteredWorker, ...]: ...
    def record_health_failure(self, worker_id: str) -> bool: ...  # True if removed (3 failures)
    def record_health_success(self, worker_id: str) -> None: ...
```

- `register` overwrites if worker_id already exists (re-registration)
- `find_by_language` checks `src in supported_languages_in AND dst in supported_languages_out`
- `record_health_failure` increments counter, removes worker after 3 consecutive failures

## `shell/cache.py`

```python
class PlanCache:
    def __init__(self, data_dir: str | Path = "/data/jobs") -> None: ...
    def save_plan(self, plan: Plan) -> Path: ...
    def load_plan(self, plan_id: str) -> Plan: ...
    def plan_exists(self, plan_id: str) -> bool: ...

class StepCache:
    def __init__(self, data_dir: str | Path = "/data/jobs") -> None: ...
    def save_outputs(self, job_id: str, step_id: str, outputs: tuple[OutputFile, ...]) -> None: ...
    def load_outputs(self, job_id: str, step_id: str) -> tuple[OutputFile, ...]: ...
    def step_has_valid_cache(self, job_id: str, step_id: str) -> bool: ...
```

- Plans stored as JSON at `{data_dir}/{plan_id}/plan.json`
- Step outputs stored at `{data_dir}/{plan_id}/{step_id}/`
- Manifest file at `{data_dir}/{plan_id}/{step_id}/manifest.json` with checksums
- `step_has_valid_cache` checks manifest exists and all checksums match

## `shell/transports/local.py`

```python
type JobHandler = Callable[[Job], Awaitable[JobResult]]

class LocalWorker(Worker):
    def __init__(
        self,
        worker_type: WorkerType,
        handler: JobHandler,
        supported_languages_in: frozenset[str] = frozenset(),
        supported_languages_out: frozenset[str] = frozenset(),
        supported_formats_in: frozenset[str] = frozenset(),
        supported_formats_out: frozenset[str] = frozenset(),
    ) -> None: ...
    async def capabilities(self) -> WorkerCapabilities: ...
    async def execute(self, job: Job) -> JobResult: ...
    async def health(self) -> bool: ...
```

- `handler` is an async callable that implements the actual work
- `health()` always returns True (local process)
- Used for CPU steps: extraction, chunking, packaging

## Tests

### test_registry.py
- Register and retrieve worker
- Unregister
- Lookup nonexistent returns None
- find_by_type filters correctly
- find_by_language filters correctly
- Health failure increments counter
- Removed after 3 consecutive failures
- Re-registration overwrites

### test_cache.py
- Save and load plan (round-trip)
- plan_exists returns True/False
- Save and load step outputs
- step_has_valid_cache with valid manifest
- step_has_valid_cache with missing manifest
- step_has_valid_cache with corrupted checksum

### test_local_worker.py
- Execute delegates to handler
- Health always returns True
- Capabilities reflect constructor args

## Acceptance Criteria

- [ ] `just validate` passes
- [ ] import-linter boundary maintained (shell imports core, not vice versa)
- [ ] 100% test coverage on new code
