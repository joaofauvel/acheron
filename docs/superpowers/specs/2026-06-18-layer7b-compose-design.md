# Acheron — Layer 7b Design Spec

**Production Compose Hardening**

This is a sub-project of [Acheron design spec](./2026-06-16-acheron-design.md) and [implementation roadmap](./2026-06-16-implementation-roadmap.md). It makes the Docker Compose stack production-ready: healthchecks, persistent volumes, and a fail-fast data dir check.

## Goal

Three production concerns the current `docker-compose.yml` doesn't address:

1. **Healthchecks** — only redis has one. The orchestrator, dashboard, and worker stubs can be marked healthy by Docker even when their processes are broken. Other services with `depends_on: orchestrator` start racing to connect before the API is actually serving.

2. **Persistent volumes** — no named volumes. Container restarts lose the orchestrator's plan cache and Redis state. Layer 7a made state persist to Redis (when configured), but Redis itself has no volume, and the orchestrator's local `PlanCache` writes to `/data/jobs` which is container-local.

3. **Fail-fast on bad data dir** — if `/data/jobs` isn't mounted or isn't writable, the first plan write fails deep in request handling. The orchestrator should crash at startup with a clear message.

## Design

### 1. Docker healthchecks

Add `healthcheck` blocks to every service in `docker-compose.yml`:

```yaml
services:
  redis:
    # existing healthcheck stays
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      retries: 3
      start_period: 5s

  orchestrator:
    healthcheck:
      test: ["CMD-SHELL", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    # ...

  dashboard:
    healthcheck:
      test: ["CMD-SHELL", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/').read()"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    # ...

  tts-stub:
  asr-stub:
  translation-stub:
    healthcheck:
      test: ["CMD-SHELL", "python", "-c", "import urllib.request; urllib.request.urlopen(f'http://localhost:{__import__(\"os\").environ[\"WORKER_PORT\"]}/health').read()"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    # ...
```

Worker healthcheck uses the existing `/health` endpoints on the HTTP stubs (no code change to those).

### 2. HTTP `/health` on the gRPC stub

The gRPC stub has a gRPC `HealthServicer` (added with the Layer 6 health-monitor fix) but no HTTP endpoint. Docker healthchecks want HTTP, so we add a small FastAPI sidecar on the gRPC worker container.

**Approach**: run a FastAPI app alongside the gRPC server in the same process, sharing the asyncio event loop. Two `uvicorn` apps is messy; better: a single FastAPI app that hosts both the gRPC servicer and the HTTP `/health`.

But that's a structural change. Simpler: run two uvicorn workers (one HTTP, one gRPC) in the same process using a custom entrypoint.

**Concrete plan**: in `stubs/grpc_worker_stub.py`:
- Add a `_http_app = FastAPI()` with a `/health` endpoint
- Start the gRPC server (as today) plus an `uvicorn.Server` for the HTTP app on a separate port (e.g., 9002)
- `WORKER_HTTP_PORT` env var, default `9002`
- The gRPC server stays on `WORKER_PORT` (default 9001)
- Both run in the same `asyncio.run` loop via `asyncio.gather`

Compose uses the HTTP port for the healthcheck:
```yaml
  tts-grpc-stub:
    environment:
      WORKER_HTTP_PORT: "9002"
    healthcheck:
      test: ["CMD-SHELL", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:9002/health').read()"]
      # ...
```

**Alternative considered**: run the HTTP server on the same port via `grpc.aio.server()` with an HTTP listener. Rejected: gRPC servers don't support HTTP on the same port cleanly. Two ports is simpler.

### 3. `depends_on` upgrade

Change all `depends_on: - service` to `depends_on: { service: { condition: service_healthy } }` for:
- `dashboard.orchestrator`
- `tts-stub.orchestrator`
- `asr-stub.orchestrator`
- `tts-grpc-stub.orchestrator`
- `translation-stub.orchestrator`
- `orchestrator.redis` (already uses this form; keep)

This eliminates the race where a worker or dashboard starts before the orchestrator's `/health` is serving.

### 4. Persistent volumes

```yaml
services:
  redis:
    volumes:
      - redis-data:/data
    # ... (remove the host port 6379 if not needed for dev access; keep it)

  orchestrator:
    volumes:
      - acheron-data:/data
    environment:
      ACHERON_DATA_DIR: /data/jobs
    # ...

volumes:
  acheron-data:
  redis-data:
```

`acheron-data` mounted at `/data`, with `ACHERON_DATA_DIR=/data/jobs` for the orchestrator's `PlanCache`. `redis-data` mounted at `/data` inside the redis container, with the redis config pointing its `dir` to `/data` (the default).

This way Redis persists across container restarts, and the orchestrator's plan cache persists across restarts.

### 5. `ACHERON_DATA_DIR` env var

Currently `create_app` defaults `data_dir` to `Path("/data/jobs")`. Make it env-driven:

```python
def create_app(
    registry: WorkerStore | None = None,
    cache: PlanCache | None = None,
    data_dir: Path | str | None = None,
) -> FastAPI:
    """..."""
    if registry is None:
        registry = create_worker_store()
    if cache is None:
        if data_dir is None:
            data_dir = Path(os.environ.get("ACHERON_DATA_DIR", "/data/jobs"))
        cache = PlanCache(data_dir)
    # ...
```

Tests pass `tmp_path` explicitly (already the pattern). The env var is only consulted when neither `cache` nor `data_dir` is provided. Production sets `ACHERON_DATA_DIR=/data/jobs` in compose.

### 6. `/data` writability check

At orchestrator startup, in `Orchestrator.__init__`:

```python
def __init__(self, registry, cache, handler=None, *, job_store=None):
    self._registry = registry
    self._cache = cache
    self._verify_data_dir_writable()  # NEW
    self._register_built_in_local_workers()
    # ...
```

`_verify_data_dir_writable`:
1. Get the data dir from `self._cache._data_dir` (private but stable; or expose a public property)
2. Create the dir if it doesn't exist (`mkdir(parents=True, exist_ok=True)`)
3. Write a temp file (e.g., `.write_test`), read it back, delete it
4. Raise `AcheronError` on any failure with a clear message: `"Data dir {data_dir} is not writable: {reason}. Mount a volume or set ACHERON_DATA_DIR."`

The check runs once at startup. Once it passes, the dir is assumed writable for the orchestrator's lifetime.

**Where this lives**: in `Orchestrator.__init__` rather than `create_app`, because:
- The check is about the orchestrator's data dependencies, not the FastAPI app
- Tests that construct `Orchestrator(registry=..., cache=PlanCache(tmp_path))` get the same validation
- A future alternative entrypoint (e.g., a CLI) gets the same validation

## Test Strategy

*New:*
- `tests/shell/test_data_dir.py` — tests for writability check (writable: passes; unwritable: raises) and `ACHERON_DATA_DIR` env var reading
- `tests/shell/stubs/test_grpc_stub_health.py` — tests the gRPC stub's HTTP /health endpoint

*Modified:*
- `tests/shell/api/test_jobs.py`, `tests/shell/conftest.py`, `tests/integration/conftest.py` — no change needed (they pass `tmp_path` explicitly)
- `docker-compose.yml` — manual smoke test in dev: `docker compose up`, then `docker ps` should show all services `healthy` within 30s

Per AGENTS.md: tests don't depend on hardcoded paths. `ACHERON_DATA_DIR` is read only when `data_dir` is None, so tests can pass `tmp_path` to override.

## Files

### New

- `tests/shell/test_data_dir.py` — writability check + env var tests
- `tests/shell/stubs/test_grpc_stub_health.py` — gRPC stub HTTP /health tests

### Modified

- `docker-compose.yml` — healthchecks, volumes, depends_on conditions, ACHERON_DATA_DIR
- `src/acheron/shell/api/app.py` — read ACHERON_DATA_DIR
- `src/acheron/shell/orchestrator.py` — writability check
- `src/acheron/shell/cache.py` — expose public `data_dir` property (or keep private access)
- `stubs/grpc_worker_stub.py` — add HTTP /health sidecar
- `pyproject.toml` — add `aiohttp~=` if needed (or use stdlib `http.server`)

### Unchanged

- All transports, stores, plans, executors — unrelated to compose hardening

## Dependencies

- No new runtime dependencies. HTTP server uses Python stdlib (`http.server` or `aiohttp` if needed for asyncio compatibility).
- `aiohttp` is a small dep (~200KB) if we go that route; stdlib `http.server` works but requires threads.
- Decision: use `aiohttp` to share the event loop with the gRPC server. Add to dev deps for the gRPC stub image only (the stub Dockerfile already installs the project wheel which has access to all transitive deps).

## Out of Scope

- **Resource limits** (cpus, mem_limit) — explicitly deferred per user decision
- **TLS** (Layer 7c)
- **Multi-host deployment** — out of scope
- **Backup/restore** — out of scope
- **Log aggregation** — out of scope
- **Metrics endpoints** (`/metrics` for Prometheus) — out of scope
