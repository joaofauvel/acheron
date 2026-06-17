"""Step handler dispatching plan steps to registered workers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from acheron.core.errors import WorkerError
from acheron.core.interfaces import Worker
from acheron.core.models import Job, WorkerCapabilities, WorkerType
from acheron.shell.transports.http import HttpWorker

if TYPE_CHECKING:
    from acheron.core.models import JobResult, Plan, PlanStep
    from acheron.shell.executors._utils import StepHandler
    from acheron.shell.registry import RegisteredWorker, WorkerRegistry

logger = logging.getLogger(__name__)

type WorkerFactory = Callable[[RegisteredWorker], Worker]


def _default_worker_factory(registered: RegisteredWorker) -> Worker:
    """Create an HttpWorker from a registered worker's endpoint."""
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
    registry: WorkerRegistry,
    worker_factory: WorkerFactory | None = None,
) -> StepHandler:
    """Create a step handler that dispatches to registered workers."""
    factory = worker_factory or _default_worker_factory

    async def handler(step: PlanStep, plan: Plan) -> JobResult:
        src = plan.source_language
        dst = plan.target_language

        workers = registry.list_all()
        match = None
        for w in workers:
            caps = w.capabilities
            if caps.worker_type != step.type:
                continue
            if not _language_matches(step.type, caps, src, dst):
                continue
            match = w
            break

        if match is None:
            msg = f"No worker for {step.type.value} ({src} → {dst})"
            raise WorkerError(msg)

        job = Job(
            job_id=f"{plan.job_id}-{step.step_id}",
            job_type=step.type,
            payload=step.payload,
            chapter_id=step.payload.get("chapter_id", ""),
        )

        logger.info("Dispatching %s to %s", step.step_id, match.worker_id)
        worker_instance = factory(match)
        return await worker_instance.execute(job)

    return handler
