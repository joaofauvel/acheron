---
branch: chore/code-review-update
initial_review_commit: 23c29e1
last_updated_commit: d0b739b
last_staleness_scan:
  commit: d0b739b
  date: 2026-06-20
---

# Verification

## TEST — Test discipline

**Grade:** A

Two open findings: a misleading test name claiming Redis coverage while actually testing memory (medium), and a tautological assertion that always passes (low). TEST-001 (direct local_handlers tests) and TEST-004 (conftest job_store injection) are now verified. REPRO and DATA prefixes carry REPRO-001, REPRO-002 (open), and all DATA stories now verified.

### TEST-001 — local_handlers.py has zero direct unit tests

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: d0b739b
  date: 2026-06-20
fixed_in:
  - 8f29b8b0b65d1ba902a2e265ac9f25f87485c078
files:
  - path: src/acheron/shell/local_handlers.py
    lines: 20-31, 34-50, 53-68, 71-86, 89-93
related: []
```

**Issue.** `extract_handler`, `chunk_handler`, and `package_handler` had no dedicated test file. They were only exercised indirectly through orchestrator integration tests. Their output construction — filename extraction from `source_path.rsplit('/', 1)[-1]`, `content_type` values, `size_bytes=0`, `checksum=''` — was never directly asserted. A grep for `extract_handler|chunk_handler|package_handler|local_handlers` across tests/ returned no imports.

**Why it matters.** These handlers run on every job's extract/chunk/package steps. A regression in filename derivation or content_type would only be caught if an integration test happens to assert on those fields, which none did directly. Medium severity — core path with only transitive coverage.

**Recommendation.** Add `tests/shell/test_local_handlers.py` with direct tests for each handler: assert OutputFile.filename, content_type, size_bytes, and path for representative payloads (including empty source_path, nested paths).

**Verification.** Run `just test` and check the coverage report for `src/acheron/shell/local_handlers.py` reaches 100%.

### TEST-002 — test_orchestrator_works_with_redis_backend tests memory, not Redis — misleading name and no Redis coverage

```yaml
status: open
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: d0b739b
  date: 2026-06-20
fixed_in: []
files:
  - path: tests/integration/test_worker_integration.py
    lines: 165-184
related: []
```

**Issue.** The test is named `test_orchestrator_works_with_redis_backend` with docstring "Orchestrator can start with ACHERON_STORE_BACKEND=redis (regression for C1)" but: (1) sets `os.environ['ACHERON_STORE_BACKEND'] = 'memory'` (line 169), (2) uses `InMemoryWorkerStore()` (line 171), and (3) never imports or uses RedisJobStore/RedisWorkerStore. The regression it claims to guard (handlers in metadata crashing Redis serialization) is not actually tested against Redis. It also uses raw `os.environ` instead of `monkeypatch`, inconsistent with the rest of the codebase.

**Why it matters.** The test gives false confidence that the Redis backend works with the orchestrator's local-worker registration. A real Redis regression would not be caught. Medium severity — the test name actively misleads reviewers.

**Recommendation.** Either rename to `test_orchestrator_registers_local_workers_without_serializable_handlers` and keep memory, or actually test with RedisJobStore/RedisWorkerStore using the `redis_url` fixture from `tests/shell/stores/conftest.py`. Use `monkeypatch.setenv` instead of `os.environ`.

**Verification.** Run `just test`; if converting to Redis, ensure the test uses the `redis_container` fixture and passes.

### TEST-003 — Tautological assertion in test_get_capabilities_no_translation_worker

```yaml
status: open
severity: low
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: d0b739b
  date: 2026-06-20
fixed_in: []
files:
  - path: tests/shell/test_orchestrator.py
    lines: 224-227
related: []
```

**Issue.** Line 227: `assert ('en', 'en') not in pairs or ('en', 'en') in pairs` is `A or not A` — always True. The assertion verifies nothing. The test's meaningful assertion is line 226 (`assert ('en', 'es') not in pairs`). The tautological line appears to be a leftover from an undecided intent.

**Why it matters.** Dead assertions erode test suite trust and can mask regressions — a reader assumes the test covers the `('en','en')` case when it doesn't. Low severity — cosmetic but misleading.

**Recommendation.** Decide the intended behavior (should same-language pairs appear without a translation worker?) and replace with a concrete assertion, e.g. `assert ('en', 'en') not in pairs` or `assert ('en', 'en') in pairs`. The test name suggests same-language pairs should NOT appear here since there's no TTS for 'en'.

**Verification.** Run `just test tests/shell/test_orchestrator.py::TestOrchestrator::test_get_capabilities_no_translation_worker`.

### TEST-004 — Conftest make_app and other API test sites do not inject job_store, leaking env-config dependence

```yaml
status: verified
severity: medium
effort: S
reviewed_at: a1b11b2
last_verified_at:
  commit: d0b739b
  date: 2026-06-20
fixed_in:
  - 2e0e46ddd406a91112b66247c13fab693f8b9066
files:
  - path: tests/shell/conftest.py
    lines: 67-77
  - path: tests/shell/conftest.py
    lines: 79-93
  - path: tests/shell/conftest.py
    lines: 96-103
  - path: tests/shell/api/test_jobs.py
    lines: 129-140
related: []
```

**Issue.** tests/shell/conftest.py:65 make_app() called create_app(registry=..., cache=..., data_dir=tmp_path) without injecting job_store, and tests/shell/api/test_jobs.py:133 replicated the same pattern. create_app then fell through to create_job_store() which reads ACHERON_STORE_BACKEND from os.environ and, for 'redis', instantiates RedisJobStore pointed at REDIS_URL. The client and client_with_token fixtures inherited this and silently depended on the developer's shell environment being free of ACHERON_STORE_BACKEND=redis. AGENTS.md states "tests shouldn't use repo configuration files or depend on hardcoded project paths" — env-config dependence is in the same family of brittleness.

**Why it matters.** Any developer with ACHERON_STORE_BACKEND=redis exported in their dev shell would see all API tests fail with Redis connection errors, even though the tests claim to be hermetic. Medium because it breaks test isolation for a plausible developer configuration.

**Recommendation.** Update tests/shell/conftest.py make_app() to pass job_store=InMemoryJobStore(), and update all other create_app() call sites in tests to inject the job store.

**Verification.** Run `just test tests/shell/api/` with both `ACHERON_STORE_BACKEND` unset and `ACHERON_STORE_BACKEND=redis REDIS_URL=redis://127.0.0.1:1` exported. Both should pass.

## REPRO — Reproducibility

**Grade:** A

### REPRO-001 — Redis list_all() returns non-deterministic order — step_handler worker selection is non-deterministic with Redis backend

```yaml
status: open
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: pending
  date: 2026-06-20
fixed_in: []
files:
  - path: src/acheron/shell/stores/redis.py
    lines: 306-315
  - path: src/acheron/shell/step_handler.py
    lines: 96-109
  - path: tests/integration/test_worker_integration.py
    lines: 222-238
related: []
```

**Issue.** `RedisWorkerStore.list_all()` (redis.py:310) iterates `await self._redis.smembers(_WORKERS_SET)` which returns a `set` — iteration order is non-deterministic. `step_handler.create_step_handler` (step_handler.py:96-109) iterates `registry.list_all()` and picks the first matching worker (`selected = w; break`). With Redis backend and multiple matching workers, which worker is selected is non-deterministic. The integration test `test_multiple_tts_workers_uses_first` asserts `tts_workers[0].worker_id == 'tts-http'` but only uses InMemoryWorkerStore (insertion-ordered), so it does not cover the Redis non-determinism.

**Why it matters.** With Redis backend, two TTS workers supporting the same language could be selected in any order across requests, leading to non-deterministic load distribution and potentially inconsistent results if workers have different models. Medium severity — affects production dispatch determinism, not just tests.

**Recommendation.** Sort `ids` in `RedisWorkerStore.list_all()` (e.g. `sorted(await self._redis.smembers(...))`) and do the same in `RedisJobStore.list_all()`. Add a test asserting deterministic ordering across calls. Alternatively, document that worker selection is arbitrary when multiple match.

**Verification.** Run `just test`; add a test that calls `list_all()` twice on a Redis store with 3+ workers and asserts identical ordering both times.

### REPRO-002 — Health monitor tests rely on timing-based asyncio.sleep windows flake-prone under xdist load

```yaml
status: open
severity: low
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: d0b739b
  date: 2026-06-20
fixed_in: []
files:
  - path: tests/shell/test_health_monitor.py
    lines: 57-92
related: [PERF-001]
```

**Issue.** `test_removes_worker_after_max_failures` starts the monitor with `interval=0.01`, then `await asyncio.sleep(0.15)` and asserts the worker was removed (requires 3 health-check failures). Under `pytest-xdist -n auto` with heavy parallelism, the background task may not get enough event-loop cycles within 0.15s to complete 3 iterations. Similarly, `test_records_success_for_healthy_worker` and `test_records_failure_for_unhealthy_worker` sleep 0.05s and assert the mock was called. There is no synchronization mechanism (e.g. an event the monitor sets after each check) — only sleep-based polling.

**Why it matters.** These tests can flake under CI load, producing false failures. Low severity because the windows are generous relative to the interval, but the pattern is inherently racy.

**Recommendation.** Replace fixed sleeps with a polling loop that checks the assertion condition with a deadline (e.g. poll every 0.01s for up to 1s until `await reg.get('w1') is None`), or expose a test hook (e.g. an `asyncio.Event` set after each `_check_all` cycle) to synchronize.

**Verification.** Run `just test tests/shell/test_health_monitor.py` repeatedly (e.g. `pytest --count=20`) under load to check for flakes.

## DATA — Data quality

**Grade:** A

All four DATA stories are now verified. DATA-004 (previously split from DATA-003) covers the worker+capabilities metadata round-trip gap. DATA-003's original concern (PlanStep.batch and non-empty metadata untested) is fully resolved: PlanStep.batch was removed in e0da69f, and metadata round-trip is tested in a21fda7.

### DATA-001 — API pydantic schemas accept arbitrary extra fields, silently dropping client typos

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: d0b739b
  date: 2026-06-20
fixed_in:
  - cf3a658b518124a878021400ed3e2c1dfcfad7c4
files:
  - path: src/acheron/shell/api/schemas.py
    lines: 1-88
related: []
```

**Issue.** None of the API request schemas (SubmitJobRequest, WorkerCapabilitiesRequest, WorkerRegistrationRequest) set `model_config = ConfigDict(extra='forbid')`. Pydantic v2 defaults to `extra='ignore'`, so unknown fields are silently dropped. A client typo on a field that has a default — e.g. `executor_strategi` instead of `executor_strategy` — is accepted with the default value rather than rejected. No test asserted that extra fields are rejected.

**Why it matters.** Silent field drops cause hard-to-diagnose misconfigurations: a worker registered with a typo'd `batch_capable` field silently registers as non-batch-capable, or a job submits with the wrong executor strategy. Medium severity because it only affects fields with defaults, but those include strategy and capability flags that change runtime behavior.

**Recommendation.** Add `model_config = ConfigDict(extra='forbid')` to SubmitJobRequest, WorkerCapabilitiesRequest, and WorkerRegistrationRequest. Add tests posting an extra field and asserting a 422 response.

**Verification.** Run `just test`; add a test in `tests/shell/api/test_jobs.py` and `test_workers.py` that posts a body with an extra `typo_field` and asserts `status_code == 422`.

### DATA-002 — Redis deserialization corruption handling inconsistent — _deserialize_job and _deserialize_worker metadata raise raw JSONDecodeError

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: d0b739b
  date: 2026-06-20
fixed_in:
  - fd6e202ab56948407ae7a0e017da725632d0d9a2
files:
  - path: src/acheron/shell/stores/redis.py
    lines: 178-256
related: [MAINT-002]
```

**Issue.** `_deserialize_capabilities` (redis.py:47-70) wrapped `json.JSONDecodeError` and `KeyError`/`ValueError` into `CacheCorruptedError`. However, `_deserialize_job` called `json.loads(blob)` with no try/except — a corrupt job blob raised raw `JSONDecodeError`. Similarly, `_deserialize_worker` called `json.loads(fields.get('metadata_json', '{}'))` unwrapped — corrupt metadata raised raw `JSONDecodeError`. There were zero tests for corrupt Redis blobs in either store test file.

**Why it matters.** Callers catching `CacheCorruptedError` to handle store corruption (as the streaming executor does for cache) would miss these cases and surface raw `JSONDecodeError` to users. Medium severity because corruption is rare but the inconsistency makes error handling unreliable.

**Recommendation.** Wrap `json.loads(blob)` in `_deserialize_job` and `json.loads(...metadata_json...)` in `_deserialize_worker` in try/except `json.JSONDecodeError` raising `CacheCorruptedError`. Add tests that write a corrupt blob to Redis and assert `CacheCorruptedError` on get/list_all.

**Verification.** `just test`; add a test that writes a corrupt job blob and a corrupt worker metadata blob to Redis and asserts `CacheCorruptedError` is raised on retrieval.

### DATA-003 — Redis store round-trip gaps: PlanStep.batch=True and non-empty metadata untested

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: d0b739b
  date: 2026-06-20
fixed_in:
  - a21fda749f8f278887f14770558ace3a784c1873
files:
  - path: tests/shell/stores/test_redis_worker_store.py
    lines: 99-130
related: [MAINT-002]
```

**Issue.** (1) The `_plan()` helper in test_redis_job_store.py created a `synthesize` PlanStep without `batch=True`, and `test_plan_with_steps_round_trips` never asserted `batch` survives. PlanStep.batch was subsequently removed in e0da69f, resolving the batch concern. (2) No test in test_redis_worker_store.py passed non-empty `metadata` to `register()` — `_tts_caps()` omitted capabilities `metadata` (defaults to `{}`), and no worker-level `metadata` dict was passed. The serialization code handled both but no test verified non-empty values round-trip.

**Why it matters.** If the serialization/deserialization code dropped or mangled `metadata`, no test would catch it. These are real fields on real production paths (metadata carries worker config like model source, version, vram_gb).

**Recommendation.** Add a test asserting non-empty metadata and capabilities.metadata round-trip through Redis serialization, and verify the empty-metadata default is `{}` not None.

**Verification.** Run `just test tests/shell/stores/test_redis_worker_store.py` — the TestMetadataRoundTrip class covers all metadata round-trip cases.

### DATA-004 — Redis store round-trip tests never exercise non-empty worker metadata, leaving a coverage gap for real production values

```yaml
status: verified
severity: medium
effort: S
reviewed_at: a1b11b2
last_verified_at:
  commit: d0b739b
  date: 2026-06-20
fixed_in:
  - a21fda749f8f278887f14770558ace3a784c1873
files:
  - path: tests/shell/stores/test_redis_worker_store.py
    lines: 99-130
related: ['DATA-003']
```

**Issue.** The non-empty worker metadata concern from the original DATA-003 was still valid: every register() call in tests/shell/stores/test_redis_worker_store.py used four positional/keyword arguments and omitted the metadata= kwarg, and _tts_caps() did not set capabilities.metadata. The serialization path in src/acheron/shell/stores/redis.py:77,85 and the deserialization at line 100 both touched this field, but no test ever passed a non-empty dict through them.

**Why it matters.** If the metadata round-trip in _worker_fields/_deserialize_worker were to silently drop, mangle, or re-order keys, no test would catch it. Production paths rely on metadata to carry worker configuration (model source, version, vram_gb, etc.).

**Recommendation.** Add a test that calls store.register with non-empty metadata and capabilities.metadata, and asserts both round-trip correctly.

**Verification.** Run `just test tests/shell/stores/test_redis_worker_store.py`; the TestMetadataRoundTrip class covers worker metadata, capabilities metadata, and empty-metadata-defaults.
