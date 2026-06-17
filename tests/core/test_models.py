import pytest

from acheron.core.models import (
    BatchJob,
    BatchStatus,
    ExecutorStrategy,
    Job,
    JobMetrics,
    JobResult,
    JobStatus,
    OutputFile,
    Plan,
    PlanResult,
    PlanStep,
    StepStatus,
    WorkerCapabilities,
    WorkerType,
)


class TestEnums:
    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (WorkerType.EXTRACTION, "extraction"),
            (WorkerType.CHUNKING, "chunking"),
            (WorkerType.TRANSLATION, "translation"),
            (WorkerType.ASR, "asr"),
            (WorkerType.TTS, "tts"),
            (WorkerType.PACKAGING, "packaging"),
        ],
    )
    def test_worker_type_values(self, member: WorkerType, value: str) -> None:
        assert member.value == value

    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (JobStatus.SUCCESS, "success"),
            (JobStatus.FAILED, "failed"),
            (JobStatus.PARTIAL, "partial"),
        ],
    )
    def test_job_status_values(self, member: JobStatus, value: str) -> None:
        assert member.value == value

    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (StepStatus.PENDING, "pending"),
            (StepStatus.RUNNING, "running"),
            (StepStatus.COMPLETE, "complete"),
            (StepStatus.FAILED, "failed"),
        ],
    )
    def test_step_status_values(self, member: StepStatus, value: str) -> None:
        assert member.value == value


class TestWorkerCapabilities:
    def test_construction(self) -> None:
        caps = WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset({"en", "es"}),
            supported_languages_out=frozenset({"en", "es"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav"}),
            max_payload_bytes=1024,
            batch_capable=True,
            model_source="huggingface:Qwen/Qwen3-TTS-12Hz-1.7B",
            metadata={"vram_gb": 8},
        )
        assert caps.worker_type == WorkerType.TTS
        assert "es" in caps.supported_languages_out
        assert caps.batch_capable is True

    def test_frozen(self) -> None:
        caps = WorkerCapabilities(
            worker_type=WorkerType.ASR,
            supported_languages_in=frozenset(),
            supported_languages_out=frozenset(),
            supported_formats_in=frozenset(),
            supported_formats_out=frozenset(),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
        )
        with pytest.raises(AttributeError):
            caps.worker_type = WorkerType.TTS  # type: ignore[misc]

    def test_default_metadata(self) -> None:
        caps = WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset(),
            supported_languages_out=frozenset(),
            supported_formats_in=frozenset(),
            supported_formats_out=frozenset(),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
        )
        assert caps.metadata == {}


class TestJob:
    def test_construction(self) -> None:
        job = Job(
            job_id="j-1",
            job_type=WorkerType.TTS,
            payload={"text": "hello"},
            chapter_id="ch1",
            sequence_ids=(0, 1, 2),
        )
        assert job.job_id == "j-1"
        assert job.sequence_ids == (0, 1, 2)

    def test_optional_sequence_ids(self) -> None:
        job = Job(
            job_id="j-2",
            job_type=WorkerType.EXTRACTION,
            payload={},
            chapter_id="ch1",
        )
        assert job.sequence_ids is None

    def test_frozen(self) -> None:
        job = Job(
            job_id="j-3",
            job_type=WorkerType.TTS,
            payload={},
            chapter_id="ch1",
        )
        with pytest.raises(AttributeError):
            job.job_id = "changed"  # type: ignore[misc]

    def test_nested_payload(self) -> None:
        job = Job(
            job_id="j-4",
            job_type=WorkerType.TTS,
            payload={"chunks": [{"text": "hello", "seq": 0}, {"text": "world", "seq": 1}]},
            chapter_id="ch1",
        )
        chunks = job.payload["chunks"]
        assert isinstance(chunks, list)
        assert len(chunks) == 2


class TestOutputFile:
    def test_construction(self) -> None:
        out = OutputFile(
            path="/data/jobs/j-1/chunk-0.wav",
            filename="chunk-0.wav",
            size_bytes=44100,
            checksum="abc123",
            content_type="audio/wav",
        )
        assert out.filename == "chunk-0.wav"


class TestJobMetrics:
    def test_defaults(self) -> None:
        metrics = JobMetrics(duration_seconds=1.5)
        assert metrics.gpu_seconds is None
        assert metrics.tokens_in is None
        assert metrics.cost_estimate is None

    def test_full(self) -> None:
        metrics = JobMetrics(
            duration_seconds=10.0,
            gpu_seconds=8.0,
            tokens_in=100,
            tokens_out=120,
            cost_estimate=0.05,
        )
        assert metrics.gpu_seconds == 8.0


class TestJobResult:
    def test_construction(self) -> None:
        result = JobResult(
            job_id="j-1",
            status=JobStatus.SUCCESS,
            outputs=(),
            metrics=JobMetrics(duration_seconds=1.0),
        )
        assert result.status == JobStatus.SUCCESS
        assert result.error is None


class TestPlanStep:
    def test_construction(self) -> None:
        step = PlanStep(
            step_id="extract",
            type=WorkerType.EXTRACTION,
            depends_on=(),
            status=StepStatus.PENDING,
            payload={"source_path": "/input/book.epub"},
        )
        assert step.batch is False

    def test_batch_flag(self) -> None:
        step = PlanStep(
            step_id="synthesize-ch1",
            type=WorkerType.TTS,
            depends_on=("translate-ch1",),
            status=StepStatus.PENDING,
            payload={},
            batch=True,
        )
        assert step.batch is True


class TestPlan:
    def test_construction(self) -> None:
        steps = (
            PlanStep(
                step_id="extract",
                type=WorkerType.EXTRACTION,
                depends_on=(),
                status=StepStatus.PENDING,
                payload={},
            ),
        )
        plan = Plan(
            plan_id="plan-1",
            job_id="job-1",
            source_type="epub",
            source_language="en",
            target_language="es",
            executor_strategy=ExecutorStrategy.BATCH_ASYNC,
            steps=steps,
        )
        assert len(plan.steps) == 1
        assert plan.steps[0].step_id == "extract"


class TestPlanResult:
    def test_construction(self) -> None:
        result = PlanResult(
            plan_id="plan-1",
            status="completed",
            completed_steps=5,
            total_steps=5,
            outputs=(),
            total_cost=0.50,
            total_duration_seconds=120.0,
        )
        assert result.status == "completed"


class TestBatchJob:
    def test_construction(self) -> None:
        jobs = (
            Job(job_id="j-1", job_type=WorkerType.TTS, payload={}, chapter_id="ch1"),
            Job(job_id="j-2", job_type=WorkerType.TTS, payload={}, chapter_id="ch1"),
        )
        batch = BatchJob(batch_id="b-1", jobs=jobs)
        assert len(batch.jobs) == 2


class TestBatchStatus:
    def test_construction(self) -> None:
        status = BatchStatus(
            batch_id="b-1",
            total=10,
            completed=7,
            failed=1,
            pending=2,
            results=(),
        )
        assert status.completed == 7
        assert status.pending == 2
