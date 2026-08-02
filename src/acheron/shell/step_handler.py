"""Step handler dispatching plan steps to registered workers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from acheron.core.errors import VoiceSelectionError, WorkerError
from acheron.core.interfaces import Worker
from acheron.core.models import Job, JobResult, JsonValue, WorkerCapabilities, WorkerStatus, WorkerType
from acheron.core.planner import _safe_voice, canonicalize_voice
from acheron.shell.transports.grpc import GrpcWorker
from acheron.shell.transports.http import HttpWorker
from acheron.tls import grpc_channel

if TYPE_CHECKING:
    from acheron.core.models import Plan, PlanStep
    from acheron.shell.cache import InMemoryStepCache, StepCache
    from acheron.shell.executors._utils import StepHandler
    from acheron.shell.local_handlers import LocalJobHandler
    from acheron.shell.registry import RegisteredWorker
    from acheron.shell.stores.base import WorkerStore

logger = logging.getLogger(__name__)

type WorkerFactory = Callable[[RegisteredWorker], Worker]
type RegistrationTokenProvider = Callable[[], str | None]


def default_worker_factory(  # noqa: PLR0913
    registered: RegisteredWorker,
    local_handlers: dict[str, LocalJobHandler] | None = None,
    *,
    data_dir: Path | str,
    registration_token: str | None = None,
    registration_token_provider: RegistrationTokenProvider | None = None,
    step_cache: StepCache | InMemoryStepCache | None = None,
) -> Worker:
    """Create a worker from a registered worker's endpoint and transport.

    For ``local`` workers, the handler is looked up from ``local_handlers`` keyed
    by worker_id, not from ``registered.metadata``. Handlers are not serializable
    so they cannot live in metadata, which is persisted by backends like Redis.

    ``data_dir`` is the orchestrator's effective data dir (from settings) and
    is forwarded to the transports so they don't need to read env vars.
    ``step_cache`` is the orchestrator-owned cache shared with remote workers.
    """
    match registered.transport:
        case "grpc":
            channel = grpc_channel(
                registered.endpoint,
                require_tls=(
                    (registration_token is not None or registration_token_provider is not None)
                    and not registered.endpoint.startswith(("localhost:", "127.0.0.1:", "[::1]:"))
                ),
            )
            return GrpcWorker(
                channel,
                data_dir=data_dir,
                registration_token=registration_token,
                registration_token_provider=registration_token_provider,
            )
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
            token = registration_token_provider() if registration_token_provider is not None else registration_token
            return HttpWorker(
                registered.endpoint,
                data_dir=data_dir,
                registration_token=token,
                registration_token_provider=registration_token_provider,
                step_cache=step_cache,
            )


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


def _required_voices(payload: dict[str, JsonValue]) -> tuple[str, ...]:
    """Extract voice names from canonical TTS payload data."""
    voices: list[str] = []
    default = payload.get("voice")
    if isinstance(default, str):
        voices.append(default)
    voice_map = payload.get("voice_map")
    if isinstance(voice_map, list):
        for item in voice_map:
            if isinstance(item, dict):
                voice = item.get("voice")
                if isinstance(voice, str):
                    voices.append(voice)
    return tuple(dict.fromkeys(voices))


def _select_worker(
    step: PlanStep,
    workers: tuple[RegisteredWorker, ...],
    src: str,
    dst: str,
) -> RegisteredWorker:
    if step.type is WorkerType.TTS:
        if step.selected_worker_id is None:
            msg = "No worker selected for TTS step by planner"
            raise WorkerError(msg)
        selected = next((worker for worker in workers if worker.worker_id == step.selected_worker_id), None)
        if selected is None:
            msg = "Planner-selected TTS worker is no longer registered"
            raise VoiceSelectionError(msg)
        if selected.capabilities.worker_type is not WorkerType.TTS:
            msg = "Planner-selected worker is no longer a TTS worker"
            raise VoiceSelectionError(msg)
        if selected.status is WorkerStatus.OFFLINE:
            msg = "Planner-selected TTS worker is offline"
            raise VoiceSelectionError(msg)
        if not _language_matches(step.type, selected.capabilities, src, dst):
            msg = "Planner-selected TTS worker no longer supports the language"
            raise VoiceSelectionError(msg)
        for voice in _required_voices(step.payload):
            try:
                canonicalize_voice(voice, selected.capabilities)
            except VoiceSelectionError as exc:
                msg = f"Selected TTS worker cannot honor voice: {_safe_voice(voice)}"
                raise VoiceSelectionError(msg) from exc
        return selected

    selected = next(
        (
            worker
            for worker in sorted(workers, key=lambda item: item.worker_id)
            if worker.status is not WorkerStatus.OFFLINE
            and worker.capabilities.worker_type == step.type
            and _language_matches(step.type, worker.capabilities, src, dst)
        ),
        None,
    )
    if selected is None:
        msg = f"No worker for {step.type.value} ({src} → {dst})"
        raise WorkerError(msg)
    return selected


class CachingStepHandler:
    """Step handler that reuses worker instances until registry generations change."""

    def __init__(  # noqa: PLR0913
        self,
        registry: WorkerStore,
        worker_factory: WorkerFactory | None = None,
        local_handlers: dict[str, LocalJobHandler] | None = None,
        *,
        data_dir: Path | str,
        registration_token: str | None = None,
        registration_token_provider: RegistrationTokenProvider | None = None,
        step_cache: StepCache | InMemoryStepCache | None = None,
    ) -> None:
        self._registry = registry
        self._data_dir = data_dir
        self._step_cache = step_cache
        if worker_factory is not None:
            self._factory = worker_factory
        elif registration_token_provider is None:
            self._factory = lambda reg: default_worker_factory(
                reg,
                local_handlers,
                data_dir=data_dir,
                registration_token=registration_token,
                step_cache=self._step_cache,
            )
        else:
            self._factory = lambda reg: default_worker_factory(
                reg,
                local_handlers,
                data_dir=data_dir,
                registration_token=registration_token,
                registration_token_provider=registration_token_provider,
                step_cache=self._step_cache,
            )
        self._cached_workers: tuple[RegisteredWorker, ...] | None = None
        self._cached_generations: dict[str, int] | None = None
        self._worker_instances: dict[str, Worker] = {}
        self._retired_worker_instances: dict[int, Worker] = {}
        self._job_worker_instances: dict[str, list[Worker]] = {}
        self._worker_refcounts: dict[int, int] = {}

    async def __call__(self, step: PlanStep, plan: Plan) -> JobResult:
        """Dispatch the step to a selected worker with generation-aware reuse."""
        src = plan.source_language
        dst = plan.target_language

        current_workers = await self._registry.list_all()
        current_generations = {worker.worker_id: worker.registration_generation for worker in current_workers}
        if self._cached_generations is not None:
            for worker_id, generation in self._cached_generations.items():
                if current_generations.get(worker_id) != generation:
                    self._retire_worker_instance(worker_id)
        self._cached_workers = current_workers
        self._cached_generations = current_generations

        selected = _select_worker(step, current_workers, src, dst)

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
            if self._step_cache is not None and isinstance(worker_instance, HttpWorker):
                worker_instance.configure_step_cache(self._step_cache)
            self._worker_instances[selected.worker_id] = worker_instance
        job_instances = self._job_worker_instances.setdefault(plan.job_id, [])
        if all(worker is not worker_instance for worker in job_instances):
            job_instances.append(worker_instance)
            worker_key = id(worker_instance)
            self._worker_refcounts[worker_key] = self._worker_refcounts.get(worker_key, 0) + 1
        result = await worker_instance.execute(job)
        return replace(result, worker_id=selected.worker_id)

    def configure_step_cache(self, step_cache: StepCache | InMemoryStepCache) -> None:
        """Configure the shared cache used by default and HTTP worker factories."""
        handler_root = Path(self._data_dir).resolve()
        cache_root = step_cache.data_dir.resolve()
        if handler_root != cache_root:
            msg = f"StepCache data directory must match the step handler data directory: {cache_root} != {handler_root}"
            raise ValueError(msg)
        self._step_cache = step_cache

    def _retire_worker_instance(self, worker_id: str) -> None:
        worker = self._worker_instances.pop(worker_id, None)
        if worker is not None:
            self._retired_worker_instances[id(worker)] = worker

    async def _invalidate_worker_cache(self) -> None:
        """Drop both the worker-list snapshot and the worker-instance pool.

        Explicitly drop all cached worker resources, retaining close behavior
        for instances still referenced by active jobs.
        """
        self._retired_worker_instances.update((id(worker), worker) for worker in self._worker_instances.values())
        self._cached_workers = None
        self._cached_generations = None
        self._worker_instances.clear()
        await self._close_retired_workers()

    async def release_job(self, job_id: str) -> None:
        """Release worker instances retained for a completed job."""
        for worker in self._job_worker_instances.pop(job_id, ()):
            worker_key = id(worker)
            remaining = self._worker_refcounts.get(worker_key, 0) - 1
            if remaining > 0:
                self._worker_refcounts[worker_key] = remaining
            else:
                self._worker_refcounts.pop(worker_key, None)
        await self._close_retired_workers()

    async def _close_retired_workers(self) -> None:
        for worker_key, worker in tuple(self._retired_worker_instances.items()):
            if self._worker_refcounts.get(worker_key, 0) > 0:
                continue
            close = getattr(worker, "close", None)
            if close is None:
                self._retired_worker_instances.pop(worker_key, None)
                continue
            try:
                await close()
            except Exception:
                logger.exception("Failed to close cached worker")
            else:
                self._retired_worker_instances.pop(worker_key, None)

    async def close(self) -> None:
        """Close resources held by cached worker instances."""
        await self._invalidate_worker_cache()


def create_step_handler(  # noqa: PLR0913
    registry: WorkerStore,
    worker_factory: WorkerFactory | None = None,
    local_handlers: dict[str, LocalJobHandler] | None = None,
    *,
    data_dir: Path | str,
    registration_token: str | None = None,
    registration_token_provider: RegistrationTokenProvider | None = None,
    step_cache: StepCache | InMemoryStepCache | None = None,
) -> StepHandler:
    """Create a step handler that dispatches to registered workers.

    ``local_handlers`` maps worker_id to its in-process handler. Required when
    the registry contains local workers (transport == "local").

    ``data_dir`` is the orchestrator's effective data dir and is forwarded to
    the transports so they don't need to read env vars. ``step_cache`` is the
    orchestrator-owned cache shared with remote workers.

    Refreshes registry state per dispatch and reuses ``Worker`` instances per
    worker_id until that worker's registration generation changes.
    """
    return CachingStepHandler(
        registry,
        worker_factory=worker_factory,
        local_handlers=local_handlers,
        data_dir=data_dir,
        registration_token=registration_token,
        registration_token_provider=registration_token_provider,
        step_cache=step_cache,
    )
