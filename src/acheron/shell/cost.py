"""Cost evidence mapping and aggregation."""

from acheron.core.models import CostBasis, CostBreakdown, JobResult, PlanStep

_CONFIDENCE_ORDER = {
    CostBasis.MEASURED: 0,
    CostBasis.CACHED: 1,
    CostBasis.STATIC: 2,
    CostBasis.STUB: 3,
    CostBasis.UNKNOWN: 4,
}


def build_cost_breakdown(step: PlanStep, result: JobResult) -> CostBreakdown | None:
    """Map one dispatch result to step-level cost evidence."""
    estimate = result.metrics.cost_estimate
    if estimate is None:
        return None
    return CostBreakdown(
        step_id=step.step_id,
        worker_type=step.type,
        worker_id=result.worker_id,
        gpu_seconds=result.metrics.gpu_seconds,
        estimate=estimate,
    )


def aggregate_cost_basis(breakdown: tuple[CostBreakdown, ...] | list[CostBreakdown]) -> CostBasis | None:
    """Return the least-confident basis represented in ``breakdown``."""
    if not breakdown:
        return None
    return max((item.estimate.basis for item in breakdown), key=lambda basis: _CONFIDENCE_ORDER[basis])


def estimate_cost(breakdown: CostBreakdown | None) -> float:
    """Return a known estimate value, excluding unknown cost from totals."""
    if breakdown is None or breakdown.estimate.cost is None:
        return 0.0
    return breakdown.estimate.cost
