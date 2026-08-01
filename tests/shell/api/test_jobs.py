"""Tests for job API routes."""

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from httpx import AsyncClient

from acheron.core.errors import (
    JobAlreadyRunningError,
    JobNotCancellableError,
    JobNotFoundError,
    NoPlanToResumeError,
    WorkerError,
)
from acheron.core.models import (
    AudioRequest,
    EpubRequest,
    ExecutorStrategy,
    WorkerCapabilities,
    WorkerStatus,
    WorkerType,
)
from acheron.shell.api.routes import jobs as jobs_module
from acheron.shell.api.routes.jobs import _booting_tts_warnings, _build_retry_request, _tracked_to_response
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
    async def test_retry_request_reuses_stored_fields_for_asr_override(self, tmp_path: Path) -> None:
        from acheron.shell.api.schemas import RetryJobRequest
        from acheron.shell.cache import PlanCache
        from acheron.shell.job_store import TrackedJob
        from acheron.shell.orchestrator import Orchestrator
        from acheron.shell.stores.memory import InMemoryWorkerStore

        source_path = tmp_path / "input" / "book.wav"
        source_path.parent.mkdir()
        source_path.write_bytes(b"audio")
        source = TrackedJob(
            job_id="job-old",
            request=AudioRequest(str(source_path), "en", "es", "whisper-v3"),
            strategy=ExecutorStrategy.STREAMING,
            label="atlas-ch1",
        )
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path))

        request, strategy, label = await _build_retry_request(
            orch,
            source,
            RetryJobRequest(asr_model="whisper-tiny"),
        )

        assert isinstance(request, AudioRequest)
        assert request.source_path == "input/book.wav"
        assert request.source_language == "en"
        assert request.target_language == "es"
        assert request.asr_model == "whisper-tiny"
        assert strategy is ExecutorStrategy.STREAMING
        assert label == "atlas-ch1"

    @pytest.mark.asyncio
    async def test_retry_route_revalidates_missing_stored_source(
        self,
        client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        source = tmp_path / "input" / "book.epub"
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
        source.unlink()

        retry = await client.post(f"/jobs/{response.json()['job_id']}/retry", json={})

        assert retry.status_code == 422
        detail = retry.json()["detail"]
        assert detail == "Invalid source_path: source file is unavailable"
        assert "input/book.epub" not in detail
        assert str(tmp_path) not in detail

    @pytest.mark.asyncio
    async def test_retry_route_sanitizes_absolute_stored_source_oserror(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from fastapi import FastAPI
        from httpx import ASGITransport

        from acheron.shell.job_store import TrackedJob

        transport = cast("ASGITransport", client._transport)  # noqa: SLF001
        app = cast("FastAPI", transport.app)
        orch = app.state.orchestrator
        await orch._job_store.put(  # noqa: SLF001
            TrackedJob(
                job_id="job-absolute-source",
                request=EpubRequest(str(tmp_path / "input" / "book.epub"), "en", "es"),
                strategy=ExecutorStrategy.STREAMING,
            )
        )

        def fail_resolve(_path: Path, *, strict: bool = False) -> Path:
            raise OSError("/private/data/book.epub: permission denied")

        monkeypatch.setattr(Path, "resolve", fail_resolve)
        response = await client.post("/jobs/job-absolute-source/retry", json={})

        assert response.status_code == 422
        assert response.json()["detail"] == "Invalid source_path: source file is unavailable"
        assert "/private/data" not in response.text

    @pytest.mark.asyncio
    async def test_retry_route_accepts_valid_replacement_after_original_deleted(
        self,
        client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        source = tmp_path / "input" / "book.epub"
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
        source.unlink()

        replacement = tmp_path / "input" / "replacement.epub"
        replacement.write_bytes(b"replacement")
        retry = await client.post(
            f"/jobs/{response.json()['job_id']}/retry",
            json={"source_path": "input/replacement.epub"},
        )

        assert retry.status_code == 200
        assert retry.json()["job_id"] != response.json()["job_id"]
        assert retry.json()["retries_from"] == response.json()["job_id"]

    @pytest.mark.asyncio
    async def test_retry_route_rejects_empty_asr_override(self, client: AsyncClient) -> None:
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

        retry = await client.post(
            f"/jobs/{response.json()['job_id']}/retry",
            json={"asr_model": "  "},
        )

        assert retry.status_code == 422
        assert retry.json()["detail"] == "asr_model override must not be empty"

    @pytest.mark.asyncio
    async def test_retry_route_merges_valid_asr_override(self, client: AsyncClient, tmp_path: Path) -> None:
        audio = tmp_path / "input" / "book.wav"
        audio.write_bytes(b"audio")
        response = await client.post(
            "/jobs",
            json={
                "source_type": "audio",
                "source_path": "input/book.wav",
                "source_language": "en",
                "target_language": "es",
                "asr_model": "whisper-v3",
            },
        )
        assert response.status_code == 201

        retry = await client.post(
            f"/jobs/{response.json()['job_id']}/retry",
            json={"asr_model": "whisper-tiny"},
        )

        assert retry.status_code == 200
        assert retry.json()["job_id"] != response.json()["job_id"]
        assert retry.json()["retries_from"] == response.json()["job_id"]
        assert retry.json()["asr_model"] == "whisper-tiny"

    @pytest.mark.asyncio
    async def test_get_job_maps_total_cost_basis(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from datetime import UTC, datetime

        from httpx import ASGITransport, AsyncClient

        from acheron.core.models import (
            CostBasis,
            EpubRequest,
            ExecutorStrategy,
            OutputFile,
            PlanResult,
            PlanStatus,
            StepError,
        )
        from acheron.shell.api.app import create_app
        from acheron.shell.cache import PlanCache
        from acheron.shell.job_store import JobProgressState, TrackedJob
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
                label="atlas-ch1",
                created_at=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
                last_persisted_at=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
                progress=JobProgressState(completed_steps=1, total_steps=1),
                status=PlanStatus.COMPLETED,
                result=PlanResult(
                    plan_id="plan-measured",
                    status=PlanStatus.COMPLETED,
                    completed_steps=1,
                    total_steps=1,
                    outputs=(
                        OutputFile(
                            path="/data/job-measured/output.m4b",
                            filename="output.m4b",
                            size_bytes=1234,
                            checksum="checksum",
                            content_type="audio/mp4",
                        ),
                    ),
                    total_cost=0.25,
                    total_duration_seconds=1.0,
                    errors=(
                        StepError(
                            step_id="step-1",
                            worker_type=WorkerType.PACKAGING,
                            worker_id="packaging-1",
                            message="warning only",
                            timestamp=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
                        ),
                    ),
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
        data = response.json()
        assert data["total_cost_basis"] == "measured"
        assert data["label"] == "atlas-ch1"
        assert data["progress"] == {
            "completed_steps": 1,
            "total_steps": 1,
            "current_step_id": None,
            "current_worker_type": None,
            "current_worker_id": None,
            "eta_seconds": None,
        }
        assert data["outputs"] == [
            {
                "download_url": "/jobs/job-measured/outputs/0",
                "filename": "output.m4b",
                "size_bytes": 1234,
                "content_type": "audio/mp4",
            }
        ]
        assert "path" not in data["outputs"][0]
        assert data["errors"][0]["worker_id"] == "packaging-1"
        assert data["created_at"] == "2026-07-29T12:00:00Z"

    def test_response_uses_persisted_progress_when_result_differs(self) -> None:
        from datetime import UTC, datetime

        from acheron.core.models import PlanResult, PlanStatus
        from acheron.shell.job_store import JobProgressState, TrackedJob

        tracked = TrackedJob(
            job_id="job-progress",
            request=EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
            strategy=ExecutorStrategy.SEQUENTIAL,
            created_at=datetime(2026, 7, 29, tzinfo=UTC),
            last_persisted_at=datetime(2026, 7, 29, tzinfo=UTC),
            progress=JobProgressState(completed_steps=0, total_steps=5),
            result=PlanResult(
                plan_id="plan-progress",
                status=PlanStatus.RUNNING,
                completed_steps=3,
                total_steps=5,
                outputs=(),
                total_cost=0.0,
                total_duration_seconds=2.0,
            ),
            status=PlanStatus.RUNNING,
        )

        response = _tracked_to_response(tracked)

        assert response.progress.completed_steps == 0
        assert response.progress.total_steps == 5

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
    async def test_cancel_job_route_returns_failed_job(
        self,
        client_with_token: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from datetime import UTC, datetime
        from unittest.mock import AsyncMock

        from fastapi import FastAPI
        from httpx import ASGITransport

        from acheron.core.models import EpubRequest, ExecutorStrategy, PlanResult, PlanStatus, StepError
        from acheron.shell.job_store import TrackedJob

        transport = cast("ASGITransport", client_with_token._transport)  # noqa: SLF001
        app = cast("FastAPI", transport.app)
        now = datetime.now(UTC)
        tracked = TrackedJob(
            job_id="job-1",
            request=EpubRequest("/input/book.epub", "en", "es"),
            strategy=ExecutorStrategy.STREAMING,
            created_at=now,
            last_persisted_at=now,
            status=PlanStatus.FAILED,
            result=PlanResult(
                plan_id="plan-1",
                status=PlanStatus.FAILED,
                completed_steps=0,
                total_steps=1,
                outputs=(),
                total_cost=0.0,
                total_duration_seconds=0.0,
                errors=(
                    StepError(
                        step_id=None,
                        worker_type=None,
                        worker_id=None,
                        message="cancelled by operator",
                        timestamp=now,
                    ),
                ),
            ),
        )
        monkeypatch.setattr(app.state.orchestrator, "cancel_job", AsyncMock(return_value=tracked))

        response = await client_with_token.post(
            "/jobs/job-1/cancel",
            headers={"Authorization": "Bearer test-registration-token-must-be-32-chars-or-more"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "failed"
        assert response.json()["errors"][0]["message"] == "cancelled by operator"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "unsafe",
        [
            "/tmp",
            r"C:\\Users\\worker\\secret.txt",
            r"\\server\\share\\secret.txt",
            r"\Windows\System32\secret.dll",
            "foo/../../secret",
            r"..\\..\\secret",
            "custom+scheme://user:secret@example.test/path?token=secret#fragment",
            "Traceback (most recent call last):",
            "  File '/srv/worker.py', line 4",
            '{"password": "top-secret"}',
            "password: top-secret",
            "Authorization: Bearer top-secret",
        ],
    )
    async def test_step_error_response_sanitizes_untrusted_message(
        self,
        client_with_token: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        unsafe: str,
    ) -> None:
        from datetime import UTC, datetime
        from unittest.mock import AsyncMock

        from fastapi import FastAPI
        from httpx import ASGITransport

        from acheron.core.models import EpubRequest, ExecutorStrategy, PlanResult, PlanStatus, StepError
        from acheron.shell.job_store import TrackedJob

        transport = cast("ASGITransport", client_with_token._transport)  # noqa: SLF001
        app = cast("FastAPI", transport.app)
        now = datetime.now(UTC)
        tracked = TrackedJob(
            job_id="job-unsafe",
            request=EpubRequest("/input/book.epub", "en", "es"),
            strategy=ExecutorStrategy.STREAMING,
            created_at=now,
            last_persisted_at=now,
            status=PlanStatus.FAILED,
            result=PlanResult(
                plan_id="plan-unsafe",
                status=PlanStatus.FAILED,
                completed_steps=0,
                total_steps=1,
                outputs=(),
                total_cost=0.0,
                total_duration_seconds=0.0,
                errors=(StepError(None, None, None, unsafe, now),),
            ),
        )
        monkeypatch.setattr(app.state.orchestrator, "cancel_job", AsyncMock(return_value=tracked))

        response = await client_with_token.post(
            "/jobs/job-unsafe/cancel",
            headers={"Authorization": "Bearer test-registration-token-must-be-32-chars-or-more"},
        )

        assert response.status_code == 200
        message = response.json()["errors"][0]["message"]
        assert message == "step failed"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("error", "status_code"),
        [
            (
                JobNotFoundError("missing /data/job-1 secret=top-secret"),
                404,
            ),
            (
                JobNotCancellableError("job is running password=top-secret", remediation="acheron job status job-1"),
                409,
            ),
        ],
    )
    async def test_cancel_errors_are_sanitized(
        self,
        client_with_token: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        error: Exception,
        status_code: int,
    ) -> None:
        from unittest.mock import AsyncMock

        from fastapi import FastAPI
        from httpx import ASGITransport

        transport = cast("ASGITransport", client_with_token._transport)  # noqa: SLF001
        app = cast("FastAPI", transport.app)
        monkeypatch.setattr(app.state.orchestrator, "cancel_job", AsyncMock(side_effect=error))

        response = await client_with_token.post(
            "/jobs/job-1/cancel",
            headers={"Authorization": "Bearer test-registration-token-must-be-32-chars-or-more"},
        )

        assert response.status_code == status_code
        detail = response.json()["detail"]
        assert detail["type"] == type(error).__name__
        assert detail["message"] == "request failed"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "unsafe",
        [
            "/tmp",
            r"C:\\Users\\worker\\secret.txt",
            r"\\server\\share\\secret.txt",
            r"\Windows\System32\secret.dll",
            "foo/../../secret",
            r"..\\..\\secret",
            "custom+scheme://user:secret@example.test/path?token=secret#fragment",
            "Traceback (most recent call last):",
            "  File '/srv/worker.py', line 4",
            '{"password": "top-secret"}',
            "password: top-secret",
            "Authorization: Bearer top-secret",
        ],
    )
    async def test_error_response_uses_stable_fallback_for_untrusted_message(
        self,
        client_with_token: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        unsafe: str,
    ) -> None:
        from unittest.mock import AsyncMock

        from fastapi import FastAPI
        from httpx import ASGITransport

        transport = cast("ASGITransport", client_with_token._transport)  # noqa: SLF001
        app = cast("FastAPI", transport.app)
        error = JobNotFoundError(unsafe, remediation=unsafe)
        monkeypatch.setattr(app.state.orchestrator, "cancel_job", AsyncMock(side_effect=error))

        response = await client_with_token.post(
            "/jobs/job-1/cancel",
            headers={"Authorization": "Bearer test-registration-token-must-be-32-chars-or-more"},
        )

        assert response.status_code == 404
        detail = response.json()["detail"]
        assert detail["message"] == "request failed"
        assert detail["remediation"] == "request failed"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"Authorization": "Bearer invalid-token"},
        ],
    )
    async def test_cancel_requires_valid_registration_token(
        self,
        client_with_token: AsyncClient,
        headers: dict[str, str],
    ) -> None:
        response = await client_with_token.post("/jobs/job-1/cancel", headers=headers)

        assert response.status_code == 401
        assert response.json()["detail"] in {"Missing Authorization header", "Invalid registration token"}

    @pytest.mark.asyncio
    async def test_get_job_not_found(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/jobs/nonexistent")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("params", "detail"),
        [
            ({"since": "2026-07-30T12:00:00"}, "lifecycle timestamps must be timezone-aware"),
            (
                {"since": "2026-07-31T00:00:00Z", "before": "2026-07-30T00:00:00Z"},
                "since must not be later than before",
            ),
        ],
    )
    async def test_list_jobs_rejects_invalid_lifecycle_filters(
        self, client: AsyncClient, params: dict[str, str], detail: str
    ) -> None:
        response = await client.get("/jobs", params=params)

        assert response.status_code == 422
        assert response.json()["detail"] == detail

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
    async def test_list_jobs_archived_query_maps_archived_at(self, client) -> None:  # type: ignore[no-untyped-def]
        from datetime import UTC, datetime

        from fastapi import FastAPI
        from httpx import ASGITransport

        transport = cast("ASGITransport", client._transport)  # noqa: SLF001
        app = cast("FastAPI", transport.app)
        submit = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": "input/book.epub",
                "source_language": "en",
                "target_language": "es",
            },
        )
        assert submit.status_code == 201
        job_id = submit.json()["job_id"]
        archived_at = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
        archived = await app.state.orchestrator._job_store.archive(job_id, archived_at=archived_at)  # noqa: SLF001

        default_response = await client.get("/jobs")
        archived_response = await client.get("/jobs", params={"include_archived": "true"})

        assert archived.job_id == job_id
        assert default_response.status_code == 200
        assert default_response.json()["jobs"] == []
        assert archived_response.status_code == 200
        data = archived_response.json()["jobs"]
        assert len(data) == 1
        assert data[0]["job_id"] == job_id
        assert data[0]["archived_at"] == "2026-07-30T12:00:00Z"
        assert data[0]["source_type"] == submit.json()["source_type"]
        assert data[0]["source_language"] == submit.json()["source_language"]
        assert data[0]["target_language"] == submit.json()["target_language"]
        assert data[0]["total_cost"] == submit.json()["total_cost"]
        assert data[0]["outputs"] == submit.json()["outputs"]

    @pytest.mark.asyncio
    async def test_list_jobs_filters_by_label(self, client) -> None:  # type: ignore[no-untyped-def]
        for label in ("atlas-ch1", "other-project"):
            response = await client.post(
                "/jobs",
                json={
                    "source_type": "epub",
                    "source_path": "input/book.epub",
                    "source_language": "en",
                    "target_language": "es",
                    "label": label,
                },
            )
            assert response.status_code == 201

        response = await client.get("/jobs", params={"label": "atlas-*"})

        assert response.status_code == 200
        assert [job["label"] for job in response.json()["jobs"]] == ["atlas-ch1"]

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

        resume_resp = await client.post(
            f"/jobs/{job_id}/resume",
            json={"invalidate_steps": [], "invalidate_chapters": []},
        )
        assert resume_resp.status_code == 200
        assert resume_resp.json()["status"] == "running"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("error", "status_code", "remediation"),
        [
            (
                JobAlreadyRunningError("job-1 is running password=secret", remediation="acheron job cancel job-1"),
                409,
                "acheron job cancel job-1",
            ),
            (
                NoPlanToResumeError("no saved plan token=secret", remediation="acheron job submit <source>"),
                422,
                "acheron job submit <source>",
            ),
        ],
    )
    async def test_resume_errors_are_structured_and_sanitized(
        self,
        client_with_token: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        error: Exception,
        status_code: int,
        remediation: str,
    ) -> None:
        from unittest.mock import AsyncMock

        from fastapi import FastAPI
        from httpx import ASGITransport

        transport = cast("ASGITransport", client_with_token._transport)  # noqa: SLF001
        app = cast("FastAPI", transport.app)
        monkeypatch.setattr(app.state.orchestrator, "resume_job", AsyncMock(side_effect=error))

        response = await client_with_token.post(
            "/jobs/job-1/resume",
            json={"invalidate_steps": [], "invalidate_chapters": []},
            headers={"Authorization": "Bearer test-registration-token-must-be-32-chars-or-more"},
        )

        assert response.status_code == status_code
        detail = response.json()["detail"]
        assert detail["type"] == type(error).__name__
        assert "secret" not in detail["message"]
        assert detail["remediation"] == remediation

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
            detail = response.json()["detail"]
            assert detail["type"] == "InvalidLanguagePathError"

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

    @pytest.mark.asyncio
    async def test_job_logs_ndjson_stream(self, client: AsyncClient) -> None:
        """GET /jobs/{id}/logs streams NDJSON events."""
        from acheron.core.schemas import JobLogEvent

        resp = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": "input/book.epub",
                "source_language": "en",
                "target_language": "es",
            },
        )
        assert resp.status_code == 201
        job_id = resp.json()["job_id"]

        logs_resp = await client.get(f"/jobs/{job_id}/logs", params={"follow": "false"})
        assert logs_resp.status_code == 200
        lines = [line for line in logs_resp.text.strip().split("\n") if line]
        assert len(lines) >= 1
        for line in lines:
            event = JobLogEvent.model_validate_json(line)
            assert event.job_id == job_id

    @pytest.mark.asyncio
    async def test_job_logs_404_for_missing_job(self, client: AsyncClient) -> None:
        """GET /jobs/{id}/logs returns 404 for unknown job."""
        resp = await client.get("/jobs/nonexistent/logs")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_job_logs_follow_true_returns_snapshot_for_terminal_job(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """follow=true on a terminal job returns a snapshot instead of hanging."""
        from datetime import UTC, datetime

        from httpx import ASGITransport, AsyncClient

        from acheron.core.models import PlanStatus
        from acheron.core.schemas import JobLogEvent
        from acheron.shell.api.app import create_app
        from acheron.shell.cache import PlanCache
        from acheron.shell.job_store import JobProgressState
        from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore
        from tests.shell.conftest import asr_caps, translation_caps, tts_caps

        monkeypatch.delenv("ACHERON_REGISTRATION_TOKEN", raising=False)
        monkeypatch.setenv("ACHERON_OPEN_REGISTRATION", "1")
        registry = InMemoryWorkerStore()
        await registry.register("tts-1", "http://tts-1", "http", tts_caps())
        await registry.register("asr-1", "http://asr-1", "http", asr_caps())
        await registry.register("trans-1", "http://trans-1", "http", translation_caps())
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "book.epub").write_bytes(b"epub-fixture-bytes")
        jobs = InMemoryJobStore()
        app = create_app(
            registry=registry,
            job_store=jobs,
            cache=PlanCache(tmp_path),
            data_dir=tmp_path,
        )
        await app.state.orchestrator.start()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.post(
                    "/jobs",
                    json={
                        "source_type": "epub",
                        "source_path": "input/book.epub",
                        "source_language": "en",
                        "target_language": "es",
                    },
                )
                assert resp.status_code == 201
                job_id = resp.json()["job_id"]

                # Force the job to a terminal state.
                tracked = await jobs.get(job_id)
                assert tracked is not None
                now = datetime.now(UTC)
                tracked.status = PlanStatus.COMPLETED
                tracked.last_persisted_at = now
                tracked.progress = JobProgressState(
                    completed_steps=1,
                    total_steps=1,
                )
                await jobs.put(tracked)

                # follow=true must NOT hang on a terminal job.
                logs_resp = await asyncio.wait_for(
                    c.get(f"/jobs/{job_id}/logs", params={"follow": "true"}),
                    timeout=3.0,
                )
        finally:
            await app.state.orchestrator.shutdown()
            await app.state.orchestrator.close()

        assert logs_resp.status_code == 200
        lines = [line for line in logs_resp.text.strip().split("\n") if line]
        assert len(lines) == 1
        event = JobLogEvent.model_validate_json(lines[0])
        assert event.job_id == job_id
        assert event.status is PlanStatus.COMPLETED


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
            response = await c.post(
                "/jobs/j-1/resume",
                json={"invalidate_steps": [], "invalidate_chapters": []},
            )
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
    async def test_incomplete_submission_rolls_back_job_plan_and_input(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from fastapi import FastAPI
        from httpx import ASGITransport

        upload = await client.post(
            "/inputs",
            files={"file": ("book.epub", b"epub", "application/epub+zip")},
        )
        uploaded = upload.json()
        transport = cast("ASGITransport", client._transport)  # noqa: SLF001
        app = cast("FastAPI", transport.app)
        orch = app.state.orchestrator
        store = orch._job_store  # noqa: SLF001
        original_put = store.put

        async def fail_after_put(job: TrackedJob) -> None:
            await original_put(job)
            raise OSError("/private/job-state: injected failure")

        monkeypatch.setattr(store, "put", fail_after_put)
        response = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": uploaded["source_path"],
                "source_language": "en",
                "target_language": "es",
                "input_id": uploaded["input_id"],
            },
        )

        assert response.status_code == 422
        assert response.json() == {"detail": "input storage failed"}
        assert await orch.list_jobs() == ()
        assert not (app.state.orchestrator.settings.orchestrator.data_dir / "inputs" / uploaded["input_id"]).exists()
        assert not list(app.state.orchestrator.settings.orchestrator.data_dir.glob("plan-*/plan.json"))

    @pytest.mark.asyncio
    async def test_source_resolution_oserror_is_sanitized(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def fail_resolve(*_args: object, **_kwargs: object) -> Path:
            raise OSError("/private/data/book.epub: permission denied")

        monkeypatch.setattr("acheron.shell.input_store.InputStore.resolve_source_path", fail_resolve)
        response = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": "input/book.epub",
                "source_language": "en",
                "target_language": "es",
            },
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "Invalid source_path: source file is unavailable"
        assert "/private/data" not in response.text

    @pytest.mark.asyncio
    async def test_epub_inspection_oserror_is_sanitized(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def fail_inspect(_source: Path) -> list[str]:
            raise OSError("/private/data/book.epub: permission denied")

        monkeypatch.setattr(jobs_module, "read_epub_chapters", fail_inspect)
        response = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": "input/book.epub",
                "source_language": "en",
                "target_language": "es",
                "voice_map": [{"start_chapter": 1, "end_chapter": 1, "voice": "Vivian"}],
            },
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "unable to inspect EPUB chapters"
        assert "/private/data" not in response.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("asr_model", ["", "   ", "\t\n"])
    async def test_audio_blank_asr_model_returns_422_without_submission(self, client, monkeypatch, asr_model) -> None:  # type: ignore[no-untyped-def]
        from unittest.mock import AsyncMock

        from fastapi import FastAPI
        from httpx import ASGITransport

        transport = cast("ASGITransport", client._transport)  # noqa: SLF001
        app = cast("FastAPI", transport.app)
        submit_job = AsyncMock()
        monkeypatch.setattr(app.state.orchestrator, "submit_job", submit_job)

        response = await client.post(
            "/jobs",
            json={
                "source_type": "audio",
                "source_path": "input/book.mp3",
                "source_language": "en",
                "target_language": "es",
                "asr_model": asr_model,
            },
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "asr_model is required for source_type='audio'"
        submit_job.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("asr_model", ["", "   "])
    async def test_epub_blank_asr_model_preserves_non_audio_rejection(self, client, monkeypatch, asr_model) -> None:  # type: ignore[no-untyped-def]
        from unittest.mock import AsyncMock

        from fastapi import FastAPI
        from httpx import ASGITransport

        transport = cast("ASGITransport", client._transport)  # noqa: SLF001
        app = cast("FastAPI", transport.app)
        submit_job = AsyncMock()
        monkeypatch.setattr(app.state.orchestrator, "submit_job", submit_job)

        response = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": "input/book.epub",
                "source_language": "en",
                "target_language": "es",
                "asr_model": asr_model,
            },
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "asr_model is only valid for source_type='audio'"
        submit_job.assert_not_awaited()

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
        assert detail == "Invalid source_path: source file is unavailable"
        assert "missing.epub" not in detail
        assert str(tmp_path) not in detail
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
        assert "../outside.epub" not in detail
        assert str(tmp_path) not in detail
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
        assert "source_path" in detail
        assert "/tmp/book.epub" not in detail
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
        assert detail == "Invalid source_path: source file is unavailable"
        assert "inputs/a-dir" not in detail
        assert str(tmp_path) not in detail
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
    async def test_valid_relative_path_passes_canonical_identity_to_submit(
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
        assert submitted.source_path == "input/book.epub"


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


class TestPreviewRoute:
    """OPS-011 / OPS-016: POST /jobs:preview reuses submit's preflight but does not persist."""

    @pytest.mark.asyncio
    async def test_preview_rejects_audio_without_asr(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/jobs:preview",
            json={
                "source_type": "audio",
                "source_path": "input/book.epub",
                "source_language": "en",
                "target_language": "es",
            },
        )
        assert response.status_code == 422
        assert "asr_model is required" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_preview_domain_errors_are_structured_and_sanitized(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from unittest.mock import AsyncMock

        from fastapi import FastAPI
        from httpx import ASGITransport

        transport = cast("ASGITransport", client._transport)  # noqa: SLF001
        app = cast("FastAPI", transport.app)
        error = WorkerError(
            "EPUB extraction failed at /srv/acheron/jobs/../secret "
            "redis://user:secret@cache.internal:6379/0?token=secret "
            "password=top-secret\nTraceback (most recent call last):\n  File '/srv/worker.py', line 4",
            remediation="acheron job retry job-1",
        )
        monkeypatch.setattr(app.state.orchestrator, "preview_job", AsyncMock(side_effect=error))

        response = await client.post(
            "/jobs:preview",
            json={
                "source_type": "epub",
                "source_path": "input/book.epub",
                "source_language": "en",
                "target_language": "es",
            },
        )

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["type"] == "WorkerError"
        assert detail["message"] == "request failed"
        assert detail["remediation"] == "acheron job retry job-1"

    @pytest.mark.asyncio
    async def test_preview_timeout_cleans_temporary_input_without_job_or_plan(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from unittest.mock import AsyncMock

        from fastapi import FastAPI
        from httpx import ASGITransport

        upload = await client.post(
            "/inputs",
            files={"file": ("book.epub", b"temporary-epub", "application/epub+zip")},
        )
        assert upload.status_code == 201
        uploaded = upload.json()
        transport = cast("ASGITransport", client._transport)  # noqa: SLF001
        app = cast("FastAPI", transport.app)
        orch = app.state.orchestrator
        monkeypatch.setattr(orch, "preview_job", AsyncMock(side_effect=TimeoutError("preflight timed out")))

        with pytest.raises(TimeoutError, match="preflight timed out"):
            await client.post(
                "/jobs:preview",
                json={
                    "source_type": "epub",
                    "source_path": uploaded["source_path"],
                    "source_language": "en",
                    "target_language": "es",
                    "input_id": uploaded["input_id"],
                },
            )

        assert await orch.list_jobs() == ()
        assert not (orch.settings.orchestrator.data_dir / uploaded["source_path"]).exists()
        assert not list(orch.settings.orchestrator.data_dir.glob("plan-*/plan.json"))

    @pytest.mark.asyncio
    async def test_preview_returns_plan_without_persisting(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Preview must not create plan files, job records, or schedule execution."""
        from httpx import ASGITransport, AsyncClient

        from tests.shell.conftest import make_app

        monkeypatch.delenv("ACHERON_REGISTRATION_TOKEN", raising=False)
        monkeypatch.setenv("ACHERON_OPEN_REGISTRATION", "1")
        app = await make_app(tmp_path)
        await app.state.orchestrator.start()
        data_dir = app.state.orchestrator.settings.orchestrator.data_dir
        jobs_before = await app.state.orchestrator.list_jobs()
        transport = ASGITransport(app=app)
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                response = await c.post(
                    "/jobs:preview",
                    json={
                        "source_type": "epub",
                        "source_path": "input/book.epub",
                        "source_language": "en",
                        "target_language": "es",
                    },
                )
                jobs_after = await app.state.orchestrator.list_jobs()
        finally:
            await app.state.orchestrator.shutdown()

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["source_type"] == "epub"
        assert body["source_language"] == "en"
        assert body["target_language"] == "es"
        assert body["steps"]
        # No plan file should have been written for the returned plan.
        assert not (data_dir / body["plan_id"]).exists()
        # No job should have been recorded.
        assert len(jobs_after) == len(jobs_before)
        # The preview endpoint must not expose step payloads.
        assert "payload" not in body["steps"][0]
