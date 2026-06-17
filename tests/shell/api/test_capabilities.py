"""Tests for capability discovery route."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from acheron.core.models import WorkerCapabilities, WorkerType
from acheron.shell.api.app import create_app
from acheron.shell.cache import PlanCache
from acheron.shell.registry import WorkerRegistry


def _tts_caps(lang: str = "es") -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TTS,
        supported_languages_in=frozenset({lang}),
        supported_languages_out=frozenset({lang}),
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"wav"}),
        max_payload_bytes=None,
        batch_capable=True,
        model_source=None,
    )


@pytest.fixture
def app(tmp_path):  # type: ignore[no-untyped-def]
    reg = WorkerRegistry()
    reg.register("tts-es", "http://tts-es", "http", _tts_caps("es"))
    reg.register("tts-fr", "http://tts-fr", "http", _tts_caps("fr"))
    return create_app(registry=reg, cache=PlanCache(tmp_path), data_dir=tmp_path)


@pytest_asyncio.fixture
async def client(app):  # type: ignore[no-untyped-def]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestCapabilitiesRoute:
    @pytest.mark.asyncio
    async def test_get_capabilities(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert len(data["language_pairs"]) >= 2

    @pytest.mark.asyncio
    async def test_get_capabilities_filtered_by_dest(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/capabilities", params={"dest": "es"})
        assert response.status_code == 200
        for pair in response.json()["language_pairs"]:
            assert pair["dst"] == "es"
