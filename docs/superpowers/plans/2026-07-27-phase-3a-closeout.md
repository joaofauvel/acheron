# Phase 3a Closeout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Phase 3a runtime simulation deterministic, Compose-backed, spec-compliant, and green across the project and UX-review gates.

**Architecture:** One `runpod-sim` service from `compose/sim.yml` owns the mock RunPod API on `127.0.0.1:8999`. Scenario modules use that service, reset it before execution, and keep their existing in-process Acheron worker/health harnesses. Justfile targets provide the reproducible boot, single-scenario, and all-scenarios interfaces.

**Tech Stack:** Python 3.14, FastAPI, HTTPX, pytest/pytest-asyncio, Docker Compose, Just, uv, Pydantic.

## Global Constraints

- Preserve the Phase 3a scope: runtime harness only; do not fix UX stories or change story lifecycle statuses.
- Keep the mock service at `127.0.0.1:8999`, as specified by `docs/ux_review/SPEC.md §7.6`.
- Keep three scenarios with JSON-oracle assertions: `pricing_outage`, `gpu_switch`, and `cold_start`.
- Use the existing `POST /_admin/reset` endpoint to isolate scenarios; do not add a second state-reset mechanism.
- Run `just lint-strict`, `just type-check`, `just test`, and `just validate` for the project gate, plus `just ux-validate` for the rubric.
- Do not add dependencies; use the existing HTTPX mock transport and pytest fixtures.
- Keep production TLS behavior unchanged; only make the plaintext gRPC test fixture independent of ambient environment variables.
- Use Conventional Commits with a concise scope for each implementation commit.

---

## File Map

| File | Responsibility in this plan |
|---|---|
| `stubs/_sdk_base/mock_runpod.py` | Add the documented endpoint GPU-patch surface and preserve reset semantics. |
| `stubs/tests/test_stubs_healthy.py` | Test GPU patching and reset restoration through the ASGI mock app. |
| `sim/__init__.py` | Define the canonical simulator URL and a typed reset helper. |
| `tests/sim/test_simulation.py` | Unit-test the reset helper and scenario discovery contract. |
| `sim/scenarios/pricing_outage.py` | Use the shared service and reset state before pricing outage assertions. |
| `sim/scenarios/gpu_switch.py` | Use the shared service and documented endpoint patch flow. |
| `sim/scenarios/cold_start.py` | Use the shared service, reset state, and emit a JSON oracle. |
| `Justfile` | Add `sim-run`, make simulator startup standalone, and make bootstrap depend on startup. |
| `sim/scenarios/INDEX.md` | Document scenario references, exercised controls, and JSON assertions. |
| `tests/shell/test_health_monitor.py` | Remove ambient CA variables for the plaintext gRPC fixture. |

---

### Task 1: Align the mock API with the GPU-switch and reset contract

**Files:**
- Modify: `stubs/_sdk_base/mock_runpod.py`
- Test: `stubs/tests/test_stubs_healthy.py`

**Interfaces:**
- Consumes: the existing `state["endpoints"]` map, `POST /_admin/reset`, and existing `GET /endpoints/{endpoint_id}`.
- Produces: `PATCH /endpoints/{endpoint_id}` accepting `{"gpu_id": "NVIDIA A40"}` and updating the endpoint's `gpu_id`; `GET /endpoints/{endpoint_id}` returns the current `gpu_id` so tests and scenarios can observe the state.

- [ ] **Step 1: Write the failing ASGI test for endpoint patch and reset**

Add a test beside `test_mock_runpod_app_starts`:

```python
@pytest.mark.asyncio
async def test_mock_runpod_endpoint_patch_and_reset() -> None:
    from stubs._sdk_base.mock_runpod import make_mock_runpod_app

    app = make_mock_runpod_app({"artifacts": []})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        patched = await client.patch("/endpoints/qwen-edge", json={"gpu_id": "NVIDIA A40"})
        assert patched.status_code == 200
        assert patched.json()["gpu_id"] == "NVIDIA A40"

        current = await client.get("/endpoints/qwen-edge")
        assert current.json()["gpu_id"] == "NVIDIA A40"

        reset = await client.post("/_admin/reset")
        assert reset.status_code == 200

        restored = await client.get("/endpoints/qwen-edge")
        assert restored.json()["gpu_id"] == "NVIDIA L4"
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run:

```bash
uv run pytest stubs/tests/test_stubs_healthy.py::test_mock_runpod_endpoint_patch_and_reset -q
```

Expected: FAIL because `PATCH /endpoints/qwen-edge` is not implemented and the endpoint response does not expose `gpu_id`.

- [ ] **Step 3: Implement the minimal mock API surface**

In `make_mock_runpod_app`:

1. Add `@app.patch("/endpoints/{endpoint_id}")` after the existing endpoint lookup route.
2. Return `404` for unknown or disabled endpoints.
3. Read the JSON body and reject a missing/non-string `gpu_id` with `400`.
4. Update `state["endpoints"][endpoint_id]["gpu_id"]` and return `{"id": endpoint_id, "gpu_id": gpu_id}`.
5. Include `"gpu_id": cfg["gpu_id"]` in the existing successful `GET /endpoints/{endpoint_id}` response.
6. Remove the now-unused `endpoint_gpu` branch from `POST /_admin/control`; the required admin toggles remain `cold_start_ms`, `pricing_api_down`, `endpoint_disabled`, and `fail_next_n`.

- [ ] **Step 4: Run the focused test to verify it passes**

Run:

```bash
uv run pytest stubs/tests/test_stubs_healthy.py::test_mock_runpod_endpoint_patch_and_reset -q
```

Expected: PASS.

- [ ] **Step 5: Commit the mock contract**

```bash
git add stubs/_sdk_base/mock_runpod.py stubs/tests/test_stubs_healthy.py
git commit -m "test(sim): cover endpoint patch and reset"
```

---

### Task 2: Add the canonical simulator client helper

**Files:**
- Modify: `sim/__init__.py`
- Create: `tests/sim/test_simulation.py`

**Interfaces:**
- Consumes: an open `httpx.AsyncClient`.
- Produces: `MOCK_URL: str = "http://127.0.0.1:8999"` and `async def reset_mock(client: httpx.AsyncClient) -> None`, which posts to `MOCK_URL + "/_admin/reset"` and raises for non-2xx responses.

- [ ] **Step 1: Write the failing helper test**

Create `tests/sim/test_simulation.py`:

```python
import httpx
import pytest

from sim import MOCK_URL, reset_mock
from sim.run import discover_scenarios


@pytest.mark.asyncio
async def test_reset_mock_posts_to_canonical_service() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await reset_mock(client)

    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == f"{MOCK_URL}/_admin/reset"


def test_discover_scenarios_returns_the_phase_3a_manifest() -> None:
    assert discover_scenarios() == ["cold_start", "gpu_switch", "pricing_outage"]
```

- [ ] **Step 2: Run the focused tests to verify the new helper test fails**

Run:

```bash
uv run pytest tests/sim/test_simulation.py::test_reset_mock_posts_to_canonical_service -q
```

Expected: FAIL because `MOCK_URL` and `reset_mock` do not exist.

- [ ] **Step 3: Implement the helper**

Add to `sim/__init__.py`:

```python
MOCK_URL = "http://127.0.0.1:8999"


async def reset_mock(client: httpx.AsyncClient) -> None:
    response = await client.post(f"{MOCK_URL}/_admin/reset")
    response.raise_for_status()
```

Keep the helper deliberately client-injected so unit tests do not need Docker or a listening port.

- [ ] **Step 4: Run the focused test to verify it passes**

Run:

```bash
uv run pytest tests/sim/test_simulation.py::test_reset_mock_posts_to_canonical_service -q
```

Expected: PASS.

- [ ] **Step 5: Commit the helper**

```bash
git add sim/__init__.py tests/sim/test_simulation.py
git commit -m "feat(sim): add shared mock reset helper"
```

---

### Task 3: Move all scenarios onto the shared mock service

**Files:**
- Modify: `sim/scenarios/pricing_outage.py`
- Modify: `sim/scenarios/gpu_switch.py`
- Modify: `sim/scenarios/cold_start.py`

**Interfaces:**
- Consumes: `sim.MOCK_URL`, `sim.reset_mock`, and the mock API from Task 1.
- Produces: scenarios that use only port `8999`, reset state before their first assertion, and do not call `start_mock_runpod_in_thread`.

- [ ] **Step 1: Remove private server ownership**

In each scenario:

- Remove `start_mock_runpod_in_thread` imports.
- Remove `MOCK_PORT` declarations.
- Import `MOCK_URL` and `reset_mock` from `sim`.
- Remove each `main()` function's private server startup.
- Remove `DEFAULT_ARTIFACTS` imports if they become unused.

- [ ] **Step 2: Reset state at the beginning of each scenario**

At the beginning of each scenario's `_run` coroutine, before setting any toggles, use:

```python
async with httpx.AsyncClient() as admin:
    await reset_mock(admin)
```

Keep the scenario-specific control calls after the reset. This is especially important for `gpu_switch`, whose endpoint GPU mutation must not leak into a later run.

- [ ] **Step 3: Use the documented GPU endpoint patch**

In `gpu_switch.py`, replace the `endpoint_gpu` admin-toggle call with a direct endpoint patch:

```python
async with httpx.AsyncClient() as admin:
    response = await admin.patch(
        f"{MOCK_URL}/endpoints/qwen-edge",
        json={"gpu_id": "NVIDIA A40"},
    )
    response.raise_for_status()
```

Retain the cache-TTL wait and the JSON metrics assertions for the two submissions.

- [ ] **Step 4: Make the cold-start oracle explicit JSON**

In `cold_start.py`, retain the existing `HealthMonitor` behavior assertion, but collect the two observed states in a JSON-serializable oracle:

```python
oracle = {
    "during_cold_start": status_during_cold_start.value,
    "after_cold_start": status_after_cold_start.value,
}
assert oracle == {"during_cold_start": "BOOTING", "after_cold_start": "HEALTHY"}
print(json.dumps({"scenario": "cold_start", "oracle": oracle}, sort_keys=True))
```

Capture `status_during_cold_start` before the second sleep and `status_after_cold_start` after the second registry read. Preserve the `STORY_REF: MAINT-009` docstring marker.

- [ ] **Step 5: Run static checks on the changed scenario modules**

Run:

```bash
uv run ruff check sim/__init__.py sim/scenarios/pricing_outage.py sim/scenarios/gpu_switch.py sim/scenarios/cold_start.py
uv run basedpyright sim/__init__.py sim/scenarios/pricing_outage.py sim/scenarios/gpu_switch.py sim/scenarios/cold_start.py
```

Expected: PASS with no unused imports, invalid calls, or type errors.

- [ ] **Step 6: Commit the scenario refactor**

```bash
git add sim/__init__.py sim/scenarios/pricing_outage.py sim/scenarios/gpu_switch.py sim/scenarios/cold_start.py
git commit -m "feat(sim): run scenarios against shared mock"
```

---

### Task 4: Make the Compose and Just interfaces match the spec

**Files:**
- Modify: `Justfile`
- Modify: `compose/sim.yml` only if the startup comment or healthcheck needs alignment

**Interfaces:**
- Consumes: the standalone `runpod-sim` service from `compose/sim.yml`.
- Produces: `just runpod-sim`, `just sim-run <scenario>`, and `just runpod-bootstrap` with no dependency on `ACHERON_REGISTRATION_TOKEN`.

- [ ] **Step 1: Verify the current commands fail or use the wrong contract**

Run:

```bash
just --dry-run sim-run pricing_outage
ACHERON_REGISTRATION_TOKEN= docker compose -f docker-compose.yml -f compose/sim.yml config
```

Expected: the first command reports no `sim-run` recipe, and the second command still evaluates the full deployment Compose configuration rather than the standalone simulation file.

- [ ] **Step 2: Update the Justfile recipes**

Replace the simulation recipes with this shape:

```just
# Boot the standalone RunPod mock used by Phase 3a.
runpod-sim:
    docker compose -f compose/sim.yml up -d --build --wait runpod-sim

# Run one Phase 3a scenario. Requires Docker and the runpod-sim service.
sim-run scenario: runpod-sim
    uv run python -m sim.run {{scenario}}

# Run all Phase 3a scenarios. Requires Docker and the runpod-sim service.
runpod-bootstrap: runpod-sim
    uv run python -m sim.run --all
```

Keep the Python module as the implementation entry point; the Just targets are the documented operator/CI interface.

- [ ] **Step 3: Verify command expansion and standalone Compose parsing**

Run:

```bash
just --dry-run sim-run pricing_outage
just --dry-run runpod-bootstrap
docker compose -f compose/sim.yml config
```

Expected:

- `sim-run` expands to standalone simulator startup followed by `uv run python -m sim.run pricing_outage`.
- `runpod-bootstrap` expands to standalone simulator startup followed by `uv run python -m sim.run --all`.
- standalone Compose config succeeds without `ACHERON_REGISTRATION_TOKEN`.

- [ ] **Step 4: Commit the command contract**

```bash
git add Justfile compose/sim.yml

git commit -m "chore(sim): align Compose and Just targets"
```

---

### Task 5: Add the scenario manifest

**Files:**
- Create: `sim/scenarios/INDEX.md`

**Interfaces:**
- Consumes: the three scenario modules and their `STORY_REF` markers.
- Produces: a stable human-readable map of Phase 3a scenarios and their JSON-oracle assertions.

- [ ] **Step 1: Create the manifest**

Write the following table and notes:

```markdown
# Phase 3a scenarios

All scenarios require `just runpod-sim` (or one of the Just targets that depends on it) and use the mock at `http://127.0.0.1:8999`. Each scenario resets the mock before making assertions.

| Scenario | Story reference | Exercise | JSON oracle |
|---|---|---|---|
| `pricing_outage` | `MAINT-002` | Toggle `pricing_api_down`, submit three jobs through the worker app, and restore pricing. | `cost_basis`: `measured` → `cached` → `measured`. |
| `gpu_switch` | `MAINT-002` | Patch `qwen-edge` from the L4 GPU to `NVIDIA A40` and submit before/after the cache TTL. | Implied hourly rate: approximately `$1.39` → `$2.49`. |
| `cold_start` | `MAINT-009` | Exercise the health monitor while the RunPod provider reports a cold endpoint. | `during_cold_start=BOOTING`, then `after_cold_start=HEALTHY`. |
```

Document that story statuses remain unchanged by this harness-only phase.

- [ ] **Step 2: Validate the manifest’s references**

Run:

```bash
rg -n 'STORY_REF: (MAINT-002|MAINT-009)' sim/scenarios/*.py
```

Expected: `pricing_outage.py` and `gpu_switch.py` contain `MAINT-002`; `cold_start.py` contains `MAINT-009`.

- [ ] **Step 3: Commit the manifest**

```bash
git add sim/scenarios/INDEX.md
git commit -m "docs(sim): index Phase 3a scenarios"
```

---

### Task 6: Isolate the plaintext gRPC health fixture from runner TLS settings

**Files:**
- Modify: `tests/shell/test_health_monitor.py`

**Interfaces:**
- Consumes: the existing `grpc_health_server` pytest fixture.
- Produces: plaintext gRPC health tests that do not depend on `SSL_CERT_FILE` or `ACHERON_TLS_CA_FILE` inherited from `uv` or CI.

- [ ] **Step 1: Reproduce the current failure**

Run:

```bash
uv run pytest tests/shell/test_health_monitor.py::TestDefaultHealthCheck -q
```

Expected before the fix: the two healthy-server tests fail with an `AioRpcError` caused by a TLS handshake against the plaintext fixture.

- [ ] **Step 2: Clear CA configuration in the fixture**

Change the fixture signature to accept `monkeypatch` and clear both CA variables before the server is exercised:

```python
@pytest_asyncio.fixture
async def grpc_health_server(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[str]:
    """Start an in-process gRPC server with a HealthServicer that reports healthy."""
    monkeypatch.delenv("ACHERON_TLS_CA_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    server = grpc.aio.server()
```

Do not change `src/acheron/tls.py`; production gRPC trust-store behavior stays covered by its existing TLS tests.

- [ ] **Step 3: Run the focused health tests**

Run:

```bash
uv run pytest tests/shell/test_health_monitor.py::TestDefaultHealthCheck -q
```

Expected: PASS for all tests in `TestDefaultHealthCheck`.

- [ ] **Step 4: Commit the test isolation fix**

```bash
git add tests/shell/test_health_monitor.py
git commit -m "test(health): isolate plaintext grpc fixture"
```

---

### Task 7: Run the complete Phase 3a verification matrix

**Files:**
- No planned source changes; only fix failures directly attributable to Tasks 1–6 before committing a correction.

**Interfaces:**
- Consumes: all Phase 3a implementation and documentation from Tasks 1–6.
- Produces: fresh passing evidence for the project gate, UX rubric validation, standalone simulator startup, every scenario, and the all-scenario runner.

- [ ] **Step 1: Run the project quality gates**

Run in order:

```bash
just lint-strict
just type-check
just test
just validate
just ux-validate
```

Expected: each command exits `0`; `just test` reports no failures and the required coverage threshold is met.

- [ ] **Step 2: Start the standalone simulator**

Run:

```bash
just runpod-sim
docker compose -f compose/sim.yml ps
```

Expected: `runpod-sim` is running and healthy, with port `8999` published.

- [ ] **Step 3: Run every scenario independently**

Run:

```bash
just sim-run pricing_outage
just sim-run gpu_switch
just sim-run cold_start
```

Expected: each command exits `0` and reports its JSON oracle with the expected transition.

- [ ] **Step 4: Run the aggregate bootstrap**

Run:

```bash
just runpod-bootstrap
```

Expected: the runner reports all three scenarios passed in sorted order.

- [ ] **Step 5: Clean up the simulator and inspect the tree**

Run:

```bash
docker compose -f compose/sim.yml down --remove-orphans
git status --short --branch
```

Expected: the simulator is stopped and the only tracked changes are the intended Phase 3a closeout commits; no generated files or unrelated edits remain.

- [ ] **Step 6: Record the verification result**

If all commands pass, report the exact commit range and command results. If Docker or another external prerequisite prevents a scenario, report that prerequisite and do not claim Phase 3a is complete.
