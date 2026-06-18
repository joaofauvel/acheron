# gRPC Streaming Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [`) syntax for tracking.

**Goal:** Add GrpcWorker transport with server-side gRPC streaming for TTS, plus a stub gRPC worker for local dev.

**Architecture:** GrpcWorker implements `StreamingWorker` (same interface as HttpWorker). Uses server-side streaming for TTS — sends text, receives PCM chunks. Stub gRPC worker for local dev returns canned silent PCM. Proto compiled via `grpcio-tools`.

**Tech Stack:** grpcio 1.81, grpcio-tools 1.81, grpcio-health-checking 1.81, protobuf

---

## File Structure

```
proto/acheron/synthesis.proto              — Proto definition
src/acheron/proto/                         — Generated code (gitignored)
src/acheron/proto/__init__.py              — Package marker
src/acheron/shell/transports/grpc.py       — GrpcWorker
tests/shell/test_grpc_worker.py            — GrpcWorker tests
stubs/grpc_worker_stub.py                  — Stub gRPC server
stubs/tests/test_grpc_worker_stub.py       — Stub tests
Justfile                                    — Add proto compile command
docker-compose.yml                          — Add tts-grpc-stub service
```

---

### Task 1: Proto Definition and Code Generation

**Files:**
- Create: `proto/acheron/synthesis.proto`
- Create: `src/acheron/proto/__init__.py`
- Modify: `Justfile`
- Modify: `pyproject.toml` (ruff per-file-ignores, basedpyright exclude)

- [ ] **Step 1: Create the proto file**

Create `proto/acheron/synthesis.proto`:

```protobuf
syntax = "proto3";

package acheron;

service Synthesis {
  rpc Synthesize(SynthesisRequest) returns (stream AudioChunk);
}

message SynthesisRequest {
  string job_id = 1;
  string text = 2;
  string language = 3;
  string model = 4;
}

message AudioChunk {
  bytes pcm_data = 1;
  int32 sample_rate = 2;
  int32 channels = 3;
}
```

- [ ] **Step 2: Create the generated code package**

Create `src/acheron/proto/__init__.py` (empty file).

- [ ] **Step 3: Add proto compile command to Justfile**

Add to `Justfile` before the `validate` recipe:

```just
# Compile protobuf definitions
proto:
    uv run python -m grpc_tools.protoc \
        -I proto \
        --python_out=src/acheron/proto \
        --grpc_python_out=src/acheron/proto \
        proto/acheron/synthesis.proto
```

- [ ] **Step 4: Compile the proto**

Run: `just proto`
Expected: `src/acheron/proto/synthesis_pb2.py` and `src/acheron/proto/synthesis_pb2_grpc.py` are created.

- [ ] **Step 5: Add generated files to gitignore**

Add to `.gitignore`:

```
src/acheron/proto/synthesis_pb2.py
src/acheron/proto/synthesis_pb2_grpc.py
```

- [ ] **Step 6: Update ruff per-file-ignores**

In `pyproject.toml`, add to `[tool.ruff.lint.per-file-ignores]`:

```
"src/acheron/proto/**" = ["I", "E", "F", "W", "D", "ANN", "N"]
```

- [ ] **Step 7: Verify lint passes**

Run: `just lint-strict`
Expected: Clean.

- [ ] **Step 8: Commit**

```bash
git add proto/ src/acheron/proto/__init__.py Justfile pyproject.toml .gitignore
git commit -m "feat: add TTS synthesis proto definition and codegen"
```

---

### Task 2: GrpcWorker Implementation

**Files:**
- Create: `src/acheron/shell/transports/grpc.py`
- Modify: `src/acheron/shell/transports/__init__.py`

- [ ] **Step 1: Write failing tests for GrpcWorker**

Create `tests/shell/test_grpc_worker.py`:

```python
"""Tests for the GrpcWorker transport."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import grpc
import pytest
import pytest_asyncio

from acheron.core.errors import WorkerError, WorkerUnavailableError
from acheron.core.models import (
    BatchJob,
    Job,
    JobStatus,
    WorkerType,
)
from acheron.proto import synthesis_pb2, synthesis_pb2_grpc
from acheron.shell.transports.grpc import GrpcWorker

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class _FakeSynthesisServicer(synthesis_pb2_grpc.SynthesisServicer):
    """In-process gRPC servicer that returns canned PCM chunks."""

    def __init__(self, chunks: list[bytes] | None = None, fail: bool = False) -> None:
        self._chunks = chunks or [b"\x00\x00" * 100]
        self._fail = fail

    def Synthesize(self, request, context):  # noqa: ANN001, ANN201, N802
        if self._fail:
            context.abort(grpc.StatusCode.UNAVAILABLE, "GPU down")
            return
        for chunk in self._chunks:
            yield synthesis_pb2.AudioChunk(
                pcm_data=chunk,
                sample_rate=22050,
                channels=1,
            )


@pytest_asyncio.fixture
async def grpc_server() -> AsyncIterator[tuple[str, _FakeSynthesisServicer]]:
    """Start an in-process gRPC server."""
    servicer = _FakeSynthesisServicer()
    server = grpc.aio.server()
    synthesis_pb2_grpc.add_SynthesisServicer_to_server(servicer, server)
    port = server.add_insecure_port("localhost:0")
    await server.start()
    yield f"localhost:{port}", servicer
    await server.stop(0)


@pytest_asyncio.fixture
async def grpc_worker(grpc_server: tuple[str, _FakeSynthesisServicer]) -> AsyncIterator[GrpcWorker]:
    """Create a GrpcWorker connected to the in-process server."""
    addr, _ = grpc_server
    channel = grpc.aio.insecure_channel(addr)
    worker = GrpcWorker(channel)
    yield worker
    await channel.close()


class TestGrpcWorkerHealth:
    @pytest.mark.asyncio
    async def test_health_returns_true(self, grpc_worker: GrpcWorker) -> None:
        result = await grpc_worker.health()
        assert result is True

    @pytest.mark.asyncio
    async def test_health_returns_false_on_unreachable(self) -> None:
        channel = grpc.aio.insecure_channel("localhost:1")
        worker = GrpcWorker(channel)
        result = await worker.health()
        assert result is False
        await channel.close()


class TestGrpcWorkerCapabilities:
    @pytest.mark.asyncio
    async def test_capabilities_returns_tts(self, grpc_worker: GrpcWorker) -> None:
        caps = await grpc_worker.capabilities()
        assert caps.worker_type == WorkerType.TTS
        assert caps.batch_capable is True


class TestGrpcWorkerExecute:
    @pytest.mark.asyncio
    async def test_execute_assembles_pcm_chunks(self, grpc_server: tuple[str, _FakeSynthesisServicer]) -> None:
        addr, servicer = grpc_server
        servicer._chunks = [b"\x01\x02", b"\x03\x04"]
        channel = grpc.aio.insecure_channel(addr)
        worker = GrpcWorker(channel)
        job = Job(job_id="j-1", job_type=WorkerType.TTS, payload={"text": "hola", "language": "es"}, chapter_id="ch1")
        result = await worker.execute(job)
        assert result.status == JobStatus.SUCCESS
        assert result.job_id == "j-1"
        # PCM chunks assembled
        assert len(result.outputs) > 0
        await channel.close()

    @pytest.mark.asyncio
    async def test_execute_raises_on_non_tts_job(self, grpc_worker: GrpcWorker) -> None:
        job = Job(job_id="j-1", job_type=WorkerType.ASR, payload={}, chapter_id="ch1")
        with pytest.raises(WorkerError, match="TTS"):
            await grpc_worker.execute(job)

    @pytest.mark.asyncio
    async def test_execute_raises_on_server_error(self, grpc_server: tuple[str, _FakeSynthesisServicer]) -> None:
        addr, servicer = grpc_server
        servicer._fail = True
        channel = grpc.aio.insecure_channel(addr)
        worker = GrpcWorker(channel)
        job = Job(job_id="j-1", job_type=WorkerType.TTS, payload={"text": "hola"}, chapter_id="ch1")
        with pytest.raises(WorkerError):
            await worker.execute(job)
        await channel.close()


class TestGrpcWorkerBatch:
    @pytest.mark.asyncio
    async def test_submit_batch_returns_handle(self, grpc_worker: GrpcWorker) -> None:
        batch = BatchJob(
            batch_id="b-1",
            jobs=(
                Job(job_id="j-1", job_type=WorkerType.TTS, payload={"text": "hola"}, chapter_id="ch1"),
                Job(job_id="j-2", job_type=WorkerType.TTS, payload={"text": "adios"}, chapter_id="ch1"),
            ),
        )
        handle = await grpc_worker.submit_batch(batch)
        assert handle == "b-1"

    @pytest.mark.asyncio
    async def test_collect_results_returns_all(self, grpc_worker: GrpcWorker) -> None:
        batch = BatchJob(
            batch_id="b-1",
            jobs=(
                Job(job_id="j-1", job_type=WorkerType.TTS, payload={"text": "hola"}, chapter_id="ch1"),
                Job(job_id="j-2", job_type=WorkerType.TTS, payload={"text": "adios"}, chapter_id="ch1"),
            ),
        )
        await grpc_worker.submit_batch(batch)
        results = await grpc_worker.collect_results("b-1")
        assert len(results) == 2
        assert all(r.status == JobStatus.SUCCESS for r in results)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/shell/test_grpc_worker.py -v`
Expected: FAIL (module `acheron.shell.transports.grpc` not found).

- [ ] **Step 3: Implement GrpcWorker**

Create `src/acheron/shell/transports/grpc.py`:

```python
"""gRPC transport for remote TTS workers with server-side streaming."""

from __future__ import annotations

import logging
import time
from typing import Any

import grpc
import grpc.aio

from acheron.core.errors import WorkerError, WorkerUnavailableError
from acheron.core.interfaces import StreamingWorker
from acheron.core.models import (
    BatchJob,
    BatchStatus,
    Job,
    JobResult,
    JobStatus,
    JobMetrics,
    OutputFile,
    WorkerCapabilities,
    WorkerType,
)
from acheron.proto import synthesis_pb2, synthesis_pb2_grpc

logger = logging.getLogger(__name__)


class GrpcWorker(StreamingWorker):
    """Worker that delegates TTS execution to a remote gRPC endpoint."""

    def __init__(self, channel: grpc.aio.Channel) -> None:
        self._channel = channel
        self._stub = synthesis_pb2_grpc.SynthesisStub(channel)
        self._batches: dict[str, tuple[JobResult, ...]] = {}

    async def capabilities(self) -> WorkerCapabilities:  # noqa: D102
        return WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset({"en", "es", "fr", "de"}),
            supported_languages_out=frozenset({"en", "es", "fr", "de"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav", "pcm"}),
            max_payload_bytes=None,
            batch_capable=True,
            model_source=None,
        )

    async def execute(self, job: Job) -> JobResult:  # noqa: D102
        if job.job_type != WorkerType.TTS:
            msg = f"GrpcWorker only supports TTS, got {job.job_type}"
            raise WorkerError(msg)

        request = synthesis_pb2.SynthesisRequest(
            job_id=job.job_id,
            text=job.payload.get("text", ""),
            language=job.payload.get("language", ""),
            model=job.payload.get("model", ""),
        )

        pcm_chunks: list[bytes] = []
        sample_rate = 22050
        channels = 1
        start_time = time.monotonic()

        try:
            async for chunk in self._stub.Synthesize(request):
                pcm_chunks.append(chunk.pcm_data)
                if chunk.sample_rate:
                    sample_rate = chunk.sample_rate
                if chunk.channels:
                    channels = chunk.channels
        except grpc.aio.AioRpcError as exc:
            if exc.code() == grpc.StatusCode.UNAVAILABLE:
                msg = f"Worker unavailable: {exc.details()}"
                raise WorkerUnavailableError(msg) from exc
            msg = f"gRPC error {exc.code()}: {exc.details()}"
            raise WorkerError(msg) from exc

        duration = time.monotonic() - start_time
        audio_data = b"".join(pcm_chunks)

        return JobResult(
            job_id=job.job_id,
            status=JobStatus.SUCCESS,
            outputs=(
                OutputFile(
                    path=f"{job.job_id}.pcm",
                    filename=f"{job.job_id}.pcm",
                    size_bytes=len(audio_data),
                    checksum="",
                    content_type="audio/pcm",
                ),
            ),
            metrics=JobMetrics(duration_seconds=duration),
        )

    async def health(self) -> bool:  # noqa: D102
        try:
            from grpc.health.v1 import health_pb2, health_pb2_grpc

            stub = health_pb2_grpc.HealthStub(self._channel)
            await stub.Check(health_pb2.HealthCheckRequest())
        except (grpc.aio.AioRpcError, Exception):
            return False
        else:
            return True

    async def submit_batch(self, batch: BatchJob) -> str:  # noqa: D102
        results: list[JobResult] = []
        for job in batch.jobs:
            result = await self.execute(job)
            results.append(result)
        self._batches[batch.batch_id] = tuple(results)
        return batch.batch_id

    async def poll_batch(self, batch_handle: str) -> BatchStatus:  # noqa: D102
        results = self._batches.get(batch_handle, ())
        total = len(results)
        completed = sum(1 for r in results if r.status == JobStatus.SUCCESS)
        failed = sum(1 for r in results if r.status == JobStatus.FAILED)
        return BatchStatus(
            batch_id=batch_handle,
            total=total,
            completed=completed,
            failed=failed,
            pending=0,
            results=results,
        )

    async def collect_results(self, batch_handle: str) -> tuple[JobResult, ...]:  # noqa: D102
        status = await self.poll_batch(batch_handle)
        return status.results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/shell/test_grpc_worker.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `just validate`
Expected: All tests pass, lint clean, type check clean.

- [ ] **Step 6: Commit**

```bash
git add src/acheron/shell/transports/grpc.py tests/shell/test_grpc_worker.py
git commit -m "feat: add GrpcWorker with server-side TTS streaming"
```

---

### Task 3: Stub gRPC TTS Worker

**Files:**
- Create: `stubs/grpc_worker_stub.py`
- Create: `stubs/tests/test_grpc_worker_stub.py`

- [ ] **Step 1: Write failing tests**

Create `stubs/tests/test_grpc_worker_stub.py`:

```python
"""Tests for the stub gRPC TTS worker."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import grpc
import grpc.aio
import pytest
import pytest_asyncio

from acheron.proto import synthesis_pb2, synthesis_pb2_grpc
from stubs.grpc_worker_stub import create_server


@pytest_asyncio.fixture
async def grpc_stub_server() -> AsyncIterator[tuple[str, None]]:
    """Start the stub gRPC server."""
    server, port = await create_server(port=0)
    await server.start()
    yield f"localhost:{port}", None
    await server.stop(0)


@pytest.mark.asyncio
async def test_synthesize_returns_pcm_chunks(grpc_stub_server: tuple[str, None]) -> None:
    addr, _ = grpc_stub_server
    async with grpc.aio.insecure_channel(addr) as channel:
        stub = synthesis_pb2_grpc.SynthesisStub(channel)
        chunks = []
        async for chunk in stub.Synthesize(
            synthesis_pb2.SynthesisRequest(job_id="test-1", text="hello", language="en")
        ):
            chunks.append(chunk)
    assert len(chunks) > 0
    assert all(c.pcm_data for c in chunks)
    assert all(c.sample_rate > 0 for c in chunks)


@pytest.mark.asyncio
async def test_synthesize_returns_silence(grpc_stub_server: tuple[str, None]) -> None:
    addr, _ = grpc_stub_server
    async with grpc.aio.insecure_channel(addr) as channel:
        stub = synthesis_pb2_grpc.SynthesisStub(channel)
        chunks = []
        async for chunk in stub.Synthesize(
            synthesis_pb2.SynthesisRequest(job_id="test-1", text="hello")
        ):
            chunks.append(chunk)
    # All PCM data should be zeros (silence)
    for chunk in chunks:
        assert all(b == 0 for b in chunk.pcm_data)


@pytest.mark.asyncio
async def test_self_registers_on_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the stub registers with the orchestrator on startup."""
    monkeypatch.setenv("WORKER_TYPE", "TTS")
    monkeypatch.setenv("WORKER_ENDPOINT", "http://tts-grpc-stub:9001")
    monkeypatch.setenv("ORCHESTRATOR_URL", "http://orchestrator:8000")
    monkeypatch.setenv("WORKER_PORT", "9001")
    monkeypatch.setenv("ACHERON_REGISTRATION_TOKEN", "dev-registration-token")

    with patch("stubs.grpc_worker_stub.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status_code = 201
        mock_response.raise_for_status = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        server, port = await create_server(port=0)
        await server.start()
        # Registration happens during create_server, so check it was called
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "/workers" in call_args[0][0]
        body = call_args[1]["json"]
        assert body["transport"] == "grpc"
        await server.stop(0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest stubs/tests/test_grpc_worker_stub.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement stub gRPC TTS worker**

Create `stubs/grpc_worker_stub.py`:

```python
"""Stub gRPC TTS worker for local development — streams canned PCM chunks."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

import grpc
import grpc.aio
import httpx

from acheron.proto import synthesis_pb2, synthesis_pb2_grpc

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

_SILENT_PCM = b"\x00\x00" * 2205  # 100ms of silence at 22050 Hz, 16-bit mono


class _SynthesisServicer(synthesis_pb2_grpc.SynthesisServicer):
    """Returns canned silent PCM chunks."""

    def Synthesize(  # noqa: N802
        self,
        request: synthesis_pb2.SynthesisRequest,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[synthesis_pb2.AudioChunk]:
        # Yield 3 chunks of silence (~33ms each)
        for _ in range(3):
            yield synthesis_pb2.AudioChunk(
                pcm_data=_SILENT_PCM,
                sample_rate=22050,
                channels=1,
            )


async def _register(endpoint: str, token: str) -> None:
    """Register with orchestrator, retrying until success."""
    orchestrator_url = os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8000")
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "worker_id": "tts-grpc-stub",
        "endpoint": endpoint,
        "transport": "grpc",
        "capabilities": {
            "worker_type": "tts",
            "supported_languages_in": ["en", "es", "fr", "de"],
            "supported_languages_out": ["en", "es", "fr", "de"],
            "metadata": {"stub": True, "transport": "grpc"},
        },
    }

    async with httpx.AsyncClient() as client:
        while True:
            try:
                health_resp = await client.get(f"{orchestrator_url}/health")
                health_resp.raise_for_status()
                resp = await client.post(
                    f"{orchestrator_url}/workers",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
            except (httpx.HTTPError, OSError) as exc:
                logger.debug("Orchestrator not ready (%s), retrying...", exc)
                await asyncio.sleep(1)
            else:
                logger.info("Registered tts-grpc-stub with orchestrator")
                return


async def create_server(port: int = 9001) -> tuple[grpc.aio.Server, int]:
    """Create and optionally start the stub gRPC server."""
    server = grpc.aio.server()
    synthesis_pb2_grpc.add_SynthesisServicer_to_server(_SynthesisServicer(), server)
    actual_port = server.add_insecure_port(f"0.0.0.0:{port}")

    endpoint = os.environ.get("WORKER_ENDPOINT", f"http://localhost:{actual_port}")
    token = os.environ.get("ACHERON_REGISTRATION_TOKEN", "")
    await _register(endpoint, token)

    return server, actual_port


async def _serve() -> None:
    """Run the stub gRPC server."""
    port = int(os.environ.get("WORKER_PORT", "9001"))
    server, actual_port = await create_server(port)
    await server.start()
    logger.info("gRPC stub worker listening on port %d", actual_port)
    await server.wait_for_termination()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_serve())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest stubs/tests/test_grpc_worker_stub.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `just validate`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add stubs/grpc_worker_stub.py stubs/tests/test_grpc_worker_stub.py
git commit -m "feat: add stub gRPC TTS worker for local development"
```

---

### Task 4: Docker Compose Update

**Files:**
- Modify: `docker-compose.yml`
- Create: `Dockerfile.grpc-stub`

- [ ] **Step 1: Create gRPC stub Dockerfile**

Create `Dockerfile.grpc-stub`:

```dockerfile
FROM python:3.14-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --no-dev --no-install-project

COPY src/acheron/ ./src/acheron/
COPY stubs/ ./stubs/
COPY proto/ ./proto/

ENV PYTHONPATH=/app/src:/app

CMD ["python", "-m", "stubs.grpc_worker_stub"]
```

- [ ] **Step 2: Add tts-grpc-stub to docker-compose.yml**

Add to `docker-compose.yml` after `tts-stub`:

```yaml
  tts-grpc-stub:
    build:
      context: .
      dockerfile: Dockerfile.grpc-stub
    ports:
      - "9001:9001"
    environment:
      WORKER_TYPE: TTS
      WORKER_ENDPOINT: http://tts-grpc-stub:9001
      ORCHESTRATOR_URL: http://orchestrator:8000
      WORKER_PORT: "9001"
      ACHERON_REGISTRATION_TOKEN: ${ACHERON_REGISTRATION_TOKEN:-dev-registration-token}
    depends_on:
      - orchestrator
```

- [ ] **Step 3: Verify compose config is valid**

Run: `docker compose config`
Expected: All 6 services listed.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml Dockerfile.grpc-stub
git commit -m "feat: add gRPC stub worker to Docker Compose"
```

---

### Task 5: Final Verification

- [ ] **Step 1: Run full validation**

Run: `just validate`
Expected: All tests pass, lint clean, type check clean, coverage > 95%.

- [ ] **Step 2: Verify proto compiles cleanly**

Run: `just proto`
Expected: No errors.

- [ ] **Step 3: Verify gRPC stub starts standalone**

Run: `uv run python -m stubs.grpc_worker_stub &` (background, then kill)
Expected: Server starts without import errors.
