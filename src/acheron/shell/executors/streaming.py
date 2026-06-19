"""Streaming pipeline executor — per-stage asyncio.Queue pipeline.

The plan's stages are dispatched sequentially via bounded queues. Each stage
runs in the outer ``asyncio.TaskGroup`` so a single failure cancels the
rest cleanly. Outputs are written to ``StepCache`` after each stage and
``PlanResult.outputs`` is built by scanning the cache at the end.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from acheron.core.errors import (
    AcheronError,
    CacheCorruptedError,
    CacheMissError,
    PipelineError,
    WorkerError,
)
from acheron.core.interfaces import Executor
from acheron.core.models import JobResult, JobStatus, OutputFile, Plan, PlanResult, PlanStep
from acheron.shell.executors._utils import StepHandler, topological_order

if TYPE_CHECKING:
    from acheron.shell.cache import StepCache

logger = logging.getLogger(__name__)


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

        last_error, total_cost = await self._run_pipeline(steps, plan, queues)
        outputs, completed_count = await self._collect_outputs(steps, plan)
        return self._build_result(plan, steps, outputs, completed_count, total_cost, last_error, start)

    async def _run_pipeline(
        self,
        steps: list[PlanStep],
        plan: Plan,
        queues: list[asyncio.Queue[JobResult | None]],
    ) -> tuple[AcheronError | None, float]:
        """Run the per-stage TaskGroup. Returns (first AcheronError, total cost from completed steps)."""
        total_cost = 0.0
        try:
            async with asyncio.TaskGroup() as tg:
                stage_tasks = [
                    tg.create_task(
                        self._stage(
                            step,
                            plan,
                            queues[i] if i > 0 else None,
                            queues[i + 1],
                        ),
                    )
                    for i, step in enumerate(steps)
                ]
                await self._consume_final_queue(tg, queues[-1])
                for task in stage_tasks:
                    step_cost = task.result()
                    if step_cost is not None:
                        total_cost += step_cost
        except BaseExceptionGroup as eg:
            return self._extract_error(eg), total_cost
        return None, total_cost

    async def _consume_final_queue(self, tg: asyncio.TaskGroup, final_queue: asyncio.Queue[JobResult | None]) -> None:
        first = await final_queue.get()
        if isinstance(first, AcheronError):
            raise first

        async def _drain() -> None:
            while True:
                item = await final_queue.get()
                if item is _END:
                    return

        tg.create_task(_drain())

    @staticmethod
    def _extract_error(eg: BaseExceptionGroup) -> AcheronError | None:
        acheron = [e for e in eg.exceptions if isinstance(e, AcheronError)]
        if acheron:
            return acheron[0]
        # Pick the first non-CancelledError, non-base exception as the cause.
        inner = next(
            (e for e in eg.exceptions if not isinstance(e, asyncio.CancelledError)),
            eg.exceptions[0] if eg.exceptions else None,
        )
        if inner is None:
            return None
        err = PipelineError(f"streaming failure: {inner}")
        err.__cause__ = inner
        return err

    async def _collect_outputs(self, steps: list[PlanStep], plan: Plan) -> tuple[tuple[OutputFile, ...], int]:
        """Return (all outputs, count of steps whose manifest was readable)."""
        outputs: list[OutputFile] = []
        completed = 0
        for step in steps:
            try:
                step_outputs = await self._cache.load_outputs(plan.job_id, step.step_id)
            except CacheMissError, CacheCorruptedError:
                # CacheMissError: step never ran or didn't reach save_outputs.
                # CacheCorruptedError: partial manifest from a mid-save cancellation.
                continue
            except OSError:
                logger.exception("failed to load outputs for %s", step.step_id)
                continue
            completed += 1
            outputs.extend(step_outputs)
        return tuple(outputs), completed

    def _build_result(  # noqa: PLR0913 - private helper takes the full set of computed fields
        self,
        plan: Plan,
        steps: list[PlanStep],
        outputs: tuple[OutputFile, ...],
        completed_count: int,
        total_cost: float,
        last_error: AcheronError | None,
        start: float,
    ) -> PlanResult:
        duration = time.monotonic() - start
        if last_error is None:
            return PlanResult(
                plan_id=plan.plan_id,
                status="completed",
                completed_steps=len(steps),
                total_steps=len(steps),
                outputs=outputs,
                total_cost=total_cost,
                total_duration_seconds=duration,
                errors=(),
            )
        return PlanResult(
            plan_id=plan.plan_id,
            status="failed",
            completed_steps=completed_count,
            total_steps=len(steps),
            outputs=outputs,
            total_cost=total_cost,
            total_duration_seconds=duration,
            errors=(str(last_error),),
        )

    async def _stage(
        self,
        step: PlanStep,
        plan: Plan,
        upstream: asyncio.Queue[JobResult | None] | None,
        downstream: asyncio.Queue[JobResult | None],
    ) -> float | None:
        """Run a single stage. Returns the step's cost on success, None on any failure.

        First stage has upstream=None and dispatches immediately. Subsequent
        stages read from upstream; a ``None`` sentinel means "no more work,
        drain and exit."
        """
        try:
            if upstream is not None:
                upstream_value = await upstream.get()
                # TODO(branchy-future): when plans gain parallel branches, distinguish
                # expected drain (upstream finished) from premature termination.
                if upstream_value is _END:
                    return None
            try:
                result = await asyncio.wait_for(
                    self._handler(step, plan),
                    timeout=self._step_timeout,
                )
            except TimeoutError as exc:
                msg = f"step {step.step_id} timed out after {self._step_timeout}s"
                raise WorkerError(msg) from exc
            except AcheronError:
                raise
            except Exception as exc:
                msg = f"unexpected failure in stage {step.step_id}: {type(exc).__name__}"
                raise PipelineError(msg) from exc

            if result.status is not JobStatus.SUCCESS:
                msg = f"step {step.step_id} returned {result.status.value}: {result.error or 'unknown error'}"
                raise WorkerError(msg)

            try:
                await self._cache.save_outputs(plan.job_id, step.step_id, result.outputs)
            except Exception as exc:
                msg = f"save_outputs failed for step {step.step_id}"
                raise PipelineError(msg) from exc

            await downstream.put(result)
            return result.metrics.cost_estimate or 0.0
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
