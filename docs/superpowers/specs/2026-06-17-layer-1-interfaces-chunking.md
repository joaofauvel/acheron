# Layer 1 — Interfaces & Chunking

ABCs for Worker, StreamingWorker, Executor. Text chunking engine.

## Design Change: Remove `Worker.register`

The spec's `Worker.register(self, registry: "WorkerRegistry")` creates a core→shell dependency.
Registration is a shell concern — the orchestrator calls `worker.capabilities()` and registers on
the worker's behalf. The Worker ABC becomes: `capabilities()`, `execute()`, `health()`.

## Module Layout

```
src/acheron/core/interfaces.py   # Worker, StreamingWorker, Executor ABCs
src/acheron/core/chunking.py     # chunk_text() pure function
src/acheron/core/models.py       # + Chunk dataclass

tests/core/test_interfaces.py
tests/core/test_chunking.py
```

## `core/interfaces.py`

```python
from abc import ABC, abstractmethod

from acheron.core.models import (
    BatchJob,
    BatchStatus,
    Job,
    JobResult,
    Plan,
    PlanResult,
    WorkerCapabilities,
)


class Worker(ABC):
    @abstractmethod
    async def capabilities(self) -> WorkerCapabilities: ...

    @abstractmethod
    async def execute(self, job: Job) -> JobResult: ...

    @abstractmethod
    async def health(self) -> bool: ...


class StreamingWorker(Worker, ABC):
    @abstractmethod
    async def submit_batch(self, batch: BatchJob) -> str: ...

    @abstractmethod
    async def poll_batch(self, batch_handle: str) -> BatchStatus: ...

    @abstractmethod
    async def collect_results(self, batch_handle: str) -> tuple[JobResult, ...]: ...


class Executor(ABC):
    @abstractmethod
    async def run(self, plan: Plan) -> PlanResult: ...
```

## `core/models.py` — Add Chunk

```python
@dataclass(frozen=True)
class Chunk:
    chapter_id: str
    sequence_id: int
    text: str
```

## `core/chunking.py`

```python
def chunk_text(text: str, chapter_id: str, max_length: int = 250) -> tuple[Chunk, ...]: ...
```

Algorithm:
1. Strip and normalize whitespace
2. `nltk.sent_tokenize(text)` to split into sentences
3. For each sentence:
   - If `len(sentence) <= max_length` → emit as chunk
   - If `len(sentence) > max_length` → split on punctuation (`, ; —`)
   - If still > max_length → hard split on nearest whitespace < max_length
4. Assign `sequence_id` in order (0, 1, 2, ...)
5. Skip empty/whitespace-only chunks

## Tests

### `test_interfaces.py`

- Concrete impl satisfies ABC
- Missing any abstract method → TypeError on instantiation
- StreamingWorker inherits Worker methods
- Executor ABC enforced

### `test_chunking.py`

- Short text (< max_length) → single chunk
- Multiple sentences → multiple chunks with correct sequence_ids
- Long sentence → punctuation fallback split
- No punctuation → hard split on whitespace
- Empty string → empty tuple
- Whitespace-only → empty tuple
- Custom max_length parameter
- Single word > max_length → hard split
- Preserves text content (no data loss across chunks)

## Acceptance Criteria

- [ ] `just lint-strict` passes
- [ ] `just type-check` passes
- [ ] `just type-check-pyright` passes
- [ ] `just test` passes with ≥80% coverage
- [ ] import-linter boundary maintained
