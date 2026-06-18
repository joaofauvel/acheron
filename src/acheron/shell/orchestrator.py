"""Orchestrator — service layer wiring registry, planner, executors, and cache."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from acheron.core.errors import AcheronError
from acheron.core.models import EpubRequest, Job, JobResult, WorkerCapabilities, WorkerType
from acheron.core.planner import compile_plan
from acheron.shell.executors import create_executor
from acheron.shell.health import HealthMonitor
from acheron.shell.job_store import TrackedJob
from acheron.shell.local_handlers import chunk_handler, extract_handler, package_handler
from acheron.shell.step_handler import create_step_handler
from acheron.shell.stores import create_job_store

if TYPE_CHECKING:
    from acheron.core.models import ExecutorStrategy, JobRequest
    from acheron.shell.cache import PlanCache
    from acheron.shell.executors._utils import StepHandler
    from acheron.shell.registry import RegisteredWorker
    from acheron.shell.stores.base import JobStore, WorkerStore

logger = logging.getLogger(__name__)


type LocalJobHandler = Callable[[Job], Awaitable[JobResult]]


_BUILT_IN_LOCAL_HANDLERS: dict[WorkerType, LocalJobHandler] = {
    WorkerType.EXTRACTION: extract_handler,
    WorkerType.CHUNKING: chunk_handler,
    WorkerType.PACKAGING: package_handler,
}


def _all_languages_caps(worker_type: WorkerType) -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=worker_type,
        supported_languages_in=frozenset({"en", "es", "fr", "de"}),
        supported_languages_out=frozenset({"en", "es", "fr", "de"}),
        supported_formats_in=frozenset(),
        supported_formats_out=frozenset(),
        max_payload_bytes=None,
        batch_capable=False,
        model_source=None,
    )


@dataclass(frozen=True)
class LanguagePair:
    """A supported source→target language pair with supporting workers."""

    src: str
    dst: str
    workers: tuple[str, ...]


def _collect_worker_caps(
    workers: tuple[RegisteredWorker, ...],
) -> tuple[set[str], set[tuple[str, str]]]:
    """Extract TTS output languages and translation pairs from registered workers."""
    tts_langs: set[str] = set()
    translation_pairs: set[tuple[str, str]] = set()
    for w in workers:
        match w.capabilities.worker_type:
            case WorkerType.TTS:
                tts_langs.update(w.capabilities.supported_languages_out)
            case WorkerType.TRANSLATION:
                for lang_in in w.capabilities.supported_languages_in:
                    for lang_out in w.capabilities.supported_languages_out:
                        translation_pairs.add((lang_in, lang_out))
    return tts_langs, translation_pairs


def _pair_is_achievable(
    lang_in: str,
    lang_out: str,
    src_filter: str | None,
    dst_filter: str | None,
    requirements: tuple[set[str], set[tuple[str, str]]],
) -> bool:
    """Check if a language pair can be fulfilled by the planner."""
    tts_langs, translation_pairs = requirements
    if src_filter and lang_in != src_filter:
        return False
    if dst_filter and lang_out != dst_filter:
        return False
    if lang_out not in tts_langs:
        return False
    return lang_in == lang_out or (lang_in, lang_out) in translation_pairs


class Orchestrator:
    """Service layer wiring together all pipeline components."""

    def __init__(
        self,
        registry: WorkerStore,
        cache: PlanCache,
        handler: StepHandler | None = None,
        *,
        job_store: JobStore | None = None,
    ) -> None:
        self._registry = registry
        self._cache = cache
        self._register_built_in_local_workers()
        self._handler = handler or create_step_handler(registry)
        self._job_store = job_store if job_store is not None else create_job_store()
        self._tasks: set[asyncio.Task[None]] = set()
        self._health_monitor = HealthMonitor(registry)

    def _register_built_in_local_workers(self) -> None:
        """Register in-process local workers for orchestration-level steps.

        Only registers a step type if no worker of that type is already in the
        registry, so user-registered workers (e.g. custom extraction logic) take
        precedence over the stubs.
        """
        for worker_type, handler in _BUILT_IN_LOCAL_HANDLERS.items():
            if self._registry.find_by_type(worker_type):
                continue
            self._registry.register(
                worker_id=f"{worker_type.value}-local",
                endpoint="local",
                transport="local",
                capabilities=_all_languages_caps(worker_type),
                metadata={"handler": handler},
            )

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
        """Aggregate language pairs achievable by the planner.

        Only includes pairs where all required worker types are registered:
        TTS for the target language, and a TRANSLATION worker when src != dst.
        """
        workers = self._registry.list_all()
        tts_langs, translation_pairs = _collect_worker_caps(workers)
        requirements = (tts_langs, translation_pairs)
        pairs: dict[tuple[str, str], list[str]] = {}

        for w in workers:
            for lang_in in w.capabilities.supported_languages_in:
                for lang_out in w.capabilities.supported_languages_out:
                    if not _pair_is_achievable(lang_in, lang_out, src, dst, requirements):
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
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Register a worker in the registry."""
        self._registry.register(worker_id, endpoint, transport, capabilities, metadata=metadata)
        logger.info(
            "Registered worker %s (%s, %s → %s)", worker_id, capabilities.worker_type.value, endpoint, transport
        )

    def list_workers(self) -> tuple[RegisteredWorker, ...]:
        """List all registered workers."""
        return self._registry.list_all()
