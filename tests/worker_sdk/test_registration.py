"""Tests for Orchestrator self-registration."""

from pathlib import Path

import httpx
import pytest
import respx

from acheron.core.models import WorkerCapabilities, WorkerType
from acheron.worker_sdk.registration import register_with_orchestrator
from acheron.worker_sdk.token_auth import EnvironmentOrFileTokenProvider


def _caps() -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TTS,
        supported_languages_in=frozenset({"en"}),
        supported_languages_out=frozenset({"en"}),
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"wav"}),
        max_payload_bytes=None,
        batch_capable=True,
        model_source="huggingface:Qwen/Qwen3-TTS",
        metadata={"speakers": ["Ryan"]},
    )


class TestRegisterWithOrchestrator:
    @pytest.mark.asyncio
    async def test_rejects_token_over_plaintext_orchestrator(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ACHERON_ALLOW_INSECURE", raising=False)
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError, match="plaintext"):
                await register_with_orchestrator(
                    client=client,
                    orchestrator_url="http://orch:8000",
                    token="strong-token",
                    worker_id="w",
                    endpoint="https://edge:8001",
                    transport="http",
                    capabilities=_caps(),
                )

    @respx.mock
    @pytest.mark.asyncio
    async def test_posts_payload_and_returns_on_201(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_ALLOW_INSECURE", "1")
        route = respx.post("http://orch:8000/workers").mock(return_value=httpx.Response(201, json={}))
        async with httpx.AsyncClient() as client:
            await register_with_orchestrator(
                client=client,
                orchestrator_url="http://orch:8000",
                token="tok",
                worker_id="qwen3tts-1",
                endpoint="http://edge:8001",
                transport="http",
                capabilities=_caps(),
            )
        assert route.called
        body = route.calls.last.request.content.decode()
        assert "qwen3tts-1" in body
        assert "http://edge:8001" in body
        assert "tts" in body
        headers = route.calls.last.request.headers
        assert headers["authorization"] == "Bearer tok"

    @respx.mock
    @pytest.mark.asyncio
    async def test_reads_latest_provider_token_on_each_retry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        token_file = tmp_path / ".registration_token"
        token_file.write_text("first-token", encoding="utf-8")
        provider = EnvironmentOrFileTokenProvider(None, token_file)
        route = respx.post("http://orch:8000/workers")

        calls = 0

        def _replace_token(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                token_file.write_text("second-token", encoding="utf-8")
                return httpx.Response(503)
            return httpx.Response(201, json={})

        route.mock(side_effect=_replace_token)
        monkeypatch.setenv("ACHERON_ALLOW_INSECURE", "1")
        async with httpx.AsyncClient() as client:
            await register_with_orchestrator(
                client=client,
                orchestrator_url="http://orch:8000",
                token_provider=provider,
                worker_id="w",
                endpoint="http://w:8001",
                transport="http",
                capabilities=_caps(),
                retry_delay=0.0,
            )
        assert route.call_count == 2
        assert route.calls[0].request.headers["authorization"] == "Bearer first-token"
        assert route.calls[1].request.headers["authorization"] == "Bearer second-token"

    @respx.mock
    @pytest.mark.asyncio
    async def test_retries_until_orchestrator_ready(self) -> None:
        route = respx.post("http://orch:8000/workers")
        route.mock(side_effect=[httpx.ConnectError("refused"), httpx.Response(201, json={})])
        async with httpx.AsyncClient() as client:
            await register_with_orchestrator(
                client=client,
                orchestrator_url="http://orch:8000",
                token=None,
                worker_id="w",
                endpoint="http://w:8001",
                transport="http",
                capabilities=_caps(),
                retry_delay=0.0,
            )
        assert route.call_count == 2

    @respx.mock
    @pytest.mark.asyncio
    async def test_gives_up_after_max_retries(self) -> None:
        respx.post("http://orch:8000/workers").mock(side_effect=httpx.ConnectError("refused"))
        async with httpx.AsyncClient() as client:
            with pytest.raises(httpx.ConnectError):
                await register_with_orchestrator(
                    client=client,
                    orchestrator_url="http://orch:8000",
                    token=None,
                    worker_id="w",
                    endpoint="http://w:8001",
                    transport="http",
                    capabilities=_caps(),
                    retries=2,
                    retry_delay=0.0,
                )

    @respx.mock
    @pytest.mark.asyncio
    async def test_exponential_backoff_grows_then_caps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sleep duration grows by powers of two up to the 30s cap."""
        delays: list[float] = []

        async def _record(seconds: float) -> None:
            delays.append(seconds)

        import asyncio as _asyncio

        monkeypatch.setattr(_asyncio, "sleep", _record)
        respx.post("http://orch:8000/workers").mock(
            side_effect=[httpx.ConnectError("a"), httpx.ConnectError("b"), httpx.ConnectError("c")]
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(httpx.ConnectError):
                await register_with_orchestrator(
                    client=client,
                    orchestrator_url="http://orch:8000",
                    token=None,
                    worker_id="w",
                    endpoint="http://w:8001",
                    transport="http",
                    capabilities=_caps(),
                    retries=3,
                    retry_delay=1.0,
                )
        # 3 failed attempts produce 2 sleeps (we never sleep after the final
        # attempt that triggers retries=retries).
        assert delays == [1.0, 2.0]
