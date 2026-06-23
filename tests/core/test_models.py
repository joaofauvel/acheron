import pytest
from pydantic import TypeAdapter

from acheron.core.models import (
    CostBasis,
    ExecutorStrategy,
    Job,
    JobMetrics,
    JobResult,
    JobStatus,
    OutputFile,
    Plan,
    PlanResult,
    PlanStatus,
    PlanStep,
    StepStatus,
    WorkerCapabilities,
    WorkerStatus,
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

    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (WorkerStatus.HEALTHY, "healthy"),
            (WorkerStatus.BOOTING, "booting"),
            (WorkerStatus.OFFLINE, "offline"),
        ],
    )
    def test_worker_status_values(self, member: WorkerStatus, value: str) -> None:
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
        assert step.payload["source_path"] == "/input/book.epub"


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
            executor_strategy=ExecutorStrategy.STREAMING,
            steps=steps,
        )
        assert len(plan.steps) == 1
        assert plan.steps[0].step_id == "extract"


class TestPlanResult:
    def test_construction(self) -> None:
        result = PlanResult(
            plan_id="plan-1",
            status=PlanStatus.COMPLETED,
            completed_steps=5,
            total_steps=5,
            outputs=(),
            total_cost=0.50,
            total_duration_seconds=120.0,
        )
        assert result.status == PlanStatus.COMPLETED


class TestCostBasis:
    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (CostBasis.MEASURED, "measured"),
            (CostBasis.CACHED, "cached"),
            (CostBasis.STATIC, "static"),
            (CostBasis.UNKNOWN, "unknown"),
        ],
    )
    def test_cost_basis_values(self, member: CostBasis, value: str) -> None:
        assert member.value == value


class TestJobMetricsCostBasis:
    _adapter = TypeAdapter(JobMetrics)

    def test_default_cost_basis_is_none(self) -> None:
        m = JobMetrics(duration_seconds=1.0)
        assert m.cost_basis is None

    def test_explicit_cost_basis_round_trip(self) -> None:
        m = JobMetrics(
            duration_seconds=2.0,
            gpu_seconds=1.5,
            cost_estimate=0.042,
            cost_basis=CostBasis.MEASURED,
        )
        dumped = self._adapter.dump_python(m)
        assert dumped["cost_basis"] == CostBasis.MEASURED
        round_trip = self._adapter.validate_python(dumped)
        assert round_trip.cost_basis == CostBasis.MEASURED

    def test_none_cost_basis_round_trip(self) -> None:
        m = JobMetrics(duration_seconds=2.0)
        dumped = self._adapter.dump_python(m)
        round_trip = self._adapter.validate_python(dumped)
        assert round_trip.cost_basis is None


class TestPlanResultCostBasis:
    _adapter = TypeAdapter(PlanResult)

    def test_default_total_cost_basis_is_none(self) -> None:
        r = PlanResult(
            plan_id="p",
            status=PlanStatus.COMPLETED,
            completed_steps=0,
            total_steps=0,
            outputs=(),
            total_cost=0.0,
            total_duration_seconds=0.0,
            errors=(),
        )
        assert r.total_cost_basis is None

    def test_explicit_total_cost_basis_round_trip(self) -> None:
        r = PlanResult(
            plan_id="p",
            status=PlanStatus.COMPLETED,
            completed_steps=1,
            total_steps=1,
            outputs=(),
            total_cost=0.042,
            total_duration_seconds=1.0,
            errors=(),
            total_cost_basis=CostBasis.MEASURED,
        )
        dumped = self._adapter.dump_python(r)
        round_trip = self._adapter.validate_python(dumped)
        assert round_trip.total_cost_basis == CostBasis.MEASURED
