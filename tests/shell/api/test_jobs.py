"""Tests for job API routes."""

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


def _translation_caps(src: str = "en", dst: str = "es") -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TRANSLATION,
        supported_languages_in=frozenset({src}),
        supported_languages_out=frozenset({dst}),
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"text"}),
        max_payload_bytes=None,
        batch_capable=False,
        model_source=None,
    )


@pytest.fixture
def app(tmp_path):  # type: ignore[no-untyped-def]
    reg = WorkerRegistry()
    reg.register("tts-1", "http://tts", "http", _tts_caps())
    reg.register("trans-1", "http://trans", "http", _translation_caps())
    return create_app(registry=reg, cache=PlanCache(tmp_path), data_dir=tmp_path)


@pytest_asyncio.fixture
async def client(app):  # type: ignore[no-untyped-def]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestJobRoutes:
    @pytest.mark.asyncio
    async def test_submit_job(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": "/input/book.epub",
                "source_language": "en",
                "target_language": "es",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["job_id"].startswith("job-")
        assert data["status"] in ("running", "completed")

    @pytest.mark.asyncio
    async def test_submit_job_invalid_strategy(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": "/input/book.epub",
                "source_language": "en",
                "target_language": "es",
                "executor_strategy": "invalid",
            },
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_submit_job_invalid_source_type(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/jobs",
            json={
                "source_type": "pdf",
                "source_path": "/input/doc.pdf",
                "source_language": "en",
                "target_language": "es",
            },
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_get_job(self, client) -> None:  # type: ignore[no-untyped-def]
        submit = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": "/input/book.epub",
                "source_language": "en",
                "target_language": "es",
            },
        )
        job_id = submit.json()["job_id"]

        response = await client.get(f"/jobs/{job_id}")
        assert response.status_code == 200
        assert response.json()["job_id"] == job_id

    @pytest.mark.asyncio
    async def test_get_job_not_found(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/jobs/nonexistent")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_list_jobs(self, client) -> None:  # type: ignore[no-untyped-def]
        await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": "/input/book.epub",
                "source_language": "en",
                "target_language": "es",
            },
        )
        response = await client.get("/jobs")
        assert response.status_code == 200
        assert len(response.json()["jobs"]) == 1
