"""Smoke tests for the 7-stub SDK matrix."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from acheron.worker_sdk import WorkerSettings
from acheron.worker_sdk.app import create_worker_app
from stubs._sdk_base import StubASRHandler, StubTranslationHandler, StubTTSHandler


def _settings(worker_id: str, **overrides: object) -> WorkerSettings:
    base: dict[str, object] = {
        "worker_id": worker_id,
        "orchestrator_url": "http://orch:8000",
        "price_source": "zero",
    }
    base.update(overrides)
    return WorkerSettings(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("handler_cls", "worker_id"),
    [
        (StubTTSHandler, "tts-local-stub"),
        (StubTTSHandler, "tts-volume-stub"),
        (StubTTSHandler, "tts-grpc-stub"),
        (StubASRHandler, "asr-local-stub"),
        (StubTranslationHandler, "translation-local-stub"),
    ],
)
@pytest.mark.asyncio
async def test_stub_health(handler_cls: type, worker_id: str) -> None:
    settings = _settings(worker_id)
    h = handler_cls(settings)
    app = create_worker_app(handler=h, settings=settings, disable_registration=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_tts_stub_execute_returns_multipart() -> None:
    settings = _settings("tts-local-stub")
    h = StubTTSHandler(settings)
    app = create_worker_app(handler=h, settings=settings, disable_registration=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/execute",
            json={
                "job_id": "j1",
                "job_type": "tts",
                "payload": {
                    "chapter_id": "ch1",
                    "chunks": [{"text": "hi", "chapter_id": "ch1", "sequence_id": 0}],
                    "target_language": "en",
                },
                "chapter_id": "ch1",
            },
        )
    assert r.status_code == 200
    assert "multipart/mixed" in r.headers["content-type"]


@pytest.mark.asyncio
async def test_tts_stub_capabilities() -> None:
    settings = _settings("tts-local-stub")
    h = StubTTSHandler(settings)
    app = create_worker_app(handler=h, settings=settings, disable_registration=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body["worker_type"] == "tts"


@pytest.mark.asyncio
async def test_asr_stub_execute_returns_text_artifact() -> None:
    settings = _settings("asr-local-stub")
    h = StubASRHandler(settings)
    app = create_worker_app(handler=h, settings=settings, disable_registration=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/execute",
            json={
                "job_id": "j2",
                "job_type": "asr",
                "payload": {"chapter_id": "ch1"},
                "chapter_id": "ch1",
            },
        )
    assert r.status_code == 200
    assert "multipart/mixed" in r.headers["content-type"]


@pytest.mark.asyncio
async def test_translation_stub_execute_returns_text() -> None:
    settings = _settings("translation-local-stub")
    h = StubTranslationHandler(settings)
    app = create_worker_app(handler=h, settings=settings, disable_registration=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/execute",
            json={
                "job_id": "j3",
                "job_type": "translation",
                "payload": {
                    "chapter_id": "ch1",
                    "chunks": [{"text": "hello", "chapter_id": "ch1", "sequence_id": 0}],
                },
                "chapter_id": "ch1",
            },
        )
    assert r.status_code == 200
    assert "multipart/mixed" in r.headers["content-type"]


@pytest.mark.asyncio
async def test_mock_runpod_app_starts() -> None:
    """The RunPod-mock helper returns a working FastAPI app."""
    from stubs._sdk_base.mock_runpod import make_mock_runpod_app

    app = make_mock_runpod_app({"artifacts": []})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/run", json={"input": {}})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_runpod_stub_health() -> None:
    """The tts_runpod_stub and translation_runpod_stub use the same SDK + mock; smoke /health."""
    settings = _settings("tts-runpod-stub", price_source="static", dollars_per_hour=0.69)
    h = StubTTSHandler(settings)
    app = create_worker_app(handler=h, settings=settings, disable_registration=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/health")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_translation_runpod_stub_health() -> None:
    """The translation_runpod_stub is the RunPod variant of the translation stub."""
    settings = _settings("translation-runpod-stub", price_source="static", dollars_per_hour=0.69)
    h = StubTranslationHandler(settings)
    app = create_worker_app(handler=h, settings=settings, disable_registration=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/health")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_tts_volume_stub_health() -> None:
    """The tts_volume_stub uses output_mode=volume and a /data volume."""
    settings = _settings("tts-volume-stub", output_mode="volume", output_volume_dir="/data")
    h = StubTTSHandler(settings)
    app = create_worker_app(handler=h, settings=settings, disable_registration=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/health")
    assert r.status_code == 200
