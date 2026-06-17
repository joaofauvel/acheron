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

- **HttpWorker** — wraps a FastAPI/REST endpoint (RunPod, HuggingFace Inference Endpoints)
- **GrpcWorker** — wraps a gRPC service (future)
- **LocalWorker** — calls a Python function directly (CPU steps: extraction, chunking, packaging)

Workers register their transport endpoint. The orchestrator dispatches via the abstract interface.

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

## Planner

The Planner takes a job request and produces a validated plan:

1. **Query worker registry** — get all registered workers and their capabilities
2. **Validate language path** — check that the requested source→target language is supported by available workers (ASR for source audio language, translation for source→target, TTS for target language)
3. **Compile steps** — generate the DAG of pipeline steps based on input type
4. **Assign workers** — pre-select workers for each step (or leave for executor to decide at dispatch time)
5. **Persist plan** — save to `/data/jobs/{job_id}/plan.json`

If the language path is invalid (e.g., no ASR worker supports the source language, or no TTS worker supports the target language), the planner rejects the job before any compute is spent.

## Executors

### Executor Interface

```python
class Executor(ABC):
    @abstractmethod
    async def run(self, plan: Plan) -> PlanResult: ...
```

### Implementations

**SequentialExecutor** — walks the plan step by step. Useful for debugging and dry-runs.

**AsyncExecutor** — traverses the DAG, runs all independent steps concurrently. Chapters are naturally independent; within a chapter, steps are sequential.

**BatchAsyncExecutor** (default) — extends AsyncExecutor with batch semantics. When dispatching synthesis for a chapter, pushes all chunks in a single batch to the `StreamingWorker`. Matches the "per-chapter batch streaming" pattern for maximum GPU throughput.

The executor strategy is set at job creation time.

## Text Chunking Engine

Runs on the orchestrator (CPU-only, NLTK punkt tokenizer).

- **Max target length**: 250 characters per chunk
- **Fallback logic**: If a sentence exceeds 250 chars, split recursively on commas, semicolons, em-dashes. If no punctuation, hard split on nearest whitespace < 250 chars.
- **Output**: JSON payloads with `chapter_id`, `sequence_id`, `text`

## Worker Registry

Workers self-register on startup via HTTP POST to the orchestrator:

```
POST /orchestrator/register
{
  "endpoint": "https://abc-123.runpod.ai",
  "transport": "http",
  "capabilities": {
    "worker_type": "TTS",
    "supported_languages_in": ["es", "en", "fr"],
    "supported_languages_out": ["es", "en", "fr"],
    "batch_capable": true,
    "model_source": "huggingface:Qwen/Qwen3-TTS-12Hz-1.7B"
  }
}
```

The orchestrator monitors registered workers via health checks (every 30s). If a worker is unreachable for 3 consecutive checks, it's removed from the registry.

## Capability Discovery

The orchestrator exposes a `/capabilities` endpoint that aggregates all registered workers' language support:

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
acheron resume job-xyz

# Force fresh start
acheron resume job-xyz --force-fresh
```

On resume, the executor checks each step: if the step is `complete` and its output directory has a valid manifest, skip it. Corrupted or partial cache → re-run that step.

## Error Handling

| Failure Mode | Strategy |
|---|---|
| Network drop (push to worker) | Exponential backoff retry (tenacity). 5 failures → mark step FAILED, continue other chapters. |
| Worker crash mid-job | Health check fails 3 consecutive times → remove from registry. Re-dispatch to another worker of same type. No alternative → mark step FAILED. |
| Sequence gap in output | Packaging validates sequence continuity. Gap → abort that chapter, don't produce broken audio. |
| Invalid language path | Planner rejects at plan compilation. No GPU time spent. |
| Worker timeout | Per-job timeout (default: 30min). Timeout → mark step FAILED. |

## Cost Containment

- **RunPod idle shutdown**: If queue empty for 300s, orchestrator calls RunPod API to terminate pod.
- **Per-job cost tracking**: Each `JobResult` includes `JobMetrics` with `cost_estimate`. Orchestrator aggregates per-job and per-worker.
- **Cost sources**: RunPod ($/hr × GPU seconds), OpenRouter (tokens × price/token), local workers ($0).

## Docker Deployment

### Orchestrator (Docker Compose)

```yaml
services:
  orchestrator:
    build: ./orchestrator
    ports:
      - "8000:8000"
    volumes:
      - ./data:/data
      - ./config:/config
    environment:
      - REDIS_URL=redis://queue:6379
      - ACHERON_DATA_DIR=/data
    depends_on:
      - queue

  queue:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data

  dashboard:
    build: ./dashboard
    ports:
      - "8080:8080"
    environment:
      - ORCHESTRATOR_URL=http://orchestrator:8000

volumes:
  redis-data:
```

### GPU Worker (RunPod / HuggingFace)

Separate Docker images per worker type, built on RunPod PyTorch base or HuggingFace TGI base. Models pre-downloaded at build time or fetched on first run.

Worker exposes:
- `POST /execute` — single job
- `POST /submit-batch` — streaming batch
- `GET /poll/{handle}` — batch status
- `GET /health` — liveness
- `GET /capabilities` — self-description

## Dashboard

HTMX + Jinja, separate container, polling orchestrator API.

**Views:**
- **Jobs** — list with status, per-chapter progress bars, step pipeline (✓/⏳/○), cost, duration
- **Workers** — registered workers, type, transport, health
- **Cost** — per-worker breakdown with usage metrics

Forward auth support (reads auth header from reverse proxy).

## CLI

```bash
# Job submission
acheron submit book.epub --src en --dest es --executor batch_async
acheron submit podcast.mp3 --src en --dest es --asr whisper-v3

# Status
acheron status job-xyz
acheron status job-xyz --verbose

# Jobs
acheron jobs --active
acheron jobs --completed

# Workers
acheron workers

# Capabilities
acheron capabilities
acheron capabilities --src en
acheron capabilities --dest es

# Resume
acheron resume job-xyz
acheron resume job-xyz --force-fresh

# Cancel
acheron cancel job-xyz

# Package
acheron package job-xyz --output ./output/
```

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
- Python 3.12+
- FastAPI
- NLTK (punkt tokenizer)
- FFmpeg (concatenation, M4B container packaging with chapter metadata)
- tenacity (retry logic)
- Redis client (redis-py)
- httpx (worker communication)
- click (CLI framework)

**TTS Worker:**
- PyTorch
- HuggingFace Transformers
- Qwen3-TTS-12Hz-1.7B

**ASR Worker:**
- PyTorch
- HuggingFace Transformers
- Whisper-v3 (Large)

**Dashboard:**
- Python 3.12+
- FastAPI or Starlette
- Jinja2
- HTMX
- httpx (API client)

**CLI:**
- Python 3.12+
- click (CLI framework)
- httpx (API client)
- rich (terminal formatting)
