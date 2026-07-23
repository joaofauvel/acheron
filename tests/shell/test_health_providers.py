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
    async def test_network_error_logs_warning_with_provider_and_endpoint(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """OBS-005: when the network call fails, the provider must emit a
        WARNING that names the provider class and the endpoint id so the
        operator can tell a misconfigured key apart from a transient outage.
        """
        import logging

        respx.get(f"{_RUNPOD_BASE}/endpoints/ep-1").mock(side_effect=httpx.ConnectError("refused"))
        provider = RunPodHealthProvider(api_key="rp-key")
        with caplog.at_level(logging.WARNING, logger="acheron.shell.health_providers"):
            status = await provider.check_status("ep-1")
        assert status == WorkerStatus.OFFLINE
        assert any("RunPodHealthProvider" in r.message and "ep-1" in r.message for r in caplog.records), (
            f"expected warning naming provider+endpoint, got: {[r.message for r in caplog.records]}"
        )

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
    async def test_string_status_returns_booting(self) -> None:
        respx.get(f"{_HF_BASE}/my-ns/ep-1").mock(return_value=httpx.Response(200, json={"status": "running"}))
        provider = HuggingFaceHealthProvider(api_key="hf-key")
        status = await provider.check_status("my-ns/ep-1")
        assert status == WorkerStatus.BOOTING

    @respx.mock
    @pytest.mark.asyncio
    async def test_string_paused_status_returns_offline(self) -> None:
        respx.get(f"{_HF_BASE}/my-ns/ep-1").mock(return_value=httpx.Response(200, json={"status": "paused"}))
        provider = HuggingFaceHealthProvider(api_key="hf-key")
        status = await provider.check_status("my-ns/ep-1")
        assert status == WorkerStatus.OFFLINE

    @respx.mock
    @pytest.mark.asyncio
    async def test_empty_dict_state_returns_offline(self) -> None:
        respx.get(f"{_HF_BASE}/my-ns/ep-1").mock(return_value=httpx.Response(200, json={"status": {"state": ""}}))
        provider = HuggingFaceHealthProvider(api_key="hf-key")
        status = await provider.check_status("my-ns/ep-1")
        assert status == WorkerStatus.OFFLINE

    @respx.mock
    @pytest.mark.asyncio
    async def test_non_string_status_returns_offline(self) -> None:
        respx.get(f"{_HF_BASE}/my-ns/ep-1").mock(return_value=httpx.Response(200, json={"status": None}))
        provider = HuggingFaceHealthProvider(api_key="hf-key")
        status = await provider.check_status("my-ns/ep-1")
        assert status == WorkerStatus.OFFLINE

    @respx.mock
    @pytest.mark.asyncio
    async def test_paused_returns_offline(self) -> None:
        respx.get(f"{_HF_BASE}/my-ns/ep-1").mock(return_value=httpx.Response(200, json={"status": {"state": "paused"}}))
        provider = HuggingFaceHealthProvider(api_key="hf-key")
        status = await provider.check_status("my-ns/ep-1")
        assert status == WorkerStatus.OFFLINE

    @respx.mock
    @pytest.mark.asyncio
    async def test_failed_returns_offline(self) -> None:
        respx.get(f"{_HF_BASE}/my-ns/ep-1").mock(return_value=httpx.Response(200, json={"status": {"state": "failed"}}))
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
    async def test_network_error_logs_warning_with_provider_and_endpoint(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """OBS-005: HF provider must emit a WARNING naming the provider class
        and the endpoint id on network failure.
        """
        import logging

        respx.get(f"{_HF_BASE}/my-ns/ep-1").mock(side_effect=httpx.ConnectError("refused"))
        provider = HuggingFaceHealthProvider(api_key="hf-key")
        with caplog.at_level(logging.WARNING, logger="acheron.shell.health_providers"):
            status = await provider.check_status("my-ns/ep-1")
        assert status == WorkerStatus.OFFLINE
        assert any("HuggingFaceHealthProvider" in r.message and "my-ns/ep-1" in r.message for r in caplog.records), (
            f"expected warning naming provider+endpoint, got: {[r.message for r in caplog.records]}"
        )

    @respx.mock
    @pytest.mark.asyncio
    async def test_authorization_header_sent(self) -> None:
        route = respx.get(f"{_HF_BASE}/my-ns/ep-1").mock(
            return_value=httpx.Response(200, json={"status": {"state": "running"}})
        )
        provider = HuggingFaceHealthProvider(api_key="hf-key")
        await provider.check_status("my-ns/ep-1")
        assert route.calls.last.request.headers["authorization"] == "Bearer hf-key"


class TestHealthProvidersContainer:
    def test_dict_get_returns_provider_by_name(self) -> None:
        providers: dict[str, RunPodHealthProvider] = {"runpod": RunPodHealthProvider(api_key="k")}
        assert isinstance(providers.get("runpod"), RunPodHealthProvider)

    def test_dict_get_unknown_returns_none(self) -> None:
        providers: dict[str, RunPodHealthProvider] = {}
        assert providers.get("runpod") is None


class TestCreateHealthProviders:
    def test_creates_runpod_when_api_key_set(self) -> None:
        from acheron.shell.config import Settings
        from acheron.shell.health_providers import create_health_providers

        settings = Settings()
        settings.providers.runpod.api_key = "rp-key"
        providers = create_health_providers(settings)
        assert isinstance(providers.get("runpod"), RunPodHealthProvider)

    def test_creates_huggingface_when_api_key_set(self) -> None:
        from acheron.shell.config import Settings
        from acheron.shell.health_providers import create_health_providers

        settings = Settings()
        settings.providers.huggingface.api_key = "hf-key"
        providers = create_health_providers(settings)
        assert isinstance(providers.get("huggingface"), HuggingFaceHealthProvider)

    def test_empty_when_no_api_keys(self) -> None:
        from acheron.shell.config import Settings
        from acheron.shell.health_providers import create_health_providers

        providers = create_health_providers(Settings())
        assert providers.get("runpod") is None
        assert providers.get("huggingface") is None

    def test_empty_string_api_key_creates_nothing(self) -> None:
        from acheron.shell.config import Settings
        from acheron.shell.health_providers import create_health_providers

        settings = Settings()
        settings.providers.runpod.api_key = ""
        providers = create_health_providers(settings)
        assert providers.get("runpod") is None
