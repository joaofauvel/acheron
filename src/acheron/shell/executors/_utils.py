"""Shared utilities for plan executors."""

from collections.abc import Awaitable, Callable
from graphlib import TopologicalSorter

from acheron.core.models import (
    JobResult,
    Plan,
    PlanStep,
)

type StepHandler = Callable[[PlanStep, Plan], Awaitable[JobResult]]


def topological_order(steps: tuple[PlanStep, ...]) -> list[PlanStep]:
    """Sort steps by dependency order using stdlib TopologicalSorter."""
    by_id = {s.step_id: s for s in steps}
    graph = {s.step_id: set(s.depends_on) for s in steps}
    ts = TopologicalSorter(graph)
    return [by_id[sid] for sid in ts.static_order()]


def dependency_waves(steps: tuple[PlanStep, ...]) -> list[list[PlanStep]]:
    """Group steps into waves where each wave can run concurrently."""
    by_id = {s.step_id: s for s in steps}
    ts = TopologicalSorter({s.step_id: set(s.depends_on) for s in steps})
    waves: list[list[PlanStep]] = []

    ts.prepare()
    while ts.is_active():
        wave = [by_id[sid] for sid in ts.get_ready()]
        waves.append(wave)
        for step in wave:
            ts.done(step.step_id)

    return waves
