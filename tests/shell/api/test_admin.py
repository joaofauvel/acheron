"""Tests for administrative authorization and contract seams."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from fastapi import APIRouter, FastAPI, Request
from httpx import ASGITransport, AsyncClient

from acheron.core.models import EpubRequest, ExecutorStrategy, PlanStatus
from acheron.shell.api.admin_audit import execute_admin_action
from acheron.shell.api.deps import AdminTokenDep
from acheron.shell.api.schemas import CleanupRequest, ReapStaleRequest, TokenRotateRequest
from acheron.shell.cache import PlanCache
from acheron.shell.job_store import AdminActionAudit, JobQuery, TrackedJob
from acheron.shell.orchestrator import Orchestrator
from acheron.shell.retention import CleanupFailure, CleanupReport
from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore
from acheron.shell.token_auth import RegistrationTokenStore, RolloutResult, TokenRotationError
from acheron.tls import CertificateError, CertificateStatus


def _app(client: AsyncClient) -> FastAPI:
    transport = cast("ASGITransport", client._transport)  # noqa: SLF001
    return cast("FastAPI", transport.app)


@pytest.mark.asyncio
async def test_token_status_requires_admin_token(client: AsyncClient) -> None:
    response = await client.get("/admin/token/status")

    assert response.status_code == 503
    assert response.json()["detail"]["type"] == "AdminConfigurationUnavailable"


@pytest.mark.asyncio
async def test_registration_token_cannot_authorize_token_routes(client: AsyncClient) -> None:
    app = _app(client)
    app.state.orchestrator.settings.orchestrator.admin_token = "a" * 32
    app.state.orchestrator.settings.orchestrator.registration_token = "r" * 32

    response = await client.get(
        "/admin/token/status",
        headers={"Authorization": "Bearer " + "r" * 32},
    )

    assert response.status_code == 401
    assert response.json()["detail"]["type"] == "AdminAuthenticationError"


@pytest.mark.asyncio
async def test_token_rotate_audits_success_once(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(client)
    orch = app.state.orchestrator
    orch.settings.orchestrator.admin_token = "a" * 32
    audit_count = len(orch.admin_audits)

    async def successful_rollout(_candidate: str) -> RolloutResult:
        return RolloutResult(success=True, worker_ids=("worker-a",))

    monkeypatch.setattr(orch._worker_rotation_coordinator, "rollout", successful_rollout)  # noqa: SLF001
    response = await client.post(
        "/admin/token/rotate",
        json={"reason": "scheduled rotation"},
        headers={"Authorization": "Bearer " + "a" * 32, "x-request-id": "token-rotate"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["rotated"] is True
    assert body["status"]["source"] == "file"
    assert body["status"]["fingerprint"]
    assert body["rollout"]["success"] is True
    assert "registration_token" not in json.dumps(body).lower()
    assert len(orch.admin_audits) == audit_count + 1
    audit = orch.admin_audits[-1]
    assert audit.action == "token/rotate"
    assert audit.request_id == "token-rotate"
    assert audit.reason == "scheduled rotation"


def test_token_rotate_reason_rejects_whitespace() -> None:
    with pytest.raises(ValueError, match="non-whitespace"):
        TokenRotateRequest(reason=" \n\t ")
    assert TokenRotateRequest(reason="  scheduled rotation  ").reason == "scheduled rotation"


@pytest.mark.asyncio
async def test_token_status_does_not_block_inflight_rotation(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(client)
    orch = app.state.orchestrator
    orch.settings.orchestrator.admin_token = "a" * 32
    rollout_started = asyncio.Event()
    release_rollout = asyncio.Event()

    async def slow_rollout(_candidate: str) -> RolloutResult:
        rollout_started.set()
        await release_rollout.wait()
        return RolloutResult(success=True, worker_ids=("worker-a",))

    monkeypatch.setattr(orch._worker_rotation_coordinator, "rollout", slow_rollout)  # noqa: SLF001
    headers = {"Authorization": "Bearer " + "a" * 32}
    rotation = asyncio.create_task(client.post("/admin/token/rotate", json={"reason": "heartbeat"}, headers=headers))
    await asyncio.wait_for(rollout_started.wait(), timeout=1)
    status = asyncio.create_task(client.get("/admin/token/status", headers=headers))
    heartbeat = asyncio.Event()

    async def beat() -> None:
        await asyncio.sleep(0)
        heartbeat.set()

    await asyncio.wait_for(beat(), timeout=1)
    assert heartbeat.is_set()
    release_rollout.set()
    status_response, rotation_response = await asyncio.gather(status, rotation)
    assert status_response.status_code == 200
    assert rotation_response.status_code == 200


@pytest.mark.asyncio
async def test_token_status_redacts_untrusted_audit_text(client: AsyncClient) -> None:
    app = _app(client)
    orch = app.state.orchestrator
    orch.settings.orchestrator.admin_token = "a" * 32
    current_token = orch._registration_token_store.read_current()  # noqa: SLF001
    old_token = "b" * 32
    punctuation_token = "historical-token!with/punctuation?and=separators"
    uuid_token = "123e4567-e89b-12d3-a456-426614174000"
    audit_path = orch._registration_token_store.audit_path  # noqa: SLF001
    audit_path.write_text(
        json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "reason": f"scheduled rotation {current_token} {punctuation_token} {uuid_token}",
                "old_fingerprint": None,
                "new_fingerprint": None,
                "worker_ids": [old_token, punctuation_token, uuid_token],
                "result": "success",
                "request_id": f"request-{punctuation_token}-{uuid_token}",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    response = await client.get(
        "/admin/token/status",
        headers={"Authorization": "Bearer " + "a" * 32},
    )
    payload = json.dumps(response.json())
    assert response.status_code == 200
    assert current_token not in payload
    assert old_token not in payload
    assert punctuation_token not in payload
    assert uuid_token not in payload
    assert "scheduled rotation" in payload
    assert "[redacted]" in payload


@pytest.mark.asyncio
async def test_token_rotate_rejects_environment_source(client: AsyncClient, tmp_path: Path) -> None:
    app = _app(client)
    orch = app.state.orchestrator
    orch.settings.orchestrator.admin_token = "a" * 32
    store = RegistrationTokenStore(tmp_path / "environment-token")
    store.load_or_create("e" * 32)
    orch._registration_token_store = store  # noqa: SLF001
    response = await client.post(
        "/admin/token/rotate",
        json={"reason": "environment mode"},
        headers={"Authorization": "Bearer " + "a" * 32},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["remediation"] == "Update the worker environment and restart workers externally"


@pytest.mark.asyncio
async def test_token_rotate_returns_structured_rollout_failure_from_coordinator(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(client)
    orch = app.state.orchestrator
    orch.settings.orchestrator.admin_token = "a" * 32

    async def fail_rollout(_candidate: str) -> RolloutResult:
        return RolloutResult(
            success=False,
            worker_ids=("worker-a",),
            remediation="Replace unsupported worker transports before retrying",
        )

    monkeypatch.setattr(orch._worker_rotation_coordinator, "rollout", fail_rollout)  # noqa: SLF001
    response = await client.post(
        "/admin/token/rotate",
        json={"reason": "unsupported fleet"},
        headers={"Authorization": "Bearer " + "a" * 32},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["type"] == "TokenRotationError"
    assert detail["remediation"] == "Replace unsupported worker transports before retrying"


@pytest.mark.asyncio
async def test_token_rotate_returns_structured_rollout_failure(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(client)
    orch = app.state.orchestrator
    orch.settings.orchestrator.admin_token = "a" * 32

    async def fail_rotation(*_args: object, **_kwargs: object) -> object:
        raise TokenRotationError(
            "Registration token rollout failed",
            remediation="Replace unsupported worker transports before retrying",
        )

    monkeypatch.setattr(orch, "rotate_registration_token", fail_rotation)
    response = await client.post(
        "/admin/token/rotate",
        json={"reason": "unsupported fleet"},
        headers={"Authorization": "Bearer " + "a" * 32},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["type"] == "TokenRotationError"
    assert detail["remediation"] == "Replace unsupported worker transports before retrying"


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
async def test_admin_audits_are_durable_and_queryable_after_restart(client: AsyncClient) -> None:
    app = _app(client)
    orch = app.state.orchestrator
    orch.settings.orchestrator.admin_token = "a" * 32
    job = TrackedJob(
        job_id="archive-me",
        request=EpubRequest("/input/book.epub", "en", "es"),
        strategy=ExecutorStrategy.SEQUENTIAL,
        status=PlanStatus.COMPLETED,
    )
    await orch._job_store.put(job)  # noqa: SLF001
    headers = {"Authorization": "Bearer " + "a" * 32}

    success = await client.post(
        "/admin/jobs/archive-me/archive",
        json={"reason": "retention review"},
        headers={**headers, "x-request-id": "audit-success"},
    )
    failure = await client.post(
        "/admin/jobs/missing/mark-failed",
        json={"reason": "operator review"},
        headers={**headers, "x-request-id": "audit-failure"},
    )

    assert success.status_code == 200
    assert failure.status_code == 404
    restarted = Orchestrator(
        InMemoryWorkerStore(),
        PlanCache(orch.settings.orchestrator.data_dir),
        job_store=InMemoryJobStore(),
        settings=orch.settings,
    )
    try:
        await restarted.start()
        audits = restarted.admin_audits
        assert len(audits) == 2
    finally:
        await restarted.shutdown()
        await restarted.close()
    assert audits[0].request_id == "audit-success"
    assert audits[0].action == "jobs/archive-me/archive"
    assert audits[0].result == "success"
    assert audits[0].reason == "retention review"
    assert audits[0].job_ids == ("archive-me",)
    assert audits[0].affected_count == 1
    assert audits[1].request_id == "audit-failure"
    assert audits[1].action == "jobs/missing/mark-failed"
    assert audits[1].result == "failure"
    assert audits[1].reason is not None
    assert audits[1].job_ids == ()
    assert audits[1].affected_count == 0


@pytest.mark.asyncio
async def test_malformed_admin_audits_do_not_block_startup(client: AsyncClient) -> None:
    app = _app(client)
    orch = app.state.orchestrator
    audit_path = orch.settings.orchestrator.data_dir / ".admin_audit.jsonl"
    valid: dict[str, object] = {
        "request_id": "valid",
        "action": "jobs/reap-stale",
        "reason": None,
        "job_ids": [],
        "affected_count": 0,
        "result": "success",
    }
    audit_path.write_bytes(
        b'{"request_id":"bad","action":"jobs/reap-stale","reason":null,"job_ids":[],"affected_count":0,"result":[]}\n'
        b"\xff\xfe\n" + (json.dumps(valid) + "\n").encode()
    )
    restarted = Orchestrator(
        InMemoryWorkerStore(),
        PlanCache(orch.settings.orchestrator.data_dir),
        job_store=InMemoryJobStore(),
        settings=orch.settings,
    )
    try:
        await restarted.start()
        assert [event.request_id for event in restarted.admin_audits] == ["valid"]
    finally:
        await restarted.shutdown()
        await restarted.close()


@pytest.mark.asyncio
async def test_admin_audit_file_is_compacted_to_bounded_tail(client: AsyncClient) -> None:
    app = _app(client)
    orch = app.state.orchestrator
    audit_path = orch.settings.orchestrator.data_dir / ".admin_audit.jsonl"
    records: list[dict[str, object]] = [
        {
            "request_id": str(index),
            "action": "jobs/reap-stale",
            "reason": None,
            "job_ids": [],
            "affected_count": 0,
            "result": "success",
        }
        for index in range(1001)
    ]
    audit_path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    restarted = Orchestrator(
        InMemoryWorkerStore(),
        PlanCache(orch.settings.orchestrator.data_dir),
        job_store=InMemoryJobStore(),
        settings=orch.settings,
    )
    try:
        restarted.record_admin_audit(
            AdminActionAudit(
                request_id="new",
                action="jobs/reap-stale",
                reason=None,
                job_ids=(),
                affected_count=0,
                result="success",
            )
        )
        assert len(audit_path.read_text(encoding="utf-8").splitlines()) == 1000
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_admin_audits_from_multiple_orchestrators_are_merged(client: AsyncClient) -> None:
    app = _app(client)
    data_dir = app.state.orchestrator.settings.orchestrator.data_dir
    first = Orchestrator(
        InMemoryWorkerStore(),
        PlanCache(data_dir),
        job_store=InMemoryJobStore(),
        settings=app.state.orchestrator.settings,
    )
    second = Orchestrator(
        InMemoryWorkerStore(),
        PlanCache(data_dir),
        job_store=InMemoryJobStore(),
        settings=app.state.orchestrator.settings,
    )
    try:
        first.record_admin_audit(
            AdminActionAudit(
                request_id="first",
                action="jobs/reap-stale",
                reason=None,
                job_ids=(),
                affected_count=0,
                result="success",
            )
        )
        second.record_admin_audit(
            AdminActionAudit(
                request_id="second",
                action="jobs/reap-stale",
                reason=None,
                job_ids=(),
                affected_count=0,
                result="success",
            )
        )
        restarted = Orchestrator(
            InMemoryWorkerStore(),
            PlanCache(data_dir),
            job_store=InMemoryJobStore(),
            settings=app.state.orchestrator.settings,
        )
        try:
            assert [event.request_id for event in restarted.admin_audits] == ["first", "second"]
        finally:
            await restarted.close()
    finally:
        await first.close()
        await second.close()


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


class _CertificateManagerStub:
    def __init__(self, status: CertificateStatus) -> None:
        self._status = status

    def status(self) -> CertificateStatus:
        return self._status

    def reload(self) -> CertificateStatus:
        return self._status


class _InvalidCertificateManagerStub:
    def status(self) -> CertificateStatus:
        raise CertificateError(
            "Unable to parse TLS certificate at /run/secrets/private-key.pem",
            remediation="Check the replacement certificate and key before retrying",
        )

    def reload(self) -> CertificateStatus:
        raise CertificateError(
            "Unable to reload TLS certificate pair at /run/secrets/private-key.pem",
            remediation="Check the replacement certificate and key before retrying",
        )


@pytest.mark.asyncio
async def test_cert_status_requires_admin_token(client: AsyncClient) -> None:
    response = await client.get("/admin/certs/status")

    assert response.status_code == 503
    assert response.json()["detail"]["type"] == "AdminConfigurationUnavailable"


@pytest.mark.asyncio
async def test_cert_status_reports_disabled_tls(client: AsyncClient) -> None:
    app = _app(client)
    app.state.orchestrator.settings.orchestrator.admin_token = "a" * 32

    response = await client.get(
        "/admin/certs/status",
        headers={"Authorization": "Bearer " + "a" * 32},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["type"] == "CertificateError"
    assert response.json()["detail"]["remediation"]


@pytest.mark.asyncio
async def test_cert_status_returns_expiry_metadata(client: AsyncClient) -> None:
    app = _app(client)
    orch = app.state.orchestrator
    orch.settings.orchestrator.admin_token = "a" * 32
    expires_at = datetime(2026, 8, 12, 10, 30, tzinfo=UTC)
    app.state.certificate_manager = _CertificateManagerStub(
        CertificateStatus(
            name="orchestrator.crt",
            subject="CN=orchestrator",
            expires_at=expires_at,
            remaining=timedelta(days=9, hours=2),
            severity="warning",
        )
    )

    response = await client.get(
        "/admin/certs/status",
        headers={"Authorization": "Bearer " + "a" * 32},
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "enabled": True,
        "name": "orchestrator.crt",
        "subject": "CN=orchestrator",
        "expires_at": "2026-08-12T10:30:00Z",
        "remaining_seconds": 784800.0,
        "remaining_display": "9d 2h 0m",
        "severity": "warning",
    }
    assert "private-key" not in json.dumps(body)
    assert len(orch.admin_audits) == 1
    assert orch.admin_audits[0].action == "certs/status"


@pytest.mark.asyncio
async def test_cert_reload_requires_admin_token(client: AsyncClient) -> None:
    response = await client.post("/admin/certs/reload")

    assert response.status_code == 503
    assert response.json()["detail"]["type"] == "AdminConfigurationUnavailable"


@pytest.mark.asyncio
async def test_cert_reload_preserves_structured_error_on_invalid_pair(client: AsyncClient) -> None:
    app = _app(client)
    orch = app.state.orchestrator
    orch.settings.orchestrator.admin_token = "a" * 32
    app.state.certificate_manager = _InvalidCertificateManagerStub()

    response = await client.post(
        "/admin/certs/reload",
        headers={"Authorization": "Bearer " + "a" * 32, "x-request-id": "cert-reload-failure"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["type"] == "CertificateError"
    assert detail["remediation"] == "Check the replacement certificate and key before retrying"
    assert "/run/secrets" not in json.dumps(detail)
    assert len(orch.admin_audits) == 1
    assert orch.admin_audits[0].request_id == "cert-reload-failure"
    assert orch.admin_audits[0].action == "certs/reload"
    assert orch.admin_audits[0].result == "failure"
