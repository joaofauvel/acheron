import httpx
import pytest
from sim.run import discover_scenarios

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
