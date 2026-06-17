"""Async plan executor — runs independent steps concurrently."""

import asyncio
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


class AsyncExecutor(Executor):
    """Executes plan steps concurrently where dependencies allow."""

    def __init__(self, handler: StepHandler) -> None:
        self._handler = handler

    async def run(self, plan: Plan) -> PlanResult:
        """Run steps in topological waves — each wave runs concurrently."""
        start = time.monotonic()
        completed = 0
        failed = 0
        outputs: list[OutputFile] = []
        total_cost = 0.0

        for wave in _dependency_waves(plan.steps):
            results = await asyncio.gather(
                *(self._handler(step, plan) for step in wave),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    failed += 1
                elif result.status == JobStatus.SUCCESS:
                    completed += 1
                    outputs.extend(result.outputs)
                    total_cost += result.metrics.cost_estimate or 0.0
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


def _dependency_waves(steps: tuple[PlanStep, ...]) -> list[list[PlanStep]]:
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
