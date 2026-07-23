"""Orchestrator — service layer wiring registry, planner, executors, and cache."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import shutil
import time
import uuid
import weakref
from dataclasses import replace
from typing import TYPE_CHECKING

from acheron.core.errors import (
    AcheronError,
    JobAlreadyRunningError,
    JobNotFoundError,
    sanitise_exc_message,
)
from acheron.core.models import (
    AudioRequest,
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
    WorkerCapabilities,
    WorkerType,
)
from acheron.core.planner import ChunkingLimits, compile_plan
from acheron.shell.cache import InMemoryStepCache, StepCache
from acheron.shell.capabilities import CapabilityAggregator, LanguagePair
from acheron.shell.config import Settings, load_settings
from acheron.shell.cost import aggregate_cost_basis
from acheron.shell.executors import create_executor
from acheron.shell.health import HealthMonitor
from acheron.shell.health_providers import create_health_providers
from acheron.shell.job_store import TrackedJob
from acheron.shell.local_handlers import (
    LocalJobHandler,
    all_languages_caps,
)
from acheron.shell.logging_context import bind_job_id
from acheron.shell.step_handler import create_step_handler
from acheron.shell.stores import create_job_store
from acheron.shell.stores.base import StoreError

if TYPE_CHECKING:
    from acheron.core.interfaces import Executor
    from acheron.core.models import JobRequest
    from acheron.shell.cache import PlanCache
    from acheron.shell.executors._utils import StepHandler
    from acheron.shell.registry import RegisteredWorker
    from acheron.shell.stores.base import JobStore, WorkerStore

logger = logging.getLogger(__name__)


def _log_unexpected(label: str, exc: BaseException) -> None:
    """Log an unexpected exception with a label; the caller decides whether to re-raise."""
    logger.exception("%s: %s", label, exc)


_MIN_TOKEN_LENGTH = 32
_PUBLIC_TOKEN_VALUES = frozenset({"dev-registration-token"})


def _validate_registration_token(token: str | None) -> None:
    if token is None:
        return
    if token in _PUBLIC_TOKEN_VALUES:
        msg = (
            f"ACHERON_REGISTRATION_TOKEN is set to the publicly-known value {token!r}. "
            f"Generate a fresh token with `openssl rand -hex 32` and set it in your environment."
        )
        raise RuntimeError(msg)
    if len(token) < _MIN_TOKEN_LENGTH:
        msg = (
            f"ACHERON_REGISTRATION_TOKEN is too short ({len(token)} chars); "
            f"minimum is {_MIN_TOKEN_LENGTH} characters. "
            f"Generate a fresh token with `openssl rand -hex 32`."
        )
        raise RuntimeError(msg)


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
            self._settings = Settings(orchestrator=default.orchestrator.model_copy(update={"data_dir": cache.data_dir}))
        else:
            self._settings = settings
        self._registry = registry
        self._cache = cache
        self._step_cache = step_cache if step_cache is not None else InMemoryStepCache()
        self._local_handlers: dict[str, LocalJobHandler] = {}
        self._handler = handler or create_step_handler(
            registry,
            local_handlers=self._local_handlers,
            data_dir=self._settings.orchestrator.data_dir,
        )
        self._job_store = job_store if job_store is not None else create_job_store()
        self._capabilities = CapabilityAggregator(registry)
        self._tasks: set[asyncio.Task[None]] = set()
        self._background_persists: set[asyncio.Task[None]] = set()
        self._background_persists_by_job: dict[str, set[asyncio.Task[None]]] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._active_jobs: set[str] = set()
        self._job_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._started = False
        self._shutting_down = False
        self._health_providers = create_health_providers(self._settings)
        self._health_monitor = HealthMonitor(
            registry,
            interval=float(self._settings.orchestrator.health_check_interval_seconds),
            providers=self._health_providers,
        )

    @property
    def settings(self) -> Settings:
        """Get the configuration settings."""
        return self._settings

    def _verify_data_dir_writable(self) -> None:
        """Ensure the step-cache data dir exists and is writable. Raises AcheronError otherwise."""
        data_dir = self._step_cache.data_dir
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
        await self._load_or_create_registration_token()
        _validate_registration_token(self._settings.orchestrator.registration_token)

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

    async def submit_job(self, request: JobRequest, strategy: ExecutorStrategy) -> TrackedJob:
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

        capabilities = tuple(w.capabilities for w in await self._registry.list_all())
        plan = compile_plan(
            request,
            strategy,
            capabilities,
            job_id=job_id,
            chunking=ChunkingLimits(
                max_chunk_length=self._settings.workers.chunking.max_chunk_length,
                chars_per_token=self._settings.chars_per_token,
            ),
        )
        self._cache.save_plan(plan)
        await self._invalidate_handler_cache()
        logger.info("Plan compiled for %s: %s (%d steps)", job_id, plan.plan_id, len(plan.steps))

        tracked = TrackedJob(
            job_id=job_id,
            request=request,
            strategy=strategy,
            plan=plan,
            status=PlanStatus.RUNNING,
        )
        async with self._lifecycle_lock:
            if self._shutting_down:
                msg = "Orchestrator is shutting down; new jobs are not accepted"
                raise RuntimeError(msg)
            await self._job_store.put(tracked)
            self._active_jobs.add(tracked.job_id)
            self._track_execution_task(tracked)

        return tracked

    async def _execute(self, tracked: TrackedJob) -> None:
        """Run the plan executor and update job status.

        Reconciles cancellation and unexpected failures with a terminal status
        before re-raising so callers can observe the original task failure.
        """
        try:
            with bind_job_id(tracked.job_id):
                await self._run_execution(tracked)
        except asyncio.CancelledError:
            tracked.status = PlanStatus.FAILED
            self._record_cancellation(tracked)
            try:
                await self._persist_shielded(tracked)
            except (OSError, ConnectionError, StoreError) as exc:
                _log_unexpected(f"Failed to persist job {tracked.job_id} after cancellation", exc)
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
                    errors=(*tracked.result.errors, sanitise_exc_message(exc)),
                )
            try:
                await self._persist_shielded(tracked)
            except Exception as persist_exc:  # noqa: BLE001
                _log_unexpected(f"Failed to persist job {tracked.job_id} after execution failure", persist_exc)
            raise
        finally:
            self._active_jobs.discard(tracked.job_id)
            release_job = getattr(self._handler, "release_job", None)
            if release_job is not None:
                await release_job(tracked.job_id)

    def _track_execution_task(self, tracked: TrackedJob) -> None:
        task = asyncio.create_task(self._execute(tracked))
        self._tasks.add(task)

        def _discard(done: asyncio.Task[None]) -> None:
            self._tasks.discard(done)
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
            logger.warning(
                "Timed out waiting for %d background reconciliation writes%s",
                len(pending),
                f" for job {job_id}" if job_id else "",
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

    async def _invalidate_handler_cache(self) -> None:
        """Invalidate the step handler's worker-instance cache, if it exposes one.

        The default :class:`CachingStepHandler` pools worker instances across
        steps; we drop the pool at the start of each new plan so a worker
        re-registration, removal, or endpoint change is reflected on the next
        dispatch.
        """
        invalidate = getattr(self._handler, "_invalidate_worker_cache", None)
        if invalidate is not None:
            await invalidate()

    async def _run_execution(self, tracked: TrackedJob) -> None:
        db_job = await self._job_store.get(tracked.job_id)
        if db_job is None or db_job.status != PlanStatus.RUNNING:
            logger.warning(
                "Idempotency guard: job %s has database status %s, skipping execution",
                tracked.job_id,
                db_job.status if db_job else "None",
            )
            return

        logger.info("Executing %s (%s strategy)", tracked.job_id, tracked.strategy.value)
        self._active_jobs.add(tracked.job_id)
        try:
            try:
                if tracked.plan is None:
                    tracked.status = PlanStatus.FAILED
                    logger.error("No plan for %s", tracked.job_id)
                else:
                    executor = self._create_executor(tracked)
                    result = await executor.run(tracked.plan)
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
        finally:
            self._active_jobs.discard(tracked.job_id)

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
            res = await handler(step, plan)
            self._record_step_progress(tracked, plan, res)
            return res

        if tracked.strategy == ExecutorStrategy.STREAMING:

            def on_step_complete(_step: PlanStep, plan: Plan, result: JobResult) -> None:
                self._record_step_progress(tracked, plan, result)

            return create_executor(
                tracked.strategy,
                self._handler,
                step_cache=self._step_cache,
                on_step_complete=on_step_complete,
            )
        return create_executor(tracked.strategy, progress_handler, step_cache=self._step_cache)

    def _record_step_progress(self, tracked: TrackedJob, plan: Plan, result: JobResult) -> None:
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
        tracked.result = replace(
            partial,
            completed_steps=partial.completed_steps + int(result.status is JobStatus.SUCCESS),
            outputs=(*partial.outputs, *result.outputs) if result.status is JobStatus.SUCCESS else partial.outputs,
            total_cost=partial.total_cost + (result.metrics.cost_estimate or 0.0),
            total_duration_seconds=partial.total_duration_seconds + result.metrics.duration_seconds,
            total_cost_basis=aggregate_cost_basis(
                [
                    JobMetrics(duration_seconds=0.0, cost_basis=partial.total_cost_basis),
                    result.metrics,
                ]
            ),
            errors=partial.errors + ((result.error,) if result.error else ()),
        )

    def _record_cancellation(self, tracked: TrackedJob) -> None:
        message = "execution cancelled during shutdown"
        if tracked.result is None:
            tracked.result = self._new_failure_result(tracked, message)
            return
        errors = tracked.result.errors if message in tracked.result.errors else (*tracked.result.errors, message)
        tracked.result = replace(tracked.result, status=PlanStatus.FAILED, errors=errors)

    def _record_failure(self, tracked: TrackedJob, exc: BaseException) -> None:
        """Mark ``tracked`` as failed and build the resulting :class:`PlanResult`."""
        tracked.status = PlanStatus.FAILED
        tracked.result = self._new_failure_result(tracked, sanitise_exc_message(exc))

    def _new_failure_result(self, tracked: TrackedJob, error: str) -> PlanResult:
        return PlanResult(
            plan_id=tracked.plan.plan_id if tracked.plan else tracked.job_id,
            status=PlanStatus.FAILED,
            completed_steps=0,
            total_steps=len(tracked.plan.steps) if tracked.plan else 0,
            outputs=(),
            total_cost=0.0,
            total_duration_seconds=0.0,
            errors=(error,),
        )

    async def get_job(self, job_id: str) -> TrackedJob | None:
        """Retrieve a tracked job by ID."""
        return await self._job_store.get(job_id)

    async def resume_job(self, job_id: str, force_fresh: bool = False) -> TrackedJob:  # noqa: FBT001, FBT002
        """Resume a tracked job, optionally discarding existing step cache."""
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
            if tracked.status == PlanStatus.RUNNING:
                if job_id in self._active_jobs:
                    msg = f"Job {job_id} is already running"
                    raise JobAlreadyRunningError(msg)
                logger.warning(
                    "Job %s status is RUNNING but not active in this process; overriding stale state", job_id
                )
            if tracked.plan is None:
                msg = f"Job {job_id} has no saved plan to resume"
                raise AcheronError(msg)

            if force_fresh:
                job_dir = self._step_cache.data_dir / job_id
                logger.info("force_fresh=True: deleting job step-cache directory: %s", job_dir)
                if job_dir.exists():
                    await asyncio.to_thread(shutil.rmtree, job_dir, ignore_errors=True)

            async with self._lifecycle_lock:
                if self._shutting_down:
                    msg = "Orchestrator is shutting down; jobs cannot be resumed"
                    raise RuntimeError(msg)
                self._active_jobs.add(job_id)
                tracked.status = PlanStatus.RUNNING
                tracked.result = None
                await self._job_store.put(tracked)
                self._track_execution_task(tracked)
            return tracked

    async def list_jobs(self) -> tuple[TrackedJob, ...]:
        """List all tracked jobs."""
        return await self._job_store.list_all()

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
        logger.info(
            "Registered worker %s (%s, %s → %s)", worker_id, capabilities.worker_type.value, endpoint, transport
        )

    async def list_workers(self) -> tuple[RegisteredWorker, ...]:
        """List all registered workers."""
        return await self._registry.list_all()
