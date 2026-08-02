"""Orchestrator — service layer wiring registry, planner, executors, and cache."""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import logging
import os
import secrets
import shutil
import tempfile
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from acheron.core.errors import (
    AcheronError,
    InvalidationTargetError,
    JobAlreadyRunningError,
    JobNotCancellableError,
    JobNotFoundError,
    JobNotResumableError,
    NoPlanToResumeError,
    sanitise_exc_message,
)
from acheron.core.models import (
    AudioRequest,
    CostBasis,
    EpubRequest,
    ExecutorStrategy,
    JobMetrics,
    JobResult,
    JobStatus,
    JsonValue,
    Plan,
    PlanResult,
    PlanStatus,
    PlanStep,
    StepError,
    WorkerCapabilities,
    WorkerType,
)
from acheron.core.planner import ChunkingLimits, compile_plan
from acheron.core.schemas import CostBreakdownResponse, CostJobSnapshot, CostSummaryResponse, JobCostResponse
from acheron.shell.api.public import public_gpu_type, public_optional_worker_id, public_worker_id
from acheron.shell.cache import InMemoryStepCache, StepCache
from acheron.shell.capabilities import CapabilityAggregator, LanguagePair
from acheron.shell.config import Settings, _validate_credential_token, load_settings
from acheron.shell.cost import aggregate_cost_basis, build_cost_breakdown, estimate_cost
from acheron.shell.executors import create_executor
from acheron.shell.health import HealthMonitor
from acheron.shell.health_providers import create_health_providers
from acheron.shell.input_store import InputPathError, InputStore
from acheron.shell.job_events import JobEventBroker
from acheron.shell.job_store import AdminActionAudit, JobQuery, TrackedJob
from acheron.shell.local_handlers import (
    LocalJobHandler,
    all_languages_caps,
)
from acheron.shell.logging_context import bind_job_id
from acheron.shell.retention import CleanupReport, RetentionPolicy, RetentionService
from acheron.shell.step_handler import create_step_handler
from acheron.shell.stores import create_job_store
from acheron.shell.stores.base import StoreError

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from acheron.core.interfaces import Executor
    from acheron.core.models import JobRequest
    from acheron.shell.cache import PlanCache
    from acheron.shell.executors._utils import StepHandler
    from acheron.shell.registry import RegisteredWorker
    from acheron.shell.stores.base import JobStore, WorkerStore

logger = logging.getLogger(__name__)
_MAX_ADMIN_AUDITS = 1000
_MAX_ADMIN_REQUEST_ID_LENGTH = 128
_MAX_ADMIN_ACTION_LENGTH = 256
_MAX_ADMIN_REASON_LENGTH = 256
_MAX_ADMIN_AUDIT_REASON_LENGTH = 512
_MAX_ADMIN_JOB_ID_LENGTH = 256
_MAX_ADMIN_AUDIT_LINE_BYTES = 4 * 1024 * 1024
_ADMIN_AUDIT_FILE = ".admin_audit.jsonl"
_ADMIN_AUDIT_LOCK_FILE = ".admin_audit.jsonl.lock"
_LOW_DISK_RATIO = 0.10
_CRITICAL_DISK_RATIO = 0.05


@dataclass(frozen=True)
class ReapResult:
    """Identifiers of jobs transitioned by stale-job reaping."""

    job_ids: tuple[str, ...]


def _sanitize_admin_reason(reason: str) -> str:
    """Bound operator-provided lifecycle reasons before persisting them."""
    first_line = next((line.strip() for line in reason.splitlines() if line.strip()), "")
    return (first_line or "administrative action")[:_MAX_ADMIN_REASON_LENGTH]


def _normalise_admin_audit(event: AdminActionAudit) -> AdminActionAudit:
    """Bound one audit event before logging, retaining, and persisting it."""
    return replace(
        event,
        request_id=event.request_id[:_MAX_ADMIN_REQUEST_ID_LENGTH],
        action=event.action[:_MAX_ADMIN_ACTION_LENGTH],
        reason=event.reason[:_MAX_ADMIN_AUDIT_REASON_LENGTH] if event.reason is not None else None,
        job_ids=tuple(job_id[:_MAX_ADMIN_JOB_ID_LENGTH] for job_id in event.job_ids[:_MAX_ADMIN_AUDITS]),
        affected_count=min(max(0, event.affected_count), _MAX_ADMIN_AUDITS),
    )


def _admin_audit_payload(event: AdminActionAudit) -> dict[str, object]:
    return {
        "request_id": event.request_id,
        "action": event.action,
        "reason": event.reason,
        "job_ids": list(event.job_ids),
        "affected_count": event.affected_count,
        "result": event.result,
    }


def _parse_admin_audit(line: str) -> AdminActionAudit | None:
    """Parse one bounded JSONL audit record, rejecting malformed values."""
    try:
        if len(line.encode("utf-8")) > _MAX_ADMIN_AUDIT_LINE_BYTES:
            return None
        raw: object = json.loads(line)
    except TypeError, ValueError, UnicodeError, RecursionError:
        return None
    if not isinstance(raw, dict):
        return None
    request_id = raw.get("request_id")
    action = raw.get("action")
    reason = raw.get("reason")
    job_ids = raw.get("job_ids")
    affected_count = raw.get("affected_count")
    result = raw.get("result")
    if (
        not isinstance(request_id, str)
        or len(request_id) > _MAX_ADMIN_REQUEST_ID_LENGTH
        or not isinstance(action, str)
        or len(action) > _MAX_ADMIN_ACTION_LENGTH
        or (reason is not None and (not isinstance(reason, str) or len(reason) > _MAX_ADMIN_AUDIT_REASON_LENGTH))
        or not isinstance(job_ids, list)
        or len(job_ids) > _MAX_ADMIN_AUDITS
        or not all(isinstance(job_id, str) and len(job_id) <= _MAX_ADMIN_JOB_ID_LENGTH for job_id in job_ids)
        or not isinstance(affected_count, int)
        or isinstance(affected_count, bool)
        or not 0 <= affected_count <= _MAX_ADMIN_AUDITS
        or not isinstance(result, str)
        or result not in {"success", "failure"}
    ):
        return None
    return AdminActionAudit(
        request_id=request_id,
        action=action,
        reason=reason,
        job_ids=tuple(cast("list[str]", job_ids)),
        affected_count=affected_count,
        result=cast("Literal['success', 'failure']", result),
    )


def _known_result_cost(result: PlanResult | None) -> float:
    """Return known cost while retaining measured components of mixed results."""
    if result is None:
        return 0.0
    if result.total_cost_basis not in {None, CostBasis.UNKNOWN}:
        return result.total_cost
    return sum(
        item.estimate.cost
        for item in result.cost_breakdown
        if item.estimate.basis is not CostBasis.UNKNOWN and item.estimate.cost is not None
    )


def _log_unexpected(label: str, exc: BaseException) -> None:
    """Log an unexpected exception with a label; the caller decides whether to re-raise."""
    logger.exception("%s: %s", label, exc)


def _validate_registration_token(token: str | None) -> None:
    _validate_credential_token(token, setting_name="registration_token")


def _chapter_matches(payload: dict[str, JsonValue], chapter: int) -> bool:
    value = payload.get("chapter_ids", payload.get("chapter_id"))
    match value:
        case list() as chapter_ids:
            return f"chapter_{chapter:03d}" in chapter_ids or f"ch{chapter}" in chapter_ids
        case int() as number:
            return number == chapter
        case str() as text:
            return text in {str(chapter), f"ch{chapter}", f"chapter_{chapter:03d}"}
        case _:
            return False


def _resolve_invalidation_steps(
    plan: Plan,
    requested_steps: Collection[str],
    requested_chapters: Collection[int],
) -> set[str]:
    step_ids = {step.step_id for step in plan.steps}
    unknown_steps = set(requested_steps) - step_ids
    if unknown_steps:
        names = ", ".join(sorted(unknown_steps))
        msg = f"Unknown step invalidation target: {names}"
        raise InvalidationTargetError(msg)

    selected = set(requested_steps)
    if requested_chapters and not any(
        "chapter_ids" in step.payload or "chapter_id" in step.payload for step in plan.steps
    ):
        msg = (
            "Chapter metadata is unavailable for this plan; numeric chapter invalidation requires "
            "a readable EPUB source. Use --invalidate-step or re-submit the source."
        )
        raise InvalidationTargetError(msg)
    for chapter in requested_chapters:
        chapter_steps = {step.step_id for step in plan.steps if _chapter_matches(step.payload, chapter)}
        if not chapter_steps:
            msg = f"Unknown chapter invalidation target: {chapter}"
            raise InvalidationTargetError(msg)
        selected.update(chapter_steps)

    changed = True
    while changed:
        changed = False
        for step in plan.steps:
            if step.step_id not in selected and any(dependency in selected for dependency in step.depends_on):
                selected.add(step.step_id)
                changed = True
    return selected


class Orchestrator:
    """Service layer wiring together all pipeline components."""

    def __init__(  # noqa: PLR0913
        self,
        registry: WorkerStore,
        cache: PlanCache,
        handler: StepHandler | None = None,
        *,
        job_store: JobStore | None = None,
        step_cache: StepCache | InMemoryStepCache | None = None,
        settings: Settings | None = None,
    ) -> None:
        if settings is None:
            default = load_settings()
            settings = Settings(orchestrator=default.orchestrator.model_copy(update={"data_dir": cache.data_dir}))
        canonical_data_dir = settings.orchestrator.data_dir.resolve()
        if cache.data_dir.resolve() != canonical_data_dir:
            msg = (
                "PlanCache data directory must match the canonical orchestrator data directory: "
                f"{cache.data_dir.resolve()} != {canonical_data_dir}"
            )
            raise ValueError(msg)
        if step_cache is not None and step_cache.data_dir.resolve() != canonical_data_dir:
            msg = (
                "StepCache data directory must match the canonical orchestrator data directory: "
                f"{step_cache.data_dir.resolve()} != {canonical_data_dir}"
            )
            raise ValueError(msg)
        self._settings = settings.model_copy(
            update={"orchestrator": settings.orchestrator.model_copy(update={"data_dir": canonical_data_dir})}
        )
        self._registry = registry
        self._cache = cache
        self._step_cache = step_cache if step_cache is not None else StepCache(canonical_data_dir)
        self._local_handlers: dict[str, LocalJobHandler] = {}
        self._handler = handler or create_step_handler(
            registry,
            local_handlers=self._local_handlers,
            data_dir=self._settings.orchestrator.data_dir,
            registration_token_provider=lambda: self._settings.orchestrator.registration_token,
            step_cache=self._step_cache,
        )
        if handler is not None:
            configure_step_cache = getattr(self._handler, "configure_step_cache", None)
            if configure_step_cache is not None:
                configure_step_cache(self._step_cache)
        self._job_store = job_store if job_store is not None else create_job_store()
        self._capabilities = CapabilityAggregator(registry)
        self._tasks: set[asyncio.Task[None]] = set()
        self._execution_tasks: dict[str, asyncio.Task[None]] = {}
        self._operator_cancellation_requested: set[str] = set()
        self._background_persists: set[asyncio.Task[None]] = set()
        self._background_persists_by_job: dict[str, set[asyncio.Task[None]]] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._active_jobs: set[str] = set()
        self._job_lock_registry_mutex = threading.Lock()
        self._job_locks: dict[str, asyncio.Lock] = {}
        self._input_lock_registry_mutex = threading.Lock()
        self._input_locks: dict[str, asyncio.Lock] = {}
        self._retention = RetentionService(
            self._job_store,
            self._cache,
            self._step_cache,
            data_dir=canonical_data_dir,
            job_lock=self._job_lifecycle_lock,
            input_lock=self._input_reference_lock,
            active_jobs=lambda: self._active_jobs,
        )
        self._started = False
        self._shutting_down = False
        self._health_providers = create_health_providers(self._settings)
        self._events = JobEventBroker()
        self._admin_audit_path = canonical_data_dir / _ADMIN_AUDIT_FILE
        self._admin_audit_lock_path = canonical_data_dir / _ADMIN_AUDIT_LOCK_FILE
        self._admin_audit_lock = threading.Lock()
        self._admin_audits: deque[AdminActionAudit] = deque(
            self._load_admin_audits(),
            maxlen=_MAX_ADMIN_AUDITS,
        )
        self._health_monitor = HealthMonitor(
            registry,
            interval=float(self._settings.orchestrator.health_check_interval_seconds),
            providers=self._health_providers,
        )

    @property
    def settings(self) -> Settings:
        """Get the configuration settings."""
        return self._settings

    @property
    def events(self) -> JobEventBroker:
        """Event broker for live progress monitoring."""
        return self._events

    @property
    def admin_audits(self) -> tuple[AdminActionAudit, ...]:
        """Administrative action audit events recorded for this process."""
        with self._admin_audit_lock:
            return tuple(self._admin_audits)

    def record_admin_audit(self, event: AdminActionAudit) -> None:
        """Normalize, log, and durably retain one administrative action event."""
        normalized = _normalise_admin_audit(event)
        with self._admin_audit_lock:
            try:
                persisted = self._persist_admin_audit(normalized)
            except OSError:
                self._admin_audits.append(normalized)
                logger.exception("Failed to persist administrative audit event %s", normalized.action)
            else:
                self._admin_audits = deque(persisted, maxlen=_MAX_ADMIN_AUDITS)
        logger.info(
            "Administrative audit event",
            extra={
                "admin_audit": _admin_audit_payload(normalized),
                "request_id": normalized.request_id,
                "action": normalized.action,
                "result": normalized.result,
                "reason": normalized.reason,
                "job_ids": normalized.job_ids,
                "affected_count": normalized.affected_count,
            },
        )

    def _persist_admin_audit(self, event: AdminActionAudit) -> tuple[AdminActionAudit, ...]:
        """Merge one event into the durable bounded tail under a process lock."""
        self._admin_audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self._admin_audit_lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                self._remove_stale_admin_audit_temps()
                events = deque(self._load_admin_audits(), maxlen=_MAX_ADMIN_AUDITS)
                events.append(event)
                self._rewrite_admin_audits(tuple(events))
                return tuple(events)
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _rewrite_admin_audits(self, events: tuple[AdminActionAudit, ...]) -> None:
        """Atomically replace the durable audit stream with its bounded tail."""
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._admin_audit_path.parent,
                prefix=f"{self._admin_audit_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as audit_file:
                temporary_path = Path(audit_file.name)
                for event in events:
                    audit_file.write(json.dumps(_admin_audit_payload(event), separators=(",", ":")) + "\n")
                audit_file.flush()
                os.fsync(audit_file.fileno())
            if temporary_path is not None:
                temporary_path.replace(self._admin_audit_path)
                temporary_path = None
            directory_fd = os.open(self._admin_audit_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary_path is not None:
                with contextlib.suppress(OSError):
                    temporary_path.unlink()

    def _remove_stale_admin_audit_temps(self) -> None:
        """Remove temporary audit snapshots left by an interrupted rewrite."""
        pattern = f"{self._admin_audit_path.name}.*.tmp"
        for temporary_path in self._admin_audit_path.parent.glob(pattern):
            with contextlib.suppress(OSError):
                temporary_path.unlink()

    def _load_admin_audits(self) -> tuple[AdminActionAudit, ...]:
        """Load the bounded tail of the durable administrative audit stream."""
        events: deque[AdminActionAudit] = deque(maxlen=_MAX_ADMIN_AUDITS)
        try:
            with self._admin_audit_path.open("rb") as audit_file:
                while raw_line := audit_file.readline(_MAX_ADMIN_AUDIT_LINE_BYTES + 1):
                    if len(raw_line) > _MAX_ADMIN_AUDIT_LINE_BYTES:
                        while raw_line and not raw_line.endswith(b"\n"):
                            raw_line = audit_file.readline(_MAX_ADMIN_AUDIT_LINE_BYTES + 1)
                        continue
                    try:
                        line = raw_line.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    event = _parse_admin_audit(line.rstrip("\r\n"))
                    if event is not None:
                        events.append(event)
        except FileNotFoundError:
            return ()
        except OSError as exc:
            logger.warning("Failed to load administrative audit events: %s", exc)
            return ()
        return tuple(events)

    def _verify_data_dir_writable(self) -> None:
        """Ensure the step-cache data dir exists and is writable. Raises AcheronError otherwise."""
        data_dir = self._settings.orchestrator.data_dir
        probe = data_dir / ".acheron_write_test"
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            probe.write_text("ok", encoding="utf-8")
            probe.read_text(encoding="utf-8")
        except OSError as exc:
            msg = (
                f"Data dir {data_dir} is not writable: {exc}. "
                "Mount a writable volume or set ACHERON_DATA_DIR to a writable path."
            )
            raise AcheronError(msg) from exc
        finally:
            with contextlib.suppress(OSError):
                probe.unlink()
        try:
            usage = shutil.disk_usage(data_dir)
        except OSError as exc:
            logger.warning("Unable to inspect free space for data dir %s: %s", data_dir, exc)
        else:
            if usage.total and usage.free / usage.total < _CRITICAL_DISK_RATIO:
                logger.error("Data dir %s has less than 5%% free space", data_dir)
            elif usage.total and usage.free / usage.total < _LOW_DISK_RATIO:
                logger.warning("Data dir %s has less than 10%% free space", data_dir)

    async def _register_built_in_local_workers(self) -> None:
        """Register in-process local workers for orchestration-level steps.

        Only registers a step type if no worker of that type is already in the
        registry, so user-registered workers (e.g. custom extraction logic) take
        precedence over the stubs. The handler is kept in a side dict on the
        orchestrator (not in worker metadata) because handlers are not
        JSON-serializable and would break non-memory backends like Redis.

        Idempotent: safe to call multiple times. Called from ``start()`` so the
        store async methods can be awaited.
        """
        from acheron.shell.local_handlers import (  # noqa: PLC0415
            ChunkingHandler,
            ExtractionHandler,
            PackagingHandler,
        )

        handlers: dict[WorkerType, LocalJobHandler] = {
            WorkerType.EXTRACTION: ExtractionHandler(self._settings.orchestrator.data_dir),
            WorkerType.CHUNKING: ChunkingHandler(
                self._settings.orchestrator.data_dir,
                self._settings.workers.chunking.max_chunk_length,
            ),
            WorkerType.PACKAGING: PackagingHandler(
                self._settings.orchestrator.data_dir,
                self._settings.workers.packaging.bitrate,
                self._settings.workers.packaging.codec,
                self._settings.workers.packaging.max_fmt_chunk_length,
            ),
        }

        for worker_type, handler in handlers.items():
            existing = await self._registry.find_by_type(worker_type)
            if existing:
                continue
            worker_id = f"{worker_type.value}-local"
            self._local_handlers[worker_id] = handler
            await self._registry.register(
                worker_id=worker_id,
                endpoint="local",
                transport="local",
                capabilities=all_languages_caps(worker_type),
                metadata={},
            )

    async def close(self) -> None:
        """Release any resources held by the stores. Idempotent and exception-isolated.

        Tears down the Redis connection pool (or any other backend-held resources).
        In-flight execution tasks must be drained via ``shutdown()`` first. Any
        shielded reconciliation writes that outlived the drain grace are given
        one bounded grace period before the stores are closed. Each resource
        cleanup is bounded by the same grace period.
        """
        pending = await self._wait_for_background_persists(
            max_wait=self._settings.orchestrator.shutdown_drain_seconds,
        )
        for task in pending:
            task.cancel()
        await asyncio.sleep(0)
        for close_attr in ("_handler", "_registry", "_job_store"):
            try:
                close = getattr(getattr(self, close_attr), "close", None)
                if close is not None:
                    try:
                        async with asyncio.timeout(self._settings.orchestrator.shutdown_drain_seconds):
                            await close()
                    except TimeoutError:
                        logger.warning("Timed out closing %s", close_attr)
            except Exception as exc:  # noqa: BLE001
                _log_unexpected(f"Failed to close {close_attr}", exc)

    async def start(self) -> None:
        """Start background tasks and register built-in local workers.

        Idempotent: calling start() more than once is a no-op so the FastAPI
        lifespan path and explicit callers can both be safe.

        Raises:
            RuntimeError: If ``ACHERON_REGISTRATION_TOKEN`` is set to a
                publicly-known value or is shorter than 32 characters.
        """
        if self._started:
            return
        self._verify_data_dir_writable()
        with self._admin_audit_lock:
            self._admin_audits = deque(self._load_admin_audits(), maxlen=_MAX_ADMIN_AUDITS)
        await self._load_or_create_registration_token()
        _validate_registration_token(self._settings.orchestrator.registration_token)
        _validate_credential_token(self._settings.orchestrator.admin_token, setting_name="admin_token")

        await self._registry.connect()
        await self._job_store.connect()
        self._started = True
        await self._register_built_in_local_workers()
        await self._health_monitor.start()

    async def _load_or_create_registration_token(self) -> None:
        """Load a persisted registration token or mint and persist a fresh one.

        The token is written to ``<data_dir>/.registration_token`` (mode 0600)
        if missing. Only the file path is logged; the token value is never
        logged at any level (SEC-008).
        """
        if self._settings.orchestrator.registration_token:
            return
        token_file = self._settings.orchestrator.data_dir / ".registration_token"
        if token_file.is_file():
            try:
                token = token_file.read_text(encoding="utf-8").strip()
                self._settings.orchestrator.registration_token = token
                logger.info("Loaded persistent registration token from %s", token_file)
            except OSError as exc:
                logger.warning("Failed to read persistent registration token from %s: %s", token_file, exc)

        if self._settings.orchestrator.registration_token:
            return
        token = secrets.token_hex(16)
        self._settings.orchestrator.registration_token = token
        try:
            token_file.write_text(token, encoding="utf-8")
            token_file.chmod(0o600)
            logger.info("Generated and persisted registration token to %s", token_file)
        except OSError as exc:
            logger.warning("Generated registration token but failed to persist to %s: %s", token_file, exc)

    async def shutdown(self) -> None:
        """Stop the health monitor and drain in-flight ``_execute`` tasks.

        Cancels every task tracked on ``self._tasks`` and awaits them with
        a grace timeout so any in-flight job reconciles to a terminal
        persisted state. On timeout the reconcile continues in the
        background and the ``TimeoutError`` propagates. For explicit
        cleanup of stores (Redis pools, file handles), call :meth:`close`
        separately.
        """
        await self._health_monitor.stop()
        await self._drain_inflight_tasks()

    async def _drain_inflight_tasks(self) -> None:
        """Cancel and await in-flight ``_execute`` tasks, best-effort.

        Cancellation arrives via ``task.cancel()``; each task's ``_execute``
        body catches ``asyncio.CancelledError``, marks the job FAILED, and
        persists it inside ``asyncio.shield`` so a firing drain grace cannot
        abort the write — a still-running persist completes in the
        background. The tasks are collected with
        ``asyncio.gather(..., return_exceptions=True)`` inside
        ``asyncio.timeout(orchestrator.shutdown_drain_seconds)`` so a slow
        store cannot hang shutdown indefinitely; on timeout a warning is
        logged and the ``TimeoutError`` propagates to the caller.
        """
        async with self._lifecycle_lock:
            self._shutting_down = True
            pending = [task for task in self._tasks if not task.done()]
        if not pending:
            return
        # Let newly-created tasks enter _execute before cancellation so its
        # reconciliation handler also covers tasks that were just spawned.
        await asyncio.sleep(0)
        for task in pending:
            task.cancel()
        grace = self._settings.orchestrator.shutdown_drain_seconds
        logger.info("Draining %d in-flight _execute tasks (grace=%.1fs)", len(pending), grace)
        start = time.monotonic()
        try:
            async with asyncio.timeout(grace):
                results = await asyncio.gather(*pending, return_exceptions=True)
        except TimeoutError:
            still_pending = sum(1 for task in pending if not task.done())
            logger.warning(
                "Drain grace timeout (%.1fs) fired with %d/%d tasks still pending and "
                "%d reconciliation writes; persisted state may be inconsistent",
                grace,
                still_pending,
                len(pending),
                len(self._background_persists),
            )
            raise
        unexpected = [
            result
            for result in results
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError)
        ]
        if unexpected:
            for exc in unexpected:
                logger.error(
                    "In-flight _execute task failed during drain: %s",
                    exc,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
            raise unexpected[0]
        logger.info("Drained %d tasks in %.2fs", len(pending), time.monotonic() - start)

    async def delete_input(self, input_id: str) -> None:
        """Delete an unreferenced uploaded input under its lifecycle lock."""
        identity = f"inputs/{input_id}"
        async with self._input_reference_lock(identity):
            jobs = await self._job_store.list_all()
            if any(self._input_identity(job.request.source_path).startswith(f"{identity}/") for job in jobs):
                raise InputPathError("input is referenced by a job")
            InputStore(self._settings.orchestrator.data_dir, create=False).delete(input_id)

    async def _rollback_submission(
        self,
        job_id: str,
        plan: Plan | None,
        *,
        task: asyncio.Task[None] | None,
        broker_started: bool,
    ) -> None:
        """Remove all state created by an incomplete submission."""
        self._active_jobs.discard(job_id)
        self._operator_cancellation_requested.discard(job_id)
        execution_task = task or self._execution_tasks.get(job_id)
        if execution_task is not None and not execution_task.done():
            execution_task.cancel()
            with contextlib.suppress(BaseException):
                await execution_task
        self._execution_tasks.pop(job_id, None)
        if execution_task is not None:
            self._tasks.discard(execution_task)
        try:
            await self._job_store.delete(job_id)
        except Exception as exc:  # noqa: BLE001
            _log_unexpected(f"Failed to roll back persisted job {job_id}", exc)
        if broker_started:
            await self._events.finish(job_id)
        if plan is not None:
            try:
                self._cache.delete_plan(plan.plan_id)
            except Exception as exc:  # noqa: BLE001
                _log_unexpected(f"Failed to roll back plan {plan.plan_id}", exc)

    async def submit_job(
        self,
        request: JobRequest,
        strategy: ExecutorStrategy,
        *,
        label: str | None = None,
        retries_from: str | None = None,
        input_id: str | None = None,
    ) -> TrackedJob:
        """Compile a plan and execute it. Returns the tracked job immediately.

        Raises:
            RuntimeError: If ``start()`` has not been called. Local workers
                are registered during start(); submitting before start would
                fail at execution with a confusing WorkerError.
            InvalidLanguagePathError: If no registered worker supports the
                requested language path.
            ChunkingTooLongForWorkerError: If the chunking step's
                ``max_chunk_length`` exceeds a text-input worker's
                ``max_input_tokens``.
        """
        if not self._started:
            msg = "Orchestrator.start() must be called before submit_job()"
            raise RuntimeError(msg)
        job_id = f"job-{uuid.uuid4().hex[:8]}"
        match request:
            case EpubRequest():
                source_type = "epub"
            case AudioRequest():
                source_type = "audio"
        logger.info(
            "Submitting job %s: %s → %s (%s, %s)",
            job_id,
            request.source_language,
            request.target_language,
            source_type,
            strategy.value,
        )

        input_identity = self._input_identity(request.source_path)
        plan: Plan | None = None
        execution_task: asyncio.Task[None] | None = None
        broker_started = False
        try:
            async with self._input_reference_lock(input_identity):
                if input_id is not None:
                    InputStore(self._settings.orchestrator.data_dir, create=False).promote(
                        input_id,
                        request.source_path,
                    )
                plan = await self._compile_plan(request, strategy, job_id=job_id)
                self._cache.save_plan(plan)
                logger.info("Plan compiled for %s: %s (%d steps)", job_id, plan.plan_id, len(plan.steps))

                tracked = TrackedJob(
                    job_id=job_id,
                    request=request,
                    strategy=strategy,
                    label=label,
                    retries_from=retries_from,
                    plan=plan,
                    status=PlanStatus.RUNNING,
                )
                async with self._lifecycle_lock:
                    if self._shutting_down:
                        msg = "Orchestrator is shutting down; new jobs are not accepted"
                        raise RuntimeError(msg)  # noqa: TRY301
                    await self._events.start(tracked.job_id)
                    broker_started = True
                    await self._job_store.put(tracked)
                    self._active_jobs.add(tracked.job_id)
                    self._track_execution_task(tracked)
                    execution_task = self._execution_tasks.get(tracked.job_id)

                return tracked
        except BaseException:
            await self._rollback_submission(
                job_id,
                plan,
                task=execution_task,
                broker_started=broker_started,
            )
            raise

    async def submit_retry(
        self,
        source_job_id: str,
        request: JobRequest,
        strategy: ExecutorStrategy,
        *,
        label: str | None,
    ) -> TrackedJob:
        """Create a fresh job linked to an earlier submission."""
        source = await self._job_store.get(source_job_id)
        if source is None:
            msg = f"Job not found: {source_job_id}"
            raise JobNotFoundError(msg)
        return await self.submit_job(
            request,
            strategy,
            label=label,
            retries_from=source_job_id,
        )

    async def _compile_plan(
        self,
        request: JobRequest,
        strategy: ExecutorStrategy,
        *,
        job_id: str | None = None,
    ) -> Plan:
        """Compile a :class:`Plan` for ``request`` using the current registry.

        Shared by :meth:`submit_job` (which passes a generated ``job_id``)
        and :meth:`preview_job` (which omits it so ``compile_plan`` mints a
        throwaway ``job_id`` for the in-memory plan).
        """
        workers = tuple(await self._registry.list_all())
        capabilities = tuple((worker.worker_id, worker.capabilities) for worker in workers)
        worker_statuses = {worker.worker_id: worker.status for worker in workers}
        return compile_plan(
            request,
            strategy,
            capabilities,
            job_id=job_id,
            chunking=ChunkingLimits(
                max_chunk_length=self._settings.workers.chunking.max_chunk_length,
                chars_per_token=self._settings.chars_per_token,
            ),
            source_root=self._settings.orchestrator.data_dir,
            worker_statuses=worker_statuses,
        )

    async def preview_job(self, request: JobRequest, strategy: ExecutorStrategy) -> Plan:
        """Compile a plan without persisting or executing a job."""
        if not self._started:
            msg = "Orchestrator.start() must be called before preview_job()"
            raise RuntimeError(msg)
        return await self._compile_plan(request, strategy)

    async def get_plan(self, plan_id: str) -> Plan:
        """Load a persisted plan without exposing the cache implementation."""
        return await asyncio.to_thread(self._cache.load_plan, plan_id)

    @staticmethod
    async def _await_shielded_cleanup(task: asyncio.Task[None]) -> None:
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.shield(task)

    async def _execute(self, tracked: TrackedJob) -> None:
        """Run the plan executor and update job status.

        Reconciles cancellation and unexpected failures with a terminal status
        before re-raising so callers can observe the original task failure.
        """
        try:
            with bind_job_id(tracked.job_id):
                await self._run_execution(tracked)
        except asyncio.CancelledError as exc:
            tracked.status = PlanStatus.FAILED
            operator_cancelled = tracked.job_id in self._operator_cancellation_requested
            reason = (
                "cancelled by operator" if operator_cancelled else str(exc) or "execution cancelled during shutdown"
            )
            self._record_cancellation(tracked, reason=reason)
            try:
                await self._persist_shielded(tracked)
                await self._publish_event(tracked, "job cancelled")
            except Exception as persist_exc:
                _log_unexpected(f"Failed to persist job {tracked.job_id} after cancellation", persist_exc)
                if operator_cancelled or not isinstance(persist_exc, (OSError, ConnectionError, StoreError)):
                    raise persist_exc from exc
            raise
        except Exception as exc:
            _log_unexpected(f"Job {tracked.job_id} failed in _execute", exc)
            tracked.status = PlanStatus.FAILED
            if tracked.result is None:
                self._record_failure(tracked, exc)
            else:
                tracked.result = replace(
                    tracked.result,
                    status=PlanStatus.FAILED,
                    errors=(
                        *tracked.result.errors,
                        StepError(
                            step_id=None,
                            worker_type=None,
                            worker_id=None,
                            message=sanitise_exc_message(exc),
                            timestamp=datetime.now(UTC),
                        ),
                    ),
                )
            try:
                await self._persist_shielded(tracked)
            except Exception as persist_exc:  # noqa: BLE001
                _log_unexpected(f"Failed to persist job {tracked.job_id} after execution failure", persist_exc)
            raise
        finally:
            try:
                finish_task = asyncio.create_task(self._events.finish(tracked.job_id))
                await self._await_shielded_cleanup(finish_task)
            finally:
                try:
                    release_job = getattr(self._handler, "release_job", None)
                    if release_job is not None:
                        release_task = asyncio.create_task(release_job(tracked.job_id))
                        await self._await_shielded_cleanup(release_task)
                finally:
                    self._active_jobs.discard(tracked.job_id)

    def _track_execution_task(self, tracked: TrackedJob) -> None:
        task = asyncio.create_task(self._execute(tracked))
        self._tasks.add(task)
        self._execution_tasks[tracked.job_id] = task

        def _discard(done: asyncio.Task[None]) -> None:
            self._tasks.discard(done)
            if self._execution_tasks.get(tracked.job_id) is done:
                self._execution_tasks.pop(tracked.job_id, None)
            self._operator_cancellation_requested.discard(tracked.job_id)
            if done.cancelled():
                return
            exc = done.exception()
            if exc is not None:
                logger.error(
                    "Job task failed after completion: %s",
                    exc,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )

        task.add_done_callback(_discard)

    def _track_persist(self, tracked: TrackedJob) -> asyncio.Task[None]:
        task = asyncio.create_task(self._job_store.put(tracked))
        self._background_persists.add(task)
        self._background_persists_by_job.setdefault(tracked.job_id, set()).add(task)

        def _discard(done: asyncio.Task[None]) -> None:
            self._background_persists.discard(done)
            job_tasks = self._background_persists_by_job.get(tracked.job_id)
            if job_tasks is not None:
                job_tasks.discard(done)
                if not job_tasks:
                    self._background_persists_by_job.pop(tracked.job_id, None)
            if done.cancelled():
                return
            exc = done.exception()
            if exc is not None:
                logger.error(
                    "Background persist failed for job %s: %s",
                    tracked.job_id,
                    exc,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )

        task.add_done_callback(_discard)
        return task

    async def _persist_shielded(self, tracked: TrackedJob) -> None:
        """Persist a job without letting cancellation interrupt the store write."""
        await asyncio.shield(self._track_persist(tracked))

    async def _publish_event(self, tracked: TrackedJob, message: str) -> None:
        """Publish a progress event to the broker."""
        from acheron.core.schemas import JobLogEvent, JobProgress  # noqa: PLC0415

        ps = tracked.progress
        event = JobLogEvent(
            job_id=tracked.job_id,
            timestamp=datetime.now(tz=UTC),
            status=tracked.status,
            step_id=ps.current_step_id,
            worker_type=ps.current_worker_type,
            worker_id=public_optional_worker_id(ps.current_worker_id),
            progress=JobProgress(
                completed_steps=ps.completed_steps,
                total_steps=ps.total_steps,
                current_step_id=ps.current_step_id,
                current_worker_type=ps.current_worker_type,
                current_worker_id=public_optional_worker_id(ps.current_worker_id),
                eta_seconds=ps.eta_seconds,
            ),
            message=message,
        )
        await self._events.publish(event)

    async def _wait_for_background_persists(
        self,
        job_id: str | None = None,
        *,
        max_wait: float | None = None,
        raise_on_timeout: bool = False,
    ) -> set[asyncio.Task[None]]:
        if job_id is None:
            tasks = list(self._background_persists)
        else:
            tasks = list(self._background_persists_by_job.get(job_id, ()))
        if not tasks:
            return set()
        done, pending = await asyncio.wait(tasks, timeout=max_wait)
        if pending:
            pending_job_ids = sorted(
                job_id
                for job_id, job_tasks in self._background_persists_by_job.items()
                if job_tasks.intersection(pending)
            )
            job_suffix = f" for jobs {', '.join(pending_job_ids)}" if pending_job_ids else ""
            logger.warning(
                "Timed out waiting for %d background reconciliation writes%s%s",
                len(pending),
                f" for job {job_id}" if job_id else "",
                job_suffix,
            )
            if raise_on_timeout:
                msg = "Background reconciliation did not finish before the timeout"
                raise RuntimeError(msg)
        for task in done:
            if task.cancelled():
                continue
            result = task.exception()
            if result is not None:
                logger.error(
                    "Background persist failed while waiting: %s",
                    result,
                    exc_info=(type(result), result, result.__traceback__),
                )
        return pending

    async def _run_execution(self, tracked: TrackedJob) -> None:
        async with self._job_lifecycle_lock(tracked.job_id):
            db_job = await self._job_store.get(tracked.job_id)
            if db_job is None or db_job.status != PlanStatus.RUNNING:
                logger.warning(
                    "Idempotency guard: job %s has database status %s, skipping execution",
                    tracked.job_id,
                    db_job.status if db_job else "None",
                )
                return
            self._active_jobs.add(tracked.job_id)

        logger.info("Executing %s (%s strategy)", tracked.job_id, tracked.strategy.value)
        operator_cancelled = False
        try:
            if tracked.plan is None:
                tracked.status = PlanStatus.FAILED
                logger.error("No plan for %s", tracked.job_id)
            else:
                executor = self._create_executor(tracked)
                result = await executor.run(tracked.plan)
                operator_cancelled = tracked.job_id in self._operator_cancellation_requested
                if operator_cancelled:
                    tracked.status = PlanStatus.FAILED
                    self._record_cancellation(tracked, reason="cancelled by operator")
                else:
                    tracked.result = result
                    tracked.status = result.status
                logger.info(
                    "Completed %s: %s (%d/%d steps)",
                    tracked.job_id,
                    result.status,
                    result.completed_steps,
                    result.total_steps,
                )
        except Exception as exc:  # noqa: BLE001
            label = (
                f"Plan execution failed for {tracked.job_id}"
                if isinstance(exc, AcheronError)
                else f"Unexpected error executing {tracked.job_id}"
            )
            _log_unexpected(label, exc)
            self._record_failure(tracked, exc)
        await self._job_store.put(tracked)
        message = "job cancelled" if operator_cancelled else f"job {tracked.status.value}"
        await self._publish_event(tracked, message)

    def _create_executor(self, tracked: TrackedJob) -> Executor:
        handler = self._handler
        if tracked.strategy != ExecutorStrategy.STREAMING:

            async def caching_handler(step: PlanStep, plan: Plan) -> JobResult:
                if await self._step_cache.step_has_valid_cache(plan.job_id, step.step_id):
                    outputs = await self._step_cache.load_outputs(plan.job_id, step.step_id)
                    return JobResult(
                        job_id=plan.job_id,
                        status=JobStatus.SUCCESS,
                        outputs=outputs,
                        metrics=JobMetrics(duration_seconds=0.0),
                    )
                res = await self._handler(step, plan)
                if res.status == JobStatus.SUCCESS:
                    await self._step_cache.save_outputs(plan.job_id, step.step_id, res.outputs)
                return res

            handler = caching_handler

        async def progress_handler(step: PlanStep, plan: Plan) -> JobResult:
            tracked.progress.total_steps = len(plan.steps)
            tracked.progress.current_step_id = step.step_id
            tracked.progress.current_worker_type = step.type
            tracked.progress.current_worker_id = None
            await self._persist_shielded(tracked)
            await self._publish_event(tracked, f"step {step.step_id} started")
            res = await handler(step, plan)
            if (
                tracked.strategy != ExecutorStrategy.STREAMING
                and tracked.job_id not in self._operator_cancellation_requested
            ):
                self._record_step_progress(tracked, plan, step, res)
                await self._persist_shielded(tracked)
                await self._publish_event(tracked, f"step {step.step_id} completed")
            return res

        async def record_streaming_step(step: PlanStep, plan: Plan, result: JobResult) -> None:
            if tracked.job_id in self._operator_cancellation_requested:
                return
            self._record_step_progress(tracked, plan, step, result)
            await self._persist_shielded(tracked)
            await self._publish_event(tracked, f"step {step.step_id} completed")

        return create_executor(
            tracked.strategy,
            progress_handler,
            step_cache=self._step_cache,
            on_step_complete=record_streaming_step if tracked.strategy == ExecutorStrategy.STREAMING else None,
        )

    def _record_step_progress(
        self,
        tracked: TrackedJob,
        plan: Plan,
        step: PlanStep,
        result: JobResult,
    ) -> None:
        """Accumulate completed step metrics so a mid-plan cancel keeps partial state."""
        partial = tracked.result or PlanResult(
            plan_id=plan.plan_id,
            status=PlanStatus.RUNNING,
            completed_steps=0,
            total_steps=len(plan.steps),
            outputs=(),
            total_cost=0.0,
            total_duration_seconds=0.0,
        )
        error = (
            StepError(
                step_id=step.step_id,
                worker_type=step.type,
                worker_id=result.worker_id,
                message=result.error or "unknown error",
                timestamp=datetime.now(UTC),
            )
            if result.status is not JobStatus.SUCCESS
            else None
        )
        completed_steps = partial.completed_steps + int(result.status is JobStatus.SUCCESS)
        item = build_cost_breakdown(step, result)
        cost_breakdown = (*partial.cost_breakdown, *((item,) if item is not None else ()))
        tracked.result = replace(
            partial,
            completed_steps=completed_steps,
            outputs=(*partial.outputs, *result.outputs) if result.status is JobStatus.SUCCESS else partial.outputs,
            total_cost=partial.total_cost + estimate_cost(item),
            total_duration_seconds=partial.total_duration_seconds + result.metrics.duration_seconds,
            total_cost_basis=aggregate_cost_basis(cost_breakdown),
            cost_breakdown=cost_breakdown,
            errors=partial.errors + ((error,) if error is not None else ()),
        )
        tracked.progress.completed_steps = completed_steps
        tracked.progress.total_steps = len(plan.steps)
        tracked.progress.current_step_id = None if completed_steps >= len(plan.steps) else step.step_id
        tracked.progress.current_worker_type = None if completed_steps >= len(plan.steps) else step.type
        tracked.progress.current_worker_id = None if completed_steps >= len(plan.steps) else result.worker_id
        if result.status is JobStatus.SUCCESS:
            tracked.progress.successful_duration_seconds += result.metrics.duration_seconds
        if completed_steps >= len(plan.steps):
            tracked.progress.eta_seconds = 0.0
        elif completed_steps and tracked.progress.successful_duration_seconds:
            average_duration = tracked.progress.successful_duration_seconds / completed_steps
            tracked.progress.eta_seconds = max(0.0, average_duration * (len(plan.steps) - completed_steps))
        else:
            tracked.progress.eta_seconds = None

    def _record_cancellation(self, tracked: TrackedJob, *, reason: str) -> None:
        message = reason
        cancellation = StepError(
            step_id=tracked.progress.current_step_id,
            worker_type=tracked.progress.current_worker_type,
            worker_id=tracked.progress.current_worker_id,
            message=message,
            timestamp=datetime.now(UTC),
        )
        if tracked.result is None:
            tracked.result = self._new_failure_result(tracked, message)
            return
        errors = (
            tracked.result.errors
            if any(error.message == message for error in tracked.result.errors)
            else (*tracked.result.errors, cancellation)
        )
        tracked.result = replace(tracked.result, status=PlanStatus.FAILED, errors=errors)

    def _record_failure(self, tracked: TrackedJob, exc: BaseException) -> None:
        """Mark ``tracked`` as failed and build the resulting :class:`PlanResult`."""
        tracked.status = PlanStatus.FAILED
        if tracked.result is not None and tracked.result.errors:
            tracked.result = replace(tracked.result, status=PlanStatus.FAILED)
        else:
            tracked.result = self._new_failure_result(tracked, sanitise_exc_message(exc))

    def _new_failure_result(self, tracked: TrackedJob, error: str) -> PlanResult:
        return PlanResult(
            plan_id=tracked.plan.plan_id if tracked.plan else tracked.job_id,
            status=PlanStatus.FAILED,
            completed_steps=tracked.progress.completed_steps,
            total_steps=len(tracked.plan.steps) if tracked.plan else tracked.progress.total_steps,
            outputs=tracked.result.outputs if tracked.result is not None else (),
            total_cost=tracked.result.total_cost if tracked.result is not None else 0.0,
            total_duration_seconds=tracked.result.total_duration_seconds if tracked.result is not None else 0.0,
            total_cost_basis=tracked.result.total_cost_basis if tracked.result is not None else None,
            cost_breakdown=tracked.result.cost_breakdown if tracked.result is not None else (),
            errors=(
                StepError(
                    step_id=tracked.progress.current_step_id,
                    worker_type=tracked.progress.current_worker_type,
                    worker_id=tracked.progress.current_worker_id,
                    message=error,
                    timestamp=datetime.now(UTC),
                ),
            ),
        )

    async def get_job(self, job_id: str) -> TrackedJob | None:
        """Retrieve a tracked job by ID."""
        return await self._job_store.get(job_id)

    def _job_lifecycle_lock(self, job_id: str) -> asyncio.Lock:
        with self._job_lock_registry_mutex:
            lock = self._job_locks.get(job_id)
            if lock is None:
                lock = asyncio.Lock()
                self._job_locks[job_id] = lock
            return lock

    def _input_identity(self, source_path: str) -> str:
        candidate = Path(source_path)
        data_dir = self._settings.orchestrator.data_dir
        resolved = (candidate if candidate.is_absolute() else data_dir / candidate).resolve(strict=False)
        try:
            return resolved.relative_to(data_dir).as_posix()
        except ValueError:
            return f"external:{resolved}"

    def _input_reference_lock(self, identity: str) -> asyncio.Lock:
        parts = Path(identity).parts
        lock_identity = "/".join(parts[:2]) if parts[:1] == ("inputs",) and parts[1:] else identity
        with self._input_lock_registry_mutex:
            lock = self._input_locks.get(lock_identity)
            if lock is None:
                lock = asyncio.Lock()
                self._input_locks[lock_identity] = lock
            return lock

    async def preview_cleanup(self, policy: RetentionPolicy, *, now: datetime | None = None) -> CleanupReport:
        """Preview retention cleanup without filesystem or record mutation."""
        return await self._retention.preview(policy, now=now)

    async def apply_cleanup(self, policy: RetentionPolicy, *, now: datetime | None = None) -> CleanupReport:
        """Apply retention cleanup with lifecycle and input-reference locking."""
        return await self._retention.apply(policy, now=now)

    async def mark_failed_by_admin(self, job_id: str, *, reason: str) -> TrackedJob:
        """Mark a non-active, non-terminal job failed from an admin operation."""
        async with self._job_lifecycle_lock(job_id):
            await self._wait_for_background_persists(
                job_id,
                max_wait=self._settings.orchestrator.shutdown_drain_seconds,
                raise_on_timeout=True,
            )
            tracked = await self._job_store.get(job_id)
            if tracked is None:
                message = f"Job not found: {job_id}"
                raise JobNotFoundError(message)
            if tracked.job_id in self._active_jobs:
                message = f"Job {job_id} has an active execution task"
                raise JobAlreadyRunningError(message, remediation=f"acheron job status {job_id}")
            if tracked.status in {PlanStatus.COMPLETED, PlanStatus.FAILED, PlanStatus.PARTIAL}:
                message = f"Job {job_id} is already {tracked.status.value}"
                raise JobNotCancellableError(message, remediation=f"acheron job status {job_id}")

            safe_reason = _sanitize_admin_reason(reason)
            tracked.status = PlanStatus.FAILED
            if tracked.result is None:
                tracked.result = replace(
                    self._new_failure_result(tracked, safe_reason),
                    errors=(
                        StepError(
                            step_id=None,
                            worker_type=None,
                            worker_id=None,
                            message=safe_reason,
                            timestamp=datetime.now(UTC),
                        ),
                    ),
                )
            else:
                tracked.result = replace(
                    tracked.result,
                    status=PlanStatus.FAILED,
                    errors=(
                        *tracked.result.errors,
                        StepError(
                            step_id=None,
                            worker_type=None,
                            worker_id=None,
                            message=safe_reason,
                            timestamp=datetime.now(UTC),
                        ),
                    ),
                )
            await self._job_store.put(tracked)
            await self._publish_event(tracked, "job failed by administrator")
            await self._events.finish(job_id)
            return tracked

    async def reap_stale_jobs(
        self,
        *,
        older_than_seconds: float,
        reason: str,
        now: datetime | None = None,
    ) -> ReapResult:
        """Transition persisted stale jobs that have no active execution task."""
        effective_now = datetime.now(UTC) if now is None else now
        if effective_now.tzinfo is None or effective_now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        effective_now = effective_now.astimezone(UTC)
        candidates = await self._job_store.list(
            JobQuery(status=PlanStatus.RUNNING, older_than_seconds=older_than_seconds),
            now=effective_now,
        )
        reaped: list[str] = []
        for candidate in sorted(candidates, key=lambda job: job.job_id)[:1000]:
            if candidate.job_id in self._active_jobs:
                continue
            try:
                await self.mark_failed_by_admin(candidate.job_id, reason=reason)
            except JobAlreadyRunningError, JobNotCancellableError:
                continue
            reaped.append(candidate.job_id)
        return ReapResult(job_ids=tuple(reaped))

    async def archive_job(self, job_id: str) -> TrackedJob:
        """Archive a terminal job while preserving its complete record."""
        async with self._job_lifecycle_lock(job_id):
            await self._wait_for_background_persists(
                job_id,
                max_wait=self._settings.orchestrator.shutdown_drain_seconds,
                raise_on_timeout=True,
            )
            tracked = await self._job_store.get(job_id)
            if tracked is None:
                message = f"Job not found: {job_id}"
                raise JobNotFoundError(message)
            if tracked.archived_at is not None:
                return tracked
            if tracked.job_id in self._active_jobs or tracked.status not in {
                PlanStatus.COMPLETED,
                PlanStatus.FAILED,
                PlanStatus.PARTIAL,
            }:
                message = f"Job {job_id} is {tracked.status.value} and cannot be archived"
                raise JobNotCancellableError(message, remediation=f"acheron job status {job_id}")
            archived = await self._job_store.archive(job_id)
            await self._publish_event(archived, "job archived")
            return archived

    async def get_job_cost(self, job_id: str) -> JobCostResponse | None:
        """Map persisted per-step cost evidence to the public cost response."""
        tracked = await self._job_store.get(job_id)
        if tracked is None:
            return None
        result = tracked.result
        return JobCostResponse(
            job_id=tracked.job_id,
            total_cost=result.total_cost if result is not None else 0.0,
            total_cost_basis=result.total_cost_basis if result is not None else None,
            cost_breakdown=(
                [
                    CostBreakdownResponse(
                        step_id=item.step_id,
                        worker_type=item.worker_type,
                        worker_id=public_worker_id(item.worker_id),
                        gpu_seconds=item.gpu_seconds,
                        cost=item.estimate.cost,
                        basis=item.estimate.basis,
                        rate_per_hour=item.estimate.rate_per_hour,
                        gpu_type=public_gpu_type(item.estimate.gpu_type),
                        secure_cloud=item.estimate.secure_cloud,
                        queried_at=item.estimate.queried_at,
                        cache_age_seconds=item.estimate.cache_age_seconds,
                    )
                    for item in result.cost_breakdown
                ]
                if result is not None
                else []
            ),
        )

    async def get_cost_summary(self, window: str) -> CostSummaryResponse:
        """Aggregate known estimates for terminal, non-archived jobs."""
        until = datetime.now(UTC)
        since: datetime | None = None
        if window != "all":
            hours = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30}.get(window)
            if hours is None:
                msg = f"Unsupported cost window: {window}"
                raise ValueError(msg)
            since = until - timedelta(hours=hours)

        terminal = {PlanStatus.COMPLETED, PlanStatus.FAILED, PlanStatus.PARTIAL}
        selected = [
            job
            for job in await self._job_store.list(
                JobQuery(since=since, before=until),
                now=until,
            )
            if job.status in terminal
        ]
        total_cost = 0.0
        unknown_cost_jobs = 0
        for job in selected:
            result = job.result
            if result is None or result.total_cost_basis in {None, CostBasis.UNKNOWN}:
                unknown_cost_jobs += 1
            total_cost += _known_result_cost(result)
        return CostSummaryResponse(
            window=window,
            since=since,
            until=until,
            total_cost=total_cost,
            job_count=len(selected),
            unknown_cost_jobs=unknown_cost_jobs,
            jobs=[
                CostJobSnapshot(
                    job_id=job.job_id,
                    status=job.status,
                    total_cost=_known_result_cost(result),
                    total_duration_seconds=result.total_duration_seconds if result is not None else 0.0,
                    completed_steps=result.completed_steps if result is not None else job.progress.completed_steps,
                    total_steps=result.total_steps if result is not None else job.progress.total_steps,
                    total_cost_basis=(result.total_cost_basis if result and result.total_cost_basis else None),
                )
                for job in selected[:1000]
                for result in (job.result,)
            ],
        )

    async def cancel_job(self, job_id: str) -> TrackedJob:
        """Cancel an active job and wait for its failed state to persist."""
        lock = self._job_locks.get(job_id)
        if lock is None:
            lock = asyncio.Lock()
            self._job_locks[job_id] = lock

        async with lock:
            await self._wait_for_background_persists(
                job_id,
                max_wait=self._settings.orchestrator.shutdown_drain_seconds,
                raise_on_timeout=True,
            )
            tracked = await self._job_store.get(job_id)
            if tracked is None:
                msg = f"Job not found: {job_id}"
                raise JobNotFoundError(msg)
            if tracked.status in {PlanStatus.COMPLETED, PlanStatus.FAILED, PlanStatus.PARTIAL}:
                msg = f"Job {job_id} is already {tracked.status.value}"
                raise JobNotCancellableError(
                    msg,
                    remediation=f"acheron job status {job_id}",
                )
            task = self._execution_tasks.get(job_id)
            if task is None:
                msg = f"Job {job_id} has no active execution task"
                raise JobNotCancellableError(
                    msg,
                    remediation=f"acheron job status {job_id}",
                )

            self._operator_cancellation_requested.add(job_id)
            task.cancel("cancelled by operator")
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await self._wait_for_background_persists(
                job_id,
                max_wait=self._settings.orchestrator.shutdown_drain_seconds,
                raise_on_timeout=True,
            )
            final = await self._job_store.get(job_id)
            if final is None:
                msg = f"Job not found: {job_id}"
                raise JobNotFoundError(msg)
            return final

    async def resume_job(
        self,
        job_id: str,
        *,
        invalidate_steps: Sequence[str] = (),
        invalidate_chapters: Sequence[int] = (),
    ) -> TrackedJob:
        """Resume a tracked job with selected step or chapter cache invalidation."""
        lock = self._job_locks.get(job_id)
        if lock is None:
            lock = asyncio.Lock()
            self._job_locks[job_id] = lock

        async with lock:
            current = await self._job_store.get(job_id)
            if current is not None and current.status == PlanStatus.RUNNING:
                msg = f"Job {job_id} is already running"
                raise JobAlreadyRunningError(msg, remediation=f"acheron job cancel {job_id}")
            prior_execution = self._execution_tasks.get(job_id)
            if (
                prior_execution is not None
                and prior_execution is not asyncio.current_task()
                and not prior_execution.done()
            ):
                async with asyncio.timeout(self._settings.orchestrator.shutdown_drain_seconds):
                    await asyncio.shield(asyncio.gather(prior_execution, return_exceptions=True))
            await self._wait_for_background_persists(
                job_id,
                max_wait=self._settings.orchestrator.shutdown_drain_seconds,
                raise_on_timeout=True,
            )
            tracked = await self._job_store.get(job_id)
            if tracked is None:
                msg = f"Job not found: {job_id}"
                raise JobNotFoundError(msg)
            if tracked.status == PlanStatus.RUNNING:
                msg = f"Job {job_id} is already running"
                raise JobAlreadyRunningError(msg, remediation=f"acheron job cancel {job_id}")
            if tracked.status not in {PlanStatus.FAILED, PlanStatus.PARTIAL}:
                msg = f"Job {job_id} has status {tracked.status.value} and cannot be resumed"
                raise JobNotResumableError(msg, remediation=f"acheron job status {job_id}")
            if tracked.plan is None:
                msg = f"Job {job_id} has no saved plan to resume"
                raise NoPlanToResumeError(msg, remediation="acheron job submit <source> --src ... --dest ...")

            invalidated_steps = _resolve_invalidation_steps(
                tracked.plan,
                invalidate_steps,
                invalidate_chapters,
            )
            await self._step_cache.invalidate_steps(job_id, invalidated_steps)

            async with self._lifecycle_lock:
                if self._shutting_down:
                    msg = "Orchestrator is shutting down; jobs cannot be resumed"
                    raise RuntimeError(msg)
                prior_terminal = await self._events.start(job_id)
                try:
                    self._active_jobs.add(job_id)
                    tracked.status = PlanStatus.RUNNING
                    tracked.result = None
                    await self._job_store.put(tracked)
                    self._track_execution_task(tracked)
                except BaseException:
                    self._active_jobs.discard(job_id)
                    await self._events.restore(job_id, prior_terminal)
                    raise
            return tracked

    async def list_jobs(self, query: JobQuery = JobQuery()) -> tuple[TrackedJob, ...]:  # noqa: B008
        """List tracked jobs using a typed query."""
        return await self._job_store.list(query)

    async def get_capabilities(
        self,
        src: str | None = None,
        dst: str | None = None,
    ) -> list[LanguagePair]:
        """Aggregate language pairs achievable by the planner.

        Delegates to CapabilityAggregator. Kept on the orchestrator as a
        convenience for callers that already have an Orchestrator reference.
        """
        return await self._capabilities.get_capabilities(src=src, dst=dst)

    async def register_worker(  # noqa: PLR0913
        self,
        worker_id: str,
        endpoint: str,
        transport: str,
        capabilities: WorkerCapabilities,
        metadata: dict[str, JsonValue] | None = None,
        *,
        handler: LocalJobHandler | None = None,
    ) -> None:
        """Register a worker in the registry.

        For ``transport="local"`` workers, pass ``handler`` to make the
        in-process handler available to the step handler. Storing the handler
        in ``metadata`` is not supported because metadata is persisted by
        backends like Redis, and handlers are not JSON-serializable.
        """
        if transport == "local" and handler is not None:
            self._local_handlers[worker_id] = handler
        await self._registry.register(worker_id, endpoint, transport, capabilities, metadata=metadata)
        logger.info("Registered worker %s (%s, %s)", worker_id, capabilities.worker_type.value, transport)

    async def list_workers(self) -> tuple[RegisteredWorker, ...]:
        """List all registered workers."""
        return await self._registry.list_all()
