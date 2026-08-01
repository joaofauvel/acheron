"""Tests for administrative authorization and contract seams."""

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from fastapi import APIRouter, FastAPI, Request
from httpx import ASGITransport, AsyncClient

from acheron.core.models import EpubRequest, ExecutorStrategy, PlanStatus
from acheron.shell.api.admin_audit import execute_admin_action
from acheron.shell.api.deps import AdminTokenDep
from acheron.shell.api.schemas import CleanupRequest, ReapStaleRequest
from acheron.shell.job_store import JobQuery, TrackedJob
from acheron.shell.retention import CleanupFailure, CleanupReport
from acheron.shell.stores.memory import InMemoryJobStore


def _app(client: AsyncClient) -> FastAPI:
    transport = cast("ASGITransport", client._transport)  # noqa: SLF001
    return cast("FastAPI", transport.app)


@pytest.mark.asyncio
async def test_open_registration_does_not_authorize_admin(client: AsyncClient) -> None:
    response = await client.post(
        "/admin/jobs/reap-stale",
        json={"older_than_seconds": 60, "reason": "restart"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["type"] == "AdminConfigurationUnavailable"


@pytest.mark.asyncio
async def test_admin_route_rejects_registration_token(client: AsyncClient) -> None:
    app = _app(client)
    app.state.orchestrator.settings.orchestrator.admin_token = "a" * 32
    app.state.orchestrator.settings.orchestrator.registration_token = "r" * 32

    response = await client.post(
        "/admin/jobs/reap-stale",
        json={"older_than_seconds": 60, "reason": "restart"},
        headers={"Authorization": "Bearer " + "r" * 32},
    )

    assert response.status_code == 401
    assert response.json()["detail"]["type"] == "AdminAuthenticationError"


@pytest.mark.asyncio
async def test_admin_auth_failure_is_audited_once(client: AsyncClient) -> None:
    app = _app(client)
    response = await client.post("/admin/jobs/reap-stale", json={"older_than_seconds": 60, "reason": "restart"})

    assert response.status_code == 503
    assert len(app.state.orchestrator.admin_audits) == 1
    assert app.state.orchestrator.admin_audits[0].result == "failure"
    assert app.state.orchestrator.admin_audits[0].action == "jobs/reap-stale"


@pytest.mark.asyncio
async def test_admin_success_audit_seam_records_once(client: AsyncClient) -> None:
    app = _app(client)
    app.state.orchestrator.settings.orchestrator.admin_token = "a" * 32
    router = APIRouter()

    @router.post("/admin/synthetic-success")
    async def synthetic_success(request: Request, _token: AdminTokenDep) -> dict[str, bool]:
        async def operation() -> dict[str, bool]:
            return {"ok": True}

        await execute_admin_action(request, app.state.orchestrator, operation)
        return await execute_admin_action(request, app.state.orchestrator, operation)

    app.include_router(router)
    response = await client.post(
        "/admin/synthetic-success",
        headers={"Authorization": "Bearer " + "a" * 32},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    audits = app.state.orchestrator.admin_audits
    assert len(audits) == 1
    assert audits[0].result == "success"
    assert audits[0].action == "synthetic-success"


@pytest.mark.asyncio
async def test_unexpected_admin_error_uses_sanitized_type(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(client)
    from acheron.shell.api.routes import admin as admin_routes

    monkeypatch.setattr(admin_routes, "_not_implemented", lambda _action: RuntimeError("internal details"))
    app.state.orchestrator.settings.orchestrator.admin_token = "a" * 32
    response = await client.post(
        "/admin/jobs/cleanup",
        json={"retention_seconds": 60, "apply": True},
        headers={"Authorization": "Bearer " + "a" * 32},
    )

    assert response.status_code == 500
    assert response.json()["detail"]["type"] == "AdminInternalError"
    assert response.json()["detail"]["type"] != "RuntimeError"


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_cleanup_partial_report_is_audited_as_failure(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(client)
    orch = app.state.orchestrator
    orch.settings.orchestrator.admin_token = "a" * 32

    async def partial_cleanup(*_args: object, **_kwargs: object) -> CleanupReport:
        return _partial_cleanup_report()

    monkeypatch.setattr(orch, "preview_cleanup", partial_cleanup)

    response = await client.post(
        "/admin/cleanup",
        json={"keep_successful_seconds": 60, "keep_failed_seconds": 60},
        headers={"Authorization": "Bearer " + "a" * 32},
    )

    assert response.status_code == 200
    assert len(response.json()["failures"]) == 1
    assert response.json()["deleted_count"] == 1
    assert orch.admin_audits[-1].result == "failure"
    assert orch.admin_audits[-1].reason == "cleanup completed with per-job failures"


def _partial_cleanup_report() -> CleanupReport:
    return CleanupReport(
        apply=False,
        candidates=(),
        deleted_job_ids=("job-deleted",),
        failures=(CleanupFailure("job-failed", ("job-failed",), "retry is safe"),),
        deleted_count=1,
        deleted_bytes=4,
        reclaimable_bytes=8,
    )


@pytest.mark.asyncio
async def test_admin_validation_failure_is_audited_once(client: AsyncClient) -> None:
    app = _app(client)
    app.state.orchestrator.settings.orchestrator.admin_token = "a" * 32
    response = await client.post(
        "/admin/jobs/reap-stale",
        json={"older_than_seconds": 60, "unknown": True},
        headers={"Authorization": "Bearer " + "a" * 32},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["type"] == "AdminRequestValidationError"
    assert len(app.state.orchestrator.admin_audits) == 1


def test_admin_request_models_are_strict_and_validate_durations() -> None:
    assert ReapStaleRequest(older_than_seconds=0, reason="restart").older_than_seconds == 0
    with pytest.raises(ValueError, match="finite"):
        ReapStaleRequest(older_than_seconds=float("inf"), reason="restart")
    with pytest.raises(ValueError, match="finite"):
        ReapStaleRequest(older_than_seconds=True, reason="restart")
    with pytest.raises(ValueError, match="supported range"):
        ReapStaleRequest(older_than_seconds=1e308, reason="restart")
    with pytest.raises(ValueError, match="finite"):
        CleanupRequest(retention_seconds=True)
    with pytest.raises(ValueError, match="greater than 0"):
        CleanupRequest(retention_seconds=0)
    with pytest.raises(ValueError, match="extra"):
        ReapStaleRequest.model_validate({"older_than_seconds": 1, "reason": "restart", "typo": True})
    with pytest.raises(ValueError, match="at most 512"):
        ReapStaleRequest(older_than_seconds=1, reason="x" * 513)
    with pytest.raises(ValueError, match="boolean"):
        CleanupRequest.model_validate(
            {"retention_seconds": 60, "keep_successful_seconds": 60, "keep_failed_seconds": 60, "apply": "yes"}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/admin/jobs/reap-stale", {"older_than_seconds": True, "reason": "restart"}),
        ("/admin/jobs/reap-stale", {"older_than_seconds": 1e308, "reason": "restart"}),
        ("/admin/cleanup", {"retention_seconds": True}),
        ("/admin/cleanup", {"retention_seconds": 1e308}),
        (
            "/admin/cleanup",
            {
                "retention_seconds": 60,
                "keep_successful_seconds": 60,
                "keep_failed_seconds": 60,
                "apply": "yes",
            },
        ),
    ],
)
async def test_admin_duration_overflow_is_client_validation(
    client: AsyncClient,
    path: str,
    body: dict[str, object],
) -> None:
    app = _app(client)
    app.state.orchestrator.settings.orchestrator.admin_token = "a" * 32

    response = await client.post(path, json=body, headers={"Authorization": "Bearer " + "a" * 32})

    assert response.status_code == 422
    assert response.json()["detail"]["type"] == "AdminRequestValidationError"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/admin/jobs/missing/mark-failed", {"reason": "operator"}),
        ("/admin/jobs/missing/archive", {}),
        ("/admin/jobs/cleanup", {"retention_seconds": 60, "apply": True}),
    ],
)
async def test_admin_contract_route_failure_is_audited_once(
    client: AsyncClient,
    path: str,
    body: dict[str, object],
) -> None:
    app = _app(client)
    app.state.orchestrator.settings.orchestrator.admin_token = "a" * 32

    response = await client.post(path, json=body, headers={"Authorization": "Bearer " + "a" * 32})

    assert response.status_code in {404, 501}
    assert response.json()["detail"]["type"] in {"JobNotFoundError", "AdminActionUnavailable"}
    assert len(app.state.orchestrator.admin_audits) == 1
    assert app.state.orchestrator.admin_audits[0].result == "failure"


@pytest.mark.asyncio
async def test_admin_invalid_bodies_are_audited_once(client: AsyncClient) -> None:
    app = _app(client)
    app.state.orchestrator.settings.orchestrator.admin_token = "a" * 32
    headers = {"Authorization": "Bearer " + "a" * 32}

    for path, body in (
        ("/admin/jobs/missing/archive", {"unknown": True}),
        ("/admin/jobs/missing/mark-failed", {"unknown": True}),
        ("/admin/jobs/cleanup", {"retention_seconds": 0}),
    ):
        response = await client.post(path, json=body, headers=headers)
        assert response.status_code == 422

    assert len(app.state.orchestrator.admin_audits) == 3
    assert all(event.result == "failure" for event in app.state.orchestrator.admin_audits)


def test_job_query_requires_utc_and_finite_non_negative_age() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        JobQuery(since=datetime.fromisoformat("2026-01-01"))
    with pytest.raises(ValueError, match="finite and non-negative"):
        JobQuery(older_than_seconds=float("nan"))
    with pytest.raises(ValueError, match="since must not be later"):
        JobQuery(
            since=datetime(2026, 1, 2, tzinfo=UTC),
            before=datetime(2026, 1, 1, tzinfo=UTC),
        )
    query = JobQuery(since=datetime(2026, 1, 1, tzinfo=UTC), include_archived=True)
    assert query.since == datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_reap_stale_route_marks_orphan_and_audits_once(client: AsyncClient) -> None:
    app = _app(client)
    orch = app.state.orchestrator
    now = datetime.now(UTC)
    orphan = TrackedJob(
        job_id="orphan",
        request=EpubRequest("/input/book.epub", "en", "es"),
        strategy=ExecutorStrategy.SEQUENTIAL,
        status=PlanStatus.RUNNING,
        created_at=now - timedelta(minutes=5),
        last_persisted_at=now - timedelta(minutes=5),
    )
    await orch._job_store.put(orphan)  # noqa: SLF001
    orch._job_store._jobs["orphan"].last_persisted_at = now - timedelta(minutes=5)  # noqa: SLF001
    orch.settings.orchestrator.admin_token = "a" * 32

    response = await client.post(
        "/admin/jobs/reap-stale",
        json={"older_than_seconds": 60, "reason": " orphaned_by_restart\nprivate detail"},
        headers={"Authorization": "Bearer " + "a" * 32},
    )

    assert response.status_code == 200
    assert response.json() == {"reaped": 1, "job_ids": ["orphan"]}
    stored = await orch.get_job("orphan")
    assert stored is not None
    assert stored.status is PlanStatus.FAILED
    assert stored.result is not None
    assert stored.result.errors[-1].message == "orphaned_by_restart"
    assert len(orch.admin_audits) == 1
    assert orch.admin_audits[0].affected_count == 1


@pytest.mark.asyncio
async def test_archive_route_is_idempotent_and_preserves_job(client: AsyncClient) -> None:
    app = _app(client)
    orch = app.state.orchestrator
    job = TrackedJob(
        job_id="archive-me",
        request=EpubRequest("/input/book.epub", "en", "es"),
        strategy=ExecutorStrategy.SEQUENTIAL,
        status=PlanStatus.COMPLETED,
    )
    await orch._job_store.put(job)  # noqa: SLF001
    orch.settings.orchestrator.admin_token = "a" * 32
    headers = {"Authorization": "Bearer " + "a" * 32}

    first = await client.post("/admin/jobs/archive-me/archive", json={}, headers=headers)
    second = await client.post("/admin/jobs/archive-me/archive", json={}, headers=headers)

    assert first.status_code == second.status_code == 200
    assert first.json()["job"]["archived_at"] is not None
    assert second.json()["job"]["archived_at"] == first.json()["job"]["archived_at"]
    assert len(orch.admin_audits) == 2


@pytest.mark.asyncio
async def test_admin_mark_failed_missing_job_is_structured(client: AsyncClient) -> None:
    app = _app(client)
    app.state.orchestrator.settings.orchestrator.admin_token = "a" * 32
    response = await client.post(
        "/admin/jobs/missing/mark-failed",
        json={"reason": "operator"},
        headers={"Authorization": "Bearer " + "a" * 32},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["type"] == "JobNotFoundError"
    assert len(app.state.orchestrator.admin_audits) == 1


@pytest.mark.asyncio
async def test_job_store_query_filters_stale_jobs_deterministically() -> None:
    store = InMemoryJobStore()
    now = datetime(2026, 7, 30, tzinfo=UTC)
    for job_id, last_persisted_at in (("old", now - timedelta(seconds=61)), ("new", now - timedelta(seconds=5))):
        await store.put(
            TrackedJob(
                job_id=job_id,
                request=EpubRequest("/input/book.epub", "en", "es"),
                strategy=ExecutorStrategy.SEQUENTIAL,
                status=PlanStatus.RUNNING,
            )
        )
        store._jobs[job_id].last_persisted_at = last_persisted_at  # noqa: SLF001

    store._jobs["old"].created_at = now - timedelta(days=1)  # noqa: SLF001
    store._jobs["new"].created_at = now  # noqa: SLF001
    store._jobs["old"].archived_at = now  # noqa: SLF001

    jobs = await store.list(
        JobQuery(status=PlanStatus.RUNNING, before=now - timedelta(hours=1), older_than_seconds=60),
        now=now,
    )
    assert jobs == ()

    jobs = await store.list(
        JobQuery(status=PlanStatus.RUNNING, before=now, older_than_seconds=60, include_archived=True),
        now=now,
    )
    assert [job.job_id for job in jobs] == ["old"]
