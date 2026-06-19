"""Streaming pipeline executor — per-stage asyncio.Queue pipeline.

The plan's stages are dispatched sequentially via bounded queues. Each stage
runs in the outer ``asyncio.TaskGroup`` so a single failure cancels the
rest cleanly. Outputs are written to ``StepCache`` after each stage and
``PlanResult.outputs`` is built by scanning the cache at the end.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from acheron.core.errors import AcheronError, PipelineError, WorkerError
from acheron.core.interfaces import Executor
from acheron.core.models import JobResult, OutputFile, Plan, PlanResult, PlanStep
from acheron.shell.executors._utils import StepHandler, topological_order

if TYPE_CHECKING:
    from acheron.shell.cache import StepCache


_END: None = None


class StreamingExecutor(Executor):
    """Pipeline executor with bounded backpressure and TaskGroup cancellation."""

    def __init__(
        self,
        handler: StepHandler,
        step_cache: StepCache,
        *,
        queue_size: int = 4,
        step_timeout: float = 1800.0,
    ) -> None:
        self._handler = handler
        self._cache = step_cache
        self._queue_size = queue_size
        self._step_timeout = step_timeout

    async def run(self, plan: Plan) -> PlanResult:
        """Run the plan as a streaming pipeline. Returns a PlanResult."""
        start = time.monotonic()
        steps = topological_order(plan.steps)
        if not steps:
            return self._empty_result(plan, start)

        queues: list[asyncio.Queue[JobResult | None]] = [
            asyncio.Queue(maxsize=self._queue_size) for _ in range(len(steps) + 1)
        ]
        # Seed the first queue with a sentinel so the first stage knows to start.
        await queues[0].put(_END)

        total_cost = 0.0
        last_error: AcheronError | None = None

        try:
            async with asyncio.TaskGroup() as tg:
                stage_tasks = []
                for i, step in enumerate(steps):
                    upstream = queues[i]
                    downstream = queues[i + 1]
                    stage_tasks.append(
                        tg.create_task(self._stage(step, plan, upstream, downstream))
                    )
                final_queue = queues[-1]
                first = await final_queue.get()
                if isinstance(first, AcheronError):
                    raise first

                async def _drain() -> None:
                    while True:
                        item = await final_queue.get()
                        if item is _END:
                            return

                tg.create_task(_drain())

                for task in stage_tasks:
                    task.result()
        except BaseExceptionGroup as eg:
            acheron = [e for e in eg.exceptions if isinstance(e, AcheronError)]
            if acheron:
                last_error = acheron[0]
            elif eg.exceptions:
                inner = eg.exceptions[0]
                last_error = PipelineError(f"streaming failure: {inner}")
                last_error.__cause__ = inner

        outputs: list[OutputFile] = []
        for step in steps:
            try:
                step_outputs = await self._cache.load_outputs(plan.job_id, step.step_id)
            except Exception:  # noqa: BLE001
                continue
            outputs.extend(step_outputs)

        if last_error is None:
            return PlanResult(
                plan_id=plan.plan_id,
                status="completed",
                completed_steps=len(steps),
                total_steps=len(steps),
                outputs=tuple(outputs),
                total_cost=total_cost,
                total_duration_seconds=time.monotonic() - start,
                errors=(),
            )

        return PlanResult(
            plan_id=plan.plan_id,
            status="failed",
            completed_steps=0,
            total_steps=len(steps),
            outputs=tuple(outputs),
            total_cost=total_cost,
            total_duration_seconds=time.monotonic() - start,
            errors=(str(last_error),),
        )

    async def _stage(
        self,
        step: PlanStep,
        plan: Plan,
        upstream: asyncio.Queue[JobResult | None],
        downstream: asyncio.Queue[JobResult | None],
    ) -> None:
        """Stage consumer: read upstream, dispatch, write downstream + cache."""
        try:
            _ = await upstream.get()
            try:
                result = await asyncio.wait_for(
                    self._handler(step, plan),
                    timeout=self._step_timeout,
                )
            except asyncio.TimeoutError as exc:
                msg = f"step {step.step_id} timed out after {self._step_timeout}s"
                raise WorkerError(msg) from exc

            try:
                await self._cache.save_outputs(plan.job_id, step.step_id, result.outputs)
            except Exception as exc:
                msg = f"save_outputs failed for step {step.step_id}"
                raise PipelineError(msg) from exc

            await downstream.put(result)
        finally:
            await downstream.put(_END)

    def _empty_result(self, plan: Plan, start: float) -> PlanResult:
        return PlanResult(
            plan_id=plan.plan_id,
            status="completed",
            completed_steps=0,
            total_steps=0,
            outputs=(),
            total_cost=0.0,
            total_duration_seconds=time.monotonic() - start,
            errors=(),
        )
