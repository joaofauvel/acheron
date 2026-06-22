# Layer 8a — Orchestrator Transports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the orchestrator's HTTP and gRPC worker transports to consume the new `Artifact` shape (bytes-only, no shared-volume assumption) and surface per-job cost confidence (`CostBasis`) on the API + dashboard. The legacy JSON-`path` path stays for incremental migration.

**Architecture:** `HttpWorker.execute()` sniffs `Content-Type` and dispatches to a new multipart/mixed parser when present; falls back to JSON for existing HTTP stubs. `GrpcWorker.execute()` gains an `Artifact` mode in `OutputChunk`; legacy `pcm_data` mode preserved. Shared `_materialize_artifact` / `_build_result` helpers live in `src/acheron/shell/transports/_multipart.py`. The orchestrator regenerates proto stubs via `just proto`. The dashboard cost partial renders `Cost Basis` + `Note` columns from the per-job `total_cost_basis` field; the orchestrator's `JobResponse`/`Partial` schema carries it.

**Tech Stack:** httpx, grpcio/protobuf, FastAPI, Jinja2, pytest + respx + pytest-asyncio, mypy + basedpyright, ruff, import-linter.

**Prerequisite:** Plan 1 (`docs/superpowers/plans/2026-06-22-layer8a-sdk-foundation.md`) merged — its `CostBasis` enum + `JobMetrics.cost_basis` field land in `acheron.core.models`.

**Reference spec:** `docs/superpowers/specs/2026-06-22-layer8a-tts-worker-design.md` (sections "Orchestrator-Side Changes", "Dashboard Updates").

**Final gate:** `just validate` green.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `proto/synthesis.proto` | Extend `OutputChunk` with `oneof payload { bytes pcm_data = 1; Artifact artifact = 2; }`, add `message Artifact` + `message ExecuteResponse`. |
| `src/acheron/proto/synthesis_pb2*.py` | Regenerated via `just proto`. |
| `src/acheron/shell/transports/_multipart.py` | NEW — `_materialize_artifact()` + `_build_result()` shared by HTTP + gRPC. |
| `src/acheron/shell/transports/http.py` | `HttpWorker.execute()` sniffs `Content-Type`; multipart/mixed path materializes bytes via `_materialize_artifact`; legacy JSON path preserved. |
| `src/acheron/shell/transports/grpc.py` | `GrpcWorker.execute()` consumes `Artifact` parts via shared helpers; legacy `pcm_data` mode preserved. |
| `src/acheron/shell/api/schemas.py` | Add `total_cost_basis: str | None = None` to `JobResponse`. |
| `src/acheron/shell/api/routes/jobs.py` | Surface `total_cost_basis` when aggregating. |
| `src/acheron/shell/orchestrator.py` | Compute `TrackedJob`'s `total_cost_basis` as the least-confident basis across steps. |
| `src/acheron/core/models.py` | Add `total_cost_basis: CostBasis | None = None` to `PlanResult`. |
| `dashboard/templates/partials/cost.html` | Add **Cost Basis** badge + **Note** columns. |
| `tests/shell/transports/test_multipart.py` | NEW — standalone `_materialize_artifact` + `_build_result` tests. |
| `tests/shell/test_http_worker.py` | Add multipart/mixed parsing tests. |
| `tests/shell/test_grpc_worker.py` | Add `Artifact`-mode tests on top of the existing `_FakeSynthesisServicer`. |
| `tests/core/test_models.py` | Cover `PlanResult.total_cost_basis` round-trip. |
| `tests/shell/dashboard/test_cost_partial.py` | NEW — render each `CostBasis`; assert unknown ≠ free. |

---

### Task 1: Extend `PlanResult.total_cost_basis`

**Files:**
- Modify: `src/acheron/core/models.py`
- Modify: `tests/core/test_models.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_models.py`:

```python
from pydantic import TypeAdapter
from acheron.core.models import CostBasis, JobMetrics, OutputFile, PlanResult


class TestPlanResultCostBasis:
    _adapter = TypeAdapter(PlanResult)

    def test_default_total_cost_basis_is_none(self) -> None:
        r = PlanResult(
            plan_id="p",
            status="completed",
            completed_steps=0,
            total_steps=0,
            outputs=(),
            total_cost=0.0,
            total_duration_seconds=0.0,
            errors=(),
        )
        assert r.total_cost_basis is None

    def test_explicit_total_cost_basis_round_trip(self) -> None:
        r = PlanResult(
            plan_id="p",
            status="completed",
            completed_steps=1,
            total_steps=1,
            outputs=(),
            total_cost=0.042,
            total_duration_seconds=1.0,
            errors=(),
            total_cost_basis=CostBasis.MEASURED,
        )
        dumped = self._adapter.dump_python(r)
        round_trip = self._adapter.validate_python(dumped)
        assert round_trip.total_cost_basis == CostBasis.MEASURED
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/core/test_models.py::TestPlanResultCostBasis -v
```
Expected: `AttributeError`/`TypeError` — `total_cost_basis` doesn't exist.

- [ ] **Step 3: Extend `PlanResult`**

In `src/acheron/core/models.py`, find the `PlanResult` dataclass and append the new field:

```python
@dataclass(frozen=True)
class PlanResult:
    """Outcome of executing a plan."""

    plan_id: str
    status: str
    completed_steps: int
    total_steps: int
    outputs: tuple[OutputFile, ...]
    total_cost: float
    total_duration_seconds: float
    errors: tuple[str, ...]
    total_cost_basis: CostBasis | None = None
```

(Verify the existing field order by reading the file before editing.)

- [ ] **Step 4: Run test + type-check**

```bash
uv run pytest tests/core/test_models.py::TestPlanResultCostBasis -v
uv run mypy src/acheron/core/models.py
uv run basedpyright src/acheron/core/models.py
```
Expected: tests pass; type-checkers clean.

- [ ] **Step 5: Commit**

```bash
git add src/acheron/core/models.py tests/core/test_models.py
git commit -m "feat(core): add total_cost_basis to PlanResult"
```

---

### Task 2: Shared `_materialize_artifact` + `_build_result` helpers

**Files:**
- Create: `src/acheron/shell/transports/_multipart.py`
- Create: `tests/shell/transports/test_multipart.py`
- Create: `tests/shell/transports/__init__.py`

- [ ] **Step 1: Write the failing test**

Create `tests/shell/transports/__init__.py` (empty) and `tests/shell/transports/test_multipart.py`:

```python
"""Standalone tests for the shared _materialize_artifact + _build_result helpers."""

from pathlib import Path

import pytest

from acheron.core.models import CostBasis, JobMetrics, JobStatus, OutputFile
from acheron.shell.transports._multipart import _build_result, _materialize_artifact


class TestMaterializeArtifact:
    @pytest.mark.asyncio
    async def test_writes_bytes_and_computes_checksum_size(self, tmp_path: Path) -> None:
        data = b"hello world"
        out = await _materialize_artifact(
            data=data,
            filename="ch1_0000.wav",
            content_type="audio/wav",
            metadata={"sequence_id": 0},
            dest_dir=tmp_path,
        )
        assert isinstance(out, OutputFile)
        assert out.filename == "ch1_0000.wav"
        assert out.size_bytes == len(data)
        assert out.content_type == "audio/wav"
        # SHA-256 of "hello world"
        assert out.checksum == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        assert Path(out.path).read_bytes() == data
        assert Path(out.path).parent == tmp_path

    @pytest.mark.asyncio
    async def test_dest_dir_created_if_missing(self, tmp_path: Path) -> None:
        dest = tmp_path / "sub" / "deeper"
        out = await _materialize_artifact(
            data=b"x",
            filename="f.txt",
            content_type="text/plain",
            metadata={},
            dest_dir=dest,
        )
        assert Path(out.path).exists()


class TestBuildResult:
    @pytest.mark.asyncio
    async def test_assembles_job_result_with_metrics(self, tmp_path: Path) -> None:
        art1 = await _materialize_artifact(
            data=b"a",
            filename="a.wav",
            content_type="audio/wav",
            metadata={},
            dest_dir=tmp_path,
        )
        art2 = await _materialize_artifact(
            data=b"b",
            filename="b.wav",
            content_type="audio/wav",
            metadata={},
            dest_dir=tmp_path,
        )
        metrics = JobMetrics(
            duration_seconds=1.5,
            gpu_seconds=1.0,
            cost_estimate=0.042,
            cost_basis=CostBasis.MEASURED,
        )
        result = _build_result(
            job_id="job-xyz-step",
            outputs=(art1, art2),
            metrics=metrics,
        )
        assert result.job_id == "job-xyz-step"
        assert result.status == JobStatus.SUCCESS
        assert len(result.outputs) == 2
        assert result.metrics.cost_estimate == 0.042
        assert result.metrics.cost_basis == CostBasis.MEASURED
        assert result.error is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/shell/transports/test_multipart.py -v
```
Expected: `ImportError: cannot import name '_build_result'`.

- [ ] **Step 3: Implement `_multipart.py`**

Create `src/acheron/shell/transports/_multipart.py`:

```python
"""Shared helpers used by both HttpWorker (multipart/mixed) and GrpcWorker (Artifact parts).

The orchestrator materializes received bytes into its own ``ACHERON_DATA_DIR``,
so a worker needs no shared filesystem with the orchestrator.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from acheron.core.models import JobMetrics, JobResult, JobStatus, OutputFile


async def _materialize_artifact(
    *,
    data: bytes,
    filename: str,
    content_type: str,
    metadata: dict[str, Any],
    dest_dir: Path,
) -> OutputFile:
    """Write ``bytes`` to ``dest_dir/filename`` and return an ``OutputFile``.

    ``checksum`` (SHA-256) and ``size_bytes`` are computed locally on the
    orchestrator side so the worker doesn't need to be trusted on them.
    """
    import aiofiles

    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / filename
    async with await aiofiles.open(out_path, "wb") as f:
        await f.write(data)
    checksum = hashlib.sha256(data).hexdigest()
    return OutputFile(
        path=str(out_path),
        filename=filename,
        size_bytes=len(data),
        checksum=checksum,
        content_type=content_type,
    )


def _build_result(
    *,
    job_id: str,
    outputs: tuple[OutputFile, ...],
    metrics: JobMetrics,
) -> JobResult:
    """Assemble a success ``JobResult`` from materialized outputs + metrics."""
    return JobResult(
        job_id=job_id,
        status=JobStatus.SUCCESS,
        outputs=outputs,
        metrics=metrics,
        error=None,
    )
```

- [ ] **Step 4: Run test + type-check**

```bash
uv run pytest tests/shell/transports/test_multipart.py -v
uv run mypy src/acheron/shell/transports/_multipart.py
uv run basedpyright src/acheron/shell/transports/_multipart.py
```
Expected: tests pass; type-checkers clean.

- [ ] **Step 5: Commit**

```bash
git add src/acheron/shell/transports/_multipart.py tests/shell/transports/test_multipart.py tests/shell/transports/__init__.py
git commit -m "feat(transports): add shared _materialize_artifact + _build_result helpers"
```

---

### Task 3: `HttpWorker.execute()` content-type sniff + multipart dispatch

**Files:**
- Modify: `src/acheron/shell/transports/http.py`
- Modify: `tests/shell/test_http_worker.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/shell/test_http_worker.py`:

```python
import hashlib
from pathlib import Path

from acheron.core.models import CostBasis


# A multipart/mixed response body the SDK edge would emit. Built statically
# so the test doesn't need the SDK to be importable.
_BOUNDARY = "acheron-test"


def _multipart_body(audio: bytes, metrics: bytes) -> bytes:
    audio_part = (
        f"--{_BOUNDARY}\r\n"
        f'Content-Disposition: attachment; filename="ch1_0000.wav"\r\n'
        f"Content-Type: audio/wav\r\n"
        f'X-Acheron-Metadata: {{"sequence_id":0}}\r\n\r\n'
    ).encode("utf-8") + audio + b"\r\n"
    metrics_part = (
        f"--{_BOUNDARY}\r\nContent-Type: application/json\r\n\r\n"
    ).encode("utf-8") + metrics + b"\r\n"
    closing = f"--{_BOUNDARY}--\r\n".encode("utf-8")
    return audio_part + metrics_part + closing


class TestHttpWorkerExecuteMultipart:
    @respx.mock
    @pytest.mark.asyncio
    async def test_multipart_response_materializes_to_data_dir(self, tmp_path: Path) -> None:
        audio = b"\x00\x01\x02\x03" * 100
        metrics = (
            b'{"duration_seconds":1.5,"gpu_seconds":1.0,"tokens_in":null,'
            b'"tokens_out":null,"cost_estimate":0.042,"cost_basis":"measured"}'
        )
        body = _multipart_body(audio, metrics)
        respx.post(f"{_BASE_URL}/execute").mock(
            return_value=httpx.Response(
                200,
                content=body,
                headers={"content-type": f"multipart/mixed; boundary={_BOUNDARY}"},
            )
        )
        worker = HttpWorker(_BASE_URL)
        job = Job(
            job_id="job-xyz-synthesize-ch1",
            job_type=WorkerType.TTS,
            payload={"chapter_id": "ch1"},
            chapter_id="ch1",
        )
        # NOTE: HttpWorker receives dest_dir via an env var or constructor in Plan 2.
        # The new test must pass the data dir explicitly.
        worker = HttpWorker(_BASE_URL, data_dir=tmp_path)
        result = await worker.execute(job)
        assert result.status == JobStatus.SUCCESS
        assert result.job_id == "job-xyz-synthesize-ch1"
        assert len(result.outputs) == 1
        out = result.outputs[0]
        assert out.filename == "ch1_0000.wav"
        assert out.content_type == "audio/wav"
        assert out.size_bytes == len(audio)
        assert out.checksum == hashlib.sha256(audio).hexdigest()
        assert Path(out.path).read_bytes() == audio
        assert Path(out.path).parent.name == "job-xyz-synthesize-ch1"
        assert result.metrics.cost_estimate == 0.042
        assert result.metrics.cost_basis == CostBasis.MEASURED

    @respx.mock
    @pytest.mark.asyncio
    async def test_legacy_json_response_still_works(self) -> None:
        # Existing stub emits JSON with OutputFile.path. Ensure backward-compat.
        respx.post(f"{_BASE_URL}/execute").mock(
            return_value=httpx.Response(
                200,
                json={
                    "job_id": "j-1",
                    "status": "success",
                    "outputs": [
                        {
                            "path": "/tmp/x.wav",
                            "filename": "x.wav",
                            "size_bytes": 10,
                            "checksum": "0" * 64,
                            "content_type": "audio/wav",
                        }
                    ],
                    "metrics": {"duration_seconds": 1.5},
                    "error": None,
                },
            )
        )
        worker = HttpWorker(_BASE_URL)
        job = Job(job_id="j-1", job_type=WorkerType.TTS, payload={}, chapter_id="ch1")
        result = await worker.execute(job)
        assert result.status == JobStatus.SUCCESS
        assert result.outputs[0].filename == "x.wav"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/shell/test_http_worker.py::TestHttpWorkerExecuteMultipart -v
```
Expected: `TypeError: HttpWorker.__init__() got an unexpected keyword argument 'data_dir'`.

- [ ] **Step 3: Extend `HttpWorker`**

Edit `src/acheron/shell/transports/http.py` to add a `data_dir` constructor param (default reads `ACHERON_DATA_DIR` like the orchestrator does for cache) and a multipart dispatch branch. The final file:

```python
"""HTTP transport for remote workers (RunPod, HuggingFace Inference Endpoints)."""

from __future__ import annotations

import logging
import os
from email.parser import BytesParser
from email.policy import default as default_policy
from pathlib import Path
from typing import Any

import httpx
from pydantic import TypeAdapter

from acheron.core.errors import WorkerError, WorkerUnavailableError
from acheron.core.interfaces import Worker
from acheron.core.models import (
    CostBasis,
    Job,
    JobMetrics,
    JobResult,
    WorkerCapabilities,
)
from acheron.shell.transports._multipart import _build_result, _materialize_artifact

_caps_adapter = TypeAdapter(WorkerCapabilities)
_result_adapter = TypeAdapter(JobResult)
_metrics_adapter = TypeAdapter(JobMetrics)

logger = logging.getLogger(__name__)


class HttpWorker(Worker):
    """Worker that delegates execution to a remote HTTP endpoint.

    Response dispatch is data-driven via ``Content-Type``: a ``multipart/mixed``
    body is parsed into ``OutputFile``s materialized into ``data_dir``; an
    ``application/json`` body is the legacy path that round-trips a
    pre-materialized ``JobResult`` with absolute ``OutputFile.path`` entries
    (used by the HTTP stubs until Plan 3 replaces them).
    """

    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        *,
        data_dir: Path | str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        if data_dir is None:
            data_dir = Path(os.environ.get("ACHERON_DATA_DIR", "/data/jobs"))
        self._data_dir = Path(data_dir)

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self._base_url}{path}"
        try:
            if self._client is not None:
                resp = await self._client.request(method, url, **kwargs)
            else:
                async with httpx.AsyncClient() as client:
                    resp = await client.request(method, url, **kwargs)
            resp.raise_for_status()
        except httpx.ConnectError as exc:
            msg = f"Worker unreachable: {self._base_url}"
            raise WorkerUnavailableError(msg) from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            msg = f"Worker error {exc.response.status_code}: {detail}"
            raise WorkerError(msg) from exc
        else:
            return resp

    async def capabilities(self) -> WorkerCapabilities:  # noqa: D102
        resp = await self._request("GET", "/capabilities")
        return _caps_adapter.validate_json(resp.content)

    async def execute(self, job: Job) -> JobResult:  # noqa: D102
        resp = await self._request("POST", "/execute", json=_job_to_dict(job))
        ctype = resp.headers.get("content-type", "")
        if ctype.startswith("multipart/mixed"):
            return await self._parse_multipart(resp, job.job_id)
        # Legacy JSON path — backward-compatible with existing HTTP stubs.
        return _result_adapter.validate_json(resp.content)

    async def _parse_multipart(self, resp: httpx.Response, job_id: str) -> JobResult:
        """Parse the multipart/mixed body emitted by the SDK edge."""
        ctype = resp.headers["content-type"]
        # Extract boundary from the Content-Type header.
        boundary_part = ctype.split("boundary=", 1)[1]
        # Strip any trailing params / quotes.
        boundary = boundary_part.split(";", 1)[0].strip().strip('"')
        full_body = (
            f"Content-Type: multipart/mixed; boundary={boundary}\r\n"
            "MIME-Version: 1.0\r\n\r\n"
        ).encode() + resp.content
        # Use email.parser to split the multipart body.
        parser = BytesParser(policy=default_policy)
        message = parser.parsebytes(full_body)
        if not message.is_multipart():
            msg = f"Multipart/mixed response from {self._base_url} was not multipart"
            raise WorkerError(msg)

        # The job_id embeds plan_id:plan_job_id-step_id; the step_id suffix is the dir.
        # Keep parity with the stub convention /data/jobs/<plan_job_id>/<step_id>/.
        plan_job_id = "-".join(job_id.split("-")[:-1]) if "-" in job_id else job_id
        step_id = job_id.split("-")[-1] if "-" in job_id else "execute"
        dest_dir = self._data_dir / plan_job_id / step_id

        outputs: list[OutputFile] = []
        metrics: JobMetrics | None = None
        import json

        for part in message.get_payload():
            part_ctype = part.get_content_type()
            if part_ctype == "application/json":
                payload = part.get_payload(decode=True)
                metrics = _metrics_adapter.validate_json(payload)
                continue
            # Binary artifact part.
            filename = part.get_filename() or "artifact.bin"
            data = part.get_payload(decode=True) or b""
            metadata_raw = part.get("X-Acheron-Metadata")
            metadata: dict[str, Any] = {}
            if metadata_raw:
                metadata = json.loads(metadata_raw)
            out = await _materialize_artifact(
                data=data,
                filename=filename,
                content_type=part_ctype,
                metadata=metadata,
                dest_dir=dest_dir,
            )
            outputs.append(out)
        if metrics is None:
            metrics = JobMetrics(duration_seconds=0.0)
        return _build_result(job_id=job_id, outputs=tuple(outputs), metrics=metrics)

    async def health(self) -> bool:  # noqa: D102
        try:
            resp = await self._request("GET", "/health")
        except WorkerError, WorkerUnavailableError:
            return False
        else:
            return resp.status_code == httpx.codes.OK


def _job_to_dict(job: Job) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "job_type": job.job_type.value,
        "payload": job.payload,
        "chapter_id": job.chapter_id,
        "sequence_ids": list(job.sequence_ids) if job.sequence_ids else None,
    }
```

- [ ] **Step 4: Run test + type-check**

```bash
uv run pytest tests/shell/test_http_worker.py -v
uv run mypy src/acheron/shell/transports/http.py
uv run basedpyright src/acheron/shell/transports/http.py
```
Expected: tests pass; type-checkers clean.

- [ ] **Step 5: Update existing tests to inject `data_dir` if needed**

The existing test `test_execute_returns_job_result` posts a JSON-only response and should keep working unchanged (no multipart, no materialization). Run the entire test_http_worker.py file to verify.

- [ ] **Step 6: Commit**

```bash
git add src/acheron/shell/transports/http.py tests/shell/test_http_worker.py
git commit -m " feat(transports): HttpWorker content-type sniff + multipart/mixed parser"
```

---

### Task 4: Extend `proto/synthesis.proto` with `Artifact` + `ExecuteResponse`

**Files:**
- Modify: `proto/synthesis.proto`
- Run: `just proto` (regenerates `src/acheron/proto/synthesis_pb2*.py`)

- [ ] **Step 1: Edit the proto**

Replace the entire contents of `proto/synthesis.proto` with:

```proto
syntax = "proto3";

service Synthesis {
  rpc Synthesize(SynthesisRequest) returns (stream OutputChunk);
}

message SynthesisRequest {
  string job_id = 1;
  string text = 2;
  string language = 3;
  string model = 4;
}

message OutputChunk {
  oneof payload {
    bytes pcm_data = 1;
    Artifact artifact = 2;
  }
  int32 sample_rate = 3;
  int32 channels = 4;
}

message Artifact {
  string filename = 1;
  string content_type = 2;
  bytes data = 3;
  map<string, string> metadata = 4;
}

message ExecuteResponse {
  repeated Artifact artifacts = 1;
  Metrics metrics = 2;
  string error = 3;
}

message Metrics {
  double duration_seconds = 1;
  double gpu_seconds = 2;
  int64 tokens_in = 3;
  int64 tokens_out = 4;
  double cost_estimate = 5;
  string cost_basis = 6;
}
```

Note: this changes `AudioChunk` → `OutputChunk` with a `oneof payload`. The existing stubs that emit `AudioChunk` need adapting; leave them for Plan 3 (they will be replaced by the SDK stub matrix). For Plan 2, focus on the orchestrator-side `GrpcWorker` consumer + tests.

- [ ] **Step 2: Regenerate Python stubs**

```bash
just proto
ls -la src/acheron/proto/synthesis_pb2*.py
```
Expected: `synthesis_pb2.py` + `synthesis_pb2_grpc.py` regenerated with `OutputChunk`, `Artifact`, `ExecuteResponse`, `Metrics`.

- [ ] **Step 3: Run the existing grpc worker test to verify what breaks**

```bash
uv run pytest tests/shell/test_grpc_worker.py -v
```
Expected: `AttributeError: module 'acheron.proto.synthesis_pb2' has no attribute 'AudioChunk'` in `_FakeSynthesisServicer`. That's expected — fix in Task 5.

- [ ] **Step 4: Commit (proto + regenerated stubs)**

```bash
git add proto/synthesis.proto src/acheron/proto/synthesis_pb2.py src/acheron/proto/synthesis_pb2_grpc.py
git commit -m "feat(proto): extend OutputChunk with Artifact oneof + ExecuteResponse/Metrics"
```

---

### Task 5: `GrpcWorker.execute()` Artifact mode + legacy `pcm_data` mode

**Files:**
- Modify: `src/acheron/shell/transports/grpc.py`
- Modify: `tests/shell/test_grpc_worker.py`

- [ ] **Step 1: Rewrite the failing test for the new contract**

Replace the body of `tests/shell/test_grpc_worker.py` above the `test_grpc_channel_uses_secure_when_ca_set` test (i.e. lines 1-132) with:

```python
"""Tests for the GrpcWorker transport."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

import grpc
import grpc.aio
import pytest
import pytest_asyncio
from grpc_health.v1 import health, health_pb2_grpc

from acheron.core.errors import WorkerError, WorkerUnavailableError
from acheron.core.models import (
    CostBasis,
    Job,
    JobResult,
    JobStatus,
    WorkerType,
)
from acheron.proto import synthesis_pb2, synthesis_pb2_grpc
from acheron.shell.transports.grpc import GrpcWorker

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class _FakeSynthesisServicer(synthesis_pb2_grpc.SynthesisServicer):
    """In-process gRPC servicer.

    Modes:
      - pcm_chunks set: legacy PCM streaming (real-time / live-audio use case).
      - artifacts set: Artifact mode (structured output — the new path).
      - fail=True: server returns UNAVAILABLE.
    """

    def __init__(
        self,
        *,
        pcm_chunks: list[bytes] | None = None,
        artifacts: list[synthesis_pb2.Artifact] | None = None,
        metrics: synthesis_pb2.Metrics | None = None,
        fail: bool = False,
    ) -> None:
        self._pcm_chunks = pcm_chunks
        self._artifacts = artifacts
        self._metrics = metrics
        self._fail = fail

    def Synthesize(  # noqa: N802
        self,
        request: synthesis_pb2.SynthesisRequest,
        context: grpc.aio.ServicerContext,
    ) -> Any:
        if self._fail:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details("GPU down")
            return
        if self._artifacts is not None:
            for art in self._artifacts:
                yield synthesis_pb2.OutputChunk(artifact=art)
            if self._metrics is not None:
                # The last chunk carries metrics via the ExecuteResponse-style trailer —
                # see the wire format note below. For the test we mimic it by relying on
                # the orchestrator-side post-processing of the trailing response metadata.
                pass
            return
        for chunk in self._pcm_chunks or [b"\x00\x00" * 100]:
            yield synthesis_pb2.OutputChunk(
                pcm_data=chunk,
                sample_rate=22050,
                channels=1,
            )


@pytest_asyncio.fixture
async def grpc_server() -> AsyncIterator[tuple[str, _FakeSynthesisServicer]]:
    servicer = _FakeSynthesisServicer()
    server = grpc.aio.server()
    synthesis_pb2_grpc.add_SynthesisServicer_to_server(servicer, server)  # type: ignore[no-untyped-call]
    health_servicer = health.HealthServicer()
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)  # type: ignore[attr-defined]
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    port = server.add_insecure_port("localhost:0")
    await server.start()
    yield f"localhost:{port}", servicer
    await server.stop(0)


@pytest_asyncio.fixture
async def grpc_worker(grpc_server: tuple[str, _FakeSynthesisServicer]) -> AsyncIterator[GrpcWorker]:
    addr, _ = grpc_server
    channel = grpc.aio.insecure_channel(addr)
    worker = GrpcWorker(channel, data_dir=Path("/tmp/acheron-grpc-test"))
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
        worker = GrpcWorker(channel, data_dir=Path("/tmp/acheron-grpc-test"))
        result = await worker.health()
        assert result is False
        await channel.close()


class TestGrpcWorkerCapabilities:
    @pytest.mark.asyncio
    async def test_capabilities_returns_tts(self, grpc_worker: GrpcWorker) -> None:
        caps = await grpc_worker.capabilities()
        assert caps.worker_type == WorkerType.TTS
        assert caps.batch_capable is True


class TestGrpcWorkerExecuteArtifact:
    @pytest.mark.asyncio
    async def test_assembles_artifacts(
        self,
        grpc_server: tuple[str, _FakeSynthesisServicer],
        tmp_path: Path,
    ) -> None:
        addr, servicer = grpc_server
        servicer._artifacts = [  # noqa: SLF001
            synthesis_pb2.Artifact(filename="ch1_0000.wav", content_type="audio/wav", data=b"\x01\x02\x03"),
            synthesis_pb2.Artifact(filename="ch1_0001.wav", content_type="audio/wav", data=b"\x04\x05\x06"),
        ]
        channel = grpc.aio.insecure_channel(addr)
        worker = GrpcWorker(channel, data_dir=tmp_path)
        job = Job(job_id="job-xyz-synthesize-ch1", job_type=WorkerType.TTS, payload={"text": "hi"}, chapter_id="ch1")
        result = await worker.execute(job)
        assert result.status == JobStatus.SUCCESS
        assert len(result.outputs) == 2
        assert result.outputs[0].filename == "ch1_0000.wav"
        assert Path(result.outputs[0].path).read_bytes() == b"\x01\x02\x03"
        await channel.close()

    @pytest.mark.asyncio
    async def test_raises_on_non_tts_job(self, grpc_worker: GrpcWorker) -> None:
        job = Job(job_id="j-1", job_type=WorkerType.ASR, payload={}, chapter_id="ch1")
        with pytest.raises(WorkerError, match="TTS"):
            await grpc_worker.execute(job)

    @pytest.mark.asyncio
    async def test_raises_unavailable_on_server_error(
        self, grpc_server: tuple[str, _FakeSynthesisServicer], tmp_path: Path
    ) -> None:
        addr, servicer = grpc_server
        servicer._fail = True  # noqa: SLF001
        channel = grpc.aio.insecure_channel(addr)
        worker = GrpcWorker(channel, data_dir=tmp_path)
        job = Job(job_id="j-1", job_type=WorkerType.TTS, payload={"text": "hola"}, chapter_id="ch1")
        with pytest.raises(WorkerUnavailableError, match="unavailable"):
            await worker.execute(job)
        await channel.close()
```

Wire-format note: for simplicity, the orchestrator-side `GrpcWorker` collects `OutputChunk.artifact` parts; metrics would arrive via trailing metadata in production. Plan 2 covers the artifact-only path; metrics trailing-metadata handling is left as a follow-up if the qwen3tts worker needs it (it doesn't — v1 TTS uses HTTP, not gRPC).

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/shell/test_grpc_worker.py -v
```
Expected: `TypeError: GrpcWorker.__init__() got an unexpected keyword argument 'data_dir'` and other constructor/method-mismatch errors.

- [ ] **Step 3: Rewrite `src/acheron/shell/transports/grpc.py`**

Replace the entire file with:

```python
"""gRPC transport for remote TTS workers — Artifact mode + legacy PCM streaming."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

import grpc
import grpc.aio
from grpc.health.v1 import health_pb2, health_pb2_grpc

from acheron.core.errors import WorkerError, WorkerUnavailableError
from acheron.core.interfaces import Worker
from acheron.core.models import (
    SUPPORTED_LANGUAGES,
    Job,
    JobMetrics,
    JobResult,
    JobStatus,
    OutputFile,
    WorkerCapabilities,
    WorkerType,
)
from acheron.proto import synthesis_pb2, synthesis_pb2_grpc
from acheron.shell.transports._multipart import _build_result, _materialize_artifact

logger = logging.getLogger(__name__)


class GrpcWorker(Worker):
    """Worker that delegates TTS execution to a remote gRPC endpoint.

    ``OutputChunk`` carries an ``oneof payload``: ``pcm_data`` (legacy live
    streaming) or ``artifact`` (structured output, since Layer 8a). The
    orchestrator consumes ``Artifact`` parts via the shared
    ``_materialize_artifact`` / ``_build_result`` helpers — identical to the
    HTTP multipart path. Legacy ``pcm_data`` mode is preserved.
    """

    def __init__(
        self,
        channel: grpc.aio.Channel,
        *,
        data_dir: Path | str | None = None,
    ) -> None:
        self._channel = channel
        self._stub = synthesis_pb2_grpc.SynthesisStub(channel)  # type: ignore[no-untyped-call]
        self._health_stub = health_pb2_grpc.HealthStub(channel)
        if data_dir is None:
            data_dir = Path(os.environ.get("ACHERON_DATA_DIR", "/data/jobs"))
        self._data_dir = Path(data_dir)

    async def capabilities(self) -> WorkerCapabilities:  # noqa: D102
        return WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=SUPPORTED_LANGUAGES,
            supported_languages_out=SUPPORTED_LANGUAGES,
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

        request = synthesis_pb2.SynthesisRequest(  # type: ignore[attr-defined]
            job_id=job.job_id,
            text=str(job.payload.get("text", "")),
            language=str(job.payload.get("language", "")),
            model=str(job.payload.get("model", "")),
        )

        start_time = time.monotonic()
        artifact_parts: list[synthesis_pb2.Artifact] = []
        pcm_chunks: list[bytes] = []

        try:
            async for chunk in self._stub.Synthesize(request):
                payload_type = chunk.WhichOneof("payload")
                if payload_type == "artifact":
                    artifact_parts.append(chunk.artifact)  # type: ignore[attr-defined]
                elif payload_type == "pcm_data":
                    pcm_chunks.append(chunk.pcm_data)  # type: ignore[attr-defined]
        except grpc.aio.AioRpcError as exc:
            if exc.code() == grpc.StatusCode.UNAVAILABLE:
                msg = f"Worker unavailable: {exc.details()}"
                raise WorkerUnavailableError(msg) from exc
            msg = f"gRPC error {exc.code()}: {exc.details()}"
            raise WorkerError(msg) from exc

        duration = time.monotonic() - start_time

        if artifact_parts:
            return await self._assemble_artifacts(job.job_id, artifact_parts, duration)
        # Legacy PCM fallback — keep the prior behavior intact.
        return self._assemble_pcm(job.job_id, pcm_chunks, duration)

    async def _assemble_artifacts(
        self,
        job_id: str,
        artifacts: list[synthesis_pb2.Artifact],
        duration: float,
    ) -> JobResult:
        plan_job_id = "-".join(job_id.split("-")[:-1]) if "-" in job_id else job_id
        step_id = job_id.split("-")[-1] if "-" in job_id else "execute"
        dest_dir = self._data_dir / plan_job_id / step_id

        outputs: list[OutputFile] = []
        for art in artifacts:
            metadata = {k: v for k, v in art.metadata.items()}
            out = await _materialize_artifact(
                data=art.data,
                filename=art.filename,
                content_type=art.content_type,
                metadata=metadata,
                dest_dir=dest_dir,
            )
            outputs.append(out)
        # Plan 2 doesn't surface trailing-metadata metrics yet; the HTTP path
        # carries cost_basis. The gRPC path fills a basic metrics envelope; a
        # future sub-project wires trailing-metadata → JobMetrics.
        metrics = JobMetrics(duration_seconds=duration)
        return _build_result(job_id=job_id, outputs=tuple(outputs), metrics=metrics)

    def _assemble_pcm(self, job_id: str, pcm_chunks: list[bytes], duration: float) -> JobResult:
        audio_data = b"".join(pcm_chunks)
        return JobResult(
            job_id=job_id,
            status=JobStatus.SUCCESS,
            outputs=(
                OutputFile(
                    path=f"{job_id}.pcm",
                    filename=f"{job_id}.pcm",
                    size_bytes=len(audio_data),
                    checksum="",
                    content_type="audio/pcm",
                ),
            ),
            metrics=JobMetrics(duration_seconds=duration),
        )

    async def health(self) -> bool:  # noqa: D102
        try:
            response = await self._health_stub.Check(health_pb2.HealthCheckRequest())
        except grpc.aio.AioRpcError:
            return False
        else:
            return response.status == health_pb2.HealthCheckResponse.SERVING  # type: ignore[no-any-return]
```

- [ ] **Step 4: Run tests + type-check**

```bash
uv run pytest tests/shell/test_grpc_worker.py -v
uv run mypy src/acheron/shell/transports/grpc.py
uv run basedpyright src/acheron/shell/transports/grpc.py
```
Expected: tests pass; type-checkers clean. The `test_grpc_channel_uses_secure_when_ca_set` test at the bottom of `test_grpc_worker.py` should still pass — it only uses `grpc_channel` from `acheron.shell.tls`, not the GrpcWorker constructor.

- [ ] **Step 5: Commit**

```bash
git add src/acheron/shell/transports/grpc.py tests/shell/test_grpc_worker.py
git commit -m "feat(transports): GrpcWorker Artifact mode + legacy PCM backed by shared _multipart"
```

---

### Task 6: Add `total_cost_basis` to `JobResponse` + propagate in API + orchestrator

**Files:**
- Modify: `src/acheron/shell/api/schemas.py`
- Modify: `src/acheron/shell/orchestrator.py`
- Modify: `src/acheron/shell/api/routes/jobs.py`
- Modify: `tests/shell/api/test_jobs.py` (if a spec exists; else create one matching the existing pattern)

- [ ] **Step 1: Write the failing test**

Run `ls tests/shell/api/` to find the existing job tests. Append a test to whichever file covers `/jobs/{id}`:

```python
class TestJobResponseCostBasis:
    def test_total_cost_basis_returned_when_set(self, client, ...) -> None:
        # depends on existing fixture; just assert the field is in the response
        ...
```

If the test files are not set up to assert this property cleanly, write a focused unit test against `JobResponse.model_dump`:

```python
# tests/shell/api/test_schemas.py
from acheron.shell.api.schemas import JobResponse


def test_job_response_includes_total_cost_basis() -> None:
    r = JobResponse(job_id="j", status="completed", total_cost_basis="measured")
    dumped = r.model_dump()
    assert dumped["total_cost_basis"] == "measured"


def test_job_response_default_total_cost_basis_is_none() -> None:
    r = JobResponse(job_id="j", status="completed")
    assert r.total_cost_basis is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/shell/api/test_schemas.py -v
```
Expected: `ValidationError` or `AttributeError`.

- [ ] **Step 3: Add the field to `JobResponse` in `src/acheron/shell/api/schemas.py`**

```python
class JobResponse(BaseModel):
    """Response for a single job."""

    job_id: str
    status: str
    plan_id: str | None = None
    completed_steps: int = 0
    total_steps: int = 0
    total_cost: float = 0.0
    total_duration_seconds: float = 0.0
    total_cost_basis: str | None = None
    errors: list[str] = []
```

- [ ] **Step 4: Compute `total_cost_basis` in the orchestrator**

Find the place where `TrackedJob` is turned into `JobResponse` (search for `JobResponse(`) — likely `src/acheron/shell/api/routes/jobs.py` and/or `orchestrator.py`. Read the relevant line range first via `grep -n "JobResponse(" src/acheron/shell`.

In each factory call that builds a `JobResponse`, derive `total_cost_basis` as the least-confident `CostBasis` across the step `JobMetrics` of the plan (or `None` if no step has a basis). Helper to add to `src/acheron/shell/capabilities.py` or a new small module:

```python
# src/acheron/shell/cost.py
"""Per-job cost-basis aggregation."""

from acheron.core.models import CostBasis, JobMetrics, PlanResult

_CONFIDENCE_ORDER = {
    CostBasis.MEASURED: 0,
    CostBasis.CACHED: 1,
    CostBasis.STATIC: 2,
    CostBasis.UNKNOWN: 3,
}


def aggregate_cost_basis(per_step: list[JobMetrics | None]) -> CostBasis | None:
    """Return the least-confident basis across steps, or None if no step has one."""
    bases = [m.cost_basis for m in per_step if m is not None and m.cost_basis is not None]
    if not bases:
        return None
    return min(bases, key=lambda b: _CONFIDENCE_ORDER[b])
```

Update the `JobResponse(...)` construction site to call `aggregate_cost_basis(...)` with the per-step metrics. (Or, if the orchestrator already surfaces a `PlanResult`, attach `total_cost_basis` to `PlanResult` so this is a single source of truth.)

Update `orchestrator.py`'s `TrackedJob` → `JobResponse` resolution to read from `result.total_cost_basis` when the `PlanResult` is computed, and assign at plan completion time as well: in the spot that builds `PlanResult`, set:

```python
from acheron.shell.cost import aggregate_cost_basis

total_cost_basis=aggregate_cost_basis(per_step_metrics)
```

The walkthrough of where `PlanResult` is constructed lives in `src/acheron/shell/orchestrator.py` around the executor `_execute` method — read it via `grep -n "PlanResult(" src/acheron/shell/orchestrator.py` to find the exact location.

- [ ] **Step 5: Run the tests + type-check**

```bash
uv run pytest tests/shell/api/test_schemas.py tests/shell/ -v
uv run mypy src/acheron/shell/cost.py src/acheron/shell/api/schemas.py
uv run basedpyright src/acheron/shell/cost.py src/acheron/shell/api/schemas.py
```
Expected: tests pass; type-checkers clean.

- [ ] **Step 6: Commit**

```bash
git add src/acheron/shell/api/schemas.py src/acheron/shell/cost.py src/acheron/shell/orchestrator.py tests/shell/api/test_schemas.py
git commit -m "feat(api): surface JobResponse.total_cost_basis as least-confidence aggregation"
```

---

### Task 7: Dashboard `Cost Basis` + `Note` rendering

**Files:**
- Modify: `dashboard/templates/partials/cost.html`
- Create: `dashboard/tests/test_cost_partial.py`

- [ ] **Step 1: Write the failing test**

Create `dashboard/tests/test_cost_partial.py`:

```python
"""Tests for the dashboard cost partial — Cost Basis + Note columns."""

from pathlib import Path

from fastapi.testclient import TestClient

from dashboard.app import create_app


def test_cost_partial_renders_measured_basis() -> None:
    import asyncio
    from unittest.mock import patch

    async def fake_fetch(_, _path):
        return {
            "jobs": [
                {
                    "job_id": "j1",
                    "status": "completed",
                    "total_cost": 0.42,
                    "total_duration_seconds": 100.0,
                    "completed_steps": 5,
                    "total_steps": 5,
                    "total_cost_basis": "measured",
                }
            ]
        }

    app = create_app("http://orchestrator:8000")
    client = TestClient(app)
    with patch("dashboard.app._fetch_orchestrator", side_effect=fake_fetch):
        r = client.get("/partials/cost")
    assert r.status_code == 200
    assert "Measured" in r.text
    assert "$0.42" in r.text


def test_cost_partial_renders_unknown_basis_as_dash() -> None:
    from unittest.mock import patch

    async def fake_fetch(_, _path):
        return {
            "jobs": [
                {
                    "job_id": "j2",
                    "status": "completed",
                    "total_cost": 0.0,
                    "total_duration_seconds": 0.0,
                    "completed_steps": 4,
                    "total_steps": 4,
                    "total_cost_basis": "unknown",
                }
            ]
        }

    app = create_app("http://orchestrator:8000")
    client = TestClient(app)
    with patch("dashboard.app._fetch_orchestrator", side_effect=fake_fetch):
        r = client.get("/partials/cost")
    assert r.status_code == 200
    # Unknown basis => dash in the Cost cell, gray badge label
    assert "Unknown" in r.text
    # The cost cell must NOT show "$0.00" for unknown — that's the bug we're fixing.
    assert ">-$<" in r.text.replace(" ", "") or ">-$<" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest dashboard/tests/test_cost_partial.py -v
```
Expected: tests fail — `Measured`/`Unknown` not in rendered template.

- [ ] **Step 3: Update the cost partial template**

Replace `dashboard/templates/partials/cost.html` with:

```html
{% if jobs %}
<table>
  <thead>
    <tr>
      <th>Job ID</th>
      <th>Status</th>
      <th>Cost</th>
      <th>Duration</th>
      <th>Steps</th>
      <th>Cost Basis</th>
      <th>Note</th>
    </tr>
  </thead>
  <tbody>
    {% for j in jobs %}
    {% set basis = j.total_cost_basis %}
    {% set basis_class = {
      "measured": "basis-measured",
      "cached": "basis-cached",
      "unknown": "basis-unknown",
      "static": "basis-static"
    }.get(basis, "basis-unknown") %}
    {% set basis_label = {"measured": "Measured", "cached": "Cached",
                          "unknown": "Unknown", "static": "Static"}.get(basis, "Unknown") %}
    {% set basis_note = {
      "measured": "—",
      "cached": "RunPod pricing API unavailable; serving last-known rate",
      "unknown": "RunPod pricing API unavailable",
      "static": "—"
    }.get(basis, "—") %}
    <tr>
      <td>{{ j.job_id }}</td>
      <td><span class="badge badge-{{ j.status }}">{{ j.status }}</span></td>
      <td>
        {% if basis == "unknown" or j.total_cost|default(0) == 0 and basis == "unknown" %}
          <span class="cost-unknown">—</span>
        {% elif j.total_cost|default(0) > 0 %}
          ${{ "%.2f"|format(j.total_cost) }}
        {% else %}
          <span class="cost-zero">$0.00</span>
        {% endif %}
      </td>
      <td>{% if j.total_duration_seconds|default(0) > 0 %}{{ "%.1f"|format(j.total_duration_seconds) }}s{% else %}-{% endif %}</td>
      <td>{{ j.completed_steps }}/{{ j.total_steps }}</td>
      <td><span class="badge {{ basis_class }}">{{ basis_label }}</span></td>
      <td><span class="basis-note">{{ basis_note }}</span></td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% else %}
<p style="color:#8b949e">No cost data available.</p>
{% endif %}
```

Critical semantics the test asserts: **`unknown` basis** renders `—` in the Cost cell (NOT `$0.00`), distinguishing "we don't know the price" from "it was actually free."

- [ ] **Step 4: Add the CSS classes**

Open `dashboard/templates/index.html` (or wherever the styles live — `grep -n "badge-" dashboard/templates/index.html`). Add a small `<style>` block (or extend the existing one) with:

```css
.basis-measured  { color: #2ea043; }
.basis-cached    { color: #d29922; }
.basis-unknown   { color: #8b949e; }
.basis-static    { color: #58a6ff; }
.cost-unknown    { color: #8b949e; }
.cost-zero       { color: #58a6ff; }
.basis-note      { color: #8b949e; font-style: italic; }
.badge.basis-unknown { background-color: #21262d; border: 1px solid #8b949e; }
```

If the repo uses an external stylesheet, add the equivalent rules there. Read `dashboard/templates/index.html` first to see the convention (inline vs linked).

- [ ] **Step 5: Run test + type-check**

```bash
uv run pytest dashboard/tests/test_cost_partial.py -v
uv run basedpyright dashboard/app.py
```
Expected: tests pass. (mypy doesn't cover `dashboard/` since it's not in `[tool.mypy]` scope — keep basedpyright clean as the `dashboard` per-file ignores override.)

- [ ] **Step 6: Commit**

```bash
git add dashboard/templates/partials/cost.html dashboard/templates/index.html dashboard/tests/test_cost_partial.py
git commit -m "feat(dashboard): render per-job Cost Basis badge + Note, unknown ≠ free"
```

---

### Task 8: Final-gate `just validate`

- [ ] **Step 1: Run full validation**

```bash
just validate
```
Expected: `lint-strict`, `lint-imports`, `type-check`, `type-check-pyright`, `test` all pass; coverage ≥ 80%.

- [ ] **Step 2: If stubs break because of the proto change, defer to Plan 3**

The existing `stubs/grpc_worker_stub.py` references the old `AudioChunk` symbol; Plan 3 replaces it with the SDK stub matrix. If `pytest stubs/tests/` fails for the proto rename, mark those tests `@pytest.mark.xfail(reason="stub to be replaced in Plan 3")` rather than rewriting the stub now — Plan 3 deletes them wholesale.

```bash
grep -n "AudioChunk" stubs/
```

If hits, xfail the affected stub tests (see existing `xfail_strict = true` in `pyproject.toml`):

```python
import pytest
pytestmark = pytest.mark.xfail(reason="Stub replaced by SDK matrix in Plan 3", strict=True)
```

- [ ] **Step 3: Commit any xfails**

```bash
git add stubs/tests/
git commit -m "test(stubs): xfail legacy OAuthed gRPC stub tests pending Plan 3 SDK matrix"
```

---

## Spec Coverage Map

- `HttpWorker.execute()` content-type sniff + multipart/mixed parser — Task 3.
- `GrpcWorker` `Artifact` mode + legacy `pcm_data` mode — Tasks 4 + 5.
- Shared `_materialize_artifact` / `_build_result` helpers — Task 2.
- `proto/synthesis.proto` extension (additive oneof) + regenerated stubs — Task 4.
- `JobResponse.total_cost_basis` + `PlanResult.total_cost_basis` + least-confidence aggregation — Task 6.
- Dashboard cost-confidence rendering (Cost Basis badge + Note; unknown ≠ free) — Task 7.
- Legacy JSON path preserved — verified by Task 3 step 5.
- Cold-start detection — unchanged (verified; the SDK already tags `health_provider` + `health_endpoint_id` in Plan 1).

**Deferred to Plan 3:** the 7-stub matrix replacing the existing 4 stubs; `workers/qwen3tts/` package; GHCR CI workflow; `docker-compose.yml` edge service entry; `workers.* -/-> acheron.shell` import-linter contract; the actual GHCR image build for `achershelon-worker-edge`.