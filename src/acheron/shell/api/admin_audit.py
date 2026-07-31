"""Audit helpers for administrative route operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from acheron.shell.job_store import AdminActionAudit

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import Request

    from acheron.shell.orchestrator import Orchestrator


@dataclass(frozen=True)
class AdminAuditDetails:
    """Optional metadata attached to an administrative audit event."""

    reason: str | None = None
    job_ids: tuple[str, ...] = ()
    affected_count: int = 0


def admin_action_name(path: str) -> str:
    """Return the route-relative name for an administrative path."""
    parts = [part for part in path.split("/") if part]
    return "/".join(parts[1:]) if parts and parts[0] == "admin" else path


def _record(
    request: Request,
    orch: Orchestrator,
    *,
    result: Literal["success", "failure"],
    details: AdminAuditDetails,
) -> None:
    if getattr(request.state, "admin_audit_recorded", False):
        return
    request.state.admin_audit_recorded = True
    orch.record_admin_audit(
        AdminActionAudit(
            request_id=getattr(request.state, "request_id", ""),
            action=admin_action_name(request.url.path),
            reason=details.reason,
            job_ids=details.job_ids,
            affected_count=details.affected_count,
            result=result,
        )
    )


def record_admin_failure(request: Request, orch: Orchestrator, *, reason: str) -> None:
    """Record a failed admin request unless another layer already recorded it."""
    if request.url.path.startswith("/admin/"):
        _record(request, orch, result="failure", details=AdminAuditDetails(reason=reason))


def record_admin_success(
    request: Request,
    orch: Orchestrator,
    *,
    details: AdminAuditDetails | None = None,
) -> None:
    """Record a normally completed admin operation exactly once."""
    if request.url.path.startswith("/admin/"):
        _record(request, orch, result="success", details=details or AdminAuditDetails())


async def execute_admin_action[T](
    request: Request,
    orch: Orchestrator,
    operation: Callable[[], Awaitable[T]],
    *,
    details: AdminAuditDetails | None = None,
) -> T:
    """Run a route operation and audit its normal or exceptional completion."""
    try:
        result = await operation()
    except Exception:
        reason = details.reason if details and details.reason else "administrative route operation failed"
        record_admin_failure(request, orch, reason=reason)
        raise
    record_admin_success(request, orch, details=details)
    return result
