"""Tests for cost evidence mapping and aggregation."""

from datetime import UTC, datetime

import pytest

from acheron.core.models import (
    CostBasis,
    CostBreakdown,
    CostEstimate,
    JobMetrics,
    JobResult,
    JobStatus,
    PlanStep,
    StepStatus,
    WorkerType,
)
from acheron.shell.cost import aggregate_cost_basis, build_cost_breakdown


def _breakdown(basis: CostBasis) -> CostBreakdown:
    return CostBreakdown(
        step_id="step",
        worker_type=WorkerType.TTS,
        worker_id="worker",
        gpu_seconds=1.0,
        estimate=CostEstimate(cost=0.1, basis=basis),
    )


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (CostBasis.MEASURED, CostBasis.CACHED, CostBasis.CACHED),
        (CostBasis.CACHED, CostBasis.STATIC, CostBasis.STATIC),
        (CostBasis.STATIC, CostBasis.STUB, CostBasis.STUB),
        (CostBasis.STUB, CostBasis.UNKNOWN, CostBasis.UNKNOWN),
        (CostBasis.MEASURED, CostBasis.UNKNOWN, CostBasis.UNKNOWN),
    ],
)
def test_aggregate_cost_basis_uses_confidence_order(left: CostBasis, right: CostBasis, expected: CostBasis) -> None:
    assert aggregate_cost_basis([_breakdown(left), _breakdown(right)]) is expected


def test_empty_returns_none() -> None:
    assert aggregate_cost_basis([]) is None


def test_cache_hit_without_estimate_has_no_breakdown() -> None:
    result = JobResult(
        job_id="job-cache",
        status=JobStatus.SUCCESS,
        outputs=(),
        metrics=JobMetrics(duration_seconds=0.0),
    )
    step = PlanStep("step", WorkerType.TTS, (), StepStatus.PENDING, {})
    assert build_cost_breakdown(step, result) is None


def test_failed_result_keeps_structured_cost_breakdown() -> None:
    estimate = CostEstimate(
        cost=0.34,
        basis=CostBasis.MEASURED,
        rate_per_hour=0.69,
        gpu_type="L4",
        secure_cloud=False,
        queried_at=datetime(2026, 7, 30, tzinfo=UTC),
        cache_age_seconds=0.0,
    )
    result = JobResult(
        job_id="job-1-step",
        status=JobStatus.FAILED,
        outputs=(),
        metrics=JobMetrics(duration_seconds=1800.0, gpu_seconds=1800.0, cost_estimate=estimate),
        error="worker failed",
        worker_id="tts-1",
    )

    breakdown = build_cost_breakdown(
        PlanStep("synthesize", WorkerType.TTS, (), StepStatus.PENDING, {}),
        result,
    )

    assert breakdown is not None
    assert breakdown.worker_id == "tts-1"
    assert breakdown.estimate.gpu_type == "L4"
