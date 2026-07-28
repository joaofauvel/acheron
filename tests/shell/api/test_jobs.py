"""Tests for job API routes."""

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from httpx import AsyncClient

from acheron.core.models import (
    AudioRequest,
    EpubRequest,
    ExecutorStrategy,
    WorkerCapabilities,
    WorkerStatus,
    WorkerType,
)
from acheron.shell.api.routes.jobs import _booting_tts_warnings
from acheron.shell.registry import RegisteredWorker

if TYPE_CHECKING:
    from acheron.shell.config import Settings
    from acheron.shell.job_store import TrackedJob


def _worker(
    worker_id: str,
    worker_type: WorkerType,
    status: WorkerStatus,
    booting_since: float | None,
) -> RegisteredWorker:
    return RegisteredWorker(
        worker_id=worker_id,
        endpoint="http://worker",
        transport="http",
        capabilities=WorkerCapabilities(
            worker_type=worker_type,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"es"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav"}),
            max_payload_bytes=None,
            batch_capable=True,
            model_source=None,
        ),
        status=status,
        booting_since=booting_since,
    )


class TestBootingTtsWarnings:
    def test_filters_sorts_and_formats_booting_tts_workers(self) -> None:
        workers = (
            _worker("tts-2", WorkerType.TTS, WorkerStatus.BOOTING, 988.5),
            _worker("asr-1", WorkerType.ASR, WorkerStatus.BOOTING, 900.0),
            _worker("tts-1", WorkerType.TTS, WorkerStatus.BOOTING, 996.2),
            _worker("tts-healthy", WorkerType.TTS, WorkerStatus.HEALTHY, None),
            _worker("tts-missing", WorkerType.TTS, WorkerStatus.BOOTING, None),
        )

        assert _booting_tts_warnings(workers, now=1000.0) == [
            "BOOTING TTS workers: tts-1 (3s elapsed), tts-2 (11s elapsed); "
            "cold start typically takes 30\u201390 seconds."
        ]

    @pytest.mark.parametrize(
        ("workers", "now"),
        [
            ((), 1000.0),
            ((_worker("tts-1", WorkerType.TTS, WorkerStatus.HEALTHY, None),), 1000.0),
            ((_worker("asr-1", WorkerType.ASR, WorkerStatus.BOOTING, 900.0),), 1000.0),
            ((_worker("tts-1", WorkerType.TTS, WorkerStatus.BOOTING, None),), 1000.0),
        ],
    )
    def test_omits_non_warning_workers(self, workers: tuple[RegisteredWorker, ...], now: float) -> None:
        assert _booting_tts_warnings(workers, now=now) == []

    def test_clamps_backwards_wall_clock(self) -> None:
        worker = _worker("tts-1", WorkerType.TTS, WorkerStatus.BOOTING, 1001.0)
        assert _booting_tts_warnings((worker,), now=1000.0) == [
            "BOOTING TTS workers: tts-1 (0s elapsed); cold start typically takes 30\u201390 seconds."
        ]


class TestJobRoutes:
    @pytest.mark.asyncio
    async def test_get_job_maps_total_cost_basis(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from httpx import ASGITransport, AsyncClient

        from acheron.core.models import CostBasis, EpubRequest, ExecutorStrategy, PlanResult, PlanStatus
        from acheron.shell.api.app import create_app
        from acheron.shell.cache import PlanCache
        from acheron.shell.job_store import TrackedJob
        from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

        monkeypatch.delenv("ACHERON_REGISTRATION_TOKEN", raising=False)
        monkeypatch.setenv("ACHERON_OPEN_REGISTRATION", "1")
        jobs = InMemoryJobStore()
        app = create_app(
            registry=InMemoryWorkerStore(),
            job_store=jobs,
            cache=PlanCache(tmp_path),
            data_dir=tmp_path,
        )
        await app.state.orchestrator.start()
        await jobs.put(
            TrackedJob(
                job_id="job-measured",
                request=EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
                strategy=ExecutorStrategy.SEQUENTIAL,
                status=PlanStatus.COMPLETED,
                result=PlanResult(
                    plan_id="plan-measured",
                    status=PlanStatus.COMPLETED,
                    completed_steps=1,
                    total_steps=1,
                    outputs=(),
                    total_cost=0.25,
                    total_duration_seconds=1.0,
                    total_cost_basis=CostBasis.MEASURED,
                ),
            )
        )
        transport = ASGITransport(app=app)
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                response = await c.get("/jobs/job-measured")
        finally:
            await app.state.orchestrator.shutdown()
            await app.state.orchestrator.close()
        assert response.status_code == 200
        assert response.json()["total_cost_basis"] == "measured"

    @pytest.mark.asyncio
    async def test_submit_job(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": "input/book.epub",
                "source_language": "en",
                "target_language": "es",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["job_id"].startswith("job-")
        assert data["status"] in ("running", "completed")

    @pytest.mark.asyncio
    async def test_submit_job_executes_end_to_end(self, client) -> None:  # type: ignore[no-untyped-def]
        """Submitted job's background _execute task actually runs.

        The ``client`` fixture starts the orchestrator (registers local
        workers) and uses the test's event loop, so background ``_execute``
        tasks survive. Polls until status changes from ``"running"`` to
        prove the background task ran (it would otherwise stay ``"running"``
        forever). Note: the default workers are fake HTTP endpoints, so
        the final status is typically ``"partial"`` — we just need to see
        the state transition.
        """
        response = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": "input/book.epub",
                "source_language": "en",
                "target_language": "es",
            },
        )
        assert response.status_code == 201
        job_id = response.json()["job_id"]

        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5.0
        status = "running"
        while loop.time() < deadline:
            poll = await client.get(f"/jobs/{job_id}")
            status = poll.json()["status"]
            if status != "running":
                break
            await asyncio.sleep(0.05)
        assert status != "running", "job stayed running: _execute task never ran"

    @pytest.mark.asyncio
    async def test_submit_job_invalid_strategy(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": "input/book.epub",
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
                "source_path": "input/doc.pdf",
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
                "source_path": "input/book.epub",
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
                "source_path": "input/book.epub",
                "source_language": "en",
                "target_language": "es",
            },
        )
        response = await client.get("/jobs")
        assert response.status_code == 200
        assert len(response.json()["jobs"]) == 1

    @pytest.mark.asyncio
    async def test_resume_job_route(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": "input/book.epub",
                "source_language": "en",
                "target_language": "es",
            },
        )
        job_id = response.json()["job_id"]

        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5.0
        while loop.time() < deadline:
            status_resp = await client.get(f"/jobs/{job_id}")
            if status_resp.json()["status"] in ("completed", "failed", "partial"):
                break
            await asyncio.sleep(0.05)

        resume_resp = await client.post(f"/jobs/{job_id}/resume")
        assert resume_resp.status_code == 200
        assert resume_resp.json()["status"] == "running"

    @pytest.mark.asyncio
    async def test_submit_job_unsupported_language(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """Test that submitting a job with unsupported language returns 422."""
        from httpx import ASGITransport, AsyncClient

        from acheron.shell.api.app import create_app
        from acheron.shell.cache import PlanCache
        from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

        monkeypatch.delenv("ACHERON_REGISTRATION_TOKEN", raising=False)
        monkeypatch.setenv("ACHERON_OPEN_REGISTRATION", "1")
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "book.epub").write_bytes(b"epub-fixture-bytes")
        app = create_app(
            registry=InMemoryWorkerStore(),
            job_store=InMemoryJobStore(),
            cache=PlanCache(tmp_path),
            data_dir=tmp_path,
        )
        await app.state.orchestrator.start()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            response = await c.post(
                "/jobs",
                json={
                    "source_type": "epub",
                    "source_path": "input/book.epub",
                    "source_language": "en",
                    "target_language": "xx",
                },
            )
            assert response.status_code == 422
            assert response.json()["detail"].startswith("InvalidLanguagePathError:")

    @pytest.mark.asyncio
    async def test_submit_job_rejects_extra_fields(self, client) -> None:  # type: ignore[no-untyped-def]
        """SubmitJobRequest must reject unknown fields so client typos fail loudly."""
        response = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": "input/book.epub",
                "source_language": "en",
                "target_language": "es",
                "executor_strategi": "streaming",  # typo: missing 'y'
            },
        )
        assert response.status_code == 422
        body = response.json()
        assert any("executor_strategi" in str(d).lower() for d in body.get("detail", []))

    @pytest.mark.asyncio
    async def test_submit_job_returns_booting_tts_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from httpx import ASGITransport, AsyncClient

        from acheron.core.models import WorkerCapabilities, WorkerType
        from acheron.shell.api.app import create_app
        from acheron.shell.cache import PlanCache
        from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

        monkeypatch.delenv("ACHERON_REGISTRATION_TOKEN", raising=False)
        monkeypatch.setenv("ACHERON_OPEN_REGISTRATION", "1")
        registry = InMemoryWorkerStore()
        capabilities = WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset({"es"}),
            supported_languages_out=frozenset({"es"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav"}),
            max_payload_bytes=None,
            batch_capable=True,
            model_source=None,
        )
        await registry.register("tts-1", "http://tts", "http", capabilities)
        await registry.register(
            "trans-1",
            "http://translation",
            "http",
            replace(
                capabilities,
                worker_type=WorkerType.TRANSLATION,
                supported_languages_in=frozenset({"en"}),
                supported_languages_out=frozenset({"es"}),
                supported_formats_out=frozenset({"text"}),
                batch_capable=False,
            ),
        )
        worker = await registry.get("tts-1")
        assert worker is not None
        worker.status = WorkerStatus.BOOTING
        worker.booting_since = 995.0
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "book.epub").write_bytes(b"epub-fixture-bytes")
        app = create_app(
            registry=registry,
            job_store=InMemoryJobStore(),
            cache=PlanCache(tmp_path),
            data_dir=tmp_path,
        )
        await app.state.orchestrator.start()
        monkeypatch.setattr("acheron.shell.api.routes.jobs.time.time", lambda: 1000.0)
        transport = ASGITransport(app=app)
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                response = await c.post(
                    "/jobs",
                    json={
                        "source_type": "epub",
                        "source_path": "input/book.epub",
                        "source_language": "en",
                        "target_language": "es",
                    },
                )
        finally:
            await app.state.orchestrator.shutdown()
            await app.state.orchestrator.close()

        assert response.status_code == 201
        assert response.json()["job_id"].startswith("job-")
        assert response.json()["status"] in ("running", "completed")
        assert response.json()["warnings"] == [
            "BOOTING TTS workers: tts-1 (5s elapsed); cold start typically takes 30\u201390 seconds."
        ]

    @pytest.mark.asyncio
    async def test_submit_job_warning_inspection_failure_is_non_gating(self, tmp_path: Path) -> None:
        from typing import cast

        from acheron.core.models import PlanStatus
        from acheron.shell.api.routes import jobs as jobs_route
        from acheron.shell.api.schemas import SubmitJobRequest
        from acheron.shell.config import Settings
        from acheron.shell.job_store import TrackedJob
        from acheron.shell.orchestrator import Orchestrator

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "book.epub").write_bytes(b"epub-fixture-bytes")
        settings = Settings()
        settings.orchestrator.data_dir = tmp_path

        class FailingInspectionOrchestrator:
            def __init__(self) -> None:
                self.settings = settings

            async def submit_job(self, request: EpubRequest, strategy: ExecutorStrategy) -> TrackedJob:
                return TrackedJob(
                    job_id="job-accepted",
                    request=request,
                    strategy=strategy,
                    status=PlanStatus.RUNNING,
                )

            async def list_workers(self) -> tuple[RegisteredWorker, ...]:
                raise RuntimeError("registry unavailable")

        result = await jobs_route.submit_job(
            SubmitJobRequest(
                source_type="epub",
                source_path="input/book.epub",
                source_language="en",
                target_language="es",
            ),
            cast("Orchestrator", FailingInspectionOrchestrator()),
            None,
        )
        assert result.status is PlanStatus.RUNNING
        assert result.warnings == []


class TestJobRouteAuth:
    """SEC-005: mutating job routes require auth when no open-registration flag is set."""

    @pytest.mark.asyncio
    async def test_submit_job_rejected_without_token_when_token_set(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When ACHERON_REGISTRATION_TOKEN is set and open_registration is not, POST /jobs returns 401."""
        from httpx import ASGITransport, AsyncClient

        from acheron.shell.api.app import create_app
        from acheron.shell.cache import PlanCache
        from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

        monkeypatch.setenv("ACHERON_REGISTRATION_TOKEN", "x" * 64)
        monkeypatch.delenv("ACHERON_OPEN_REGISTRATION", raising=False)
        app = create_app(
            registry=InMemoryWorkerStore(),
            job_store=InMemoryJobStore(),
            cache=PlanCache(tmp_path),
            data_dir=tmp_path,
        )
        await app.state.orchestrator.start()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            response = await c.post(
                "/jobs",
                json={
                    "source_type": "epub",
                    "source_path": "/input/book.epub",
                    "source_language": "en",
                    "target_language": "es",
                },
            )
        await app.state.orchestrator.shutdown()
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_resume_job_rejected_without_token_when_token_set(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When ACHERON_REGISTRATION_TOKEN is set, POST /jobs/{id}/resume returns 401."""
        from httpx import ASGITransport, AsyncClient

        from acheron.shell.api.app import create_app
        from acheron.shell.cache import PlanCache
        from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

        monkeypatch.setenv("ACHERON_REGISTRATION_TOKEN", "x" * 64)
        monkeypatch.delenv("ACHERON_OPEN_REGISTRATION", raising=False)
        app = create_app(
            registry=InMemoryWorkerStore(),
            job_store=InMemoryJobStore(),
            cache=PlanCache(tmp_path),
            data_dir=tmp_path,
        )
        await app.state.orchestrator.start()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            response = await c.post("/jobs/j-1/resume")
        await app.state.orchestrator.shutdown()
        assert response.status_code == 401


class _RecordingOrchestrator:
    """Spy orchestrator that records ``submit_job`` calls without running a plan.

    Uses an injected ``settings`` so the route's source-path preflight
    can resolve a real fixture file below ``tmp_path``. ``submit_job``
    is replaced with a no-op that returns a fresh :class:`TrackedJob`
    so the route exercises the "acceptance" path.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.submit_calls: list[tuple[EpubRequest | AudioRequest, ExecutorStrategy]] = []
        self.list_workers_calls = 0

    async def submit_job(
        self,
        request: EpubRequest | AudioRequest,
        strategy: ExecutorStrategy,
    ) -> TrackedJob:
        from acheron.core.models import PlanStatus
        from acheron.shell.job_store import TrackedJob

        self.submit_calls.append((request, strategy))
        return TrackedJob(
            job_id="job-accepted",
            request=request,
            strategy=strategy,
            status=PlanStatus.RUNNING,
        )

    async def list_workers(self) -> tuple[RegisteredWorker, ...]:
        self.list_workers_calls += 1
        return ()


class TestJobRoutePreflight:
    """Submission preflight: source-path resolution + ASR model checks before orchestrator.submit_job()."""

    @pytest.mark.asyncio
    async def test_missing_source_path_returns_422_and_never_calls_submit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from httpx import ASGITransport, AsyncClient

        from acheron.shell.api.app import create_app
        from acheron.shell.cache import PlanCache
        from acheron.shell.config import Settings
        from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

        monkeypatch.delenv("ACHERON_REGISTRATION_TOKEN", raising=False)
        monkeypatch.setenv("ACHERON_OPEN_REGISTRATION", "1")
        settings = Settings()
        settings.orchestrator.data_dir = tmp_path
        spy = _RecordingOrchestrator(settings)
        app = create_app(
            registry=InMemoryWorkerStore(),
            job_store=InMemoryJobStore(),
            cache=PlanCache(tmp_path),
            data_dir=tmp_path,
            settings=settings,
        )
        # Replace the orchestrator with our spy after create_app so create_app's
        # construction does not run plan compilation on the spy.
        await app.state.orchestrator.shutdown()
        await app.state.orchestrator.close()
        app.state.orchestrator = spy
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            response = await c.post(
                "/jobs",
                json={
                    "source_type": "epub",
                    "source_path": "missing.epub",
                    "source_language": "en",
                    "target_language": "es",
                },
            )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "source_path" in detail
        assert "expected at" in detail
        assert "missing.epub" in detail
        assert spy.submit_calls == []

    @pytest.mark.asyncio
    async def test_traversal_source_path_returns_422(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from httpx import ASGITransport, AsyncClient

        from acheron.shell.api.app import create_app
        from acheron.shell.cache import PlanCache
        from acheron.shell.config import Settings
        from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

        monkeypatch.delenv("ACHERON_REGISTRATION_TOKEN", raising=False)
        monkeypatch.setenv("ACHERON_OPEN_REGISTRATION", "1")
        settings = Settings()
        settings.orchestrator.data_dir = tmp_path
        spy = _RecordingOrchestrator(settings)
        app = create_app(
            registry=InMemoryWorkerStore(),
            job_store=InMemoryJobStore(),
            cache=PlanCache(tmp_path),
            data_dir=tmp_path,
            settings=settings,
        )
        await app.state.orchestrator.shutdown()
        await app.state.orchestrator.close()
        app.state.orchestrator = spy
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            response = await c.post(
                "/jobs",
                json={
                    "source_type": "epub",
                    "source_path": "../outside.epub",
                    "source_language": "en",
                    "target_language": "es",
                },
            )
        assert response.status_code == 422
        detail = response.json()["detail"]
        # relative-path error mentions the source path and the data dir
        assert "../outside.epub" in detail
        assert str(tmp_path) in detail
        assert spy.submit_calls == []

    @pytest.mark.asyncio
    async def test_absolute_source_path_returns_422(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from httpx import ASGITransport, AsyncClient

        from acheron.shell.api.app import create_app
        from acheron.shell.cache import PlanCache
        from acheron.shell.config import Settings
        from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

        monkeypatch.delenv("ACHERON_REGISTRATION_TOKEN", raising=False)
        monkeypatch.setenv("ACHERON_OPEN_REGISTRATION", "1")
        settings = Settings()
        settings.orchestrator.data_dir = tmp_path
        spy = _RecordingOrchestrator(settings)
        app = create_app(
            registry=InMemoryWorkerStore(),
            job_store=InMemoryJobStore(),
            cache=PlanCache(tmp_path),
            data_dir=tmp_path,
            settings=settings,
        )
        await app.state.orchestrator.shutdown()
        await app.state.orchestrator.close()
        app.state.orchestrator = spy
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            response = await c.post(
                "/jobs",
                json={
                    "source_type": "epub",
                    "source_path": "/tmp/book.epub",
                    "source_language": "en",
                    "target_language": "es",
                },
            )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "/tmp/book.epub" in detail
        assert spy.submit_calls == []

    @pytest.mark.asyncio
    async def test_directory_source_path_returns_422(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from httpx import ASGITransport, AsyncClient

        from acheron.shell.api.app import create_app
        from acheron.shell.cache import PlanCache
        from acheron.shell.config import Settings
        from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

        monkeypatch.delenv("ACHERON_REGISTRATION_TOKEN", raising=False)
        monkeypatch.setenv("ACHERON_OPEN_REGISTRATION", "1")
        (tmp_path / "inputs").mkdir()
        (tmp_path / "inputs" / "a-dir").mkdir()
        settings = Settings()
        settings.orchestrator.data_dir = tmp_path
        spy = _RecordingOrchestrator(settings)
        app = create_app(
            registry=InMemoryWorkerStore(),
            job_store=InMemoryJobStore(),
            cache=PlanCache(tmp_path),
            data_dir=tmp_path,
            settings=settings,
        )
        await app.state.orchestrator.shutdown()
        await app.state.orchestrator.close()
        app.state.orchestrator = spy
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            response = await c.post(
                "/jobs",
                json={
                    "source_type": "epub",
                    "source_path": "inputs/a-dir",
                    "source_language": "en",
                    "target_language": "es",
                },
            )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "source_path" in detail
        assert "expected at" in detail
        assert "inputs/a-dir" in detail
        assert spy.submit_calls == []

    @pytest.mark.asyncio
    async def test_symlink_escape_returns_422(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from httpx import ASGITransport, AsyncClient

        from acheron.shell.api.app import create_app
        from acheron.shell.cache import PlanCache
        from acheron.shell.config import Settings
        from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

        monkeypatch.delenv("ACHERON_REGISTRATION_TOKEN", raising=False)
        monkeypatch.setenv("ACHERON_OPEN_REGISTRATION", "1")
        outside = tmp_path.parent / f"outside-{tmp_path.name}.epub"
        outside.write_bytes(b"outside")
        try:
            inputs_subdir = tmp_path / "inputs" / "abc"
            inputs_subdir.mkdir(parents=True)
            (inputs_subdir / "link.epub").symlink_to(outside)
            settings = Settings()
            settings.orchestrator.data_dir = tmp_path
            spy = _RecordingOrchestrator(settings)
            app = create_app(
                registry=InMemoryWorkerStore(),
                job_store=InMemoryJobStore(),
                cache=PlanCache(tmp_path),
                data_dir=tmp_path,
                settings=settings,
            )
            await app.state.orchestrator.shutdown()
            await app.state.orchestrator.close()
            app.state.orchestrator = spy
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                response = await c.post(
                    "/jobs",
                    json={
                        "source_type": "epub",
                        "source_path": "inputs/abc/link.epub",
                        "source_language": "en",
                        "target_language": "es",
                    },
                )
        finally:
            outside.unlink(missing_ok=True)
        assert response.status_code == 422
        assert spy.submit_calls == []

    @pytest.mark.asyncio
    async def test_audio_without_asr_model_returns_422(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from httpx import ASGITransport, AsyncClient

        from acheron.shell.api.app import create_app
        from acheron.shell.cache import PlanCache
        from acheron.shell.config import Settings
        from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

        monkeypatch.delenv("ACHERON_REGISTRATION_TOKEN", raising=False)
        monkeypatch.setenv("ACHERON_OPEN_REGISTRATION", "1")
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "book.mp3").write_bytes(b"mp3")
        settings = Settings()
        settings.orchestrator.data_dir = tmp_path
        spy = _RecordingOrchestrator(settings)
        app = create_app(
            registry=InMemoryWorkerStore(),
            job_store=InMemoryJobStore(),
            cache=PlanCache(tmp_path),
            data_dir=tmp_path,
            settings=settings,
        )
        await app.state.orchestrator.shutdown()
        await app.state.orchestrator.close()
        app.state.orchestrator = spy
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            response = await c.post(
                "/jobs",
                json={
                    "source_type": "audio",
                    "source_path": "input/book.mp3",
                    "source_language": "en",
                    "target_language": "es",
                },
            )
        assert response.status_code == 422
        assert response.json()["detail"] == "asr_model is required for source_type='audio'"
        assert spy.submit_calls == []

    @pytest.mark.asyncio
    async def test_epub_with_asr_model_returns_422(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from httpx import ASGITransport, AsyncClient

        from acheron.shell.api.app import create_app
        from acheron.shell.cache import PlanCache
        from acheron.shell.config import Settings
        from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

        monkeypatch.delenv("ACHERON_REGISTRATION_TOKEN", raising=False)
        monkeypatch.setenv("ACHERON_OPEN_REGISTRATION", "1")
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "book.epub").write_bytes(b"epub")
        settings = Settings()
        settings.orchestrator.data_dir = tmp_path
        spy = _RecordingOrchestrator(settings)
        app = create_app(
            registry=InMemoryWorkerStore(),
            job_store=InMemoryJobStore(),
            cache=PlanCache(tmp_path),
            data_dir=tmp_path,
            settings=settings,
        )
        await app.state.orchestrator.shutdown()
        await app.state.orchestrator.close()
        app.state.orchestrator = spy
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            response = await c.post(
                "/jobs",
                json={
                    "source_type": "epub",
                    "source_path": "input/book.epub",
                    "source_language": "en",
                    "target_language": "es",
                    "asr_model": "whisper-v3",
                },
            )
        assert response.status_code == 422
        assert response.json()["detail"] == "asr_model is only valid for source_type='audio'"
        assert spy.submit_calls == []

    @pytest.mark.asyncio
    async def test_valid_relative_path_passes_resolved_absolute_to_submit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from httpx import ASGITransport, AsyncClient

        from acheron.shell.api.app import create_app
        from acheron.shell.cache import PlanCache
        from acheron.shell.config import Settings
        from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

        monkeypatch.delenv("ACHERON_REGISTRATION_TOKEN", raising=False)
        monkeypatch.setenv("ACHERON_OPEN_REGISTRATION", "1")
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "book.epub").write_bytes(b"epub-fixture-bytes")
        settings = Settings()
        settings.orchestrator.data_dir = tmp_path
        spy = _RecordingOrchestrator(settings)
        app = create_app(
            registry=InMemoryWorkerStore(),
            job_store=InMemoryJobStore(),
            cache=PlanCache(tmp_path),
            data_dir=tmp_path,
            settings=settings,
        )
        await app.state.orchestrator.shutdown()
        await app.state.orchestrator.close()
        app.state.orchestrator = spy
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            response = await c.post(
                "/jobs",
                json={
                    "source_type": "epub",
                    "source_path": "input/book.epub",
                    "source_language": "en",
                    "target_language": "es",
                },
            )
        assert response.status_code == 201
        assert len(spy.submit_calls) == 1
        submitted, _ = spy.submit_calls[0]
        resolved = Path(submitted.source_path).resolve()
        assert resolved == (tmp_path / "input" / "book.epub").resolve()
        assert str(resolved).startswith(str(tmp_path.resolve()))


class TestUploadToSubmitIntegration:
    """Round-trip: POST /inputs then POST /jobs using the returned source_path."""

    @pytest.mark.asyncio
    async def test_upload_response_source_path_is_accepted_by_submit(
        self,
        client: AsyncClient,
    ) -> None:
        from fastapi import FastAPI
        from httpx import ASGITransport

        transport = cast("ASGITransport", client._transport)  # noqa: SLF001
        app = cast("FastAPI", transport.app)

        upload = await client.post(
            "/inputs",
            files={"file": ("book.epub", b"uploaded-epub-bytes", "application/epub+zip")},
        )
        assert upload.status_code == 201
        source_path = upload.json()["source_path"]
        assert source_path.startswith("inputs/")
        assert source_path.endswith("/book.epub")

        # Stored at the server-relative path under data_dir
        stored = app.state.orchestrator.settings.orchestrator.data_dir / source_path
        assert stored.is_file()
        assert stored.read_bytes() == b"uploaded-epub-bytes"

        response = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": source_path,
                "source_language": "en",
                "target_language": "es",
            },
        )
        assert response.status_code == 201
        assert response.json()["job_id"].startswith("job-")
