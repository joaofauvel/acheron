"""Batch async executor — extends async with batch submission for GPU workers."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from graphlib import TopologicalSorter

from acheron.core.interfaces import Executor
from acheron.core.models import (
    BatchJob,
    JobResult,
    JobStatus,
    OutputFile,
    Plan,
    PlanResult,
    PlanStep,
)

type StepHandler = Callable[[PlanStep, Plan], Awaitable[JobResult]]
type BatchSubmitter = Callable[[BatchJob], Awaitable[JobResult]]


class BatchAsyncExecutor(Executor):
    """Executes plan steps with batch submission for TTS/ASR steps."""

    def __init__(
        self,
        handler: StepHandler,
        batch_submitter: BatchSubmitter | None = None,
    ) -> None:
        self._handler = handler
        self._batch_submitter = batch_submitter

    async def run(self, plan: Plan) -> PlanResult:
        """Run steps in waves. Batch-flagged steps use batch submission."""
        start = time.monotonic()
        completed = 0
        failed = 0
        outputs: list[OutputFile] = []
        total_cost = 0.0

        for wave in _dependency_waves(plan.steps):
            batch_steps = [s for s in wave if s.batch and self._batch_submitter]
            regular_steps = [s for s in wave if not s.batch or not self._batch_submitter]

            tasks: list[Awaitable[JobResult]] = [self._handler(s, plan) for s in regular_steps]
            if self._batch_submitter:
                for step in batch_steps:
                    batch = BatchJob(batch_id=f"batch-{step.step_id}", jobs=())
                    tasks.append(self._batch_submitter(batch))

            results = await asyncio.gather(*tasks, return_exceptions=True)
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
