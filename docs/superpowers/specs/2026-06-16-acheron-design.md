# Acheron — Design Spec

**Distributed Asynchronous Audio-Transformation Pipeline**

Acheron converts EPUB or audio input into offline chapterized audiobooks in a target language, compliant with audiobook players (M4B format). It uses a plan-then-execute architecture with abstract worker interfaces, supporting GPU-heavy workloads on transient cloud instances (RunPod, HuggingFace) and lightweight orchestration on a persistent server.

## Architecture Overview

### Core Design Decisions

- **Plan-Then-Execute**: Jobs compile into validated pipeline plans before any GPU time is spent. Plans are immutable DAGs that the executor traverses step-by-step.
- **Abstract Worker Interface**: All compute (ASR, TTS, translation) goes through a unified `Worker` abstraction. Workers register capabilities, the orchestrator dispatches jobs. Transport (HTTP, gRPC) is an implementation detail.
- **Chunking as Plan Step**: Text chunking is explicit in the plan, not buried in the TTS worker. Enables granular progress tracking and batch dispatch.
- **Streaming Workers**: TTS/ASR workers support batch submission via `StreamingWorker` interface for maximum GPU throughput.
- **Cache-Backed Resumability**: Step outputs are cached locally. Failed jobs resume from last completed step unless forced fresh.

### System Architecture

```
Docker Compose (orchestrator host)
┌─────────────────────────────────────────────────────┐
│  orchestrator (FastAPI)  │  queue (Redis)           │
│  dashboard (HTMX/Jinja)  │                          │
└─────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────┐
│    Abstract Worker Interface            │
│  ┌───────────────┐ ┌────────────────┐  │
│  │ capabilities()│ │ health()       │  │
│  │ execute()     │ │                │  │
│  └───────────────┘ └────────────────┘  │
└───────────────────┬─────────────────────┘
                    │
     ┌──────────────┼──────────────┐
     ▼              ▼              ▼
┌──────────┐ ┌──────────┐ ┌──────────────┐
│ASR Worker│ │TTS Worker│ │Translation   │
│(RunPod/  │ │(RunPod/  │ │Worker        │
│ HF)      │ │ HF)      │ │(API/local)   │
└──────────┘ └──────────┘ └──────────────┘
```

## Worker Abstraction

### Base Interface

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

class WorkerType(Enum):
    EXTRACTION = "extraction"
    CHUNKING = "chunking"
    TRANSLATION = "translation"
    ASR = "asr"
    TTS = "tts"
    PACKAGING = "packaging"

class JobStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"

@dataclass
class WorkerCapabilities:
    worker_type: WorkerType
    supported_languages_in: set[str]   # ISO 639-1 codes
    supported_languages_out: set[str]
    supported_formats_in: set[str]     # e.g. {"epub", "mp3", "wav", "md"}
    supported_formats_out: set[str]    # e.g. {"wav", "m4b", "md"}
    max_payload_bytes: int | None
    batch_capable: bool
    model_source: str | None           # e.g. "huggingface:Qwen/Qwen3-TTS-12Hz-1.7B"
    metadata: dict                     # model name, VRAM usage, etc.

@dataclass
class Job:
    job_id: str
    job_type: WorkerType
    payload: dict
    chapter_id: str
    sequence_ids: list[int] | None

@dataclass
class OutputFile:
    path: str
    filename: str
    size_bytes: int
    checksum: str       # SHA-256
    content_type: str   # e.g. "audio/wav", "text/markdown"

@dataclass
class JobResult:
    job_id: str
    status: JobStatus
    outputs: list[OutputFile]
    metrics: JobMetrics
    error: str | None

@dataclass
class JobMetrics:
    duration_seconds: float
    gpu_seconds: float | None
    tokens_in: int | None
    tokens_out: int | None
    cost_estimate: float | None
    cost_basis: CostBasis | None       # MEASURED | CACHED | STATIC | UNKNOWN (since Layer 8a)

class CostBasis(Enum):
    MEASURED = "measured"   # fresh provider rate multiplied by actual gpu_seconds
    CACHED   = "cached"     # serving last-known rate; provider API currently unavailable
    STATIC   = "static"     # configured $/hr (no API call) or ZeroPrice (operator opted out)
    UNKNOWN  = "unknown"    # never refreshed or cache expired and refresh failed — cost is None

class Worker(ABC):
    @abstractmethod
    async def capabilities(self) -> WorkerCapabilities: ...

    @abstractmethod
    async def execute(self, job: Job) -> JobResult: ...

    @abstractmethod
    async def health(self) -> bool: ...
```

### Streaming Worker Extension

For TTS and ASR workers that support batch submission:

```python
@dataclass
class BatchJob:
    batch_id: str
    jobs: list[Job]

@dataclass
class BatchStatus:
    batch_id: str
    total: int
    completed: int
    failed: int
    pending: int
    results: list[JobResult]

class StreamingWorker(Worker):
    @abstractmethod
    async def submit_batch(self, batch: BatchJob) -> str:
        """Submit a batch, return a batch_handle."""
        ...

    @abstractmethod
    async def poll_batch(self, batch_handle: str) -> BatchStatus:
        """Check batch progress."""
        ...

    @abstractmethod
    async def collect_results(self, batch_handle: str) -> list[JobResult]:
        """Pull all completed results."""
        ...
```

### Transport Implementations

- **HttpWorker** — wraps a FastAPI/REST endpoint (RunPod, HuggingFace Inference Endpoints). Response dispatch is data-driven via `Content-Type`: `multipart/mixed` (the layered default, since [Layer 8a](./2026-06-22-layer8a-tts-worker-design.md)) is parsed into `OutputFile`s materialized into the orchestrator's `ACHERON_DATA_DIR`; `application/json` is the legacy path that round-trips `JobResult` with pre-materialized `OutputFile.path` strings and is preserved for the HTTP stubs.
- **GrpcWorker** — wraps a gRPC bidirectional streaming service (Layer 6). `OutputChunk` carries an `oneof payload` of either legacy `pcm_data` (raw PCM, for low-latency live-streaming variants) or `Artifact` parts (structured output, since [Layer 8a](./2026-06-22-layer8a-tts-worker-design.md)). The orchestrator-side `GrpcWorker` consumes `Artifact` parts via the shared `_materialize_artifact` / `_build_result` helpers (in `shell/transports/_multipart.py`), identical to the HTTP multipart path; legacy `pcm_data` mode is preserved for the live-stream use case. Internal to the worker — orchestrator interface unchanged.
- **LocalWorker** — calls a Python function directly (CPU steps: extraction, chunking, packaging). The orchestrator auto-registers built-in local workers for `EXTRACTION`, `CHUNKING`, and `PACKAGING` if no external worker of that type is registered, so the default stack can run end-to-end without any extraction/chunking/packaging workers deployed.

Workers register their transport endpoint. The orchestrator dispatches via the abstract interface. gRPC workers register without a URL scheme (`host:port`), since `grpc.insecure_channel` rejects `http://host:port` as a malformed hostname.

### Output contract (since Layer 8a)

Workers return artifacts as bytes — never as filesystem paths into the orchestrator's volume. The orchestrator materializes received bytes into `ACHERON_DATA_DIR/{job_id}/{step_id}/{filename}` and computes `size_bytes` + SHA-256 `checksum` itself, so a worker need not share a filesystem with the orchestrator. This makes physically-separated workers (RunPod serverless, Hugging Face Inference Endpoints, dedicated remote hosts) first-class. A legacy JSON-`path` path remains for the HTTP stubs, but new workers built on the [`acheron.worker_sdk`](./2026-06-22-layer8a-tts-worker-design.md) blueprint use the `multipart/mixed` response shape.

### Worker Registry

`WorkerRegistry` is an in-memory store (backed by Redis) mapping worker IDs to their endpoint, capabilities, and health status. The orchestrator's `WorkerRegistry` service manages registration, health monitoring, and lookup.

## Pipeline Plan

### Plan Schema

```json
{
  "plan_id": "plan-abc123",
  "job_id": "job-xyz",
  "source_type": "epub",
  "source_language": "en",
  "target_language": "es",
  "executor_strategy": "batch_async",
  "steps": [
    {
      "step_id": "extract",
      "type": "EXTRACTION",
      "depends_on": [],
      "status": "pending",
      "payload": {"source_path": "/input/book.epub"}
    },
    {
      "step_id": "chunk-ch1",
      "type": "CHUNKING",
      "depends_on": ["extract"],
      "status": "pending",
      "payload": {"chapter_id": "ch1", "text": "..."}
    },
    {
      "step_id": "translate-ch1",
      "type": "TRANSLATION",
      "depends_on": ["chunk-ch1"],
      "status": "pending",
      "payload": {"chapter_id": "ch1", "chunks": [...]}
    },
    {
      "step_id": "synthesize-ch1",
      "type": "TTS",
      "depends_on": ["translate-ch1"],
      "status": "pending",
      "payload": {"chapter_id": "ch1", "chunks": [...]},
      "batch": true
    },
    {
      "step_id": "package-ch1",
      "type": "PACKAGING",
      "depends_on": ["synthesize-ch1"],
      "status": "pending",
      "payload": {"chapter_id": "ch1"}
    }
  ]
}
```

### Step Status Lifecycle

`pending` → `running` → `complete` | `failed`

Plans persist to disk after each step completes. Failed steps can be retried; completed steps are skipped on resume.

### Plan Data Model

```python
class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"

class ExecutorStrategy(Enum):
    SEQUENTIAL = "sequential"
    ASYNC = "async"
    BATCH_ASYNC = "batch_async"

@dataclass(frozen=True)
class EpubRequest:
    source_path: str
    source_language: str
    target_language: str

@dataclass(frozen=True)
class AudioRequest:
    source_path: str
    source_language: str
    target_language: str
    asr_model: str | None = None

type JobRequest = EpubRequest | AudioRequest

@dataclass
class PlanStep:
    step_id: str
    type: WorkerType
    depends_on: list[str]
    status: StepStatus
    payload: dict
    batch: bool = False

@dataclass
class Plan:
    plan_id: str
    job_id: str
    source_type: str            # "epub" | "audio"
    source_language: str
    target_language: str
    executor_strategy: ExecutorStrategy
    steps: list[PlanStep]

@dataclass
class PlanResult:
    plan_id: str
    status: str                 # "completed" | "failed" | "partial"
    completed_steps: int
    total_steps: int
    outputs: list[OutputFile]
    total_cost: float
    total_duration_seconds: float
    errors: tuple[str, ...]     # error messages from failed steps
```

### Pipeline Steps by Input Type

**EPUB Input:**
```
extract (CPU) → chunk (CPU) → translate (API/worker) → synthesize (GPU/worker) → package (CPU)
```

**Audio Input:**
```
extract (CPU) → transcribe (GPU/ASR) → chunk (CPU) → translate (API/worker) → synthesize (GPU/worker) → package (CPU)
```

**Same-language optimization:** When `source_language == target_language`, the translate step is skipped entirely. The synthesize step depends directly on chunk (or transcribe for audio). No translation worker is required for same-language jobs.

## Planner

The Planner takes a job request and produces a validated plan:

1. **Query worker registry** — get all registered workers and their capabilities
2. **Validate language path** — check that the requested source→target language is supported by available workers (ASR for source audio language, translation for source→target when languages differ, TTS for target language)
3. **Compile steps** — generate the DAG of pipeline steps based on input type. When `source_language == target_language`, the translate step is omitted and synthesize depends directly on the preceding step.
4. **Assign workers** — pre-select workers for each step (or leave for executor to decide at dispatch time)
5. **Persist plan** — save to `/data/jobs/{job_id}/plan.json`

If the language path is invalid (e.g., no ASR worker supports the source language, no translation worker supports the source→target pair, or no TTS worker supports the target language), the planner rejects the job before any compute is spent.

## Executors

### Executor Interface

```python
class Executor(ABC):
    @abstractmethod
    async def run(self, plan: Plan) -> PlanResult: ...
```

### Step Handler

Executors take a `StepHandler` callable that dispatches individual steps:

```python
type StepHandler = Callable[[PlanStep, Plan], Awaitable[JobResult]]
```

### Implementations

**SequentialExecutor** — walks the plan step by step via topological order. Skips dependents of failed steps. Useful for debugging and dry-runs.

**AsyncExecutor** — traverses the DAG in dependency waves. All steps in a wave run concurrently via `asyncio.gather`. Dependents of failed steps are skipped.

**BatchAsyncExecutor** — extends AsyncExecutor with batch semantics. Batch-flagged steps receive all outputs from completed preceding steps so the handler can construct a `BatchJob` with the correct payloads.

**StreamingExecutor** (default for all new jobs) — per-stage pipeline using bounded `asyncio.Queue`s. All stages of a plan run as concurrent tasks in a single outer `asyncio.TaskGroup`, with `asyncio.Queue(maxsize=4)` between adjacent stages providing native backpressure. Any stage failure cancels all sibling stages immediately (fail-fast, all-or-nothing semantics). Step results are written to `StepCache` (now async, via aiofiles) as they complete — not accumulated in memory. `asyncio.wait_for()` enforces a per-step timeout (default 1800s) at every worker dispatch. A `None` sentinel on each queue's `finally` block lets downstream stages drain and exit on cancel. `PlanResult.outputs` is built by scanning `StepCache` at the end; `total_cost` is the sum of per-step `JobMetrics.cost_estimate`; `completed_steps` reflects the actual number of steps whose manifest was readable (not 0 on partial-success). See [Layer 9 spec](./2026-06-18-pipeline-streaming-design.md) and the [9a focused spec](./2026-06-19-layer9a-streaming-executor-design.md). Today's plans have a linear topology (extract → chunk → translate? → synthesize → package); per-chapter parallelism is a future layer.

All executors capture error details in `PlanResult.errors`.

### Executor Factory

```python
def create_executor(strategy: ExecutorStrategy, handler: StepHandler) -> Executor: ...
```

Creates the appropriate executor instance based on the strategy enum.

The executor strategy is set at job creation time.

## Text Chunking Engine

Runs on the orchestrator (CPU-only, NLTK punkt tokenizer).

- **Max target length**: 250 characters per chunk
- **Fallback logic**: If a sentence exceeds 250 chars, split recursively on commas, semicolons, em-dashes. If no punctuation, hard split on nearest whitespace < 250 chars.
- **Output**: JSON payloads with `chapter_id`, `sequence_id`, `text`

## Worker Registry

Workers self-register via `POST /workers` on the orchestrator API. The registry state lives in a `WorkerStore` (abstract base class) with two implementations: `InMemoryWorkerStore` (default, lost on restart) and `RedisWorkerStore` (persists across restarts). Backend selection is via the `ACHERON_STORE_BACKEND` env var (`memory` or `redis`, default `memory`). The orchestrator and `create_app` use the factory `create_worker_store()` by default; tests pass instances explicitly.

**Registration security:** Shared secret model. The orchestrator uses the `registration_token` configuration value (set via `acheron.yaml` or overridden via `ACHERON_REGISTRATION_TOKEN` environment variable). `POST /workers` requires `Authorization: Bearer <token>` header. Missing or invalid token → 401. If the token is unset, the orchestrator automatically generates a random secure registration token and persists it to `{data_dir}/.registration_token` at startup. To run with open registration, the user must explicitly opt in by setting `ACHERON_OPEN_REGISTRATION=1`.

**Platform health checks (Layer 11):** When the orchestrator's HTTP/gRPC probe fails, the `HealthMonitor` consults a `HealthProvider` plugin (configured in `acheron.yaml` under `providers:`) named by the worker's `capabilities.metadata["health_provider"]`. The provider queries the platform API (RunPod Serverless endpoints, Hugging Face Inference Endpoints) using `capabilities.metadata["health_endpoint_id"]` and returns a `WorkerStatus` (`HEALTHY` | `BOOTING` | `OFFLINE`). Booting workers are not removed from the registry; offline workers follow the existing 3-strike removal. The worker's `status` and `last_error` are persisted by the store and surfaced on the `/workers` API response.

**Backend status partial:** The orchestrator serves `GET /partials/status` (an HTML snippet) that the dashboard proxies to render a green "Connected" / red "Disconnected" indicator next to the heading.

**Health monitoring:** A `HealthMonitor` background task polls all registered workers every 30s, dispatching by transport:
- HTTP workers: `GET {endpoint}/health`
- gRPC workers: gRPC `Health.Check` (requires the worker to register a `HealthServicer` returning `SERVING`)
- Local workers: always healthy (no remote endpoint to probe)

After 3 consecutive failures, the worker is removed from the registry. The monitor runs as an asyncio task, started/stopped via the orchestrator's FastAPI lifespan.

**Step dispatch:** A `StepHandler` dispatches plan steps to workers by matching `step.type` and the plan's language pair. Language matching logic:
- Translation: source in `supported_languages_in` AND target in `supported_languages_out`
- ASR: source in `supported_languages_in`
- TTS: target in `supported_languages_in` AND target in `supported_languages_out`
- Extraction/Chunking/Packaging: no language check

**Storage backends:** The `WorkerStore` and `JobStore` ABCs live in `src/acheron/shell/stores/`. All methods are `async def`. The ABCs expose a concrete `async def connect()` with a no-op default; `InMemoryWorkerStore` / `InMemoryJobStore` inherit it for free, and `RedisWorkerStore` / `RedisJobStore` override it to `await self._redis.ping()`. `Orchestrator.start()` awaits `connect()` on both stores before doing any work. The store `__init__` does no I/O (Redis is lazy). `close()` is `async def`; on Redis it calls `await self._redis.aclose()`. Implementations:
- `InMemoryWorkerStore` / `InMemoryJobStore` — async, dict-backed, used for dev and tests
- `RedisWorkerStore` / `RedisJobStore` — `redis.asyncio.Redis` client, JSON-serialized state in Redis hashes and sets. The `TrackedJob` round-trip persists the full job state including the `Plan` and `PlanResult`.

`Orchestrator.close()` releases store resources with exception isolation (one store's close failing doesn't skip the other), called from the FastAPI lifespan. `Orchestrator.close()` teardown must run after `Orchestrator.shutdown()` has drained in-flight `_execute` tasks — otherwise a job whose `put()` races the Redis pool teardown will see a `ConnectionError` mid-flight.

**Local worker handlers** are kept in a side dict on the orchestrator (`_local_handlers: dict[str, LocalJobHandler]`), not in `RegisteredWorker.metadata`. Worker `metadata` is a JSON-serializable contract used by persistence backends; coroutine handlers are not serializable. Use the `Orchestrator.register_worker(handler=...)` keyword-only parameter to register a local worker.

**Production (Layer 7):** Layers 7a (storage abstraction + Redis backend), 7b (production compose hardening), and 7c (TLS support) are done. See [implementation roadmap](./2026-06-16-implementation-roadmap.md).

**Layer 9 — Pipeline Streaming & Async Redis:** `StreamingExecutor` (9a) and async Redis stores (9b). See [Layer 9 spec](./2026-06-18-pipeline-streaming-design.md).

**TLS (Layer 7c):** Acheron services support TLS via environment variables; cert provenance and reverse proxying are the deployer's responsibility. Three env vars control it: `ACHERON_TLS_CERT_FILE` and `ACHERON_TLS_KEY_FILE` for server-side TLS (both required together), and `SSL_CERT_FILE` for client-side trust (httpx and stdlib `ssl` honor it automatically). Unset env vars = HTTP. Production deploys generate real certs (Let's Encrypt via cert-manager, internal CA, etc.) with the right SANs; no Acheron code change. See the [Layer 7c sub-spec](./2026-06-18-layer7c-tls.md) for the env-var contract, dev cert script, and compose integration.

**Layer 7b details:**

- **Healthchecks** on every service in `docker-compose.yml`: `interval: 30s, timeout: 5s, retries: 3, start_period: 10s`. HTTP stubs hit their existing `/health` endpoint. The gRPC stub exposes a FastAPI sidecar on a separate `WORKER_HTTP_PORT` (default 9002) since gRPC has no native HTTP probe.
- **`depends_on: condition: service_healthy`** for every service that depends on another. Removes startup races.
- **Named volumes** for persistence: `acheron-data` mounted at `/data` on the orchestrator (with `ACHERON_DATA_DIR=/data/jobs` for the plan cache) and `redis-data` for the redis service.
- **Fail-fast on bad data dir**: `Orchestrator.__init__` checks `ACHERON_DATA_DIR` is writable (mkdir + write probe + read back + delete). Raises `AcheronError` with a clear message if not.
- **`ACHERON_DATA_DIR`** env var, default `/data/jobs`. Read by `create_app` when `data_dir` is not provided. Tests pass `tmp_path` explicitly to override.
- **No resource limits** — deferred to a future sub-project.

## Capability Discovery

The orchestrator exposes a `/capabilities` endpoint that aggregates language pairs achievable by the planner. Only pairs where all required worker types are registered are included: TTS for the target language, and a TRANSLATION worker when source and target differ. Same-language pairs (e.g., en→en) require only TTS.

```
GET /capabilities
→ {
    "language_pairs": [
      {"src": "en", "dst": "es", "workers": ["runpod-asr", "openrouter", "runpod-tts"]},
      {"src": "en", "dst": "fr", "workers": ["runpod-asr", "openrouter", "runpod-tts"]},
      {"src": "fr", "dst": "en", "workers": ["openrouter", "runpod-tts"]}
    ]
  }
```

The CLI exposes this as `acheron capabilities [--src LANG] [--dest LANG]`.

## Caching & Resumability

### Cache Model

- Each completed step writes outputs to `/data/jobs/{job_id}/{step_id}/`
- A manifest file lists outputs with checksums and metadata
- The plan persists to disk after each step completes

### Resume Flow

```bash
# Resume from last completed step
acheron job resume job-xyz

# Force fresh start
acheron job resume job-xyz --force-fresh
```

On resume, the executor checks each step: if the step is `complete` and its output directory has a valid manifest, skip it. Corrupted or partial cache → re-run that step. See the [local workers and resuming spec](./2026-06-20-local-workers-and-resuming-design.md) for endpoint details.

## Error Handling

| Failure Mode | Strategy |
|---|---|
| Network drop (push to worker) | Exponential backoff retry (tenacity). 5 failures → raise `WorkerError`, job fails. |
| Worker crash mid-job | Health check fails 3 consecutive times → remove from registry. Re-dispatch to another worker of same type. No alternative → `WorkerError`, job fails. |
| Stage failure (`StreamingExecutor`) | Outer `asyncio.TaskGroup` cancels all sibling stages immediately. Job marked `failed`; `completed_steps` reflects the count of steps that actually wrote a manifest (not 0 on partial success). No partial audiobook written. |
| Sequence gap in output | Packaging validates sequence continuity. Gap → abort that stage. |
| Invalid language path | Planner rejects at plan compilation. No GPU time spent. |
| Worker timeout | Per-step `asyncio.wait_for()` timeout (default 1800s). Raises `WorkerError("step <id> timed out after Ns")`, fails the job. |
| Unexpected stage failure | Wrapped as `PipelineError(AcheronError)` with the stage id and exception type: `PipelineError("unexpected failure in stage {id}: {Type}") from exc`. Never silently swallowed. |
| Cache read/write failure | `StepCache.save_outputs` raises `OSError` or `CacheCorruptedError`; the stage wraps as `PipelineError("save_outputs failed for step {id}") from exc`. A corrupted manifest on the cache scan path is silently skipped (treated as "no outputs for this step"). |
| `Orchestrator.close()` during in-flight dispatch | Callers must `shutdown()` first; close() teardown tears down the Redis pool, which forces a `ConnectionError` on any pending `put()`. Documented in the close() docstring. |

## Cost Containment

- **RunPod idle shutdown**: If queue empty for 300s, orchestrator calls RunPod API to terminate pod.
- **Per-job cost tracking**: Each `JobResult` includes `JobMetrics` with `cost_estimate` and `cost_basis` (since [Layer 8a](./2026-06-22-layer8a-tts-worker-design.md)). Orchestrator aggregates per-job and per-worker. `cost_estimate is None` (basis `UNKNOWN`) means the worker could not price the step — it is skipped from `PlanResult.total_cost`, never silently coerced to `$0.00`.
- **Cost sources**: RunPod serverless ($/hr fetched from the RunPod GraphQL `gpuTypes.lowestPrice.uninterruptablePrice`, multiplied by actual GPU-seconds — fault-tolerant fallback to cached/unknown), OpenRouter (tokens × price/token), local workers (`ZeroPrice`, basis `STATIC`). Worker-side pricing is best-effort: a transient API outage at startup does not block worker registration; mid-flight outages report `None`; `PlanResult.total_cost_basis` is the least-confident basis across steps.
- **Dashboard rendering**: the `Cost` table visualizes `cost_basis` as a colored badge (`Measured` green / `Cached` amber / `Unknown` gray / `Static` neutral) with a short, plain-English note in an adjacent column so operators can tell "real numbers from the provider" apart from "we gave up."

## Docker Deployment

### Orchestrator (Docker Compose)

The full production-ready compose is in `docker-compose.yml` at the repo root. Key elements:

- Every service has a `healthcheck` block
- The orchestrator uses the `acheron-data` named volume and reads `ACHERON_DATA_DIR=/data/jobs`
- All inter-service `depends_on` use `condition: service_healthy`
- The gRPC stub runs an HTTP sidecar on `WORKER_HTTP_PORT` (default 9002) so Docker can probe it

A representative service:

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

### GPU Worker (Decoupled & Plug-and-play) — Layer 8

Layer 8 is decomposed into three independent sub-projects (8a TTS, 8b ASR, 8c Translation); each gets its own spec → plan → implementation cycle. See [Layer 8a TTS design](./2026-06-22-layer8a-tts-worker-design.md) and the [implementation roadmap](./2026-06-16-implementation-roadmap.md).

Workers are completely decoupled, model-specific containers built with PyTorch and CUDA. They depend on the [`acheron.worker_sdk`](./2026-06-22-layer8a-tts-worker-design.md) blueprint subpackage of the orchestrator's `acheron` wheel — which imports only `acheron.core` types and never `acheron.shell`. They communicate with the orchestrator solely through the standardized REST/gRPC interfaces and never touch the orchestrator's I/O code.

* **qwen3tts-worker:** Run Qwen3-TTS-12Hz-1.7B-CustomVoice for text synthesis via 9 built-in premium speakers. Min 24GB VRAM. Layer 8a (in progress).
* **whisperv3large-worker:** Run Whisper-v3 Large ASR for audio transcription. Min 10GB VRAM. Layer 8b (planned).
* **translategemma-worker:** Run TranslateGemma-12B (`google/translategemma-12b-it`) for text translation across 55 languages. Min ~16GB VRAM. Used when `source_language != target_language`; skipped otherwise. Layer 8c (planned). Supersedes the stub spec at [2026-06-21-translategemma-worker-design.md](./2026-06-21-translategemma-worker-design.md).

**Deployment & API:**
* Workers ship one deployment mode each — the blueprint is one mode per worker. v1 (qwen3tts) ships RunPod Serverless only; a local-GPU `Qwen3TTSLocalHandler` would be a separate future worker package, not a runtime flag.
* RunPod Serverless workers are published to GHCR by CI on tag and `main`. The deployer pulls the image into a RunPod template, configures the endpoint (GPU list, idle timeout, network volume for cached HF weights), and runs a generic `acheron-worker-sdk-edge` container alongside the orchestrator that registers with the orchestrator and forwards `/execute` calls to the RunPod endpoint via `runpod.Endpoint(id).run(...).output(timeout=N)`.
* Exposed endpoints: `/health` (health probe), `/capabilities` (supported target/source languages), and `/execute` (job execution, multipart/mixed response). On container boot (the edge container), workers self-register their endpoint URL and capabilities with the orchestrator (`POST /workers`), tagging `capabilities.metadata["health_provider"] = "runpod"` and `capabilities.metadata["health_endpoint_id"] = <endpoint id>` so the existing `RunPodHealthProvider` cold-start detection (Layer 11) handles Booting/Offline state transitions.

## Dashboard

HTMX + Jinja2, separate container (`dashboard/`), polling orchestrator API every 2s per section.

**Layout:** Single scrollable page with three sections:

- **Jobs** — table with job ID, status badge, progress bar (completed/total), cost, duration. Gracefully handles missing cost/duration data.
- **Workers** — table with worker ID, type, endpoint, transport, health indicator (green/red dot), failure count.
- **Cost** — table with job ID, status, cost, duration, steps completed.

**Configuration:** Reads `ACHERON_URL` env var for the orchestrator base URL (default `http://localhost:8000`). An explicit `orchestrator_url` argument to `create_app()` overrides the env var.

**Error handling:** If the orchestrator is unreachable, each section shows "No data" instead of crashing.

**Auth:** Forward auth via `X-Forwarded-User` header from reverse proxy. No auth logic in the app.

**Routes:**
- `GET /` — full page
- `GET /partials/jobs` — jobs table partial
- `GET /partials/workers` — workers table partial
- `GET /partials/cost` — cost table partial

**Dependencies:** FastAPI, Jinja2, HTMX (CDN), httpx

## CLI

**Dependencies:** click, httpx, rich

**Configuration:** `ACHERON_URL` env var sets the orchestrator base URL (default: `http://localhost:8000`).

**Error handling:** HTTP errors (4xx/5xx) are caught and displayed as `Error {status}: {detail}`. No raw tracebacks.

**Logging:** `acheron -v` enables verbose logging (DEBUG level) to stderr. Covers orchestrator events: job submission, plan compilation, execution, worker registration. Default: WARNING level (silent).

### Implemented Commands

```bash
# Service status
acheron status

# Job commands Click group:
acheron job submit book.epub --src en --dest es
acheron job submit book.epub --src en --dest es --executor streaming

acheron job status job-xyz
acheron job status job-xyz --verbose   # also shows errors

acheron job resume job-xyz
acheron job resume job-xyz --force-fresh

# Jobs listing (filters are client-side)
acheron jobs --active       # status == "running"
acheron jobs --completed    # status in ("completed", "failed")

# Workers
acheron workers

# Capabilities
acheron capabilities
acheron capabilities --src en
acheron capabilities --dest es
```

### Deferred Commands

These commands require API endpoints or worker-targeting plumbing that don't exist yet:

```bash
acheron job cancel job-xyz               # cancel a running job
acheron job package job-xyz --output ./  # package completed job to M4B (manual run)
acheron job submit podcast.mp3 --src en --dest es --asr whisper-v3   # pick a specific ASR worker
acheron job submit book.epub  --src en --dest es --tts  qwen3tts-1   # pick a specific TTS worker
acheron job submit book.epub  --src en --dest es --translation openrouter  # pick a translation worker
```

The `asr_model` field on `AudioRequest` / `SubmitJobRequest` is wired into the transcribe step's payload today, but `step_handler._language_matches` selects workers purely by `WorkerType` + language pair (first-registered-wins) — the field is a no-op. Per-step worker targeting (`asr_model`, `tts_model`, `translation_model` hints on the plan request, validated by the planner against the registry) is deferred to a separate sub-project. With one RunPod worker per `WorkerType` per deployment, language-match selection suffices in v1.

## Translation Engine

Workers report supported language pairs. Translation can be backed by:
- **API**: OpenRouter (DeepSeek-V3, Gemini 2.5 Flash)
- **Local**: TranslateGemma on GPU worker

Prompt template for API translation (dynamically templated with target language):

```
System: Eres un traductor profesional literario. Traduce el siguiente texto en {source_language} al {target_language}. Mantén estrictamente el formato Markdown, los nombres propios y la estructura de los párrafos. No agregues introducciones, comentarios ni notas al pie. Genera únicamente la traducción limpia.
```

## Dependencies

**Orchestrator:**
- Python 3.14+
- FastAPI
- NLTK (punkt tokenizer)
- FFmpeg (concatenation, M4B container packaging with chapter metadata)
- tenacity (retry logic)
- Redis client (redis-py) — Layer 7
- httpx (worker communication)
- click (CLI framework)

**TTS Worker (Layer 8a — `acheron.worker_sdk` blueprint + `qwen3tts` worker):**
- PyTorch + CUDA
- `qwen-tts` (HuggingFace Transformers + the official Qwen3-TTS package)
- Qwen3-TTS-12Hz-1.7B-CustomVoice (9 built-in speakers; voice cloning via Base deferred to a separate sub-project)
- Deployment mode: RunPod Serverless (24GB GPU list: L4 / A5000 / RTX 3090)
- Output: `multipart/mixed` with `BytesArtifact` per chapter WAV

**ASR Worker (Layer 8):**
- PyTorch + CUDA
- HuggingFace Transformers
- Whisper-v3 (Large)

**Translation Worker (Layer 8):**
- PyTorch + CUDA
- HuggingFace Transformers
- TranslateGemma-12B (`google/translategemma-12b-it`)

**Dashboard:**
- Python 3.14+
- FastAPI
- Jinja2
- HTMX
- httpx (API client)

**CLI:**
- Python 3.14+
- click (CLI framework)
- httpx (API client)
- rich (terminal formatting)
