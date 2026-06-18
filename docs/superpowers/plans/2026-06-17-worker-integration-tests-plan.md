# Worker Integration Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [`) syntax for tracking.

**Goal:** Add orchestrator→worker integration tests with real HTTP and gRPC stub workers, plus a transport-aware worker factory.

**Architecture:** Real stub workers run as background tasks in pytest fixtures. The orchestrator's worker factory is updated to dispatch to HttpWorker or GrpcWorker based on registered transport. Tests submit real jobs through the orchestrator and verify results.

**Tech Stack:** pytest-asyncio, httpx, grpc.aio, FastAPI, existing stub workers

---

## File Structure

```
src/acheron/shell/step_handler.py              — updated factory (transport-aware)
stubs/translation_stub.py                       — translation stub app
stubs/tests/test_translation_stub.py            — translation stub unit tests
tests/integration/test_worker_integration.py    — integration tests
tests/integration/conftest.py                   — add worker fixtures
```

---

### Task 1: Transport-Aware Worker Factory

**Files:**
- Modify: `src/acheron/shell/step_handler.py`

- [ ] **Step 1: Update `_default_worker_factory` to handle gRPC and local transports**

In `src/acheron/shell/step_handler.py`, replace the existing factory:

```python
def _default_worker_factory(registered: RegisteredWorker) -> Worker:
    """Create a worker from a registered worker's endpoint and transport."""
    match registered.transport:
        case "grpc":
            import grpc.aio
            from acheron.shell.transports.grpc import GrpcWorker
            channel = grpc.aio.insecure_channel(registered.endpoint)
            return GrpcWorker(channel)
        case "local":
            handler = registered.metadata.get("handler")
            if handler is None:
                msg = f"Local worker {registered.worker_id} missing handler in metadata"
                raise WorkerError(msg)
            from acheron.shell.transports.local import LocalWorker
            return LocalWorker(
                worker_type=registered.capabilities.worker_type,
                handler=handler,
                supported_languages_in=registered.capabilities.supported_languages_in,
                supported_languages_out=registered.capabilities.supported_languages_out,
            )
        case _:
            return HttpWorker(registered.endpoint)
```

This requires `RegisteredWorker` to have a `metadata` dict. Add it to `registry.py`.

- [ ] **Step 2: Add `metadata` field to `RegisteredWorker`**

In `src/acheron/shell/registry.py`, add to the `RegisteredWorker` dataclass:

```python
@dataclass
class RegisteredWorker:
    worker_id: str
    endpoint: str
    transport: str
    capabilities: WorkerCapabilities
    consecutive_failures: int = 0
    last_health_check: float | None = None
    metadata: dict[str, object] = field(default_factory=dict)
```

Update `register` to accept and store metadata:

```python
def register(
    self,
    worker_id: str,
    endpoint: str,
    transport: str,
    capabilities: WorkerCapabilities,
    metadata: dict[str, object] | None = None,
) -> None:
    self._workers[worker_id] = RegisteredWorker(
        worker_id=worker_id,
        endpoint=endpoint,
        transport=transport,
        capabilities=capabilities,
        consecutive_failures=0,
        last_health_check=time.time(),
        metadata=metadata or {},
    )
```

- [ ] **Step 3: Update orchestrator's `register_worker` to pass metadata**

In `src/acheron/shell/orchestrator.py`:

```python
def register_worker(
    self,
    worker_id: str,
    endpoint: str,
    transport: str,
    capabilities: WorkerCapabilities,
    metadata: dict[str, object] | None = None,
) -> None:
    self._registry.register(worker_id, endpoint, transport, capabilities, metadata=metadata)
```

- [ ] **Step 4: Run existing tests to verify no regressions**

Run: `just validate`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/acheron/shell/step_handler.py src/acheron/shell/registry.py src/acheron/shell/orchestrator.py
git commit -m "feat: transport-aware worker factory with metadata support"
```

---

### Task 2: Translation Stub

**Files:**
- Create: `stubs/translation_stub.py`
- Create: `stubs/tests/test_translation_stub.py`

- [ ] **Step 1: Write failing tests**

Create `stubs/tests/test_translation_stub.py`:

```python
"""Tests for the translation stub."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from stubs.translation_stub import create_app


@pytest.fixture
def translation_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_TYPE", "TRANSLATION")
    monkeypatch.setenv("WORKER_ENDPOINT", "http://translation-stub:8003")
    monkeypatch.setenv("ORCHESTRATOR_URL", "http://orchestrator:8000")
    monkeypatch.setenv("WORKER_PORT", "8003")
    monkeypatch.setenv("ACHERON_REGISTRATION_TOKEN", "dev-registration-token")


@pytest.mark.asyncio
async def test_health_returns_200(translation_env: None) -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_submit_returns_translated_text(translation_env: None) -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/submit",
            json={"job_id": "t-1", "payload": {"text": "hello", "source_language": "en", "target_language": "es"}},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert "translated" in data["output_data"].lower()


@pytest.mark.asyncio
async def test_self_registers_on_startup(translation_env: None) -> None:
    with patch("stubs.translation_stub.httpx.AsyncClient") as mock_client_cls:
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
        async with app.router.lifespan_context(app):
            pass

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        body = call_args[1]["json"]
        assert body["capabilities"]["worker_type"] == "translation"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest stubs/tests/test_translation_stub.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement translation stub**

Create `stubs/translation_stub.py`:

```python
"""Stub translation worker — returns mock translated text."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import FastAPI

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


def _get_config() -> dict[str, str]:
    return {
        "worker_type": os.environ.get("WORKER_TYPE", "TRANSLATION"),
        "worker_endpoint": os.environ.get("WORKER_ENDPOINT", "http://localhost:8003"),
        "orchestrator_url": os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8000"),
        "registration_token": os.environ.get("ACHERON_REGISTRATION_TOKEN", ""),
    }


async def _register(cfg: dict[str, str]) -> None:
    worker_type = cfg["worker_type"].lower()
    worker_id = f"{worker_type}-stub"
    headers: dict[str, str] = {}
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
            except (httpx.HTTPError, OSError) as exc:
                logger.debug("Orchestrator not ready (%s), retrying...", exc)
                await asyncio.sleep(1)
            else:
                logger.info("Registered %s with orchestrator", worker_id)
                return


def create_app() -> FastAPI:
    cfg = _get_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await _register(cfg)
        yield

    app = FastAPI(title="Translation Stub Worker", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/submit")
    async def submit(body: dict[str, Any]) -> dict[str, Any]:
        text = body.get("payload", {}).get("text", "")
        src = body.get("payload", {}).get("source_language", "en")
        dst = body.get("payload", {}).get("target_language", "es")
        translated = f"{text} [translated {src}→{dst}]"
        return {"status": "completed", "output_data": translated}

    return app
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest stubs/tests/test_translation_stub.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add stubs/translation_stub.py stubs/tests/test_translation_stub.py
git commit -m "feat: add translation stub worker"
```

---

### Task 3: Integration Test Fixtures

**Files:**
- Modify: `tests/integration/conftest.py`

- [ ] **Step 1: Add stub server fixtures**

Add to `tests/integration/conftest.py`:

```python
import asyncio
from typing import TYPE_CHECKING

import grpc.aio

from acheron.shell.orchestrator import Orchestrator
from acheron.shell.step_handler import create_step_handler

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


async def _start_http_stub(app_factory, port: int) -> tuple[str, asyncio.Task[None]]:
    """Start a FastAPI stub as a background task."""
    import uvicorn
    app = app_factory()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.5)  # let server start
    return f"http://127.0.0.1:{port}", task


@pytest_asyncio.fixture
async def http_tts_stub() -> AsyncIterator[str]:
    from stubs.worker_stub import create_app as tts_app
    url, task = await _start_http_stub(tts_app, 18001)
    yield url
    task.cancel()


@pytest_asyncio.fixture
async def http_asr_stub() -> AsyncIterator[str]:
    import os
    os.environ.setdefault("WORKER_TYPE", "ASR")
    os.environ.setdefault("WORKER_ENDPOINT", "http://127.0.0.1:18002")
    os.environ.setdefault("ORCHESTRATOR_URL", "http://127.0.0.1:1")
    os.environ.setdefault("WORKER_PORT", "18002")
    os.environ.setdefault("ACHERON_REGISTRATION_TOKEN", "")
    from stubs.worker_stub import create_app as asr_app
    url, task = await _start_http_stub(asr_app, 18002)
    yield url
    task.cancel()


@pytest_asyncio.fixture
async def http_translation_stub() -> AsyncIterator[str]:
    import os
    os.environ.setdefault("WORKER_TYPE", "TRANSLATION")
    os.environ.setdefault("WORKER_ENDPOINT", "http://127.0.0.1:18003")
    os.environ.setdefault("ORCHESTRATOR_URL", "http://127.0.0.1:1")
    os.environ.setdefault("WORKER_PORT", "18003")
    os.environ.setdefault("ACHERON_REGISTRATION_TOKEN", "")
    from stubs.translation_stub import create_app as trans_app
    url, task = await _start_http_stub(trans_app, 18003)
    yield url
    task.cancel()


@pytest_asyncio.fixture
async def grpc_tts_stub() -> AsyncIterator[str]:
    from stubs.grpc_worker_stub import create_server
    server, port = await create_server(port=0, register=False)
    await server.start()
    yield f"localhost:{port}"
    await server.stop(0)
```

- [ ] **Step 2: Add orchestrator fixture with real workers**

```python
@pytest_asyncio.fixture
async def wired_orchestrator(
    tmp_path: Path,
    http_tts_stub: str,
    http_translation_stub: str,
    grpc_tts_stub: str,
) -> AsyncIterator[Orchestrator]:
    """Orchestrator with real stub workers registered."""
    from acheron.core.models import WorkerCapabilities, WorkerType
    from acheron.shell.cache import PlanCache
    from acheron.shell.registry import WorkerRegistry

    reg = WorkerRegistry()
    cache = PlanCache(tmp_path)

    # HTTP TTS worker
    reg.register(
        "tts-http", http_tts_stub, "http",
        WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset({"en", "es", "fr", "de"}),
            supported_languages_out=frozenset({"en", "es", "fr", "de"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav", "pcm"}),
            max_payload_bytes=None, batch_capable=True, model_source=None,
        ),
    )

    # gRPC TTS worker
    reg.register(
        "tts-grpc", grpc_tts_stub, "grpc",
        WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset({"en", "es", "fr", "de"}),
            supported_languages_out=frozenset({"en", "es", "fr", "de"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav", "pcm"}),
            max_payload_bytes=None, batch_capable=True, model_source=None,
        ),
    )

    # Translation worker
    reg.register(
        "trans-http", http_translation_stub, "http",
        WorkerCapabilities(
            worker_type=WorkerType.TRANSLATION,
            supported_languages_in=frozenset({"en", "es", "fr", "de"}),
            supported_languages_out=frozenset({"en", "es", "fr", "de"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"text"}),
            max_payload_bytes=None, batch_capable=False, model_source=None,
        ),
    )

    handler = create_step_handler(reg)
    orch = Orchestrator(registry=reg, cache=cache, handler=handler)
    yield orch
```

- [ ] **Step 3: Run existing integration tests to verify no regressions**

Run: `uv run pytest tests/integration/ -v`
Expected: All existing tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/conftest.py
git commit -m "feat: add worker integration test fixtures"
```

---

### Task 4: Happy Path Integration Tests

**Files:**
- Create: `tests/integration/test_worker_integration.py`

- [ ] **Step 1: Write EPUB → TTS (HTTP) test**

Create `tests/integration/test_worker_integration.py`:

```python
"""Integration tests: orchestrator → real workers."""

from __future__ import annotations

import pytest

from acheron.core.models import AudioRequest, EpubRequest, ExecutorStrategy


class TestWorkerIntegrationHappyPath:
    @pytest.mark.asyncio
    async def test_epub_tts_http(self, wired_orchestrator) -> None:  # type: ignore[no-untyped-def]
        """EPUB request dispatches to HTTP TTS worker."""
        orch = wired_orchestrator
        request = EpubRequest(
            source_path="/tmp/test.epub",
            source_language="en",
            target_language="es",
        )
        tracked = await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)
        # Wait for execution to complete
        import asyncio
        for _ in range(50):
            await asyncio.sleep(0.1)
            if tracked.status != "running":
                break
        assert tracked.status in ("completed", "partial")
        assert tracked.result is not None
        assert tracked.result.completed_steps > 0

    @pytest.mark.asyncio
    async def test_epub_tts_grpc(self, wired_orchestrator) -> None:  # type: ignore[no-untyped-def]
        """EPUB request dispatches to gRPC TTS worker (first TTS match)."""
        orch = wired_orchestrator
        request = EpubRequest(
            source_path="/tmp/test.epub",
            source_language="en",
            target_language="es",
        )
        tracked = await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)
        import asyncio
        for _ in range(50):
            await asyncio.sleep(0.1)
            if tracked.status != "running":
                break
        assert tracked.status in ("completed", "partial")
```

- [ ] **Step 2: Run tests to verify they work**

Run: `uv run pytest tests/integration/test_worker_integration.py -v`
Expected: PASS (may need timeout tuning).

- [ ] **Step 3: Add audio request test**

```python
    @pytest.mark.asyncio
    async def test_audio_asr_tts(self, wired_orchestrator) -> None:  # type: ignore[no-untyped-def]
        """Audio request dispatches ASR then TTS."""
        orch = wired_orchestrator
        # Need ASR worker registered for this test
        from acheron.core.models import WorkerCapabilities, WorkerType
        orch.register_worker(
            "asr-http", "http://127.0.0.1:18002", "http",
            WorkerCapabilities(
                worker_type=WorkerType.ASR,
                supported_languages_in=frozenset({"en", "es", "fr", "de"}),
                supported_languages_out=frozenset({"en", "es", "fr", "de"}),
                supported_formats_in=frozenset({"mp3", "wav"}),
                supported_formats_out=frozenset({"text"}),
                max_payload_bytes=None, batch_capable=False, model_source=None,
            ),
        )
        request = AudioRequest(
            source_path="/tmp/test.mp3",
            source_language="en",
            target_language="es",
        )
        tracked = await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)
        import asyncio
        for _ in range(50):
            await asyncio.sleep(0.1)
            if tracked.status != "running":
                break
        assert tracked.status in ("completed", "partial")
```

- [ ] **Step 4: Run all integration tests**

Run: `uv run pytest tests/integration/test_worker_integration.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_worker_integration.py
git commit -m "feat: add happy path worker integration tests"
```

---

### Task 5: Error Path and Edge Case Tests

**Files:**
- Modify: `tests/integration/test_worker_integration.py`

- [ ] **Step 1: Add error path tests**

```python
class TestWorkerIntegrationErrors:
    @pytest.mark.asyncio
    async def test_no_matching_worker(self, wired_orchestrator) -> None:  # type: ignore[no-untyped-def]
        """Job for unsupported language pair fails at plan compilation."""
        from acheron.core.errors import InvalidLanguagePathError
        orch = wired_orchestrator
        request = EpubRequest(
            source_path="/tmp/test.epub",
            source_language="xx",  # unsupported
            target_language="yy",  # unsupported
        )
        with pytest.raises(InvalidLanguagePathError):
            await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)

    @pytest.mark.asyncio
    async def test_worker_unreachable(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Job fails when worker is unreachable."""
        from acheron.core.models import WorkerCapabilities, WorkerType
        from acheron.shell.cache import PlanCache
        from acheron.shell.registry import WorkerRegistry
        from acheron.shell.orchestrator import Orchestrator

        reg = WorkerRegistry()
        # Register workers at unreachable endpoints
        reg.register(
            "tts-http", "http://127.0.0.1:1", "http",
            WorkerCapabilities(
                worker_type=WorkerType.TTS,
                supported_languages_in=frozenset({"es"}),
                supported_languages_out=frozenset({"es"}),
                supported_formats_in=frozenset({"text"}),
                supported_formats_out=frozenset({"wav"}),
                max_payload_bytes=None, batch_capable=True, model_source=None,
            ),
        )
        reg.register(
            "trans-http", "http://127.0.0.1:1", "http",
            WorkerCapabilities(
                worker_type=WorkerType.TRANSLATION,
                supported_languages_in=frozenset({"en"}),
                supported_languages_out=frozenset({"es"}),
                supported_formats_in=frozenset({"text"}),
                supported_formats_out=frozenset({"text"}),
                max_payload_bytes=None, batch_capable=False, model_source=None,
            ),
        )

        from acheron.shell.step_handler import create_step_handler
        handler = create_step_handler(reg)
        orch = Orchestrator(registry=reg, cache=PlanCache(tmp_path), handler=handler)

        request = EpubRequest(
            source_path="/tmp/test.epub",
            source_language="en",
            target_language="es",
        )
        tracked = await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)
        import asyncio
        for _ in range(50):
            await asyncio.sleep(0.1)
            if tracked.status != "running":
                break
        assert tracked.status == "failed"
```

- [ ] **Step 2: Add edge case tests**

```python
class TestWorkerIntegrationEdgeCases:
    @pytest.mark.asyncio
    async def test_multiple_workers_same_type(self, wired_orchestrator) -> None:  # type: ignore[no-untyped-def]
        """First matching worker is used when multiple exist."""
        orch = wired_orchestrator
        # wired_orchestrator has tts-http and tts-grpc
        # The first registered (tts-http) should be used
        request = EpubRequest(
            source_path="/tmp/test.epub",
            source_language="en",
            target_language="es",
        )
        tracked = await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)
        import asyncio
        for _ in range(50):
            await asyncio.sleep(0.1)
            if tracked.status != "running":
                break
        assert tracked.status in ("completed", "partial")
        assert tracked.result is not None
```

- [ ] **Step 3: Run all integration tests**

Run: `uv run pytest tests/integration/test_worker_integration.py -v`
Expected: PASS.

- [ ] **Step 4: Run full validation**

Run: `just validate`
Expected: All tests pass, lint clean, type check clean.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_worker_integration.py
git commit -m "feat: add error path and edge case worker integration tests"
```
