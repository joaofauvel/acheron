---
branch: chore/code-review-update
initial_review_commit: 23c29e1
last_updated_commit: a1b11b2
last_staleness_scan:
  commit: a1b11b2
  date: 2026-06-19
---

# Architecture

## ARCH — Architecture

**Grade:** A

Two low findings carried in: a module-private `_BUILT_IN_LOCAL_HANDLERS` symbol imported cross-module from local_handlers.py, and Orchestrator.__init__ still derives the default StepCache from PlanCache.data_dir, weakening the ARCH-003 injection seam. Four verified stories (ARCH-001 through ARCH-004) are immutable. The core->shell import-linter contract holds.

### ARCH-001 — BatchAsyncExecutor is a no-op duplicate of AsyncExecutor; ExecutorStrategy.BATCH_ASYNC controls nothing

```yaml
status: verified
severity: high
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: a1b11b2
  date: 2026-06-19
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

**Issue.** `BatchAsyncExecutor.run()` (batch_async.py:26-79) is byte-for-byte identical to `AsyncExecutor.run()` (async_executor.py:22-75) except for the docstring — a direct diff of the two bodies shows only the docstring line differs. The class docstring (batch_async.py:17-21) promises that "Batch-flagged steps receive all outputs from completed preceding steps so the handler can construct a BatchJob with the correct payloads," but the implementation just calls `self._handler(step, plan)` per step in a wave — it never constructs a `BatchJob`, never calls `StreamingWorker.submit_batch`/`poll_batch`/`collect_results`, and never inspects `PlanStep.batch`. The `StreamingWorker` ABC methods (interfaces.py:39-51) are only ever invoked by the transport workers themselves (grpc.py:103-124, http.py:77-92), never by any executor. Consequently `ExecutorStrategy.BATCH_ASYNC` (models.py:42) selects a strategy that behaves identically to `ExecutorStrategy.ASYNC`. The CLI even defaults `--executor` to `batch_async` (cli.py:141), so the default strategy users hit is a no-op abstraction.

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
  commit: a1b11b2
  date: 2026-06-19
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

### ARCH-003 — Orchestrator accretes capability aggregation, worker registration, job lifecycle, and data-dir verification in one 345-line class

```yaml
status: verified
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: a1b11b2
  date: 2026-06-19
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

**Why it matters.** A 345-line service class with four responsibilities is harder to test in isolation (any test for capability aggregation drags in job submission, the data-dir probe, and built-in worker registration) and accretes further as each concern grows. The capability route is coupled to the orchestrator rather than to the registry it actually queries. Medium because it works today, but the coupling raises the cost of every future change to any of the four concerns.

**Recommendation.** Extract a `CapabilityAggregator` (taking a `WorkerStore`) owning `_collect_worker_caps`, `_pair_is_achievable`, `LanguagePair`, and `get_capabilities`; have the orchestrator delegate or have the API route depend on the aggregator directly. Move `_all_languages_caps` and `_BUILT_IN_LOCAL_HANDLERS` next to `local_handlers.py`. Inject `StepCache` explicitly into `Orchestrator` rather than deriving it from `PlanCache.data_dir`. Keep `Orchestrator` focused on job lifecycle + lifecycle wiring.

**Verification.** `just test` after extraction; `just lint-imports` to confirm the new boundary; confirm the `/capabilities` route still resolves and that capability-aggregation tests no longer need to construct a full Orchestrator with a writable data dir.

### ARCH-004 — metadata typed dict[str, object] on RegisteredWorker/WorkerStore ABC vs dict[str, JsonValue] on WorkerCapabilities — Redis serde requires JSON-serializable but the type permits non-serializable

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: fb69e0e
  date: 2026-06-19
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

**Issue.** `RegisteredWorker.metadata` is typed `dict[str, object]` (registry.py:27) and `WorkerStore.register(... metadata: dict[str, object] | None)` (base.py:30), while `WorkerCapabilities.metadata` is typed `dict[str, JsonValue]` (models.py:58). The Redis backend serializes worker metadata with `json.dumps(metadata, sort_keys=True)` (redis.py:85) and deserializes it with `json.loads` (redis.py:100) — both assume JSON-serializable values. The `dict[str, object]` annotation does not enforce that; a caller passing `{"handler": some_callable}` or `{"count": object()}` would pass the type checker (mypy strict, basedpyright standard) and the ABC contract, then crash at Redis serialization time. The orchestrator goes out of its way to document that "metadata holds JSON-serializable values only" (orchestrator.py:148, registry.py:18) because the type does not say so. `InMemoryWorkerStore` accepts the same loose type (memory.py:28).

**Why it matters.** The gap between the declared type (`object`) and the real contract (JSON-serializable) is a runtime-error-via-types hazard that AGENTS.md calls out as "make illegal states unrepresentable" and "avoid Any and don't let Mapping[str, Any] become a documentation-via-runtime-error contract." The docstring compensation is exactly the documentation-via-runtime-error pattern. Medium because it fails only at the Redis backend, not the memory backend, so it surfaces as a backend-specific crash rather than a type error.

**Recommendation.** Align both `RegisteredWorker.metadata` and `WorkerStore.register`'s `metadata` parameter to `dict[str, JsonValue]` (reusing the alias from `core/models.py`), so the type system enforces JSON-serializability across all backends. Drop the compensating docstrings once the type carries the contract. If non-serializable side data is genuinely needed (e.g. local handlers), keep it in the orchestrator's side dict as already done for `_local_handlers` — do not relax the store metadata type.

**Verification.** `just type-check` and `just type-check-pyright` to confirm the stricter type propagates; `just test`; grep to confirm no caller passes a non-JsonValue metadata dict.

## CFG — Configuration

**Grade:** A

One medium finding: `ACHERON_STORE_BACKEND` / `REDIS_URL` selection logic is duplicated across `create_worker_store` and `create_job_store`, risking split-brain configuration if a contributor updates only one. One low finding flags duplicated knowledge (TLS CA env-read logic and a hardcoded language set repeated across modules).

### CFG-001 — ACHERON_STORE_BACKEND / REDIS_URL selection logic duplicated across create_worker_store and create_job_store

```yaml
status: open
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: a1b11b2
  date: 2026-06-19
fixed_in: []
files:
  - path: src/acheron/shell/stores/__init__.py
    lines: 39-53
  - path: src/acheron/shell/stores/__init__.py
    lines: 39-53
related: [ARCH-002]
```

**Issue.** `create_worker_store()` (17-37) and `create_job_store()` (39-53) each independently read `ACHERON_STORE_BACKEND` and `REDIS_URL` and run an identical `match backend: case 'memory': ... case 'redis': ...` ladder. The only difference is which concrete class is instantiated. Adding a new backend (e.g. sqlite) requires editing both functions in lockstep, and the two reads of `ACHERON_STORE_BACKEND` can diverge if the env var is mutated between the two calls (an unlikely but representable race). The `REDIS_URL` default `redis://localhost:6379` is also duplicated on lines 33 and 50.

**Why it matters.** Two functions that must stay in sync is a classic DRY/maintainability hazard; a contributor adding a backend who updates only one function silently produces a split-brain configuration (worker store on one backend, job store on another). Medium because the blast radius of a missed update is a silent state-location mismatch.

**Recommendation.** Unify into a single `create_stores() -> tuple[WorkerStore, JobStore]` (or a `_select_backend()` helper returning both classes) that reads `ACHERON_STORE_BACKEND` and `REDIS_URL` once and constructs both stores from the same selection. Keep the individual factories as thin wrappers if external callers need them, but route them through the unified selector.

**Verification.** `just test` (existing `test_worker_integration.py:163` covers the redis/memory switch); add a test asserting both stores share the same backend for a given `ACHERON_STORE_BACKEND`; `just lint-strict`.

### CFG-002 — Duplicated knowledge: TLS CA env-read logic and hardcoded language set repeated across modules

```yaml
status: open
severity: low
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: a1b11b2
  date: 2026-06-19
fixed_in: []
files:
  - path: src/acheron/shell/tls.py
    lines: 60
  - path: src/acheron/cli.py
    lines: 46
  - path: src/acheron/shell/orchestrator.py
    lines: 42-55
  - path: src/acheron/shell/transports/grpc.py
    lines: 39-40
related: [SEC-003]
```

**Issue.** Two small but distinct duplications recur: (1) The `ACHERON_TLS_CA_FILE or SSL_CERT_FILE` trust-store resolution is implemented in `tls.py:60` (`grpc_channel_credentials`) and re-implemented verbatim in `cli.py:46` (`_resolve_trust_store`). Both read the same two env vars with the same precedence; if the precedence changes (e.g. adding `ACHERON_TLS_CA_DIR`), both sites must be updated. (2) The hardcoded language set `{"en", "es", "fr", "de"}` appears in `_all_languages_caps` (orchestrator.py:48-49) and again in `GrpcWorker.capabilities` (grpc.py:43-44). Both claim to describe the same "supported languages" universe but are independent literals; adding a language requires editing both, and the gRPC worker's claim is decoupled from the orchestrator's built-in-worker claim.

**Why it matters.** Each duplication is small, but together they are the kind of recurring duplicated knowledge that drifts silently: a contributor adds a language to one site and ships a worker that advertises a different language set than the orchestrator's built-ins. Low because the duplication is localized and easy to spot, but it is a latent drift hazard.

**Recommendation.** Extract the trust-store resolution into a single helper (e.g. `tls.resolve_ca_path() -> str | None`) and call it from both `tls.py` and `cli.py`. Extract the supported-languages set into a shared constant in `core/models.py` (or a `core/constants.py`) and import it in both `orchestrator.py` and `grpc.py`. Prefer making the gRPC worker's languages configurable via `WorkerCapabilities` rather than hardcoded, if feasible.

**Verification.** `just test`; `just lint-strict`; grep to confirm only one site defines the language set and one site defines the CA resolution order.

### ARCH-005 — _BUILT_IN_LOCAL_HANDLERS is a module-private (leading-underscore) name imported cross-module from local_handlers.py

```yaml
status: open
severity: low
effort: S
reviewed_at: a1b11b2
last_verified_at:
  commit: a1b11b2
  date: 2026-06-19
fixed_in: []
files:
  - path: src/acheron/shell/local_handlers.py
    lines: 89-93
  - path: src/acheron/shell/orchestrator.py
    lines: 20
```

**Issue.** The ARCH-003 extraction moved `_BUILT_IN_LOCAL_HANDLERS` from `orchestrator.py` into `shell/local_handlers.py` (local_handlers.py:89-93), but the orchestrator still imports this name across a module boundary (orchestrator.py:20: `from acheron.shell.local_handlers import _BUILT_IN_LOCAL_HANDLERS, ...`). Leading-underscore names are the Python convention for module-private symbols; importing them from another module breaks the encapsulation contract and signals an unfinished refactor.

**Why it matters.** Cross-module private imports make refactoring `local_handlers.py` (renaming, splitting, relocating) silently break the orchestrator's import. Today the only consumer is the orchestrator, but the import is a latent maintenance hazard that the original ARCH-003 cleanup did not resolve, and ruff's `PLC2701` (import-private-name) would flag it if the project enabled that rule.

**Recommendation.** Pick one: (1) rename `_BUILT_IN_LOCAL_HANDLERS` to `BUILT_IN_LOCAL_HANDLERS` in local_handlers.py and drop the leading underscore at the import site in orchestrator.py:20; (2) move the registration loop out of `Orchestrator._register_built_in_local_workers` and into local_handlers.py as `async def register_built_in_local_workers(registry, local_handlers: dict[str, LocalJobHandler]) -> None`, then have the orchestrator call it. Option 2 is the cleaner separation of concerns.

**Verification.** just test; just lint-strict; grep -rn '^from acheron.shell.local_handlers import _' src/ to confirm no leading-underscore imports remain.

### ARCH-006 — Orchestrator.__init__ still derives StepCache from PlanCache.data_dir as the default, keeping the PlanCache coupling ARCH-003 wanted removed

```yaml
status: open
severity: low
effort: S
reviewed_at: a1b11b2
last_verified_at:
  commit: a1b11b2
  date: 2026-06-19
fixed_in: []
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 40-52
```

**Issue.** ARCH-003 explicitly recommended 'Inject StepCache explicitly into Orchestrator rather than deriving it from PlanCache.data_dir.' The diff added a `step_cache: StepCache | None = None` keyword (orchestrator.py:47) and stores the injected value, but the default path still constructs `StepCache(cache.data_dir)` (orchestrator.py:51) and immediately calls `self._verify_data_dir_writable()` (orchestrator.py:52) — so any caller that does not inject a StepCache still couples Orchestrator construction to a writable PlanCache data dir.

**Why it matters.** The default path defeats the purpose of the injection seam. Tests still need a `tmp_path` (or equivalent) to construct an Orchestrator, and any future code path that wants the CapabilityAggregator in isolation must drag a PlanCache + writable data dir along. Low because the seam exists for callers who want it, but the default is the wrong default.

**Recommendation.** Either (a) make `step_cache` a required keyword argument (no default) and remove the `cache.data_dir` derivation entirely — callers must always pass a StepCache; (b) drop the `cache: PlanCache` parameter from Orchestrator and pass `StepCache` directly to the call sites that need it; or (c) move `self._verify_data_dir_writable()` out of `__init__` into `start()` so the construction probe is not a barrier to instantiation. (a) is the smallest change that actually delivers ARCH-003's stated intent.

**Verification.** just test; assert that Orchestrator(...) without a `step_cache` argument raises a clear TypeError; assert that constructing an Orchestrator with a writable `tmp_path` PlanCache still works; just lint-strict.
