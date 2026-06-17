"""Orchestrator — service layer wiring registry, planner, executors, and cache."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from acheron.core.errors import AcheronError
from acheron.core.models import EpubRequest
from acheron.core.planner import compile_plan
from acheron.shell.executors import create_executor
from acheron.shell.health import HealthMonitor
from acheron.shell.job_store import JobStore, TrackedJob
from acheron.shell.step_handler import create_step_handler

if TYPE_CHECKING:
    from acheron.core.models import ExecutorStrategy, JobRequest, WorkerCapabilities
    from acheron.shell.cache import PlanCache
    from acheron.shell.executors._utils import StepHandler
    from acheron.shell.registry import RegisteredWorker, WorkerRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LanguagePair:
    """A supported source→target language pair with supporting workers."""

    src: str
    dst: str
    workers: tuple[str, ...]


class Orchestrator:
    """Service layer wiring together all pipeline components."""

    def __init__(
        self,
        registry: WorkerRegistry,
        cache: PlanCache,
        handler: StepHandler | None = None,
    ) -> None:
        self._registry = registry
        self._cache = cache
        self._handler = handler or create_step_handler(registry)
        self._job_store = JobStore()
        self._tasks: set[asyncio.Task[None]] = set()
        self._health_monitor = HealthMonitor(registry)

    async def start(self) -> None:
        """Start background tasks."""
        await self._health_monitor.start()

    async def shutdown(self) -> None:
        """Stop background tasks."""
        await self._health_monitor.stop()

    async def submit_job(self, request: JobRequest, strategy: ExecutorStrategy) -> TrackedJob:
        """Compile a plan and execute it. Returns the tracked job immediately.

        Raises:
            AcheronError: If plan compilation fails (e.g. invalid language path).
        """
        job_id = f"job-{uuid.uuid4().hex[:8]}"
        source_type = "epub" if isinstance(request, EpubRequest) else "audio"
        logger.info(
            "Submitting job %s: %s → %s (%s, %s)",
            job_id,
            request.source_language,
            request.target_language,
            source_type,
            strategy.value,
        )

        capabilities = tuple(w.capabilities for w in self._registry.list_all())
        plan = compile_plan(request, strategy, capabilities, job_id=job_id)
        self._cache.save_plan(plan)
        logger.info("Plan compiled for %s: %s (%d steps)", job_id, plan.plan_id, len(plan.steps))

        tracked = TrackedJob(
            job_id=job_id,
            request=request,
            strategy=strategy,
            plan=plan,
            status="running",
        )
        self._job_store.put(tracked)

        task = asyncio.create_task(self._execute(tracked))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

        return tracked

    async def _execute(self, tracked: TrackedJob) -> None:
        """Run the plan executor and update job status."""
        logger.info("Executing %s (%s strategy)", tracked.job_id, tracked.strategy.value)
        try:
            if tracked.plan is None:
                tracked.status = "failed"
                logger.error("No plan for %s", tracked.job_id)
            else:
                executor = create_executor(tracked.strategy, self._handler)
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
        except AcheronError:
            logger.exception("Plan execution failed for %s", tracked.job_id)
            tracked.status = "failed"
        except Exception:
            logger.exception("Unexpected error executing %s", tracked.job_id)
            tracked.status = "failed"
        self._job_store.put(tracked)

    async def get_job(self, job_id: str) -> TrackedJob | None:
        """Retrieve a tracked job by ID."""
        return self._job_store.get(job_id)

    async def list_jobs(self) -> tuple[TrackedJob, ...]:
        """List all tracked jobs."""
        return self._job_store.list_all()

    def get_capabilities(
        self,
        src: str | None = None,
        dst: str | None = None,
    ) -> list[LanguagePair]:
        """Aggregate language pairs from registered workers."""
        workers = self._registry.list_all()
        pairs: dict[tuple[str, str], list[str]] = {}

        for w in workers:
            for lang_in in w.capabilities.supported_languages_in:
                for lang_out in w.capabilities.supported_languages_out:
                    if src and lang_in != src:
                        continue
                    if dst and lang_out != dst:
                        continue
                    key = (lang_in, lang_out)
                    if key not in pairs:
                        pairs[key] = []
                    pairs[key].append(w.worker_id)

        return [LanguagePair(src=k[0], dst=k[1], workers=tuple(v)) for k, v in pairs.items()]

    def register_worker(
        self,
        worker_id: str,
        endpoint: str,
        transport: str,
        capabilities: WorkerCapabilities,
    ) -> None:
        """Register a worker in the registry."""
        self._registry.register(worker_id, endpoint, transport, capabilities)
        logger.info(
            "Registered worker %s (%s, %s → %s)", worker_id, capabilities.worker_type.value, endpoint, transport
        )

    def list_workers(self) -> tuple[RegisteredWorker, ...]:
        """List all registered workers."""
        return self._registry.list_all()
