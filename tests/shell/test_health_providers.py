"""Tests for platform health providers."""

from __future__ import annotations

import httpx
import pytest
import respx

from acheron.core.models import WorkerStatus
from acheron.shell.health_providers import HuggingFaceHealthProvider, RunPodHealthProvider

_RUNPOD_BASE = "https://rest.runpod.io/v1"
_HF_BASE = "https://api.endpoints.huggingface.cloud/v2/endpoints"


class TestRunPodHealthProvider:
    @respx.mock
    @pytest.mark.asyncio
    async def test_endpoint_exists_returns_booting(self) -> None:
        respx.get(f"{_RUNPOD_BASE}/endpoints/ep-1").mock(return_value=httpx.Response(200, json={"id": "ep-1"}))
        provider = RunPodHealthProvider(api_key="rp-key")
        status = await provider.check_status("ep-1")
        assert status == WorkerStatus.BOOTING

    @respx.mock
    @pytest.mark.asyncio
    async def test_endpoint_not_found_returns_offline(self) -> None:
        respx.get(f"{_RUNPOD_BASE}/endpoints/missing").mock(return_value=httpx.Response(404))
        provider = RunPodHealthProvider(api_key="rp-key")
        status = await provider.check_status("missing")
        assert status == WorkerStatus.OFFLINE

    @respx.mock
    @pytest.mark.asyncio
    async def test_network_error_returns_offline(self) -> None:
        respx.get(f"{_RUNPOD_BASE}/endpoints/ep-1").mock(side_effect=httpx.ConnectError("refused"))
        provider = RunPodHealthProvider(api_key="rp-key")
        status = await provider.check_status("ep-1")
        assert status == WorkerStatus.OFFLINE

    @respx.mock
    @pytest.mark.asyncio
    async def test_authorization_header_sent(self) -> None:
        route = respx.get(f"{_RUNPOD_BASE}/endpoints/ep-1").mock(return_value=httpx.Response(200, json={}))
        provider = RunPodHealthProvider(api_key="rp-key")
        await provider.check_status("ep-1")
        assert route.calls.last.request.headers["authorization"] == "Bearer rp-key"


class TestHuggingFaceHealthProvider:
    @respx.mock
    @pytest.mark.asyncio
    async def test_initializing_returns_booting(self) -> None:
        respx.get(f"{_HF_BASE}/my-ns/ep-1").mock(
            return_value=httpx.Response(200, json={"status": {"state": "initializing"}})
        )
        provider = HuggingFaceHealthProvider(api_key="hf-key")
        status = await provider.check_status("my-ns/ep-1")
        assert status == WorkerStatus.BOOTING

    @respx.mock
    @pytest.mark.asyncio
    async def test_starting_returns_booting(self) -> None:
        respx.get(f"{_HF_BASE}/my-ns/ep-1").mock(
            return_value=httpx.Response(200, json={"status": {"state": "starting"}})
        )
        provider = HuggingFaceHealthProvider(api_key="hf-key")
        status = await provider.check_status("my-ns/ep-1")
        assert status == WorkerStatus.BOOTING

    @respx.mock
    @pytest.mark.asyncio
    async def test_running_returns_booting(self) -> None:
        """Platform says running but HTTP probe failed → cold start."""
        respx.get(f"{_HF_BASE}/my-ns/ep-1").mock(
            return_value=httpx.Response(200, json={"status": {"state": "running"}})
        )
        provider = HuggingFaceHealthProvider(api_key="hf-key")
        status = await provider.check_status("my-ns/ep-1")
        assert status == WorkerStatus.BOOTING

    @respx.mock
    @pytest.mark.asyncio
    async def test_paused_returns_offline(self) -> None:
        respx.get(f"{_HF_BASE}/my-ns/ep-1").mock(
            return_value=httpx.Response(200, json={"status": {"state": "paused"}})
        )
        provider = HuggingFaceHealthProvider(api_key="hf-key")
        status = await provider.check_status("my-ns/ep-1")
        assert status == WorkerStatus.OFFLINE

    @respx.mock
    @pytest.mark.asyncio
    async def test_failed_returns_offline(self) -> None:
        respx.get(f"{_HF_BASE}/my-ns/ep-1").mock(
            return_value=httpx.Response(200, json={"status": {"state": "failed"}})
        )
        provider = HuggingFaceHealthProvider(api_key="hf-key")
        status = await provider.check_status("my-ns/ep-1")
        assert status == WorkerStatus.OFFLINE

    @respx.mock
    @pytest.mark.asyncio
    async def test_not_found_returns_offline(self) -> None:
        respx.get(f"{_HF_BASE}/my-ns/missing").mock(return_value=httpx.Response(404))
        provider = HuggingFaceHealthProvider(api_key="hf-key")
        status = await provider.check_status("my-ns/missing")
        assert status == WorkerStatus.OFFLINE

    @respx.mock
    @pytest.mark.asyncio
    async def test_network_error_returns_offline(self) -> None:
        respx.get(f"{_HF_BASE}/my-ns/ep-1").mock(side_effect=httpx.ConnectError("refused"))
        provider = HuggingFaceHealthProvider(api_key="hf-key")
        status = await provider.check_status("my-ns/ep-1")
        assert status == WorkerStatus.OFFLINE

    @respx.mock
    @pytest.mark.asyncio
    async def test_authorization_header_sent(self) -> None:
        route = respx.get(f"{_HF_BASE}/my-ns/ep-1").mock(
            return_value=httpx.Response(200, json={"status": {"state": "running"}})
        )
        provider = HuggingFaceHealthProvider(api_key="hf-key")
        await provider.check_status("my-ns/ep-1")
        assert route.calls.last.request.headers["authorization"] == "Bearer hf-key"
