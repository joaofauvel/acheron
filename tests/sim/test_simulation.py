import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sim.run import discover_scenarios
from stubs._sdk_base.mock_runpod import make_mock_runpod_app

import sim
from acheron.worker_sdk import pricing as pricing_mod
from sim import MOCK_URL, reset_mock


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


@pytest.mark.asyncio
async def test_mock_endpoint_patch_health_and_reset_restore_state() -> None:
    app = make_mock_runpod_app({"artifacts": []})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        patched = await client.patch("/endpoints/qwen-edge", json={"gpu_id": "NVIDIA A40"})
        assert patched.status_code == 200

        health = await client.get("/endpoints/qwen-edge")
        assert health.status_code == 200
        assert health.json() == {"id": "qwen-edge", "status": "ready", "gpu_id": "NVIDIA A40"}

        reset = await client.post("/_admin/reset")
        assert reset.status_code == 200

        restored = await client.get("/endpoints/qwen-edge")
        assert restored.status_code == 200
        assert restored.json() == {"id": "qwen-edge", "status": "ready", "gpu_id": "NVIDIA L4"}


@pytest.mark.asyncio
async def test_reset_mock_best_effort_swallows_cleanup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_reset(client: httpx.AsyncClient) -> None:
        raise RuntimeError("simulator unavailable")

    monkeypatch.setattr(sim, "reset_mock", fail_reset)
    await sim.reset_mock_best_effort()


@pytest.mark.asyncio
async def test_run_async_best_effort_swallows_cleanup_failure() -> None:
    async def fail_cleanup() -> None:
        raise RuntimeError("cleanup failed")

    await sim.run_async_best_effort(fail_cleanup)


def test_patched_transports_preserve_scenario_error_when_restore_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    monkeypatch.setattr(sim, "patch_runpod_endpoint", lambda _: "endpoint-original")
    monkeypatch.setattr(sim, "patch_pricing_transport", lambda _: "pricing-original")

    def fail_endpoint_restore(_: object) -> None:
        events.append("endpoint")
        raise RuntimeError("endpoint restore failed")

    def fail_pricing_restore(_: object) -> None:
        events.append("pricing")
        raise RuntimeError("pricing restore failed")

    monkeypatch.setattr(sim, "restore_runpod_endpoint", fail_endpoint_restore)
    monkeypatch.setattr(sim, "restore_pricing_transport", fail_pricing_restore)

    with pytest.raises(RuntimeError, match="scenario failed"), sim.patched_runpod_transports(MOCK_URL):
        raise RuntimeError("scenario failed")

    assert events == ["pricing", "endpoint"]


def test_patched_transports_restore_partial_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    restored: list[object] = []

    monkeypatch.setattr(sim, "patch_runpod_endpoint", lambda _: "endpoint-original")

    def fail_pricing_patch(_: str) -> object:
        raise RuntimeError("pricing setup failed")

    monkeypatch.setattr(sim, "patch_pricing_transport", fail_pricing_patch)
    monkeypatch.setattr(sim, "restore_runpod_endpoint", restored.append)

    with pytest.raises(RuntimeError, match="pricing setup failed"), sim.patched_runpod_transports(MOCK_URL):
        raise AssertionError("context should not be entered")

    assert len(restored) == 1
    assert callable(restored[0])


def test_patched_transports_restore_mutation_when_patch_setup_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    original_init = pricing_mod.RunPodPrice.__post_init__
    real_patch_pricing = sim.patch_pricing_transport

    def partially_failing_patch(mock_url: str) -> object:
        real_patch_pricing(mock_url)
        raise RuntimeError("pricing setup failed after mutation")

    monkeypatch.setattr(sim, "patch_pricing_transport", partially_failing_patch)
    try:
        with (
            pytest.raises(RuntimeError, match="pricing setup failed after mutation"),
            sim.patched_runpod_transports(MOCK_URL),
        ):
            raise AssertionError("context should not be entered")
        leaked = pricing_mod.RunPodPrice.__post_init__ is not original_init
    finally:
        setattr(pricing_mod.RunPodPrice, sim.POST_INIT_ATTR, original_init)

    assert not leaked


def test_patched_transports_restore_endpoint_mutation_when_patch_setup_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from acheron.worker_sdk import _runpod_client as rpd

    original_open = getattr(rpd, sim.OPEN_ENDPOINT_ATTR)
    real_patch_endpoint = sim.patch_runpod_endpoint

    def partially_failing_patch(mock_url: str) -> object:
        real_patch_endpoint(mock_url)
        raise RuntimeError("endpoint setup failed after mutation")

    monkeypatch.setattr(sim, "patch_runpod_endpoint", partially_failing_patch)
    try:
        with (
            pytest.raises(RuntimeError, match="endpoint setup failed after mutation"),
            sim.patched_runpod_transports(MOCK_URL),
        ):
            raise AssertionError("context should not be entered")
        leaked = getattr(rpd, sim.OPEN_ENDPOINT_ATTR) is not original_open
    finally:
        setattr(rpd, sim.OPEN_ENDPOINT_ATTR, original_open)

    assert not leaked


def test_patched_transports_preserve_setup_error_when_restore_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    original_init = pricing_mod.RunPodPrice.__post_init__
    real_patch_pricing = sim.patch_pricing_transport
    restore_calls: list[object] = []
    setup_error = RuntimeError("pricing setup failed")

    def partially_failing_patch(mock_url: str) -> object:
        real_patch_pricing(mock_url)
        raise setup_error

    def fail_restore(original: object) -> None:
        restore_calls.append(original)
        raise RuntimeError("pricing restore failed")

    monkeypatch.setattr(sim, "patch_pricing_transport", partially_failing_patch)
    monkeypatch.setattr(sim, "restore_pricing_transport", fail_restore)
    try:
        with pytest.raises(RuntimeError) as raised, sim.patched_runpod_transports(MOCK_URL):
            raise AssertionError("context should not be entered")
    finally:
        setattr(pricing_mod.RunPodPrice, sim.POST_INIT_ATTR, original_init)

    assert raised.value is setup_error
    assert len(restore_calls) == 1
