import pytest

from acheron.core.errors import (
    AcheronError,
    CacheCorruptedError,
    CacheError,
    CacheMissError,
    ChunkingTooLongForWorkerError,
    InvalidationTargetError,
    InvalidLanguagePathError,
    JobAlreadyRunningError,
    JobNotCancellableError,
    NoPlanToResumeError,
    PlanError,
    WorkerError,
    WorkerUnavailableError,
)


class TestExceptionHierarchy:
    @pytest.mark.parametrize(
        "exc_cls",
        [
            InvalidLanguagePathError,
            ChunkingTooLongForWorkerError,
            PlanError,
            WorkerUnavailableError,
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
            (WorkerUnavailableError, WorkerError),
            (CacheMissError, CacheError),
            (CacheCorruptedError, CacheError),
        ],
    )
    def test_child_inherits_from_parent(self, child: type, parent: type) -> None:
        assert issubclass(child, parent)


class TestMessagePropagation:
    def test_remediation_is_available_on_domain_errors(self) -> None:
        exc = JobAlreadyRunningError(
            "Job job-1 is already running",
            remediation="acheron job cancel job-1",
        )

        assert str(exc) == "Job job-1 is already running"
        assert exc.remediation == "acheron job cancel job-1"

    def test_new_job_errors_inherit_from_job_error(self) -> None:
        from acheron.core.errors import JobError

        assert issubclass(NoPlanToResumeError, JobError)
        assert issubclass(JobNotCancellableError, JobError)
        assert issubclass(InvalidationTargetError, JobError)

    def test_message_accessible(self) -> None:
        exc = InvalidLanguagePathError("en -> xx not supported")
        assert str(exc) == "en -> xx not supported"

    def test_catch_by_base(self) -> None:
        with pytest.raises(AcheronError):
            raise WorkerUnavailableError("not reachable")

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


class TestSanitisePublicMessage:
    @pytest.mark.parametrize(
        "message",
        [
            "/tmp",
            r"C:\\Users\\worker\\secret.txt",
            "foo/../../secret",
            r"..\\..\\secret",
            "custom+scheme://user:secret@example.test/path?token=secret#fragment",
            "Traceback (most recent call last):",
            "  File '/srv/worker.py', line 4",
            '{"password": "top-secret"}',
            "password: top-secret",
            "Authorization: Bearer top-secret",
        ],
    )
    def test_unsafe_message_uses_stable_fallback(self, message: str) -> None:
        from acheron.core.errors import sanitise_public_message

        assert sanitise_public_message(message) == "request failed"

    def test_preserves_ordinary_domain_message(self) -> None:
        from acheron.core.errors import sanitise_public_message

        assert sanitise_public_message("No worker supports en → es") == "No worker supports en → es"

    def test_empty_sensitive_message_uses_fallback(self) -> None:
        from acheron.core.errors import sanitise_public_message

        assert sanitise_public_message("\n\n") == "request failed"


class TestSanitiseExcMessage:
    def test_formats_class_name_with_first_line(self) -> None:
        from acheron.core.errors import sanitise_exc_message

        assert sanitise_exc_message(RuntimeError("boom")) == "RuntimeError: boom"

    def test_strips_traceback_file_lines(self) -> None:
        from acheron.core.errors import sanitise_exc_message

        exc = RuntimeError("secret stuff\n  File '/etc/passwd'\nTraceback (most recent call last):")
        assert sanitise_exc_message(exc) == "RuntimeError: secret stuff"

    def test_strips_leading_blank_lines(self) -> None:
        from acheron.core.errors import sanitise_exc_message

        exc = RuntimeError("\n\n  File '/etc/passwd'\nactual message")
        assert sanitise_exc_message(exc) == "RuntimeError: actual message"

    def test_empty_message_returns_placeholder(self) -> None:
        from acheron.core.errors import sanitise_exc_message

        assert sanitise_exc_message(RuntimeError("")) == "RuntimeError: <no message>"

    def test_uses_actual_subclass_name(self) -> None:
        from acheron.core.errors import WorkerError, sanitise_exc_message

        assert sanitise_exc_message(WorkerError("timeout")) == "WorkerError: timeout"

    def test_strips_credential_pattern(self) -> None:
        from acheron.core.errors import sanitise_exc_message

        exc = RuntimeError("DB connect failed password=foo at /runpod-volume")
        assert sanitise_exc_message(exc) == "RuntimeError: DB connect failed password=<redacted> at /runpod-volume"

    def test_strips_multiple_credential_variants(self) -> None:
        from acheron.core.errors import sanitise_exc_message

        exc = RuntimeError("auth api_key=abc123 token=xyz")
        result = sanitise_exc_message(exc)
        assert "abc123" not in result
        assert "xyz" not in result
