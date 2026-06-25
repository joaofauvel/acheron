---
bundle: B12
name: Worker SDK consolidation
severity: MEDIUM
stories: 8
m_effort: 4
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B12 — Worker SDK consolidation (ARCH-009, -012, -014, -020, MAINT-011, -013, -015, CORR-015)

> **For agentic workers:** Use the **Common Workflow** from the main plan. **Tackle in this order: MAINT-013 (small dedup) → ARCH-012 + MAINT-011 + CORR-015 (mount, not copy) → ARCH-009 (move ABC) → MAINT-015 (extract Protocol) → ARCH-020 (StepDispatch) → ARCH-014 (match-based dispatch).** ARCH-014 is the most invasive; land it last.

**Bundle summary:** Move `HealthProvider` ABC to `core/interfaces`; replace `create_worker_app`'s route-copy with FastAPI's `app.mount`; extract shared `Protocol` for `inputs.py`/`artifacts.py`; introduce a `StepDispatch` table for `_execute_with_upstream_input`; refactor `HttpWorker.execute()` to use `match` instead of inline `if worker_type == ASR`.

**External lib verified:** FastAPI's `app.mount(path, sub_app)` is the standard pattern; the sub-app is independent (own OpenAPI, own routes). Source: `/fastapi/fastapi`.

**Expected commits:** 5-6.

---

## Tasks (tackle in order)

### Task 1: MAINT-013 — `_caps_to_response` (edge) and `_caps_to_dict` (registration) duplicate

**Story:** `docs/code_review/code-quality.md` § MAINT-013 (LOW, S effort).

**Files:**
- Modify: `src/acheron/worker_sdk/_caps.py` (new) or extract to existing.
- Modify: `src/acheron/worker_sdk/_edge_http.py`; `src/acheron/worker_sdk/registration.py` (call sites).
- Test: `tests/worker_sdk/test_caps.py` (new) or add to existing test files.

**Change:** collapse `_caps_to_response(caps) -> WorkerCapabilitiesResponse` and `_caps_to_dict(caps) -> dict` into a single `_caps_serialize(caps) -> dict` helper. The response wrapper just calls `_caps_serialize` and returns a typed `WorkerCapabilitiesResponse(**serialized)`.

**Test:** 3 unit tests on the new helper (full caps, missing optional fields, round-trip via Pydantic).

**Commit:** `refactor(MAINT-013): collapse _caps_to_response and _caps_to_dict into a single helper`.

---

### Task 2: ARCH-012 + MAINT-011 + CORR-015 — `create_worker_app` cherry-picks routes from `EdgeApp` via hardcoded `inner_paths`

**Story:** `docs/code_review/architecture.md` § ARCH-012 (MEDIUM, M); `code-quality.md` § MAINT-011 (MEDIUM, M); `correctness.md` § CORR-015 (MEDIUM, S). All three are angles on the same code.

**Files:**
- Modify: `src/acheron/worker_sdk/app.py` (`create_worker_app`).
- Test: `tests/worker_sdk/test_app.py`.

**Design:** use FastAPI's `app.mount("", EdgeApp.app)` (mount at the root). The sub-app handles all routes. The outer `app` only adds the `lifespan` for startup/shutdown (price refresh, registration).

```python
def create_worker_app(handler: WorkerHandler, *, settings: WorkerSettings) -> FastAPI:
    inner = EdgeApp(handler, settings=settings)
    app = FastAPI(lifespan=...)
    app.mount("", inner.app)  # mount the inner app at root
    return app
```

Verify that the FastAPI sub-app is fully independent (own routes, own OpenAPI) — `app.mount` semantics. The lifespan should still run on the outer app.

**Test:** the existing tests on `create_worker_app` should still pass. Add 1 test asserting that a new route added to `EdgeApp` (e.g. `GET /version`) is reachable via the outer `create_worker_app`'s test client.

**Commit:** `refactor(ARCH-012, MAINT-011, CORR-015): use FastAPI app.mount instead of copying routes in create_worker_app`.

---

### Task 3: ARCH-009 — `HealthProvider` ABC lives in `shell/health_providers.py` instead of `core/interfaces`

**Story:** `docs/code_review/architecture.md` § ARCH-009 (LOW, S effort).

**Files:**
- Modify: `src/acheron/core/interfaces.py` (add the ABC).
- Modify: `src/acheron/shell/health_providers.py` (re-export for back-compat? — AGENTS.md says no back-compat in greenfield, so delete the old location and update all imports).
- Test: `tests/shell/test_health_providers.py` (imports should still work; no new test).

**Change:** move the `HealthProvider` ABC to `core/interfaces.py`. Update all 5-6 import sites.

**Test:** `grep -n 'from acheron.shell.health_providers import' src/ tests/ dashboard/` should return 0 hits; the existing tests should still pass.

**Commit:** `refactor(ARCH-009): move HealthProvider ABC from shell/health_providers.py to core/interfaces.py`.

---

### Task 4: MAINT-015 (M) — `inputs.py` is a near-verbatim copy of `artifacts.py`

**Story:** `docs/code_review/code-quality.md` § MAINT-015 (MEDIUM, M effort).

**Files:**
- Modify: `src/acheron/worker_sdk/_io.py` (new).
- Modify: `src/acheron/worker_sdk/inputs.py`; `src/acheron/worker_sdk/artifacts.py` (import from `_io.py`).
- Test: `tests/worker_sdk/test_inputs.py`; `tests/worker_sdk/test_artifacts.py` (existing tests should still pass).

**Design:** extract the shared Protocol (the `FileLike` / `BytesLike` duck-type interface) into `_io.py`. Both `inputs.py` and `artifacts.py` import it and add their domain-specific helpers.

```python
# _io.py
class FileLikeProtocol(Protocol):
    filename: str
    content_type: str
    async def stream(self) -> AsyncIterator[bytes]: ...
    async def read(self) -> bytes: ...


class BytesLikeProtocol(Protocol):
    data: bytes
    content_type: str
    metadata: dict[str, str]
```

**Test:** existing tests on `FileInput`, `BytesInput`, `FileArtifact` should still pass. Add 1 test asserting that the new `FileLikeProtocol` is satisfied by both `FileInput` and `FileArtifact`.

**Commit:** `refactor(MAINT-015): extract FileLikeProtocol and BytesLikeProtocol to worker_sdk/_io.py`.

---

### Task 5: ARCH-020 (M) — `HttpWorker._execute_with_upstream_input` has a leaky triple-magic-string signature

**Story:** `docs/code_review/architecture.md` § ARCH-020 (MEDIUM, M effort).

**Files:**
- Modify: `src/acheron/shell/transports/http.py` (the `HttpWorker` class).
- Test: `tests/shell/transports/test_http_multipart.py`.

**Design:** introduce a `StepDispatch` dataclass that bundles the 3 magic strings:

```python
@dataclass(frozen=True)
class StepDispatch:
    upstream_step: str  # "chunks" / "audio" / etc.
    content_type_predicate: Callable[[str], bool]  # lambda c: c.startswith("application/json")
    form_field: str  # "chunks" / "audio"


# Module-level table
MATCHES_BY_TYPE: dict[tuple[WorkerType, str], StepDispatch] = {
    (WorkerType.TRANSLATION, "application/json"): StepDispatch(
        upstream_step="chunks", content_type_predicate=lambda c: c == "application/json", form_field="chunks"
    ),
    (WorkerType.TTS, "application/json"): StepDispatch(
        upstream_step="chunks", content_type_predicate=lambda c: c == "application/json", form_field="chunks"
    ),
    (WorkerType.ASR, "audio/wav"): StepDispatch(
        upstream_step="audio", content_type_predicate=lambda c: c.startswith("audio/"), form_field="audio"
    ),
}


def _execute_with_upstream_input(self, job: Job, dispatch: StepDispatch) -> JobResult:
    upstream_outputs = self._get_upstream_outputs(job, dispatch.upstream_step)
    matching = [o for o in upstream_outputs if dispatch.content_type_predicate(o.content_type)]
    if not matching:
        raise WorkerError(f"no upstream output matches {dispatch}")
    if len(matching) > 1:
        raise WorkerError(f"multiple upstream outputs match {dispatch} (not yet supported)")
    file = matching[0]
    return self._post_multipart(job, file, form_field=dispatch.form_field)
```

**Test:** existing tests on `_execute_with_upstream_input` should still pass. Add 1 test asserting that the table covers all 3 (WorkerType, content_type) pairs.

**Commit:** `refactor(ARCH-020): introduce StepDispatch dataclass and MATCHES_BY_TYPE table for _execute_with_upstream_input`.

---

### Task 6: ARCH-014 (M) — `HttpWorker.execute()` branches on `WorkerType.ASR` to add a transport-specific (multipart) flow

**Story:** `docs/code_review/architecture.md` § ARCH-014 (MEDIUM, M effort).

**Files:**
- Modify: `src/acheron/shell/transports/http.py`.
- Test: `tests/shell/transports/test_http_multipart.py`.

**Design:** replace the inline `if worker_type == ASR:` with a `match` on the job's `StepKind`:

```python
def execute(self, job: Job) -> JobResult:
    match job.job_type:
        case JobType.ASR:
            return self._execute_multipart(job)
        case JobType.TRANSLATION | JobType.TTS:
            return self._execute_json(job)
        case _:
            raise WorkerError(f"unsupported job type: {job.job_type}")
```

If `worker_type` is the dispatch key (vs. `job_type`), adapt. The point is to remove the string-based `if`/`elif` chain and use `match`.

**Test:** existing tests should still pass.

**Commit:** `refactor(ARCH-014): use match-statement dispatch in HttpWorker.execute()`.

---

## Bundle summary

- **Stories:** 8 (4 M-effort: ARCH-014, ARCH-020, MAINT-011, MAINT-015; 4 S-effort: ARCH-009, ARCH-012, CORR-015, MAINT-013).
- **Commits:** 5-6 (Tasks 2+3 are tightly coupled via the import graph; consider landing together).
- **Order matters:** MAINT-013 first (small dedup, no deps). Tasks 2 (mount) and 3 (move ABC) can land in either order. Task 4 (Protocol) before Task 5 (StepDispatch uses the Protocol). Task 6 (match) last.
- **External lib verification done:** `app.mount` works as expected.
- **Surface to user if:** the `app.mount` change breaks the FastAPI lifespan (e.g. the sub-app's lifespan is run instead of the outer's), or ARCH-014's `match` change requires a new dependency on `StepKind` that doesn't exist.
