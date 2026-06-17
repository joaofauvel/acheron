"""Batch async executor — extends async with batch submission for GPU workers."""

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


class BatchAsyncExecutor(Executor):
    """Executes plan steps with batch submission for TTS/ASR steps.

    Batch-flagged steps receive all outputs from completed preceding steps
    so the handler can construct a BatchJob with the correct payloads.
    """

    def __init__(self, handler: StepHandler) -> None:
        self._handler = handler

    async def run(self, plan: Plan) -> PlanResult:
        """Run steps in waves. Batch-flagged steps use batch submission."""
        start = time.monotonic()
        completed = 0
        failed = 0
        outputs: list[OutputFile] = []
        total_cost = 0.0
        failed_steps: set[str] = set()

        for wave in dependency_waves(plan.steps):
            runnable = [s for s in wave if not any(d in failed_steps for d in s.depends_on)]
            skipped = [s for s in wave if any(d in failed_steps for d in s.depends_on)]

            for step in skipped:
                failed_steps.add(step.step_id)
                failed += 1

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
                elif result.status == JobStatus.SUCCESS:
                    completed += 1
                    outputs.extend(result.outputs)
                    total_cost += result.metrics.cost_estimate or 0.0
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
