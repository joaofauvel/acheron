"""Orchestrator — service layer wiring registry, planner, executors, and cache."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import shutil
import uuid
import weakref
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
    JsonValue,
    PlanResult,
    PlanStatus,
    WorkerCapabilities,
    WorkerType,
)
from acheron.core.planner import compile_plan, validate_chunking_fits_workers
from acheron.shell.cache import StepCache
from acheron.shell.capabilities import CapabilityAggregator, LanguagePair
from acheron.shell.config import Settings, load_settings
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

if TYPE_CHECKING:
    from acheron.core.models import JobRequest
    from acheron.shell.cache import PlanCache
    from acheron.shell.executors._utils import StepHandler
    from acheron.shell.registry import RegisteredWorker
    from acheron.shell.stores.base import JobStore, WorkerStore

logger = logging.getLogger(__name__)


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
        step_cache: StepCache | None = None,
        settings: Settings | None = None,
    ) -> None:
        if settings is None:
            default = load_settings()
            self._settings = Settings(orchestrator=default.orchestrator.model_copy(update={"data_dir": cache.data_dir}))
        else:
            self._settings = settings
        self._registry = registry
        self._cache = cache
        self._step_cache = step_cache if step_cache is not None else StepCache(self._settings.orchestrator.data_dir)
        self._local_handlers: dict[str, LocalJobHandler] = {}
        self._handler = handler or create_step_handler(
            registry,
            local_handlers=self._local_handlers,
            data_dir=self._settings.orchestrator.data_dir,
        )
        self._job_store = job_store if job_store is not None else create_job_store()
        self._capabilities = CapabilityAggregator(registry)
        self._tasks: set[asyncio.Task[None]] = set()
        self._active_jobs: set[str] = set()
        self._job_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._started = False
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
        Callers must drain in-flight ``_execute`` tasks via ``shutdown()`` first —
        otherwise a job whose ``put()`` races the pool teardown will see a
        ``ConnectionError`` mid-flight.
        """
        for close_attr in ("_registry", "_job_store"):
            try:
                await getattr(self, close_attr).close()
            except Exception:
                logger.exception("Failed to close %s", close_attr)

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

        if not self._settings.orchestrator.registration_token:
            token_file = self._settings.orchestrator.data_dir / ".registration_token"
            if token_file.is_file():
                try:
                    token = token_file.read_text(encoding="utf-8").strip()
                    self._settings.orchestrator.registration_token = token
                    logger.info("Loaded persistent registration token from %s", token_file)
                except OSError as exc:
                    logger.warning("Failed to read persistent registration token from %s: %s", token_file, exc)

            if not self._settings.orchestrator.registration_token:
                token = secrets.token_hex(16)
                self._settings.orchestrator.registration_token = token
                try:
                    token_file.write_text(token, encoding="utf-8")
                    token_file.chmod(0o600)
                    logger.info("Generated and persisted registration token to %s", token_file)
                except OSError as exc:
                    logger.warning("Generated registration token but failed to persist to %s: %s", token_file, exc)

        _validate_registration_token(self._settings.orchestrator.registration_token)

        await self._registry.connect()
        await self._job_store.connect()
        self._started = True
        await self._register_built_in_local_workers()
        await self._health_monitor.start()

    async def shutdown(self) -> None:
        """Stop the health monitor background task.

        Does not cancel ``_execute`` tasks spawned by ``submit_job``; those are
        tracked on ``self._tasks`` and reaped when the event loop tears down.
        For explicit cleanup of stores (Redis pools, file handles), call
        :meth:`close` separately.
        """
        await self._health_monitor.stop()

    async def submit_job(self, request: JobRequest, strategy: ExecutorStrategy) -> TrackedJob:
        """Compile a plan and execute it. Returns the tracked job immediately.

        Raises:
            RuntimeError: If ``start()`` has not been called. Local workers
                are registered during start(); submitting before start would
                fail at execution with a confusing WorkerError.
            AcheronError: If plan compilation fails (e.g. invalid language path).
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
        plan = compile_plan(request, strategy, capabilities, job_id=job_id)
        validate_chunking_fits_workers(
            capabilities,
            self._settings.workers.chunking.max_chunk_length,
            chars_per_token=self._settings.chars_per_token,
        )
        self._cache.save_plan(plan)
        logger.info("Plan compiled for %s: %s (%d steps)", job_id, plan.plan_id, len(plan.steps))

        tracked = TrackedJob(
            job_id=job_id,
            request=request,
            strategy=strategy,
            plan=plan,
            status=PlanStatus.RUNNING,
        )
        await self._job_store.put(tracked)

        task = asyncio.create_task(self._execute(tracked))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

        return tracked

    async def _execute(self, tracked: TrackedJob) -> None:
        """Run the plan executor and update job status."""
        with bind_job_id(tracked.job_id):
            await self._run_execution(tracked)

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
                    handler = self._handler
                    if tracked.strategy != ExecutorStrategy.STREAMING:
                        from acheron.core.models import (  # noqa: PLC0415
                            JobMetrics,
                            JobResult,
                            JobStatus,
                            Plan,
                            PlanStep,
                        )

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

                    executor = create_executor(
                        tracked.strategy,
                        handler,
                        step_cache=self._step_cache,
                    )
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
            except AcheronError as exc:
                logger.exception("Plan execution failed for %s", tracked.job_id)
                tracked.status = PlanStatus.FAILED
                tracked.result = PlanResult(
                    plan_id=tracked.plan.plan_id if tracked.plan else tracked.job_id,
                    status=PlanStatus.FAILED,
                    completed_steps=0,
                    total_steps=len(tracked.plan.steps) if tracked.plan else 0,
                    outputs=(),
                    total_cost=0.0,
                    total_duration_seconds=0.0,
                    errors=(sanitise_exc_message(exc),),
                )
            except Exception as exc:
                logger.exception("Unexpected error executing %s", tracked.job_id)
                tracked.status = PlanStatus.FAILED
                tracked.result = PlanResult(
                    plan_id=tracked.plan.plan_id if tracked.plan else tracked.job_id,
                    status=PlanStatus.FAILED,
                    completed_steps=0,
                    total_steps=len(tracked.plan.steps) if tracked.plan else 0,
                    outputs=(),
                    total_cost=0.0,
                    total_duration_seconds=0.0,
                    errors=(sanitise_exc_message(exc),),
                )
            await self._job_store.put(tracked)
        finally:
            self._active_jobs.discard(tracked.job_id)

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

            self._active_jobs.add(job_id)
            tracked.status = PlanStatus.RUNNING
            tracked.result = None
            await self._job_store.put(tracked)

            task = asyncio.create_task(self._execute(tracked))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
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
