---
branch: chore/code-review-update
initial_review_commit: 23c29e1
last_updated_commit: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_staleness_scan:
  commit: eb6849c85d83f2277eb450f18a11e63cae2defd1
  date: 2026-06-24
---

# Code quality

## MAINT — Maintainability

**Grade:** B

MAINT-001, MAINT-003, MAINT-004 remain verified. The 11 carry-over open stories (MAINT-002, 005, 006, 007, 008, 009, 010, 011, 012, 013, 014) were re-resolved against the new HEAD: line numbers mostly held but shifted where the diff touched the file (e.g. `transports/http.py:145→223`, `_edge_http.py:44-55→49-60`, `stubs/_sdk_base` +1 across the board, `cloud.py:132-139→164-168` in the related type story). MAINT-009 caught one new site shift. The pattern is consistent with the prior sweep: the diff is type-and-typing concentrated, so maintainability findings mostly carry through unchanged. One new finding: MAINT-015 (medium) — `inputs.py` is a near-verbatim copy of `artifacts.py` (same Protocol + three-variant shape duplicated 95%); the 8c worker surface is the moment to consolidate before more workers copy the shape.

### MAINT-001 — BatchAsyncExecutor is a verbatim duplicate of AsyncExecutor; entire batch submission machinery is vestigial

```yaml
status: verified
severity: high
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: ["e0da69f"]
files:
  - path: src/acheron/shell/executors/batch_async.py
    lines: 16-79
  - path: src/acheron/shell/executors/async_executor.py
    lines: 16-75
  - path: src/acheron/cli.py
    lines: 141
  - path: src/acheron/core/interfaces.py
    lines: 35-51
  - path: src/acheron/shell/transports/grpc.py
    lines: 38-125
  - path: src/acheron/core/models.py
    lines: 114
  - path: src/acheron/core/planner.py
    lines: 134-198
related: [CORR-002, ARCH-001, CORR-003]
```

**Issue.** `BatchAsyncExecutor.run` (batch_async.py:26-79) is byte-identical to `AsyncExecutor.run` (async_executor.py:22-75) — a direct diff confirms only the docstring differs. The docstring promises "Batch-flagged steps receive all outputs from completed preceding steps so the handler can construct a BatchJob with the correct payloads," but the body just calls `self._handler(step, plan)` per step exactly like AsyncExecutor; no BatchJob is ever constructed. No executor ever calls `StreamingWorker.submit_batch`/`poll_batch`/`collect_results` (interfaces.py:39-51), so the GrpcWorker (grpc.py:103-125) and HttpWorker (http.py:77-92) batch methods are dead. `PlanStep.batch` (models.py:114) is set `True` on synthesize steps (planner.py:134,198) and serialized by Redis (redis.py:123) but read by nothing. Worse, `batch_async` is the CLI default (cli.py:141 `default="batch_async"`). Latent bug if ever wired up: `GrpcWorker._batches` (grpc.py:38) is per-instance state, but `step_handler.py:112` creates a fresh worker per step, so batch handles could not survive across calls.

**Why it matters.** AGENTS.md states "Silent/unexpected behavior is worse than no control at all." Users selecting batch_async for GPU throughput get plain async — correct outputs, but the advertised optimization is a no-op. The dead batch surface (StreamingWorker ABC, BatchJob/BatchStatus models, transport batch methods, PlanStep.batch field) is misleading API surface that must be maintained for nothing. High because it is misleading-by-default.

**Recommendation.** Per greenfield rules, either implement batch submission (collect upstream outputs for batch-flagged steps, build a BatchJob, call submit_batch/poll_batch/collect_results on a StreamingWorker reused across the batch) or remove BatchAsyncExecutor, the StreamingWorker batch methods, BatchJob/BatchStatus, PlanStep.batch, and change the CLI default to a strategy that exists. Do not leave the stub.

**Verification.** `just test` — add a test asserting batch_async submits a BatchJob to a StreamingWorker (currently impossible). `just type-check`. If removing: grep for `submit_batch|BatchJob|BatchStatus|\.batch` returns zero src hits.

### MAINT-002 — redis.py hand-rolls JSON ser/deser for domain models that cache.py serializes via pydantic, duplicating and drifting

```yaml
status: open
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: e54458416e9bfe890a473dd9d542978d205b40a1
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/shell/stores/redis.py
    lines: 30-282
  - path: src/acheron/shell/cache.py
    lines: 27-128
related: [DATA-002, MAINT-015]
```

**Issue.** redis.py hand-rolls ~150 lines of JSON serialization for TrackedJob/Plan/PlanStep/PlanResult/WorkerCapabilities (`_serialize_job`, `_deserialize_job`, `_serialize_capabilities`, `_deserialize_capabilities`, `_deserialize_worker` — redis.py:30-282) via json.dumps/loads with field-by-field reconstruction and a manual `source_type` match dispatch. cache.py serializes the same Plan/OutputFile models via pydantic `TypeAdapter` (cache.py:44,59,85,107 — `_plan_adapter.dump_json`/`validate_json`). Adding a field to PlanStep/Plan/PlanResult requires editing the redis ser site and the deser site separately, while cache.py adapts automatically. The manual deser re-derives `source_type` via a match on AudioRequest/EpubRequest, duplicating planner logic rather than persisting it. The new `CostBasis` field added in this delta required touching both the manual ser site (line 179) and deser site (line 272), reinforcing the drift story. The fundamental duplication remains — redis.py is still the largest file in the shell package.

**Why it matters.** Two divergent serialization paths for the same domain models is a fragility hotspot: field additions silently desync between the Redis and disk-cache backends, and a round-trip mismatch would surface only at runtime. Medium — no current bug, but a clear maintainability and drift risk in the largest file.

**Recommendation.** Introduce a shared serialization module using pydantic TypeAdapter for TrackedJob (and the existing Plan/OutputFile adapters) and use it in both cache.py and redis.py. Eliminate the manual source_type match by persisting source_type or relying on the tagged union. This collapses ~150 lines and makes both backends share one schema.

**Verification.** `just test` (redis round-trip tests still pass), `just type-check`. Add a PlanStep field and confirm zero redis.py edits are needed.

### MAINT-003 — _BUILT_IN_LOCAL_HANDLERS private symbol imported across module boundary

```yaml
status: verified
severity: low
effort: S
reviewed_at: a1b11b2
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in:
  - 92ed9da
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 19-20
  - path: src/acheron/shell/local_handlers.py
    lines: 90
related: ['ARCH-005']
```

**Issue.** The refactor that extracted capability aggregation to capabilities.py also moved _BUILT_IN_LOCAL_HANDLERS from orchestrator.py to local_handlers.py while keeping the leading underscore. orchestrator.py:20 now `from acheron.shell.local_handlers import _BUILT_IN_LOCAL_HANDLERS, ...` — a private-by-convention name imported across the module boundary. Other module-private helpers in this codebase (e.g. _collect_worker_caps, _pair_is_achievable in capabilities.py) stay module-internal. The leading underscore signals 'do not import', but the orchestrator cannot start without it.

**Why it matters.** A leading-underscore name imported externally misleads readers and tools (e.g. IDE 'unused symbol' hints, future refactor scripts) into treating it as private. Future moves/renames of local_handlers internals will silently break the orchestrator. The pattern is also inconsistent with the rest of the refactor, which correctly made all_languages_caps public (no underscore).

**Recommendation.** Either rename _BUILT_IN_LOCAL_HANDLERS to BUILT_IN_LOCAL_HANDLERS (public) in local_handlers.py and update the orchestrator import, or expose a public factory function get_built_in_local_handlers() -> dict[WorkerType, LocalJobHandler] in local_handlers.py. The dataclass-frozen list of (WorkerType -> handler) is a stable public contract — the underscore is misleading.

**Verification.** grep -rn 'from acheron.shell.local_handlers import' src/ should not pull a name starting with underscore; `just lint-strict`, `just test`

### MAINT-004 — _stage return value in StreamingExecutor is dead code — computed cost returned as float|None but never consumed by _run_pipeline

```yaml
status: verified
severity: low
effort: S
reviewed_at: d0b739b
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in:
  - 640bb03
files:
  - path: src/acheron/shell/executors/streaming.py
    lines: 190-245
  - path: src/acheron/shell/executors/streaming.py
    lines: 78-109
related: [CORR-008]
```

**Issue.** `_stage` (streaming.py:180-237) returns `float | None` (cost or None) and its docstring (line 189) says "Returns the step's cost (including on FAILED), None if no work ran." However, `_run_pipeline` (streaming.py:67-98) calls `task.result()` on each stage task without capturing the return value (line 95: `task.result()` — no assignment). Costs are instead read from the shared `stage_costs` list parameter. The return value of `_stage` is computed at line 237 (`return stage_costs[stage_index]`) but silently dropped.

**Why it matters.** A misleading type signature and docstring wastes reader effort tracing data flow that doesn't exist. The dead return value survived the CORR-008 fix because the migration from per-task return to shared list was incomplete — it captured cost into the list but forgot to remove the return path.

**Recommendation.** Change `_stage` return type to `None`, remove the `return stage_costs[stage_index]` line, and update the docstring to say "Records cost in stage_costs[stage_index]." Three-line fix.

**Verification.** `just test`; `just type-check`; grep `def _stage` confirms no return type annotation.

### MAINT-005 — Orchestrator._execute duplicates PlanResult construction across adjacent exception handlers

```yaml
status: open
severity: low
effort: S
reviewed_at: be7b3ab
last_verified_at:
  commit: e123f35
  date: '2026-06-24'
fixed_in: []
files:
- path: src/acheron/shell/orchestrator.py
  lines: 359-384
related:
- OBS-004
```

**Issue.** `Orchestrator._execute` duplicates the same 9-field `PlanResult` constructor in its `except AcheronError` and `except Exception` blocks. The two blocks differ only in their log messages; the failure result is identical.

**Why it matters.** Duplicated domain-object construction across adjacent exception handlers is a small maintainability hotspot. Adding a field to `PlanResult` or changing failure semantics requires editing both sites, and they can drift silently.

**Recommendation.** Extract a helper such as `_record_failure(tracked, exc)` that sets `tracked.status = PlanStatus.FAILED` and builds the `PlanResult` once, called from both exception handlers.

**Verification.** `just test`; `just type-check`. Both except blocks should reduce to a single helper call.

### MAINT-006 — Orchestrator.start() inlines 17-line registration-token block; logs the token in plaintext

```yaml
status: open
severity: medium
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: e123f35
  date: '2026-06-24'
fixed_in: []
files:
- path: src/acheron/shell/orchestrator.py
  lines: 209-228
related:
- SEC-008
- MAINT-007
```

**Issue.** `Orchestrator.start()` (orchestrator.py:177-194) embeds a 17-line block that loads or generates the registration token, reads/writes a side file at `<data_dir>/.registration_token`, and logs the result. The block interleaves two concerns — orchestrator lifecycle wiring and credentials management — and `start()` is now 34 lines where a 5-line declarative method that calls a helper would be clearer. The block is also the largest single responsibility creep inside `start()` since the `HealthProviders` wiring at lines 77-82. Separately, the log message at orchestrator.py:192 emits the freshly generated token at INFO level — see SEC-008 for the security side.

**Why it matters.** Inline startup concerns make `start()` harder to test (does too much to factor out) and harder to read; the token block is unrelated to the other startup steps (verify_data_dir_writable, connect stores, register local workers, start health monitor). Mixed responsibilities in a single method is a maintainability hotspot.

**Recommendation.** Extract the token block to a private coroutine `_load_or_create_registration_token() -> None` that mutates `self._settings.orchestrator.registration_token`. Replace the inline block with a single call. In the helper, log only that a token was generated or loaded — do NOT log the token value itself.

**Verification.** `just test`, `just type-check`. `Orchestrator.start()` should be 5-7 lines of declarative wiring; a test asserting an existing token file is not overwritten and a missing file yields a fresh token; INFO logs do not contain the token.

### MAINT-007 — RunPodHealthProvider and HuggingFaceHealthProvider duplicate the HTTP fetch envelope

```yaml
status: open
severity: medium
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: 1fbedbc
  date: '2026-06-24'
fixed_in: []
files:
- path: src/acheron/shell/health_providers.py
  lines: 42-63, 80-111
- path: src/acheron/shell/health_providers.py
  lines: 70-94
related:
- MAINT-006
```

**Issue.** `RunPodHealthProvider.check_status` (health_providers.py:39-53) and `HuggingFaceHealthProvider.check_status` (health_providers.py:70-94) share an identical 8-line fetch envelope: `try` → `async with httpx.AsyncClient() as client: resp = await client.get(url, headers=headers, timeout=10.0)` → `except (httpx.HTTPError, OSError)` → return `WorkerStatus.OFFLINE` → status code check. The two methods only diverge in URL construction and how they map the response body to BOOTING/OFFLINE. Adding a third provider (SageMaker, Vast.ai) requires re-typing the same fetch envelope.

**Why it matters.** Bundle-level MAINT: 2 sites of ~10 lines of duplicated platform-agnostic HTTP plumbing. The `HealthProvider` ABC is the right abstraction for the diverging response mapping; the fetch envelope is a separate, common concern.

**Recommendation.** Extract a private helper `async def _fetch_provider_response(path: str, headers: dict[str, str]) -> httpx.Response | None` that handles the `AsyncClient` lifecycle, timeout, and error handling; each provider delegates. The providers retain only URL construction and body interpretation.

**Verification.** `just test`, `just type-check`. Adding a third provider should require zero new fetch boilerplate.

### MAINT-008 — HealthMonitor._handle_failure reassigns its `error` parameter inside the try/except

```yaml
status: open
severity: low
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: e54458416e9bfe890a473dd9d542978d205b40a1
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/shell/health.py
    lines: 133-152
related: [EXC-003]
```

**Issue.** `_handle_failure(self, worker: RegisteredWorker, error: str)` accepts the probe error message as a parameter, but inside the provider try/except it reassigns `error = f"{error}; provider {provider_name} error: {exc}"` (health.py:144). The parameter is therefore both an input and a locally-mutated accumulator; the chained error message is only valid after the reassignment, and a reader tracing data flow has to check both the parameter declaration and the assignment to know what `error` is at the call sites (146, 149).

**Why it matters.** Reassigning a function parameter to a new value is a naming/clarity smell per the lens. The construct is also the kind of thing basedpyright/pylint PAL would flag under stricter checks.

**Recommendation.** Introduce a local `chained_error = error` and build the chained message after the except block; pass `chained_error` to `set_worker_status`. The mutation disappears and the parameter is read-only.

**Verification.** `just test`, `just type-check`; add a test that exercises a provider raising — the worker should be marked OFFLINE with the chained error string and the parameter should remain unchanged at function return.

### MAINT-009 — Python 2-style `except A, B:` syntax used at 7 sites across 6 files

```yaml
status: open
severity: low
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: 7d4754a
  date: '2026-06-24'
fixed_in: []
files:
- path: src/acheron/shell/health_providers.py
  lines: 49
- path: src/acheron/shell/health_providers.py
  lines: 80
- path: src/acheron/shell/transports/http.py
  lines: '189'
- path: src/acheron/shell/local_handlers.py
  lines: 317
- path: src/acheron/shell/executors/streaming.py
  lines: 155
- path: src/acheron/worker_sdk/pricing.py
  lines: 143
- path: src/acheron/shell/cache.py
  lines: 115
related: []
```

**Issue.** The repo uses the deprecated Python 2 except form `except A, B:` (which Python 3 parses as a tuple `except (A, B):` — *not* `except A as B:`). The 7 sites are: `health_providers.py:49,80` (`httpx.HTTPError, OSError`), `transports/http.py:145` (`WorkerError, WorkerUnavailableError`), `local_handlers.py:296` (`CacheMissError, CacheCorruptedError, OSError`), `executors/streaming.py:155` (`CacheMissError, CacheCorruptedError`), `worker_sdk/pricing.py:143` (`httpx.HTTPError, OSError, KeyError, ValueError, TypeError`), `cache.py:115` (`CacheMissError, CacheCorruptedError, OSError`). The site at `pricing.py:143` is a particularly broad basket — `(httpx.HTTPError, OSError, KeyError, ValueError, TypeError)` is essentially `except Exception` with a hand-picked name. Reader confusion risk: a developer familiar with Python 2 reads `except X, Y:` as `except X as Y:` and looks for the bound name `Y`.

**Why it matters.** Violates the greenfield-clarity rubric — six of the seven sites would trip any pyupgrade-aware linter (the project enables `select = ["ALL"]` in pyproject.toml but doesn't enable B034/pyupgrade rules that catch this). Pattern-level: a reader tracing data flow may misread a comma in an except clause as a name binding. A future `redis-py` stub fix or pyupgrade rule bump would force a sweep; better to do it now in 1-line edits per site.

**Recommendation.** Per site: replace `except A, B:` with `except (A, B):`. Bundle all 7 sites in one commit. For the `pricing.py:143` broad basket, narrow to the actual failure modes (`httpx.HTTPError, OSError` for the HTTP call; `KeyError, ValueError, TypeError` for the JSON-shape-guard, in a separate `try` inside the GraphQL handler).

**Verification.** `grep -rn 'except [A-Z][A-Za-z0-9_.]*, [A-Z]' src/` returns zero hits; `uv run ruff check --select B034,UP024 src/` shows no findings; `just lint-strict`, `just test`.

### MAINT-010 — `create_worker_app` has a duplicate docstring — the second is a no-op string literal that survived the rewrite

```yaml
status: fixed
severity: low
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: eb6849c85d83f2277eb450f18a11e63cae2defd1
  date: 2026-06-24
fixed_in: ["eb6849c85d83f2277eb450f18a11e63cae2defd1"]
files:
  - path: src/acheron/worker_sdk/app.py
    lines: 93-97
related: [MAINT-011, EXC-004]
```

**Issue.** `create_worker_app` declares a Google-style docstring (lines 93-97) explaining `disable_registration`, then immediately repeats the same opening line as a bare string literal on line 98: `    """Build the edge FastAPI app wired with registration + price refresh."""`. Python parses the second literal as an expression statement that evaluates-and-discards the string, so it has no functional effect — it is pure dead code that misleads readers (looks like a real docstring at a glance). Likely a paste-merge artefact from the R2 review that added the `disable_registration` block.

**Why it matters.** The duplicate is exactly the kind of stale doc drift the project's review rubric catches (cf. AGENTS.md "do not add unnecessary and/or coupled comments ... that make changes hard to maintain and generate staleness"). Future maintainers will assume the lower literal is the canonical contract.

**Recommendation.** Delete line 98. `ruff format` will not auto-fix this; manual edit only.

**Verification.** `just lint-strict` (no rule catches it); `grep -n '"""' src/acheron/worker_sdk/app.py` shows exactly two `def` docstrings and no orphan literal; `just test`.

### MAINT-011 — `create_worker_app` builds an `EdgeApp` only to copy its routes onto the outer app via path-string matching; the inner `EdgeApp` is dead code

```yaml
status: open
severity: medium
effort: M
reviewed_at: dbec2be
last_verified_at:
  commit: 1fbedbc
  date: '2026-06-24'
fixed_in: []
files:
- path: src/acheron/worker_sdk/app.py
  lines: 99-146
related:
- CORR-015
- ARCH-012
- MAINT-010
```

**Issue.** The outer `lifespan` (lines 115-133) is the only one that runs; the inner `EdgeApp` built at line 101 is discarded, and its `app.routes` is harvested by the loop at lines 139-143: `for route in inner.app.routes: path = getattr(route, 'path', None); if path in inner_paths: app.routes.append(route)`. This (a) bypasses FastAPI's `APIRouter.include_router` / `app.mount` machinery, (b) hard-codes the path set `{'/health', '/capabilities', '/execute'}` so adding a 4th inner route silently breaks the outer app (no error, just a missing endpoint), and (c) directly mutates the outer `app.routes` list which FastAPI doesn't guarantee as a stable contract. The comment on lines 136-138 admits the inner `lifespan` is dead code that the outer one supersedes — that admission should drive the refactor, not stay as a warning.

**Why it matters.** The construction pattern is fragile: a `Route` subclass without a `path` attribute would be silently dropped (`getattr(route, 'path', None)` returns `None`, not an error), and adding a path to the inner app is now a two-place edit (inner `EdgeApp` route table + the `inner_paths` set in `app.py`). The inner `EdgeApp` is also a public class — callers might use it directly and find that its lifespan is overridden by `create_worker_app`, which is surprising.

**Recommendation.** Refactor `EdgeApp` to expose its route registration as a public method (e.g. `EdgeApp.routes() -> list[APIRouter]`) and have `create_worker_app` mount it via `app.include_router`. Drop the `inner_paths` set and the `app.routes.append` mutation. The inner `EdgeApp` can keep its own lifespan as a no-op, or be split into a routes-only class and a lifespan-only class so neither is dead.

**Verification.** `just test`; new test that a 4th route added to `EdgeApp` is reachable via the outer app without editing `app.py`; `just type-check` (the `getattr` path-string dance disappears).

### MAINT-012 — `_registration_caps` manually re-lists every `WorkerCapabilities` field to swap in enriched metadata; should use `dataclasses.replace`

```yaml
status: open
severity: low
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: 1fbedbc
  date: '2026-06-24'
fixed_in: []
files:
- path: src/acheron/worker_sdk/app.py
  lines: 59-83
related:
- MAINT-013
```

**Issue.** `_registration_caps` (lines 60-84) returns a new `WorkerCapabilities` with the same 9 fields as `caps`, differing only in `metadata` (now `enriched`). The function literally retypes every field by name. `WorkerCapabilities` is a `@dataclass(frozen=True)` (models.py:77-89) so `dataclasses.replace(caps, metadata=enriched)` produces the same result in 2 lines.

**Why it matters.** Adding a new field to `WorkerCapabilities` (e.g. a new capability knob) requires editing this function *and* every other call site that constructs `WorkerCapabilities` by-name. Pattern-level: this is the second of three `WorkerCapabilities` copy patterns (the third is `_caps_to_dict` in registration.py:78). The drift surface is the same as MAINT-002 in spirit.

**Recommendation.** Replace lines 71-84 with `return dataclasses.replace(caps, metadata=enriched)`. Drop the parameter `caps.metadata`; the function is now a 2-liner that can be inlined into `create_worker_app` if desired.

**Verification.** `just test`; `dataclasses.replace(caps, metadata=enriched) == WorkerCapabilities(...)` for the existing 3 cases; `just type-check`.

### MAINT-013 — `_caps_to_response` (edge) and `_caps_to_dict` (registration) duplicate the same `WorkerCapabilities` → dict serialisation

```yaml
status: open
severity: low
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: 1fbedbc
  date: '2026-06-24'
fixed_in: []
files:
- path: src/acheron/worker_sdk/_edge_http.py
  lines: 51-62
- path: src/acheron/worker_sdk/registration.py
  lines: 78-91
related:
- MAINT-012
```

**Issue.** Two functions produce the same `{worker_type, supported_languages_in, supported_languages_out, supported_formats_in, supported_formats_out, max_payload_bytes, batch_capable, model_source, metadata}` dict from a `WorkerCapabilities` value. The only difference is the return-type annotation: `_caps_to_response` → `dict[str, Any]`, `_caps_to_dict` → `dict[str, object]`. Both use `sorted(frozenset)` for the language/format fields and `dict(metadata)` for the metadata.

**Why it matters.** The orchestrator's `POST /workers` request schema (`WorkerCapabilitiesRequest` in `shell/api/schemas.py:45-56`) is the consumer of `_caps_to_dict`; the SDK's `GET /capabilities` response uses `_caps_to_response`. Adding a field to `WorkerCapabilities` (cf. MAINT-012) now requires editing two serialisers in lockstep — the third copy of the same shape. A `WorkerCapabilities.model_dump()` (pydantic) or a single `_caps_to_dict` imported by both call sites removes the duplication.

**Recommendation.** Promote `_caps_to_dict` (registration.py) to the canonical form (it has the better `dict[str, object]` annotation) and import it from `_edge_http.py`. Drop `_caps_to_response`. If pydantic interop is desired, derive both from a single `pydantic.TypeAdapter(WorkerCapabilities).dump_python` call site.

**Verification.** `just test`; `WorkerCapabilities` round-trip parity for both endpoints; `grep -rn 'sorted(caps.supported_languages_in' src/` returns zero hits.

### MAINT-014 — Stub handlers redundantly override the ABC's default no-op `startup`/`shutdown` methods

```yaml
status: fixed
severity: low
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: pending
  date: 2026-06-25
fixed_in: [pending]
files:
- path: stubs/_sdk_base/__init__.py
  lines: 53-57, 101-105, 140-144
- path: stubs/_sdk_base/__init__.py
  lines: 101-105
- path: stubs/_sdk_base/__init__.py
  lines: 140-144
related: []
```

**Issue.** `WorkerHandler` (`worker_sdk/handler.py:29-35`) declares `startup` and `shutdown` as no-op ABC defaults (`async def ... -> None: return`). The three stub classes (`StubTTSHandler`, `StubASRHandler`, `StubTranslationHandler`) override both methods with bodies that are literal `return None`. 6 redundant methods × 2 lines each = 12 lines of pure no-op code that, on a greenfield project, will inevitably drift from the ABC's signature (e.g. if the ABC adds an `await self._x()` to the default, the stubs will silently skip it).

**Why it matters.** Per the rubric, "do not add unnecessary and/or coupled comments ... that make changes hard to maintain and generate staleness." Methods are the comment-equivalent here. The override is also confusing to new readers: is there a hidden reason `StubTTSHandler.startup` does something different from the ABC's default? (There isn't — both are `return None`.)

**Recommendation.** Delete all 6 overrides. The `WorkerHandler` defaults take over automatically.

**Verification.** `grep -n 'async def startup\|async def shutdown' stubs/` returns zero hits; `just test` still passes (no behavioural change); `just type-check`.

### MAINT-015 — `inputs.py` is a near-verbatim copy of `artifacts.py` — same Protocol + three-variant shape duplicated 95%

```yaml
status: open
severity: medium
effort: M
reviewed_at: e54458416e9bfe890a473dd9d542978d205b40a1
last_verified_at:
  commit: e54458416e9bfe890a473dd9d542978d205b40a1
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/worker_sdk/inputs.py
    lines: 1-79
  - path: src/acheron/worker_sdk/artifacts.py
    lines: 1-78
related: [MAINT-002, MAINT-013]
```

**Issue.** `src/acheron/worker_sdk/inputs.py` (NEW 79 lines) is structurally a copy of `src/acheron/worker_sdk/artifacts.py` (78 lines): same `@runtime_checkable` Protocol (Input/Artifact), same three variants (Bytes/Stream/File), same field shape (content_type, metadata, stream), same FileX.stream() 64-KiB read loop, same `# noqa: TC003` import pattern, same `dataclass(frozen=True)` field metadata. The only differences are (a) `data: bytes` vs `filename: str` and (b) `producer: Callable` vs `path: Path`. ~80 lines of structural duplication; a new 8c worker will inevitably get a 4th copy unless a shared base is introduced.

**Why it matters.** AGENTS.md bans coupled comments and stale-prone structures; symmetric pairs of structurally-identical files are exactly the kind of drift hotspot the greenfield rubric catches. Adding a field to the wire format (e.g. `encoding`, `checksum`) requires editing both files in lockstep. The 8b layer is the natural moment to consolidate before more workers copy the shape.

**Recommendation.** Introduce a single `Wire[T]` Protocol with a `direction: Literal['input', 'output']` parameter, or extract a shared `_wire.py` base with covariant type parameters. Each variant can be a thin subclass. Bundle: ~80 lines collapse to ~30 + the variants. Keep `inputs.py` and `artifacts.py` as the public re-exports so callers can keep their current imports.

**Verification.** `just test`; `git diff --stat src/acheron/worker_sdk/{inputs,artifacts}.py` shows a single shared base; new test that adding a field to one variant does not require editing the other file; `just type-check`.

## EXC — Exception discipline

**Grade:** A

EXC-001 (medium) remains open and re-resolved: `tenacity` is still unused, and the `_runpod_client.py:84-86` bare `TimeoutError` site is still there. EXC-002 is verified. EXC-003 (low) and EXC-004 (medium) re-resolved with no line shifts. One new finding: EXC-005 (medium) — `_edge_http.py` `EdgeApp._dispatch` catches bare `BaseException` for handler failures, the same anti-pattern as EXC-004 in a second file. Pattern-level: the broad-catch anti-pattern is now present at two sites in the worker_sdk (`app.py:122` and `_edge_http.py:242`); `KeyboardInterrupt` during a long handler and `asyncio.CancelledError` on orchestrator-cancelled steps both get wrapped in a 500 instead of propagating.

### EXC-001 — tenacity dependency is unused; WorkerTimeoutError/PlanValidationError are never raised; transient network calls have no retry

```yaml
status: open
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: 7d4754a
  date: '2026-06-24'
fixed_in: []
files:
- path: pyproject.toml
  lines: 23
- path: src/acheron/core/errors.py
  lines: 27-28, 39-40
- path: src/acheron/shell/executors/streaming.py
  lines: 246-248
- path: src/acheron/shell/transports/grpc.py
  lines: 90-95
- path: src/acheron/shell/transports/http.py
  lines: 66-84
related:
- CORR-014
```

**Issue.** `tenacity~=9.1` is a production dependency (pyproject.toml:20) with zero imports across src/ and tests/. No network call is retried: GrpcWorker.execute (a streaming RPC, grpc.py:67-75) and HttpWorker._request (http.py:44-59) raise on the first transient failure; Redis store ops have no retry. `WorkerTimeoutError` (errors.py:28) is defined and exported but never raised — streaming.py:213 catches `TimeoutError` and raises plain `WorkerError`, discarding the more specific type. `PlanValidationError` (errors.py:16) is likewise defined/exported but never raised. New error path: `_runpod_client.py:84-86` raises bare `TimeoutError` from inside a worker (could/should be `WorkerTimeoutError`).

**Why it matters.** A dead dependency and dead exception types are misleading surface area. Remote worker and Redis calls are exactly where transient failures occur; the absence of retries means a single transient network blip fails a whole step/job. The timeout-specific exception exists but is not used, so callers cannot distinguish a timeout from other worker errors. Medium — no current data loss, but fragile in normal operation and contradicts the declared dependency.

**Recommendation.** Pick one direction per greenfield rules: either remove tenacity, WorkerTimeoutError, and PlanValidationError (YAGNI), or wire tenacity retries into transport/Redis call sites on transient errors only (gRPC UNAVAILABLE/cancelled, HTTP ConnectError/429/5xx, Redis ConnectionError) and raise WorkerTimeoutError on timeout. Do not leave the stubs.

**Verification.** `just validate`. If removing: grep for `tenacity|WorkerTimeoutError|PlanValidationError` returns zero src hits. If adding: tests asserting retry-then-succeed on a flaky stub and timeout→WorkerTimeoutError.

### EXC-002 — Broad `except Exception` at boundary sites is mostly well-applied but two could name narrower types

```yaml
status: verified
severity: low
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in:
  - a5b1ff0
files:
  - path: src/acheron/core/chunking.py
    lines: 28
  - path: src/acheron/shell/cache.py
    lines: 60
  - path: src/acheron/shell/cache.py
    lines: 108
  - path: src/acheron/shell/orchestrator.py
    lines: 123-128
  - path: src/acheron/shell/orchestrator.py
    lines: 216-238
  - path: src/acheron/shell/executors/streaming.py
    lines: 230
  - path: src/acheron/shell/executors/streaming.py
    lines: 244
related: []
```

**Issue.** There are 7 `except Exception` sites in src. Most are defensible task/stage boundaries that wrap into domain errors and log: orchestrator.close (orchestrator.py:116, exception-isolated teardown per docstring), orchestrator._execute (orchestrator.py:216, top-level task boundary → status='failed'), streaming._stage (streaming.py:218,232 → PipelineError). Two could be narrower: chunking.py:28 wraps `nltk.sent_tokenize` in `ChunkingError` but NLTK's common failure is `LookupError` (missing punkt data) — naming it would distinguish missing-data from genuine parse failures; cache.py:60,108 wrap pydantic validation in `CacheCorruptedError` where `pydantic.ValidationError` is the expected type and a broad catch could mask an unrelated OSError or AttributeError bug in the adapter call.

**Why it matters.** Narrower exceptions at these two sites would prevent masking programming errors (e.g., AttributeError from a refactoring bug) as "corruption." Low — the current behavior is correct for the expected cases and all sites log/wrap rather than silently swallow.

**Recommendation.** Narrow chunking.py:28 to `(LookupError, OSError, ...)` as appropriate; narrow cache.py:60,108 to `pydantic.ValidationError` (chaining preserves the original). Leave the orchestrator/streaming boundary catches as-is.

**Verification.** `just test`, `just type-check`. Confirm a missing-punkt scenario still raises ChunkingError and a malformed manifest still raises CacheCorruptedError.

### EXC-003 — HealthMonitor._handle_failure catches bare `Exception` from the platform provider; recovery should live inside the provider contract

```yaml
status: fixed
severity: low
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: pending
  date: 2026-06-25
fixed_in:
- pending
files:
- path: src/acheron/shell/health.py
  lines: 139-148
- path: src/acheron/shell/health_providers.py
  lines: 52-60, 90-98
- path: src/acheron/shell/health_providers.py
  lines: 70-94
related:
- MAINT-008
- OBS-005
```

**Issue.** The platform-provider call inside `_handle_failure` (health.py:139-145) wraps `await provider.check_status(endpoint_id)` in `except Exception as exc:  # noqa: BLE001`. The recovery logs a warning, forces `platform_status = WorkerStatus.OFFLINE`, and chains the error. The broad catch masks programming errors (`TypeError`, `AttributeError`) inside a provider as 'provider transient error'. The `HealthProvider` ABC docstring already promises 'returns OFFLINE on platform error' but the contract is not enforced by the type or by an internal try/except in either implementation.

**Why it matters.** Pattern-level EXC: a third provider that adds a different exception class (e.g. an SDK raising `boto3.ClientError`) would be caught here, hiding transient and permanent failures behind the same log line. A fourth provider that raises `AttributeError` on a payload shape change would be reported as 'provider transient error' rather than a programming bug. The existing `_check_http_health` and `_check_grpc_health` in `health.py:44-65` already swallow narrower `(httpx.HTTPError, OSError)` correctly — apply the same pattern to the providers.

**Recommendation.** Move the swallow into each provider's `check_status`: `RunPodHealthProvider` and `HuggingFaceHealthProvider` catch `(httpx.HTTPError, OSError, ValueError, KeyError)` and return `WorkerStatus.OFFLINE`. `HealthMonitor._handle_failure` then trusts the contract and removes its try/except.

**Verification.** `just test`, `just type-check`. Drop the noqa and the try/except in `_handle_failure`; providers gain the swallow and the `HealthProvider` ABC contract is enforced by the implementation.

### EXC-004 — `create_worker_app` lifespan catches bare `BaseException` for the eager price refresh; swallows `KeyboardInterrupt`/`SystemExit`/`CancelledError`

```yaml
status: verified
severity: medium
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: 1fbedbc
  date: '2026-06-24'
fixed_in:
- 1fbedbc
files:
- path: src/acheron/worker_sdk/app.py
  lines: 123-129
related:
- OBS-008
- MAINT-010
```

**Issue.** The price refresh at startup is wrapped in `try: ... except BaseException:  # noqa: BLE001: logger.warning(...)`. The intent (per the inline comment "fault-tolerant, never blocks") is to allow a transient pricing API failure to not block container startup. But `BaseException` is broader than what the comment justifies — it also catches `KeyboardInterrupt` (Ctrl-C during startup), `SystemExit` (an operator's `docker stop`), and `asyncio.CancelledError` (the FastAPI lifespan being cancelled by uvicorn). All three should propagate to the operator; swallowing them turns a clean shutdown into a hanging container. The `app.py:122` `# noqa: BLE001` only suppresses the *lint* complaint; it doesn't argue the *correctness* of catching `BaseException`.

**Why it matters.** A worker container that swallows `KeyboardInterrupt` during the first 1-2 seconds of boot (the price-refresh window) leaves the operator's Ctrl-C without effect. The recovery branch logs at WARNING then proceeds to `await _register()` and then `yield` — the lifespan is fine, but a Ctrl-C'd deployer wonders why the container takes 30s to die. Pattern-level: the same anti-pattern as EXC-003 (`# noqa: BLE001` at the boundary instead of inside the provider).

**Recommendation.** Narrow to `except Exception:  # noqa: BLE001` (the comment can stay; `Exception` covers `httpx.HTTPError`, `OSError`, `KeyError`, `ValueError`, `TypeError` — the actual failure modes of a GraphQL price query). `BaseException` subclasses that should propagate (`KeyboardInterrupt`, `SystemExit`, `CancelledError`) will then do so. If the project wants to be extra strict, list the concrete types: `except (httpx.HTTPError, OSError, KeyError, ValueError, TypeError):`.

**Verification.** `just test`; a new test that injects a price source whose `refresh()` raises `KeyboardInterrupt` (or `asyncio.CancelledError`) and asserts the exception propagates out of the lifespan, not into the WARNING log.

### EXC-005 — `_edge_http.py` `_dispatch` catches bare `BaseException` for handler failures; same anti-pattern as EXC-004 in a second file

```yaml
status: verified
severity: medium
effort: S
reviewed_at: e54458416e9bfe890a473dd9d542978d205b40a1
last_verified_at:
  commit: 1fbedbc
  date: '2026-06-24'
fixed_in:
- 1fbedbc
files:
- path: src/acheron/worker_sdk/_edge_http.py
  lines: 286-304
related:
- EXC-004
```

**Issue.** `EdgeApp._dispatch` (lines 237-255) wraps `await self.handler.handle(job, input_obj)` in `except BaseException as exc:`. This is the same anti-pattern as EXC-004 (`create_worker_app` lifespan, app.py:122). `BaseException` is broader than what the error-conversion contract justifies: it also catches `KeyboardInterrupt` (operator Ctrl-C during a long handler), `SystemExit` (container shutdown), and `asyncio.CancelledError` (FastAPI's own request cancellation, which is the dominant path for an orchestrator cancelling a step). All three should propagate cleanly. The diff also removed the 3-line inline rationale comment, so the broad catch is now unexplained at the call site.

**Why it matters.** Pattern-level EXC: the same broad-catch anti-pattern is now present in two files (`app.py:122` and `_edge_http.py:242`). The handler is async user code — it can raise `KeyboardInterrupt` during model load — and the broad catch means a Ctrl-C'd deployer gets a 500 response and a WARNING log line instead of a clean shutdown.

**Recommendation.** Narrow to `except Exception as exc:` (or list the concrete expected types: `(WorkerError, ValueError, KeyError, TypeError, OSError)`). The `BaseException` subclasses that should propagate (`KeyboardInterrupt`, `SystemExit`, `CancelledError`) will then do so.

**Verification.** `just test`; new test that injects a handler raising `KeyboardInterrupt` and asserts the exception propagates out of `_dispatch` rather than being wrapped in a 500; `grep -n 'except BaseException' src/acheron/worker_sdk/` returns zero hits.

## TYPE — Type safety

**Grade:** A

TYPE-002 remains verified. All seven open TYPE stories (TYPE-001, 003, 004, 005, 006, 007, 008) re-resolved: most line numbers held, with notable shifts in `cloud.py` (TYPE-007 moved from 132-139 to 164-168; TYPE-008 expanded from 7 to 10 sites, with significant line shifts in `cloud.py` and `_edge_http.py`). The new `workers/granite_speech/handler.py` package is now in scope: TYPE-009 (low) — `GraniteSpeechRunpodHandler` types `self._model` and `self._processor` as `Any` with a 2-line stale-prone comment justifying a workspace-test detail; a Protocol stub under `TYPE_CHECKING` would be cleaner. Pattern-level: the worker packages are a new `Any`/`# type: ignore` surface that will multiply the worker_sdk count.

### TYPE-001 — AcheronClient returns dict[str, Any] consumed via magic-string keys; metadata contracts partially resolved

```yaml
status: open
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: 0e6c576
  date: '2026-06-24'
fixed_in: []
files:
- path: src/acheron/api_client.py
  lines: 41-128
- path: src/acheron/cli.py
  lines: 165-292
related:
- ARCH-004
```

**Issue.** Every `AcheronClient` method returns `dict[str, Any]` or `list[dict[str, Any]]` (api_client.py:35,51,58,65,76). The CLI then indexes these with magic strings — `result['job_id']`, `j['status']`, `j['worker_id']`, `p['workers']`, `result['errors']` (cli.py:169-236); any key typo is a runtime KeyError. Typed pydantic response schemas (JobResponse, WorkerResponse, CapabilitiesResponse) already exist in schemas.py but are not used on the client side. The metadata contract sub-issue is resolved: `RegisteredWorker.metadata` (registry.py:27), `WorkerStore.register` metadata (stores/base.py:30), and schemas.py:55 are now all `dict[str, JsonValue]`, matching `WorkerCapabilities.metadata`.

**Why it matters.** AGENTS.md explicitly says avoid `Any` and avoid "Mapping[str, Any] as documentation-via-runtime-error contract." The CLI's untyped consumption of the API is exactly that pattern. Medium — works today but defeats the type checker on a public boundary.

**Recommendation.** Have `AcheronClient` return the existing pydantic response models (JobResponse, WorkerResponse, CapabilitiesResponse) instead of raw dicts; have the CLI consume typed models via attribute access.

**Verification.** `just type-check`, `just lint-strict`. CLI key access becomes attribute access verified by mypy; a typo is now a compile error.

### TYPE-002 — PlanResult.status and TrackedJob.status are stringly-typed with a vocabulary diverging from the JobStatus enum

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in:
  - ad78be49740dd3d5094ba30a376593c1e2e499cb
files:
  - path: src/acheron/core/models.py
    lines: 133
  - path: src/acheron/shell/job_store.py
    lines: 21
  - path: src/acheron/shell/orchestrator.py
    lines: 180-218
  - path: src/acheron/shell/executors/sequential.py
    lines: 56
  - path: src/acheron/shell/executors/async_executor.py
    lines: 64
  - path: src/acheron/shell/executors/streaming.py
    lines: 158-237
related: [CORR-001]
```

**Issue.** `JobResult.status` was typed `JobStatus` (enum: success/failed/partial) but `PlanResult.status` (models.py:135) and `TrackedJob.status` (job_store.py:21) were `str`, assigned via bare string literals across 10+ sites. A typo like 'complted' compiled and silently produced a wrong status; orchestrator._execute set `tracked.status='failed'` (string) while the underlying JobResult carried `JobStatus.FAILED` (enum) — two representations of the same concept with no compiler enforcement.

**Why it matters.** Violated AGENTS.md "make illegal states unrepresentable" and "avoid string-based dispatch." Status typos were unchecked and the dual success/completed vocabulary was a latent confusion bug. Medium — not currently broken but a clear typing gap.

**Recommendation.** Introduce a `PlanStatus` enum for `PlanResult.status` and `TrackedJob.status`; replace all string-literal assignments with enum members. Align the 'completed'/'success' vocabularies.

**Verification.** `just type-check` (mypy flags string assignments to enum fields), `just lint-strict`. grep for `status\s*=\s*["']` returns zero hits in src.

### TYPE-003 — redis.py accumulates 8 `# type: ignore[misc]` markers on `await self._redis.<method>()` calls

```yaml
status: open
severity: medium
effort: M
reviewed_at: 63faed4
last_verified_at:
  commit: e54458416e9bfe890a473dd9d542978d205b40a1
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/shell/stores/redis.py
    lines: 296
  - path: src/acheron/shell/stores/redis.py
    lines: 327
  - path: src/acheron/shell/stores/redis.py
    lines: 334
  - path: src/acheron/shell/stores/redis.py
    lines: 361
  - path: src/acheron/shell/stores/redis.py
    lines: 362
  - path: src/acheron/shell/stores/redis.py
    lines: 388
  - path: src/acheron/shell/stores/redis.py
    lines: 405
  - path: src/acheron/shell/stores/redis.py
    lines: 427
related: []
```

**Issue.** `redis.py:3-5` documents that `redis.asyncio` stubs type methods as `Awaitable[T] | T` and that the `T` branch is unreachable in async call sites. Despite the file-level justification, every `await self._redis.<method>()` call site carries a `# type: ignore[misc]` marker (8 sites: `ping` x2 at 296/402, `hgetall` at 327, `smembers` x2 at 331/424, `hincrby` at 361, `hset` at 362, `hset-with-mapping` at 388). The markers do not have per-site justification comments, and the rationale depends on a single header paragraph that could be deleted in a future refactor pass. The new `set_worker_status` method (lines 375-388) added another `hset(mapping=...)` call site at line 388, growing the list. A future `redis-py` version that fixes the stubs would leave the markers as dead annotations.

**Why it matters.** Pattern-level TYPE: each marker is a future-typing-debt accumulator per the rubric. AGENTS.md says "Avoid linter and type ignores in general without a very good reason that should be explicitly explained to the user" — the explanation exists at the file level but the per-site obligation is not met.

**Recommendation.** Either (a) add a one-line `# noqa: misc: redis.asyncio stubs Awaitable[T]|T; see file header` per site to make each marker self-documenting, or (b) centralize the redis access in a thin `RedisAwaitable` wrapper class with a single `# type: ignore[misc]` and a docstring. Option (b) collapses 8 markers to 1 and the next provider change touches one place.

**Verification.** `just type-check`, `just test`. The count of `# type: ignore[misc]` markers should drop to 0 (option b) or to 8 with consistent per-site comments (option a).

### TYPE-004 — WorkerResponse.status is stringly-typed despite a WorkerStatus enum existing at core/models.py

```yaml
status: fixed
severity: low
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: pending
  date: 2026-06-25
fixed_in: [pending]
files:
  - path: src/acheron/shell/api/schemas.py
    lines: 70-78
  - path: src/acheron/shell/api/routes/workers.py
    lines: 51
  - path: src/acheron/shell/api/routes/workers.py
    lines: 68
related: [TYPE-005]
```

**Issue.** The new `WorkerStatus` enum is defined at `core/models.py:58-63` and used everywhere in `shell/` (registry, health, stores, health_providers). The API response schema `WorkerResponse.status` is typed `str = "healthy"` (schemas.py:75), and `routes/workers.py:51,68` call sites do `WorkerStatus.HEALTHY.value` to populate the schema. AGENTS.md says "avoid string-based dispatch" and "make illegal states unrepresentable" — the response schema is the exact anti-pattern. A typo in a test fixture like `status='healty'` would compile and pass pydantic validation; the `WorkerStatus` enum would reject it.

**Why it matters.** The internal API is fully enum-typed; the public API is the only stringly-typed surface. This is exactly the boundary the project wants typed. Low — no current bug, but a lost typo-check at the API boundary.

**Recommendation.** Change `WorkerResponse.status: WorkerStatus = WorkerStatus.HEALTHY` and let pydantic serialize the enum to a JSON string automatically. Update `routes/workers.py:51,68` to use `WorkerStatus.HEALTHY` and `w.status` directly (no `.value`).

**Verification.** `just test`, `just type-check`. Confirm a malformed `status='healty'` is now rejected by pydantic, and the response JSON still serializes to `"status": "healthy"`.

### TYPE-005 — `JobResponse.status` and `JobResponse.total_cost_basis` are stringly-typed despite `PlanStatus` and `CostBasis` enums existing at `core/models.py`

```yaml
status: fixed
severity: low
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: pending
  date: 2026-06-25
fixed_in: [pending]
files:
  - path: src/acheron/shell/api/schemas.py
    lines: 31
  - path: src/acheron/shell/api/schemas.py
    lines: 38
  - path: src/acheron/shell/api/routes/jobs.py
    lines: 90
  - path: src/acheron/shell/api/routes/jobs.py
    lines: 96
related: [TYPE-004]
```

**Issue.** Direct parallel to TYPE-004 (now fixed for `WorkerResponse`). `JobResponse.status: str` (schemas.py:29) and `JobResponse.total_cost_basis: str | None = None` (schemas.py:35, added in this diff at 63faed4..HEAD) are typed as raw strings even though `PlanStatus` (models.py:42-49) and `CostBasis` (models.py:68-74) enums exist and are the only values ever passed in. The call sites at `routes/jobs.py:90,96` do `tracked.status.value` and `result.total_cost_basis.value`. A test fixture typo like `status='complted'` is accepted by pydantic at request time but rejected by `PlanStatus` at construction. The new `total_cost_basis` field (added in 63faed4..HEAD) is the most recent instance of the same anti-pattern.

**Why it matters.** The internal API is fully enum-typed post-TYPE-002; the public API is now the only stringly-typed surface. Three enums (`WorkerStatus`, `PlanStatus`, `CostBasis`) all leak `.value` strings across the wire for the same reason.

**Recommendation.** Change `JobResponse.status: PlanStatus = PlanStatus.PENDING` and `JobResponse.total_cost_basis: CostBasis | None = None`. Update `routes/jobs.py:90,96` to drop `.value`. Pydantic serialises the enum to its JSON string automatically. One mechanical edit unblocks a class of typo bugs.

**Verification.** `just test`, `just type-check`; a malformed `status='complted'` request body is now rejected at the request schema (the pydantic `extra='forbid'` already applies to requests — the change is to make the response type also strict). Confirm response JSON still serialises to `"status": "completed"`.

### TYPE-006 — `grpc.py` accumulates 5 `# type: ignore[...]` markers for the new proto Artifact oneof

```yaml
status: open
severity: low
effort: M
reviewed_at: dbec2be
last_verified_at:
  commit: 7d4754a
  date: '2026-06-24'
fixed_in: []
files:
- path: src/acheron/shell/transports/grpc.py
  lines: 52, 73, 81, 108, 153
- path: src/acheron/shell/transports/grpc.py
  lines: 72
- path: src/acheron/shell/transports/grpc.py
  lines: 80
- path: src/acheron/shell/transports/grpc.py
  lines: 107
- path: src/acheron/shell/transports/grpc.py
  lines: 152
related: []
```

**Issue.** The grpc transport grew 5 new `# type: ignore[...]` markers as part of the proto Artifact oneof change: line 49 `no-untyped-call` (SynthesisStub constructor — proto stubs are untyped), line 72 `attr-defined` (SynthesisRequest — same), line 80 `name-defined` (`list[synthesis_pb2.Artifact]` — proto-generated names are not picked up by mypy), line 107 `name-defined` (function arg), line 152 `no-any-return` (response.status — the HealthCheckResponse.SERVING is an Any-typed enum value). None of the markers have a per-site comment explaining the suppression; the file-level mypy override `[tool.mypy.overrides] module = ["acheron.proto.*"] ignore_errors = true` (pyproject.toml:107-109) is the closest thing to a justification, but it only silences *errors*, not the markers' future-typing-debt role. The `name-defined` markers (lines 80, 107) are the new addition — they appear because `synthesis_pb2.Artifact` isn't in the mypy module's namespace despite being generated.

**Why it matters.** Pattern-level TYPE: 5 markers in a 152-line file, with no single root-cause comment. The `# type: ignore[attr-defined]` and `# type: ignore[name-defined]` are arguably the same root cause (mypy can't see proto-generated classes), but they get suppressed as if they were different. A future regen of the proto stubs (or a mypy version bump) may resolve some but not all, leaving 3 dead markers. Pattern is similar to TYPE-003 in redis.py.

**Recommendation.** Two options: (a) add one `TypeAlias` line at the top of grpc.py — `_Artifact = synthesis_pb2.Artifact  # type: ignore[attr-defined]` — that imports the name once and lets the rest of the file drop the `synthesis_pb2.Artifact` annotation; (b) widen the mypy override to cover the entire grpc transport (less surgical). Option (a) collapses 2 of the 5 markers. The other 3 (`no-untyped-call`, `attr-defined` on SynthesisRequest, `no-any-return` on response.status) are genuine proto-stub gaps and should keep their markers with one-line comments.

**Verification.** `just type-check`; the count of `# type: ignore` in grpc.py drops from 5 to 3, all with one-line justifications.

### TYPE-007 — `RunPodForwarderHandler.__init__` calls `phantom_handler(settings)` under `# type: ignore[call-arg]`

```yaml
status: open
severity: low
effort: M
reviewed_at: dbec2be
last_verified_at:
  commit: 7d4754a
  date: '2026-06-24'
fixed_in: []
files:
- path: src/acheron/worker_sdk/cloud.py
  lines: '175'
related: []
```

**Issue.** `RunPodForwarderHandler.__init__` accepts `phantom_handler: type[WorkerHandler] | None` and, if non-None, calls `phantom_handler(settings)`. The call is annotated `phantom_handler(settings)  # type: ignore[call-arg]`. The comment (lines 134-136) explains: *"The phantom's __init__ is not part of the WorkerHandler Protocol (each handler defines its own constructor); we type-ignore the call site rather than widening the Protocol signature."* This is the *right* call, but the rightness is locked inside a comment, not a type — the next `cloud.py` reader has to re-derive it. The `WorkerHandler` Protocol (handler.py:13-19) and ABC (handler.py:13-35) both omit `__init__`, so a `type[WorkerHandler]` constraint carries no constructor shape. A `type[WorkerHandler]`-typed parameter that must be `Callable[[WorkerSettings], WorkerHandler]` is an interesting TypeScript-style "duck-type the constructor" — but the actual type is the more honest `Callable[[WorkerSettings], WorkerHandler]`.

**Why it matters.** A reader who sees `phantom_handler: type[WorkerHandler]` will reasonably wonder *what* the constructor signature is. The `# type: ignore[call-arg]` is a real signal that the annotation lies. Per AGENTS.md, "prefer ... use typing in your favor to avoid seas of complex branching that are brittle and hard to maintain." The right typing here is the constructor-protocol.

**Recommendation.** Introduce a `class WorkerHandlerFactory(Protocol): def __call__(self, settings: WorkerSettings) -> WorkerHandler: ...` (or `type WorkerHandlerFactory = Callable[[WorkerSettings], WorkerHandler]`) and type the field/parameter as `WorkerHandlerFactory | None`. Drop the `# type: ignore[call-arg]`. The call site `phantom_handler(settings)` then type-checks because the factory's protocol says exactly that.

**Verification.** `just type-check` (the `# type: ignore[call-arg]` disappears); `just test`; new test that an invalid factory (e.g. `lambda _: ...`) is rejected by mypy.

### TYPE-008 — WorkerSDK has 14+ `Any`/`dict[str, Any]` annotations in 5 files

```yaml
status: open
severity: low
effort: M
reviewed_at: dbec2be
last_verified_at:
  commit: 1fbedbc
  date: '2026-06-24'
fixed_in: []
files:
- path: src/acheron/worker_sdk/cloud.py
  lines: 20, 38-39, 42
- path: src/acheron/worker_sdk/_edge_http.py
  lines: 13, 51, 73, 77, 84, 153, 195
- path: src/acheron/worker_sdk/pricing.py
  lines: 13,183,184,192
- path: src/acheron/worker_sdk/cli.py
  lines: 20,31,39,63,64
- path: src/acheron/worker_sdk/app.py
  lines: 32-52
related: []
```

**Issue.** The new worker_sdk is the largest `Any` consumer in src/. Each `dict[str, Any]` annotation is a wire shape (RunPod job input, multipart metadata, GraphQL response, settings dict). The project already has `JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]` at `core/models.py:8` and uses it for `WorkerCapabilities.metadata` everywhere. The SDK's wire-side dicts could reuse the same alias. `_runpod_client._post_graphql` (pricing.py:179-193) returns `dict[str, Any]` for the GraphQL body, but every field it then accesses (`data.myself.endpoints`, `gpuTypes[0].lowestPrice.uninterruptablePrice`) is stringly-keyed — a pydantic `GraphQLResponse` model would convert these to typed access.

**Why it matters.** AGENTS.md explicitly bans `Any`: *"Avoid Any and don't let Mapping[str, Any] become a documentation-via-runtime-error contract."* The SDK is the new public surface — it sets the typing tone for the next dozen worker packages. Bundle: each is a 1-line edit; together they make the SDK `Any`-free and let mypy catch shape errors at construction (e.g. `_runpod_client.run` accepts a `dict[str, object]` which is even broader than `Any` for keys, but the inner access doesn't constrain values at all).

**Recommendation.** Three bundles, one per file. (1) `cloud.py` + `_edge_http.py` + `app.py`: replace `dict[str, Any]` with `dict[str, JsonValue]` for the wire-side payloads (RunPod input, multipart metadata header, settings dict). The `_rp_handler` shape in cloud.py:37,40 stays `Callable[..., Awaitable[dict[str, Any]]]` because the runpod SDK uses raw dicts internally. (2) `pricing.py`: introduce a pydantic `GraphQLGpuTypesResponse` for the GPU price query; the `_post_graphql` helper returns `dict[str, Any]` as an internal type only. (3) `cli.py`: `type[Any]` for the imported handler class is fine (intentional, since the import path is configurable) — keep that one. Audit result: roughly 6 `Any` annotations remain after the cleanup (all justified, all in `Any`-typed dispatch sites).

**Verification.** `grep -rn 'dict\[str, Any\]\|: Any' src/acheron/worker_sdk/` drops from 14+ to ~6 justified sites; `just type-check`; `just test`.

### TYPE-009 — `GraniteSpeechRunpodHandler` types `self._model` and `self._processor` as `Any`; 2-line comment is a stale-prone impl-phase justification

```yaml
status: fixed
severity: low
effort: S
reviewed_at: e54458416e9bfe890a473dd9d542978d205b40a1
last_verified_at:
  commit: 45599f0
  date: 2026-06-25
fixed_in:
- 45599f0
files:
- path: workers/granite_speech/handler.py
  lines: 14-58
related:
- TYPE-008
```

**Issue.** `GraniteSpeechRunpodHandler.__init__` (lines 37-42) types `self._model: Any = None` and `self._processor: Any = None`. The accompanying 2-line comment ('The model + processor are typed loosely so the workspace tests don't need torch or transformers installed') is a stale-prone impl-phase comment — it references a workspace-test implementation detail that can change. The actual production code does not use the `Any`: lines 64-80 import torch + transformers inside `startup()`; lines 127-145 import torch inside `_transcribe()`.

**Why it matters.** AGENTS.md explicitly bans `Any` and stale-prone comments that reference impl details. Bundle-level TYPE-008 already counts 14+ `Any` annotations in worker_sdk; the worker packages are a new surface that will multiply the count.

**Recommendation.** Introduce a minimal Protocol stub for `_ModelProto` and `_ProcessorProto`. Type the fields as `_ModelProto | None` and `_ProcessorProto | None`. Move the heavy imports under `TYPE_CHECKING` for type-checker satisfaction. Delete the 2-line comment — the `TYPE_CHECKING` import explains itself.

**Verification.** `just type-check` (no new `# type: ignore` needed); `grep -n ': Any' workers/granite_speech/handler.py` drops from 2 to 0; `just test`.

## MAINT (8c delta)

### MAINT-016 — `ChunkingTooLongForWorkerError` subclasses `InvalidLanguagePathError` — inheritance used as a type-tag dispatch mechanism

```yaml
status: verified
severity: medium
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: 4863597
  date: 2026-06-24
fixed_in: [4863597]
files:
  - path: src/acheron/core/errors.py
    lines: 16-20
  - path: src/acheron/core/planner.py
    lines: 92-128
  - path: src/acheron/shell/orchestrator.py
    lines: 274-278
related: [ARCH-018]
```

**Issue.** `ChunkingTooLongForWorkerError` is a subclass of `InvalidLanguagePathError` only so existing `except InvalidLanguagePathError` handlers (in the dashboard / job-rejection code) keep matching. The class docstring admits this verbatim: "Subclass of `InvalidLanguagePathError` so existing handling (job rejection, dashboard) still works." That is exactly the type-tag-dispatch pattern AGENTS.md prohibits ("prefer strict domain separation, avoid string-based dispatch, and use typing in your favor"). The two errors are conceptually unrelated: one is a language-pair capability problem, the other is a chunk-size-budget problem. Once a second caller wants to distinguish "language pair unsupported" from "chunking too long", the inheritance is a trap — `except InvalidLanguagePathError` will now wrongly catch chunking errors, and the subclass chain (AcheronError → PlanError → InvalidLanguagePathError → ChunkingTooLongForWorkerError) misrepresents the domain hierarchy.

**Why it matters.** Pattern-level MAINT/ARCH: the new `submit_job` flow treats `validate_chunking_fits_workers` as a sibling validator to `compile_plan`, but couples their error types via inheritance. The orchestrator's `submit_job` docstring promises "Raises AcheronError if plan compilation fails" — that contract is now satisfied via the inheritance ladder rather than a shared base, and any future caller that catches `InvalidLanguagePathError` will over-match. The 8c layer is the moment to fix the hierarchy before more plan-time validators (token budgets, format constraints, model availability) copy the same pattern.

**Recommendation.** Make `ChunkingTooLongForWorkerError` a sibling of `InvalidLanguagePathError` (both subclass `PlanError`). At the API boundary, catch the shared `PlanError` parent for job-rejection UX. The dashboard's existing `InvalidLanguagePathError` handler will silently stop catching the new error — but the dashboard already catches the broader `PlanError` (or should), so this is the correct separation. If a single catch is required at the call site, use `except PlanError as exc:` and let the `__cause__` distinguish the two.

**Verification.** `grep -rn 'except InvalidLanguagePathError' src/ tests/` should show no call site that needs the subclass; `grep -n 'ChunkingTooLongForWorkerError' src/` confirms a single shared parent; `just type-check`; `just test`; the new `tests/shell/test_orchestrator.py:ChunkingTooLongForWorkerError` test still passes (it catches the new exception directly, not the parent).

### MAINT-017 — chunks.json parsing duplicated byte-for-byte between qwen3tts and translategemma handlers — third instance of the wire-shape drift pattern

```yaml
status: fixed
severity: medium
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: pending
  date: 2026-06-25
fixed_in:
- pending
files:
- path: workers/_shared.py
  lines: 82-104
- path: workers/qwen3tts/handler.py
  lines: 131
- path: workers/translategemma/handler.py
  lines: 187
related:
- MAINT-015
- MAINT-018
- CORR-032
```

**Issue.** The 13-line `chunks.json` parsing block is byte-identical in both worker handlers. The translategemma version (lines 187-199) is inline inside `handle()`; the qwen3tts version (lines 198-216) is the `_load_chunks` method. Both do `b''.join([chunk async for chunk in input.stream()])`, both check `if not chunks_json_bytes: return []`, both call `json.loads(chunks_json_bytes.decode('utf-8'))`, both catch `(json.JSONDecodeError, UnicodeDecodeError) as exc: raise WorkerError(msg) from exc`, and both check `isinstance(raw_chunks, list)` with the same error string. Layer 8c is the natural consolidation moment: a third worker that consumes `chunks.json` (a future ASR-via-chunks, an OCR worker, a TTS-via-chunks) will copy this verbatim.

**Why it matters.** Direct parallel to MAINT-015 (inputs.py/artifacts.py structural copy) and MAINT-002 (redis/cache dual serialization). The greenfield rubric flags "two divergent paths for the same wire format" as a fragility hotspot. Each handler-local copy can drift in error messages, validation rules, or stream handling; a single test that fixes a bug in one handler will silently leave the other broken.

**Recommendation.** Extract `parse_chunks_json(input: Input) -> list[dict[str, Any]]` to `workers/_shared.py` (or a new `workers/_shared/chunks.py` if the shared module grows). Both `Qwen3TTSRunpodHandler._load_chunks` and the inline parser in `TranslateGemmaRunpodHandler.handle` collapse to a single one-liner. Move the test cases for malformed-JSON and non-list top-level to the shared helper's test file (under `workers/_shared/tests/`).

**Verification.** `grep -rn 'chunks_json_bytes = b""' workers/` returns one hit (the shared helper); the two call sites both call `parse_chunks_json(input)`; `just test` (both handler test files still pass; the shared helper's test file adds coverage for the malformed-input cases).

### MAINT-018 — Per-chunk field validation duplicated between translategemma (_normalize_chunk) and qwen3tts (_chunk_text / _chunk_chapter_id); shared `Chunk` dataclass would unify them

```yaml
status: fixed
severity: low
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: pending
  date: 2026-06-25
fixed_in:
- pending
files:
- path: workers/_shared.py
  lines: 40-71
- path: workers/qwen3tts/handler.py
  lines: 138-156
- path: workers/translategemma/handler.py
  lines: 196-204
related:
- MAINT-017
- MAINT-019
```

**Issue.** Translategemma's `_normalize_chunk(c: object) -> dict[str, Any]` (lines 297-315) validates and returns a fresh dict with `chapter_id`/`sequence_id`/`text`. Qwen3TTS splits the same validation into two module-level helpers: `_chunk_text(c) -> str` (lines 58-67) and `_chunk_chapter_id(c) -> str` (lines 70-84). Both validate the same three fields with the same isinstance checks, raise `WorkerError` on the same failure modes, and use the same field names. The translategemma shape (return a normalised dict) is more reusable, but the qwen3tts helpers are inline-callable. The drift risk is that a future chunk schema change (e.g. adding a `metadata` field) touches two files with different shapes.

**Why it matters.** Pattern-level MAINT. AGENTS.md bans "stale-prone comments ... and coupled structures." The handlers diverge in shape but not in intent — one returns a `dict[str, Any]`, the other returns per-field strings. Once a future handler needs `chunk.metadata` or `chunk.instruct` (qwen3tts already reads `chunk.get('instruct', '')` at line 167), the helper will fork into N per-field accessors per handler.

**Recommendation.** Add a `Chunk` dataclass to `workers/_shared.py` (or alongside the suggested `parse_chunks_json` helper) with `chapter_id: str`, `sequence_id: int`, `text: str`, and an optional `instruct: str = ''` field (qwen3tts already reads it). The dataclass has a `from_dict(c: object) -> Chunk` classmethod that performs the validation. The translategemma handler uses `Chunk.from_dict`; the qwen3tts handler uses `Chunk` accessors instead of `_chunk_text`/`_chunk_chapter_id`.

**Verification.** `grep -rn 'chapter_id.*str.*sequence_id.*int\|isinstance.*chapter_id' workers/` returns one hit; the two handlers both consume the shared `Chunk` type; `just test`; the `sequence_id: int` validator now lives in one place.

### MAINT-019 — `TranslateGemmaRunpodHandler.handle` is 54 lines (over 50) and bundles 3 distinct concerns: validation, parsing, inference + artifact building

```yaml
status: fixed
severity: low
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: pending
  date: 2026-06-25
fixed_in:
- pending
files:
- path: workers/translategemma/handler.py
  lines: 170-177
related:
- CORR-029
- MAINT-017
- MAINT-018
```

**Issue.** `TranslateGemmaRunpodHandler.handle` (lines 170-223) is 54 lines and conflates three responsibilities: (1) precondition validation (model loaded, input present, src/tgt in supported langs) — lines 172-185, (2) chunks.json parsing (lines 187-201) — same body as the qwen3tts `_load_chunks` method, (3) inference call + artifact-building loop (lines 203-223). The function is exactly the same length as `Qwen3TTSRunpodHandler.handle` (53 lines), so this is not a new regression — but the consolidation of the parsing block (MAINT-017) automatically shrinks `handle` to ~38 lines. A pattern-level observation: the handlers' `handle()` methods are converging on the same shape and the same 50-line threshold.

**Why it matters.** Per the brief's function-length >50 line rule. Each handler's `handle()` mixes validation, parsing, and inference at the same level of abstraction, which makes the inference step harder to test in isolation. The artifact-building loop (lines 205-223) is also the only place that depends on `self._settings.model_id` — a small helper would let the test fixture exercise it without the rest of the handler.

**Recommendation.** Once the `parse_chunks_json` helper (MAINT-017) is in place, extract `_validate_job(job: Job) -> tuple[str, str]` (returns src/tgt) and `_build_translation_artifacts(chunks, translated, src, tgt, model_id) -> list[Artifact]` as private methods. `handle` then becomes: validate → parse → call inference → build artifacts, each step a one-liner. Mirrors the qwen3tts `_load_chunks` / `_validate_target_lang` / `_resolve_speaker` decomposition that landed in this delta.

**Verification.** `grep -n 'def handle' workers/translategemma/handler.py` shows a handle method under 30 lines; new test that `_build_translation_artifacts` is reachable directly from the test file; `just test`.

## TYPE (8c delta)

### TYPE-010 — All three RunPod worker handlers type self._model/self._processor as `Any` with a stale-prone impl-phase comment — third instance of TYPE-009

```yaml
status: open
severity: low
effort: M
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: 0e6c576
  date: '2026-06-24'
fixed_in: []
files:
- path: workers/translategemma/handler.py
  lines: 118-121
- path: workers/qwen3tts/handler.py
  lines: 98-99, 39-42, 118-121
- path: workers/granite_speech/handler.py
  lines: 37-42
related:
- TYPE-009
- CORR-033
```

**Issue.** The delta adds the third worker package with the same `self._model: Any = None` / `self._processor: Any = None` anti-pattern, plus the same 2-line stale-prone comment ("The model + processor are typed loosely so the workspace tests don't need torch or transformers installed"). Concretely: `TranslateGemmaRunpodHandler.__init__` (translategemma/handler.py:118-121) now types both `_model` and `_processor` as `Any` with the same comment. The pre-existing qwen3tts `Any` typing also surfaces a new `# type: ignore[no-any-return]` marker at line 172 (`return self._model.generate_custom_voice(...)`) introduced in this delta — directly enabled by the `Any` typing. All three handlers carry the same comment text, which is itself a copy-paste smell. The comment references an impl-phase workspace-test detail that has now changed once (and will change again when the workspace test layout is reorganised).

**Why it matters.** AGENTS.md bans `Any` and stale-prone comments. TYPE-009 already tracks granite_speech; this delta extends the same anti-pattern to translategemma and adds a new `# type: ignore` to qwen3tts as a knock-on effect. The worker packages are a new `Any`/`# type: ignore` surface that will multiply the count. A future per-worker fix would touch three files; a Protocol-based fix touches one shared stub.

**Recommendation.** Bundle the three handlers. Introduce a shared `_ModelProto` and `_ProcessorProto` Protocol in `worker_sdk/handler.py` (or a new `worker_sdk/_handler_types.py`) declaring the small subset of attributes the handlers actually use (`.generate`, `.apply_chat_template`, `.tokenizer`, `.decode`). Type the fields as `_ModelProto | None` and `_ProcessorProto | None`. Move the heavy `import torch` / `import transformers` under `TYPE_CHECKING` for type-checker satisfaction. Delete all three 2-line comments — the `TYPE_CHECKING` import explains itself. The qwen3tts `# type: ignore[no-any-return]` at line 172 disappears as a free side effect.

**Verification.** `grep -rn ': Any = None' workers/` returns zero hits; the qwen3tts `# type: ignore[no-any-return]` at line 172 is gone; `just type-check`; `just test` (workspace tests still run because the lazy `import torch` and `import transformers` inside `startup` are unchanged).
