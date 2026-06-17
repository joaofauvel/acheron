"""Sequential plan executor — walks steps one at a time."""

import time

from acheron.core.interfaces import Executor
from acheron.core.models import (
    JobStatus,
    OutputFile,
    Plan,
    PlanResult,
)
from acheron.shell.executors._utils import StepHandler, topological_order


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
        failed_steps: set[str] = set()

        for step in topological_order(plan.steps):
            if any(dep in failed_steps for dep in step.depends_on):
                failed_steps.add(step.step_id)
                failed += 1
                continue

            result = await self._handler(step, plan)
            if result.status == JobStatus.SUCCESS:
                completed += 1
                outputs.extend(result.outputs)
            else:
                failed += 1
                failed_steps.add(step.step_id)
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
