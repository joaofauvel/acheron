"""Step handler dispatching plan steps to registered workers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from acheron.core.errors import WorkerError
from acheron.core.interfaces import Worker
from acheron.core.models import Job, JobResult, WorkerCapabilities, WorkerType
from acheron.shell.transports.grpc import GrpcWorker
from acheron.shell.transports.http import HttpWorker
from acheron.tls import grpc_channel

if TYPE_CHECKING:
    from pathlib import Path

    from acheron.core.models import Plan, PlanStep
    from acheron.shell.executors._utils import StepHandler
    from acheron.shell.local_handlers import LocalJobHandler
    from acheron.shell.registry import RegisteredWorker
    from acheron.shell.stores.base import WorkerStore

logger = logging.getLogger(__name__)

type WorkerFactory = Callable[[RegisteredWorker], Worker]


def default_worker_factory(
    registered: RegisteredWorker,
    local_handlers: dict[str, LocalJobHandler] | None = None,
    *,
    data_dir: Path | str,
) -> Worker:
    """Create a worker from a registered worker's endpoint and transport.

    For ``local`` workers, the handler is looked up from ``local_handlers`` keyed
    by worker_id, not from ``registered.metadata``. Handlers are not serializable
    so they cannot live in metadata, which is persisted by backends like Redis.

    ``data_dir`` is the orchestrator's effective data dir (from settings) and
    is forwarded to the transports so they don't need to read env vars.
    ``HttpWorker`` constructs its own ``StepCache`` from ``data_dir``.
    """
    match registered.transport:
        case "grpc":
            channel = grpc_channel(registered.endpoint)
            return GrpcWorker(channel, data_dir=data_dir)
        case "local":
            from acheron.shell.transports.local import LocalWorker  # noqa: PLC0415

            handler = (local_handlers or {}).get(registered.worker_id)
            if handler is None:
                msg = f"Local worker {registered.worker_id} has no handler registered"
                raise WorkerError(msg)
            return LocalWorker(
                worker_type=registered.capabilities.worker_type,
                handler=handler,
                supported_languages_in=registered.capabilities.supported_languages_in,
                supported_languages_out=registered.capabilities.supported_languages_out,
            )
        case _:
            return HttpWorker(registered.endpoint, data_dir=data_dir)


def _language_matches(step_type: WorkerType, caps: WorkerCapabilities, src: str, dst: str) -> bool:
    """Check if a worker's language capabilities match the step requirements."""
    match step_type:
        case WorkerType.TRANSLATION:
            return src in caps.supported_languages_in and dst in caps.supported_languages_out
        case WorkerType.ASR:
            return src in caps.supported_languages_in and src in caps.supported_languages_out
        case WorkerType.TTS:
            return dst in caps.supported_languages_in and dst in caps.supported_languages_out
        case _:
            return True


class CachingStepHandler:
    """Step handler that caches the worker list and worker instances per plan.

    The handler is a callable: ``await handler(step, plan) -> JobResult``. The
    orchestrator invalidates the cache on ``submit_job`` and on task cancellation
    via :meth:`_invalidate_worker_cache` so a worker that was re-registered or
    removed between plans is picked up on the next dispatch.
    """

    def __init__(
        self,
        registry: WorkerStore,
        worker_factory: WorkerFactory | None = None,
        local_handlers: dict[str, LocalJobHandler] | None = None,
        *,
        data_dir: Path | str,
    ) -> None:
        self._registry = registry
        self._data_dir = data_dir
        self._factory = worker_factory or (
            lambda reg: default_worker_factory(reg, local_handlers, data_dir=data_dir)
        )
        self._cached_workers: tuple[RegisteredWorker, ...] | None = None
        self._cached_plan_id: str | None = None
        self._worker_instances: dict[str, Worker] = {}

    async def __call__(self, step: PlanStep, plan: Plan) -> JobResult:
        src = plan.source_language
        dst = plan.target_language

        if self._cached_workers is None or plan.plan_id != self._cached_plan_id:
            self._cached_workers = await self._registry.list_all()
            self._cached_plan_id = plan.plan_id
        workers = self._cached_workers

        selected: RegisteredWorker | None = None
        for w in workers:
            caps = w.capabilities
            if caps.worker_type != step.type:
                continue
            if not _language_matches(step.type, caps, src, dst):
                continue
            selected = w
            break

        if selected is None:
            msg = f"No worker for {step.type.value} ({src} → {dst})"
            raise WorkerError(msg)

        chapter_id = step.payload.get("chapter_id", "")
        job = Job(
            job_id=f"{plan.job_id}-{step.step_id}",
            job_type=step.type,
            payload=step.payload,
            chapter_id=str(chapter_id) if chapter_id is not None else "",
        )

        logger.info("Dispatching %s to %s", step.step_id, selected.worker_id)
        worker_instance = self._worker_instances.get(selected.worker_id)
        if worker_instance is None:
            worker_instance = self._factory(selected)
            self._worker_instances[selected.worker_id] = worker_instance
        return await worker_instance.execute(job)

    def _invalidate_worker_cache(self) -> None:
        """Drop both the worker-list snapshot and the worker-instance pool.

        The orchestrator calls this on ``submit_job`` and on task cancellation
        so a worker re-registration, removal, or endpoint change is reflected
        on the next dispatch.
        """
        self._cached_workers = None
        self._cached_plan_id = None
        self._worker_instances.clear()


def create_step_handler(
    registry: WorkerStore,
    worker_factory: WorkerFactory | None = None,
    local_handlers: dict[str, LocalJobHandler] | None = None,
    *,
    data_dir: Path | str,
) -> StepHandler:
    """Create a step handler that dispatches to registered workers.

    ``local_handlers`` maps worker_id to its in-process handler. Required when
    the registry contains local workers (transport == "local").

    ``data_dir`` is the orchestrator's effective data dir and is forwarded to
    the transports so they don't need to read env vars. ``HttpWorker``
    constructs its own ``StepCache`` from ``data_dir``.

    Caches ``registry.list_all()`` per plan (plan_id) and reuses ``Worker``
    instances per worker_id across steps to avoid redundant registry round-trips
    and gRPC channel / HTTP connection churn.
    """
    return CachingStepHandler(
        registry,
        worker_factory=worker_factory,
        local_handlers=local_handlers,
        data_dir=data_dir,
    )
