# Layer 8a — SDK Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish the `acheron.worker_sdk` blueprint and the core `CostBasis` tracking that real GPU workers (Plan 3) and the orchestrator transport layer (Plan 2) consume.

**Architecture:** `acheron.worker_sdk` is a new subpackage of the existing `acheron` wheel. It imports from `acheron.core` only; `acheron.shell` stays forbidden via import-linter. The SDK ships a `WorkerHandler` ABC, composable `Artifact` outputs, a `WorkerSettings` model with YAML discovery, fault-tolerant `RunPodPrice` via GraphQL (endpoint-discovered GPU + `uninterruptablePrice`), a registration client, a `make_runpod_handler` cloud adapter, an internal RunPod forwarder, and a `create_worker_app` FastAPI factory. The CLI is the `acheron-worker-edge` image's entrypoint module — never user-invoked.

**Tech Stack:** Python 3.14, pydantic v2, pydantic-settings, httpx, FastAPI, uvicorn, the `runpod` Python SDK, pytest + respx + pytest-asyncio, mypy + basedpyright, ruff, import-linter.

**Reference spec:** `docs/superpowers/specs/2026-06-22-layer8a-tts-worker-design.md`

**Final gate:** `just validate` green (lint-strict, lint-imports, mypy, basedpyright, pytest — all clean, coverage ≥ 80%).

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/acheron/core/models.py` | Add `CostBasis` enum and `JobMetrics.cost_basis` field. |
| `tests/core/test_models.py` | Cover `CostBasis` + `cost_basis` round-trip via `TypeAdapter(JobMetrics)`. |
| `src/acheron/worker_sdk/handler.py` | `WorkerHandler` ABC + `startup`/`shutdown` lifecycle hooks. |
| `src/acheron/worker_sdk/artifacts.py` | `Artifact` Protocol + `BytesArtifact` / `StreamArtifact` / `FileArtifact`. |
| `src/acheron/worker_sdk/settings.py` | `WorkerSettings(BaseSettings)` with `env_prefix="ACHERON_WORKER_"` and `_ENV_ONLY_FIELDS`. |
| `src/acheron/worker_sdk/config_loader.py` | YAML discovery (`WORKER_CONFIG` → `<name>.worker.yaml` → `worker.yaml` → env-only) + secrets rejection. |
| `src/acheron/worker_sdk/pricing.py` | `PriceSource` Protocol + `ZeroPrice` / `StaticPrice` / `RunPodPrice`; `PriceEstimate`; `to_cost_basis()` helper. |
| `src/acheron/worker_sdk/registration.py` | `register_with_orchestrator()` client with backoff retry. |
| `src/acheron/worker_sdk/schemas.py` | Pydantic models mirroring `core.models.Job` + `JobResult` for `/execute` validation. |
| `src/acheron/worker_sdk/cloud.py` | `make_runpod_handler()` adapter that wraps a `WorkerHandler` as a RunPod-compatible callable. |
| `src/acheron/worker_sdk/_runpod_client.py` | Internal: wraps `runpod.Endpoint(id).run(...)` + status poll + timeout + cost timing. |
| `src/acheron/worker_sdk/_edge_http.py` | Internal FastAPI app served by the entrypoint (the actual `/health`, `/capabilities`, `/execute` routes). |
| `src/acheron/worker_sdk/app.py` | `create_worker_app(handler, settings, *, backend)` factory — lifespan wires backend, registration, pricing refresh. |
| `src/acheron/worker_sdk/cli.py` | `acheron-worker-edge` image `CMD` module. Resolves handler from import path, builds settings from YAML, runs uvicorn. |
| `src/acheron/worker_sdk/__init__.py` | Public re-exports per spec. |
| `tests/worker_sdk/*` | Mirror of `src/acheron/worker_sdk/` per AGENTS.md — unit tests, no GPU, no Docker. |
| `pyproject.toml` | Add `runpod` runtime dep, `acheron-worker-edge` console script, import-linter `worker-sdk-no-shell` contract. |

---

### Task 1: Add `CostBasis` enum + `JobMetrics.cost_basis`

**Files:**
- Modify: `src/acheron/core/models.py`
- Modify: `tests/core/test_models.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_models.py`:

```python
from pydantic import TypeAdapter
from acheron.core.models import CostBasis, JobMetrics


class TestCostBasis:
    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (CostBasis.MEASURED, "measured"),
            (CostBasis.CACHED, "cached"),
            (CostBasis.STATIC, "static"),
            (CostBasis.UNKNOWN, "unknown"),
        ],
    )
    def test_cost_basis_values(self, member: CostBasis, value: str) -> None:
        assert member.value == value


class TestJobMetricsCostBasis:
    _adapter = TypeAdapter(JobMetrics)

    def test_default_cost_basis_is_none(self) -> None:
        m = JobMetrics(duration_seconds=1.0)
        assert m.cost_basis is None

    def test_explicit_cost_basis_round_trip(self) -> None:
        m = JobMetrics(duration_seconds=2.0, gpu_seconds=1.5, cost_estimate=0.042, cost_basis=CostBasis.MEASURED)
        dumped = self._adapter.dump_python(m)
        assert dumped["cost_basis"] == "measured"
        round_trip = self._adapter.validate_python(dumped)
        assert round_trip.cost_basis == CostBasis.MEASURED

    def test_none_cost_basis_omits_from_optional_round_trip(self) -> None:
        m = JobMetrics(duration_seconds=2.0)
        dumped = self._adapter.dump_python(m, exclude_defaults=False)
        assert dumped.get("cost_basis") in (None, "unknown") or "cost_basis" not in dumped
        round_trip = self._adapter.validate_python(dumped)
        assert round_trip.cost_basis is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/core/test_models.py::TestCostBasis -v
```
Expected: `ImportError: cannot import name 'CostBasis'`.

- [ ] **Step 3: Add `CostBasis` and extend `JobMetrics`**

In `src/acheron/core/models.py`, after the `WorkerStatus` enum (line 64):

```python
class CostBasis(Enum):
    """Confidence level for a per-job cost estimate (since Layer 8a)."""

    MEASURED = "measured"
    CACHED = "cached"
    STATIC = "static"
    UNKNOWN = "unknown"
```

Modify `JobMetrics` (currently at lines 103-111) to add the new field:

```python
@dataclass(frozen=True)
class JobMetrics:
    """Timing and cost data for a completed job."""

    duration_seconds: float
    gpu_seconds: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_estimate: float | None = None
    cost_basis: CostBasis | None = None
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/core/test_models.py::TestCostBasis tests/core/test_models.py::TestJobMetricsCostBasis -v
```
Expected: PASS.

- [ ] **Step 5: Run the full core suite + type-check**

```bash
uv run mypy src/acheron/core/models.py tests/core/test_models.py
uv run basedpyright src/acheron/core/models.py
```
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add src/acheron/core/models.py tests/core/test_models.py
git commit -m "feat(core): add CostBasis enum and JobMetrics.cost_basis"
```

---

### Task 2: `acheron.worker_sdk` package skeleton + import-linter contract

**Files:**
- Create: `src/acheron/worker_sdk/__init__.py`
- Create: `tests/worker_sdk/__init__.py`
- Create: `tests/worker_sdk/test_smoke.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write the failing test**

Create `tests/worker_sdk/__init__.py` (empty) and `tests/worker_sdk/test_smoke.py`:

```python
"""Smoke test proving the worker_sdk subpackage imports cleanly."""


def test_package_importable() -> None:
    import acheron.worker_sdk  # noqa: F401
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/worker_sdk/test_smoke.py -v
```
Expected: `ImportError: No module named 'acheron.worker_sdk'`.

- [ ] **Step 3: Create the package skeleton**

Create `src/acheron/worker_sdk/__init__.py`:

```python
"""Acheron worker SDK — the blueprint for Layer 8 real GPU workers.

Public surface re-exports are filled in by later tasks as modules land.
Importing this package must not require runpod/torch/etc. — those deps are
imported lazily by the modules that need them so unit tests of pure types
(handler, artifacts, settings) work without GPU SDKs installed.
"""
```

- [ ] **Step 4: Add the import-linter contract**

In `pyproject.toml`, immediately after the existing `core-shell-boundary` contract (after line 136 in the current file), append:

```toml
[[tool.importlinter.contracts]]
name = "worker-sdk-no-shell"
type = "forbidden"
source_modules = ["acheron.worker_sdk"]
forbidden_modules = ["acheron.shell"]
```

- [ ] **Step 5: Run the test + linter to verify**

```bash
uv run pytest tests/worker_sdk/test_smoke.py -v
uv run lint-imports
```
Expected: PASS + linter exit 0 (no forbidden import attempted yet).

- [ ] **Step 6: Commit**

```bash
git add src/acheron/worker_sdk/__init__.py tests/worker_sdk/__init__.py tests/worker_sdk/test_smoke.py pyproject.toml
git commit -m "feat(worker_sdk): scaffold package + import-linter boundary"
```

---

### Task 3: `Artifact` Protocol + three composable variants

**Files:**
- Create: `src/acheron/worker_sdk/artifacts.py`
- Create: `tests/worker_sdk/test_artifacts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/worker_sdk/test_artifacts.py`:

```python
"""Tests for the Artifact composition primitives."""

import asyncio
from pathlib import Path
from typing import AsyncIterator

import pytest

from acheron.worker_sdk.artifacts import (
    Artifact,
    BytesArtifact,
    FileArtifact,
    StreamArtifact,
)


async def _collect(artifact: Artifact) -> bytes:
    return b"".join([chunk async for chunk in artifact.stream()])


class TestBytesArtifact:
    @pytest.mark.asyncio
    async def test_stream_yields_data_once(self) -> None:
        a = BytesArtifact(filename="x.wav", content_type="audio/wav", data=b"hello")
        out = await _collect(a)
        assert out == b"hello"

    @pytest.mark.asyncio
    async def test_metadata_default_empty(self) -> None:
        a = BytesArtifact(filename="x.wav", content_type="audio/wav", data=b"")
        assert a.metadata == {}


class TestStreamArtifact:
    @pytest.mark.asyncio
    async def test_stream_yields_each_chunk(self) -> None:
        async def gen() -> AsyncIterator[bytes]:
            yield b"chunk1"
            yield b"chunk2"

        a = StreamArtifact(filename="long.wav", content_type="audio/wav", producer=gen)
        out = await _collect(a)
        assert out == b"chunk1chunk2"


class TestFileArtifact:
    @pytest.mark.asyncio
    async def test_stream_reads_from_disk_in_chunks(self, tmp_path: Path) -> None:
        path = tmp_path / "blob.bin"
        path.write_bytes(b"x" * 200_000)  # larger than the 64kb read window
        a = FileArtifact(filename="blob.bin", content_type="application/octet-stream", path=path)
        out = await _collect(a)
        assert len(out) == 200_000
        assert out == b"x" * 200_000
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/worker_sdk/test_artifacts.py -v
```
Expected: `ImportError: cannot import name 'Artifact'`.

- [ ] **Step 3: Implement `artifacts.py`**

Create `src/acheron/worker_sdk/artifacts.py`:

```python
"""Composable output artifact primitives for WorkerHandler.handle() returns.

The multipart encoder / volume writer treat these uniformly via the
`Artifact` Protocol so workers mix-and-match the variant their model's API
naturally produces — no forced buffering.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from acheron.core.models import JsonValue


@runtime_checkable
class Artifact(Protocol):
    """Transport-neutral output produced by `WorkerHandler.handle()`."""

    filename: str
    content_type: str
    metadata: dict[str, JsonValue]

    def stream(self) -> AsyncIterator[bytes]: ...


@dataclass(frozen=True)
class BytesArtifact:
    """In-memory bytes — chapter-level WAV, short text."""

    filename: str
    content_type: str
    data: bytes
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    async def stream(self) -> AsyncIterator[bytes]:
        yield self.data


@dataclass(frozen=True)
class StreamArtifact:
    """Lazily-produced chunks — long audio, batched generation."""

    filename: str
    content_type: str
    producer: Callable[[], AsyncIterator[bytes]]
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    async def stream(self) -> AsyncIterator[bytes]:
        async for chunk in self.producer():
            yield chunk


@dataclass(frozen=True)
class FileArtifact:
    """Worker wrote to disk (shared-volume mode or a tmp file)."""

    filename: str
    content_type: str
    path: Path
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    async def stream(self) -> AsyncIterator[bytes]:
        import aiofiles

        async with await aiofiles.open(self.path, "rb") as f:
            while True:
                chunk = await f.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
```

Note: `aiofiles` is imported lazily inside `FileArtifact.stream` so the artifact module unit-tests don't require aiofiles for `BytesArtifact` and `StreamArtifact`. `aiofiles` is already a top-level orchestrator dep (pyproject line 10), so importing is fine.

- [ ] **Step 4: Run test + type-check**

```bash
uv run pytest tests/worker_sdk/test_artifacts.py -v
uv run mypy src/acheron/worker_sdk/artifacts.py
uv run basedpyright src/acheron/worker_sdk/artifacts.py
```
Expected: tests pass; both type-checkers clean.

- [ ] **Step 5: Commit**

```bash
git add src/acheron/worker_sdk/artifacts.py tests/worker_sdk/test_artifacts.py
git commit -m "feat(worker_sdk): add Artifact Protocol + BytesArtifact/StreamArtifact/FileArtifact"
```

---

### Task 4: `WorkerHandler` ABC

**Files:**
- Create: `src/acheron/worker_sdk/handler.py`
- Create: `tests/worker_sdk/test_handler.py`

- [ ] **Step 1: Write the failing test**

Create `tests/worker_sdk/test_handler.py`:

```python
"""Tests for the WorkerHandler ABC."""

import pytest

from acheron.core.models import WorkerCapabilities, WorkerType, Job
from acheron.worker_sdk.artifacts import BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler


class _Echo(WorkerHandler):
    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"en"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
        )

    async def handle(self, job: Job) -> list[BytesArtifact]:
        return [BytesArtifact(filename="out.wav", content_type="audio/wav", data=b"echo")]


class TestWorkerHandlerContract:
    def test_subclass_can_be_instantiated(self) -> None:
        h = _Echo()
        assert isinstance(h, WorkerHandler)

    def test_startup_default_is_noop(self) -> None:
        import asyncio

        h = _Echo()
        asyncio.run(h.startup())  # must not raise

    def test_shutdown_default_is_noop(self) -> None:
        import asyncio

        h = _Echo()
        asyncio.run(h.shutdown())  # must not raise

    @pytest.mark.asyncio
    async def test_handle_returns_artifacts(self) -> None:
        import asyncio

        h = _Echo()
        job = Job(job_id="j1", job_type=WorkerType.TTS, payload={}, chapter_id="ch1")
        out = await h.handle(job)
        assert len(out) == 1
        assert out[0].filename == "out.wav"


class TestAbstractEnforcement:
    def test_cannot_instantiate_bare_abc(self) -> None:
        with pytest.raises(TypeError):
            WorkerHandler()  # type: ignore[abstract]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/worker_sdk/test_handler.py -v
```
Expected: `ImportError: cannot import name 'WorkerHandler'`.

- [ ] **Step 3: Implement `handler.py`**

Create `src/acheron/worker_sdk/handler.py`:

```python
"""The blueprint ABC every Layer 8 real GPU worker implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from acheron.core.models import Job, WorkerCapabilities

    from acheron.worker_sdk.artifacts import Artifact


class WorkerHandler(ABC):
    """Implemented by each worker.

    Loaded once at container boot. `startup()` runs before any job
    dispatch; `shutdown()` releases GPU memory at the edge container's
    lifespan teardown.
    """

    @abstractmethod
    def capabilities(self) -> WorkerCapabilities:
        """Return the worker's static description. No I/O — sync."""

    @abstractmethod
    async def handle(self, job: Job) -> list[Artifact]:
        """Run inference for `job` and return transport-neutral artifacts."""

    async def startup(self) -> None:
        """Optional hook: load model onto GPU, warm caches. Default: no-op."""

    async def shutdown(self) -> None:
        """Optional hook: free GPU memory. Default: no-op."""
```

- [ ] **Step 4: Run test + type-check**

```bash
uv run pytest tests/worker_sdk/test_handler.py -v
uv run mypy src/acheron/worker_sdk/handler.py
uv run basedpyright src/acheron/worker_sdk/handler.py
```
Expected: tests pass; both type-checkers clean.

- [ ] **Step 5: Re-export from package `__init__`**

Append to `src/acheron/worker_sdk/__init__.py`:

```python
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact, FileArtifact, StreamArtifact
from acheron.worker_sdk.handler import WorkerHandler

__all__ = ["Artifact", "BytesArtifact", "FileArtifact", "StreamArtifact", "WorkerHandler"]
```

Verify `import-linter` still passes — `worker_sdk` imports `core` (allowed) only and not `shell`.

- [ ] **Step 6: Commit**

```bash
git add src/acheron/worker_sdk/handler.py src/acheron/worker_sdk/__init__.py tests/worker_sdk/test_handler.py
git commit -m "feat(worker_sdk): add WorkerHandler ABC + public re-exports"
```

---

### Task 5: `WorkerSettings` (pydantic BaseSettings + env-only fields)

**Files:**
- Create: `src/acheron/worker_sdk/settings.py`
- Create: `tests/worker_sdk/test_settings.py`

- [ ] **Step 1: Write the failing test**

Create `tests/worker_sdk/test_settings.py`:

```python
"""Tests for WorkerSettings."""

import pydantic
import pytest

from acheron.worker_sdk.settings import WorkerSettings


class TestDefaults:
    def test_minimal_settings_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_WORKER__WORKER_ID", "qwen3tts-1")
        monkeypatch.setenv("ACHERON_WORKER__ORCHESTRATOR_URL", "http://orch:8000")
        s = WorkerSettings()
        assert s.worker_id == "qwen3tts-1"
        assert s.orchestrator_url == "http://orch:8000"
        assert s.listen_port == 8001
        assert s.price_source == "runpod"
        assert s.output_mode == "multipart"
        assert s.execution_timeout_s == 1800.0
        assert s.default_speaker == "Ryan"

    def test_per_language_defaults_empty_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_WORKER__WORKER_ID", "w")
        monkeypatch.setenv("ACHERON_WORKER__ORCHESTRATOR_URL", "http://o:8000")
        s = WorkerSettings()
        assert s.per_language_defaults == {}


class TestEnvOnlyFields:
    @pytest.mark.parametrize(
        "field",
        ["registration_token", "runpod_api_key", "runpod_endpoint_id"],
    )
    def test_env_only_field_rejected_by_explicit_construction(self, field: str) -> None:
        with pytest.raises(pydantic.ValidationError, match=field):
            WorkerSettings(  # type: ignore[call-arg]
                worker_id="w",
                orchestrator_url="http://o:8000",
                **{field: "secret"},
            )

    def test_env_only_field_accepted_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_WORKER__WORKER_ID", "w")
        monkeypatch.setenv("ACHERON_WORKER__ORCHESTRATOR_URL", "http://o:8000")
        monkeypatch.setenv("ACHERON_WORKER__RUNPOD_API_KEY", "rk_abc")
        monkeypatch.setenv("ACHERON_WORKER__RUNPOD_ENDPOINT_ID", "i02xupws")
        s = WorkerSettings()
        assert s.runpod_api_key == "rk_abc"
        assert s.runpod_endpoint_id == "i02xupws"


class TestValidation:
    def test_volume_mode_requires_output_volume_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_WORKER__WORKER_ID", "w")
        monkeypatch.setenv("ACHERON_WORKER__ORCHESTRATOR_URL", "http://o:8000")
        monkeypatch.setenv("ACHERON_WORKER__OUTPUT_MODE", "volume")
        with pytest.raises(pydantic.ValidationError, match="output_volume_dir"):
            WorkerSettings()

    def test_worker_id_required(self) -> None:
        with pytest.raises(pydantic.ValidationError, match="worker_id"):
            WorkerSettings()  # type: ignore[call-arg]

    def test_orchestrator_url_required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_WORKER__WORKER_ID", "w")
        with pytest.raises(pydantic.ValidationError, match="orchestrator_url"):
            WorkerSettings()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/worker_sdk/test_settings.py -v
```
Expected: `ImportError: cannot import name 'WorkerSettings'`.

- [ ] **Step 3: Implement `settings.py`**

Create `src/acheron/worker_sdk/settings.py`:

```python
"""Configuration for Acheron worker containers.

Env vars use the ``ACHERON_WORKER_`` prefix to avoid collision with the
orchestrator's own env namespace (``ACHERON_REGISTRATION_TOKEN`` etc.).

Secrets (``registration_token``, ``runpod_api_key``, ``runpod_endpoint_id``)
are env-only — rejected when passed to the constructor so they cannot
silently land in committed ``worker.yaml`` overrides or image layers.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_ONLY_FIELDS = frozenset({"registration_token", "runpod_api_key", "runpod_endpoint_id"})


class _WorkerSettingsBase(BaseSettings):
    """Settings model — env vars prefixed ``ACHERON_WORKER_``.

    Subclasses must set :attr:`model_config` with the actual env prefix.
    """

    model_config = SettingsConfigDict(
        env_prefix="ACHERON_WORKER_",
        case_sensitive=True,
        extra="ignore",
    )


class WorkerSettings(_WorkerSettingsBase):
    """Resolved worker runtime configuration.

    Seek ``from_yaml`` on :class:`config_loader` for the discovery flow.
    """

    worker_id: str
    orchestrator_url: str

    # Secrets — env-only. Validated by `_SecretsOnlyModel` below to reject
    # direct-constructor inputs.
    registration_token: str | None = None
    runpod_api_key: str | None = None
    runpod_endpoint_id: str | None = None

    listen_host: str = "0.0.0.0"
    listen_port: int = 8001

    execution_timeout_s: float = 1800.0

    output_mode: Literal["multipart", "volume"] = "multipart"
    output_volume_dir: str | None = None

    price_source: Literal["runpod", "static", "zero"] = "runpod"
    secure_cloud: bool = False
    dollars_per_hour: float | None = None
    price_cache_ttl_s: float = 3600.0

    default_speaker: str = "Ryan"
    per_language_defaults: dict[str, str] = Field(default_factory=dict)

    handler: str = ""
    model_id: str | None = None

    model_config = SettingsConfigDict(
        env_prefix="ACHERON_WORKER_",
        case_sensitive=True,
        extra="forbid",
    )

    @model_validator(mode="after")
    def _validate(self) -> WorkerSettings:
        if self.output_mode == "volume" and not self.output_volume_dir:
            msg = "output_volume_dir is required when output_mode == 'volume'"
            raise ValueError(msg)
        if self.price_source == "static" and self.dollars_per_hour is None:
            msg = "dollars_per_hour is required when price_source == 'static'"
            raise ValueError(msg)
        return self


def _reject_env_only_construction(cls: type[WorkerSettings]) -> None:
    """Patch ``__init__`` to forbid passing env-only fields explicitly."""


class _SecretsRejection(BaseModel):
    """Verification wrapper used when constructing WorkerSettings directly.

    Workers obtain settings via :func:`acheron.worker_sdk.config_loader.from_yaml`
    or :meth:`WorkerSettings` reading the environment. Direct constructor
    calls with secret fields must fail loudly so secrets never leak from YAML
    overrides into image layers.
    """

    model_config = ConfigDict(extra="forbid")
```

The simple pydantic approach for env-only rejection is tricky. Refactor `settings.py` to be cleaner by using `model_validator(mode="before")` to inspect raw inputs: rewrite `settings.py` to:

```python
"""Configuration for Acheron worker containers.

Env vars use the ``ACHERON_WORKER_`` prefix to avoid collision with the
orchestrator's own env namespace (``ACHERON_REGISTRATION_TOKEN`` etc.).

Secrets (``registration_token``, ``runpod_api_key``, ``runpod_endpoint_id``)
are env-only — rejected when passed to the constructor so they cannot
silently land in committed ``worker.yaml`` overrides or image layers.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from pydantic.settings import BaseSettings, SettingsConfigDict

_ENV_ONLY_FIELDS: frozenset[str] = frozenset({
    "registration_token",
    "runpod_api_key",
    "runpod_endpoint_id",
})


class WorkerSettings(BaseSettings):
    """Resolved worker runtime configuration."""

    worker_id: str
    orchestrator_url: str

    registration_token: str | None = None
    runpod_api_key: str | None = None
    runpod_endpoint_id: str | None = None

    listen_host: str = "0.0.0.0"
    listen_port: int = 8001

    execution_timeout_s: float = 1800.0

    output_mode: Literal["multipart", "volume"] = "multipart"
    output_volume_dir: str | None = None

    price_source: Literal["runpod", "static", "zero"] = "runpod"
    secure_cloud: bool = False
    dollars_per_hour: float | None = None
    price_cache_ttl_s: float = 3600.0

    default_speaker: str = "Ryan"
    per_language_defaults: dict[str, str] = Field(default_factory=dict)

    handler: str = ""
    model_id: str | None = None

    model_config = SettingsConfigDict(
        env_prefix="ACHERON_WORKER_",
        case_sensitive=True,
        extra="forbid",
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_env_only_in_constructor(cls, data: Any) -> Any:
        if isinstance(data, Mapping):
            offenders = _ENV_ONLY_FIELDS & data.keys()
            if offenders:
                msg = (
                    "Fields are env-only and cannot be set via constructor or YAML: "
                    f"{sorted(offenders)}. Set them via ACHERON_WORKER_* env vars."
                )
                raise ValueError(msg)
        return data

    @model_validator(mode="after")
    def _validate_composite(self) -> WorkerSettings:
        if self.output_mode == "volume" and not self.output_volume_dir:
            msg = "output_volume_dir is required when output_mode == 'volume'"
            raise ValueError(msg)
        if self.price_source == "static" and self.dollars_per_hour is None:
            msg = "dollars_per_hour is required when price_source == 'static'"
            raise ValueError(msg)
        return self
```

Replace the earlier sketch with this final version (overwrite the file).

- [ ] **Step 4: Run test + type-check**

```bash
uv run pytest tests/worker_sdk/test_settings.py -v
uv run mypy src/acheron/worker_sdk/settings.py
uv run basedpyright src/acheron/worker_sdk/settings.py
```
Expected: tests pass; type-checkers clean.

- [ ] **Step 5: Re-export**

Append to `src/acheron/worker_sdk/__init__.py` (extend `__all__`):

```python
from acheron.worker_sdk.settings import WorkerSettings

__all__ = [
    "Artifact",
    "BytesArtifact",
    "FileArtifact",
    "StreamArtifact",
    "WorkerHandler",
    "WorkerSettings",
]
```

- [ ] **Step 6: Commit**

```bash
git add src/acheron/worker_sdk/settings.py src/acheron/worker_sdk/__init__.py tests/worker_sdk/test_settings.py
git commit -m "feat(worker_sdk): add WorkerSettings with env-only secrets rejection"
```

---

### Task 6: Config loader (YAML discovery + env override)

**Files:**
- Create: `src/acheron/worker_sdk/config_loader.py`
- Create: `tests/worker_sdk/test_config_loader.py`

- [ ] **Step 1: Write the failing test**

Create `tests/worker_sdk/test_config_loader.py`:

```python
"""Tests for WorkerSettings YAML discovery + env override."""

from pathlib import Path

import pytest

from acheron.worker_sdk.config_loader import load_settings


class TestDiscoveryOrder:
    def test_worker_config_env_var_wins_absolute(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        yaml_path = tmp_path / "explicit.yaml"
        yaml_path.write_text("worker_id: fromfile\norchestrator_url: http://o:8000\n")
        monkeypatch.setenv("WORKER_CONFIG", str(yaml_path))
        s = load_settings()
        assert s.worker_id == "fromfile"

    def test_worker_name_worker_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "qwen3tts.worker.yaml").write_text(
            "worker_id: fromfile\norchestrator_url: http://o:8000\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("WORKER_NAME", "qwen3tts")
        monkeypatch.delenv("WORKER_CONFIG", raising=False)
        s = load_settings()
        assert s.worker_id == "fromfile"

    def test_worker_yaml_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "worker.yaml").write_text(
            "worker_id: fromfile\norchestrator_url: http://o:8000\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("WORKER_CONFIG", raising=False)
        monkeypatch.delenv("WORKER_NAME", raising=False)
        s = load_settings()
        assert s.worker_id == "fromfile"

    def test_env_only_fallback(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("ACHERON_WORKER__WORKER_ID", "envonly")
        monkeypatch.setenv("ACHERON_WORKER__ORCHESTRATOR_URL", "http://o:8000")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("WORKER_CONFIG", raising=False)
        monkeypatch.delenv("WORKER_NAME", raising=False)
        s = load_settings()
        assert s.worker_id == "envonly"


class TestEnvOverrideWins:
    def test_env_var_overrides_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        yaml_path = tmp_path / "explicit.yaml"
        yaml_path.write_text(
            "worker_id: fromfile\norchestrator_url: http://o:8000\ndefault_speaker: Vivian\n"
        )
        monkeypatch.setenv("WORKER_CONFIG", str(yaml_path))
        monkeypatch.setenv("ACHERON_WORKER__DEFAULT_SPEAKER", "Ryan")
        s = load_settings()
        assert s.default_speaker == "Ryan"  # env wins


class TestSecretRejection:
    def test_secret_in_yaml_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        yaml_path = tmp_path / "explicit.yaml"
        yaml_path.write_text(
            "worker_id: fromfile\norchestrator_url: http://o:8000\nrunpod_api_key: rk_secret\n"
        )
        monkeypatch.setenv("WORKER_CONFIG", str(yaml_path))
        with pytest.raises(ValueError, match="env-only"):
            load_settings()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/worker_sdk/test_config_loader.py -v
```
Expected: `ImportError: cannot import name 'load_settings'`.

- [ ] **Step 3: Implement `config_loader.py`**

Create `src/acheron/worker_sdk/config_loader.py`:

```python
"""Worker configuration discovery and loading.

Resolution order (first match wins):
  1. ``WORKER_CONFIG`` env var → explicit path (absolute or relative).
  2. ``<cwd>/<worker_name>.worker.yaml`` — ``worker_name`` from
     ``WORKER_NAME`` env var or the current directory's basename.
  3. ``<cwd>/worker.yaml``.
  4. Env vars only (no file).

Env vars override YAML values on conflict. Secrets are rejected when
present in YAML (fail-loud to keep them out of image layers).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from acheron.worker_sdk.settings import WorkerSettings, _ENV_ONLY_FIELDS


def _candidate_paths() -> list[Path]:
    """Return the ordered list of candidate YAML config paths."""
    candidates: list[Path] = []
    explicit = os.environ.get("WORKER_CONFIG")
    if explicit:
        candidates.append(Path(explicit))
    worker_name = os.environ.get("WORKER_NAME") or Path.cwd().name
    name_yaml = Path.cwd() / f"{worker_name}.worker.yaml"
    if name_yaml not in candidates:
        candidates.append(name_yaml)
    generic_yaml = Path.cwd() / "worker.yaml"
    if generic_yaml not in candidates:
        candidates.append(generic_yaml)
    return candidates


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        msg = f"Worker config {path} must be a YAML mapping, got {type(data).__name__}"
        raise TypeError(msg)
    return data


def load_settings() -> WorkerSettings:
    """Discover the worker config and build :class:`WorkerSettings`."""
    yaml_data: dict[str, Any] = {}
    for path in _candidate_paths():
        if path.is_file():
            yaml_data = _load_yaml(path)
            break

    offenders = _ENV_ONLY_FIELDS & yaml_data.keys()
    if offenders:
        msg = (
            "Fields are env-only and cannot be set via constructor or YAML: "
            f"{sorted(offenders)}. Set them via ACHERON_WORKER_* env vars."
        )
        raise ValueError(msg)

    try:
        return WorkerSettings(**yaml_data)
    except ValidationError as exc:
        for err in exc.errors():
            if err.get("type") == "value_error":
                raise ValueError(err["msg"]) from exc
        raise
```

- [ ] **Step 4: Run test + type-check**

```bash
uv run pytest tests/worker_sdk/test_config_loader.py -v
uv run mypy src/acheron/worker_sdk/config_loader.py
uv run basedpyright src/acheron/worker_sdk/config_loader.py
```
Expected: tests pass; type-checkers clean.

- [ ] **Step 5: Commit**

```bash
git add src/acheron/worker_sdk/config_loader.py tests/worker_sdk/test_config_loader.py
git commit -m "feat(worker_sdk): add YAML discovery + env override config loader"
```

---

### Task 7: Pydantic schemas for `/execute` requests and responses

**Files:**
- Create: `src/acheron/worker_sdk/schemas.py`
- Create: `tests/worker_sdk/test_schemas.py`

- [ ] **Step 1: Write the failing test**

Create `tests/worker_sdk/test_schemas.py`:

```python
"""Tests for the /execute request/response pydantic schemas."""

import pytest
from pydantic import ValidationError

from acheron.worker_sdk.schemas import ExecuteError, ExecuteRequest


class TestExecuteRequest:
    def test_full_payload_validates(self) -> None:
        body = ExecuteRequest.model_validate(
            {
                "job_id": "j-1",
                "job_type": "tts",
                "payload": {"chapter_id": "ch1", "chunks": [{"text": "hola"}], "target_language": "es"},
                "chapter_id": "ch1",
                "sequence_ids": [0, 1],
            }
        )
        assert body.job_id == "j-1"
        assert body.job_type == "tts"
        assert body.chapter_id == "ch1"
        assert body.sequence_ids == [0, 1]

    def test_sequence_ids_optional(self) -> None:
        body = ExecuteRequest.model_validate(
            {"job_id": "j-1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"}
        )
        assert body.sequence_ids is None

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            ExecuteRequest.model_validate(
                {"job_id": "j", "job_type": "tts", "payload": {}, "chapter_id": "c", "boom": 1}
            )


class TestExecuteError:
    def test_shape(self) -> None:
        e = ExecuteError.model_validate({"status": "failed", "error": "model OOM"})
        assert e.status == "failed"
        assert e.error == "model OOM"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/worker_sdk/test_schemas.py -v
```
Expected: `ImportError: cannot import name 'ExecuteRequest'`.

- [ ] **Step 3: Implement `schemas.py`**

Create `src/acheron/worker_sdk/schemas.py`:

```python
"""Pydantic schemas for the worker /execute request and error response.

Strict (``extra="forbid"``) so client typos fail loudly — matches the
orchestrator's API schema convention.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from acheron.core.models import JsonValue  # noqa: TC001  (pydantic needs the runtime type)


class ExecuteRequest(BaseModel):
    """POST /execute body — mirrors core.models.Job."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    job_type: str
    payload: dict[str, JsonValue]
    chapter_id: str
    sequence_ids: list[int] | None = None


class ExecuteError(BaseModel):
    """JSON body returned when the handler raises (no artifacts emitted)."""

    model_config = ConfigDict(extra="forbid")

    status: str
    error: str
```

- [ ] **Step 4: Run test + type-check**

```bash
uv run pytest tests/worker_sdk/test_schemas.py -v
uv run mypy src/acheron/worker_sdk/schemas.py
uv run basedpyright src/acheron/worker_sdk/schemas.py
```
Expected: tests pass; type-checkers clean.

- [ ] **Step 5: Commit**

```bash
git add src/acheron/worker_sdk/schemas.py tests/worker_sdk/test_schemas.py
git commit -m "feat(worker_sdk): add pydantic /execute request + error schemas"
```

---

### Task 8: `register_with_orchestrator` client

**Files:**
- Create: `src/acheron/worker_sdk/registration.py`
- Create: `tests/worker_sdk/test_registration.py`

- [ ] **Step 1: Write the failing test**

Create `tests/worker_sdk/test_registration.py`:

```python
"""Tests for Orchestrator self-registration."""

import httpx
import pytest
import respx

from acheron.core.models import WorkerCapabilities, WorkerType
from acheron.worker_sdk.registration import register_with_orchestrator


def _caps() -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TTS,
        supported_languages_in=frozenset({"en"}),
        supported_languages_out=frozenset({"en"}),
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"wav"}),
        max_payload_bytes=None,
        batch_capable=True,
        model_source="huggingface:Qwen/Qwen3-TTS",
        metadata={"speakers": ["Ryan"]},
    )


class TestRegisterWithOrchestrator:
    @respx.mock
    @pytest.mark.asyncio
    async def test_posts_payload_and_returns_on_201(self) -> None:
        route = respx.post("http://orch:8000/workers").mock(return_value=httpx.Response(201, json={}))
        async with httpx.AsyncClient() as client:
            await register_with_orchestrator(
                client=client,
                orchestrator_url="http://orch:8000",
                token="tok",
                worker_id="qwen3tts-1",
                endpoint="http://edge:8001",
                transport="http",
                capabilities=_caps(),
            )
        assert route.called
        body = route.calls.last.request.content.decode()
        assert "qwen3tts-1" in body
        assert "http://edge:8001" in body
        assert "tts" in body
        headers = route.calls.last.request.headers
        assert headers["authorization"] == "Bearer tok"

    @respx.mock
    @pytest.mark.asyncio
    async def test_retries_until_orchestrator_ready(self) -> None:
        route = respx.post("http://orch:8000/workers")
        route.mock(side_effect=[httpx.ConnectError("refused"), httpx.Response(201, json={})])
        async with httpx.AsyncClient() as client:
            await register_with_orchestrator(
                client=client,
                orchestrator_url="http://orch:8000",
                token=None,
                worker_id="w",
                endpoint="http://w:8001",
                transport="http",
                capabilities=_caps(),
                retry_delay=0.0,
            )
        assert route.call_count == 2

    @respx.mock
    @pytest.mark.asyncio
    async def test_gives_up_after_max_retries(self) -> None:
        respx.post("http://orch:8000/workers").mock(side_effect=httpx.ConnectError("refused"))
        async with httpx.AsyncClient() as client:
            with pytest.raises(httpx.ConnectError):
                await register_with_orchestrator(
                    client=client,
                    orchestrator_url="http://orch:8000",
                    token=None,
                    worker_id="w",
                    endpoint="http://w:8001",
                    transport="http",
                    capabilities=_caps(),
                    retries=2,
                    retry_delay=0.0,
                )
```

Also add a small fixture for retry timing to `tests/worker_sdk/conftest.py`:

```python
"""conftest for tests/worker_sdk/."""

import pytest


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make asyncio.sleep + time.monotonic cheap in tests."""
    import asyncio

    async def _instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/worker_sdk/test_registration.py -v
```
Expected: `ImportError: cannot import name 'register_with_orchestrator'`.

- [ ] **Step 3: Implement `registration.py`**

Create `src/acheron/worker_sdk/registration.py`:

```python
"""Self-registration client for the edge container.

Posts ``WorkerRegistrationRequest`` to the orchestrator's ``POST /workers``
route, with exponential backoff until the orchestrator is reachable. Tags
the worker's capabilities metadata with ``health_provider`` and
``health_endpoint_id`` for the existing RunPodHealthProvider cold-start
detection plumbing (Layer 11).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from acheron.core.models import WorkerCapabilities

logger = logging.getLogger(__name__)


async def register_with_orchestrator(
    *,
    client: httpx.AsyncClient,
    orchestrator_url: str,
    token: str | None,
    worker_id: str,
    endpoint: str,
    transport: str,
    capabilities: WorkerCapabilities,
    retries: int = 30,
    retry_delay: float = 2.0,
) -> None:
    """Register the worker, retrying until the orchestrator is reachable."""
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "worker_id": worker_id,
        "endpoint": endpoint,
        "transport": transport,
        "capabilities": _caps_to_dict(capabilities),
    }

    url = f"{orchestrator_url.rstrip('/')}/workers"
    attempt = 0
    while True:
        try:
            resp = await client.post(url, json=payload, headers=headers, timeout=10.0)
            resp.raise_for_status()
        except (httpx.HTTPError, OSError) as exc:
            attempt += 1
            if attempt >= retries:
                msg = f"Could not register worker {worker_id} after {retries} attempts"
                raise httpx.ConnectError(msg) from exc
            logger.debug("Orchestrator not ready (%s); retrying...", exc)
            await asyncio.sleep(retry_delay)
        else:
            logger.info("Registered %s with orchestrator", worker_id)
            return


def _caps_to_dict(caps: WorkerCapabilities) -> dict[str, object]:
    """Serialize WorkerCapabilities for POST /workers."""
    metadata = dict(caps.metadata)
    return {
        "worker_type": caps.worker_type.value,
        "supported_languages_in": sorted(caps.supported_languages_in),
        "supported_languages_out": sorted(caps.supported_languages_out),
        "supported_formats_in": sorted(caps.supported_formats_in),
        "supported_formats_out": sorted(caps.supported_formats_out),
        "max_payload_bytes": caps.max_payload_bytes,
        "batch_capable": caps.batch_capable,
        "model_source": caps.model_source,
        "metadata": metadata,
    }
```

- [ ] **Step 4: Run test + type-check**

```bash
uv run pytest tests/worker_sdk/test_registration.py -v
uv run mypy src/acheron/worker_sdk/registration.py
uv run basedpyright src/acheron/worker_sdk/registration.py
```
Expected: tests pass; type-checkers clean.

- [ ] **Step 5: Commit**

```bash
git add src/acheron/worker_sdk/registration.py tests/worker_sdk/test_registration.py tests/worker_sdk/conftest.py
git commit -m "feat(worker_sdk): add register_with_orchestrator client"
```

---

### Task 9: `PriceSource` Protocol + `ZeroPrice` + `StaticPrice`

**Files:**
- Create: `src/acheron/worker_sdk/pricing.py`
- Create: `tests/worker_sdk/test_pricing.py`

- [ ] **Step 1: Write the failing test**

Create `tests/worker_sdk/test_pricing.py`:

```python
"""Tests for the PriceSource variants (ZeroPrice, StaticPrice)."""

import pytest

from acheron.worker_sdk.pricing import PriceEstimate, StaticPrice, ZeroPrice, to_cost_basis


class TestZeroPrice:
    @pytest.mark.asyncio
    async def test_returns_zero_with_static_label(self) -> None:
        est = await ZeroPrice().estimate(gpu_seconds=10.0)
        assert est.cost == 0.0
        assert to_cost_basis(est).value == "static"


class TestStaticPrice:
    @pytest.mark.asyncio
    async def test_computes_cost_from_rate(self) -> None:
        est = await StaticPrice(dollars_per_hour=0.69).estimate(gpu_seconds=3600.0)
        assert est.cost == 0.69
        assert to_cost_basis(est).value == "static"

    @pytest.mark.asyncio
    async def test_zero_gpu_seconds_yields_zero(self) -> None:
        est = await StaticPrice(dollars_per_hour=0.69).estimate(gpu_seconds=0.0)
        assert est.cost == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/worker_sdk/test_pricing.py -v
```
Expected: `ImportError: cannot import name 'PriceEstimate'`.

- [ ] **Step 3: Implement the protocol + Zero/Static variants**

Create `src/acheron/worker_sdk/pricing.py` (the `RunPodPrice` variant lands in Task 10):

```python
"""Price discovery for Layer 8 workers — fault-tolerant, never blocks a job.

`PriceSource` is the seam. Three variants; workers compose the right one.
The backend calls ``await price_source.estimate(gpu_seconds)`` after each
handle() and populates ``JobMetrics.cost_estimate`` + ``cost_basis`` from
the returned :class:`PriceEstimate`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from acheron.core.models import CostBasis


@dataclass(frozen=True)
class PriceEstimate:
    """Outcome of a price query.

    ``cost is None`` means unknown (provider API unavailable and no cache);
    ``cost == 0.0`` means an actual $0 (stub/local/ZeroPrice).
    """

    cost: float | None
    reason: str | None = None


@runtime_checkable
class PriceSource(Protocol):
    """Provider-agnostic price source."""

    async def estimate(self, gpu_seconds: float) -> PriceEstimate: ...


@dataclass(frozen=True)
class ZeroPrice:
    """Stubs/local — no cost tracking. Reports $0 with STATIC basis."""

    async def estimate(self, gpu_seconds: float) -> PriceEstimate:
        return PriceEstimate(cost=0.0, reason="zero (stub/local)")


@dataclass(frozen=True)
class StaticPrice:
    """Fixed $/hr from config — operator opted out of API rate lookup."""

    dollars_per_hour: float

    async def estimate(self, gpu_seconds: float) -> PriceEstimate:
        cost = round(gpu_seconds * self.dollars_per_hour / 3600.0, 6)
        return PriceEstimate(cost=cost, reason="static config")


def to_cost_basis(estimate: PriceEstimate) -> CostBasis:
    """Map a :class:`PriceEstimate` to a wire :class:`CostBasis` value.

    RunPodPrice sets ``reason`` to a sentinel string that distinguishes the
    fresh-measurement case from the cached case; the worker-side mapping
    preserves the spec's ``MEASURED`` vs ``CACHED`` distinction.
    """
    if estimate.cost is None:
        return CostBasis.UNKNOWN
    if estimate.reason == "runpod:measured":
        return CostBasis.MEASURED
    if estimate.reason == "runpod:cached":
        return CostBasis.CACHED
    # "zero (stub/local)", "static config", and any other reason
    return CostBasis.STATIC
```

- [ ] **Step 4: Run test + type-check**

```bash
uv run pytest tests/worker_sdk/test_pricing.py -v
uv run mypy src/acheron/worker_sdk/pricing.py
uv run basedpyright src/acheron/worker_sdk/pricing.py
```
Expected: tests pass; type-checkers clean.

- [ ] **Step 5: Commit**

```bash
git add src/acheron/worker_sdk/pricing.py tests/worker_sdk/test_pricing.py
git commit -m "feat(worker_sdk): add PriceSource Protocol + ZeroPrice/StaticPrice"
```

---

### Task 10: `RunPodPrice` (GraphQL + fault tolerance + cost basis mapping)

**Files:**
- Modify: `src/acheron/worker_sdk/pricing.py`
- Modify: `tests/worker_sdk/test_pricing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/worker_sdk/test_pricing.py`:

```python
import httpx
import respx

from acheron.worker_sdk.pricing import RunPodPrice


_MYSELF_QUERY = "query { myself { endpoints { id gpuIds } } }"
_GPUTYPES_QUERY = 'query { gpuTypes(input: {id: "NVIDIA GeForce RTX 3090"}) { lowestPrice(input: {gpuCount: 1, secureCloud: false}) { uninterruptablePrice } } }'


def _graphql_response(data: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, json={"data": data})


class TestRunPodPrice:
    @respx.mock
    @pytest.mark.asyncio
    async def test_measured_when_fresh_refresh_succeeds(self) -> None:
        # Two graphql calls are made per estimate() when the cache is cold:
        # 1. myself { endpoints { id gpuIds } }
        # 2. gpuTypes(input: {id: <gpu>}) { lowestPrice { uninterruptablePrice } }
        routes = respx.post("https://api.runpod.io/graphql")
        routes.mock(
            side_effect=[
                _graphql_response(
                    {"myself": {"endpoints": [{"id": "eid", "gpuIds": "NVIDIA GeForce RTX 3090"}]}}
                ),
                _graphql_response(
                    {"gpuTypes": [{"lowestPrice": {"uninterruptablePrice": 0.69}}]}
                ),
            ]
        )
        price = RunPodPrice(api_key="k", endpoint_id="eid", secure_cloud=False, cache_ttl_s=3600.0)
        est = await price.estimate(gpu_seconds=3600.0)
        assert est.cost == 0.69
        assert est.reason == "runpod:measured"
        assert to_cost_basis(est).value == "measured"

    @respx.mock
    @pytest.mark.asyncio
    async def test_cached_when_refresh_fails_after_a_prior_success(self) -> None:
        # First estimate() succeeds (cache populated); second one fails refresh,
        # serves the cached rate under CACHED basis.
        routes = respx.post("https://api.runpod.io/graphql")
        routes.mock(
            side_effect=[
                _graphql_response(
                    {"myself": {"endpoints": [{"id": "eid", "gpuIds": "NVIDIA GeForce RTX 3090"}]}}
                ),
                _graphql_response(
                    {"gpuTypes": [{"lowestPrice": {"uninterruptablePrice": 0.69}}]}
                ),
                httpx.ConnectError("boom"),  # refresh attempt on the second estimate()
                httpx.ConnectError("boom"),
            ]
        )
        price = RunPodPrice(api_key="k", endpoint_id="eid", secure_cloud=False, cache_ttl_s=0.0)  # ttl=0 -> always refresh
        est1 = await price.estimate(gpu_seconds=3600.0)
        assert est1.reason == "runpod:measured"
        est2 = await price.estimate(gpu_seconds=3600.0)
        assert est2.cost == 0.69
        assert est2.reason == "runpod:cached"
        assert to_cost_basis(est2).value == "cached"

    @respx.mock
    @pytest.mark.asyncio
    async def test_unknown_when_never_refreshed(self) -> None:
        respx.post("https://api.runpod.io/graphql").mock(side_effect=httpx.ConnectError("boom"))
        price = RunPodPrice(api_key="k", endpoint_id="eid", secure_cloud=False)
        est = await price.estimate(gpu_seconds=3600.0)
        assert est.cost is None
        assert "unavailable" in (est.reason or "")
        assert to_cost_basis(est).value == "unknown"

    @respx.mock
    @pytest.mark.asyncio
    async def test_api_failure_does_not_propagate(self) -> None:
        respx.post("https://api.runpod.io/graphql").mock(return_value=httpx.Response(500))
        price = RunPodPrice(api_key="k", endpoint_id="eid", secure_cloud=False)
        try:
            est = await price.estimate(gpu_seconds=3600.0)
        except Exception:
            pytest.fail("RunPodPrice.estimate must not raise on API failure")
        assert est.cost is None or est.cost is not None  # never raises
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/worker_sdk/test_pricing.py::TestRunPodPrice -v
```
Expected: `ImportError: cannot import name 'RunPodPrice'`.

- [ ] **Step 3: Implement `RunPodPrice`**

Append to `src/acheron/worker_sdk/pricing.py`:

```python
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class RunPodPrice:
    """Pulls $/hr from RunPod GraphQL using the endpoint's configured GPU.

    RunPod is the single source of truth for the GPU type — the worker does
    not configure ``gpu_type``. ``_refresh_rate()`` makes two GraphQL calls:
    (1) read the endpoint's ``gpuIds`` via ``myself { endpoints { id gpuIds } }``,
    (2) resolve ``uninterruptablePrice`` via ``gpuTypes(input: {id: $gpu_id})``.
    Changing the GPU on the RunPod endpoint takes effect on the next
    cache refresh (``cache_ttl_s``).
    """

    api_key: str
    endpoint_id: str
    secure_cloud: bool = False
    cache_ttl_s: float = 3600.0

    _rate: float | None = field(default=None, init=False)
    _rate_fetched_at: float = field(default=0.0, init=False)

    async def _refresh_rate(self, client: httpx.AsyncClient) -> bool:
        """Hit the GraphQL endpoint; populate ``_rate``. Return False on any failure."""
        try:
            gpu_id = await self._fetch_gpu_id(client)
            if gpu_id is None:
                return False
            rate = await self._fetch_uninterruptable_price(client, gpu_id)
            if rate is None:
                return False
            self._rate = rate
            self._rate_fetched_at = time.monotonic()
            return True
        except (httpx.HTTPError, OSError, KeyError, ValueError, TypeError):
            return False

    async def _fetch_gpu_id(self, client: httpx.AsyncClient) -> str | None:
        query = "query { myself { endpoints { id gpuIds } } }"
        resp = await self._post_graphql(client, query)
        endpoints = resp["data"]["myself"]["endpoints"]
        for ep in endpoints:
            if ep["id"] == self.endpoint_id:
                return ep["gpuIds"]
        return None

    async def _fetch_uninterruptable_price(self, client: httpx.AsyncClient, gpu_id: str) -> float | None:
        query = (
            "query($id: String!, $secure: Boolean!) {"
            "  gpuTypes(input: {id: $id}) {"
            "    lowestPrice(input: {gpuCount: 1, secureCloud: $secure}) { uninterruptablePrice }"
            "  }"
            "}"
        )
        resp = await self._post_graphql(
            client,
            query,
            variables={"id": gpu_id, "secure": self.secure_cloud},
        )
        return float(resp["data"]["gpuTypes"][0]["lowestPrice"]["uninterruptablePrice"])

    async def _post_graphql(
        self,
        client: httpx.AsyncClient,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resp = await client.post(
            "https://api.runpod.io/graphql",
            params={"api_key": self.api_key},
            json={"query": query, "variables": variables or {}},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()

    async def estimate(self, gpu_seconds: float) -> PriceEstimate:
        stale = self._rate is None or (time.monotonic() - self._rate_fetched_at) > self.cache_ttl_s
        if stale:
            async with httpx.AsyncClient() as client:
                refreshed = await self._refresh_rate(client)
        if self._rate is None:
            return PriceEstimate(
                cost=None, reason=f"runpod pricing unavailable for endpoint {self.endpoint_id}"
            )
        cost = round(gpu_seconds * self._rate / 3600.0, 6)
        if stale and not refreshed:
            return PriceEstimate(cost=cost, reason="runpod:cached")
        return PriceEstimate(cost=cost, reason="runpod:measured")
```

Note: the `stale` flag captures *whether the cache was past TTL when `estimate()` was entered*. If `stale and not refreshed`, the value served is the cached rate under CACHED basis; if `stale and refreshed` (or not stale at all), it is MEASURED. The first call from a fresh instance (no prior cache) that succeeds reports MEASURED; a subsequent call where refresh fails reports CACHED.

Update the module docstring comment to reflect this. Also, the existing `stale` variable is assigned but its `refreshed` value isn't read in the success branch — restructure slightly:

Replace the body of `estimate()` with:

```python
    async def estimate(self, gpu_seconds: float) -> PriceEstimate:
        now = time.monotonic()
        stale = self._rate is None or (now - self._rate_fetched_at) > self.cache_ttl_s
        refreshed: bool | None = None
        if stale:
            async with httpx.AsyncClient() as client:
                refreshed = await self._refresh_rate(client)
        if self._rate is None:
            return PriceEstimate(
                cost=None, reason=f"runpod pricing unavailable for endpoint {self.endpoint_id}"
            )
        cost = round(gpu_seconds * self._rate / 3600.0, 6)
        if refreshed is False:
            # Cache was stale and refresh just failed; serve cached rate under CACHED.
            return PriceEstimate(cost=cost, reason="runpod:cached")
        return PriceEstimate(cost=cost, reason="runpod:measured")
```

- [ ] **Step 4: Run test + type-check**

```bash
uv run pytest tests/worker_sdk/test_pricing.py -v
uv run mypy src/acheron/worker_sdk/pricing.py
uv run basedpyright src/acheron/worker_sdk/pricing.py
```
Expected: tests pass; type-checkers clean.

- [ ] **Step 5: Commit**

```bash
git add src/acheron/worker_sdk/pricing.py tests/worker_sdk/test_pricing.py
git commit -m "feat(worker_sdk): add RunPodPrice with GraphQL endpoint-discovered GPU + fault tolerance"
```

---

### Task 11: `make_runpod_handler` cloud adapter

**Files:**
- Create: `src/acheron/worker_sdk/cloud.py`
- Create: `tests/worker_sdk/test_cloud.py`

- [ ] **Step 1: Write the failing test**

Create `tests/worker_sdk/test_cloud.py`:

```python
"""Tests for the make_runpod_handler cloud adapter."""

import asyncio
from typing import Any

import pytest

from acheron.core.models import Job, WorkerCapabilities, WorkerType
from acheron.worker_sdk.artifacts import BytesArtifact
from acheron.worker_sdk.cloud import make_runpod_handler
from acheron.worker_sdk.handler import WorkerHandler


class _Stub(WorkerHandler):
    def __init__(self) -> None:
        self.last_input: dict[str, Any] = {}

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset(),
            supported_languages_out=frozenset(),
            supported_formats_in=frozenset(),
            supported_formats_out=frozenset(),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
        )

    async def handle(self, job: Job) -> list[BytesArtifact]:
        self.last_input = dict(job.payload)
        return [BytesArtifact(filename="out.wav", content_type="audio/wav", data=b"audio")]


class TestMakeRunpodHandler:
    @pytest.mark.asyncio
    async def test_adapter_returns_runpod_payload_dict(self) -> None:
        h = _Stub()
        adapter = make_runpod_handler(h)
        raw = {"input": {"job_id": "j1", "job_type": "tts", "payload": {"text": "hi"}, "chapter_id": "ch1"}}
        out = await adapter(raw)
        assert "artifacts" in out
        assert len(out["artifacts"]) == 1
        a = out["artifacts"][0]
        assert a["filename"] == "out.wav"
        assert a["content_type"] == "audio/wav"
        assert a["data"] == "audio"  # str — base64-encoded per the wire convention

    @pytest.mark.asyncio
    async def test_adapter_propagates_input_payload_to_handler(self) -> None:
        h = _Stub()
        adapter = make_runpod_handler(h)
        raw = {"input": {"job_id": "j1", "job_type": "tts", "payload": {"text": "hi"}, "chapter_id": "ch1"}}
        await adapter(raw)
        assert h.last_input == {"text": "hi"}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/worker_sdk/test_cloud.py -v
```
Expected: `ImportError: cannot import name 'make_runpod_handler'`.

- [ ] **Step 3: Implement `cloud.py`**

Create `src/acheron/worker_sdk/cloud.py`:

```python
"""Cloud-side adapter wrapping a WorkerHandler as a RunPod-compatible callable.

``runpod.serverless.start({"handler": fn})`` expects ``fn(job: dict) -> dict``.
We wrap a :class:`WorkerHandler` so the same handler module runs inside
the RunPod serverless runtime image — its ``handle()`` contract is
identical whether the caller is the cloud-side handler loop or (in a
future sub-project) a local edge runtime.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Awaitable, Callable
from typing import Any, cast

from acheron.core.models import Job, JobType, WorkerType
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact, FileArtifact, StreamArtifact
from acheron.worker_sdk.handler import WorkerHandler


def make_runpod_handler(handler: WorkerHandler) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return a RunPod-compatible async callable wrapping ``handler``."""

    async def _rp_handler(runpod_job: dict[str, Any]) -> dict[str, Any]:
        job = _deserialise_job(runpod_job["input"])
        artifacts = await handler.handle(job)
        return {"artifacts": [await _serialise(a) for a in artifacts]}

    return _rp_handler


def _deserialise_job(input_payload: dict[str, Any]) -> Job:
    return Job(
        job_id=input_payload["job_id"],
        job_type=WorkerType(input_payload["job_type"]),
        payload=cast(dict[str, Any], input_payload.get("payload", {})),
        chapter_id=input_payload.get("chapter_id", ""),
        sequence_ids=tuple(input_payload["sequence_ids"]) if input_payload.get("sequence_ids") else None,
    )


async def _serialise(artifact: Artifact) -> dict[str, Any]:
    body = b"".join([chunk async for chunk in artifact.stream()])
    return {
        "filename": artifact.filename,
        "content_type": artifact.content_type,
        "data": base64.b64encode(body).decode("ascii"),
        "metadata": artifact.metadata,
    }
```

Note: `JobType` is not a real import — remove that line. The correct import is just `Job` and `WorkerType`. Fix the imports to:

```python
from acheron.core.models import Job, WorkerType
```

Drop the `JobType` import (it doesn't exist — `WorkerType` covers it).

- [ ] **Step 4: Run test + type-check**

```bash
uv run pytest tests/worker_sdk/test_cloud.py -v
uv run mypy src/acheron/worker_sdk/cloud.py
uv run basedpyright src/acheron/worker_sdk/cloud.py
```
Expected: tests pass; type-checkers clean.

- [ ] **Step 5: Commit**

```bash
git add src/acheron/worker_sdk/cloud.py tests/worker_sdk/test_cloud.py
git commit -m "feat(worker_sdk): add make_runpod_handler cloud adapter"
```

---

### Task 12: Internal `_runpod_client` (submit + poll + collect)

**Files:**
- Create: `src/acheron/worker_sdk/_runpod_client.py`
- Create: `tests/worker_sdk/test_runpod_client.py`

**Note:** the real `runpod` SDK is imported lazily inside `_submit_and_await` so unit tests can mock it via `monkeypatch.setattr` without the SDK installed. Add `runpod` to dependencies only in this task — update `pyproject.toml`:

```diff
 dependencies = [
     "aiofiles~=24.0",
     "click~=8.4",
     "fastapi~=0.137",
     "grpcio~=1.81",
     "grpcio-health-checking~=1.81",
     "httpx~=0.28",
     "nltk~=3.9",
     "pydantic~=2.13",
     "pydantic-settings~=2.14",
     "pyyaml~=6.0",
     "redis~=7.0",
+    "runpod~=1.7",
     "rich~=14.3",
     "tenacity~=9.1",
     "uvicorn[standard]~=0.49",
 ]
```

- [ ] **Step 1: Write the failing test**

Create `tests/worker_sdk/test_runpod_client.py`:

```python
"""Tests for the internal RunPod client wrapper.

Uses an injected fake ``runpod.Endpoint`` to avoid the heavy SDK dependency.
"""

import pytest

from acheron.worker_sdk._runpod_client import RunPodClient, RunPodJobResult


class _FakeEndpoints:
    """Simulates runpod.Endpoint(id).run + status + output()."""

    def __init__(self, *, output: object | None = None, exc: Exception | None = None) -> None:
        self._output = output
        self._exc = exc
        self.status_calls = 0

    def run(self, input: dict) -> "_FakeRun":  # noqa: A002
        return _FakeRun(output=self._output, exc=self._exc)


class _FakeRun:
    def __init__(self, *, output: object | None, exc: Exception | None) -> None:
        self._output = output
        self._exc = exc

    def status(self) -> str:
        return "COMPLETED"

    def output(self, timeout: float | None = None) -> object:
        if self._exc:
            raise self._exc
        return self._output


def _patch_endpoint(monkeypatch: pytest.MonkeyPatch, fake: _FakeEndpoints) -> None:
    import acheron.worker_sdk._runpod_client as mod

    def _factory(endpoint_id: str) -> _FakeEndpoints:
        return fake

    monkeypatch.setattr(mod, "_open_endpoint", _factory)


class TestRunPodClient:
    @pytest.mark.asyncio
    async def test_returns_artifacts_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeEndpoints(output={"artifacts": [{"filename": "out.wav", "data": "AAEC"}]})
        _patch_endpoint(monkeypatch, fake)
        client = RunPodClient(
            api_key="k", endpoint_id="eid", execution_timeout_s=60.0, api_key_env="runpod"
        )
        result = await client.run(input={"text": "hi"})
        assert isinstance(result, RunPodJobResult)
        assert result.artifacts[0]["filename"] == "out.wav"
        assert result.gpu_seconds is not None
        assert result.gpu_seconds > 0.0

    @pytest.mark.asyncio
    async def test_propagates_timeout_as_error_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import asyncio

        fake = _FakeEndpoints(exc=TimeoutError("slow"))
        _patch_endpoint(monkeypatch, fake)
        client = RunPodClient(
            api_key="k", endpoint_id="eid", execution_timeout_s=0.0, api_key_env="runpod"
        )
        with pytest.raises(TimeoutError):
            await client.run(input={"text": "hi"})

    @pytest.mark.asyncio
    async def test_endpoint_id_and_api_key_passed_to_factory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen_args: dict[str, str] = {}

        def _factory(endpoint_id: str, *, api_key: str) -> _FakeEndpoints:
            seen_args["endpoint_id"] = endpoint_id
            seen_args["api_key"] = api_key
            return _FakeEndpoints(output={"artifacts": []})

        import acheron.worker_sdk._runpod_client as mod

        monkeypatch.setattr(mod, "_open_endpoint", _factory)
        client = RunPodClient(
            api_key="rk_secret", endpoint_id="eid", execution_timeout_s=60.0, api_key_env="runpod"
        )
        await client.run(input={})
        assert seen_args == {"endpoint_id": "eid", "api_key": "rk_secret"}
```

Note: the third test asserts `_open_endpoint(endpoint_id, api_key=...)` signature — keep the implementation in step.

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/worker_sdk/test_runpod_client.py -v
```
Expected: `ImportError: cannot import name 'RunPodClient'`.

- [ ] **Step 3: Implement `_runpod_client.py`**

Create `src/acheron/worker_sdk/_runpod_client.py`:

```python
"""Internal RunPod Serverless client used by the edge container.

The edge container (acheron-worker-edge image) is GPU-less: it serialises a
Job into RunPod's ``/run`` input, submits via the ``runpod`` Python SDK,
polls until COMPLETED/FAILED, and decodes the artifacts. ``gpu_seconds``
is the wall-time of the call — a fair proxy for billing when the serverless
endpoint schedules single-GPU pods per job.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Protocol

from acheron.core.errors import WorkerError


class _Endpoint(Protocol):
    def run(self, input: dict[str, Any]) -> Any: ...  # noqa: A002


def _open_endpoint(endpoint_id: str, *, api_key: str) -> _Endpoint:
    import runpod  # imported lazily — only the edge image needs it.

    runpod.api_key = api_key  # type: ignore[attr-defined]
    return runpod.Endpoint(endpoint_id)  # type: ignore[no-any-return]


@dataclass(frozen=True)
class RunPodJobResult:
    """Decoded response from a finished RunPod job."""

    artifacts: list[dict[str, Any]]
    gpu_seconds: float


class RunPodClient:
    """Wraps the runpod SDK with timeout + cost timing.

    Instantiated once per edge container lifespan; ``run()`` is called for
    each ``/execute`` request received from the orchestrator.
    """

    def __init__(self, *, api_key: str, endpoint_id: str, execution_timeout_s: float) -> None:
        self._api_key = api_key
        self._endpoint_id = endpoint_id
        self._execution_timeout_s = execution_timeout_s

    async def run(self, input: dict[str, Any]) -> RunPodJobResult:
        endpoint = await asyncio.to_thread(_open_endpoint, self._endpoint_id, api_key=self._api_key)
        start = time.monotonic()
        request = await asyncio.to_thread(endpoint.run, input)
        try:
            output = await asyncio.wait_for(
                asyncio.to_thread(request.output, timeout=self._execution_timeout_s),
                timeout=self._execution_timeout_s,
            )
        except TimeoutError as exc:
            msg = f"RunPod job timed out after {self._execution_timeout_s}s (endpoint={self._endpoint_id})"
            raise TimeoutError(msg) from exc

        gpu_seconds = time.monotonic() - start
        output_dict = output if isinstance(output, dict) else {"artifacts": output}
        artifacts = output_dict.get("artifacts", [])
        if not isinstance(artifacts, list):
            msg = f"RunPod output.artifacts must be a list, got {type(artifacts).__name__}"
            raise WorkerError(msg)
        return RunPodJobResult(artifacts=artifacts, gpu_seconds=gpu_seconds)
```

- [ ] **Step 4: Run test + type-check**

```bash
uv run pytest tests/worker_sdk/test_runpod_client.py -v
uv run mypy src/acheron/worker_sdk/_runpod_client.py
uv run basedpyright src/acheron/worker_sdk/_runpod_client.py
```
Expected: tests pass; type-checkers clean.

- [ ] **Step 5: Commit**

```bash
git add src/acheron/worker_sdk/_runpod_client.py tests/worker_sdk/test_runpod_client.py pyproject.toml
git commit -m "feat(worker_sdk): add internal _runpod_client + add runpod dependency"
```

---

### Task 13: Internal `_edge_http` — FastAPI app with `/health`, `/capabilities`, `/execute`

**Files:**
- Create: `src/acheron/worker_sdk/_edge_http.py`
- Create: `tests/worker_sdk/test_edge_http.py`

- [ ] **Step 1: Write the failing test**

Create `tests/worker_sdk/test_edge_http.py`:

```python
"""Tests for the internal edge FastAPI app."""

import httpx
import pytest
from httpx import ASGITransport

from acheron.core.models import Job, WorkerCapabilities, WorkerType
from acheron.worker_sdk._edge_http import EdgeApp
from acheron.worker_sdk.artifacts import BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler


class _Stub(WorkerHandler):
    def __init__(self) -> None:
        self.calls = 0

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"en"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source="huggingface:test",
        )

    async def handle(self, job: Job) -> list[BytesArtifact]:
        self.calls += 1
        return [BytesArtifact(filename="out.wav", content_type="audio/wav", data=b"audio")]


@pytest.fixture
async def client():
    h = _Stub()
    app = EdgeApp(handler=h, capabilities=h.capabilities()).app
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, h


class TestEdgeRoutes:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client) -> None:
        c, _ = client
        r = await c.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_capabilities_returns_shape(self, client) -> None:
        c, _ = client
        r = await c.get("/capabilities")
        assert r.status_code == 200
        body = r.json()
        assert body["worker_type"] == "tts"
        assert body["supported_languages_in"] == ["en"]
        assert body["supported_formats_out"] == ["wav"]

    @pytest.mark.asyncio
    async def test_execute_returns_multipart(self, client) -> None:
        c, h = client
        r = await c.post(
            "/execute",
            json={
                "job_id": "j1",
                "job_type": "tts",
                "payload": {"chunks": [{"text": "hi"}], "target_language": "en"},
                "chapter_id": "ch1",
            },
        )
        assert r.status_code == 200
        assert "multipart/mixed" in r.headers["content-type"]
        assert h.calls == 1
        # The body is multipart/mixed with at least one binary part and a trailing JSON metrics part.
        body_bytes = r.content
        assert b"audio" in body_bytes

    @pytest.mark.asyncio
    async def test_execute_on_handler_error_returns_json(self, client, monkeypatch: pytest.MonkeyPatch) -> None:
        c, h = client

        async def _boom(job: Job) -> list[BytesArtifact]:
            raise RuntimeError("OOM")

        monkeypatch.setattr(h, "handle", _boom)
        r = await c.post(
            "/execute",
            json={"job_id": "j1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"},
        )
        assert r.status_code == 500
        body = r.json()
        assert body["status"] == "failed"
        assert "OOM" in body["error"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/worker_sdk/test_edge_http.py -v
```
Expected: `ImportError: cannot import name 'EdgeApp'`.

- [ ] **Step 3: Implement `_edge_http.py`**

Create `src/acheron/worker_sdk/_edge_http.py`:

```python
"""Internal FastAPI app served by the edge container.

Routes: ``GET /health``, ``GET /capabilities``, ``POST /execute``.

``/execute`` emits a ``multipart/mixed`` body: one binary part per
:class:`Artifact` returned by the handler, plus a trailing
``application/json`` part carrying ``JobMetrics`` (duration, gpu_seconds,
cost_estimate, cost_basis). On handler failure the response is a plain
JSON ``ExecuteError`` body with status 500.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from acheron.core.models import JobMetrics, WorkerCapabilities, WorkerType
from acheron.worker_sdk.artifacts import BytesArtifact, FileArtifact, StreamArtifact
from acheron.worker_sdk.pricing import PriceEstimate, PriceSource, to_cost_basis
from acheron.worker_sdk.schemas import ExecuteError, ExecuteRequest

logger = logging.getLogger(__name__)


def _caps_to_response(caps: WorkerCapabilities) -> dict[str, Any]:
    return {
        "worker_type": caps.worker_type.value,
        "supported_languages_in": sorted(caps.supported_languages_in),
        "supported_languages_out": sorted(caps.supported_languages_out),
        "supported_formats_in": sorted(caps.supported_formats_in),
        "supported_formats_out": sorted(caps.supported_formats_out),
        "max_payload_bytes": caps.max_payload_bytes,
        "batch_capable": caps.batch_capable,
        "model_source": caps.model_source,
        "metadata": caps.metadata,
    }


def _job_from_request(body: ExecuteRequest) -> Job:
    from acheron.core.models import Job as _Job  # lazy to keep the module's import surface tight

    return _Job(
        job_id=body.job_id,
        job_type=WorkerType(body.job_type),
        payload=dict(body.payload),
        chapter_id=body.chapter_id,
        sequence_ids=tuple(body.sequence_ids) if body.sequence_ids is not None else None,
    )


def _build_multipart_response(
    artifacts: list[BytesArtifact | StreamArtifact | FileArtifact],
    metrics: JobMetrics,
) -> Response:
    boundary = f"acheron-{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for a in artifacts:
        header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: attachment; filename="{a.filename}"\r\n'
            f"Content-Type: {a.content_type}\r\n"
            f"X-Acheron-Metadata: {_encode_metadata(a.metadata)}\r\n\r\n"
        ).encode("utf-8")
        body = b"".join([chunk async for chunk in a.stream()])  # type: ignore[attr-defined]
        parts.append(header + body + b"\r\n")
    metrics_json = (
        f'{{"duration_seconds":{metrics.duration_seconds}'
        f',"gpu_seconds":{metrics.gpu_seconds!r}'
        f',"tokens_in":{metrics.tokens_in!r}'
        f',"tokens_out":{metrics.tokens_out!r}'
        f',"cost_estimate":{metrics.cost_estimate!r}'
        f',"cost_basis":"{metrics.cost_basis.value if metrics.cost_basis else "unknown"}"'
        f"}}"
    ).encode("utf-8")
    parts.append(
        f"--{boundary}\r\nContent-Type: application/json\r\n\r\n".encode("utf-8")
        + metrics_json
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    return Response(
        content=body,
        media_type=f"multipart/mixed; boundary={boundary}",
    )


def _encode_metadata(metadata: dict[str, Any]) -> str:
    import json

    return json.dumps(metadata, separators=(",", ":"))


class EdgeApp:
    """Container for the edge FastAPI app + handler + price source."""

    def __init__(
        self,
        *,
        handler: Any,  # WorkerHandler — typed loose to avoid the cycle
        capabilities: WorkerCapabilities,
        price_source: PriceSource | None = None,
    ) -> None:
        self.handler = handler
        self.capabilities = capabilities
        self.price_source = price_source

        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001
            await handler.startup()
            try:
                yield
            finally:
                await handler.shutdown()

        app = FastAPI(title="acheron-worker-edge", lifespan=lifespan)

        @app.get("/health")
        async def health() -> dict[str, str]:
            return {"status": "ok"}

        @app.get("/capabilities")
        async def get_capabilities() -> dict[str, Any]:
            return _caps_to_response(self.capabilities)

        @app.post("/execute")
        async def execute(body: ExecuteRequest) -> Response:
            return await self._run_execute(body)

        self.app = app

    async def _run_execute(self, body: ExecuteRequest) -> Response:
        job = _job_from_request(body)
        start = time.monotonic()
        try:
            artifacts = await self.handler.handle(job)
        except Exception as exc:
            logger.exception("Handler failed for job %s", job.job_id)
            return JSONResponse(
                status_code=500,
                content=ExecuteError(status="failed", error=str(exc)).model_dump(),
            )
        duration = time.monotonic() - start
        gpu_seconds = duration  # edge forwarder has no GPU; for local-backend use, the handler times itself.
        if self.price_source is not None:
            est = await self.price_source.estimate(gpu_seconds)
            cost = est.cost
            basis = to_cost_basis(est)
        else:
            cost = None
            basis = None
        metrics = JobMetrics(
            duration_seconds=duration,
            gpu_seconds=gpu_seconds,
            cost_estimate=cost,
            cost_basis=basis,
        )
        return _build_multipart_response(artifacts, metrics)
```

Note: `_build_multipart_response` uses `async for chunk in a.stream()` inside a sync body — that's a bug. Refactor by materializing artifacts' bytes ahead of time. Replace `_build_multipart_response` with an async version:

```python
async def _build_multipart_response(
    artifacts: list[BytesArtifact | StreamArtifact | FileArtifact],
    metrics: JobMetrics,
) -> Response:
    boundary = f"acheron-{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for a in artifacts:
        header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: attachment; filename="{a.filename}"\r\n'
            f"Content-Type: {a.content_type}\r\n"
            f"X-Acheron-Metadata: {_encode_metadata(a.metadata)}\r\n\r\n"
        ).encode("utf-8")
        body_data = b""
        async for chunk in a.stream():
            body_data += chunk
        parts.append(header + body_data + b"\r\n")
    metrics_json = (
        f'{{"duration_seconds":{metrics.duration_seconds}'
        f',"gpu_seconds":{metrics.gpu_seconds!r}'
        f',"tokens_in":{metrics.tokens_in!r}'
        f',"tokens_out":{metrics.tokens_out!r}'
        f',"cost_estimate":{metrics.cost_estimate!r}'
        f',"cost_basis":"{metrics.cost_basis.value if metrics.cost_basis else "unknown"}"'
        f"}}"
    ).encode("utf-8")
    parts.append(
        f"--{boundary}\r\nContent-Type: application/json\r\n\r\n".encode("utf-8")
        + metrics_json
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    return Response(
        content=body,
        media_type=f"multipart/mixed; boundary={boundary}",
    )
```

Update `_run_execute` accordingly: `return await _build_multipart_response(artifacts, metrics)`.

Re-read the file and verify it's coherent (the `async for` body in `_build_multipart_response` is fine, and `_run_execute` awaits it). Replace the buggy version of `_build_multipart_response` if it's still in the file.

- [ ] **Step 4: Run test + type-check**

```bash
uv run pytest tests/worker_sdk/test_edge_http.py -v
uv run mypy src/acheron/worker_sdk/_edge_http.py
uv run basedpyright src/acheron/worker_sdk/_edge_http.py
```
Expected: tests pass; type-checkers clean.

- [ ] **Step 5: Commit**

```bash
git add src/acheron/worker_sdk/_edge_http.py tests/worker_sdk/test_edge_http.py
git commit -m "feat(worker_sdk): add internal _edge_http FastAPI app with multipart /execute response"
```

---

### Task 14: `create_worker_app` factory (lifespan + registration + price refresh)

**Files:**
- Create: `src/acheron/worker_sdk/app.py`
- Create: `tests/worker_sdk/test_app.py`

- [ ] **Step 1: Write the failing test**

Create `tests/worker_sdk/test_app.py`:

```python
"""Tests for create_worker_app factory."""

import httpx
import pytest
import respx
from httpx import ASGITransport
from pydantic_settings import SettingsConfigDict
from typing_extensions import Any  # noqa: TP003

from acheron.core.models import Job, WorkerCapabilities, WorkerType
from acheron.worker_sdk.app import create_worker_app
from acheron.worker_sdk.artifacts import BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler
from acheron.worker_sdk.settings import WorkerSettings


class _Stub(WorkerHandler):
    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"en"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
        )

    async def handle(self, job: Job) -> list[BytesArtifact]:
        return [BytesArtifact(filename="out.wav", content_type="audio/wav", data=b"audio")]


def _settings(**overrides: Any) -> WorkerSettings:
    base = {
        "worker_id": "w",
        "orchestrator_url": "http://orch:8000",
        "listen_port": 0,
        "price_source": "zero",
    }
    base.update(overrides)
    return WorkerSettings(**base)  # type: ignore[arg-type]


class TestCreateWorkerApp:
    @respx.mock
    @pytest.mark.asyncio
    async def test_lifespan_registers_then_serves_then_cleanly_shuts_down(self) -> None:
        route = respx.post("http://orch:8000/workers").mock(return_value=httpx.Response(201, json={}))
        h = _Stub()
        s = _settings()
        app = create_worker_app(handler=h, settings=s)
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/health")
            assert r.status_code == 200
        assert route.called
        # after exit, handler.shutdown() should have run — track via spy if needed.

    @pytest.mark.asyncio
    async def test_execute_routes_through_app(self) -> None:
        h = _Stub()
        s = _settings(price_source="zero")
        app = create_worker_app(handler=h, settings=s, disable_registration=True)
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/execute",
                json={"job_id": "j1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"},
            )
            assert r.status_code == 200
            assert "multipart/mixed" in r.headers["content-type"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/worker_sdk/test_app.py -v
```
Expected: `ImportError: cannot import name 'create_worker_app'`.

- [ ] **Step 3: Implement `app.py`**

Create `src/acheron/worker_sdk/app.py`:

```python
"""Public ``create_worker_app`` factory building the edge FastAPI app."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator

import httpx
from fastapi import FastAPI

from acheron.worker_sdk._edge_http import EdgeApp
from acheron.worker_sdk.pricing import PriceSource, RunPodPrice, StaticPrice, ZeroPrice
from acheron.worker_sdk.registration import register_with_orchestrator

if TYPE_CHECKING:
    from acheron.worker_sdk.handler import WorkerHandler
    from acheron.worker_sdk.settings import WorkerSettings

logger = logging.getLogger(__name__)


def _build_price_source(settings: WorkerSettings) -> PriceSource:
    match settings.price_source:
        case "runpod":
            if not settings.runpod_api_key or not settings.runpod_endpoint_id:
                logger.warning("price_source=runpod but RUNPOD_API_KEY/RUNPOD_ENDPOINT_ID not set; prices will be unknown")
                return ZeroPrice()
            return RunPodPrice(
                api_key=settings.runpod_api_key,
                endpoint_id=settings.runpod_endpoint_id,
                secure_cloud=settings.secure_cloud,
                cache_ttl_s=settings.price_cache_ttl_s,
            )
        case "static":
            if settings.dollars_per_hour is None:
                logger.warning("price_source=static but dollars_per_hour not set; falling back to ZeroPrice")
                return ZeroPrice()
            return StaticPrice(dollars_per_hour=settings.dollars_per_hour)
        case "zero":
            return ZeroPrice()
        case _:
            return ZeroPrice()


def _endpoint_url(settings: WorkerSettings) -> str:
    """The URL the orchestrator will use to reach this edge container."""
    return f"http://{os.environ.get('WORKER_HOST', 'localhost')}:{settings.listen_port}"


def create_worker_app(
    *,
    handler: WorkerHandler,
    settings: WorkerSettings,
    disable_registration: bool = False,
) -> FastAPI:
    """Build the edge FastAPI app wired with registration + price refresh."""
    caps = handler.capabilities()
    price_source = _build_price_source(settings)

    async def _register() -> None:
        async with httpx.AsyncClient() as client:
            await register_with_orchestrator(
                client=client,
                orchestrator_url=settings.orchestrator_url,
                token=settings.registration_token,
                worker_id=settings.worker_id,
                endpoint=_endpoint_url(settings),
                transport="http",
                capabilities=caps,
            )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # 1. startup hook (model load, etc.)
        await handler.startup()
        # 2. eager price refresh — fault-tolerant, never blocks
        if isinstance(price_source, RunPodPrice):
            async with httpx.AsyncClient() as client:
                refreshed = await price_source._refresh_rate(client)
                if not refreshed:
                    logger.warning("RunPod price API unreachable at startup; worker will register with unknown pricing")
        # 3. register with orchestrator (skipped in tests / when explicitly disabled)
        if not disable_registration:
            await _register()
        try:
            yield
        finally:
            await handler.shutdown()

    inner = EdgeApp(handler=handler, capabilities=caps, price_source=price_source)
    # Re-use EdgeApp's app but install our composite lifespan.
    app = FastAPI(title="acheron-worker-edge", lifespan=lifespan)
    # Mount the inner app's routes manually so we don't run the inner lifespan.
    for r in inner.app.routes:
        if r.path in {"/health", "/capabilities", "/execute"}:
            app.routes.append(r)
    return app
```

Note: `RunPodPrice._refresh_rate` is accessed here as a "friend" — the SDK's app.py reaches into the price source's internal refresh hook to do an eager one-shot at startup. To avoid circular tight coupling, instead expose a public method on `PriceSource`: add to the Protocol:

```python
@runtime_checkable
class PriceSource(Protocol):
    async def estimate(self, gpu_seconds: float) -> PriceEstimate: ...
    async def refresh(self) -> bool: ...
```

Update `ZeroPrice`, `StaticPrice`, `RunPodPrice` to also expose `async def refresh(self) -> bool: return True` (Zero/Static) or the existing refresh body (RunPod). Replace the `isinstance` + `_refresh_rate` call in `app.py` with `await price_source.refresh()`.

Re-edit `pricing.py`: in each PriceSource model class, add `async def refresh(self) -> bool`. For `ZeroPrice`/`StaticPrice` return `True`; for `RunPodPrice` delegate to `self._refresh_rate`.

Update `app.py`:

```python
        # 2. eager price refresh — fault-tolerant, never blocks
        try:
            await price_source.refresh()
        except Exception:
            logger.warning("Price refresh raised at startup; worker will register anyway", exc_info=True)
```

(Drop the `isinstance(price_source, RunPodPrice)` branch and the `_refresh_rate` "friend" call.)

- [ ] **Step 4: Run test + type-check**

```bash
uv run pytest tests/worker_sdk/test_app.py -v
uv run mypy src/acheron/worker_sdk/app.py src/acheron/worker_sdk/pricing.py
uv run basedpyright src/acheron/worker_sdk/app.py src/acheron/worker_sdk/pricing.py
```
Expected: tests pass; type-checkers clean.

- [ ] **Step 5: Re-export**

Update `src/acheron/worker_sdk/__init__.py`:

```python
from acheron.worker_sdk.app import create_worker_app
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact, FileArtifact, StreamArtifact
from acheron.worker_sdk.cloud import make_runpod_handler
from acheron.worker_sdk.handler import WorkerHandler
from acheron.worker_sdk.pricing import PriceEstimate, PriceSource, RunPodPrice, StaticPrice, ZeroPrice
from acheron.worker_sdk.registration import register_with_orchestrator
from acheron.worker_sdk.settings import WorkerSettings

__all__ = [
    "Artifact",
    "BytesArtifact",
    "FileArtifact",
    "StreamArtifact",
    "WorkerHandler",
    "WorkerSettings",
    "PriceSource",
    "PriceEstimate",
    "RunPodPrice",
    "StaticPrice",
    "ZeroPrice",
    "create_worker_app",
    "make_runpod_handler",
    "register_with_orchestrator",
]
```

- [ ] **Step 6: Commit**

```bash
git add src/acheron/worker_sdk/app.py src/acheron/worker_sdk/pricing.py src/acheron/worker_sdk/__init__.py tests/worker_sdk/test_app.py
git commit -m "feat(worker_sdk): add create_worker_app factory with lifespan + registration + price refresh"
```

---

### Task 15: CLI entrypoint module (`acheron-worker-edge`)

**Files:**
- Create: `src/acheron/worker_sdk/cli.py`
- Create: `tests/worker_sdk/test_cli.py`
- Modify: `pyproject.toml` (add `[project.scripts] acheron-worker-edge`)

- [ ] **Step 1: Write the failing test**

Create `tests/worker_sdk/test_cli.py`:

```python
"""Tests for the acheron-worker-edge image entrypoint."""

import importlib

import pytest

from acheron.worker_sdk.cli import _import_handler


class _StubHandler:  # exposed via a pseudo-module path for the test
    pass


def test_import_handler_loads_class_from_dotted_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    from types import ModuleType

    fake_mod = ModuleType("fake_worker_pkg.fake_mod")
    fake_mod.MyClass = _StubHandler  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fake_worker_pkg.fake_mod", fake_mod)

    klass = _import_handler("fake_worker_pkg.fake_mod:MyClass")
    assert klass is _StubHandler


def test_import_handler_raises_on_missing_colon() -> None:
    with pytest.raises(ValueError, match="must be 'module:Class'"):
        _import_handler("somemodule")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/worker_sdk/test_cli.py -v
```
Expected: `ImportError: cannot import name '_import_handler'`.

- [ ] **Step 3: Implement `cli.py`**

Create `src/acheron/worker_sdk/cli.py`:

```python
"""``acheron-worker-edge`` is the image's CMD module, not a user-facing CLI.

The deployer configures the edge container via ``docker-compose.yml``
service env vars + ``worker.yaml`` discovery — they never invoke this
binary directly. It exists so the same published generic image serves
TTS / ASR / translation RunPod workers (only the handler import path +
``worker.yaml`` differ per service).

Usage (in the published image's CMD):
    python -m acheron.worker_sdk.cli --handler workers.qwen3tts.handler:Qwen3TTSRunpodHandler
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys
from typing import Any

import uvicorn

from acheron.worker_sdk.app import create_worker_app
from acheron.worker_sdk.config_loader import load_settings

logger = logging.getLogger(__name__)


def _import_handler(import_path: str) -> type[Any]:
    """Resolve ``"pkg.mod:ClassName"`` to the class object."""
    if ":" not in import_path:
        msg = f"Handler import path must be 'module:Class' (got {import_path!r})"
        raise ValueError(msg)
    module_name, _, class_name = import_path.partition(":")
    module = importlib.import_module(module_name)
    try:
        return getattr(module, class_name)
    except AttributeError as exc:
        msg = f"Module {module_name!r} has no attribute {class_name!r}"
        raise AttributeError(msg) from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="acheron-worker-edge image entrypoint")
    parser.add_argument("--handler", required=True, help="Dotted path pkg.mod:ClassName")
    parser.add_argument("--config", default=None, help="Path to worker YAML (overrides discovery)")
    args = parser.parse_args()

    if args.config:
        import os

        os.environ["WORKER_CONFIG"] = args.config

    settings = load_settings()

    if not settings.handler:
        msg = "Handler import path missing — set 'handler' in worker.yaml or pass --handler"
        raise SystemExit(msg)

    handler_class = _import_handler(settings.handler)
    handler = handler_class(settings)
    app = create_worker_app(handler=handler, settings=settings)

    logging.basicConfig(
        level=os.environ.get("ACHERON_WORKER__LOG_LEVEL", "INFO"),
        stream=sys.stdout,
    )
    uvicorn.run(app, host=settings.listen_host, port=settings.listen_port)


if __name__ == "__main__":
    main()
```

Drop the unused `import os` at the top — it's used only inside the `if args.config:` block, keep it there. Actually `logging` and `sys` are used; `os` only inside the conditional — keep at top for clarity but ensure ruff doesn't complain. Ruff's `PLC0415` (imports outside top level) is suppressed in tests, but in src it isn't. To follow the "imports at top" convention, move `import os` to the top with the others.

Final imports block:

```python
from __future__ import annotations

import argparse
import importlib
import logging
import os
import sys
from typing import Any

import uvicorn

from acheron.worker_sdk.app import create_worker_app
from acheron.worker_sdk.config_loader import load_settings
```

And drop the inner `import os`. 

- [ ] **Step 4: Add the console script**

In `pyproject.toml` under `[project.scripts]` (currently line 30-31):

```toml
[project.scripts]
acheron = "acheron.cli:main"
acheron-worker-edge = "acheron.worker_sdk.cli:main"
```

- [ ] **Step 5: Run test + type-check + smoke the entrypoint binary**

```bash
uv run pip install -e . --quiet  # exposes the new console script
uv run pytest tests/worker_sdk/test_cli.py -v
uv run mypy src/acheron/worker_sdk/cli.py
uv run basedpyright src/acheron/worker_sdk/cli.py
uv run acheron-worker-edge --help
```
Expected: tests pass; both type-checkers clean; `--help` prints usage mentioning `--handler`.

- [ ] **Step 6: Commit**

```bash
git add src/acheron/worker_sdk/cli.py tests/worker_sdk/test_cli.py pyproject.toml
git commit -m "feat(worker_sdk): add acheron-worker-edge image entrypoint CLI module"
```

---

### Task 16: Final-gate `just validate`

- [ ] **Step 1: Run full validation**

```bash
just validate
```
Expected: `lint-strict`, `lint-imports`, `type-check`, `type-check-pyright`, `test` all pass; coverage ≥ 80%.

- [ ] **Step 2: If coverage is short, add targeted tests**

Likely gaps: `_edge_http` error handling paths, `_runpod_client` failure modes, `pricing` fallback `to_cost_basis`. Add tests covering the missing lines, then re-run. Do not raise the line-by-line noise — prefer parameterized tests that hit multiple branches.

- [ ] **Step 3: Commit any additional tests**

```bash
git add tests/
git commit -m "test(worker_sdk): cover error paths for validate coverage"
```

---

## Spec Coverage Map

- `CostBasis` enum + `JobMetrics.cost_basis` — Task 1.
- `acheron.worker_sdk` subpackage + import-linter boundary — Task 2.
- `WorkerHandler` ABC + lifecycle hooks — Task 4.
- `Artifact` composition (Bytes/Stream/File) — Task 3.
- `WorkerSettings` env-only secrets rejection — Task 5.
- Config discovery (4-priority) + YAML — Task 6.
- RunPod price discovery (endpoint-introduced GPU + fault tolerance) — Tasks 9 + 10.
- `PriceEstimate` + `to_cost_basis` mapping — Tasks 9 + 10.
- `register_with_orchestrator` retry-backoff client — Task 8.
- Pydantic `/execute` schemas — Task 7.
- `make_runpod_handler` cloud adapter — Task 11.
- Internal `_runpod_client` (submit + poll + timeout) — Task 12.
- Internal `_edge_http` (FastAPI with `/health` `/capabilities` `/execute` multipart) — Task 13.
- `create_worker_app` lifespan (startup → price refresh → register → shutdown) — Task 14.
- CLI entrypoint module + console script — Task 15.
- `runpod` runtime dep — Task 12.

**Deferred to Plan 2:** HttpWorker multipart/mixed parser; GrpcWorker Artifact mode + proto extension; shared `_materialize_artifact` / `_build_result`; dashboard cost-confidence rendering; `JobResponse.total_cost_basis`.

**Deferred to Plan 3:** `workers/qwen3tts/` package (`Qwen3TTSRunpodHandler`, `runpod_entrypoint.py`, `worker.yaml`, `Dockerfile.runpod`, tests); the 7-stub matrix under `stubs/` replacing the existing 4 stubs; the `acheron-worker-edge` Dockerfile; GHCR CI workflow; Justfile `build-worker` target; `docker-compose.yml` edge service entry; `workers.* -/-> acheron.shell` import-linter contract.