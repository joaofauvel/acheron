"""Step handler dispatching plan steps to registered workers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from acheron.core.errors import WorkerError
from acheron.core.interfaces import Worker
from acheron.core.models import Job, JobResult, WorkerCapabilities, WorkerType
from acheron.shell.tls import grpc_channel
from acheron.shell.transports.grpc import GrpcWorker
from acheron.shell.transports.http import HttpWorker

if TYPE_CHECKING:
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
) -> Worker:
    """Create a worker from a registered worker's endpoint and transport.

    For ``local`` workers, the handler is looked up from ``local_handlers`` keyed
    by worker_id, not from ``registered.metadata``. Handlers are not serializable
    so they cannot live in metadata, which is persisted by backends like Redis.
    """
    match registered.transport:
        case "grpc":
            channel = grpc_channel(registered.endpoint)
            return GrpcWorker(channel)
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
            return HttpWorker(registered.endpoint)


def _language_matches(step_type: WorkerType, caps: WorkerCapabilities, src: str, dst: str) -> bool:
    """Check if a worker's language capabilities match the step requirements."""
    match step_type:
        case WorkerType.TRANSLATION:
            return src in caps.supported_languages_in and dst in caps.supported_languages_out
        case WorkerType.ASR:
            return src in caps.supported_languages_in
        case WorkerType.TTS:
            return dst in caps.supported_languages_in and dst in caps.supported_languages_out
        case _:
            return True


def create_step_handler(
    registry: WorkerStore,
    worker_factory: WorkerFactory | None = None,
    local_handlers: dict[str, LocalJobHandler] | None = None,
) -> StepHandler:
    """Create a step handler that dispatches to registered workers.

    ``local_handlers`` maps worker_id to its in-process handler. Required when
    the registry contains local workers (transport == "local").
    """
    factory = worker_factory or (lambda reg: default_worker_factory(reg, local_handlers))

    async def handler(step: PlanStep, plan: Plan) -> JobResult:
        src = plan.source_language
        dst = plan.target_language

        workers = registry.list_all()
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
        worker_instance = factory(selected)
        return await worker_instance.execute(job)

    return handler
