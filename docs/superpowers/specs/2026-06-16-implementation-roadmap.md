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

### Layer 6 — gRPC Streaming Transport

**Scope**: GrpcWorker implementing `StreamingWorker` via bidirectional gRPC streams. `.proto` definitions for TTS/ASR streaming. Direct PCM byte streaming from GPU to orchestrator, bypassing worker disk I/O.

**Files:**
- `src/acheron/shell/transports/grpc.py` — GrpcWorker
- `proto/acheron/synthesis.proto` — TTS bidirectional streaming
- `proto/acheron/transcription.proto` — ASR bidirectional streaming
- `tests/shell/test_grpc_worker.py`

**Design**: GrpcWorker implements the existing `Worker` + `StreamingWorker` interface. Streaming is internal to the worker — orchestrator sends a job, GrpcWorker opens a bidirectional gRPC stream to the GPU worker, receives audio bytes as they're generated, and returns assembled `JobResult`. No architectural changes to core/interfaces/planner.

**Deps**: Layer 0-5. External: grpcio, protobuf

---

### Layer 7 — Production Hardening

**Scope**: Redis-backed stores, registration security, TLS, persistent storage, resource limits, healthchecks.

**Files**:
- `src/acheron/shell/registry.py` — Redis-backed `WorkerRegistry`
- `src/acheron/shell/job_store.py` — Redis-backed `JobStore`
- `docker-compose.yml` — TLS, volumes, resource limits, healthchecks
- `Dockerfile.orchestrator` — production-hardened image

**Design**:
- `WorkerRegistry` and `JobStore` get Redis implementations alongside existing in-memory ones. Selected via config/env var.
- Registration security: `ACHERON_REGISTRATION_TOKEN` env var, `Authorization: Bearer <token>` on `POST /workers`. 401 on mismatch. Unset = open registration (dev mode).
- TLS: self-signed certs or reverse proxy (nginx/caddy) for local dev. Production uses cert-manager or cloud provider certs.
- Persistent volumes: `/data/jobs/` for cached step outputs, Redis data volume.
- Resource limits: CPU/memory constraints per container in Compose.
- Healthchecks: `GET /health` on orchestrator and workers, integrated with Docker healthcheck and Compose `depends_on`.

**Deps**: Layer 0-5. External: redis-py (already in deps)

---

### Layer 8 — Real GPU Workers

**Scope**: PyTorch-based TTS and ASR workers with real models. GPU Dockerfiles. Deployment configs.

**Files**:
- `src/acheron/shell/transports/local.py` — extended with TTS/ASR implementations
- `workers/tts/` — TTS worker app + Dockerfile
- `workers/asr/` — ASR worker app + Dockerfile
- `workers/tts/Dockerfile` — PyTorch + CUDA base, Qwen3-TTS model
- `workers/asr/Dockerfile` — PyTorch + CUDA base, Whisper-v3 model

**Design**:
- TTS: Qwen3-TTS-12Hz-1.7B, HuggingFace Transformers. Accepts text, returns audio bytes. Min 8GB VRAM.
- ASR: Whisper-v3 Large, HuggingFace Transformers. Accepts audio, returns text. Min 10GB VRAM.
- Deployment: RunPod serverless, HuggingFace Inference Endpoints, local GPU (`docker compose --gpus`).
- Model management: pre-downloaded at build time or fetched on first run with persistent volume caching.
- Workers implement same HTTP interface as stubs (`/health`, `/submit`, `/capabilities`).

**Deps**: Layer 0-7. External: PyTorch, Transformers, CUDA

---

## Status

| Layer | Status | Notes |
|-------|--------|-------|
| 0 | done | Data models, enums, errors |
| 1 | done | Interfaces, chunking |
| 2 | done | Registry, caching, local worker |
| 3 | done | Planner, executors |
| 4 | done | API + CLI |
| 5 | done | HttpWorker, dashboard, Docker Compose, registration security |
| 6 | done | gRPC streaming transport: GrpcWorker, proto, stub worker, transport-aware health monitor |
| 7a | done | Storage abstraction + Redis backend (sync `redis.Redis` client) |
| 7b | done | Production compose hardening: healthchecks on all services, named volumes, depends_on conditions, fail-fast data dir check, gRPC HTTP /health sidecar (FastAPI) |
| 7c | done | TLS via env vars: `ACHERON_TLS_{CERT,KEY,CA}_FILE`; dev cert script (`just certs`); compose wires certs, env vars, and HTTPS healthchecks; dashboard stays HTTP |
| 8 | planned | Real GPU workers: TTS (Qwen3), ASR (Whisper-v3) |

## Layer 7 — Decomposition

Layer 7 is a grab-bag of production concerns with distinct sub-systems. To keep design surfaces small, it's split into three independent sub-projects. Each gets its own spec → plan → implementation cycle.

### Sub-project 7a — Storage abstraction + Redis backend

Make worker registry and job state survive orchestrator restarts by adding a Redis backend behind a shared ABC interface. In-memory backend stays for dev.

- ABC `WorkerStore` and `JobStore` in a new `src/acheron/shell/stores/` subpackage
- `InMemoryWorkerStore` (renames current `WorkerRegistry`) and `InMemoryJobStore` (renames current `JobStore`)
- `RedisWorkerStore` and `RedisJobStore` implement the same interface
- `ACHERON_STORE_BACKEND=memory|redis` env var selects at startup; fail fast if `redis` is unreachable
- Redis data layout: `worker:{id}` HASH + `workers` SET for registry; `job:{id}` STRING (JSON) + `jobs` SET for jobs
- testcontainers for integration tests against real Redis

### Sub-project 7b — Production compose hardening

Deployment-side hardening for the Docker Compose stack.

- Docker healthchecks on every service (`GET /health` for orchestrator and workers)
- Resource limits (`cpus`, `mem_limit`) on every service
- Persistent volumes: `/data` mounted for orchestrator (step output cache), named `redis-data` for Redis
- Orchestrator refuses to start if `/data` is not writable

### Sub-project 7c — TLS termination via reverse proxy

TLS via reverse proxy (nginx or caddy) with self-signed certs for local dev.

- New `proxy/` directory with `nginx.conf` and `Dockerfile`
- Routes `/api/*` to orchestrator, `/` to dashboard
- Self-signed cert generation script for local dev
- `docker compose --profile tls up` opt-in
- Production deploys use real certs via cert-manager or cloud provider
