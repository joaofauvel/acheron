---
branch: code-review-refresh
initial_review_commit: 23c29e1
last_updated_commit: 77aadcd327643367129d4b3874a3c9c217b40084
last_staleness_scan:
  commit: 77aadcd327643367129d4b3874a3c9c217b40084
  date: 2026-06-26
---

# Verification

## TEST — Test discipline

**Grade:** B

TEST-001, TEST-003, TEST-004 remain verified. TEST-002, TEST-005, TEST-006, TEST-007 kept open (code unchanged since 63faed4, gaps remain). One new TEST finding: TEST-008 (low) — `worker_sdk/app._build_price_source` static/runpod-missing-key branches and `_registration_caps` no-op branch have no direct test. Layer 8a added strong 1:1 test coverage for the new `worker_sdk/` (14 test files mirror the 13 source modules) and the qwen3tts worker (`_FakeModel` pattern), but the static-fallback pricing branch and the non-RunPod passthrough metadata assertion are untested. **2026-06-26 refresh**: TEST-018 (low) — `test_app.py` still missing static-without-rate and registration_caps-passthrough tests (TEST-008 fix incomplete, regression of TEST-008). TEST-019 (low) — TestFileArtifact class is undertested relative to TestBytesArtifact (1 test vs 4). TEST-020 (low) — `test_pricing.py` has no tests for `ZeroPrice.refresh()` and `StaticPrice.refresh()` (the no-op contract).

### TEST-001 — local_handlers.py has zero direct unit tests

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
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
  commit: e54458416e9bfe890a473dd9d542978d205b40a1
  date: 2026-06-23
fixed_in: []
files:
  - path: tests/integration/test_worker_integration.py
    lines: 218-237
related: []
```

**Issue.** The test is named `test_orchestrator_works_with_redis_backend` with docstring "Orchestrator can start with ACHERON_STORE_BACKEND=redis (regression for C1)" but: (1) sets `os.environ['ACHERON_STORE_BACKEND'] = 'memory'` (line 169), (2) uses `InMemoryWorkerStore()` (line 171), and (3) never imports or uses RedisJobStore/RedisWorkerStore. The regression it claims to guard (handlers in metadata crashing Redis serialization) is not actually tested against Redis. It also uses raw `os.environ` instead of `monkeypatch`, inconsistent with the rest of the codebase.

**Why it matters.** The test gives false confidence that the Redis backend works with the orchestrator's local-worker registration. A real Redis regression would not be caught. Medium severity — the test name actively misleads reviewers.

**Recommendation.** Either rename to `test_orchestrator_registers_local_workers_without_serializable_handlers` and keep memory, or actually test with RedisJobStore/RedisWorkerStore using the `redis_url` fixture from `tests/shell/stores/conftest.py`. Use `monkeypatch.setenv` instead of `os.environ`.

**Verification.** Run `just test`; if converting to Redis, ensure the test uses the `redis_container` fixture and passes.

### TEST-003 — Tautological assertion in test_get_capabilities_no_translation_worker

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
  commit: dbec2be
  date: 2026-06-23
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

### TEST-005 — `_metadata_str` helper in health.py has no direct unit tests

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
    lines: 38-41
related: []
```

**Issue.** `_metadata_str(worker, key)` has a defensive `isinstance(value, str) else ''` branch (health.py:40-41). The function is exercised transitively by `TestHealthMonitorProviderIntegration` when the metadata dict contains a real string, but no test asserts the type-coercion behavior: passing `metadata={'health_provider': None}` or `{'health_provider': 123}` should return `''` rather than raising or returning the raw value. The helper is small, but it's the single chokepoint the health monitor relies on for reading platform identifiers, and a future refactor that drops the `isinstance` guard would silently pass a non-string to `HealthProviders.get()` and `provider.check_status()`.

**Why it matters.** The branch is the only thing that prevents malformed worker capabilities metadata from crashing the orchestrator at health-check time. A regression here would surface as a `TypeError` from inside the health monitor's failure-handling path, far from the actual cause.

**Recommendation.** Add a small test in `tests/shell/test_health_monitor.py` (or a dedicated `test_metadata_str.py`) that calls `_metadata_str` directly with a `RegisteredWorker` whose `capabilities.metadata` has (a) missing key, (b) `None`, (c) `int`, (d) valid `str`; assert `''`/`''`/`''`/value respectively.

**Verification.** Run `just test tests/shell/test_health_monitor.py`; the new tests should pass without exercising any provider I/O.

### TEST-006 — HuggingFaceHealthProvider.check_status has untested `str` and `else` branches

```yaml
status: open
severity: low
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: 1fbedbc
  date: '2026-06-24'
fixed_in: []
files:
- path: src/acheron/shell/health_providers.py
  lines: 100-111
related: []
```

**Issue.** `HuggingFaceHealthProvider.check_status` (health_providers.py:78-92) has three parsing branches for the response shape: `dict` (`state = status.get('state', '')`), `str` (`state = status_raw`), and `else` (`state = ''`). `tests/shell/test_health_providers.py` only exercises the `dict` branch (5 tests covering initializing/starting/running/paused/failed). The `str` and `else` branches — including the implicit `state == ''` offline fallback — are not tested.

**Why it matters.** The `str` branch is the only defensive handling for an alternative API shape; the `else` branch is the offline fallback. Together they cover all non-dict response shapes. Low-severity because the default behavior is OFFLINE (safe), but the path is undocumented at the test level.

**Recommendation.** Add three tests in `TestHuggingFaceHealthProvider`: (1) `status: 'running'` returns BOOTING, (2) `status: 'paused'` returns OFFLINE, (3) `status: {'state': ''}` returns OFFLINE. All use `respx.mock`.

**Verification.** Run `just test tests/shell/test_health_providers.py`; the new tests should pass against the respx-mocked endpoint.

### TEST-007 — HealthMonitor._handle_failure BOOTING→OFFLINE and OFFLINE→HEALTHY transitions are not covered

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
  - path: src/acheron/shell/health.py
    lines: 133-152
  - path: tests/shell/test_health_monitor.py
    lines: 205-300
related: [CORR-012]
```

**Issue.** `TestHealthMonitorProviderIntegration` covers 5 of the 6 meaningful transitions through `_handle_failure` (health.py:127-152): healthy+BOOTING-provider→BOOTING, healthy+OFFLINE-provider→OFFLINE, healthy+no-provider→OFFLINE, healthy→HEALTHY, healthy+raising-provider→OFFLINE. The two missing transitions are: (1) a worker that's already BOOTING and gets another failure with the provider now returning OFFLINE — should transition to OFFLINE and increment `consecutive_failures`; (2) a worker that's OFFLINE and gets a success probe — should reset to HEALTHY and clear `last_error`.

**Why it matters.** The BOOTING→OFFLINE transition is the production hot path: a cold-starting RunPod/HF endpoint that times out before finishing its cold start. Without a test, a future refactor of the early-return at `health.py:139-140` (`if platform_status == WorkerStatus.BOOTING: ...; return`) would silently leave BOOTING workers stuck in BOOTING state with no failure counter. The OFFLINE→HEALTHY transition is also relevant because the 'success reset' logic was changed in this delta to also clear `status` and `last_error`. Note: the new `test_success_resets_to_healthy` partially mitigates by exercising the success-reset path (BOOTING→HEALTHY), but a literal OFFLINE+success→HEALTHY recovery is still not asserted.

**Recommendation.** Add two tests in `TestHealthMonitorProviderIntegration`: (1) `test_booting_to_offline_transition` — pre-set status BOOTING, mock provider returns OFFLINE on next probe, assert status becomes OFFLINE and `consecutive_failures == 1`; (2) `test_offline_to_healthy_recovery` — pre-set status OFFLINE via `set_worker_status`, mock `health_check` returns `HealthProbeResult(healthy=True)`, assert status becomes HEALTHY and `last_error is None`. Both tests already use `_poll_for` so they fit the existing pattern.

**Verification.** Run `just test tests/shell/test_health_monitor.py`; the new tests should pass under the existing `_poll_for` deadline.

### TEST-008 — `worker_sdk/app._build_price_source` static/runpod-missing-key branches and `_registration_caps` no-op branch have no direct test

```yaml
status: fixed
severity: low
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: 048e5c2
  date: '2026-06-25'
fixed_in: [048e5c2]
files:
- path: src/acheron/worker_sdk/app.py
  lines: 31-51
- path: tests/worker_sdk/test_app.py
  lines: 56-88, 121-152
related: []
```

**Issue.** `_build_price_source` has three branches: 'runpod' with valid keys (RunPodPrice), 'runpod' with missing keys (ZeroPrice with warning, line 35-39), 'static' with dollars_per_hour (StaticPrice), 'static' with missing dollars_per_hour (ZeroPrice with warning, line 47-50), and the default (ZeroPrice, line 51-52). `test_app.py` only exercises the 'runpod'+valid-keys path (test_registration_payload_includes_runpod_health_metadata) and 'zero' (test_factory_exposes_three_routes, test_execute_routes_through_app). The static/zero path is not directly tested. `_registration_caps` returns caps unchanged when settings.price_source != 'runpod' or no endpoint_id (line 69-70); the metadata MUST NOT contain `health_provider`/`health_endpoint_id` keys in that case — currently no test asserts that.

**Why it matters.** A regression in the static-fallback warning (e.g. dropping the `logger.warning` call) or in the `_registration_caps` early-return (e.g. accidentally enriching metadata even when `price_source='static'`) would be invisible to the current test suite. The `/workers` registration payload would silently carry bogus `health_provider` keys, breaking the orchestrator's `RunPodHealthProvider` cold-start detection for workers that opted out of RunPod pricing.

**Recommendation.** Add three tests in `test_app.py`: (1) `test_build_price_source_static_with_rate_returns_static_price`, asserting `StaticPrice` instance + `dollars_per_hour` round-trip; (2) `test_build_price_source_static_without_rate_falls_back_to_zero`, asserting the warning is logged; (3) `test_registration_caps_passthrough_when_not_runpod`, building a `WorkerSettings(price_source='static', ...)` and asserting the registered payload lacks `health_provider` / `health_endpoint_id` keys.

**Verification.** Run `just test tests/worker_sdk/test_app.py` — new tests pass without requiring any external mocking beyond respx for the price refresh path.

## REPRO — Reproducibility

**Grade:** A

REPRO-001 remains open (no fix in this delta; cited code unchanged). REPRO-002 remains verified. One new REPRO finding: REPRO-003 (low) — `tests/worker_sdk/conftest.py` `_no_sleep` fixture masks `asyncio.sleep` timing in retry/registration tests.

### REPRO-001 — Redis list_all() returns non-deterministic order — step_handler worker selection is non-deterministic with Redis backend

```yaml
status: open
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: e123f35
  date: '2026-06-24'
fixed_in: []
files:
- path: src/acheron/shell/stores/redis.py
  lines: 332-341
- path: src/acheron/shell/step_handler.py
  lines: 105-144
- path: tests/integration/test_worker_integration.py
  lines: 280-294
related: []
```

**Issue.** `RedisWorkerStore.list_all()` (redis.py:310) iterates `await self._redis.smembers(_WORKERS_SET)` which returns a `set` — iteration order is non-deterministic. `step_handler.create_step_handler` (step_handler.py:86-121) iterates `registry.list_all()` and picks the first matching worker (`selected = w; break`). With Redis backend and multiple matching workers, which worker is selected is non-deterministic. The integration test `test_multiple_tts_workers_uses_first` asserts `tts_workers[0].worker_id == 'tts-http'` but only uses InMemoryWorkerStore (insertion-ordered), so it does not cover the Redis non-determinism.

**Why it matters.** With Redis backend, two TTS workers supporting the same language could be selected in any order across requests, leading to non-deterministic load distribution and potentially inconsistent results if workers have different models. Medium severity — affects production dispatch determinism, not just tests.

**Recommendation.** Sort `ids` in `RedisWorkerStore.list_all()` (e.g. `sorted(await self._redis.smembers(...))`) and do the same in `RedisJobStore.list_all()`. Add a test asserting deterministic ordering across calls. Alternatively, document that worker selection is arbitrary when multiple match.

**Verification.** Run `just test`; add a test that calls `list_all()` twice on a Redis store with 3+ workers and asserts identical ordering both times.

### REPRO-002 — Health monitor tests rely on timing-based asyncio.sleep windows flake-prone under xdist load

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
  - path: tests/shell/test_health_monitor.py
    lines: 72-145
related: [PERF-001]
```

**Issue.** `test_removes_worker_after_max_failures` starts the monitor with `interval=0.01`, then `await asyncio.sleep(0.15)` and asserts the worker was removed (requires 3 health-check failures). Under `pytest-xdist -n auto` with heavy parallelism, the background task may not get enough event-loop cycles within 0.15s to complete 3 iterations. Similarly, `test_records_success_for_healthy_worker` and `test_records_failure_for_unhealthy_worker` sleep 0.05s and assert the mock was called. There is no synchronization mechanism (e.g. an event the monitor sets after each check) — only sleep-based polling.

**Why it matters.** These tests can flake under CI load, producing false failures. Low severity because the windows are generous relative to the interval, but the pattern is inherently racy.

**Recommendation.** Replace fixed sleeps with a polling loop that checks the assertion condition with a deadline (e.g. poll every 0.01s for up to 1s until `await reg.get('w1') is None`), or expose a test hook (e.g. an `asyncio.Event` set after each `_check_all` cycle) to synchronize.

**Verification.** Run `just test tests/shell/test_health_monitor.py` repeatedly (e.g. `pytest --count=20`) under load to check for flakes.

### REPRO-003 — `tests/worker_sdk/conftest.py` `_no_sleep` fixture masks `asyncio.sleep` timing in retry/registration tests

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
  - path: tests/worker_sdk/conftest.py
    lines: 8-14
  - path: tests/worker_sdk/test_registration.py
    lines: 84-113
related: []
```

**Issue.** `conftest.py` auto-monkeypatches `asyncio.sleep` to a no-op (line 9-13) for all tests under `tests/worker_sdk/`. The intent is to keep retry loops fast, but the override is applied to ALL `asyncio.sleep` calls in the test process — including any backoff inside the SDK code under test. `test_exponential_backoff_grows_then_caps` (test_registration.py:84-113) records the *parameter passed to* `asyncio.sleep` via a side-effect fixture (line 88-95), but does not actually verify the sleep behavior — a regression where someone replaces `await asyncio.sleep(backoff)` with `await asyncio.sleep(0)` would be invisible (the parameter 1.0/2.0/4.0 would still be recorded).

**Why it matters.** The exponential backoff is the production anti-thrash mechanism: a bug that breaks the backoff would cause the edge container to hammer the orchestrator with retries during a degraded network, defeating the purpose of `_MAX_BACKOFF_S`. The current test only verifies the parameter list, not the actual sleep duration in the wild.

**Recommendation.** Either (a) move the monkeypatch to a per-test fixture scoped to only the tests that need instant sleep, leaving retry-timing tests to assert real elapsed time (e.g. with `time.monotonic()` before/after, asserting delta > backoff * 0.9); or (b) replace the conftest auto-patch with a targeted `monkeypatch.setattr(asyncio, 'sleep', _instant)` in the test that needs it. Currently the suite's REPRO-002 fix pattern (polling deadlines) is the right model.

**Verification.** Run `just test tests/worker_sdk/test_registration.py` — existing tests pass either way; the fix is structural, not behavioral.

## DATA — Data quality

**Grade:** A

DATA-001, DATA-002, DATA-003, DATA-004 are all verified. DATA-005 remains open. Two new DATA findings: DATA-006 (medium) — `HttpWorker._parse_multipart` edge cases (no metrics part, missing boundary, non-multipart body) are not covered; DATA-007 (low) — `_runpod_client` output.artifacts-not-list path and FileArtifact stream edge cases lack direct tests.

### DATA-001 — API pydantic schemas accept arbitrary extra fields, silently dropping client typos

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
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
  commit: dbec2be
  date: 2026-06-23
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
  commit: dbec2be
  date: 2026-06-23
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
  commit: dbec2be
  date: 2026-06-23
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

### DATA-005 — RedisWorkerStore._deserialize_worker invalid status field has no corruption test

```yaml
status: fixed
severity: medium
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: 32d67ae
  date: '2026-06-25'
fixed_in: [32d67ae]
files:
  - path: src/acheron/shell/stores/redis.py
    lines: 101-106
  - path: tests/shell/stores/test_redis_worker_store.py
    lines: 100-116, 118-167
related: [DATA-002]
```

**Issue.** `_deserialize_worker` (redis.py:91-108) now parses the persisted `'status'` field and raises `CacheCorruptedError` on `WorkerStatus(status_str)` failure (lines 101-104). This is the symmetric contract test to the existing `test_corrupt_worker_metadata_raises_cache_corrupted` (test_redis_worker_store.py:100-116), which only covers corrupt `metadata_json`. No test writes a worker blob with `status='unknown_state'` (or any non-`WorkerStatus` string) and asserts the deserializer raises `CacheCorruptedError` with a message that includes the invalid status.

**Why it matters.** If a future code change renames or removes a `WorkerStatus` enum value, existing Redis blobs written under the old name would silently round-trip as `CacheCorruptedError` without the new test catching the regression. The corrupt-blob contract test family is the safety net for schema drift on persisted worker state.

**Recommendation.** Add `test_corrupt_worker_status_raises_cache_corrupted` in `TestCorruption`, parallel to the existing metadata test. Use `aioredis.hset` to inject a worker blob with `status='garbage'`, call `store.get('w-bad')`, assert `CacheCorruptedError` with a message containing 'invalid status'.

**Verification.** Run `just test tests/shell/stores/test_redis_worker_store.py`; the new test should pass without requiring any new fixtures.

### DATA-006 — `HttpWorker._parse_multipart` edge cases (no metrics part, missing boundary, non-multipart body) are not covered

```yaml
status: verified
severity: medium
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: a9298e0
  date: 2026-06-24
fixed_in: [a9298e0]
files:
  - path: src/acheron/shell/transports/_multipart.py
    lines: 29-99
  - path: tests/shell/transports/test_http_multipart.py
    lines: 135-160
  - path: tests/shell/transports/test_asr_multipart.py
    lines: 1-233
related: [CORR-013, CORR-028, CORR-031]
```

**Issue.** `HttpWorker._parse_multipart` (http.py:89-140) has three defensive paths that no test exercises: (1) when the worker omits the trailing application/json part, the parser falls through to `metrics = JobMetrics(duration_seconds=0.0)` (line 138-139) — `test_multipart.py`'s `TestBuildResult` and `test_http_worker.py`'s `TestHttpWorkerExecuteMultipart` both build complete bodies; (2) when content-type lacks a `boundary=` parameter, `ctype.split('boundary=', 1)[1]` raises IndexError (line 93); (3) when the body is not actually multipart (e.g. content-type: multipart/mixed but body is plain text), the parser's `is_multipart()` check (line 102-104) raises WorkerError. `test_multipart.py` covers the shared `_multipart` helpers (materialize/safe_join) but not the orchestrator's parser; `test_http_worker.py` covers success + legacy JSON but not these failure paths.

**Why it matters.** A misbehaving worker that returns a bare audio body with no metrics part would silently produce a `JobResult` with `metrics.cost_basis = None` and no `cost_estimate` — indistinguishable from a 'price source not wired' state, but with empty/missing cost data that the dashboard renders as 'Unknown'. A worker returning a body with `content-type: application/json` but `content-type: multipart/mixed` claiming (the worker's HTTPS proxy stripped the real content-type) would crash the orchestrator with IndexError far from the actual cause.

**Recommendation.** Add three tests in `tests/shell/test_http_worker.py` `TestHttpWorkerExecuteMultipart` (or a new `TestHttpWorkerMultipartEdgeCases` class): (1) `test_no_metrics_part_yields_zero_duration` — build a multipart body with only an audio part, assert `metrics.duration_seconds == 0.0` and `metrics.cost_basis is None`; (2) `test_missing_boundary_raises_worker_error` — mock a response with `content-type: multipart/mixed` (no `boundary=`) and assert `WorkerError`; (3) `test_non_multipart_body_raises_worker_error` — mock `content-type: multipart/mixed` with `body=b'not actually multipart'` and assert `WorkerError` with 'not multipart' in the message.

**Verification.** Run `just test tests/shell/test_http_worker.py`; the new tests should pass without any new fixtures (respx-mocked endpoint).

### DATA-007 — `_runpod_client` output.artifacts-not-list path and FileArtifact stream edge cases lack direct tests

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
  - path: src/acheron/worker_sdk/_runpod_client.py
    lines: 88-91
  - path: src/acheron/worker_sdk/artifacts.py
    lines: 71-77
  - path: tests/worker_sdk/test_runpod_client.py
    lines: 1-100
  - path: tests/worker_sdk/test_artifacts.py
    lines: 45-52
related: [CORR-014]
```

**Issue.** Two undertested defensive branches: (1) `_runpod_client.py:89-93` raises `WorkerError` when the RunPod response's `output.artifacts` is not a list. `test_runpod_client.py`'s `test_returns_artifacts_on_success` exercises the happy path; no test writes a non-list `artifacts` value (e.g. dict, string, None) and asserts `WorkerError` with 'must be a list' in the message. (2) `artifacts.py:71-77` — `FileArtifact.stream` reads 64 KiB chunks. `test_artifacts.py:test_stream_reads_from_disk_in_chunks` writes 200 KiB (3 reads); no test for an empty file (zero reads, yields nothing), a 1-byte file (one read < 64 KiB), or a missing/non-existent path (`aiofiles.open` raises `FileNotFoundError` — does it propagate as-is, or get swallowed?).

**Why it matters.** A runpod SDK version bump that changed the output shape from list to dict would crash the edge container with an unhelpful error from deep in the SDK; the test for the type-guard would catch the regression before deploy. `FileArtifact` handling of missing paths is a security boundary — if the path is something the worker passed in via metadata, leaking the raw exception to the orchestrator could reveal filesystem layout.

**Recommendation.** Add three tests: (1) `test_runpod_client.py:test_artifacts_not_list_raises_worker_error`, parametrized over `artifacts = {}`, `'a string'`, `None`, with each raising `WorkerError` match='must be a list'; (2) `test_artifacts.py:test_file_artifact_empty_file_streams_nothing` — write an empty tmp file, collect, assert `== b''`; (3) `test_artifacts.py:test_file_artifact_missing_path_raises_filenotfounderror` — assert `FileNotFoundError` propagates from `stream()` without catching.

**Verification.** Run `just test tests/worker_sdk/test_runpod_client.py tests/worker_sdk/test_artifacts.py`; new tests pass without new dependencies.

### TEST-009 — `test_inputs.py` missing Protocol isinstance, FileInput missing-path, StreamInput empty, and FileInput empty-file edge cases

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
  - path: src/acheron/worker_sdk/inputs.py
    lines: 16-29, 59-74
  - path: tests/worker_sdk/test_inputs.py
    lines: 1-86
related: []
```

**Issue.** `test_inputs.py` covers happy paths and a few defensive cases (frozen dataclass, 64 KiB chunking, metadata defaults) but does not exercise: (1) `isinstance(b, Input)` against the `@runtime_checkable` Protocol — the contract every handler relies on; (2) `FileInput.stream()` on a non-existent path; (3) `StreamInput.stream()` when the producer yields no bytes; (4) `FileInput.stream()` on an empty file.

**Why it matters.** A regression that drops `@runtime_checkable` would silently break `EdgeApp._dispatch` parameter validation. A `FileInput` path-traversal bug would crash inside `aiofiles.open` deep in the handler with no test proving the path was actually opened.

**Recommendation.** Add 4 small tests: `test_isinstance_input`; `test_file_input_missing_path_raises_filenotfounderror`; `test_stream_input_empty_producer_yields_nothing`; `test_file_input_empty_file_streams_nothing`.

**Verification.** `just test tests/worker_sdk/test_inputs.py`.

### TEST-010 — `test_safe_chapter_id.py` missing unicode `chapter_id` coverage

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
    lines: 10-31
  - path: workers/_shared/tests/test_safe_chapter_id.py
    lines: 1-55
related: []
```

**Issue.** Covers blank, whitespace-only, NUL/newline/tab, path-separator, dot/dotdot, length boundaries — but not unicode `chapter_id` values. A regression adding `cid.isascii()` to the check would break valid unicode values like '第1章' or 'café'.

**Why it matters.** The ePUB metadata field accepts arbitrary text; unicode is a legitimate production value. `qwen3tts/handler.py` now delegates to this shared helper, propagating the regression to the existing TTS path.

**Recommendation.** Add `test_unicode_chapter_id_passes` parametrized test asserting `safe_chapter_id('第1章') == '第1章'`, `safe_chapter_id('café') == 'café'`, `safe_chapter_id('Ω') == 'Ω'`.

**Verification.** `just test workers/_shared/tests/test_safe_chapter_id.py`.

### TEST-011 — `test_cloud_audio.py` missing default-content_type and default-metadata branches in `make_runpod_handler`

```yaml
status: open
severity: low
effort: S
reviewed_at: e54458416e9bfe890a473dd9d542978d205b40a1
last_verified_at:
  commit: 7d4754a
  date: '2026-06-24'
fixed_in: []
files:
- path: src/acheron/worker_sdk/cloud.py
  lines: 32, 36, 40
- path: tests/worker_sdk/test_cloud_audio.py
  lines: 1-192
related: []
```

**Issue.** `cloud.py:55` falls back to `'audio/wav'` when content_type is missing; `cloud.py:50` falls back to `{}` when metadata is missing. Neither default-fallback branch is asserted. The `str(None)` cast is also a silent coercion that is never tested.

**Why it matters.** The defaults are the wire contract's forward-compat extension. A change making the handler raise on missing content_type would break older clients; dropping `str(...)` would let `None` propagate as `data: null`.

**Recommendation.** Add 2 tests: `test_input_audio_missing_content_type_defaults_to_audio_wav` and `test_input_audio_missing_metadata_defaults_to_empty_dict`.

**Verification.** `just test tests/worker_sdk/test_cloud_audio.py`.

### TEST-012 — `test_step_handler.py` mutates module-level `default_worker_factory` instead of using `monkeypatch`

```yaml
status: fixed
severity: low
effort: S
reviewed_at: e54458416e9bfe890a473dd9d542978d205b40a1
last_verified_at:
  commit: 156755f
  date: '2026-06-25'
fixed_in: [156755f]
files:
- path: tests/shell/test_step_handler.py
  lines: 293-326
related: []
```

**Issue.** `test_create_step_handler_default_factory_lambda_threads_cache` (lines 336-369) does `sh.default_worker_factory = _capturing_default; try: ...; finally: sh.default_worker_factory = original_default`. If the test raises before the `finally` block, the global is left pointing at the test's mock for the rest of the process.

**Why it matters.** A test failure or pytest-xdist worker crash would leave the module's default factory pointing at a test mock — other tests would silently route through it.

**Recommendation.** Replace with `monkeypatch.setattr('acheron.shell.step_handler.default_worker_factory', _capturing_default)`.

**Verification.** `just test tests/shell/test_step_handler.py`.

### TEST-013 — `test_edge_http.py` and `test_edge_http_multipart.py` don't assert `X-Acheron-Metadata` header construction in `_build_multipart_response`

```yaml
status: fixed
severity: low
effort: S
reviewed_at: e54458416e9bfe890a473dd9d542978d205b40a1
last_verified_at:
  commit: 43ef688
  date: '2026-06-25'
fixed_in: [43ef688]
files:
- path: src/acheron/worker_sdk/_edge_http.py
  lines: 104-114, 156-160
- path: tests/worker_sdk/test_edge_http.py
  lines: 70-86, 211-260
- path: tests/worker_sdk/test_edge_http_multipart.py
  lines: 1-289, 290-378
related:
- CORR-013
```

**Issue.** `_build_multipart_response` emits `X-Acheron-Metadata: {json}` per artifact part (line 108). No test parses the response to confirm the per-part metadata header is present and contains the artifact's metadata dict. CORR-013 already noted the symmetric parser gap; the build-side test is the missing half.

**Why it matters.** A regression dropping the metadata header would be invisible until CORR-013 is fixed, at which point the build-side test becomes load-bearing.

**Recommendation.** Add a test that builds an artifact with `metadata={'sequence_id': 0}`, posts to `/execute`, parses the multipart response, asserts `X-Acheron-Metadata` header contains the dict.

**Verification.** `just test tests/worker_sdk/test_edge_http.py`.

### DATA-008 — `HttpWorker._parse_multipart` response-side edge cases (no metrics part, missing boundary, non-multipart body) still uncovered after Layer 8b test additions

```yaml
status: verified
severity: medium
effort: S
reviewed_at: e54458416e9bfe890a473dd9d542978d205b40a1
last_verified_at:
  commit: a9298e0
  date: 2026-06-24
fixed_in: [a9298e0]
files:
  - path: src/acheron/shell/transports/_multipart.py
    lines: 29-99
  - path: tests/shell/transports/test_http_multipart.py
    lines: 152-160
  - path: tests/shell/transports/test_asr_multipart.py
    lines: 1-233
related: [DATA-006, CORR-013, CORR-028, CORR-031]
```

**Issue.** The new `test_asr_multipart.py` covers the ASR REQUEST side (orchestrator → worker) but NOT the response parser (`_parse_multipart`, http.py:167-218): (1) trailing `application/json` part missing → defaults to `JobMetrics(duration_seconds=0.0)` (line 217); (2) content-type lacks `boundary=` → `ctype.split('boundary=', 1)[1]` raises `IndexError` (line 171); (3) body is not actually multipart → `is_multipart()` returns False → `WorkerError` raised (lines 180-182).

**Why it matters.** A worker returning a bare audio body with no metrics part would silently produce a `JobResult` with `cost_basis=None` and `duration_seconds=0.0`. A worker with no boundary parameter would crash with `IndexError` far from the cause.

**Recommendation.** Add 3 tests: `test_no_metrics_part_yields_zero_duration`; `test_missing_boundary_raises_indexerror` (or change impl to raise `WorkerError`); `test_non_multipart_body_raises_worker_error`.

**Verification.** `just test tests/shell/test_http_worker.py`; coverage of `_parse_multipart` reaches 100%.

## TEST (8c delta)

### TEST-014 — `workers/translategemma/tests/test_handler.py` does not cover the model.generate error path, partial-success, or pad_token_id init

```yaml
status: open
severity: medium
effort: M
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: 0e6c576
  date: '2026-06-24'
fixed_in: []
files:
- path: workers/translategemma/tests/test_handler.py
  lines: 1-269
- path: workers/translategemma/handler.py
  lines: 160-275
related:
- CORR-029
- CORR-033
- MAINT-019
```

**Issue.** test_handler.py (269 lines) covers the validation surface (11 `test_handle_with_*_raises` tests), happy path (single chunk, multi-chunk, empty chunks, empty body), and batched dispatch (10 chunks → 3 batches of [4,4,2]). The handler's GPU-side failure surface is not tested at all: (1) the production line 203 `translated = await asyncio.to_thread(self._translate_all, chunks, src, tgt)` has no try/except — when `_translate_batch` raises (CUDA OOM, NaN/inf in input_ids, processor shape mismatch, etc.) the exception propagates raw to the RunPod runtime; no test asserts that handle() either re-raises as `WorkerError` or wraps with chain. (2) When batch 1 succeeds and batch 2 fails, the handler currently aborts mid-stream, dropping batch 1's translations and producing no partial output — a regression that switched to per-batch exception capture (e.g. to surface partial-success) would be invisible. (3) The tokenizer boot path at handler.py:268-269 `if tokenizer.pad_token_id is None and tokenizer.eos_token_id is not None: tokenizer.pad_token_id = tokenizer.eos_token_id` is a one-shot init; no test exercises it because the test uses `_spy_translate_all` and never builds a real processor. (4) `_translate_all` is fed raw `chunks` whose `text` field is user-controlled ePUB text; a test that proves `text=None` (not str) is rejected by `_normalize_chunk` would be a useful guard.

**Why it matters.** This handler runs in the RunPod serverless runtime — every uncaught exception translates to a billable cold-start. The token-billing and the silent-drop-batch-1 cases are the most operationally expensive untested paths in the new worker layer. A regression that allowed `text` to be a non-str (e.g. a dict the ePUB parser handed back) would crash inside `tokenizer(text=...)` deep in the model.generate call.

**Recommendation.** Add 4 tests: (1) `test_handle_translate_batch_raises_propagates_as_workererror` — patch `_translate_batch` to raise `RuntimeError("CUDA OOM")`; assert `WorkerError` (or `RuntimeError`) propagates and no artifact is built; (2) `test_handle_partial_batch_failure_does_not_drop_successful_batch` — patch `_translate_batch` to raise on the second call only; assert behavior matches the chosen contract (re-raise or partial-output); (3) `test_translate_batch_initializes_pad_token_id_when_none` — instantiate a fake processor whose `tokenizer.pad_token_id is None` and `tokenizer.eos_token_id = 0`; call `_translate_batch` with a tiny batch; assert `tokenizer.pad_token_id == 0` after the call; (4) `test_handle_chunk_text_not_str_raises` — pass `chunks=[{"chapter_id":"ch1","sequence_id":0,"text":None}]`; assert `WorkerError` with 'text is required' (or whichever message the existing `_normalize_chunk` emits).

**Verification.** Run `just test workers/translategemma/tests/test_handler.py`; the 4 new tests should pass without torch/transformers installed (use mock processor / spy).

### TEST-015 — `src/acheron/tls.py` (new top-level module, 114 lines) has no direct unit tests — only subprocess happy-path coverage

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
  - path: src/acheron/tls.py
    lines: 1-109
  - path: tests/integration/test_tls.py
    lines: 176-205
related: [SEC-011, OBS-011]
```

**Issue.** The new top-level `src/acheron/tls.py` module is the single source of TLS configuration for both the orchestrator and the worker SDK (per the moved-out-of-shell import-linter comment at line 1-6). It exports 5 public functions: `uvicorn_ssl_kwargs`, `grpc_server_credentials`, `resolve_ca_path`, `grpc_channel_credentials`, `grpc_channel`, plus the private `_require_pair` and `_allow_insecure` helpers. The 3 tests in `tests/integration/test_tls.py` (now unblocked in this delta — `test_orchestrator_health_over_https`, `test_http_worker_registers_over_https`, `test_grpc_worker_registers`) spawn orchestrator + 2 stub subprocesses with a real CA bundle from `scripts/generate_dev_certs.py` and only assert the happy path: 200 on `/health`, worker ID present in `/workers`. They do NOT exercise: (1) `_require_pair` raising `AcheronError` when only `ACHERON_TLS_CERT_FILE` is set without the key, or vice versa (tls.py:35-37); (2) `uvicorn_ssl_kwargs` logging the WARNING when both env vars are unset and `ACHERON_ALLOW_INSECURE != "1"` (tls.py:50-54); (3) `uvicorn_ssl_kwargs` returning `{}` silently when both are unset and `ACHERON_ALLOW_INSECURE=1`; (4) `resolve_ca_path` falling back from `ACHERON_TLS_CA_FILE` to `SSL_CERT_FILE` (tls.py:79); (5) `grpc_server_credentials` / `grpc_channel_credentials` reading a malformed PEM (e.g. truncated, or a public-key file passed where a cert is expected); (6) `grpc_channel` logging the insecure-fallback WARNING (tls.py:107-112). A regression that dropped the `_allow_insecure()` guard, the warning message, or the SSL_CERT_FILE fallback would be invisible to the integration tests because they always set `ACHERON_ALLOW_INSECURE=1` (test_tls.py:95).

**Why it matters.** This module is a security boundary: SEC-014 / SEC-016 (registration token in cleartext) and SEC-011 (dev-default registration token) are all wired through `orchestrator_url` which the worker connects to over `grpc_channel()`. If a future refactor removes the warning log on insecure fallback, a production deploy with `ACHERON_TLS_CA_FILE` unset would silently send the auto-generated 32-char hex registration token in cleartext (SEC-008 widens). The `_require_pair` symmetric-raise is the only thing preventing a half-configured TLS server from booting with a cert and no key (or vice versa).

**Recommendation.** Add `tests/worker_sdk/test_tls.py` (or a new `tests/core/test_tls.py`) with 8 small tests using `monkeypatch.setenv` and `tmp_path` PEM fixtures: (1) `test_require_pair_raises_when_only_cert_set`; (2) `test_require_pair_raises_when_only_key_set`; (3) `test_require_pair_returns_none_when_both_unset`; (4) `test_uvicorn_ssl_kwargs_returns_empty_dict_with_warning_when_insecure`; (5) `test_uvicorn_ssl_kwargs_returns_empty_dict_silently_when_allow_insecure`; (6) `test_resolve_ca_path_falls_back_from_acheron_tls_ca_file_to_ssl_cert_file`; (7) `test_grpc_server_credentials_raises_on_malformed_pem`; (8) `test_grpc_channel_logs_warning_when_no_ca_and_no_allow_insecure`. For the `caplog` based tests use pytest's `caplog` fixture.

**Verification.** Run `just test tests/worker_sdk/test_tls.py` (or wherever the new file lives); the 8 tests should pass without requiring the gRPC runtime to be exercised (use `caplog.records` to assert warning messages and `monkeypatch.setenv("ACHERON_TLS_CA_FILE", str(bad_pem))` for the malformed-PEM case).

### TEST-016 — `workers/translategemma/tests/test_handler.py:235-241` class-level mutation anti-pattern — second instance of open TEST-012

```yaml
status: fixed
severity: medium
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: 299f08c
  date: '2026-06-25'
fixed_in: [299f08c]
files:
- path: workers/translategemma/tests/test_handler.py
  lines: 223-271
related: [TEST-012]
```

**Issue.** `TestTranslateAll.test_translate_all_chunks_into_batches_of_max_batch_size` and the next test do `original = TranslateGemmaRunpodHandler._translate_batch; TranslateGemmaRunpodHandler._translate_batch = _spy  # type: ignore[method-assign]` BEFORE the try block. If pytest-xdist worker crashes or the test is interrupted between the assignment and the `try`, the class's `_translate_batch` is left pointing at the test's spy. The `try/finally` only covers the `h._translate_all(...)` call, not the assignment. This is the same anti-pattern documented in open TEST-012 (tests/shell/test_step_handler.py:336-369) for `default_worker_factory` — second instance. The same test file's `_spy_translate_all` helper at line 60-70 already uses the correct `monkeypatch.setattr(handler_module.TranslateGemmaRunpodHandler, "_translate_all", _spy)` pattern, so the inconsistency is also a clarity issue within the same file.

**Why it matters.** Class-level state mutation in tests is the most common pytest-xdist silent-leak pattern. A crash in any of the 2 affected tests leaves the handler class broken for every subsequent test in the same process. The `monkeypatch.setattr` pattern (used elsewhere in the same file) is the documented project standard and auto-restores on test teardown.

**Recommendation.** Replace the manual class-level mutation with `monkeypatch.setattr(handler_module.TranslateGemmaRunpodHandler, "_translate_batch", _spy)` (where `handler_module = workers.translategemma.handler`). Drop the `# type: ignore[method-assign]` comments. Optionally accept `monkeypatch` as a test parameter to get the auto-restore.

**Verification.** Run `just test workers/translategemma/tests/test_handler.py::TestTranslateAll` repeatedly under load (e.g. `pytest --count=20 -x`) to confirm no class state leak; observe that the `try/finally` block can be deleted.

### TEST-017 — `tests/integration/test_tls.py` hardcodes 3 repo-relative paths via `Path(__file__).resolve().parents[2]` — new brittleness introduced in this delta

```yaml
status: fixed
severity: medium
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: b37cd7d
  date: '2026-06-25'
fixed_in: [b37cd7d]
files:
- path: tests/integration/test_tls.py
  lines: 70-73, 97, 107, 116
- path: tests/integration/conftest.py
  lines: 339-348
related: [TEST-004, DOC-005]
```

**Issue.** The delta rewires the test_tls.py subprocess env vars from `WORKER_TYPE`/`WORKER_ENDPOINT`/`ORCHESTRATOR_URL`/`WORKER_PORT` (env-only, no filesystem dependence) to `WORKER_CONFIG=str(repo_root / "stubs" / "tts_local_stub" / "worker.yaml")` and the parallel line 121 path for `tts_grpc_stub`. `repo_root` is computed at line 109 as `Path(__file__).resolve().parents[2]` — a hardcoded 2-level parent traversal. The same file also references `scripts/generate_dev_certs.py` (line 73) and `src`/`stubs` for `PYTHONPATH` (lines 97-99) the same way. AGENTS.md hard rule: "Tests shouldn't use repo configuration files or depend on hardcoded project paths, as that makes for brittle tests. Use fixtures (such as conftest modules) and parameterization." Moving `tests/integration/test_tls.py` to a subdirectory (e.g. `tests/integration/tls/`) or moving `stubs/tts_*_stub/worker.yaml` to a shared fixtures dir would silently break the test at the subprocess Popen call. The pre-delta xfailed version had no such file-path dependencies (env vars only).

**Why it matters.** This is the only test in `tests/integration/` that hardcodes repo paths to specific worker.yaml files (verified by `grep -rn 'repo_root\|Path(__file__).resolve().parents' tests/integration/`). It widens the brittleness pattern from one env-config-dependent test family (test_data_dir.py, test_main.py) into a path-dependent test family that breaks on stub relocation. The pattern is exactly what AGENTS.md forbids.

**Recommendation.** Move the `repo_root` computation into a `tests/integration/conftest.py` fixture (`@pytest.fixture(scope="session") def repo_root() -> Path: ...`) so a single fixture source of truth is used. Better: copy `stubs/tts_*_stub/worker.yaml` into `tmp_path` per-test and point `WORKER_CONFIG` at the copy, so the test is hermetic. At minimum, add a comment at line 109 explaining the parent-traversal invariant so future refactors don't silently break it.

**Verification.** Run `just test tests/integration/test_tls.py`; then move `tests/integration/test_tls.py` to `tests/integration/tls/test_orchestrator_health_over_https.py` and observe the fixture-dependent version still passes while the path-hardcoded version would break.

## DATA (8c delta)

### DATA-009 — `tests/core/test_planner.py:TestValidateChunkingFitsWorkers` has no boundary-condition test (==, one-over, max_input_tokens=0, empty caps)

```yaml
status: fixed
severity: medium
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: a4bd73e
  date: '2026-06-25'
fixed_in: [a4bd73e]
files:
- path: tests/core/test_planner.py
  lines: 209-304, 300-340
- path: src/acheron/core/planner.py
  lines: 92-128
related:
- CORR-026
- ARCH-019
- CFG-009
```

**Issue.** The 9 new tests in `TestValidateChunkingFitsWorkers` cover the function's interior well (within-limit, exceeds-limit for TTS and TRANSLATION, unbounded workers, non-text workers, chars_per_token variants, error-message contents, invalid chars_per_token=0 ValueError, multi-worker check). The 4 most production-relevant boundary cases are missing: (1) the equality case `chunking_max_length == max_input_tokens * chars_per_token` — at `max_input_tokens=2048, chars_per_token=4` this is `chunking_max_length=8192` which yields `estimated_tokens=2048`, the comparison `2048 > 2048` is False, so the function should NOT raise. (2) the first-failing-over case: at the same params, `chunking_max_length=8196` yields `2049 > 2048` and SHOULD raise. (3) `max_input_tokens=0` is a degenerate semantic: with `chunking_max_length < chars_per_token`, `estimated_tokens=0`, `0 > 0` is False, so the function silently permits any non-trivial input against a 0-token worker. (4) empty capabilities tuple `caps=()` — the function iterates zero times and returns without raising; semantically correct but not asserted. None of these are covered. The current tests use `max_input_tokens=2048` and `chunking_max_length=9000` (far over the limit) and never probe the boundary.

**Why it matters.** The boundary is the most common production hit: an operator sets `chunking_max_length=8192` to match `max_input_tokens=2048 * chars_per_token=4` thinking it's safe, and the function returns OK. The first-failing value is 8196 (integer-division floor), not 8193 — a regression that switched the comparison to `>=` would fail at 8192 instead of 8196, breaking the boundary contract. The `max_input_tokens=0` case is a misconfiguration that a future tightening of the function (e.g. `if max_input_tokens <= 0: raise`) would silently change the production reject-set.

**Recommendation.** Add 4 tests in `TestValidateChunkingFitsWorkers`: (1) `test_passes_at_exact_equality_boundary` — caps `(max_input_tokens=2048,)`, `chunking_max_length=8192, chars_per_token=4` → no raise; (2) `test_raises_one_over_boundary` — same caps, `chunking_max_length=8196, chars_per_token=4` → `ChunkingTooLongForWorkerError`; (3) `test_zero_max_input_tokens_silently_permits_small_input` — caps `(max_input_tokens=0,)`, `chunking_max_length=1, chars_per_token=4` → no raise (documents the degenerate semantic); (4) `test_empty_capabilities_is_noop` — caps `()`, `chunking_max_length=10_000_000` → no raise.

**Verification.** Run `just test tests/core/test_planner.py::TestValidateChunkingFitsWorkers`; the 4 new tests pass with the current implementation. The first two would catch a regression that changes `>` to `>=` in planner.py:121.

### TEST-018 — test_app.py still missing static-without-rate and registration_caps-passthrough tests (TEST-008 fix incomplete, regression of TEST-008)

```yaml
status: open
severity: low
effort: S
reviewed_at: 77aadcd
last_verified_at:
  commit: 77aadcd
  date: 2026-06-26
fixed_in: []
files:
  - path: src/acheron/worker_sdk/app.py
    lines: 47-50, 69-70
  - path: tests/worker_sdk/test_app.py
    lines: 121-156
related: [TEST-008]
```

**Issue.** TEST-008 (status: fixed) was resolved by adding TestBuildPriceSource covering only 1.5 of the 3 recommended tests: `test_build_price_source_static_with_rate_returns_static_price` (line 124) and `test_build_price_source_runpod_without_api_key_returns_zero_stub` (line 137, a partial-credit test). The two remaining branches are still untested: (1) `test_build_price_source_static_without_rate_falls_back_to_zero` — covers app.py:47-50 (the warning + ZeroPrice fallback when `dollars_per_hour is None`); (2) `test_registration_caps_passthrough_when_not_runpod` — covers app.py:69-70 (the early-return when `price_source != 'runpod'`, asserting the registered payload lacks `health_provider`/`health_endpoint_id` keys). The original concern (a worker that opted out of RunPod silently broadcasting bogus health_provider metadata) is not gated by any test.

**Why it matters.** A regression that dropped the `logger.warning` in app.py:48 or accidentally enriched metadata even when `price_source='static'` would be invisible to the current suite. The orchestrator's RunPodHealthProvider would then look up a non-RunPod worker on the RunPod REST API and surface a confusing 404 to the operator.

**Recommendation.** Add the two missing tests in `tests/worker_sdk/test_app.py`: `test_build_price_source_static_without_rate_falls_back_to_zero` (assert warning logged + ZeroPrice returned) and `test_registration_caps_passthrough_when_not_runpod` (build a `WorkerSettings(price_source='static', runpod_endpoint_id=None)` and assert the registered payload's metadata has no `health_provider` key).

**Verification.** Run `just test tests/worker_sdk/test_app.py` — new tests pass without external mocking.

### TEST-019 — TestFileArtifact class is undertested relative to TestBytesArtifact (1 test vs 4)

```yaml
status: open
severity: low
effort: S
reviewed_at: 77aadcd
last_verified_at:
  commit: 77aadcd
  date: 2026-06-26
fixed_in: []
files:
  - path: src/acheron/worker_sdk/artifacts.py
    lines: 57-73
  - path: tests/worker_sdk/test_artifacts.py
    lines: 45-53
related: []
```

**Issue.** TestBytesArtifact has 4 tests (test_stream_yields_data_once, test_metadata_default_empty, etc.) and TestStreamArtifact has 1; TestFileArtifact (artifacts.py:62-78) has only 1 test: `test_stream_reads_from_disk_in_chunks` (lines 47-52, 200 KiB → 3 reads). The dataclass fields are untested: `metadata` defaulting to `{}` (BytesArtifact has this test at line 28, FileArtifact doesn't), and the `stream()` path on an empty file (zero reads, yields `b''`) and on a missing path (`aiofiles.open` raises FileNotFoundError, line 73). The latter is a security boundary — FileArtifact is the worker-to-orchestrator channel for files on disk and a missing path on the worker's filesystem would surface as a raw exception to the orchestrator.

**Why it matters.** The 200 KiB happy path doesn't probe the loop's empty-read exit (line 76-77) or the `aiofiles.open` failure path (line 73). A refactor that swapped `while True: chunk = await f.read(64 * 1024)` for `async for chunk in f` would be caught by the existing test only if it broke the 3-read case; the 1-byte and 0-byte cases are unasserted. A future commit tightening `FileArtifact.path` to require a `Path` (vs `str | Path`) would not break the 200 KiB test.

**Recommendation.** Add 3 tests in TestFileArtifact: (1) `test_metadata_default_empty` mirroring TestBytesArtifact:28; (2) `test_stream_yields_nothing_on_empty_file` — write an empty tmp file, collect via `_collect`, assert `b''`; (3) `test_stream_raises_filenotfounderror_on_missing_path` — point at a non-existent path, assert `FileNotFoundError` propagates from `stream()` (don't catch).

**Verification.** Run `just test tests/worker_sdk/test_artifacts.py`; the 3 new tests pass with `tmp_path` fixtures only.

### TEST-020 — test_pricing.py has no tests for `ZeroPrice.refresh()` and `StaticPrice.refresh()` (the no-op contract)

```yaml
status: open
severity: low
effort: S
reviewed_at: 77aadcd
last_verified_at:
  commit: 77aadcd
  date: 2026-06-26
fixed_in: []
files:
  - path: src/acheron/worker_sdk/pricing.py
    lines: 41-67
  - path: tests/worker_sdk/test_pricing.py
    lines: 1-50
related: []
```

**Issue.** `ZeroPrice.refresh()` (pricing.py:55-57) returns `True` (no-op). `StaticPrice.refresh()` (pricing.py:71-73) returns `True` (no-op). These are the two no-op branches of the `PriceSource.refresh()` Protocol, both claiming 'no rate lookup needed'. test_pricing.py has 3 tests for the variants (test_returns_zero_with_static_label, test_computes_cost_from_rate, test_zero_gpu_seconds_yields_zero) but zero tests for `refresh()`. The orchestrator lifespan calls `await price_source.refresh()` after construction (app.py lifespan, per the test_lifespan_continues_when_price_refresh_raises_httpx_error test in test_app.py:162-186) — a regression where `StaticPrice.refresh()` started returning `False` would propagate a 'price source not warm' signal to whatever consumes the return value.

**Why it matters.** The contract `refresh() -> bool` (True = warmed, False = not warmed) is a wire-level assertion: a worker reporting `False` from refresh is signalling its lifespan should not claim to be ready. A typo'd return value (e.g. `return gpu_seconds` on a future `StaticPrice.refresh()` that decides to recompute rates) would silently break the lifespan contract. The 4 refresh-related tests in test_runpod_price.py cover only the RunPodPrice path.

**Recommendation.** Add 2 small tests in test_pricing.py: `test_zero_price_refresh_returns_true` and `test_static_price_refresh_returns_true`, each `@pytest.mark.asyncio`, asserting `await ZeroPrice().refresh() is True` and `await StaticPrice(dollars_per_hour=0.69).refresh() is True` respectively. These are the parallel contract tests to test_runpod_price.py's refresh coverage.

**Verification.** Run `just test tests/worker_sdk/test_pricing.py`; the 2 new tests pass without fixtures.

