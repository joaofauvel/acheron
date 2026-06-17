"""Local worker that dispatches jobs to Python callables."""

from collections.abc import Awaitable, Callable

from acheron.core.interfaces import Worker
from acheron.core.models import (
    Job,
    JobResult,
    WorkerCapabilities,
    WorkerType,
)

type JobHandler = Callable[[Job], Awaitable[JobResult]]


class LocalWorker(Worker):
    """Worker that delegates execution to a local async callable."""

    def __init__(  # noqa: PLR0913
        self,
        worker_type: WorkerType,
        handler: JobHandler,
        supported_languages_in: frozenset[str] = frozenset(),
        supported_languages_out: frozenset[str] = frozenset(),
        supported_formats_in: frozenset[str] = frozenset(),
        supported_formats_out: frozenset[str] = frozenset(),
    ) -> None:
        self._worker_type = worker_type
        self._handler = handler
        self._supported_languages_in = supported_languages_in
        self._supported_languages_out = supported_languages_out
        self._supported_formats_in = supported_formats_in
        self._supported_formats_out = supported_formats_out

    async def capabilities(self) -> WorkerCapabilities:
        """Return this worker's configured capabilities."""
        return WorkerCapabilities(
            worker_type=self._worker_type,
            supported_languages_in=self._supported_languages_in,
            supported_languages_out=self._supported_languages_out,
            supported_formats_in=self._supported_formats_in,
            supported_formats_out=self._supported_formats_out,
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
        )

    async def execute(self, job: Job) -> JobResult:
        """Delegate job execution to the registered handler."""
        return await self._handler(job)

    async def health(self) -> bool:
        """Local workers are always healthy."""
        return True
