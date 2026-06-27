# Acheron — Implementation Roadmap

Incremental implementation plan for [Acheron design spec](./architecture.md).

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
- Registration security: `registration_token` configuration value (overridable via `ACHERON_REGISTRATION_TOKEN` environment variable). `POST /workers` requires `Authorization: Bearer <token>`. Unset token auto-generates a secure token at startup and persists it to `{data_dir}/.registration_token`. Explicitly opt into open registration with `ACHERON_OPEN_REGISTRATION=1`.
- TLS: self-signed certs or reverse proxy (nginx/caddy) for local dev. Production uses cert-manager or cloud provider certs.
- Persistent volumes: `/data/jobs/` for cached step outputs, Redis data volume.
- Resource limits: CPU/memory constraints per container in Compose.
- Healthchecks: `GET /health` on orchestrator and workers, integrated with Docker healthcheck and Compose `depends_on`.

**Deps**: Layer 0-5. External: redis-py (already in deps)

---

### Layer 8 — Real GPU Workers (Plug-and-play)

Layer 8 is decomposed into three independent sub-projects (8a TTS, 8b ASR, 8c Translation). Each gets its own spec → plan → implementation cycle. See [Layer 8a TTS design](./layer-8a-tts-worker.md).

**Scope**: Decoupled, model-specific GPU workers with PyTorch/CUDA. Built on the [`acheron.worker_sdk`](./layer-8a-tts-worker.md) blueprint (a new subpackage of the existing `acheron` wheel that imports `acheron.core` only, never `acheron.shell`).

**Files** (per sub-project):
- `workers/qwen3tts/` — Qwen3 TTS worker codebase + `Dockerfile.runpod` (sub-project 8a)
- `workers/whisperv3large/` — Whisper-v3 Large ASR worker codebase + `Dockerfile.runpod` (sub-project 8b)
- `workers/translategemma/` — TranslateGemma-12B translation worker codebase + `Dockerfile.runpod` (sub-project 8c)
- `src/acheron/worker_sdk/` — the blueprint (shipped with 8a, reused by 8b / 8c)

**Design**:
- Each worker is a self-contained package that depends on `acheron` for the `worker_sdk` + `core` types only. `worker_sdk` imports `acheron.core` only; it never imports `acheron.shell`. Import-linter enforces `workers.* -/-> acheron.shell`.
- Workers interact with the orchestrator solely through standard REST/gRPC specifications.
- Per-worker output contract: workers return `Artifact` parts (bytes-only — never filesystem paths into the orchestrator's volume). The orchestrator materializes received bytes into its own `ACHERON_DATA_DIR/{job_id}/{step_id}/`, eliminating the shared-volume assumption so physically-separated workers (RunPod Serverless, Hugging Face Inference Endpoints, dedicated remote hosts) are first-class.
- A worker ships **one deployment mode** — not a runtime flag. v1 (qwen3tts) ships RunPod Serverless only; a local-GPU `Qwen3TTSLocalHandler` would be a separate future worker package, not a config knob on the existing one.
- Configuration: managed natively on each worker container via `worker.yaml` + env vars, bypassing orchestrator configuration. Discovery: `WORKER_CONFIG` env → `<worker_name>.worker.yaml` → `worker.yaml` → env-only. Secrets (`runpod_api_key`, `registration_token`) are env-only — rejected if present in YAML.
- CI publishes each worker image to GHCR on tag and `main`; the deployer never builds worker images.
- Cold-start detection reuses the existing `RunPodHealthProvider` (Layer 11) via `capabilities.metadata["health_provider"] = "runpod"` + `health_endpoint_id`.

**Deps**: CUDA, PyTorch, Transformers (independent per worker); `runpod` SDK as a top-level orchestrator dep (for the `worker_sdk` edge runtime).

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
| 8a | in progress | TTS worker (`qwen3tts`, RunPod Serverless) + `acheron.worker_sdk` blueprint. See [Layer 8a design](./layer-8a-tts-worker.md). |
| 8b | planned | ASR worker (`whisperv3large`), reusing the blueprint. |
| 8c | planned | Translation worker (`translategemma`), reusing the blueprint. Supersedes an earlier stub spec that predates the blueprint. |
| 9b-i | done | Store ABC + InMemory async (`async def` ABCs, all call sites await) |
| 9b-ii | done | Redis async backend (`redis.asyncio.Redis`, testcontainers integration tests) |
| 9a | done | Streaming pipeline executor (`StreamingExecutor` is the new default; `PipelineError`; per-step timeout; per-stage queue with sentinel drain) |
| 10 | done | Built-in local workers (Extraction, Chunking, Packaging), settings via `acheron.yaml`, API/CLI resume |
| 11 | partial | Decoupled health checks (RunPod/HF), dashboard error & status updates. Worker packaging + CI/CD deferred to a separate plan. |


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

---

## Layer 8 — Decomposition

Layer 8 is decomposed into three independent sub-projects (8a TTS, 8b ASR, 8c Translation). Each gets its own spec → plan → implementation cycle. The TTS sub-project (8a) establishes the [`acheron.worker_sdk`](./layer-8a-tts-worker.md) blueprint the other two reuse.

### Sub-project 8a — TTS worker + worker SDK blueprint

Establish the worker blueprint: `WorkerHandler` ABC, composable `Artifact` outputs, RunPod edge runtime, fault-tolerant RunPod price discovery with `CostBasis` tracking, registration client, FastAPI factory, runpod-edge CLI subcommand. Ship `workers/qwen3tts/` against the `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` model (9 built-in premium speakers; voice cloning via Base deferred to a future sub-project).

- RunPod Serverless deployment mode only; local-GPU handler is a separate future worker package, not a runtime flag.
- Output contract: workers return artifacts as bytes (multipart/mixed for HTTP, `repeated Artifact` for gRPC). The orchestrator materializes them into its own `ACHERON_DATA_DIR` — no shared-volume assumption.
- Pricing: `RunPodPrice` (GraphQL `gpuTypes.lowestPrice.uninterruptablePrice`) is the default, fault-tolerant to cached/unknown. Dashboard surfaces cost confidence (`Measured` / `Cached` / `Unknown` / `Static`).
- CI publishes `acheron-qwen3tts-runpod` images to GHCR on tag and `main`.
- See [Layer 8a TTS design](./layer-8a-tts-worker.md).

### Sub-project 8b — ASR worker

Replay the blueprint for `workers/whisperv3large/` against Whisper-v3 Large. gRPC `Artifact` mode is exercised if this sub-project picks the gRPC transport; otherwise HTTP + multipart.

### Sub-project 8c — Translation worker

Replay the blueprint for `workers/translategemma/` against `google/translategemma-12b-it`. Supersedes an earlier stub spec that predates the blueprint.

---

## Layer 9 — Decomposition

Layer 9 addresses pipeline-level streaming and async Redis, decomposed into three independent sub-projects. See [Layer 9 design spec](./pipeline-streaming.md).

### Sub-project 9b-i — Store ABC + InMemory async

Migrate `WorkerStore` and `JobStore` ABCs to `async def`. Update `InMemoryWorkerStore` and `InMemoryJobStore` trivially (no I/O). Update all call sites in `health.py`, `step_handler.py`, and `orchestrator.py` to `await`. Validates with existing unit tests.

### Sub-project 9b-ii — Redis async backend

Swap `RedisWorkerStore` and `RedisJobStore` from `redis.Redis` to `redis.asyncio.Redis`. `__init__` does no I/O; an instance method `async def connect()` (called from `Orchestrator.start()`) does `await self._redis.ping()`. `close()` becomes `async def` and calls `await self._redis.aclose()`. Integration tests via testcontainers.

### Sub-project 9a — Streaming pipeline executor

New `StreamingExecutor` is the new default strategy. Per-stage `asyncio.Queue` pipeline with bounded backpressure (linear topology — current plans have 4-5 single-step stages), fail-fast all-or-nothing job semantics, per-step `asyncio.wait_for()` timeout (default 1800s, configurable per instance), and `StepCache.save_outputs()` per chunk as resumability foundation. `StepCache` itself becomes async via aiofiles. New `PipelineError(AcheronError)` in `core/errors.py` for executor-internal invariants (cache, sentinel protocol, unexpected stage exceptions). `ExecutorStrategy.STREAMING` added; API and client default changed to `"streaming"`; `BatchAsyncExecutor` remains as opt-in. Per-chapter parallelism deferred to a future layer (plans are linear today).

---

## Layer 10 — Built-in Local Workers & Resuming Core

Implement real built-in workers, yaml-based configuration settings, and execution resuming functionality. See [Layer 10 design spec](./local-workers-and-resuming.md).

- **Local Workers**: Implement standard ZIP+ElementTree parsing for EPUB chapter extraction, chunking logic via NLTK, and M4B concatenator and chapterizer metadata utilizing `ffmpeg` and `ffprobe`.
- **Configuration settings**: Introduce `acheron.yaml` utilizing `pydantic` configuration validation in `src/acheron/shell/config.py`.
- **Resuming Core**: Check cache validity before starting executor stages. Add `/jobs/{id}/resume` POST API route and `acheron job resume` Click CLI subcommand.

---

## Layer 11 — Decoupled Platform Health Checks & Dashboard Integration

Implement decoupled provider health checks, modular container image compilation, and dashboard updates. See [Layer 11 design spec](./deployment-and-dashboard.md).

- **Decoupled health checks**: Abstract `HealthProvider` class configuration mapping platform-specific endpoints (RunPod/HF) using API keys defined in `acheron.yaml`. ✅
- **Dashboard Updates**: Backend status endpoint (green/red dot) and worker status badges + error viewer. ✅
- **Modular Workers + CI/CD**: Isolated worker packages and GHCR publish workflow. Deferred to a separate plan (requires Docker/CUDA build context).

