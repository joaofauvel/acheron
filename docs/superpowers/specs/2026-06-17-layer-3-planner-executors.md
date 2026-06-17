# Layer 3 — Planner + Executors

Plan compilation and execution. Uses structural pattern matching instead of string dispatch.

## Design Principles

- **Make illegal states unrepresentable**: types prevent invalid configs, not validation
- **Structural pattern matching**: match on enums and dataclass types, not strings
- **Planner is pure logic**: takes data in, returns data out, no I/O

## Spec Changes (backported to master)

- `Plan.executor_strategy` changes from `str` to `ExecutorStrategy` enum
- New `EpubRequest` and `AudioRequest` types replace dict-based job requests
- `type JobRequest = EpubRequest | AudioRequest`

## Module Layout

```
src/acheron/core/planner.py
src/acheron/shell/executors/__init__.py
src/acheron/shell/executors/sequential.py
src/acheron/shell/executors/async_executor.py
src/acheron/shell/executors/batch_async.py

tests/core/test_planner.py
tests/shell/test_executors.py
```

## `core/models.py` — New types

```python
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
```

`Plan.executor_strategy` becomes `ExecutorStrategy` (was `str`).

## `core/planner.py`

```python
def compile_plan(
    request: JobRequest,
    strategy: ExecutorStrategy,
    capabilities: tuple[WorkerCapabilities, ...],
    plan_id: str,
    job_id: str,
) -> Plan: ...
```

- Validates language path using capabilities
- Raises `InvalidLanguagePathError` if no workers support the path
- match on `request` type to select step sequence
- Returns immutable Plan

Step sequences:
- EPUB: extract → chunk → translate → synthesize → package
- Audio: extract → transcribe → chunk → translate → synthesize → package

## `shell/executors/`

### WorkerDispatcher Protocol

```python
class WorkerDispatcher(Protocol):
    async def dispatch(self, worker_id: str, job: Job) -> JobResult: ...
```

### SequentialExecutor

Walks steps in dependency order. Executes one at a time. Returns PlanResult.

### AsyncExecutor

Topological sort of steps. Runs all independent steps concurrently via `asyncio.gather`.

### BatchAsyncExecutor

Extends AsyncExecutor. For TTS/ASR steps with `batch=True`, submits all chunks as a single BatchJob to a StreamingWorker.

### Executor Factory

```python
def create_executor(strategy: ExecutorStrategy, dispatcher: WorkerDispatcher) -> Executor:
    return match strategy:
        case ExecutorStrategy.SEQUENTIAL: return SequentialExecutor(dispatcher)
        case ExecutorStrategy.ASYNC: return AsyncExecutor(dispatcher)
        case ExecutorStrategy.BATCH_ASYNC: return BatchAsyncExecutor(dispatcher)
```

## Tests

### test_planner.py
- EPUB request produces correct step sequence
- Audio request includes ASR step
- Invalid language path raises InvalidLanguagePathError
- Steps have correct dependencies
- All steps have PENDING status
- Worker assigned when capability matches

### test_executors.py
- SequentialExecutor runs steps in order
- AsyncExecutor runs independent steps concurrently
- BatchAsyncExecutor submits batches for TTS steps
- Failed step produces FAILED plan status
- All steps complete produces COMPLETED plan status

## Acceptance Criteria

- [ ] `just validate` passes
- [ ] No string-based dispatch anywhere
- [ ] match on all enum/type unions
