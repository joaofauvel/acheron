"""Async plan executor — runs independent steps concurrently."""

import asyncio
import time

from acheron.core.interfaces import Executor
from acheron.core.models import (
    JobStatus,
    OutputFile,
    Plan,
    PlanResult,
)
from acheron.shell.executors._utils import StepHandler, dependency_waves


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
        failed_steps: set[str] = set()
        errors: list[str] = []

        for wave in dependency_waves(plan.steps):
            runnable = [s for s in wave if not any(d in failed_steps for d in s.depends_on)]
            skipped = [s for s in wave if any(d in failed_steps for d in s.depends_on)]

            for step in skipped:
                failed_steps.add(step.step_id)
                failed += 1
                errors.append(f"{step.step_id}: skipped (dependency failed)")

            if not runnable:
                continue

            results = await asyncio.gather(
                *(self._handler(step, plan) for step in runnable),
                return_exceptions=True,
            )
            for step, result in zip(runnable, results, strict=True):
                if isinstance(result, BaseException):
                    failed += 1
                    failed_steps.add(step.step_id)
                    errors.append(f"{step.step_id}: {type(result).__name__}: {result}")
                elif result.status == JobStatus.SUCCESS:
                    completed += 1
                    outputs.extend(result.outputs)
                    total_cost += result.metrics.cost_estimate or 0.0
                else:
                    failed += 1
                    failed_steps.add(step.step_id)
                    errors.append(f"{step.step_id}: {result.error or 'unknown error'}")
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
            errors=tuple(errors),
        )
