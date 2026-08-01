"""Pydantic models for the JobResponse + total_cost_basis round-trip and
WorkerResponse enum coercion."""

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from acheron.core.models import (
    CostBasis,
    ExecutorStrategy,
    Plan,
    PlanStatus,
    PlanStep,
    StepStatus,
    WorkerStatus,
    WorkerType,
)
from acheron.core.schemas import (
    CapabilitiesResponse,
    CostBreakdownResponse,
    CostEstimateResponse,
    CostSummaryResponse,
    InputResponse,
    JobCostResponse,
    JobLogEvent,
    JobProgress,
    JobResponse,
    OutputSummary,
    PlanResponse,
    WorkerCapability,
    WorkerResponse,
)

_adapter = TypeAdapter(JobResponse)


def _job_response_data(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "job_id": "j",
        "status": PlanStatus.COMPLETED,
        "plan_id": None,
        "label": None,
        "retries_from": None,
        "source_type": "epub",
        "source_language": "en",
        "target_language": "es",
        "asr_model": None,
        "executor_strategy": ExecutorStrategy.STREAMING,
        "created_at": datetime(2026, 7, 29, tzinfo=UTC),
        "last_persisted_at": datetime(2026, 7, 29, tzinfo=UTC),
        "progress": {},
        "total_cost": 0.0,
        "total_duration_seconds": 0.0,
        "total_cost_basis": None,
        "outputs": [],
        "errors": [],
        "warnings": [],
    }
    data.update(overrides)
    return data


def test_cost_estimate_response_round_trips_utc_metadata() -> None:
    response = CostEstimateResponse(
        cost=0.34,
        basis=CostBasis.MEASURED,
        rate_per_hour=0.69,
        gpu_type="L4",
        secure_cloud=False,
        queried_at=datetime(2026, 7, 30, 12, tzinfo=UTC),
        cache_age_seconds=0.0,
    )

    assert response.queried_at == datetime(2026, 7, 30, 12, tzinfo=UTC)
    assert response.model_dump(mode="json")["basis"] == "measured"


def test_cost_responses_reject_non_finite_values() -> None:
    with pytest.raises(ValidationError):
        CostEstimateResponse(cost=float("inf"), basis=CostBasis.UNKNOWN)
    with pytest.raises(ValidationError):
        CostSummaryResponse(
            window="all",
            since=None,
            until=datetime.now(UTC),
            total_cost=float("nan"),
            job_count=0,
            unknown_cost_jobs=0,
        )
    with pytest.raises(ValidationError):
        JobCostResponse(job_id="j-1", total_cost=-1, total_cost_basis=None, cost_breakdown=[])


def test_cost_estimate_response_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        CostEstimateResponse(
            cost=None,
            basis=CostBasis.UNKNOWN,
            queried_at=datetime(2026, 7, 30, 12, tzinfo=UTC).replace(tzinfo=None),
        )


def test_cost_estimate_response_rejects_negative_cache_age() -> None:
    with pytest.raises(ValidationError, match="cache_age_seconds"):
        CostEstimateResponse(
            cost=0.34,
            basis=CostBasis.CACHED,
            cache_age_seconds=-1.0,
        )


def test_cost_breakdown_response_round_trips_estimate() -> None:
    response = CostBreakdownResponse(
        step_id="synthesize",
        worker_type=WorkerType.TTS,
        worker_id="tts-1",
        gpu_seconds=1800.0,
        cost=0.34,
        basis=CostBasis.MEASURED,
        rate_per_hour=0.69,
        gpu_type="L4",
        secure_cloud=False,
        queried_at=datetime(2026, 7, 30, 12, tzinfo=UTC),
        cache_age_seconds=0.0,
    )

    assert response.basis is CostBasis.MEASURED


def test_output_summary_exposes_download_url_only() -> None:
    output = OutputSummary(
        download_url="/jobs/job-1/outputs/0",
        filename="result.m4b",
        size_bytes=5,
        content_type="audio/mp4",
    )

    dumped = output.model_dump()
    assert dumped["download_url"] == "/jobs/job-1/outputs/0"
    assert "path" not in dumped


class TestJobResponseTotalCostBasis:
    def test_default_total_cost_basis_is_none(self) -> None:
        r = JobResponse.model_validate(_job_response_data())
        assert r.total_cost_basis is None

    def test_warnings_default_to_empty(self) -> None:
        r = JobResponse.model_validate(_job_response_data())
        assert r.warnings == []

    def test_warnings_serialize_and_validate(self) -> None:
        warning = "BOOTING TTS workers: tts-1 (3s elapsed); cold start typically takes 30\u201390 seconds."
        r = JobResponse.model_validate(_job_response_data(status=PlanStatus.RUNNING, warnings=[warning]))
        dumped = _adapter.dump_python(r, mode="json")
        assert dumped["warnings"] == [warning]
        assert _adapter.validate_python(dumped).warnings == [warning]

    def test_explicit_total_cost_basis_round_trip(self) -> None:
        r = JobResponse.model_validate(_job_response_data(total_cost_basis=CostBasis.MEASURED))
        dumped = _adapter.dump_python(r, mode="json")
        assert dumped["total_cost_basis"] == "measured"
        round_trip = _adapter.validate_python(dumped)
        assert round_trip.total_cost_basis == CostBasis.MEASURED

    def test_total_cost_basis_serialization(self) -> None:
        r = JobResponse.model_validate(_job_response_data(total_cost_basis=CostBasis.UNKNOWN))
        assert r.model_dump(mode="json")["total_cost_basis"] == "unknown"

    def test_status_accepts_value_string(self) -> None:
        r = JobResponse.model_validate(_job_response_data(status="completed"))
        assert r.status is PlanStatus.COMPLETED

    def test_rejects_invalid_status(self) -> None:
        with pytest.raises(ValidationError):
            JobResponse.model_validate(_job_response_data(status="complted"))

    def test_rejects_invalid_cost_basis(self) -> None:
        with pytest.raises(ValidationError):
            JobResponse.model_validate(_job_response_data(total_cost_basis="not-a-basis"))


class TestWorkerResponseStatus:
    def test_timing_defaults(self) -> None:
        r = WorkerResponse(
            worker_id="w",
            endpoint="http://x",
            transport="http",
            worker_type="tts",
            consecutive_failures=0,
        )
        assert r.booting_elapsed_seconds is None
        assert r.booting_timeout_seconds == 600.0

    def test_accepts_enum_member(self) -> None:
        r = WorkerResponse(
            worker_id="w",
            endpoint="http://x",
            transport="http",
            worker_type="tts",
            consecutive_failures=0,
            status=WorkerStatus.HEALTHY,
        )
        assert r.status is WorkerStatus.HEALTHY

    def test_accepts_enum_value_string(self) -> None:
        r = WorkerResponse(
            worker_id="w",
            endpoint="http://x",
            transport="http",
            worker_type="tts",
            consecutive_failures=0,
            status="healthy",  # type: ignore[arg-type]
        )
        assert r.status is WorkerStatus.HEALTHY

    def test_rejects_invalid_status(self) -> None:
        with pytest.raises(ValidationError):
            WorkerResponse(
                worker_id="w",
                endpoint="http://x",
                transport="http",
                worker_type="tts",
                consecutive_failures=0,
                status="not-a-status",  # type: ignore[arg-type]
            )

    def test_json_serializes_to_value(self) -> None:
        r = WorkerResponse(
            worker_id="w",
            endpoint="http://x",
            transport="http",
            worker_type="tts",
            consecutive_failures=0,
            status=WorkerStatus.HEALTHY,
        )
        assert r.model_dump(mode="json")["status"] == "healthy"


def test_job_response_exposes_phase_4c_fields() -> None:
    response = JobResponse.model_validate(
        {
            "job_id": "job-1",
            "status": "failed",
            "plan_id": "plan-1",
            "label": "atlas-ch1",
            "retries_from": None,
            "source_type": "audio",
            "source_language": "en",
            "target_language": "es",
            "asr_model": "whisper-v3",
            "executor_strategy": "streaming",
            "created_at": "2026-07-29T12:00:00Z",
            "last_persisted_at": "2026-07-29T12:00:05Z",
            "progress": {
                "completed_steps": 2,
                "total_steps": 5,
                "current_step_id": "step-3",
                "current_worker_type": "tts",
                "current_worker_id": "tts-1",
                "eta_seconds": None,
            },
            "total_cost": 0.0,
            "total_duration_seconds": 4.5,
            "total_cost_basis": None,
            "outputs": [],
            "errors": [
                {
                    "step_id": "step-3",
                    "worker_type": "tts",
                    "worker_id": "tts-1",
                    "message": "malformed audio",
                    "timestamp": "2026-07-29T12:00:04Z",
                }
            ],
            "warnings": [],
        }
    )

    assert response.progress.current_worker_id == "tts-1"
    assert response.errors[0].message == "malformed audio"
    assert response.created_at.tzinfo is not None


def test_job_response_rejects_naive_lifecycle_timestamps() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        JobResponse.model_validate(_job_response_data(created_at=datetime.fromisoformat("2026-07-29T00:00:00")))


def test_job_response_normalizes_offset_lifecycle_timestamps() -> None:
    response = JobResponse.model_validate(_job_response_data(created_at="2026-07-29T14:00:00+02:00"))

    assert response.created_at == datetime(2026, 7, 29, 12, tzinfo=UTC)
    assert response.created_at.tzinfo == UTC


def test_job_log_event_serializes_as_one_json_object() -> None:
    event = JobLogEvent(
        job_id="job-1",
        timestamp=datetime(2026, 7, 29, tzinfo=UTC),
        status=PlanStatus.RUNNING,
        step_id="step-3",
        worker_type=WorkerType.TTS,
        worker_id="tts-1",
        progress=JobProgress(completed_steps=2, total_steps=5),
        message="step started",
    )

    assert event.model_dump_json().count("\n") == 0


def test_input_response_preserves_upload_metadata() -> None:
    response = InputResponse(
        source_path="inputs/id/book.epub",
        filename="book.epub",
        size_bytes=12,
        content_type="application/epub+zip",
    )
    assert InputResponse.model_validate(response.model_dump()) == response


def test_capabilities_response_defaults_worker_inventory_to_empty() -> None:
    response = CapabilitiesResponse(language_pairs=[])
    assert response.workers == []


def test_worker_capability_excludes_internal_model_and_metadata() -> None:
    response = WorkerCapability(
        worker_id="tts-1",
        worker_type="tts",
        model_source="Qwen/Qwen3-TTS",
        metadata={"voice": "vivian"},
    )
    assert "metadata" not in response.model_dump()
    assert "model_source" not in response.model_dump()


def test_capabilities_response_typed_mode_round_trips() -> None:
    """CapabilitiesResponse accepts typed-mode payloads but redacts internal fields on output."""
    payload = {
        "language_pairs": [],
        "workers": [
            {
                "worker_id": "tts-1",
                "worker_type": "tts",
                "model_source": "Qwen/Qwen3-TTS",
                "metadata": {"voice": "vivian"},
            },
            {
                "worker_id": "tts-2",
                "worker_type": "tts",
                "model_source": "Qwen/Qwen3-TTS",
                "metadata": {"voice": "aria"},
            },
        ],
    }
    response = CapabilitiesResponse.model_validate(payload)
    assert response.language_pairs == []
    assert [w.worker_id for w in response.workers] == ["tts-1", "tts-2"]
    assert response.workers[0].metadata == {"voice": "vivian"}
    assert response.workers[1].metadata == {"voice": "aria"}
    dumped = response.model_dump(mode="json")
    assert "metadata" not in dumped["workers"][0]
    assert "model_source" not in dumped["workers"][1]


def test_plan_response_exposes_structure_without_internal_payload() -> None:
    plan = Plan(
        plan_id="plan-1",
        job_id="job-1",
        source_type="epub",
        source_language="en",
        target_language="es",
        executor_strategy=ExecutorStrategy.STREAMING,
        steps=(
            PlanStep(
                step_id="extract",
                type=WorkerType.EXTRACTION,
                depends_on=(),
                status=StepStatus.PENDING,
                payload={"source_path": "/data/inputs/book.epub"},
            ),
        ),
    )

    response = PlanResponse.from_plan(plan)

    assert response.plan_id == "plan-1"
    assert response.steps[0].worker_type is WorkerType.EXTRACTION
    assert response.steps[0].depends_on == []
    assert response.steps[0].status is StepStatus.PENDING
    assert "payload" not in response.model_dump()
    assert "/data/inputs/book.epub" not in response.model_dump_json()
