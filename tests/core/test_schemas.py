"""Pydantic models for the JobResponse + total_cost_basis round-trip and
WorkerResponse enum coercion."""

import pytest
from pydantic import TypeAdapter, ValidationError

from acheron.core.models import CostBasis, PlanStatus, WorkerStatus
from acheron.core.schemas import (
    CapabilitiesResponse,
    InputResponse,
    JobResponse,
    WorkerCapability,
    WorkerResponse,
)

_adapter = TypeAdapter(JobResponse)


class TestJobResponseTotalCostBasis:
    def test_default_total_cost_basis_is_none(self) -> None:
        r = JobResponse(job_id="j", status=PlanStatus.COMPLETED)
        assert r.total_cost_basis is None

    def test_warnings_default_to_empty(self) -> None:
        r = JobResponse(job_id="j", status=PlanStatus.COMPLETED)
        assert r.warnings == []

    def test_warnings_serialize_and_validate(self) -> None:
        warning = "BOOTING TTS workers: tts-1 (3s elapsed); cold start typically takes 30\u201390 seconds."
        r = JobResponse(job_id="j", status=PlanStatus.RUNNING, warnings=[warning])
        dumped = _adapter.dump_python(r, mode="json")
        assert dumped["warnings"] == [warning]
        assert _adapter.validate_python(dumped).warnings == [warning]

    def test_explicit_total_cost_basis_round_trip(self) -> None:
        r = JobResponse(
            job_id="j",
            status=PlanStatus.COMPLETED,
            total_cost_basis=CostBasis.MEASURED,
        )
        dumped = _adapter.dump_python(r, mode="json")
        assert dumped["total_cost_basis"] == "measured"
        round_trip = _adapter.validate_python(dumped)
        assert round_trip.total_cost_basis == CostBasis.MEASURED

    def test_total_cost_basis_serialization(self) -> None:
        r = JobResponse(
            job_id="j",
            status=PlanStatus.COMPLETED,
            total_cost_basis=CostBasis.UNKNOWN,
        )
        assert r.model_dump(mode="json")["total_cost_basis"] == "unknown"

    def test_status_accepts_value_string(self) -> None:
        r = JobResponse(job_id="j", status="completed")  # type: ignore[arg-type]
        assert r.status is PlanStatus.COMPLETED

    def test_rejects_invalid_status(self) -> None:
        with pytest.raises(ValidationError):
            JobResponse(job_id="j", status="complted")  # type: ignore[arg-type]

    def test_rejects_invalid_cost_basis(self) -> None:
        with pytest.raises(ValidationError):
            JobResponse(
                job_id="j",
                status=PlanStatus.COMPLETED,
                total_cost_basis="not-a-basis",  # type: ignore[arg-type]
            )


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


def test_worker_capability_preserves_model_and_metadata() -> None:
    response = WorkerCapability(
        worker_id="tts-1",
        worker_type="tts",
        model_source="Qwen/Qwen3-TTS",
        metadata={"voice": "vivian"},
    )
    assert response.model_dump()["metadata"] == {"voice": "vivian"}
