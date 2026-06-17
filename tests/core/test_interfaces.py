import pytest

from acheron.core.interfaces import Executor, StreamingWorker, Worker
from acheron.core.models import (
    BatchJob,
    BatchStatus,
    Job,
    JobResult,
    Plan,
    PlanResult,
    WorkerCapabilities,
    WorkerType,
)


class ConcreteWorker(Worker):
    async def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"en"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
        )

    async def execute(self, job: Job) -> JobResult:
        raise NotImplementedError

    async def health(self) -> bool:
        return True


class ConcreteStreamingWorker(StreamingWorker):
    async def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"en"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav"}),
            max_payload_bytes=None,
            batch_capable=True,
            model_source=None,
        )

    async def execute(self, job: Job) -> JobResult:
        raise NotImplementedError

    async def health(self) -> bool:
        return True

    async def submit_batch(self, batch: BatchJob) -> str:
        return "handle-1"

    async def poll_batch(self, batch_handle: str) -> BatchStatus:
        raise NotImplementedError

    async def collect_results(self, batch_handle: str) -> tuple[JobResult, ...]:
        return ()


class ConcreteExecutor(Executor):
    async def run(self, plan: Plan) -> PlanResult:
        raise NotImplementedError


class WorkerMissingCapabilities(Worker):
    async def execute(self, job: Job) -> JobResult:
        raise NotImplementedError

    async def health(self) -> bool:
        return True


class WorkerMissingExecute(Worker):
    async def capabilities(self) -> WorkerCapabilities:
        raise NotImplementedError

    async def health(self) -> bool:
        return True


class WorkerMissingHealth(Worker):
    async def capabilities(self) -> WorkerCapabilities:
        raise NotImplementedError

    async def execute(self, job: Job) -> JobResult:
        raise NotImplementedError


class StreamingWorkerMissingSubmit(StreamingWorker):
    async def capabilities(self) -> WorkerCapabilities:
        raise NotImplementedError

    async def execute(self, job: Job) -> JobResult:
        raise NotImplementedError

    async def health(self) -> bool:
        return True

    async def poll_batch(self, batch_handle: str) -> BatchStatus:
        raise NotImplementedError

    async def collect_results(self, batch_handle: str) -> tuple[JobResult, ...]:
        return ()


class ExecutorMissingRun(Executor):
    pass


class TestWorker:
    def test_instantiation_with_all_methods(self) -> None:
        worker = ConcreteWorker()
        assert worker is not None

    def test_missing_capabilities_raises(self) -> None:
        with pytest.raises(TypeError):
            WorkerMissingCapabilities()  # type: ignore[abstract]

    def test_missing_execute_raises(self) -> None:
        with pytest.raises(TypeError):
            WorkerMissingExecute()  # type: ignore[abstract]

    def test_missing_health_raises(self) -> None:
        with pytest.raises(TypeError):
            WorkerMissingHealth()  # type: ignore[abstract]


class TestStreamingWorker:
    def test_inherits_worker_methods(self) -> None:
        worker = ConcreteStreamingWorker()
        assert isinstance(worker, Worker)

    def test_instantiation_with_all_methods(self) -> None:
        worker = ConcreteStreamingWorker()
        assert worker is not None

    def test_missing_submit_batch_raises(self) -> None:
        with pytest.raises(TypeError):
            StreamingWorkerMissingSubmit()  # type: ignore[abstract]


class TestExecutor:
    def test_instantiation_with_run(self) -> None:
        executor = ConcreteExecutor()
        assert executor is not None

    def test_missing_run_raises(self) -> None:
        with pytest.raises(TypeError):
            ExecutorMissingRun()  # type: ignore[abstract]
