---
bundle: B17
name: Type safety — typed models & ignores
severity: MIXED
stories: 6
m_effort: 4
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B17 — Type safety — typed models & ignores (TYPE-001, -003, -006, -007, -008, -010)

> **For agentic workers:** Use the **Common Workflow** from the main plan. **Tackle in this order: TYPE-010 (Protocols) → TYPE-001 (typed client responses) → TYPE-003 (Redis wrapper) → TYPE-008 (WorkerSDK Any) → TYPE-006 (gRPC stubs) → TYPE-007 (phantom_handler).** TYPE-010 introduces the Protocols that TYPE-001 and the worker handlers depend on.

**Bundle summary:** Replace `dict[str, Any]` and `Any`-typed model fields with typed Protocols and Pydantic models. Touches the AcheronClient, the redis store, the worker handlers, and the gRPC transport.

**Expected commits:** 4-5.

---

## Tasks (tackle in order)

### Task 1: TYPE-010 (M) — 3 RunPod worker handlers type `self._model` and `self._processor` as `Any`

**Story:** `docs/code_review/code-quality.md` § TYPE-010 (LOW, M effort).

**Files:**
- Modify: `workers/_shared/protocols.py` (new) — define `_ModelProto`, `_ProcessorProto`, `_TokenizerProto`.
- Modify: `workers/qwen3tts/handler.py`; `workers/granite_speech/handler.py`; `workers/translategemma/handler.py`.
- Test: `tests/worker_sdk/test_protocols.py` (new) — assert the Protocols are runtime-checkable.

**Design:**

```python
# workers/_shared/protocols.py
from typing import Protocol, runtime_checkable


@runtime_checkable
class _TokenizerProto(Protocol):
    pad_token_id: int | None
    def __call__(self, text: str, return_tensors: str = "pt"): ...
    def decode(self, token_ids: list[int], skip_special_tokens: bool = True) -> str: ...


@runtime_checkable
class _ProcessorProto(Protocol):
    tokenizer: _TokenizerProto
    def __call__(self, *args, **kwargs): ...


@runtime_checkable
class _ModelProto(Protocol):
    def generate(self, *args, **kwargs): ...
    def eval(self): ...
```

Each handler declares `self._model: _ModelProto` and `self._processor: _ProcessorProto` (or a more specific type). mypy should pass without `# type: ignore[no-any-return]`.

**Test:** the existing handler tests should still pass. Add 1 test asserting `isinstance(self._model, _ModelProto)` returns True for a real model (using a fake that satisfies the protocol).

**Commit:** `fix(TYPE-010): introduce _ModelProto and _ProcessorProto Protocols in workers/_shared/protocols.py; type the 3 RunPod handlers`.

---

### Task 2: TYPE-001 (M) — `AcheronClient` returns `dict[str, Any]` consumed via magic-string keys

**Story:** `docs/code_review/code-quality.md` § TYPE-001 (MEDIUM, M effort).

**Files:**
- Modify: `src/acheron/shell/api/schemas.py` (or wherever the response models are).
- Modify: `src/acheron/shell/api/client.py` (or wherever `AcheronClient` is).
- Test: `tests/shell/api/test_client.py` (or wherever the client is tested).

**Design:** introduce typed response models:

```python
from pydantic import BaseModel


class JobResponse(BaseModel):
    id: str
    status: JobStatus
    plan: Plan | None
    total_cost_basis: Decimal | None
    error: str | None


class WorkerResponse(BaseModel):
    id: str
    url: str
    transport: str
    capabilities: WorkerCapabilities
    last_health_check: datetime | None
    last_error: str | None
    cost_basis: CostBasis


class CapabilitiesResponse(BaseModel):
    workers: list[WorkerResponse]
```

`AcheronClient.get_job(id) -> JobResponse`, `AcheronClient.list_workers() -> list[WorkerResponse]`, etc. Pydantic v2's `model_validate(response.json())` does the parsing.

**Test:** 1 round-trip test per method (mock httpx to return a known dict, assert the client returns the typed Pydantic model with the right field values).

**Commit:** `fix(TYPE-001): return typed Pydantic response models from AcheronClient`.

---

### Task 3: TYPE-003 (M) — `redis.py` accumulates 8 `# type: ignore[misc]` markers on `await self._redis.<method>`

**Story:** `docs/code_review/code-quality.md` § TYPE-003 (MEDIUM, M effort).

**Files:**
- Modify: `src/acheron/shell/stores/redis.py`.
- Test: `tests/shell/stores/test_redis_worker_store.py` (existing tests should still pass).

**Design:** wrap `redis.asyncio.Redis` in a thin typed proxy:

```python
class RedisLike(Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def set(self, key: str, value: bytes, *, ex: int | None = None) -> bool | None: ...
    # ... 6 more methods that are currently type-ignored


class RedisStore:
    def __init__(self, redis: RedisLike) -> None:
        self._redis: RedisLike = redis
```

The constructor takes a `RedisLike` (which `redis.asyncio.Redis` satisfies at runtime via duck typing, but mypy can verify). No more `# type: ignore[misc]` needed.

**Test:** existing tests should still pass. mypy should report 0 errors.

**Commit:** `fix(TYPE-003): wrap redis.asyncio.Redis in RedisLike Protocol proxy; drop 8 type: ignore markers`.

---

### Task 4: TYPE-008 (M) — WorkerSDK has 14+ `Any`/`dict[str, Any]` annotations in 5 files

**Story:** `docs/code_review/code-quality.md` § TYPE-008 (LOW, M effort).

**Files:**
- Modify: `src/acheron/worker_sdk/_aliases.py` (new) — define `_JsonDict = dict[str, "WorkerResponsePayload"]` and the discriminated union.
- Modify: 5 worker_sdk files (replace `dict[str, Any]` with `_JsonDict` where the keys are known).
- Test: mypy passes; existing tests still pass.

**Design:**

```python
# _aliases.py
from typing import Literal, Union
from pydantic import BaseModel


class WorkerInfoPayload(BaseModel):
    type: Literal["worker_info"]
    id: str
    capabilities: dict


class JobResultPayload(BaseModel):
    type: Literal["job_result"]
    outputs: list


class HeartbeatPayload(BaseModel):
    type: Literal["heartbeat"]
    ts: float


WorkerResponsePayload = Union[WorkerInfoPayload, JobResultPayload, HeartbeatPayload]
```

Replace `dict[str, Any]` with the appropriate Pydantic model in each of the 5 files.

**Test:** mypy clean. Existing tests pass.

**Commit:** `fix(TYPE-008): replace Any/dict[str, Any] in worker_sdk with typed Pydantic payload models`.

---

### Task 5: TYPE-006 (S) — `grpc.py` accumulates 5 `# type: ignore` markers for the new proto `Artifact` types

**Story:** `docs/code_review/code-quality.md` § TYPE-006 (LOW, S effort).

**Files:**
- Modify: `stubs/grpc_gen/acheron_pb2.pyi` (new) — minimal type stub for the proto `Artifact` types.
- Modify: `src/acheron/shell/transports/grpc.py` (drop the `# type: ignore` markers).
- Test: mypy clean.

**Design:** generate a `.pyi` for the proto module using `grpc_tools.protoc` (or write by hand if the types are simple). The `.pyi` declares the `Artifact` message and its fields.

**Test:** mypy clean. Existing tests pass.

**Commit:** `fix(TYPE-006): add minimal .pyi stub for grpc proto Artifact; drop type: ignore markers`.

---

### Task 6: TYPE-007 (S) — `RunPodForwarderHandler.__init__` calls `phantom_handler(settings)` under `# type: ignore`

**Story:** `docs/code_review/code-quality.md` § TYPE-007 (LOW, S effort).

**Files:**
- Modify: `src/acheron/worker_sdk/cloud.py`; the `phantom_handler` factory.
- Test: mypy clean.

**Design:** declare the `phantom_handler` factory's return type as a `RunPodHandlerProtocol` (define the Protocol in `cloud.py` or a new `protocols.py`).

```python
class RunPodHandlerProtocol(Protocol):
    def handle(self, payload: dict) -> dict: ...
    async def health(self) -> dict: ...


def phantom_handler(settings: WorkerSettings) -> RunPodHandlerProtocol: ...
```

**Test:** mypy clean. Existing tests pass.

**Commit:** `fix(TYPE-007): type phantom_handler return as RunPodHandlerProtocol; drop type: ignore`.

---

## Bundle summary

- **Stories:** 6 (4 M-effort: TYPE-001, -003, -008, -010; 2 S-effort: TYPE-006, -007).
- **Commits:** 4-5.
- **Order matters:** TYPE-010 first (Protocols), then TYPE-001 (uses Pydantic), then TYPE-003 (Redis wrapper), then TYPE-008 (WorkerSDK), then TYPE-006 (gRPC stubs), then TYPE-007 (phantom_handler).
- **Cross-bundle:** B16's TYPE-009 (GraniteSpeech handler model annotation) depends on TYPE-010's Protocols. Land B17 first.
- **External libs:** Pydantic v2 (already a dep); no new deps.
- **Surface to user if:** the proto `.pyi` stub needs `grpc_tools.protoc` (would require a new dev dep — prefer to hand-write the stub).
