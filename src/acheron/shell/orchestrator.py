"""Orchestrator — service layer wiring registry, planner, executors, and cache."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from acheron.core.errors import AcheronError
from acheron.core.models import AudioRequest, EpubRequest, WorkerCapabilities, WorkerType
from acheron.core.planner import compile_plan
from acheron.shell.executors import create_executor
from acheron.shell.health import HealthMonitor
from acheron.shell.job_store import TrackedJob
from acheron.shell.local_handlers import (
    LocalJobHandler,
    chunk_handler,
    extract_handler,
    package_handler,
)
from acheron.shell.step_handler import create_step_handler
from acheron.shell.stores import create_job_store

if TYPE_CHECKING:
    from acheron.core.models import ExecutorStrategy, JobRequest
    from acheron.shell.cache import PlanCache
    from acheron.shell.executors._utils import StepHandler
    from acheron.shell.registry import RegisteredWorker
    from acheron.shell.stores.base import JobStore, WorkerStore

from acheron.shell.cache import StepCache

logger = logging.getLogger(__name__)


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
        self._step_cache = StepCache(cache.data_dir)
        self._verify_data_dir_writable()
        self._local_handlers: dict[str, LocalJobHandler] = {}
        self._handler = handler or create_step_handler(registry, local_handlers=self._local_handlers)
        self._job_store = job_store if job_store is not None else create_job_store()
        self._tasks: set[asyncio.Task[None]] = set()
        self._started = False
        self._health_monitor = HealthMonitor(registry)

    def _verify_data_dir_writable(self) -> None:
        """Ensure the data dir exists and is writable. Raises AcheronError otherwise."""
        data_dir = self._cache.data_dir
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
        store methods (now ``async def``) can be awaited.
        """
        for worker_type, handler in _BUILT_IN_LOCAL_HANDLERS.items():
            existing = await self._registry.find_by_type(worker_type)
            if existing:
                continue
            worker_id = f"{worker_type.value}-local"
            self._local_handlers[worker_id] = handler
            await self._registry.register(
                worker_id=worker_id,
                endpoint="local",
                transport="local",
                capabilities=_all_languages_caps(worker_type),
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
        """
        if self._started:
            return
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
        self._cache.save_plan(plan)
        logger.info("Plan compiled for %s: %s (%d steps)", job_id, plan.plan_id, len(plan.steps))

        tracked = TrackedJob(
            job_id=job_id,
            request=request,
            strategy=strategy,
            plan=plan,
            status="running",
        )
        await self._job_store.put(tracked)

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
                executor = create_executor(
                    tracked.strategy,
                    self._handler,
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
        except AcheronError:
            logger.exception("Plan execution failed for %s", tracked.job_id)
            tracked.status = "failed"
        except Exception:
            logger.exception("Unexpected error executing %s", tracked.job_id)
            tracked.status = "failed"
        await self._job_store.put(tracked)

    async def get_job(self, job_id: str) -> TrackedJob | None:
        """Retrieve a tracked job by ID."""
        return await self._job_store.get(job_id)

    async def list_jobs(self) -> tuple[TrackedJob, ...]:
        """List all tracked jobs."""
        return await self._job_store.list_all()

    async def get_capabilities(
        self,
        src: str | None = None,
        dst: str | None = None,
    ) -> list[LanguagePair]:
        """Aggregate language pairs achievable by the planner.

        Only includes pairs where all required worker types are registered:
        TTS for the target language, and a TRANSLATION worker when src != dst.
        """
        workers = await self._registry.list_all()
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

    async def register_worker(  # noqa: PLR0913
        self,
        worker_id: str,
        endpoint: str,
        transport: str,
        capabilities: WorkerCapabilities,
        metadata: dict[str, object] | None = None,
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
