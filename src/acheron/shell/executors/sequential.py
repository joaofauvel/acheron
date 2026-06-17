"""Sequential plan executor — walks steps one at a time."""

import time
from collections.abc import Awaitable, Callable
from graphlib import TopologicalSorter

from acheron.core.interfaces import Executor
from acheron.core.models import (
    JobResult,
    JobStatus,
    OutputFile,
    Plan,
    PlanResult,
    PlanStep,
)

type StepHandler = Callable[[PlanStep, Plan], Awaitable[JobResult]]


class SequentialExecutor(Executor):
    """Executes plan steps in dependency order, one at a time."""

    def __init__(self, handler: StepHandler) -> None:
        self._handler = handler

    async def run(self, plan: Plan) -> PlanResult:
        """Walk steps in topological order, executing each sequentially."""
        start = time.monotonic()
        completed = 0
        failed = 0
        outputs: list[OutputFile] = []
        total_cost = 0.0

        for step in _topological_order(plan.steps):
            result = await self._handler(step, plan)
            if result.status == JobStatus.SUCCESS:
                completed += 1
                outputs.extend(result.outputs)
            else:
                failed += 1
            total_cost += result.metrics.cost_estimate or 0.0

        duration = time.monotonic() - start
        status = "completed" if failed == 0 else "failed" if completed == 0 else "partial"

        return PlanResult(
            plan_id=plan.plan_id,
            status=status,
            completed_steps=completed,
            total_steps=len(plan.steps),
            outputs=tuple(outputs),
            total_cost=total_cost,
            total_duration_seconds=duration,
        )


def _topological_order(steps: tuple[PlanStep, ...]) -> list[PlanStep]:
    """Sort steps by dependency order using stdlib TopologicalSorter."""
    by_id = {s.step_id: s for s in steps}
    graph = {s.step_id: set(s.depends_on) for s in steps}
    ts = TopologicalSorter(graph)
    return [by_id[sid] for sid in ts.static_order()]
