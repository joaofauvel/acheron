from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter

from acheron.core.models import (
    CostBasis,
    CostBreakdown,
    CostEstimate,
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
    StepError,
    StepStatus,
    VoiceRange,
    VoiceSelection,
    WorkerCapabilities,
    WorkerStatus,
    WorkerType,
    sanitize_worker_error,
)


def test_voice_range_rejects_overlap() -> None:
    with pytest.raises(ValueError, match="overlap"):
        VoiceSelection.from_ranges(
            default_voice=None,
            ranges=(VoiceRange(1, 3, "Vivian"), VoiceRange(3, 5, "Ryan")),
            chapter_count=5,
        )


def test_voice_selection_rejects_uncovered_and_out_of_range_chapters() -> None:
    with pytest.raises(ValueError, match="uncovered"):
        VoiceSelection.from_ranges(None, (VoiceRange(1, 2, "Vivian"),), chapter_count=3)
    with pytest.raises(ValueError, match="beyond"):
        VoiceSelection.from_ranges("Ryan", (VoiceRange(1, 4, "Vivian"),), chapter_count=3)


class TestWorkerErrorSanitization:
    @pytest.mark.parametrize(
        "message",
        [
            'provider aws error: request body: {"token":"secret", "url":"https://private.example"}',
            'Traceback (most recent call last):\n  File "worker.py", line 1\nRuntimeError: bearer secret',
            'provider details: request_id=req-123\nresponse: {"api_key": "secret"}\nValueError: leaked',
            "https://user:secret@worker.example:8443/path?token=secret failed with token=secret",
            "/srv/private/secret Traceback (most recent call last): File /srv/worker.py",
            r"\\server\share\secret ..\..\credentials",
            'worker failed: {"client_id":"client-123", "privateKey":"key-123", "refresh_token":"refresh-123"}',
            "worker failed AWS_ACCESS_KEY_ID=access-123 AWS_SECRET_ACCESS_KEY=secret-123",
        ],
    )
    def test_diagnostics_and_traceback_continuations_are_not_retained(self, message: str) -> None:
        sanitized = sanitize_worker_error(message)
        assert len(sanitized) <= 512
        assert all(
            secret not in sanitized.lower()
            for secret in (
                "secret",
                "token",
                "api_key",
                "request_id",
                "traceback",
                "client-123",
                "key-123",
                "refresh-123",
                "access-123",
            )
        )
        assert "https://" not in sanitized
        assert "worker.py" not in sanitized
        assert "leaked" not in sanitized

    def test_provider_error_without_diagnostic_marker_is_replaced_with_safe_summary(self) -> None:
        sanitized = sanitize_worker_error("provider aws error: upstream unavailable")
        assert sanitized == "provider check failed"
        assert "upstream unavailable" not in sanitized

    def test_prefixed_traceback_discards_traceback_and_exception_lines(self) -> None:
        sanitized = sanitize_worker_error("error Traceback (most recent call last):\nValueError: leaked")
        assert sanitized == "health check failed"
        assert "Traceback" not in sanitized
        assert "ValueError" not in sanitized
        assert "leaked" not in sanitized


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


class TestWorkerCapabilitiesMaxInputTokens:
    def test_default_is_none(self) -> None:
        caps = WorkerCapabilities(
            worker_type=WorkerType.TRANSLATION,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"es"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"text"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
        )
        assert caps.max_input_tokens is None

    def test_explicit_int(self) -> None:
        caps = WorkerCapabilities(
            worker_type=WorkerType.TRANSLATION,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"es"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"text"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
            max_input_tokens=2048,
        )
        assert caps.max_input_tokens == 2048

    def test_included_in_asdict(self) -> None:
        import dataclasses

        caps = WorkerCapabilities(
            worker_type=WorkerType.TRANSLATION,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"es"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"text"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
            max_input_tokens=2048,
        )
        assert dataclasses.asdict(caps)["max_input_tokens"] == 2048


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
            cost_estimate=CostEstimate(cost=0.05, basis=CostBasis.STATIC),
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
    def test_typed_step_errors_preserve_attribution(self) -> None:
        error = StepError(
            step_id="step-3",
            worker_type=WorkerType.TTS,
            worker_id="tts-1",
            message="malformed audio",
            timestamp=datetime(2026, 7, 29, tzinfo=UTC),
        )
        result = PlanResult(
            plan_id="plan-1",
            status=PlanStatus.FAILED,
            completed_steps=2,
            total_steps=5,
            outputs=(),
            total_cost=0.0,
            total_duration_seconds=4.5,
            errors=(error,),
        )

        assert result.errors[0].worker_id == "tts-1"
        assert result.errors[0].step_id == "step-3"

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
            (CostBasis.STUB, "stub"),
            (CostBasis.UNKNOWN, "unknown"),
        ],
    )
    def test_cost_basis_values(self, member: CostBasis, value: str) -> None:
        assert member.value == value


class TestJobMetricsCostEstimate:
    _adapter = TypeAdapter(JobMetrics)

    def test_default_cost_estimate_is_none(self) -> None:
        m = JobMetrics(duration_seconds=1.0)
        assert m.cost_estimate is None

    def test_explicit_cost_estimate_round_trip(self) -> None:
        estimate = CostEstimate(cost=0.042, basis=CostBasis.MEASURED)
        m = JobMetrics(duration_seconds=2.0, gpu_seconds=1.5, cost_estimate=estimate)
        dumped = self._adapter.dump_python(m)
        assert dumped["cost_estimate"]["cost"] == 0.042
        assert dumped["cost_estimate"]["basis"] is CostBasis.MEASURED
        round_trip = self._adapter.validate_python(dumped)
        assert round_trip.cost_estimate == estimate


def test_cost_estimate_preserves_rate_for_forensics() -> None:
    queried_at = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    estimate = CostEstimate(
        cost=0.34,
        basis=CostBasis.MEASURED,
        rate_per_hour=0.69,
        gpu_type="L4",
        secure_cloud=False,
        queried_at=queried_at,
        cache_age_seconds=0.0,
    )

    assert estimate.gpu_type == "L4"
    assert estimate.secure_cloud is False
    assert estimate.queried_at == queried_at


def test_cost_estimate_rejects_non_finite_cache_age() -> None:
    with pytest.raises(ValueError, match="cache_age_seconds"):
        CostEstimate(cost=0.34, basis=CostBasis.CACHED, cache_age_seconds=float("nan"))


def test_stub_cost_is_not_static() -> None:
    estimate = CostEstimate(cost=0.0, basis=CostBasis.STUB)

    assert estimate.basis is CostBasis.STUB


def test_cost_breakdown_preserves_step_and_worker_identity() -> None:
    breakdown = CostBreakdown(
        step_id="synthesize",
        worker_type=WorkerType.TTS,
        worker_id="tts-1",
        gpu_seconds=1800.0,
        estimate=CostEstimate(cost=0.34, basis=CostBasis.MEASURED),
    )

    assert breakdown.step_id == "synthesize"
    assert breakdown.worker_id == "tts-1"


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
