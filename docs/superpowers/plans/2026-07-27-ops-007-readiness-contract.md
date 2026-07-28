# OPS-007 Worker-Fleet Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dashboard's unconditional green `Connected` badge with a worker-fleet readiness badge that distinguishes ready, waiting, and disconnected states.

**Architecture:** The orchestrator's existing `/partials/status` HTML endpoint will inspect registered service workers through `OrchestratorDep`, aggregate healthy/total counts by remote worker type, and render a green `Ready` or yellow `Waiting` badge. Built-in local workers are excluded so their presence cannot falsely imply that GPU capacity is ready. The dashboard remains an HTML proxy; it only gains the yellow-dot style required to render the new response.

**Tech Stack:** Python 3.14, FastAPI, `WorkerStore`/`Orchestrator`, pytest-asyncio with in-process ASGI transport, httpx, Jinja2/HTMX.

## Global Constraints

- Use TDD: write each behavior test before its implementation.
- Add no dependencies and no type ignores.
- Keep tests in-process with `ASGITransport`; do not start a server.
- Preserve the existing HTML partial contract and red `Disconnected` dashboard fallback.
- Count only `WorkerType.ASR`, `WorkerType.TRANSLATION`, and `WorkerType.TTS` as service workers; extraction, chunking, and packaging workers are local prerequisites, not fleet readiness.
- Show yellow when no service workers are registered or when any registered service worker is not healthy.
- Show green only when at least one service worker is registered and every registered service worker is healthy.
- Preserve non-root deployment, TLS, authentication, and Compose behavior.
- Keep the implementation limited to OPS-007; do not implement OPS-006, OPS-019, or MAINT-009 in this plan.
- Final verification must use the repository gates and the OPS-007 UX verifier before the story is marked verified.

---

## File map

- Modify `src/acheron/shell/api/routes/partials.py`: inject the orchestrator, aggregate service-worker readiness, and render the status badge.
- Modify `dashboard/templates/index.html`: define the yellow status-dot style.
- Modify `tests/shell/api/test_partials.py`: expose the in-memory worker store to tests and cover empty, partial, and fully healthy service fleets.
- Modify `dashboard/tests/test_dashboard.py`: verify the dashboard preserves a yellow readiness response and includes its style.
- Modify `docs/ux_review/ops.md`: update OPS-007 status, implementation references, and verification metadata after the final commit.

## Interfaces

The route will consume the existing `Orchestrator.list_workers() -> tuple[RegisteredWorker, ...]` interface and each worker's `capabilities.worker_type` and `status` values.

The route will produce one of these HTML fragments:

```html
<span class="dot dot-green"></span> Ready (tts 3/3)
<span class="dot dot-yellow"></span> Waiting (tts 1/3)
<span class="dot dot-yellow"></span> Waiting for workers (0/0 service workers healthy)
```

The dashboard proxy continues to forward the fragment unchanged. If the orchestrator cannot be reached, it continues to produce:

```html
<span class="dot dot-red"></span> Disconnected
```

### Task 1: Add failing readiness endpoint tests

**Files:**
- Modify: `tests/shell/api/test_partials.py`

**Interfaces:**
- Consumes: `create_app`, `InMemoryWorkerStore`, `WorkerCapabilities`, `WorkerStatus`, and `WorkerType`.
- Produces: integration coverage proving the exact readiness states consumed by the route implementation.

- [ ] **Step 1: Refactor the test lifecycle fixture to expose the worker store**

Replace the single fixture that constructs and owns the client with an `app_context` fixture that yields `(app, registry)` after startup, then add a `client` fixture that builds an `ASGITransport` client from `app_context[0]`. Preserve the existing shutdown call in the fixture finalizer. Add a synchronous `registry` fixture returning `app_context[1]` so tests can register workers and change their health state without using private orchestrator attributes.

- [ ] **Step 2: Add a reusable TTS registration helper**

Add this helper to `tests/shell/api/test_partials.py`:

```python
async def _register_tts(registry: InMemoryWorkerStore, worker_id: str) -> None:
    await registry.register(
        worker_id=worker_id,
        endpoint=f"local://{worker_id}",
        transport="local",
        capabilities=WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"es"}),
            supported_formats_in=frozenset(),
            supported_formats_out=frozenset(),
            max_payload_bytes=None,
            batch_capable=False,
            model_source="test",
        ),
    )
```

- [ ] **Step 3: Replace the unconditional-green test with the empty-fleet test**

Change `test_returns_connected_html` to assert that the started application, which contains only built-in local workers, returns a yellow waiting state:

```python
@pytest.mark.asyncio
async def test_returns_waiting_when_no_service_workers_are_registered(client: AsyncClient) -> None:
    resp = await client.get("/partials/status")
    assert resp.status_code == 200
    assert "dot-yellow" in resp.text
    assert "Waiting for workers" in resp.text
    assert "dot-green" not in resp.text
```

- [ ] **Step 4: Add the partial-fleet test**

Add a test that registers three TTS workers, changes two to `WorkerStatus.BOOTING`, requests the endpoint, and asserts the yellow partial count:

```python
@pytest.mark.asyncio
async def test_returns_waiting_when_some_service_workers_are_unhealthy(
    client: AsyncClient, registry: InMemoryWorkerStore
) -> None:
    for worker_id in ("tts-1", "tts-2", "tts-3"):
        await _register_tts(registry, worker_id)
    await registry.set_worker_status("tts-2", WorkerStatus.BOOTING, "cold start")
    await registry.set_worker_status("tts-3", WorkerStatus.OFFLINE, "probe failed")

    resp = await client.get("/partials/status")
    assert resp.status_code == 200
    assert "dot-yellow" in resp.text
    assert "Waiting" in resp.text
    assert "tts 1/3" in resp.text
```

- [ ] **Step 5: Add the fully-ready test**

Add a test that registers three TTS workers without changing their initial healthy status and asserts:

```python
@pytest.mark.asyncio
async def test_returns_ready_when_all_service_workers_are_healthy(
    client: AsyncClient, registry: InMemoryWorkerStore
) -> None:
    for worker_id in ("tts-1", "tts-2", "tts-3"):
        await _register_tts(registry, worker_id)

    resp = await client.get("/partials/status")
    assert resp.status_code == 200
    assert "dot-green" in resp.text
    assert "Ready" in resp.text
    assert "tts 3/3" in resp.text
```

- [ ] **Step 6: Run the new tests and confirm they fail for the current implementation**

Run:

```bash
uv run pytest --no-cov tests/shell/api/test_partials.py -q
```

Expected: the existing implementation fails the empty-fleet, partial-fleet, and fully-ready assertions because it always returns the green `Connected` fragment.

### Task 2: Implement service-fleet readiness rendering

**Files:**
- Modify: `src/acheron/shell/api/routes/partials.py`

**Interfaces:**
- Consumes: `OrchestratorDep`, `Orchestrator.list_workers()`, `RegisteredWorker.capabilities.worker_type`, and `RegisteredWorker.status`.
- Produces: `_SERVICE_WORKER_TYPES`, `_render_fleet_status(workers)`, and an updated `status_partial(orch: OrchestratorDep)` route.

- [ ] **Step 1: Define the service-worker type boundary**

Add the runtime imports for `WorkerStatus` and `WorkerType`, the dependency import for `OrchestratorDep`, and a type-checking-only import for `RegisteredWorker`. Define:

```python
_SERVICE_WORKER_TYPES = frozenset({WorkerType.ASR, WorkerType.TRANSLATION, WorkerType.TTS})
```

- [ ] **Step 2: Implement the pure aggregation helper**

Add `_render_fleet_status(workers: tuple[RegisteredWorker, ...]) -> str`. Filter to `_SERVICE_WORKER_TYPES`, group by `worker.capabilities.worker_type`, and sort groups by their enum value. For each group, count total workers and workers whose status is `WorkerStatus.HEALTHY`, rendering entries such as `tts 1/3` joined by `, `. Render the exact states below:

```python
if not service_workers:
    return '<span class="dot dot-yellow"></span> Waiting for workers (0/0 service workers healthy)'

if healthy_count == len(service_workers):
    return f'<span class="dot dot-green"></span> Ready ({details})'

return f'<span class="dot dot-yellow"></span> Waiting ({details})'
```

Use only enum values and computed counts in the fragment; no request data is interpolated.

- [ ] **Step 3: Make the route query the orchestrator**

Change the route signature to `async def status_partial(orch: OrchestratorDep) -> HTMLResponse`, call `await orch.list_workers()`, and return `HTMLResponse(_render_fleet_status(workers))`. Update the docstring to describe readiness rather than reachability. Leave connection failures to the dashboard proxy's existing red fallback.

- [ ] **Step 4: Run the focused API tests**

Run:

```bash
uv run pytest --no-cov tests/shell/api/test_partials.py -q
```

Expected: all status partial tests pass, including the empty, partial, and fully healthy service-fleet cases.

### Task 3: Preserve readiness through the dashboard proxy

**Files:**
- Modify: `dashboard/templates/index.html`
- Modify: `dashboard/tests/test_dashboard.py`

**Interfaces:**
- Consumes: the HTML fragment returned by the orchestrator and the dashboard's existing `/partials/status` proxy.
- Produces: a styled `.dot-yellow` indicator and proxy regression coverage.

- [ ] **Step 1: Add the yellow-dot style**

Add this CSS rule beside `.dot-green` and `.dot-red` in `dashboard/templates/index.html`:

```css
.dot-yellow { background: #d29922; }
```

- [ ] **Step 2: Add the dashboard proxy regression test**

Add a test in `TestStatusPartial` that mocks the orchestrator response as `<span class="dot dot-yellow"></span> Waiting (tts 1/3)`, requests `/partials/status`, and asserts both `dot-yellow` and `Waiting (tts 1/3)` are present. Add an index-page assertion that `dot-yellow` is present so the returned state has a visible style.

- [ ] **Step 3: Run the focused dashboard tests**

Run:

```bash
uv run pytest --no-cov dashboard/tests/test_dashboard.py -q
```

Expected: all dashboard tests pass, including disconnected red fallback and yellow readiness forwarding.

### Task 4: Refresh story metadata and run the project gates

**Files:**
- Modify: `docs/ux_review/ops.md`

**Interfaces:**
- Consumes: the final implementation commit hash and the verified readiness behavior.
- Produces: OPS-007 metadata that points to the current implementation and records the fixed status without claiming post-merge verification prematurely.

- [ ] **Step 1: Refresh OPS-007 references**

Update the story's `files` line ranges to the final `partials.py` and dashboard locations. Change `status: open` to `status: fixed`, set `fixed_in: [pending]` until the implementation commit exists, and retain an empty `verified_in` until the post-merge UX verifier records the merged commit.

- [ ] **Step 2: Run lint and type checks**

Run:

```bash
just lint-strict
just type-check
```

Expected: Ruff formatting/checks and mypy complete successfully with no new errors.

- [ ] **Step 3: Run the complete project gate**

Run:

```bash
just validate
just ux-validate
```

Expected: the complete test suite passes, coverage remains above the configured threshold, and the UX rubric validates.

- [ ] **Step 4: Run deployment regression checks**

Run:

```bash
just runpod-bootstrap
just first-run --step 3
```

Expected: simulator bootstrap scenarios pass and the first-run success-criteria step passes without leftover Compose resources.

- [ ] **Step 5: Run the story verifier**

Run:

```bash
just ux-verify OPS-007
```

Expected: OPS-007 verification passes against the current working-tree implementation and reports the intentional pre-merge verification state.

- [ ] **Step 6: Commit the story atomically**

Stage the implementation, tests, dashboard style, and OPS-007 metadata together and commit:

```bash
git add src/acheron/shell/api/routes/partials.py \
  dashboard/templates/index.html \
  tests/shell/api/test_partials.py \
  dashboard/tests/test_dashboard.py \
  docs/ux_review/ops.md
git commit -m "fix(OPS-007): surface worker fleet readiness"
```

- [ ] **Step 7: Resolve the implementation commit reference**

Replace `fixed_in: [pending]` with the hash printed by `git rev-parse HEAD`, then amend the commit without changing its message:

```bash
git rev-parse HEAD
git add docs/ux_review/ops.md
git commit --amend --no-edit
```

- [ ] **Step 8: Request two fresh-context review passes**

Run independent correctness and documentation-staleness reviews against the amended implementation commit. Resolve findings without weakening the readiness contract, then rerun the affected focused tests and the final gates before reporting completion.
