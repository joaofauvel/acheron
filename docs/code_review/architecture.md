---
branch: chore/code-review-update
initial_review_commit: 23c29e1
last_updated_commit: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_staleness_scan:
  commit: eb6849c85d83f2277eb450f18a11e63cae2defd1
  date: 2026-06-24
---

# Architecture

## ARCH — Architecture

**Grade:** B

Layer 8b widened `worker_sdk` (8b granite-speech worker) and the HTTP transport (ASR multipart fan-in). The hexagonal layering remains clean, but the new code surfaced three new ARCH findings: ARCH-014 (medium) — `HttpWorker.execute()` now branches on `WorkerType.ASR` to add a transport-specific audio pipeline, inverting the transport-neutral Worker boundary; ARCH-015 (medium) — `step_cache` is threaded through `default_worker_factory` even though only the HTTP branch consumes it, leaking an HTTP/ASR concern into the dispatch signature; ARCH-016 (low) — `workers/_shared` is a module file co-located with a same-name test directory and an out-of-workspace `pyproject.toml`, a latent package-vs-module footgun. ARCH-008, ARCH-009, ARCH-010, ARCH-011, ARCH-012, ARCH-013 re-resolved (lines shifted). All other stories remain verified at e544584.

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
  commit: 9b4adb6
  date: 2026-06-24
fixed_in: []
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 75-108
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
  commit: e54458416e9bfe890a473dd9d542978d205b40a1
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
  commit: 9b4adb6
  date: 2026-06-24
fixed_in: []
files:
  - path: src/acheron/shell/health_providers.py
    lines: 97-115
  - path: src/acheron/shell/health.py
    lines: 77-91
  - path: src/acheron/shell/orchestrator.py
    lines: 103-108
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
  commit: e54458416e9bfe890a473dd9d542978d205b40a1
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/worker_sdk/__init__.py
    lines: 1-12
  - path: src/acheron/worker_sdk/__init__.py
    lines: 12-14
  - path: src/acheron/worker_sdk/cloud.py
    lines: 22-27
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
  commit: e54458416e9bfe890a473dd9d542978d205b40a1
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/worker_sdk/app.py
    lines: 87-144
  - path: src/acheron/worker_sdk/_edge_http.py
    lines: 124-271
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
  commit: e54458416e9bfe890a473dd9d542978d205b40a1
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/shell/transports/grpc.py
    lines: 42-53
  - path: src/acheron/shell/transports/http.py
    lines: 51-65
related: [CFG-006]
```

**Issue.** Both transports now accept a `data_dir: Path | str | None = None` constructor arg and, when None, default it from `os.environ.get('ACHERON_DATA_DIR', '/data/jobs')` (grpc.py:52, http.py:54). The new `src/acheron/shell/transports/_multipart.py` correctly extracted the shared artifact-materialization logic, but the `data_dir` env-var fallback is duplicated verbatim. The hardcoded default path `/data/jobs` and the env-var name now live in two files in lockstep — adding a new transport (e.g. an S3-backed worker) would require a third copy of the same three lines.

**Why it matters.** Same DRY hazard as the original CFG-001 finding for `create_worker_store` / `create_job_store`: two functions must stay in sync. A contributor who adds a new default path or renames the env var must update both transports. The transports are also the wrong layer to read env vars — the orchestrator owns the data_dir configuration, and the transports should accept it as an explicit dependency from the orchestrator's Settings. AGENTS.md's "strict domain separation" rule argues for moving the env-var read to the settings loader (see CFG-006) and making `data_dir` a required transport constructor arg.

**Recommendation.** Either (a) make `data_dir` a required positional arg on both transports and have the orchestrator pass `settings.orchestrator.data_dir`; or (b) move the env-var fallback to a single helper (e.g. `transports._paths.default_data_dir() -> Path`) and call it from both. Option (a) is the smaller change and the cleaner separation.

**Verification.** just test; grep -n 'ACHERON_DATA_DIR' src/acheron/shell/transports/ to confirm only one site reads the env var; just lint-strict; just type-check.

## CFG — Configuration

**Grade:** B

CFG-001, CFG-002 remain verified. CFG-003, CFG-004, CFG-005, CFG-006, CFG-007 remain open and re-resolved. CFG-007 is now widened by the 8b worker: `WorkerSettings.model_id` is configured in FOUR YAML files (qwen3tts + granite-speech, each with a `worker.yaml` and `worker.edge.yaml`) and still has zero consumers, so the documented knob is now silent across a wider surface. New CFG-008 (medium) — tracks that regression.

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
  commit: e54458416e9bfe890a473dd9d542978d205b40a1
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/shell/api/deps.py
    lines: 22-49
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
  commit: 9b4adb6
  date: 2026-06-24
fixed_in: []
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 75-96
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
  commit: e54458416e9bfe890a473dd9d542978d205b40a1
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/shell/config.py
    lines: 15-26
  - path: acheron.yaml.example
    lines: 47-53
  - path: src/acheron/shell/health_providers.py
    lines: 108-115
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
  commit: e54458416e9bfe890a473dd9d542978d205b40a1
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/shell/transports/grpc.py
    lines: 52
  - path: src/acheron/shell/transports/http.py
    lines: 62
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
  commit: e54458416e9bfe890a473dd9d542978d205b40a1
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/worker_sdk/settings.py
    lines: 50-51
  - path: src/acheron/worker_sdk/settings.py
    lines: 62-63
  - path: workers/qwen3tts/worker.yaml
    lines: 37
  - path: workers/qwen3tts/worker.edge.yaml
    lines: 17
  - path: workers/granite_speech/worker.yaml
    lines: 30
  - path: workers/granite_speech/worker.edge.yaml
    lines: 13
  - path: workers/translategemma/worker.yaml
    lines: 30
  - path: workers/translategemma/worker.edge.yaml
    lines: 13
  - path: workers/qwen3tts/handler.py
    lines: 54-125
  - path: workers/granite_speech/handler.py
    lines: 30-121
  - path: workers/translategemma/handler.py
    lines: 125, 148, 205
related: [CFG-008, CFG-010]
```

**Issue.** Two new `WorkerSettings` fields are configured but never consumed. (1) `model_id: str | None = None` (settings.py:62) is set in both `workers/qwen3tts/worker.yaml:35` and `workers/qwen3tts/worker.edge.yaml:17`, but `grep -rn '\.model_id' src/ workers/` returns zero matches — no code path reads it. The qwen3tts handler hard-codes `_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"` (handler.py:52) and uses that constant directly in `capabilities()` and `startup()`. (2) `output_mode: Literal["multipart", "volume"] = "multipart"` (settings.py:50) is validated in the `_validate_composite` after-validator (settings.py:116-117) to require `output_volume_dir` when `output_mode == "volume"`, but no code consumes the field: `_edge_http.py` always emits `multipart/mixed` (lines 96-117), and `cli.py` does not branch on `output_mode` either. The edge-side `worker.edge.yaml:24-25` even says "Edge transport is always HTTP multipart; the edge never writes to a shared volume." AGENTS.md explicitly calls this out: "config knobs that don't actually control anything" and "silent/unexpected behavior is worse than no control at all."

**Why it matters.** An operator who reads `output_mode: volume` in `worker.yaml` and expects volume delivery will get multipart silently. An operator who edits `model_id` in `worker.yaml` to point at a different Qwen3-TTS revision will get the hard-coded model instead. Both knobs create a documentation-via-runtime-error contract: the YAML says one thing, the code does another. The `output_mode` validation does not even fire (the edge container is always multipart), so a misconfigured `worker.yaml` will not fail loud at boot.

**Recommendation.** Either (a) implement the controls: read `settings.model_id` in the qwen3tts handler instead of the hard-coded `_MODEL_ID`, and branch the `_edge_http.py` execute() path on `settings.output_mode` (multipart for the edge, volume for the volume stub); or (b) drop the fields from `WorkerSettings` and the YAML files until the corresponding behavior is implemented. Per AGENTS.md's greenfield rule, do not keep a named knob that silently behaves like another. Option (b) is the smaller change for now.

**Verification.** `just test`; `grep -rn 'model_id\|output_mode' src/ workers/` to confirm only one site defines each and one site consumes it; `just lint-strict`; `just type-check`.

### ARCH-014 — `HttpWorker.execute()` branches on `WorkerType.ASR` to add a transport-specific audio pipeline

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
  - path: src/acheron/shell/transports/http.py
    lines: 90-157
  - path: src/acheron/shell/transports/http.py
    lines: 114-157
related: [ARCH-013, ARCH-020]
```

**Issue.** `HttpWorker.execute()` (http.py:90-112) is now a `match job.job_type` dispatching ASR, TRANSLATION, TTS, and `_` to a parametric helper `_execute_with_upstream_input(job, *, upstream_step, content_type_predicate, form_field)` (http.py:114-157). The helper reads the upstream step's output from `self._step_cache`, builds a `multipart/form-data` body with a JSON envelope + binary payload, posts it, and parses a `multipart/mixed` response. The Worker interface is supposed to be transport-neutral; the transport is now coupled to a specific job type's semantics (the literal `WorkerType.ASR/TRANSLATION/TTS` discriminants in the `match` arms, the magic step_id literals `"extract"` / `"chunk"` at http.py:97 and http.py:103, the audio content-type filter, the form field name `"audio"` / `"chunks"`). The 8b single-AWorkerType branch widened to a triple-worker-type match with a leaky triple-magic-string helper (see ARCH-020).

**Why it matters.** The `Worker` ABC is the project's transport-neutral boundary. Putting `WorkerType.ASR` knowledge inside the transport implementation inverts the dependency that AGENTS.md's "strict domain separation" rule calls out. Adding a new audio-in step type requires editing HttpWorker; adding a new wire format for ASR requires editing HttpWorker.

**Recommendation.** Extract an `AsrHttpWorker(Worker)` (or an `AsrTransport` mixin) that owns the audio-fan-in + multipart upload. Have `HttpWorker.execute()` dispatch on the worker's registered `worker_type` (a constructor arg) or return `WorkerError` for ASR. Alternatively, encode the input requirement on the `Job` (`Job.input: Input | None`) and have a single transport class branch on `job.input is not None` (data-driven) rather than on the enum.

**Verification.** `git grep -n 'WorkerType\.ASR' src/acheron/shell/transports/` returns no matches outside tests; `just test`; `just lint-strict`.

### ARCH-015 — `step_cache` is threaded through `default_worker_factory` even though only the HTTP branch consumes it

```yaml
status: open
severity: medium
effort: S
reviewed_at: e54458416e9bfe890a473dd9d542978d205b40a1
last_verified_at:
  commit: 9b4adb6
  date: 2026-06-24
fixed_in: []
files:
  - path: src/acheron/shell/step_handler.py
    lines: 29-64
  - path: src/acheron/shell/step_handler.py
    lines: 80-101
  - path: src/acheron/shell/orchestrator.py
    lines: 85-95
  - path: src/acheron/shell/transports/http.py
    lines: 51-64
related: [ARCH-014]
```

**Issue.** `create_step_handler` (step_handler.py:80) and `default_worker_factory` (step_handler.py:29) now take a `step_cache: StepCache | None = None` keyword. `Orchestrator.__init__` constructs the cache and passes it in. The cache flows through the factory's `case "grpc"` and `case "local"` branches untouched and is only consumed at `case _: return HttpWorker(registered.endpoint, step_cache=step_cache)` (step_handler.py:64).

**Why it matters.** The factory signature is a leaky abstraction: every transport is told that the orchestrator wants the ASR step's audio file accessible, but only one consumer (HttpWorker) uses it. Adding a new transport that does not need `step_cache` forces the author to add a useless kwarg. AGENTS.md's "make illegal states unrepresentable" implies the parameter should be on the consumer, not the dispatcher.

**Recommendation.** Move `step_cache` construction into the consumer. Let `HttpWorker.__init__` continue to accept an optional `step_cache` but have the factory pass `None` for non-HTTP transports. The `default_worker_factory` and `create_step_handler` signatures then drop the parameter.

**Verification.** `git grep -n 'step_cache' src/acheron/shell/step_handler.py` returns no matches; `just test`; `just lint-strict`; `just type-check`.

### ARCH-016 — `workers/_shared` is a module (file) co-located with a same-name test directory and an out-of-workspace `pyproject`

```yaml
status: open
severity: low
effort: S
reviewed_at: e54458416e9bfe890a473dd9d542978d205b40a1
last_verified_at:
  commit: e54458416e9bfe890a473dd9d542978d205b40a1
  date: 2026-06-23
fixed_in: []
files:
  - path: workers/_shared.py
    lines: 1-31
  - path: workers/_shared/pyproject.toml
    lines: 1-2
  - path: workers/_shared/tests/test_safe_chapter_id.py
    lines: 1-55
  - path: pyproject.toml
    lines: 195
related: []
```

**Issue.** `workers/_shared.py` is a single-file module that exports `safe_chapter_id`. A separate `workers/_shared/` directory exists alongside it, containing `pyproject.toml` (with `[tool.pytest.ini_options] pythonpath = ["../.."]`) and `tests/test_safe_chapter_id.py`. The `pyproject.toml` is NOT listed in `tool.uv.workspace.members` (pyproject.toml:195 has only `workers/qwen3tts` and `workers/granite_speech`). The naming is inconsistent: every other worker (`workers/qwen3tts`, `workers/granite_speech`) is a package with `__init__.py`; `_shared` is a file.

**Why it matters.** The co-existence of `workers/_shared.py` and `workers/_shared/` is a latent footgun. If a future contributor adds `workers/_shared/__init__.py` (e.g. to share pytest fixtures between workers), Python's package-vs-module precedence will silently make `from workers._shared import safe_chapter_id` resolve to the empty package, breaking every handler import.

**Recommendation.** Pick one shape: (a) convert `_shared` to a real package — `rm workers/_shared.py`, `mkdir -p workers/_shared`, move the function into `workers/_shared/__init__.py`, add `workers/_shared` to `tool.uv.workspace.members`; (b) drop the directory entirely — keep `workers/_shared.py`, move the test, drop the hand-rolled `pyproject.toml`. Option (a) matches the package convention used by the other workers.

**Verification.** `python -c "import workers._shared; assert workers._shared.__file__.endswith('workers/_shared/__init__.py')"` passes (option a); `git grep -n '_shared' workers/` shows consistent package-shape; `uv lock --check` succeeds.

### CFG-008 — CFG-007 regression: `WorkerSettings.model_id` is set in 4 YAML files and still never consumed by any handler

```yaml
status: open
severity: medium
effort: S
reviewed_at: e54458416e9bfe890a473dd9d542978d205b40a1
last_verified_at:
  commit: e54458416e9bfe890a473dd9d542978d205b40a1
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/worker_sdk/settings.py
    lines: 62-63
  - path: workers/qwen3tts/worker.yaml
    lines: 37
  - path: workers/qwen3tts/worker.edge.yaml
    lines: 17
  - path: workers/granite_speech/worker.yaml
    lines: 30
  - path: workers/granite_speech/worker.edge.yaml
    lines: 13
  - path: workers/translategemma/worker.yaml
    lines: 30
  - path: workers/translategemma/worker.edge.yaml
    lines: 13
  - path: workers/qwen3tts/handler.py
    lines: 54
  - path: workers/granite_speech/handler.py
    lines: 30
  - path: workers/translategemma/handler.py
    lines: 125, 148, 205
related: [CFG-007, CFG-010]
```

**Issue.** The 8b worker addition widened CFG-007: `WorkerSettings.model_id: str | None` (settings.py:62) is now configured in FOUR YAML files (`workers/qwen3tts/worker.yaml:37`, `workers/qwen3tts/worker.edge.yaml:17`, `workers/granite_speech/worker.yaml:30`, `workers/granite_speech/worker.edge.yaml:13`) and STILL has zero consumers. `workers/qwen3tts/handler.py:54` hard-codes `_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"` and uses it in `capabilities()` and `startup()`. `workers/granite_speech/handler.py:30` hard-codes `_MODEL_ID = "ibm-granite/granite-speech-4.1-2b"`.

**Why it matters.** An operator editing `model_id` in any of the 4 YAMLs to point at a different revision will silently get the hard-coded model anyway — exactly the documentation-via-runtime-error pattern AGENTS.md calls out.

**Recommendation.** Either (a) implement the wiring: in each handler, replace the module-level `_MODEL_ID` reference inside `startup()` and `capabilities()` with `self._settings.model_id or _MODEL_ID`; or (b) drop the field from `WorkerSettings` and the 4 YAMLs until the wiring is implemented. Per AGENTS.md's greenfield rule, do not keep a named knob that silently behaves like another.

**Verification.** `git grep -n 'model_id' src/ workers/` returns either zero sites (option b) or one definition + one consumer per handler (option a); `just test`; `just lint-strict`; `just type-check`.

## ARCH (8c delta)

### ARCH-017 — `shell/tls.py` is a 24-line back-compat shim re-exporting `acheron.tls` — direct AGENTS.md greenfield violation

```yaml
status: fixed
severity: high
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: pending
  date: 2026-06-24
fixed_in: ["pending"]
files:
  - path: src/acheron/shell/tls.py
    lines: 1-24
  - path: src/acheron/shell/step_handler.py
    lines: 12
  - path: src/acheron/shell/health.py
    lines: 18
  - path: src/acheron/shell/api/__main__.py
    lines: 10
  - path: src/acheron/cli.py
    lines: 20
  - path: tests/shell/test_tls.py
    lines: 10
  - path: tests/shell/test_grpc_worker.py
    lines: 271
related: [DOC-005]
```

**Issue.** Layer 8c moved the real implementation from `shell/tls.py` to a new top-level `acheron/tls.py` (the only place the import-linter contract lets both `shell` and `worker_sdk`/`workers` consume it). It then LEFT `shell/tls.py` in place as a 24-line re-export shim whose docstring opens with the literal phrase "Backwards-compat shim — TLS helpers live in :mod:`acheron.tls` now." Seven call sites still import from `acheron.shell.tls` (step_handler.py, health.py, shell/api/__main__.py, cli.py, tests/shell/test_tls.py, tests/shell/test_grpc_worker.py, and the shim itself).

**Why it matters.** AGENTS.md hard rule 2 is explicit: "Project is greenfield, it should never have `legacy` code or `legacy` fallbacks, replace/refactor old paths over adding compatibility fallbacks." The shim is a 24-line dead-end module whose only purpose is to preserve old import paths. The new top-level module is the canonical location; the seven remaining `from acheron.shell.tls import ...` lines are simple `sed` targets. Keeping the shim normalises "we add a back-compat shim when we move things" as a pattern, which is exactly what AGENTS.md forbids.

**Recommendation.** Delete `src/acheron/shell/tls.py` outright. Update the seven import sites to `from acheron.tls import ...` (the new top-level location). The migration is mechanical; ruff isort + lint-strict will catch any miss. Per AGENTS.md, no shim is appropriate here — the new path is the only path.

**Verification.** `git grep -n 'acheron\.shell\.tls' src/ tests/` returns zero matches after the migration. `just test` (full 767-test suite). `just lint-strict` confirms no unused import or duplicate-name warnings. `git grep -n 'class tls\|def ' src/acheron/tls.py` shows the canonical location has the only definitions.

### ARCH-018 — `ChunkingTooLongForWorkerError` is a subclass of `InvalidLanguagePathError` for back-compat reasons that don't exist — codifies a documentation-via-runtime-error contract

```yaml
status: open
severity: high
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: eb6849c85d83f2277eb450f18a11e63cae2defd1
  date: 2026-06-24
fixed_in: []
files:
  - path: src/acheron/core/errors.py
    lines: 16-22
  - path: src/acheron/core/planner.py
    lines: 128
  - path: tests/core/test_errors.py
    lines: 66-68
related: [MAINT-016]
```

**Issue.** `ChunkingTooLongForWorkerError(InvalidLanguagePathError)` is defined at `core/errors.py:16-22` with a docstring that justifies the subclass relationship as "Subclass of `InvalidLanguagePathError` so existing handling (job rejection, dashboard) still works." `git grep 'InvalidLanguagePathError' src/ tests/ dashboard/` returns zero `except InvalidLanguagePathError` clauses and zero dashboard consumers — the back-compat justification has no receiver. The test `tests/core/test_errors.py:66-68` even names the contract: `def test_chunking_too_long_caught_as_language_path(self) -> None: with pytest.raises(InvalidLanguagePathError): raise ChunkingTooLongForWorkerError(...)` — codifying that a chunking-too-long error is caught as a language-path error. A "chunking step's max_chunk_length exceeds a text-input worker's max_input_tokens" is not a language-path problem: the source/target language pair is unchanged; the limit is structural (chars vs tokens).

**Why it matters.** AGENTS.md hard rules call for "strict domain separation" and "make illegal states unrepresentable." The class name says one thing, the parent says another. Any future `except InvalidLanguagePathError` handler — in the API's `/submit` route, in the dashboard's job-rejection UI, in a future monitoring alert — will silently swallow chunking-misconfiguration errors as language-path errors, with no log line and no different user message. The misclassification also makes the exception hierarchy harder to reason about: `isinstance(e, InvalidLanguagePathError)` no longer means "no worker speaks this language pair" — it also means "chunking settings are bigger than the worker's token window." AGENTS.md calls this exactly the "documentation-via-runtime-error contract" pattern it warns against.

**Recommendation.** Make `ChunkingTooLongForWorkerError` a sibling of `InvalidLanguagePathError` (both inherit from `PlanError`), or its own sub-category. Drop the `InvalidLanguagePathError` parent. The docstring's back-compat justification can be removed. Update `tests/core/test_errors.py:41` (the parametrised `test_child_inherits_from_parent` case) and the one `tests/shell/test_orchestrator.py:149` `pytest.raises(InvalidLanguagePathError, match='max_input_tokens=10')` to use the new class. Delete the `test_chunking_too_long_caught_as_language_path` test — it documents a contract that should not exist.

**Verification.** `git grep -n 'issubclass.*InvalidLanguagePathError' src/` returns only the legitimate `(InvalidLanguagePathError, PlanError)` case. `git grep -n 'except InvalidLanguagePathError\|pytest.raises(InvalidLanguagePathError)' tests/` shows no test or production handler treats chunking-too-long as a language-path error. `just test` (all 767 tests pass with the corrected hierarchy). `just type-check`.

### ARCH-019 — `validate_chunking_fits_workers` is a post-step in `submit_job` that should be folded into `compile_plan`

```yaml
status: open
severity: medium
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: 9b4adb6
  date: 2026-06-24
fixed_in: []
files:
  - path: src/acheron/core/planner.py
    lines: 19-52
  - path: src/acheron/core/planner.py
    lines: 92-128
  - path: src/acheron/shell/orchestrator.py
    lines: 274-278
related: [CFG-009, CORR-026]
```

**Issue.** `compile_plan` (planner.py:19-52) already runs `compile_plan → _validate_language_path` as one unit. The new `validate_chunking_fits_workers` (planner.py:92-128) is called immediately after, from `submit_job` (orchestrator.py:243-249), passing the same `capabilities` tuple, `self._settings.workers.chunking.max_chunk_length`, and `self._settings.chars_per_token`. The orchestrator now does two validation passes over the same capabilities tuple, threading the chunking settings in from the shell layer to the core layer for the second one.

**Why it matters.** Layer 8c is the first time the shell layer passes orchestrator-side settings into a core validator. The pattern is an inversion of the existing boundary: `compile_plan(request, strategy, capabilities)` is the canonical "validate the plan against the world" entry point; `validate_chunking_fits_workers` should be a private step inside it (or after, inside `compile_plan`'s body), not a public post-step the shell has to call. Adding a new plan-time check (e.g. "translation model max output tokens") will need another `validate_*` call in `submit_job` with another `settings.workers.*` thread-through, growing the seam. The current shape also makes the two validation passes non-atomic: a caller that calls `compile_plan` without `validate_chunking_fits_workers` gets an unchecked plan.

**Recommendation.** Fold `validate_chunking_fits_workers` into `compile_plan`'s body (after `_validate_language_path`). Extend the public signature minimally — either make `max_chunk_length` and `chars_per_token` additional required kwargs of `compile_plan`, or have `compile_plan` accept a `ChunkingSettings`-shaped struct as an optional kwarg. Update `submit_job` to call only `compile_plan(request, strategy, capabilities, chunking=...)`. Drop the standalone public export from `core/planner.py`.

**Verification.** `git grep -n 'validate_chunking_fits_workers' src/` shows only the definition (in `compile_plan`'s body). `git grep -n 'validate_chunking_fits_workers' tests/` shows the existing `tests/core/test_planner.py:181-281` cases pass with the new structure. `just test`; `just type-check`; `just lint-imports` (no boundary regression).

### ARCH-020 — `HttpWorker._execute_with_upstream_input` has a leaky triple-magic-string signature shared by three call sites

```yaml
status: open
severity: medium
effort: M
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: eb6849c85d83f2277eb450f18a11e63cae2defd1
  date: 2026-06-24
fixed_in: []
files:
  - path: src/acheron/shell/transports/http.py
    lines: 90-157
related: [ARCH-014, ARCH-015, CORR-027]
```

**Issue.** Layer 8c replaced the explicit ASR branch in `HttpWorker.execute()` with a single parametric helper `_execute_with_upstream_input(job, *, upstream_step, content_type_predicate, form_field)` (http.py:90-157), called three times: `("extract", lambda c: c.startswith("audio/"), "audio")` for ASR, `("chunk", lambda c: c == "application/json", "chunks")` for TRANSLATION+TTS. Three of the three arguments are discriminators the caller must supply correctly, and adding a new "upstream input" job type means a fourth site where three more strings must stay in lockstep. The runtime lambda for `content_type_predicate` is also untyped at the call site — basedpyright/mypy will accept any `Callable[[str], bool]` but cannot verify the caller passed the predicate that matches the form field's expected content type.

**Why it matters.** AGENTS.md hard rules call for "strict domain separation" and "use typing in your favor to avoid seas of complex branching that are brittle and hard to maintain and extend." The triple-string signature is the opposite: a runtime-typed dispatch where three magic values must be coordinated at the call site. The 8b ARCH-014 finding flagged a similar inversion (HttpWorker branches on `WorkerType.ASR` to add a transport-specific audio pipeline); 8c widened the branching to three branches sharing one helper, so the magic-string problem is now triplicated. Per `git grep -n 'WorkerType\.ASR\|WorkerType\.TRANSLATION\|WorkerType\.TTS' src/acheron/shell/transports/`, all three enum cases now reach into the transport's data layer for upstream-step semantics.

**Recommendation.** Encode the discriminator as a structured argument. Options: (a) introduce a `WorkerType`-keyed dispatch table `dict[WorkerType, UpstreamInputSpec]` that maps each text-input worker type to its `(upstream_step, content_type_predicate, form_field)` triple, and have `execute()` look up by `job.job_type`; (b) add a `Job.upstream_input_spec` field on the `Job` dataclass so the data flow is explicit and the transport is data-driven; (c) extract an `UpstreamInputHttpWorker(Worker)` subclass per input shape (the original ARCH-014 recommendation). (a) is the smallest change that removes the magic strings without inventing new types.

**Verification.** `git grep -n 'upstream_step=\|form_field=' src/acheron/shell/transports/` returns either zero matches (data-driven dispatch) or one constant per `WorkerType` case. The `Callable[[str], bool]` parameter is replaced by a frozen `UpstreamInputSpec` dataclass. `just test` (existing `test_asr_multipart.py` and `test_http_worker.py` continue to pass with the new dispatch). `just type-check`.

### ARCH-021 — Identical uvicorn+TLS 7-line boilerplate duplicated across 4 entry points after the worker-side TLS rollout

```yaml
status: open
severity: medium
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: eb6849c85d83f2277eb450f18a11e63cae2defd1
  date: 2026-06-24
fixed_in: []
files:
  - path: src/acheron/shell/api/__main__.py
    lines: 19-26
  - path: src/acheron/worker_sdk/cli.py
    lines: 76-83
  - path: stubs/tts_local_stub/main.py
    lines: 21-28
  - path: stubs/tts_grpc_stub/main.py
    lines: 21-28
related: []
```

**Issue.** Layer 8c added TLS to the worker-side entry points. All four `__main__` blocks (orchestrator: `shell/api/__main__.py:19-26`; worker-sdk CLI: `worker_sdk/cli.py:76-83`; and the two stubs: `stubs/tts_local_stub/main.py:21-28` + `stubs/tts_grpc_stub/main.py:21-28`) end with the exact same 7-line envelope: `ssl = uvicorn_ssl_kwargs(); uvicorn.run(<app>, host=<h>, port=<p>, ssl_certfile=ssl.get("ssl_certfile"), ssl_keyfile=ssl.get("ssl_keyfile"))`. The orchestrator site was pre-existing; the worker_sdk/cli + 2 stubs sites are new in 8c. Each was written separately; the 3 new ones are textually identical to the orchestrator site.

**Why it matters.** Three of the four sites are NEW in this delta. The pattern was set by the orchestrator's existing 7-line block; the new sites are copy-paste, not derivation from a helper. Per AGENTS.md hard rule 2, this is the same DRY/sprawl pattern that CFG-006 already tracks for env-var reads: the canonical function is `uvicorn_ssl_kwargs()`, but its *consumer* (the `uvicorn.run(...)` 5-arg envelope) is the recurring copy. A future TLS configuration addition (e.g. `ssl_ca_certs=`, `ssl_keyfile_password=`) would need four lockstep edits. The third copy is the moment DRY should win.

**Recommendation.** Add a `run_with_tls(app: ASGIApp, host: str, port: int) -> None` helper to `acheron/tls.py` (or a `run_worker_app` helper in `worker_sdk/app.py` if you prefer not to live in the TLS module). The helper does the `ssl = uvicorn_ssl_kwargs(); uvicorn.run(...)` envelope in one call. Update the four sites to call the helper. The orchestrator's site is unchanged in behaviour; the new sites gain shared semantics for free.

**Verification.** `git grep -n 'ssl_certfile=ssl.get\|ssl_keyfile=ssl.get' src/ stubs/` returns at most one match (inside the new helper). `just test` (existing 767-test suite + the 3 un-xfailed TLS integration tests continue to pass with the consolidated helper). `just lint-strict`; `just type-check`.

### ARCH-022 — `HttpWorker._post_multipart` is a near-byte-duplicate of `HttpWorker._request` — should be a one-liner wrapper

```yaml
status: open
severity: low
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: eb6849c85d83f2277eb450f18a11e63cae2defd1
  date: 2026-06-24
fixed_in: []
files:
  - path: src/acheron/shell/transports/http.py
    lines: 66-84
  - path: src/acheron/shell/transports/http.py
    lines: 159-181
related: [ARCH-021, PERF-007]
```

**Issue.** `_post_multipart` (http.py:159-181) reproduces the same try/except/raise-status/ConnectError→WorkerUnavailableError/StatusError→WorkerError envelope as `_request` (http.py:66-84), with only two differences: the method is hardcoded to POST, the path to `/execute`, and the parameter is `files=form` instead of a kwarg passthrough. The 23-line helper is functionally a one-liner: `return await self._request("POST", "/execute", files=form)`.

**Why it matters.** Two near-identical request methods in the same class is a clear DRY hazard: any future change to the error-conversion contract (e.g. add retry, add a request-id header) must be applied to both. The two also differ subtly — `_request` accepts arbitrary kwargs, `_post_multipart` accepts only `form: Mapping[str, tuple[...]]`, so a future caller that wants both `files=form` and `headers={...}` will reach for `_request` directly, defeating the encapsulation. The helper exists for one specific call site, which is a code smell.

**Recommendation.** Delete `_post_multipart`. Replace the call at `http.py:153` with `resp = await self._request("POST", "/execute", files=form)`. Drop the `Mapping` TYPE_CHECKING import (no other user). If the form-typing guidance is worth keeping, type `_request` with an overload that pins `files` to `Mapping[str, tuple[str | None, bytes, str]]` when present — but the unpacking is already structurally obvious from the only call site.

**Verification.** `git grep -n '_post_multipart' src/acheron/shell/transports/` returns zero matches. `git grep -n 'def _post_multipart\|def _request' src/acheron/shell/transports/http.py` returns only one definition. `just test` (the existing `test_asr_multipart.py` cases continue to pass; the request still uses the same client / no-client branch). `just lint-strict`; `just type-check`.

## CFG (8c delta)

### CFG-009 — `Settings.chars_per_token` is a top-level knob consumed by exactly one function and duplicated in two defaults

```yaml
status: open
severity: medium
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: 9b4adb6
  date: 2026-06-24
fixed_in: []
files:
  - path: src/acheron/shell/config.py
    lines: 141
  - path: src/acheron/core/planner.py
    lines: 92-128
  - path: src/acheron/shell/orchestrator.py
    lines: 274-278
  - path: acheron.yaml.example
    lines: 1-65
related: [ARCH-019, CORR-026]
```

**Issue.** `Settings.chars_per_token: int = Field(default=4)` is declared at the top level of `Settings` (`shell/config.py:141`) with the only consumer being `validate_chunking_fits_workers` — invoked from exactly one call site (`orchestrator.py:245-249`). The default `4` is duplicated at the function signature: `def validate_chunking_fits_workers(..., chars_per_token: int = 4)`. The YAML example (`acheron.yaml.example:1-65`) does not document the field. AGENTS.md explicitly warns: "Avoid config knobs that don't actually control anything, unless there is reasonable expectation that a new behavior is going to be added soon (YAGNI); prefer a concise comment instead."

**Why it matters.** A top-level Settings field with a single consumer is a knob that has to be carried through the full env / YAML / `_EnvAliasSettingsSource` / pydantic validation surface for a 1-call-site use. The default lives in two places that must stay in sync (Settings and function signature). The YAML example doesn't mention the field, so an operator who discovers it via `Settings` schema introspection will set it without guidance. The cost of a single orchestrator-only constant is one extra import in tests, no env-var plumbing, no schema-validator maintenance, and one fewer field for AGENTS.md's "YAGNI" to flag.

**Recommendation.** Drop `Settings.chars_per_token` entirely. Either (a) hard-code the constant in `core/planner.py` as `_DEFAULT_CHARS_PER_TOKEN = 4` next to the function and remove the parameter; or (b) keep the parameter but default it from the function signature only, and have `submit_job` pass `chars_per_token=settings.workers.chunking.chars_per_token` only if a new `ChunkingSettings.chars_per_token` field is added (which is a structural decision, not a single-knob one). (a) is the smallest change; (b) keeps the operator-facing knob if a near-future tokenizer-based estimator is on the roadmap. Either way, drop the YAML example entry (currently absent) and document the chosen shape in a single comment near the constant.

**Verification.** `git grep -n 'chars_per_token' src/` returns either zero sites (option a) or one constant + one consumer (option b). `just test` (the `test_smaller_chars_per_token_triggers_earlier` and `test_invalid_chars_per_token_raises` cases in `tests/core/test_planner.py:249-271` continue to work by passing the kwarg directly). `just type-check`; `just lint-strict`.

### CFG-010 — `WorkerSettings.model_id` is now consumed only by `translategemma` — qwen3tts and granite_speech still hard-code the value, widening the CFG-007/008 silence from 4 YAMLs to 6

```yaml
status: open
severity: medium
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: eb6849c85d83f2277eb450f18a11e63cae2defd1
  date: 2026-06-24
fixed_in: []
files:
  - path: workers/qwen3tts/handler.py
    lines: 55, 100-117, 119-133
  - path: workers/qwen3tts/worker.yaml
    lines: 37
  - path: workers/qwen3tts/worker.edge.yaml
    lines: 17
  - path: workers/granite_speech/handler.py
    lines: 30, 44-60, 62-78, 121
  - path: workers/granite_speech/worker.yaml
    lines: 30
  - path: workers/granite_speech/worker.edge.yaml
    lines: 13
  - path: workers/translategemma/handler.py
    lines: 29, 125, 148, 205
  - path: workers/translategemma/worker.yaml
    lines: 30
  - path: workers/translategemma/worker.edge.yaml
    lines: 13
related: [CFG-007, CFG-008]
```

**Issue.** Layer 8c adds a new worker package, `workers/translategemma/`, that CONSUMES `WorkerSettings.model_id` correctly: `model_id = self._settings.model_id or _MODEL_ID_DEFAULT` (translategemma/handler.py:125, 148, 205). It also sets `model_id: "google/translategemma-12b-it"` in both `worker.yaml:30` and `worker.edge.yaml:13`. The two pre-existing workers (qwen3tts, granite_speech) do NOT consume the field: qwen3tts/handler.py hard-codes `_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"` and uses it in `capabilities()` (line 115) and `startup()` (line 127); granite_speech/handler.py hard-codes `_MODEL_ID = "ibm-granite/granite-speech-4.1-2b"` and uses it in `capabilities()` (line 58), `startup()` (line 72), and the artifact metadata (line 121). The 8c delta therefore widened the CFG-007/008 silence from 4 YAMLs (2 worker.yaml + 2 worker.edge.yaml) to 6 YAMLs (2+2+2), while introducing the FIRST worker that actually consumes the field.

**Why it matters.** AGENTS.md hard rule 2 explicitly calls this out: "config knobs that don't actually control anything ... silent/unexpected behavior is worse than no control at all." An operator who edits `model_id` in `workers/qwen3tts/worker.yaml:37` to point at a different Qwen3-TTS revision will silently get the hard-coded model — the YAML and the code disagree, and the YAML is documented-but-ignored. The 8c translategemma work proved the wiring is feasible (a 3-line change in 3 places); the asymmetry between the 3 worker packages is now a 1-of-3 implementation. CFG-007/008 remains the same story, but the surface has grown: 6 YAMLs, 4 silent, 2 effective.

**Recommendation.** Apply the same `self._settings.model_id or _DEFAULT` pattern to qwen3tts and granite_speech (the three call sites each: `capabilities()`, `startup()`, and any artifact metadata). If a 4th worker is added, the pattern is now obviously the convention; if not, the option is to drop the field from the 4 silent YAMLs and from `WorkerSettings.model_id` (per the AGENTS.md YAGNI guidance, until the wiring exists). Either path closes the CFG-007/008 finding; the wiring path matches what translategemma already does.

**Verification.** `git grep -n 'model_id' src/acheron/worker_sdk/settings.py workers/` shows either zero consumers (option b) or one definition + three workers with `self._settings.model_id or _DEFAULT` (option a). `just test` (existing `test_capabilities.py` for each worker continues to pass with the field read from settings). `just type-check`; `just lint-strict`.

### CFG-011 — `WorkerCapabilities.max_input_tokens` is published in capabilities() by 2 workers but only consumed in 1 place (the planner) — value is hard-coded in handlers, not configurable via WorkerSettings

```yaml
status: open
severity: low
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: eb6849c85d83f2277eb450f18a11e63cae2defd1
  date: 2026-06-24
fixed_in: []
files:
  - path: src/acheron/core/models.py
    lines: 89
  - path: src/acheron/worker_sdk/settings.py
    lines: 50-118
  - path: workers/qwen3tts/handler.py
    lines: 114
  - path: workers/translategemma/handler.py
    lines: 30, 134
related: [CFG-009, CFG-010]
```

**Issue.** `WorkerCapabilities.max_input_tokens: int | None = None` is a new field on the `core/models.py` dataclass (line 89). The orchestrator's `validate_chunking_fits_workers` (planner.py:118, 121, 124, 125) is the only consumer. Two workers publish it: qwen3tts/handler.py:114 with the literal `2048`; translategemma/handler.py:134 with `_MAX_INPUT_TOKENS = 2048` (a module-level constant). There is no `WorkerSettings.max_input_tokens` field (per `git grep -n 'max_input_tokens' src/acheron/worker_sdk/`). Granite-speech doesn't publish it (correct — it's ASR, not text-input). The 2048 value is therefore baked into the handler source, not the YAML.

**Why it matters.** The field is half-configurable: a `Settings.chars_per_token` user can tune the orchestrator's check (CFGs side), but the worker-side cap that the check is measured against is a hard-coded constant per handler. An operator who wants to deploy a worker with a 4096-token model cannot do so without editing the handler. The asymmetry with translategemma's `model_id` (consumed from `WorkerSettings`) is visible at the same call site: `max_input_tokens=_MAX_INPUT_TOKENS` is hard-coded, `model_source=f"huggingface:{model_id}"` reads from settings.

**Recommendation.** Add `max_input_tokens: int | None = None` to `WorkerSettings`. In each worker that publishes a `max_input_tokens`, read it: `max_input_tokens=self._settings.max_input_tokens or _DEFAULT_MAX_INPUT_TOKENS`. Document the field in the worker YAML files. This is the same one-line wiring the translategemma worker already does for `model_id`; applying it to `max_input_tokens` is the natural symmetry.

**Verification.** `git grep -n 'max_input_tokens' src/acheron/worker_sdk/settings.py` returns one definition. `git grep -n 'max_input_tokens=_MAX\|max_input_tokens=2048' workers/` returns either zero matches (option: drop the field until wiring exists) or one default-and-consume pattern per text-input worker. `just test` (existing `test_capabilities.py` for qwen3tts and translategemma continues to pass with the default). `just type-check`; `just lint-strict`.
