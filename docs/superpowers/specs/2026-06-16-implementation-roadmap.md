# Acheron — Implementation Roadmap

Incremental implementation plan for [Acheron design spec](./2026-06-16-acheron-design.md).

## Architecture Principles

- **Imperative Shell, Functional Core** — `core/` has pure logic, `shell/` has I/O
- **import-linter** enforces `core` never imports from `shell`
- Each layer is a self-contained unit that passes `just validate`

## Layers

### Layer 0 — Data Models & Enums ✓ (current)

**Scope**: All dataclasses, enums, domain exceptions. Zero I/O.

**Files**:
- `src/acheron/core/models.py` — WorkerType, JobStatus, StepStatus, WorkerCapabilities, Job, OutputFile, JobResult, JobMetrics, Plan, PlanStep, PlanResult, BatchJob, BatchStatus
- `src/acheron/core/errors.py` — AcheronError hierarchy (PlanError, WorkerError, CacheError)
- `tests/core/test_models.py`
- `tests/core/test_errors.py`
- import-linter contracts in `pyproject.toml`

**Deps**: None (pure types)

---

### Layer 1 — Interfaces & Chunking

**Scope**: ABCs for Worker/StreamingWorker/Executor. Text chunking engine (NLTK punkt + fallback).

**Files**:
- `src/acheron/core/interfaces.py` — Worker, StreamingWorker, Executor ABCs
- `src/acheron/core/chunking.py` — `chunk_text(text, max_length=250) -> list[Chunk]`
- `tests/core/test_interfaces.py`
- `tests/core/test_chunking.py`

**Deps**: Layer 0 models

---

### Layer 2 — Worker Registry + Caching + LocalWorker

**Scope**: Redis-backed worker registry with health monitoring. File-based plan/manifest caching. LocalWorker transport.

**Files**:
- `src/acheron/shell/registry.py` — WorkerRegistry (add, remove, health check, lookup)
- `src/acheron/shell/cache.py` — Plan persistence, manifest read/write to `/data/jobs/`
- `src/acheron/shell/transports/local.py` — LocalWorker (calls Python functions directly)
- `tests/shell/test_registry.py`
- `tests/shell/test_cache.py`
- `tests/shell/test_local_worker.py`

**Deps**: Layer 0-1. External: redis-py

---

### Layer 3 — Planner + Executors

**Scope**: Plan compilation (job request → Plan DAG). Sequential, Async, BatchAsync executors.

**Files**:
- `src/acheron/core/planner.py` — Pure logic: validate language path, compile steps, assign workers
- `src/acheron/shell/executors/sequential.py` — Step-by-step execution
- `src/acheron/shell/executors/async_executor.py` — Concurrent DAG traversal
- `src/acheron/shell/executors/batch_async.py` — Batch streaming for GPU workers
- `tests/core/test_planner.py`
- `tests/shell/test_executors.py`

**Deps**: Layer 0-2

---

### Layer 4 — API + CLI

**Scope**: FastAPI endpoints for job submission, status, capabilities, worker management. Click CLI.

**Files**:
- `src/acheron/shell/api/app.py` — FastAPI app
- `src/acheron/shell/api/routes/jobs.py`
- `src/acheron/shell/api/routes/workers.py`
- `src/acheron/shell/api/routes/capabilities.py`
- `src/acheron/shell/cli.py` — Click CLI with rich formatting
- `tests/shell/api/`
- `tests/shell/test_cli.py`

**Deps**: Layer 0-3

---

### Layer 5 — GPU Workers + Dashboard

**Scope**: HttpWorker transport for RunPod/HuggingFace. HTMX dashboard. Docker Compose.

**Files**:
- `src/acheron/shell/transports/http.py` — HttpWorker (RunPod, HF Inference Endpoints)
- `src/acheron/shell/transports/grpc.py` — GrpcWorker (future)
- `dashboard/` — HTMX/Jinja separate container
- `docker-compose.yml`
- `Dockerfile.orchestrator`
- `Dockerfile.tts-worker`
- `Dockerfile.asr-worker`

**Deps**: Layer 0-4. External: RunPod API, HuggingFace

---

## Status

| Layer | Status | Notes |
|-------|--------|-------|
| 0 | done | Data models, enums, errors |
| 1 | done | Interfaces, chunking |
| 2 | done | Registry, caching, local worker |
| 3 | done | Planner, executors |
| 4 | done | API + CLI |
| 5 | pending | GPU workers, dashboard, Docker |
