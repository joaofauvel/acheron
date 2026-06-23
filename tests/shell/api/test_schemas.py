"""Pydantic models for the JobResponse + total_cost_basis round-trip."""

from pydantic import TypeAdapter

from acheron.shell.api.schemas import JobResponse

_adapter = TypeAdapter(JobResponse)


class TestJobResponseTotalCostBasis:
    def test_default_total_cost_basis_is_none(self) -> None:
        r = JobResponse(job_id="j", status="completed")
        assert r.total_cost_basis is None

    def test_explicit_total_cost_basis_round_trip(self) -> None:
        r = JobResponse(job_id="j", status="completed", total_cost_basis="measured")
        dumped = _adapter.dump_python(r)
        assert dumped["total_cost_basis"] == "measured"
        round_trip = _adapter.validate_python(dumped)
        assert round_trip.total_cost_basis == "measured"

    def test_total_cost_basis_serialization(self) -> None:
        r = JobResponse(job_id="j", status="completed", total_cost_basis="unknown")
        assert r.model_dump()["total_cost_basis"] == "unknown"
