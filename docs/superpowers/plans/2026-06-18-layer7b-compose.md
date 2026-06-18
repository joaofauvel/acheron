# Layer 7b Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Production hardening for the Docker Compose stack: healthchecks on every service, persistent volumes for `/data` and Redis, fail-fast data dir check, env-configurable data dir.

**Architecture:** Mostly compose changes plus a small Python change in the gRPC stub (add FastAPI /health sidecar) and the orchestrator (writability check). No new dependencies.

**Tech Stack:** Existing — Docker Compose, FastAPI, uvicorn, httpx, pytest.

---

## File Structure

### Modified

- `docker-compose.yml` — healthchecks on every service, named volumes, depends_on conditions, ACHERON_DATA_DIR
- `src/acheron/shell/api/app.py` — read ACHERON_DATA_DIR from env when `data_dir` not passed
- `src/acheron/shell/orchestrator.py` — writability check in `__init__`
- `src/acheron/shell/cache.py` — expose `data_dir` as a public property
- `stubs/grpc_worker_stub.py` — add HTTP `/health` sidecar (FastAPI + uvicorn) on `WORKER_HTTP_PORT`
- `Dockerfile` (grpc-stub target) — no change needed (FastAPI/uvicorn already in the project wheel)

### New

- `tests/shell/test_data_dir.py` — writability check + ACHERON_DATA_DIR env var tests
- `tests/shell/stubs/test_grpc_stub_health.py` — tests for the gRPC stub's HTTP `/health` endpoint

---

## Task 1: Expose `data_dir` as public property on `PlanCache`

**Files:**
- Modify: `src/acheron/shell/cache.py`

- [ ] **Step 1: Write the failing test**

Create `tests/shell/test_data_dir.py`:

```python
"""Tests for data dir handling."""

from pathlib import Path

from acheron.shell.cache import PlanCache


def test_data_dir_is_public_attribute(tmp_path: Path) -> None:
    """PlanCache exposes its data_dir for startup checks."""
    cache = PlanCache(data_dir=tmp_path)
    assert cache.data_dir == tmp_path
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/julia/devel/acheron && uv run pytest tests/shell/test_data_dir.py -v --no-cov`
Expected: FAIL with `AttributeError: 'PlanCache' object has no attribute 'data_dir'` (or similar).

- [ ] **Step 3: Add the public property**

In `src/acheron/shell/cache.py`, find the `PlanCache` class and add a `data_dir` property. The class has two `__init__` methods (one for plans, one for step outputs). Add the property to the first one (or as a class-level property if the structure allows).

Read the file first to find the right location. Add:

```python
    @property
    def data_dir(self) -> Path:
        """The root directory for cached plans and step outputs."""
        return self._data_dir
```

(Place it inside the `PlanCache` class, near the `__init__`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/julia/devel/acheron && uv run pytest tests/shell/test_data_dir.py -v --no-cov`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/julia/devel/acheron && git add src/acheron/shell/cache.py tests/shell/test_data_dir.py && git commit -m "feat(cache): expose data_dir as public property on PlanCache"
```

---

## Task 2: Read `ACHERON_DATA_DIR` env var in `create_app`

**Files:**
- Modify: `src/acheron/shell/api/app.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/shell/test_data_dir.py`:

```python
import os


def test_create_app_reads_acheron_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """create_app falls back to ACHERON_DATA_DIR when data_dir not passed."""
    from acheron.shell.api.app import create_app
    from acheron.shell.stores.memory import InMemoryWorkerStore

    monkeypatch.setenv("ACHERON_DATA_DIR", str(tmp_path))
    app = create_app(registry=InMemoryWorkerStore())
    assert app.state.orchestrator._cache.data_dir == tmp_path  # noqa: SLF001
```

(Add `import pytest` to the top of the file.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/julia/devel/acheron && uv run pytest tests/shell/test_data_dir.py -v --no-cov`
Expected: FAIL with `TypeError: create_app() got an unexpected keyword argument 'registry'` or the env var not being read.

- [ ] **Step 3: Update `create_app` to read the env var**

In `src/acheron/shell/api/app.py`, update the `create_app` signature and body:

```python
def create_app(
    registry: WorkerStore | None = None,
    cache: PlanCache | None = None,
    data_dir: Path | str | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    if registry is None:
        registry = create_worker_store()
    if cache is None:
        if data_dir is None:
            data_dir = Path(os.environ.get("ACHERON_DATA_DIR", "/data/jobs"))
        cache = PlanCache(data_dir)
    # ... rest unchanged
```

Add `import os` at the top (or merge with the existing `from __future__ import annotations` block).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/julia/devel/acheron && uv run pytest tests/shell/test_data_dir.py -v --no-cov`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/julia/devel/acheron && git add src/acheron/shell/api/app.py tests/shell/test_data_dir.py && git commit -m "feat(app): read ACHERON_DATA_DIR env var in create_app"
```

---

## Task 3: Add writability check to `Orchestrator.__init__`

**Files:**
- Modify: `src/acheron/shell/orchestrator.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/shell/test_data_dir.py`:

```python
import pytest
from acheron.shell.orchestrator import Orchestrator
from acheron.shell.cache import PlanCache
from acheron.shell.errors import AcheronError
from acheron.shell.stores.memory import InMemoryWorkerStore


def test_orchestrator_creates_data_dir_if_missing(tmp_path: Path) -> None:
    """Orchestrator creates the data dir if it doesn't exist."""
    target = tmp_path / "new" / "subdir"
    reg = InMemoryWorkerStore()
    cache = PlanCache(data_dir=target)
    Orchestrator(registry=reg, cache=cache)
    assert target.exists()
    assert target.is_dir()


def test_orchestrator_raises_on_unwritable_data_dir(tmp_path: Path) -> None:
    """Orchestrator raises AcheronError if data dir is not writable."""
    target = tmp_path / "locked"
    target.mkdir()
    target.chmod(0o444)  # read-only
    reg = InMemoryWorkerStore()
    cache = PlanCache(data_dir=target)
    with pytest.raises(AcheronError, match="not writable"):
        Orchestrator(registry=reg, cache=cache)
    target.chmod(0o755)  # restore for cleanup
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/julia/devel/acheron && uv run pytest tests/shell/test_data_dir.py -v --no-cov`
Expected: FAIL (orchestrator doesn't check writability yet).

- [ ] **Step 3: Add the writability check**

In `src/acheron/shell/orchestrator.py`, modify `Orchestrator.__init__`:

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
        self._verify_data_dir_writable()
        self._register_built_in_local_workers()
        self._handler = handler or create_step_handler(registry)
        self._job_store = job_store if job_store is not None else create_job_store()
        self._tasks: set[asyncio.Task[None]] = set()
        self._health_monitor = HealthMonitor(registry)

    def _verify_data_dir_writable(self) -> None:
        """Ensure the data dir exists and is writable. Raises AcheronError otherwise."""
        from acheron.core.errors import AcheronError  # noqa: PLC0415

        data_dir = self._cache.data_dir
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            probe = data_dir / ".acheron_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.read_text(encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            msg = (
                f"Data dir {data_dir} is not writable: {exc}. "
                "Mount a writable volume or set ACHERON_DATA_DIR to a writable path."
            )
            raise AcheronError(msg) from exc
```

(Remove the now-unused `from acheron.core.errors import AcheronError` if it's only used here — actually it's used elsewhere too, keep it.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/julia/devel/acheron && uv run pytest tests/shell/test_data_dir.py -v --no-cov`
Expected: PASS

- [ ] **Step 5: Run full validation**

Run: `cd /home/julia/devel/acheron && just validate 2>&1 | tail -8`
Expected: all checks pass. The unwritable-dir test may behave differently when run as root (root can write to read-only files). If it fails, skip the test in CI (mark with `@pytest.mark.skipif(os.geteuid() == 0, ...)`).

- [ ] **Step 6: Commit**

```bash
cd /home/julia/devel/acheron && git add src/acheron/shell/orchestrator.py tests/shell/test_data_dir.py && git commit -m "feat(orchestrator): fail fast at startup if data dir is not writable"
```

---

## Task 4: Add HTTP `/health` sidecar to gRPC stub

**Files:**
- Modify: `stubs/grpc_worker_stub.py`

- [ ] **Step 1: Write the failing test**

Create `tests/shell/stubs/test_grpc_stub_health.py`:

```python
"""Tests for the gRPC stub's HTTP /health sidecar."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from stubs.grpc_worker_stub import create_http_app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest_asyncio.fixture
async def http_client() -> AsyncIterator[AsyncClient]:
    app = create_http_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_health_returns_ok(http_client: AsyncClient) -> None:
    resp = await http_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/julia/devel/acheron && uv run pytest tests/shell/stubs/test_grpc_stub_health.py -v --no-cov`
Expected: FAIL with `ImportError: cannot import name 'create_http_app'`.

- [ ] **Step 3: Add the `create_http_app` factory and sidecar launch**

In `stubs/grpc_worker_stub.py`, add the imports:
```python
import uvicorn
from fastapi import FastAPI
```

Add the HTTP app factory:
```python
def create_http_app() -> FastAPI:
    """Create the FastAPI app for the /health sidecar."""
    app = FastAPI(title="gRPC Stub Health")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/julia/devel/acheron && uv run pytest tests/shell/stubs/test_grpc_stub_health.py -v --no-cov`
Expected: PASS

- [ ] **Step 5: Wire the sidecar into the gRPC stub's main entrypoint**

In `stubs/grpc_worker_stub.py`, update `_serve` to start both servers:

```python
async def _serve() -> None:
    """Run the stub gRPC server and HTTP /health sidecar."""
    port = int(os.environ.get("WORKER_PORT", "9001"))
    http_port = int(os.environ.get("WORKER_HTTP_PORT", "9002"))

    server, actual_port = await create_server(port)
    await server.start()
    logger.info("gRPC stub worker listening on port %d", actual_port)

    http_app = create_http_app()
    config = uvicorn.Config(http_app, host="0.0.0.0", port=http_port, log_level="warning")
    http_server = uvicorn.Server(config)
    logger.info("HTTP /health sidecar listening on port %d", http_port)

    server_task = asyncio.create_task(server.wait_for_termination())
    http_task = asyncio.create_task(http_server.serve())
    try:
        await asyncio.gather(server_task, http_task)
    finally:
        await server.stop(0)
```

- [ ] **Step 6: Commit**

```bash
cd /home/julia/devel/acheron && git add stubs/grpc_worker_stub.py tests/shell/stubs/test_grpc_stub_health.py && git commit -m "feat(grpc-stub): add HTTP /health sidecar on WORKER_HTTP_PORT"
```

---

## Task 5: Update `docker-compose.yml` with healthchecks, volumes, and depends_on conditions

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add healthcheck to redis (verify existing) and orchestrator**

The redis healthcheck is already there. Add a healthcheck to orchestrator:

```yaml
  orchestrator:
    build:
      context: .
      target: orchestrator
    ports:
      - "8000:8000"
    environment:
      REDIS_URL: redis://redis:6379
      ACHERON_REGISTRATION_TOKEN: ${ACHERON_REGISTRATION_TOKEN:-dev-registration-token}
      ACHERON_DATA_DIR: /data/jobs
    volumes:
      - acheron-data:/data
    healthcheck:
      test: ["CMD-SHELL", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    depends_on:
      redis:
        condition: service_healthy
```

- [ ] **Step 2: Add healthcheck to dashboard**

```yaml
  dashboard:
    build:
      context: .
      target: dashboard
    ports:
      - "8080:8080"
    environment:
      ACHERON_URL: http://orchestrator:8000
    healthcheck:
      test: ["CMD-SHELL", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/').read()"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    depends_on:
      orchestrator:
        condition: service_healthy
```

- [ ] **Step 3: Add healthcheck to HTTP stubs (tts-stub, asr-stub, translation-stub)**

For each of `tts-stub`, `asr-stub`, `translation-stub`, add:

```yaml
    healthcheck:
      test: ["CMD-SHELL", "python", "-c", "import urllib.request, os; urllib.request.urlopen(f'http://localhost:{os.environ[\"WORKER_PORT\"]}/health').read()"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    depends_on:
      orchestrator:
        condition: service_healthy
```

(Replace the existing simple `depends_on: - orchestrator`.)

- [ ] **Step 4: Add healthcheck and HTTP_PORT to tts-grpc-stub**

```yaml
  tts-grpc-stub:
    build:
      context: .
      target: grpc-stub
    ports:
      - "9001:9001"
      - "9002:9002"
    environment:
      WORKER_ENDPOINT: tts-grpc-stub:9001
      ORCHESTRATOR_URL: http://orchestrator:8000
      WORKER_PORT: "9001"
      WORKER_HTTP_PORT: "9002"
      ACHERON_REGISTRATION_TOKEN: ${ACHERON_REGISTRATION_TOKEN:-dev-registration-token}
    healthcheck:
      test: ["CMD-SHELL", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:9002/health').read()"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    depends_on:
      orchestrator:
        condition: service_healthy
```

(Also: change `WORKER_ENDPOINT` to `tts-grpc-stub:9001` (no scheme) since the gRPC stub now strips it, but keeping `http://` for backward compat is fine — the stub strips it before registering.)

- [ ] **Step 5: Add volume to redis and declare named volumes**

For `redis`:
```yaml
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      retries: 3
      start_period: 5s
```

Add at the bottom of the file:
```yaml
volumes:
  acheron-data:
  redis-data:
```

- [ ] **Step 6: Validate the docker-compose.yml is syntactically correct**

Run: `cd /home/julia/devel/acheron && docker compose config 2>&1 | tail -10`
Expected: `services:` and the services listed without error. If Docker isn't available, just `cat` the file to eyeball it.

- [ ] **Step 7: Commit**

```bash
cd /home/julia/devel/acheron && git add docker-compose.yml && git commit -m "chore(compose): add healthchecks, named volumes, depends_on conditions"
```

---

## Task 6: Final validation

- [ ] **Step 1: Run full validation**

Run: `cd /home/julia/devel/acheron && just validate 2>&1 | tail -10`
Expected: all checks pass.

- [ ] **Step 2: Verify the plan tasks are complete**

Confirm:
- [ ] `PlanCache.data_dir` is a public property
- [ ] `create_app` reads `ACHERON_DATA_DIR` env var
- [ ] `Orchestrator.__init__` runs writability check, raises `AcheronError` on failure
- [ ] gRPC stub has `create_http_app()` factory and runs FastAPI sidecar
- [ ] `docker-compose.yml` has healthchecks on every service, named volumes, depends_on conditions
- [ ] All existing tests pass
- [ ] New tests pass
- [ ] `docker compose config` validates without error (if Docker available)

- [ ] **Step 3: Smoke test (if Docker available)**

If Docker is running locally:
```bash
cd /home/julia/devel/acheron && docker compose up --build -d
sleep 30
docker compose ps  # all should be "healthy"
docker compose down -v
```

If Docker isn't available, skip this step.
