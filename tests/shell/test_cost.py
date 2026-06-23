"""Tests for cost-basis aggregation across plan steps."""

from acheron.core.models import CostBasis, JobMetrics
from acheron.shell.cost import aggregate_cost_basis


def test_empty_returns_none() -> None:
    assert aggregate_cost_basis([]) is None


def test_all_none_returns_none() -> None:
    assert aggregate_cost_basis([None, None]) is None


def test_single_measured() -> None:
    assert aggregate_cost_basis([JobMetrics(duration_seconds=1.0, cost_basis=CostBasis.MEASURED)]) == CostBasis.MEASURED


def test_least_confident_wins() -> None:
    """MEASURED + UNKNOWN → UNKNOWN (operator can't trust the total)."""
    bases = [
        JobMetrics(duration_seconds=1.0, cost_basis=CostBasis.MEASURED),
        JobMetrics(duration_seconds=1.0, cost_basis=CostBasis.CACHED),
        JobMetrics(duration_seconds=1.0, cost_basis=CostBasis.STATIC),
        JobMetrics(duration_seconds=1.0, cost_basis=CostBasis.UNKNOWN),
    ]
    assert aggregate_cost_basis(bases) == CostBasis.UNKNOWN


def test_skips_none_metrics() -> None:
    """A None entry (skipped step) is ignored — only real metrics contribute."""
    bases = [
        None,
        JobMetrics(duration_seconds=1.0, cost_basis=CostBasis.MEASURED),
        None,
    ]
    assert aggregate_cost_basis(bases) == CostBasis.MEASURED


def test_skips_metrics_without_basis() -> None:
    """A metric with cost_basis=None (no price source wired) is ignored."""
    bases = [
        JobMetrics(duration_seconds=1.0, cost_basis=None),
        JobMetrics(duration_seconds=1.0, cost_basis=CostBasis.STATIC),
    ]
    assert aggregate_cost_basis(bases) == CostBasis.STATIC
