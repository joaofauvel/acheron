---
branch: chore/code-review-update
initial_review_commit: 23c29e1
last_updated_commit: dbec2be
last_staleness_scan:
  commit: dbec2be
  date: 2026-06-23
---

# Architecture

## ARCH — Architecture

**Grade:** B

Layer 8a added the `worker_sdk` subpackage and the `workers/qwen3tts/` RunPod worker, plus a transport `_multipart` refactor. The hexagonal layering is clean (no `core/` → `shell/`, `worker_sdk` → `shell/`, or `workers/` → `shell/` imports), but the new code surfaced three new ARCH findings and one transport-DRY finding. ARCH-008 re-resolved (lines shifted by the new constructor). All other stories remain verified at dbec2be.

### ARCH-001 — BatchAsyncExecutor is a no-op duplicate of AsyncExecutor; ExecutorStrategy.BATCH_ASYNC controls nothing

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
    lines: 26-79
  - path: src/acheron/shell/executors/async_executor.py
    lines: 22-75
  - path: src/acheron/shell/executors/__init__.py
    lines: 31-32
  - path: src/acheron/core/models.py
    lines: 42
related: [CORR-002, MAINT-001]
```

**Issue.** `BatchAsyncExecutor.run()` (batch_async.py:26-79) is byte-for-byte identical to `AsyncExecutor.run()` (async_executor.py:22-75) except for the docstring — a direct diff of the two bodies shows only the docstring line differs. The class docstring (batch_async.py:17-21) promises that "Batch-flagged steps receive all outputs from completed preceding steps so the handler can construct a BatchJob with the correct payloads," but the implementation just calls `self._handler(step, plan)` per step in a wave — it never constructs a `BatchJob`, never calls `StreamingWorker.submit_batch`/`poll_batch`/`collect_results`, and never inspects `PlanStep.batch`. The `StreamingWorker` ABC methods (interfaces.py:39-51) are only ever invoked by the transport workers themselves (grpc.py:103-124, http.py:77-92), never by any executor. Consequently `ExecutorStrategy.BATCH_ASYNC` (models.py:42) selects a strategy that behaves identically to `ExecutorStrategy.ASYNC`. The CLI even defaulted `--executor` to `batch_async` (cli.py:141), so the default strategy users hit was a no-op abstraction.

**Why it matters.** Users selecting BATCH_ASYNC (including via the CLI default) get plain async behavior while believing they are getting batched GPU submission for throughput — a silent contract violation. This is exactly the "config knob that doesn't actually control anything" and "silent/unexpected behavior is worse than no control at all" cases called out in AGENTS.md hard rules. High because it is a silent behavioral gap on the default strategy, not a crash.

**Recommendation.** Either implement BatchAsyncExecutor to actually gather `step.batch`-flagged steps, build a `BatchJob` from preceding-step outputs, and call `submit_batch`/`poll_batch`/`collect_results` on a `StreamingWorker`; or, if batch submission is not yet real, remove `BatchAsyncExecutor` and `ExecutorStrategy.BATCH_ASYNC` entirely per the greenfield "no legacy fallbacks" rule and switch the CLI default to a strategy that actually exists. Do not keep a named strategy that silently behaves like another.

**Verification.** `just test` with a plan containing `step.batch=True` and a `StreamingWorker` fake asserting `submit_batch` was called; `just lint-imports` after any removal to confirm no dangling references; grep for `submit_batch` callers to confirm the batch path is actually exercised.

### ARCH-002 — Store construction asymmetry: app.py injects WorkerStore but lets Orchestrator create JobStore internally

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: ["b47c6a881a18f859b981f67d04aebc65c209fddc"]
files:
  - path: src/acheron/shell/api/app.py
    lines: 44-53
  - path: src/acheron/shell/orchestrator.py
    lines: 105-119
related: [CFG-001]
```

**Issue.** `create_app` (app.py:44-53) creates the `WorkerStore` externally via `create_worker_store()` and passes it to `Orchestrator(registry=registry, cache=cache)`, but does not pass `job_store`. The Orchestrator then creates the `JobStore` internally via `create_job_store()` (orchestrator.py:119) when `job_store` is None. The construction responsibility for the two stores is therefore split across two layers: the app owns the worker store, the orchestrator owns the job store. Both ultimately read the same `ACHERON_STORE_BACKEND` env var, so they stay consistent today, but the asymmetry means a caller injecting a custom `WorkerStore` (e.g. a test fake, or `app.py` callers wanting Redis) still gets a real `JobStore` from the env-driven factory — which for the Redis backend requires a live Redis server at `connect()` time. The `Orchestrator.__init__` signature accepts `job_store` as optional keyword, so the seam exists, but the production path does not use it.

**Why it matters.** The split makes it impossible to fully control store backends from a single call site in production and forces test fixtures to know to inject both stores to avoid the env-driven default. It is also a latent inconsistency: if `create_worker_store` and `create_job_store` ever diverge, the app and the orchestrator would silently use different selection paths. Medium because it works today but couples construction ownership unclearly.

**Recommendation.** Either create both stores in `create_app` and pass both to `Orchestrator(registry=..., cache=..., job_store=...)`, or have the Orchestrator create both internally (drop the external `registry` injection from the production path, keeping it only as a test seam). Pick one construction owner and apply it consistently.

**Verification.** `just test`; add a test asserting `create_app` with a custom `registry` and custom `job_store` uses both without consulting `ACHERON_STORE_BACKEND`; `just lint-strict`.

### ARCH-003 — Orchestrator accretes capability aggregation, worker registration, job lifecycle, and data-dir verification in one class

```yaml
status: verified
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: ["990b51f64ce8df8c288559bfa6f90548589d8afc"]
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 102-345
  - path: src/acheron/shell/orchestrator.py
    lines: 58-99
  - path: src/acheron/shell/orchestrator.py
    lines: 124-166
related: []
```

**Issue.** `Orchestrator` (orchestrator.py:102-345) mixes at least four distinct responsibilities: (1) job lifecycle — `submit_job`/`_execute`/`get_job`/`list_jobs` (206-290); (2) worker registration — `register_worker`/`list_workers` (319-345); (3) capability aggregation — `get_capabilities` plus the module-level `_collect_worker_caps`, `_pair_is_achievable`, and `LanguagePair` dataclass (58-99, 292-317); (4) infrastructure bootstrap — `_register_built_in_local_workers` (142-166), `_verify_data_dir_writable` (124-140), and lifecycle `start`/`shutdown`/`close` (168-204). The capability-aggregation logic is self-contained (it only reads `registry.list_all()` and computes language pairs) yet lives on the orchestrator and is exposed via `Orchestrator.get_capabilities`, coupling the API's `/capabilities` route to the orchestrator instance rather than to a dedicated aggregator. `_all_languages_caps` (45-55) and `_BUILT_IN_LOCAL_HANDLERS` (38-42) are module-level configuration that would more naturally live alongside `local_handlers.py`. The class also reaches into `cache.data_dir` to construct a second `StepCache` internally (115), coupling it to `PlanCache`'s internals.

**Why it matters.** A multi-responsibility service class is harder to test in isolation (any test for capability aggregation drags in job submission, the data-dir probe, and built-in worker registration) and accretes further as each concern grows. The capability route is coupled to the orchestrator rather than to the registry it actually queries. Medium because it works today, but the coupling raises the cost of every future change to any of the four concerns.

**Recommendation.** Extract a `CapabilityAggregator` (taking a `WorkerStore`) owning `_collect_worker_caps`, `_pair_is_achievable`, `LanguagePair`, and `get_capabilities`; have the orchestrator delegate or have the API route depend on the aggregator directly. Move `_all_languages_caps` and `_BUILT_IN_LOCAL_HANDLERS` next to `local_handlers.py`. Inject `StepCache` explicitly into `Orchestrator` rather than deriving it from `PlanCache.data_dir`. Keep `Orchestrator` focused on job lifecycle + lifecycle wiring.

**Verification.** `just test` after extraction; `just lint-imports` to confirm the new boundary; confirm the `/capabilities` route still resolves and that capability-aggregation tests no longer need to construct a full Orchestrator with a writable data dir.

### ARCH-004 — metadata typed dict[str, object] on RegisteredWorker/WorkerStore ABC vs dict[str, JsonValue] on WorkerCapabilities

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: ["fb69e0e"]
files:
  - path: src/acheron/shell/registry.py
    lines: 27
  - path: src/acheron/shell/stores/base.py
    lines: 30
  - path: src/acheron/core/models.py
    lines: 58
  - path: src/acheron/shell/stores/redis.py
    lines: 85
related: [TYPE-001]
```

**Issue.** `RegisteredWorker.metadata` is typed `dict[str, object]` (registry.py:27) and `WorkerStore.register(... metadata: dict[str, object] | None)` (base.py:30), while `WorkerCapabilities.metadata` is typed `dict[str, JsonValue]` (models.py:58). The Redis backend serializes worker metadata with `json.dumps(metadata, sort_keys=True)` (redis.py:85) and deserializes it with `json.loads` (redis.py:100) — both assume JSON-serializable values. The `dict[str, object]` annotation does not enforce that; a caller passing `{"handler": some_callable}` or `{"count": object()}` would pass the type checker and the ABC contract, then crash at Redis serialization time. The orchestrator goes out of its way to document that "metadata holds JSON-serializable values only" (orchestrator.py:148, registry.py:18) because the type does not say so. `InMemoryWorkerStore` accepts the same loose type (memory.py:28).

**Why it matters.** The gap between the declared type (`object`) and the real contract (JSON-serializable) is a runtime-error-via-types hazard that AGENTS.md calls out as "make illegal states unrepresentable" and "avoid Any and don't let Mapping[str, Any] become a documentation-via-runtime-error contract." The docstring compensation is exactly the documentation-via-runtime-error pattern. Medium because it fails only at the Redis backend, not the memory backend, so it surfaces as a backend-specific crash rather than a type error.

**Recommendation.** Align both `RegisteredWorker.metadata` and `WorkerStore.register`'s `metadata` parameter to `dict[str, JsonValue]` (reusing the alias from `core/models.py`), so the type system enforces JSON-serializability across all backends. Drop the compensating docstrings once the type carries the contract. If non-serializable side data is genuinely needed (e.g. local handlers), keep it in the orchestrator's side dict as already done for `_local_handlers` — do not relax the store metadata type.

**Verification.** `just type-check` and `just type-check-pyright` to confirm the stricter type propagates; `just test`; grep to confirm no caller passes a non-JsonValue metadata dict.

### ARCH-005 — _BUILT_IN_LOCAL_HANDLERS is a module-private name imported cross-module from local_handlers.py

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
  - path: src/acheron/shell/local_handlers.py
    lines: 90
  - path: src/acheron/shell/orchestrator.py
    lines: 19-20
related: []
```

**Issue.** The ARCH-003 extraction moved `_BUILT_IN_LOCAL_HANDLERS` from `orchestrator.py` into `shell/local_handlers.py` (local_handlers.py:89-93), but the orchestrator still imports this name across a module boundary (orchestrator.py:20: `from acheron.shell.local_handlers import _BUILT_IN_LOCAL_HANDLERS, ...`). Leading-underscore names are the Python convention for module-private symbols; importing them from another module breaks the encapsulation contract and signals an unfinished refactor.

**Why it matters.** Cross-module private imports make refactoring `local_handlers.py` (renaming, splitting, relocating) silently break the orchestrator's import. Today the only consumer is the orchestrator, but the import is a latent maintenance hazard that the original ARCH-003 cleanup did not resolve, and ruff's `PLC2701` (import-private-name) would flag it if the project enabled that rule.

**Recommendation.** Pick one: (1) rename `_BUILT_IN_LOCAL_HANDLERS` to `BUILT_IN_LOCAL_HANDLERS` in local_handlers.py and drop the leading underscore at the import site in orchestrator.py:20; (2) move the registration loop out of `Orchestrator._register_built_in_local_workers` and into local_handlers.py as `async def register_built_in_local_workers(registry, local_handlers: dict[str, LocalJobHandler]) -> None`, then have the orchestrator call it. Option 2 is the cleaner separation of concerns.

**Verification.** just test; just lint-strict; grep -rn '^from acheron.shell.local_handlers import _' src/ to confirm no leading-underscore imports remain.

### ARCH-006 — Orchestrator.__init__ still derives StepCache from PlanCache.data_dir as the default

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
    lines: 47-52
  - path: src/acheron/shell/orchestrator.py
    lines: 123-128
related: []
```

**Issue.** ARCH-003 explicitly recommended 'Inject StepCache explicitly into Orchestrator rather than deriving it from PlanCache.data_dir.' The diff added a `step_cache: StepCache | None = None` keyword (orchestrator.py:47) and stores the injected value, but the default path still constructs `StepCache(cache.data_dir)` (orchestrator.py:51) and immediately calls `self._verify_data_dir_writable()` (orchestrator.py:52) — so any caller that does not inject a StepCache still couples Orchestrator construction to a writable PlanCache data dir.

**Why it matters.** The default path defeats the purpose of the injection seam. Tests still need a `tmp_path` (or equivalent) to construct an Orchestrator, and any future code path that wants the CapabilityAggregator in isolation must drag a PlanCache + writable data dir along. Low because the seam exists for callers who want it, but the default is the wrong default.

**Recommendation.** Either (a) make `step_cache` a required keyword argument (no default) and remove the `cache.data_dir` derivation entirely — callers must always pass a StepCache; (b) drop the `cache: PlanCache` parameter from Orchestrator and pass `StepCache` directly to the call sites that need it; or (c) move `self._verify_data_dir_writable()` out of `__init__` into `start()` so the construction probe is not a barrier to instantiation. (a) is the smallest change that actually delivers ARCH-003's stated intent.

**Verification.** just test; assert that Orchestrator(...) without a `step_cache` argument raises a clear TypeError; assert that constructing an Orchestrator with a writable `tmp_path` PlanCache still works; just lint-strict.

### ARCH-007 — StreamingExecutor._stage has 7 parameters and uses shared mutable list[float | None] as cost side-channel

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

**Issue.** `_stage` (streaming.py:180-237) grew from 4 parameters (plus `self`) to 7 with the addition of `stage_index: int` and `stage_costs: list[float | None]` for the CORR-008 fix. The `stage_costs` list is allocated in `_run_pipeline` (line 77) and shared across all concurrent `_stage` tasks — each task writes `stage_costs[stage_index] = result.metrics.cost_estimate or 0.0` (line 224) before any status check, so the cost survives a TaskGroup cancellation. `_run_pipeline` then discards `task.result()` return values (lines 94-95) and sums costs from the shared list (lines 97-98). The parameter count required `# noqa: PLR0913` (line 180), and the mutable-list side-channel is a pragmatic but brittle alternative to a structured type.

**Why it matters.** The side-channel mutation couples `_run_pipeline` and `_stage` through a shared mutable structure — a `StageOutcome` dataclass (cost + error) or `add_done_callback` would make the data flow explicit rather than implicit. The `# noqa: PLR0913` suppression signals the parameter count is one past the design limit. Low because the implementation is correct and tested, but it is a maintainability concern.

**Recommendation.** Wrap `_stage`'s cost information in a structured type (e.g. `@dataclass class StageOutcome: cost: float | None; error: AcheronError | None`) or use `asyncio.Task.add_done_callback` to extract the cost even on failure. Either would let `_stage` shrink back to 4-5 parameters and drop the `noqa: PLR0913`.

**Verification.** `just test`; `just type-check`; grep for `# noqa: PLR0913` in streaming.py to confirm the suppression is gone.

### ARCH-008 — Orchestrator.__init__ still derives default StepCache from PlanCache.data_dir

```yaml
status: open
severity: low
effort: S
reviewed_at: be7b3ab
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 53-82
  - path: src/acheron/shell/api/app.py
    lines: 46-49
related: [ARCH-006, CFG-004]
```

**Issue.** The ARCH-006 fix moved the writable-data-dir probe into `start()`, but the constructor path still builds `StepCache(self._settings.orchestrator.data_dir)` (orchestrator.py:68) and, when no `settings` is injected, mutates `self._settings.orchestrator.data_dir = cache.data_dir` (orchestrator.py:65) to align the two. The `cache` parameter is still required, and the in-place mutation of the settings object conflates the YAML/algorithm-input shape with the runtime cache. `create_app` (app.py:46-49) performs the same mutation: `if data_dir is not None: settings.orchestrator.data_dir = Path(data_dir)`.

**Why it matters.** Settings is the only place the YAML/env contract is encoded, and mutating it post-load means downstream code (the `verify_registration_token` dep, the auto-token logic in `start()`, the health provider wiring) sees a value that no longer matches the user's config. Two call sites own the same field with overlapping logic; the precedence between `settings.data_dir`, the constructed-`cache` default, and an explicit `data_dir=` arg in `create_app` is implicit.

**Recommendation.** Make the effective `data_dir` a single value computed at construction. In `create_app`, compute `effective_data_dir = Path(data_dir) if data_dir is not None else settings.orchestrator.data_dir`, then build `PlanCache(effective_data_dir)` and pass `data_dir=effective_data_dir` into the `Settings(...)` factory. In `Orchestrator.__init__`, drop the `if settings is None: self._settings.orchestrator.data_dir = cache.data_dir` mutation; instead require `settings` to be passed and trust it, or compute a default `Settings` with the right `data_dir` before assignment.

**Verification.** `just test`; assert that `Orchestrator(registry, PlanCache('/foo'), settings=Settings(orchestrator=OrchestratorSettings(data_dir=Path('/bar'))))` does not mutate `settings` (i.e. `settings.orchestrator.data_dir == Path('/bar')` after construction). `grep -n 'settings.orchestrator.data_dir =' src/ | grep -v 'load_settings'` returns no assignment sites.

### ARCH-009 — HealthProvider ABC lives in shell/health_providers.py instead of core/interfaces.py

```yaml
status: open
severity: medium
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/shell/health_providers.py
    lines: 16-22
  - path: src/acheron/core/interfaces.py
    lines: 14-39
related: []
```

**Issue.** The new `HealthProvider` ABC (health_providers.py:16-22) is the project's third domain interface alongside `Worker` and `Executor` (core/interfaces.py:14-39). The project's convention (per the orientation brief) is "interfaces live in `core/interfaces.py`; consumers depend on them directly." The concrete impls (`RunPodHealthProvider`, `HuggingFaceHealthProvider`) belong in `shell/`, but the ABC itself describes a stable domain contract (query a platform API to map an endpoint id to a `WorkerStatus`) and should sit alongside the other domain interfaces.

**Why it matters.** Keeping the ABC in `shell/` couples every consumer (tests, future providers, potential non-shell callers) to a shell concern (httpx imports) and locks the file behind a non-interface module. Adding a third provider will compound the import churn. The second deviation from the convention would make the rule unenforceable.

**Recommendation.** Move `class HealthProvider(ABC)` and its `check_status` abstractmethod from `shell/health_providers.py:16-22` to `core/interfaces.py` alongside `Worker` and `Executor`. Keep `RunPodHealthProvider`, `HuggingFaceHealthProvider`, `HealthProviders`, and `create_health_providers` in `shell/health_providers.py`. Update the `TYPE_CHECKING` import in `shell/health.py:21` to point at the relocated ABC. Do not move `WorkerStatus` (a core enum, already in `core/models.py`).

**Verification.** `git grep -n 'class HealthProvider' src/` returns exactly one match in `src/acheron/core/interfaces.py`. `just lint-strict` and `just type-check` pass; `just test` runs the health provider tests against the relocated ABC.

### ARCH-010 — HealthProviders container is a no-behavior wrapper over `dict[str, HealthProvider]`

```yaml
status: open
severity: low
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/shell/health_providers.py
    lines: 97-114
  - path: src/acheron/shell/health.py
    lines: 85-137
  - path: src/acheron/shell/orchestrator.py
    lines: 77-82
related: []
```

**Issue.** `HealthProviders` (health_providers.py:97-105) has a single `get(name) -> HealthProvider | None` method that returns `self._providers.get(name)`. There is no extra behavior, no validation, no event hooks, and no async surface. The class exists only to give the dependency an explicit name. The `HealthMonitor` field is typed `providers: HealthProviders | None` instead of `providers: Mapping[str, HealthProvider] | None`, which weakens the test seam — a `dict` literal works fine, but the type forces a wrapper allocation.

**Why it matters.** Per AGENTS.md, abstractions without behavior are the same shape as config knobs without control: YAGNI overhead that the greenfield project explicitly avoids. Future contributors will look for hidden behavior in `HealthProviders` and find none, which raises the cost of every later change.

**Recommendation.** Delete the `HealthProviders` class and have `create_health_providers` return `dict[str, HealthProvider]` directly. Change `HealthMonitor.__init__` to take `providers: Mapping[str, HealthProvider] | None` and look up via `self._providers.get(provider_name)`. Update the orchestrator call site (orchestrator.py:77-82) to pass the dict literal.

**Verification.** `git grep -n 'class HealthProviders' src/` returns no matches. `just lint-strict`, `just type-check`, `just test` pass. The provider tests still construct a `Mapping[str, HealthProvider]` literal for injection.

### ARCH-011 — `worker_sdk/__init__.py` docstring falsely claims the module is GPU-SDK-free at import time

```yaml
status: open
severity: medium
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/worker_sdk/__init__.py
    lines: 1-12
  - path: src/acheron/worker_sdk/cloud.py
    lines: 24
  - path: src/acheron/worker_sdk/_runpod_client.py
    lines: 21
related: [CORR-016]
```

**Issue.** The module docstring states "intentionally GPU-SDK free at import time: importing acheron.worker_sdk does not transitively load runpod (that import lives in `_runpod_client`, which is not part of the public re-exports)." The statement is false. The public re-exports at `__init__.py:10-12` include `from acheron.worker_sdk.cloud import RunPodForwarderHandler, make_runpod_handler` and `cloud.py:24` does `from acheron.worker_sdk._runpod_client import RunPodClient, RunPodJobResult` and `_runpod_client.py:21` does `import runpod` at module top. Any test or downstream consumer that imports `acheron.worker_sdk` (e.g. to use `WorkerHandler`, `Artifact`, `WorkerSettings`, `create_worker_app`) transitively loads the runpod SDK — the dependency the docstring claims is opt-in.

**Why it matters.** Per AGENTS.md, silent/unexpected behavior is worse than no control at all. A contributor who reads the docstring and writes `import acheron.worker_sdk` in a CPU-only test fixture (e.g. an executor unit test that uses a WorkerHandler fake) will get a hard dep on the runpod SDK, and CI will fail in environments where runpod is not installed. The architectural claim is verifiable against the public API surface and the docstring lies about it.

**Recommendation.** Make the public re-exports in `__init__.py` GPU-SDK-free: replace the unconditional `from acheron.worker_sdk.cloud import ...` with a lazy `__getattr__` (PEP 562) that imports cloud only on first attribute access. Alternatively, drop the two `cloud.*` symbols from `__all__` and require callers to do `from acheron.worker_sdk.cloud import RunPodForwarderHandler` explicitly. Either way, fix the docstring so the import-time claim matches the public surface.

**Verification.** `python -c 'import acheron.worker_sdk'` followed by `python -c 'import sys; assert "runpod" not in sys.modules; import acheron.worker_sdk'`. A test asserting `import acheron.worker_sdk; import sys; assert "runpod" not in sys.modules` (with the test using only `WorkerHandler`/`Artifact`/`WorkerSettings`/`create_worker_app` from the SDK) reproduces the current bug. just test + just lint-strict.

### ARCH-012 — `create_worker_app` cherry-picks routes from `EdgeApp.app.routes` via a hardcoded `inner_paths` set

```yaml
status: open
severity: medium
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/worker_sdk/app.py
    lines: 87-144
  - path: src/acheron/worker_sdk/_edge_http.py
    lines: 119-155
related: [CORR-015, MAINT-011]
```

**Issue.** `create_worker_app` (app.py:87-144) constructs an `EdgeApp` (line 101) to get a route source, then builds a *separate* `FastAPI` app (line 135) with its own lifespan, and copies the inner app's routes into the outer app via a hardcoded set: `inner_paths = {"/health", "/capabilities", "/execute"}` (line 139) plus a path-attribute loop (lines 140-143). The inner `EdgeApp.app` instance is otherwise discarded — the docstring on line 137-138 explicitly says "the inner `EdgeApp` is built only as a route source — its lifespan is dead code that this outer `lifespan` supersedes." The construction-then-cherry-pick pattern is structural: if `EdgeApp` adds a route (e.g. `/metrics`, `/readyz`), `create_worker_app` silently drops it; if `EdgeApp` renames a route, the hardcoded set carries a dead entry.

**Why it matters.** The pattern is brittle: two FastAPI app instances with one lifespan, and a string-typed route filter that the type system cannot keep in sync with the inner app's route table. A future maintainer adding a route to `EdgeApp` will not realise the `create_worker_app` surface is narrower, and a route-rename will silently fail at request time. The duplicated docstring on lines 93-97 and 98 (`"""Build the edge FastAPI app wired with registration + price refresh."""` twice) is a tell that the body has accreted without consolidation.

**Recommendation.** Refactor `create_worker_app` to delegate route construction to a single `EdgeApp` (or equivalent) and have it expose the price-refresh + registration wiring as hooks/middleware. The simplest path: add a `lifespan=` parameter or a `register_health_check=` hook to `EdgeApp`, build the lifespan inline in `EdgeApp.__init__` accepting a registration hook, and have `create_worker_app` return `EdgeApp.app` directly. Drop the `inner_paths` filter and the dead lifespan in `EdgeApp`.

**Verification.** just test (the existing tests for /health, /capabilities, /execute on the edge app continue to pass); add a test that registers a new `EdgeApp` route (e.g. a stub `/metrics`) and asserts `create_worker_app` exposes it; just lint-strict; just type-check. The duplicate docstring on app.py:98 should also be removed as part of the refactor.

### ARCH-013 — `transports/grpc.py` and `transports/http.py` both duplicate the `data_dir` env-var fallback to `ACHERON_DATA_DIR`

```yaml
status: open
severity: low
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/shell/transports/grpc.py
    lines: 42-53
  - path: src/acheron/shell/transports/http.py
    lines: 44-55
related: [CFG-006]
```

**Issue.** Both transports now accept a `data_dir: Path | str | None = None` constructor arg and, when None, default it from `os.environ.get('ACHERON_DATA_DIR', '/data/jobs')` (grpc.py:52, http.py:54). The new `src/acheron/shell/transports/_multipart.py` correctly extracted the shared artifact-materialization logic, but the `data_dir` env-var fallback is duplicated verbatim. The hardcoded default path `/data/jobs` and the env-var name now live in two files in lockstep — adding a new transport (e.g. an S3-backed worker) would require a third copy of the same three lines.

**Why it matters.** Same DRY hazard as the original CFG-001 finding for `create_worker_store` / `create_job_store`: two functions must stay in sync. A contributor who adds a new default path or renames the env var must update both transports. The transports are also the wrong layer to read env vars — the orchestrator owns the data_dir configuration, and the transports should accept it as an explicit dependency from the orchestrator's Settings. AGENTS.md's "strict domain separation" rule argues for moving the env-var read to the settings loader (see CFG-006) and making `data_dir` a required transport constructor arg.

**Recommendation.** Either (a) make `data_dir` a required positional arg on both transports and have the orchestrator pass `settings.orchestrator.data_dir`; or (b) move the env-var fallback to a single helper (e.g. `transports._paths.default_data_dir() -> Path`) and call it from both. Option (a) is the smaller change and the cleaner separation.

**Verification.** just test; grep -n 'ACHERON_DATA_DIR' src/acheron/shell/transports/ to confirm only one site reads the env var; just lint-strict; just type-check.

## CFG — Configuration

**Grade:** B

CFG-001, CFG-002 remain verified. CFG-003, CFG-004, CFG-005 remain open and re-resolved at slightly shifted line numbers. Two new CFG findings: CFG-006 (medium) — five more env-var reads outside the settings loaders in the new transport and worker_sdk code, on top of the four sites already flagged; CFG-007 (medium) — `WorkerSettings.model_id` and `WorkerSettings.output_mode` are config knobs that don't actually control anything.

### CFG-001 — ACHERON_STORE_BACKEND / REDIS_URL selection logic duplicated across create_worker_store and create_job_store

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in:
  - f5ce538072b568c9ef47ce53f2ef5e2f3d262ceb
files:
  - path: src/acheron/shell/stores/__init__.py
    lines: 39-53
  - path: src/acheron/shell/stores/__init__.py
    lines: 39-53
related: [ARCH-002]
```

**Issue.** `create_worker_store()` (17-37) and `create_job_store()` (39-53) each independently read `ACHERON_STORE_BACKEND` and `REDIS_URL` and run an identical `match backend: case 'memory': ... case 'redis': ...` ladder. The only difference is which concrete class is instantiated. Adding a new backend (e.g. sqlite) requires editing both functions in lockstep, and the two reads of `ACHERON_STORE_BACKEND` can diverge if the env var is mutated between the two calls (an unlikely but representable race). The `REDIS_URL` default `redis://localhost:6379` is also duplicated on lines 33 and 50.

**Why it matters.** Two functions that must stay in sync is a classic DRY/maintainability hazard; a contributor adding a backend who updates only one function silently produces a split-brain configuration. Medium because the blast radius of a missed update is a silent state-location mismatch.

**Recommendation.** Unify into a single `create_stores() -> tuple[WorkerStore, JobStore]` (or a `_select_backend()` helper returning both classes) that reads `ACHERON_STORE_BACKEND` and `REDIS_URL` once and constructs both stores from the same selection. Keep the individual factories as thin wrappers if external callers need them, but route them through the unified selector.

**Verification.** `just test` (existing `test_worker_integration.py:163` covers the redis/memory switch); add a test asserting both stores share the same backend for a given `ACHERON_STORE_BACKEND`; `just lint-strict`.

### CFG-002 — Duplicated knowledge: TLS CA env-read logic and hardcoded language set repeated across modules

```yaml
status: verified
severity: low
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in:
  - be7b3ab
files:
  - path: src/acheron/shell/tls.py
    lines: 74
  - path: src/acheron/cli.py
    lines: 46
  - path: src/acheron/core/models.py
    lines: 20
  - path: src/acheron/shell/local_handlers.py
    lines: 24-25
  - path: src/acheron/shell/transports/grpc.py
    lines: 39-40
related: [SEC-003]
```

**Issue.** Two small but distinct duplications recur: (1) The `ACHERON_TLS_CA_FILE or SSL_CERT_FILE` trust-store resolution is implemented in `tls.py:74` (`grpc_channel_credentials`) and re-implemented verbatim in `cli.py:46` (`_resolve_trust_store`). Both read the same two env vars with the same precedence; the addition of `_allow_insecure()` and WARNING logs did not resolve the duplication — both sites independently resolve the CA. (2) The hardcoded language set `{"en", "es", "fr", "de"}` moved from `orchestrator.py` to `local_handlers.py:24-25` during the ARCH-003 extraction, but is still duplicated with `GrpcWorker.capabilities` (grpc.py:39-40).

**Why it matters.** Each duplication is small, but together they are the kind of recurring duplicated knowledge that drifts silently: a contributor adds a language to one site and ships a worker that advertises a different language set than the orchestrator's built-ins. Low because the duplication is localized and easy to spot, but it is a latent drift hazard.

**Recommendation.** Extract the trust-store resolution into a single helper (e.g. `tls.resolve_ca_path() -> str | None`) and call it from both `tls.py` and `cli.py`. Extract the supported-languages set into a shared constant in `core/models.py` (or a `core/constants.py`) and import it in both `local_handlers.py` and `grpc.py`. Prefer making the gRPC worker's languages configurable via `WorkerCapabilities` rather than hardcoded.

**Verification.** `just test`; `just lint-strict`; grep to confirm only one site defines the language set and one site defines the CA resolution order.

### CFG-003 — `ACHERON_OPEN_REGISTRATION` read directly in deps.py, bypassing the new settings loader

```yaml
status: open
severity: medium
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/shell/api/deps.py
    lines: 33
  - path: src/acheron/shell/config.py
    lines: 111-130
related: [CFG-006]
```

**Issue.** The new `Settings`/`load_settings` system (config.py:111-130) introduces pydantic-settings with `env_prefix='ACHERON_'` and `env_nested_delimiter='__'`, plus a custom `_EnvAliasSettingsSource` that maps `ACHERON_DATA_DIR` and `ACHERON_REGISTRATION_TOKEN` onto their nested keys. `ACHERON_OPEN_REGISTRATION` (a new env var documented in README.md and acheron.yaml.example) is read directly in `verify_registration_token` via `os.environ.get('ACHERON_OPEN_REGISTRATION') == '1'` (deps.py:33) — outside the loader. Other env-var reads (`ACHERON_URL`, `ACHERON_TLS_*`, `ACHERON_STORE_BACKEND`, `REDIS_URL`) are also outside the loader, but they predate this diff; `ACHERON_OPEN_REGISTRATION` is new and joins the sprawl pattern that the same diff is trying to fix for other vars.

**Why it matters.** The deps function is the only place in the codebase that knows about `ACHERON_OPEN_REGISTRATION`. A future YAML or env-var rename, a test that wants to toggle the flag, or a new route that should also respect open-registration will reach for `os.environ.get` again, perpetuating the sprawl. The same flag cannot be set via `acheron.yaml` (no `OrchestratorSettings.open_registration` field exists), so users who migrate to file-based config lose the toggle.

**Recommendation.** Add `open_registration: bool = False` to `OrchestratorSettings` in config.py and extend the settings source to read `ACHERON_OPEN_REGISTRATION` into that field. Change deps.py:33 to read `orch.settings.orchestrator.open_registration` and drop the `os.environ.get` call. Document the field in acheron.yaml.example under `orchestrator:` (next to `registration_token`).

**Verification.** `just type-check`; a unit test asserting that `Settings(ACHERON_OPEN_REGISTRATION='1').orchestrator.open_registration is True` and the default is `False`; `grep -rn 'ACHERON_OPEN_REGISTRATION' src/` returns only the loader site, the example YAML, and the README.

### CFG-004 — Orchestrator mutates `Settings.orchestrator.data_dir` in-place from two call sites

```yaml
status: open
severity: medium
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 63-68
  - path: src/acheron/shell/api/app.py
    lines: 46-49
related: [ARCH-008, CFG-006]
```

**Issue.** AGENTS.md requires a strict YAML-vs-core dataclass split: YAML shapes are user-authored, core dataclasses are algorithm inputs. The new `Settings` model (config.py) is the YAML/algorithm-input shape. `Orchestrator.__init__` (orchestrator.py:63-68) does:

```
self._settings = settings or load_settings()
if settings is None:
    self._settings.orchestrator.data_dir = cache.data_dir
```

`create_app` (app.py:46-49) performs the same mutation: `if data_dir is not None: settings.orchestrator.data_dir = Path(data_dir)`. Two call sites own the same field with overlapping logic; the precedence between `settings.data_dir`, the constructed-`cache` default, and an explicit `data_dir=` arg in `create_app` is implicit.

**Why it matters.** Settings is the only place the YAML/env contract is encoded, and mutating it post-load means downstream code (the `verify_registration_token` dep, the auto-token logic in `start()`, the health provider wiring) sees a value that no longer matches the user's config. If `create_app` later reads `settings.orchestrator.data_dir` to log the configured path, the log will lie about where state actually goes. The pattern is a latent split-brain: `app.py` and `Orchestrator.__init__` both know how to derive the effective `data_dir` and both reach into the same field.

**Recommendation.** Make the effective `data_dir` a single value computed at construction. Compute `effective_data_dir = Path(data_dir) if data_dir is not None else settings.orchestrator.data_dir`, build `PlanCache(effective_data_dir)`, pass `data_dir=effective_data_dir` into a fresh `Settings(...)`, and pass both into `Orchestrator`. In `Orchestrator.__init__`, drop the in-place mutation; trust the `settings` argument (or compute a default `Settings` with the right `data_dir` before assignment).

**Verification.** `just type-check`; a test that constructs `Orchestrator(registry, PlanCache('/foo'), settings=Settings(orchestrator=OrchestratorSettings(data_dir=Path('/bar'))))` asserts `orch.settings.orchestrator.data_dir == Path('/bar')` (no in-place rewrite). `grep -n 'settings.orchestrator.data_dir =' src/ | grep -v 'load_settings'` returns no assignment sites.

### CFG-005 — `${VAR}` env-var expansion silently substitutes unset env vars as empty strings, disabling providers

```yaml
status: open
severity: medium
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/shell/config.py
    lines: 15-26
  - path: acheron.yaml.example
    lines: 47-53
  - path: src/acheron/shell/health_providers.py
    lines: 108-114
related: [CORR-010, CORR-011, CFG-006]
```

**Issue.** `_expand_env_vars` (config.py:15-26) does `_ENV_VAR_PATTERN.sub(lambda m: os.environ.get(m.group(1), ''), value)`. An unset env var becomes the empty string. The example YAML uses this for `api_key: "${RUNPOD_API_KEY}"` and `api_key: "${HF_API_KEY}"` (acheron.yaml.example:47-52). `create_health_providers` (health_providers.py:110-114) checks `if settings.providers.<name>.api_key:`, so an empty string is falsy and the provider is silently dropped. AGENTS.md flags this pattern explicitly: "silent/unexpected behavior is worse than no control at all."

**Why it matters.** A user who follows the example YAML and forgets to set `RUNPOD_API_KEY` gets a working orchestrator that just never RunPod-checks anything. There is no `ValidationError`, no log entry, and no CLI message — the provider name 'runpod' never appears in startup logs. The auto-generated registration token path already demonstrates the project's preference for failing loud (raise on bad data dir, log warnings on token-file errors). The env-var expansion inverts that contract for secrets.

**Recommendation.** Make the expansion distinguish "referenced but missing" from "no reference". Either raise a `ConfigError` (or log a WARNING) at load time for each referenced-but-unset var, naming both the YAML key path and the missing env var; or keep the empty-string fallback but log a WARNING at load time for each miss. Either way, the `create_health_providers` factory should not silently filter on falsy `api_key`: change it to `if settings.providers.<name>.api_key is not None` and let the empty-string case blow up where it should.

**Verification.** Load a YAML with `api_key: "${RUNPOD_API_KEY}"` against an empty environment and assert a clear error (or WARNING log entry) naming `RUNPOD_API_KEY`. `grep -n 'os.environ.get(m.group' src/acheron/shell/config.py` shows the loader no longer silently defaults to `''`.

### CFG-006 — Env vars read outside the project's settings loaders — 5 new sites in transports and worker_sdk

```yaml
status: open
severity: medium
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/shell/transports/grpc.py
    lines: 52
  - path: src/acheron/shell/transports/http.py
    lines: 54
  - path: src/acheron/worker_sdk/_runpod_client.py
    lines: 45-51
  - path: src/acheron/worker_sdk/app.py
    lines: 57
  - path: src/acheron/worker_sdk/cli.py
    lines: 72
related: [CFG-003, CFG-004, CFG-005, ARCH-013]
```

**Issue.** Five env-var reads sit outside the project's settings loaders, perpetuating the sprawl pattern that CFG-003, CFG-004, and CFG-005 already flag. (1) `grpc.py:52` reads `ACHERON_DATA_DIR` (the same env var the new `Settings` loader maps to `orchestrator.data_dir` via `_EnvAliasSettingsSource`). (2) `http.py:54` reads the same env var. (3) `_runpod_client.py:45` reads `ACHERON_WORKER__RUNPOD_BASE_URL` — the `WorkerSettings` env prefix is `ACHERON_WORKER__` and `extra="forbid"` means the field is invisible to `WorkerSettings`, so the SDK reaches around its own settings system. (4) `app.py:57` reads `WORKER_HOST` outside any settings namespace. (5) `cli.py:72` reads `ACHERON_WORKER__LOG_LEVEL` outside `WorkerSettings` (`extra=forbid` again). The same antipattern recurs in both the orchestrator and the worker_sdk: env vars read in the deep implementation, with no single source of truth for what is configurable and how.

**Why it matters.** Each site individually looks like a one-line convenience, but the cumulative effect is that the `Settings` model in `config.py` and the `WorkerSettings` model in `worker_sdk/settings.py` are not the authoritative config schema — they are partial overlays over a sprawl of `os.environ.get` calls. A future operator who sets `ACHERON_DATA_DIR` from the loader will be surprised when the gRPC transport also reads it. AGENTS.md flags this: "silent/unexpected behavior is worse than no control at all." A test that toggles `ACHERON_DATA_DIR` for the orchestrator will not affect the transport's effective data dir (or vice versa).

**Recommendation.** Add the missing fields to the appropriate settings model: `orchestrator.data_dir` already exists — change the transports to require `data_dir` (see ARCH-013) and let the orchestrator pass it. Add `worker_sdk_base_url: str | None = None` (or similar) to `WorkerSettings` for the runpod test seam, and set `extra="ignore"` instead of `extra="forbid"` for fields that must remain env-only. Add `worker_host: str | None = None` and `log_level: str = "INFO"` to `WorkerSettings` and remove the direct env reads. Then a single `Settings(...)` / `WorkerSettings(...)` call yields the full configuration surface.

**Verification.** `just type-check` (the stricter types propagate); `just test`; `grep -n 'os.environ.get' src/acheron/` confirms only the settings loaders read env vars. A test asserting `Settings(ACHERON_DATA_DIR='/foo')` and `WorkerSettings(..., ACHERON_WORKER__LOG_LEVEL='DEBUG')` produce the expected `data_dir` and `log_level` from a single call site.

### CFG-007 — `WorkerSettings.model_id` and `WorkerSettings.output_mode` are config knobs that don't actually control anything

```yaml
status: open
severity: medium
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/worker_sdk/settings.py
    lines: 50-51, 62-63
  - path: workers/qwen3tts/worker.yaml
    lines: 35
  - path: workers/qwen3tts/worker.edge.yaml
    lines: 17
  - path: src/acheron/worker_sdk/cli.py
    lines: 30-75
  - path: workers/qwen3tts/handler.py
    lines: 52
related: []
```

**Issue.** Two new `WorkerSettings` fields are configured but never consumed. (1) `model_id: str | None = None` (settings.py:62) is set in both `workers/qwen3tts/worker.yaml:35` and `workers/qwen3tts/worker.edge.yaml:17`, but `grep -rn '\.model_id' src/ workers/` returns zero matches — no code path reads it. The qwen3tts handler hard-codes `_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"` (handler.py:52) and uses that constant directly in `capabilities()` and `startup()`. (2) `output_mode: Literal["multipart", "volume"] = "multipart"` (settings.py:50) is validated in the `_validate_composite` after-validator (settings.py:116-117) to require `output_volume_dir` when `output_mode == "volume"`, but no code consumes the field: `_edge_http.py` always emits `multipart/mixed` (lines 96-117), and `cli.py` does not branch on `output_mode` either. The edge-side `worker.edge.yaml:24-25` even says "Edge transport is always HTTP multipart; the edge never writes to a shared volume." AGENTS.md explicitly calls this out: "config knobs that don't actually control anything" and "silent/unexpected behavior is worse than no control at all."

**Why it matters.** An operator who reads `output_mode: volume` in `worker.yaml` and expects volume delivery will get multipart silently. An operator who edits `model_id` in `worker.yaml` to point at a different Qwen3-TTS revision will get the hard-coded model instead. Both knobs create a documentation-via-runtime-error contract: the YAML says one thing, the code does another. The `output_mode` validation does not even fire (the edge container is always multipart), so a misconfigured `worker.yaml` will not fail loud at boot.

**Recommendation.** Either (a) implement the controls: read `settings.model_id` in the qwen3tts handler instead of the hard-coded `_MODEL_ID`, and branch the `_edge_http.py` execute() path on `settings.output_mode` (multipart for the edge, volume for the volume stub); or (b) drop the fields from `WorkerSettings` and the YAML files until the corresponding behavior is implemented. Per AGENTS.md's greenfield rule, do not keep a named knob that silently behaves like another. Option (b) is the smaller change for now.

**Verification.** `just test`; `grep -rn 'model_id\|output_mode' src/ workers/` to confirm only one site defines each and one site consumes it; `just lint-strict`; `just type-check`.
