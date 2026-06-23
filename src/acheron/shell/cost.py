"""Per-job cost-basis aggregation."""

from acheron.core.models import CostBasis, JobMetrics

_CONFIDENCE_ORDER = {
    CostBasis.MEASURED: 0,
    CostBasis.CACHED: 1,
    CostBasis.STATIC: 2,
    CostBasis.UNKNOWN: 3,
}


def aggregate_cost_basis(per_step: list[JobMetrics | None]) -> CostBasis | None:
    """Return the least-confident basis across steps, or None if no step has one.

    Used to surface the per-job cost confidence on the API + dashboard: a plan
    that ran mostly on ``MEASURED`` rates with one ``UNKNOWN`` step shows as
    ``UNKNOWN`` overall (the operator can't fully trust the total).
    """
    bases = [m.cost_basis for m in per_step if m is not None and m.cost_basis is not None]
    if not bases:
        return None
    return max(bases, key=lambda b: _CONFIDENCE_ORDER[b])
