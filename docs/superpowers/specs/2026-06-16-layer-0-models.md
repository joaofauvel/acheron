# Layer 0 — Data Models & Enums

All dataclasses, enums, and domain exceptions for Acheron. Zero I/O, pure types.

## Module Layout

```
src/acheron/__init__.py          # re-exports public API
src/acheron/core/__init__.py     # empty
src/acheron/core/models.py       # all dataclasses + enums
src/acheron/core/errors.py       # domain exceptions
src/acheron/shell/__init__.py    # empty

tests/core/__init__.py           # empty
tests/core/test_models.py
tests/core/test_errors.py
```

## `core/models.py`

### Enums

```python
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

class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
```

### Dataclasses

All frozen. Sequences use `tuple` for immutability.

**WorkerCapabilities** — describes what a worker can do:
- `worker_type: WorkerType`
- `supported_languages_in: frozenset[str]` (ISO 639-1)
- `supported_languages_out: frozenset[str]`
- `supported_formats_in: frozenset[str]` (e.g. "epub", "mp3", "wav", "md")
- `supported_formats_out: frozenset[str]` (e.g. "wav", "m4b", "md")
- `max_payload_bytes: int | None`
- `batch_capable: bool`
- `model_source: str | None` (e.g. "huggingface:Qwen/Qwen3-TTS-12Hz-1.7B")
- `metadata: dict` — model name, VRAM usage, etc.

**Job** — a unit of work:
- `job_id: str`
- `job_type: WorkerType`
- `payload: dict`
- `chapter_id: str`
- `sequence_ids: tuple[int, ...] | None`

**OutputFile** — a produced artifact:
- `path: str`
- `filename: str`
- `size_bytes: int`
- `checksum: str` (SHA-256)
- `content_type: str` (e.g. "audio/wav", "text/markdown")

**JobMetrics** — timing/cost data:
- `duration_seconds: float`
- `gpu_seconds: float | None`
- `tokens_in: int | None`
- `tokens_out: int | None`
- `cost_estimate: float | None`

**JobResult** — outcome of a job:
- `job_id: str`
- `status: JobStatus`
- `outputs: tuple[OutputFile, ...]`
- `metrics: JobMetrics`
- `error: str | None`

**PlanStep** — a single step in a pipeline plan:
- `step_id: str`
- `type: WorkerType`
- `depends_on: tuple[str, ...]`
- `status: StepStatus`
- `payload: dict`
- `batch: bool = False`

**Plan** — an immutable DAG of steps:
- `plan_id: str`
- `job_id: str`
- `source_type: str` ("epub" | "audio")
- `source_language: str`
- `target_language: str`
- `executor_strategy: str` ("sequential" | "async" | "batch_async")
- `steps: tuple[PlanStep, ...]`

**PlanResult** — outcome of executing a plan:
- `plan_id: str`
- `status: str` ("completed" | "failed" | "partial")
- `completed_steps: int`
- `total_steps: int`
- `outputs: tuple[OutputFile, ...]`
- `total_cost: float`
- `total_duration_seconds: float`

**BatchJob** — a batch of jobs for streaming workers:
- `batch_id: str`
- `jobs: tuple[Job, ...]`

**BatchStatus** — progress of a batch:
- `batch_id: str`
- `total: int`
- `completed: int`
- `failed: int`
- `pending: int`
- `results: tuple[JobResult, ...]`

## `core/errors.py`

```python
class AcheronError(Exception): ...

class PlanError(AcheronError): ...
class InvalidLanguagePath(PlanError): ...
class PlanValidationError(PlanError): ...

class WorkerError(AcheronError): ...
class WorkerUnavailable(WorkerError): ...
class WorkerTimeout(WorkerError): ...

class CacheError(AcheronError): ...
class CacheMiss(CacheError): ...
class CacheCorrupted(CacheError): ...
```

## import-linter

Add to `pyproject.toml`:

```toml
[tool.importlinter]
root_package = "acheron"

[[tool.importlinter.contracts]]
name = "core-shell-boundary"
type = "forbidden"
source_modules = ["acheron.core"]
forbidden_modules = ["acheron.shell"]
```

## Tests

**test_models.py**:
- Construct each dataclass with valid data, verify field access
- Verify frozen immutability (raises FrozenInstanceError on mutation)
- Verify enum values match spec strings
- Verify frozenset/tuple usage (hashable, immutable)

**test_errors.py**:
- Exception hierarchy: `InvalidLanguagePath` is a `PlanError` is an `AcheronError`
- Message propagation via `str(e)`
- Can be caught by base class

## Acceptance Criteria

- [ ] `just lint-strict` passes
- [ ] `just type-check` passes (mypy strict)
- [ ] `just test` passes with ≥80% coverage
- [ ] import-linter blocks `core` → `shell` imports
