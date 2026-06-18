# Docker Compose Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [`) syntax for tracking.

**Goal:** Containerize orchestrator + stub workers, wire with Docker Compose, add registration security.

**Architecture:** Orchestrator gets a Dockerfile and registration token validation. Stub workers are minimal FastAPI apps that self-register on startup and return instant mock results. Docker Compose ties everything together.

**Tech Stack:** FastAPI, uvicorn, httpx, Docker, Docker Compose

---

## File Structure

```
src/acheron/shell/api/routes/workers.py   — add token validation dependency
src/acheron/shell/api/deps.py             — add registration token dependency
tests/shell/api/test_workers.py           — add token validation tests
stubs/__init__.py                         — package marker
stubs/worker_stub.py                      — stub FastAPI app
stubs/tests/__init__.py                   — package marker
stubs/tests/test_worker_stub.py           — stub unit tests
Dockerfile.orchestrator                   — orchestrator container
Dockerfile.worker-stub                    — stub worker container
docker-compose.yml                        — service wiring
```

---

### Task 1: Registration Security — Token Validation

Add `ACHERON_REGISTRATION_TOKEN` env var check to `POST /workers`. If set, requests must include `Authorization: Bearer <token>`. If unset, registration is open (backward compatible).

**Files:**
- Modify: `src/acheron/shell/api/deps.py`
- Modify: `src/acheron/shell/api/routes/workers.py`
- Modify: `tests/shell/api/test_workers.py`
- Modify: `tests/shell/conftest.py`

- [ ] **Step 1: Write failing tests for token validation**

Add to `tests/shell/api/test_workers.py`:

```python
import os

class TestRegistrationSecurity:
    @pytest.mark.asyncio
    async def test_register_with_valid_token(self, client_with_token) -> None:
        response = await client_with_token.post(
            "/workers",
            json={
                "worker_id": "asr-1",
                "endpoint": "http://asr:8000",
                "transport": "http",
                "capabilities": {
                    "worker_type": "asr",
                    "supported_languages_in": ["en"],
                    "supported_languages_out": ["en"],
                },
            },
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 201

    @pytest.mark.asyncio
    async def test_register_without_token_rejected(self, client_with_token) -> None:
        response = await client_with_token.post(
            "/workers",
            json={
                "worker_id": "asr-1",
                "endpoint": "http://asr:8000",
                "transport": "http",
                "capabilities": {
                    "worker_type": "asr",
                    "supported_languages_in": ["en"],
                    "supported_languages_out": ["en"],
                },
            },
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_register_with_wrong_token_rejected(self, client_with_token) -> None:
        response = await client_with_token.post(
            "/workers",
            json={
                "worker_id": "asr-1",
                "endpoint": "http://asr:8000",
                "transport": "http",
                "capabilities": {
                    "worker_type": "asr",
                    "supported_languages_in": ["en"],
                    "supported_languages_out": ["en"],
                },
            },
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_register_without_token_env_open(self, client) -> None:
        """When ACHERON_REGISTRATION_TOKEN is unset, registration is open."""
        response = await client.post(
            "/workers",
            json={
                "worker_id": "asr-1",
                "endpoint": "http://asr:8000",
                "transport": "http",
                "capabilities": {
                    "worker_type": "asr",
                    "supported_languages_in": ["en"],
                    "supported_languages_out": ["en"],
                },
            },
        )
        assert response.status_code == 201
```

Add `client_with_token` fixture to `tests/shell/conftest.py`:

```python
@pytest_asyncio.fixture
async def client_with_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    """Create an async client with registration token enabled."""
    monkeypatch.setenv("ACHERON_REGISTRATION_TOKEN", "test-token")
    app = make_app(tmp_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/shell/api/test_workers.py -v`
Expected: FAIL on new tests (fixture not found, 401 not returned, etc.)

- [ ] **Step 3: Add registration token dependency**

Add to `src/acheron/shell/api/deps.py`:

```python
import os

from fastapi import Header, HTTPException


def verify_registration_token(authorization: str | None = Header(None)) -> None:
    """Validate registration token if ACHERON_REGISTRATION_TOKEN is set."""
    token = os.environ.get("ACHERON_REGISTRATION_TOKEN")
    if token is None:
        return  # open registration (dev mode)
    if authorization is None:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    scheme, _, provided = authorization.partition(" ")
    if scheme.lower() != "bearer" or provided != token:
        raise HTTPException(status_code=401, detail="Invalid registration token")


RegistrationTokenDep = Annotated[None, Depends(verify_registration_token)]
```

- [ ] **Step 4: Wire dependency into registration route**

Modify `src/acheron/shell/api/routes/workers.py` — add `RegistrationTokenDep` parameter to `register_worker`:

```python
from acheron.shell.api.deps import OrchestratorDep, RegistrationTokenDep

@router.post("", status_code=201, response_model=WorkerResponse)
async def register_worker(
    body: WorkerRegistrationRequest,
    orch: OrchestratorDep,
    _token: RegistrationTokenDep,
) -> WorkerResponse:
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/shell/api/test_workers.py -v`
Expected: PASS

- [ ] **Step 6: Run full test suite**

Run: `just validate`
Expected: All 274+ tests pass, lint clean, type check clean.

- [ ] **Step 7: Commit**

```bash
git add src/acheron/shell/api/deps.py src/acheron/shell/api/routes/workers.py tests/shell/api/test_workers.py tests/shell/conftest.py
git commit -m "feat(api): add registration token validation for POST /workers"
```

---

### Task 2: Stub Worker App

Minimal FastAPI app that self-registers on startup and returns instant mock results.

**Files:**
- Create: `stubs/__init__.py`
- Create: `stubs/worker_stub.py`
- Create: `stubs/tests/__init__.py`
- Create: `stubs/tests/test_worker_stub.py`

- [ ] **Step 1: Write failing tests for stub worker**

Create `stubs/tests/__init__.py` (empty).

Create `stubs/tests/test_worker_stub.py`:

```python
"""Tests for the stub worker app."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from stubs.worker_stub import create_app


@pytest.fixture
def tts_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_TYPE", "TTS")
    monkeypatch.setenv("WORKER_ENDPOINT", "http://tts-stub:8001")
    monkeypatch.setenv("ORCHESTRATOR_URL", "http://orchestrator:8000")
    monkeypatch.setenv("WORKER_PORT", "8001")
    monkeypatch.setenv("ACHERON_REGISTRATION_TOKEN", "dev-registration-token")


@pytest.fixture
def asr_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_TYPE", "ASR")
    monkeypatch.setenv("WORKER_ENDPOINT", "http://asr-stub:8002")
    monkeypatch.setenv("ORCHESTRATOR_URL", "http://orchestrator:8000")
    monkeypatch.setenv("WORKER_PORT", "8002")
    monkeypatch.setenv("ACHERON_REGISTRATION_TOKEN", "dev-registration-token")


@pytest.mark.asyncio
async def test_health_returns_200(tts_env: None) -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_tts_submit_returns_wav(tts_env: None) -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/submit", json={"job_id": "test-1", "payload": {"text": "hello"}})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    # WAV header starts with RIFF
    import base64
    audio_bytes = base64.b64decode(data["output_data"])
    assert audio_bytes[:4] == b"RIFF"


@pytest.mark.asyncio
async def test_asr_submit_returns_text(asr_env: None) -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/submit", json={"job_id": "test-1", "payload": {"audio": "base64data"}})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert "mock transcription" in data["output_data"]


@pytest.mark.asyncio
async def test_self_registers_on_startup(tts_env: None) -> None:
    """Verify the app attempts to register with the orchestrator on startup."""
    with patch("stubs.worker_stub.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status_code = 201
        mock_response.raise_for_status = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        app = create_app()
        # Trigger lifespan
        async with app.router.lifespan_context(app):
            pass

        # Should have called POST /workers
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "/workers" in call_args[0][0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest stubs/tests/test_worker_stub.py -v`
Expected: FAIL (module not found, etc.)

- [ ] **Step 3: Create stub package**

Create `stubs/__init__.py` (empty file).

- [ ] **Step 4: Implement stub worker app**

Create `stubs/worker_stub.py`:

```python
"""Stub worker for local development — returns instant mock results."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import struct
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import FastAPI

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


def _silent_wav(duration_ms: int = 100, sample_rate: int = 22050) -> bytes:
    """Generate a minimal valid WAV file with silence."""
    num_samples = int(sample_rate * duration_ms / 1000)
    data_size = num_samples * 2  # 16-bit mono
    # RIFF header + fmt chunk + data chunk
    return (
        b"RIFF"
        + struct.pack("<I", 36 + data_size)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
        + b"data"
        + struct.pack("<I", data_size)
        + b"\x00" * data_size
    )


def _get_config() -> dict[str, str]:
    """Read configuration from environment."""
    return {
        "worker_type": os.environ["WORKER_TYPE"],
        "worker_endpoint": os.environ["WORKER_ENDPOINT"],
        "orchestrator_url": os.environ["ORCHESTRATOR_URL"],
        "worker_port": os.environ.get("WORKER_PORT", "8001"),
        "registration_token": os.environ.get("ACHERON_REGISTRATION_TOKEN", ""),
    }


async def _register(cfg: dict[str, str]) -> None:
    """Register with orchestrator, retrying until success."""
    worker_type = cfg["worker_type"].lower()
    worker_id = f"{worker_type}-stub"
    headers = {}
    if cfg["registration_token"]:
        headers["Authorization"] = f"Bearer {cfg['registration_token']}"

    payload = {
        "worker_id": worker_id,
        "endpoint": cfg["worker_endpoint"],
        "transport": "http",
        "capabilities": {
            "worker_type": worker_type,
            "supported_languages_in": ["en", "es", "fr", "de"],
            "supported_languages_out": ["en", "es", "fr", "de"],
            "metadata": {"stub": True},
        },
    }

    async with httpx.AsyncClient() as client:
        while True:
            try:
                health_resp = await client.get(f"{cfg['orchestrator_url']}/health")
                health_resp.raise_for_status()
                resp = await client.post(
                    f"{cfg['orchestrator_url']}/workers",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                logger.info("Registered %s with orchestrator", worker_id)
                return
            except (httpx.HTTPError, OSError) as exc:
                logger.debug("Orchestrator not ready (%s), retrying...", exc)
                await asyncio.sleep(1)


def create_app() -> FastAPI:
    """Create the stub worker FastAPI app."""
    cfg = _get_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await _register(cfg)
        yield

    app = FastAPI(title=f"{cfg['worker_type']} Stub Worker", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/submit")
    async def submit(body: dict[str, Any]) -> dict[str, Any]:
        if cfg["worker_type"] == "TTS":
            audio = _silent_wav()
            return {"status": "completed", "output_data": base64.b64encode(audio).decode()}
        return {"status": "completed", "output_data": "mock transcription"}

    return app
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest stubs/tests/test_worker_stub.py -v`
Expected: PASS

- [ ] **Step 6: Add stubs to ruff per-file-ignores**

In `pyproject.toml`, add to `[tool.ruff.lint.per-file-ignores]`:
```
"stubs/tests/**" = ["S"]
```

- [ ] **Step 7: Run full test suite**

Run: `just validate`
Expected: All tests pass, lint clean.

- [ ] **Step 8: Commit**

```bash
git add stubs/ tests/ pyproject.toml
git commit -m "feat: add stub worker app for local development"
```

---

### Task 3: Orchestrator Dockerfile

**Files:**
- Create: `Dockerfile.orchestrator`

- [ ] **Step 1: Create orchestrator Dockerfile**

Create `Dockerfile.orchestrator`:

```dockerfile
FROM python:3.14-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --no-dev --no-install-project

COPY src/acheron/ ./src/acheron/

ENV PYTHONPATH=/app/src

CMD ["uvicorn", "acheron.shell.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Verify image builds**

Run: `docker build -f Dockerfile.orchestrator -t acheron-orchestrator .`
Expected: Successfully built.

- [ ] **Step 3: Commit**

```bash
git add Dockerfile.orchestrator
git commit -m "feat: add orchestrator Dockerfile"
```

---

### Task 4: Worker Stub Dockerfile

**Files:**
- Create: `Dockerfile.worker-stub`

- [ ] **Step 1: Create worker stub Dockerfile**

Create `Dockerfile.worker-stub`:

```dockerfile
FROM python:3.14-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --no-dev --no-install-project

COPY src/acheron/ ./src/acheron/
COPY stubs/ ./stubs/

ENV PYTHONPATH=/app/src:/app

CMD ["uvicorn", "stubs.worker_stub:create_app", "--factory", "--host", "0.0.0.0", "--port", "8001"]
```

The port is overridden at runtime via `WORKER_PORT` env var and `--port` in the compose command.

- [ ] **Step 2: Verify image builds**

Run: `docker build -f Dockerfile.worker-stub -t acheron-worker-stub .`
Expected: Successfully built.

- [ ] **Step 3: Commit**

```bash
git add Dockerfile.worker-stub
git commit -m "feat: add worker stub Dockerfile"
```

---

### Task 5: Docker Compose

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 1: Create docker-compose.yml**

Create `docker-compose.yml`:

```yaml
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      retries: 3

  orchestrator:
    build:
      context: .
      dockerfile: Dockerfile.orchestrator
    ports:
      - "8000:8000"
    environment:
      REDIS_URL: redis://redis:6379
      ACHERON_REGISTRATION_TOKEN: ${ACHERON_REGISTRATION_TOKEN:-dev-registration-token}
    depends_on:
      redis:
        condition: service_healthy

  dashboard:
    build:
      context: .
      dockerfile: dashboard/Dockerfile
    ports:
      - "8080:8080"
    environment:
      ACHERON_URL: http://orchestrator:8000
    depends_on:
      - orchestrator

  tts-stub:
    build:
      context: .
      dockerfile: Dockerfile.worker-stub
    ports:
      - "8001:8001"
    environment:
      WORKER_TYPE: TTS
      WORKER_ENDPOINT: http://tts-stub:8001
      ORCHESTRATOR_URL: http://orchestrator:8000
      WORKER_PORT: "8001"
      ACHERON_REGISTRATION_TOKEN: ${ACHERON_REGISTRATION_TOKEN:-dev-registration-token}
    depends_on:
      - orchestrator

  asr-stub:
    build:
      context: .
      dockerfile: Dockerfile.worker-stub
    ports:
      - "8002:8002"
    environment:
      WORKER_TYPE: ASR
      WORKER_ENDPOINT: http://asr-stub:8002
      ORCHESTRATOR_URL: http://orchestrator:8000
      WORKER_PORT: "8002"
      ACHERON_REGISTRATION_TOKEN: ${ACHERON_REGISTRATION_TOKEN:-dev-registration-token}
    depends_on:
      - orchestrator
```

- [ ] **Step 2: Verify compose config is valid**

Run: `docker compose config`
Expected: Valid YAML output with all 5 services.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add Docker Compose with orchestrator, dashboard, and stub workers"
```

---

### Task 6: End-to-End Verification

- [ ] **Step 1: Start the stack**

Run: `docker compose up --build -d`
Expected: All 5 services start.

- [ ] **Step 2: Verify orchestrator is healthy**

Run: `curl http://localhost:8000/health`
Expected: `{"status":"ok"}`

- [ ] **Step 3: Verify stubs registered**

Run: `curl http://localhost:8000/workers`
Expected: Two workers (tts-stub, asr-stub) listed.

- [ ] **Step 4: Verify dashboard loads**

Run: `curl http://localhost:8080/`
Expected: HTML response with jobs/workers/cost sections.

- [ ] **Step 5: Stop the stack**

Run: `docker compose down`
Expected: All services stopped.

- [ ] **Step 6: Commit any fixes if needed**

If end-to-end verification revealed issues, fix and commit.
