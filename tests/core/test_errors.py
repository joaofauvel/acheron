import pytest

from acheron.core.errors import (
    AcheronError,
    CacheCorruptedError,
    CacheError,
    CacheMissError,
    ChunkingTooLongForWorkerError,
    InvalidLanguagePathError,
    PlanError,
    PlanValidationError,
    WorkerError,
    WorkerTimeoutError,
    WorkerUnavailableError,
)


class TestExceptionHierarchy:
    @pytest.mark.parametrize(
        "exc_cls",
        [
            InvalidLanguagePathError,
            ChunkingTooLongForWorkerError,
            PlanValidationError,
            PlanError,
            WorkerUnavailableError,
            WorkerTimeoutError,
            WorkerError,
            CacheMissError,
            CacheCorruptedError,
            CacheError,
        ],
    )
    def test_all_inherit_from_acheron_error(self, exc_cls: type) -> None:
        assert issubclass(exc_cls, AcheronError)

    @pytest.mark.parametrize(
        ("child", "parent"),
        [
            (InvalidLanguagePathError, PlanError),
            (ChunkingTooLongForWorkerError, PlanError),
            (PlanValidationError, PlanError),
            (WorkerUnavailableError, WorkerError),
            (WorkerTimeoutError, WorkerError),
            (CacheMissError, CacheError),
            (CacheCorruptedError, CacheError),
        ],
    )
    def test_child_inherits_from_parent(self, child: type, parent: type) -> None:
        assert issubclass(child, parent)


class TestMessagePropagation:
    def test_message_accessible(self) -> None:
        exc = InvalidLanguagePathError("en -> xx not supported")
        assert str(exc) == "en -> xx not supported"

    def test_catch_by_base(self) -> None:
        with pytest.raises(AcheronError):
            raise WorkerTimeoutError("timed out after 30s")

    def test_catch_by_intermediate(self) -> None:
        with pytest.raises(PlanError):
            raise PlanValidationError("missing step dependency")

    def test_chunking_too_long_caught_by_plan_error(self) -> None:
        with pytest.raises(PlanError):
            raise ChunkingTooLongForWorkerError("chunking exceeds worker limit")


class TestPipelineError:
    def test_pipeline_error_inherits_from_acheron_error(self) -> None:
        from acheron.core.errors import PipelineError

        assert issubclass(PipelineError, AcheronError)

    def test_pipeline_error_does_not_inherit_from_worker_error(self) -> None:
        from acheron.core.errors import PipelineError, WorkerError

        assert not issubclass(PipelineError, WorkerError)
