"""Streaming pipeline executor — per-stage asyncio.Queue pipeline."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from acheron.core.errors import (
    AcheronError,
    CacheCorruptedError,
    CacheMissError,
    PipelineError,
    WorkerError,
)
from acheron.core.interfaces import Executor
from acheron.core.models import JobMetrics, JobResult, JobStatus, OutputFile, Plan, PlanResult, PlanStatus, PlanStep
from acheron.shell.cost import aggregate_cost_basis
from acheron.shell.executors._utils import StepHandler, topological_order

if TYPE_CHECKING:
    from acheron.shell.cache import InMemoryStepCache, StepCache

logger = logging.getLogger(__name__)


_END: None = None


class StreamingExecutor(Executor):
    """Pipeline executor with bounded backpressure and TaskGroup cancellation."""

    def __init__(
        self,
        handler: StepHandler,
        step_cache: StepCache | InMemoryStepCache,
        *,
        queue_size: int = 4,
        step_timeout: float = 1800.0,
        on_step_complete: Callable[[PlanStep, Plan, JobResult], None] | None = None,
    ) -> None:
        self._handler = handler
        self._cache = step_cache
        self._queue_size = queue_size
        self._step_timeout = step_timeout
        self._on_step_complete = on_step_complete

    async def run(self, plan: Plan) -> PlanResult:
        """Run the plan as a streaming pipeline. Returns a PlanResult."""
        start = time.monotonic()
        steps = topological_order(plan.steps)
        if not steps:
            return self._empty_result(plan, start)

        queues: list[asyncio.Queue[JobResult | None]] = [
            asyncio.Queue(maxsize=self._queue_size) for _ in range(len(steps) + 1)
        ]

        last_error, total_cost, per_step_metrics = await self._run_pipeline(steps, plan, queues)
        outputs, completed_count = await self._collect_outputs(steps, plan)
        return self._build_result(
            plan, steps, outputs, completed_count, total_cost, per_step_metrics, last_error, start
        )

    async def _run_pipeline(
        self,
        steps: list[PlanStep],
        plan: Plan,
        queues: list[asyncio.Queue[JobResult | None]],
    ) -> tuple[AcheronError | None, float, list[JobMetrics | None]]:
        """Run the per-stage TaskGroup. Returns (error, total cost, per-step metrics)."""
        stage_costs: list[float | None] = [None] * len(steps)
        stage_metrics: list[JobMetrics | None] = [None] * len(steps)

        def _make_recorder(idx: int) -> Callable[[float, JobMetrics], None]:
            def record(cost: float, metrics: JobMetrics) -> None:
                stage_costs[idx] = cost
                stage_metrics[idx] = metrics

            return record

        try:
            async with asyncio.TaskGroup() as tg:
                stage_tasks = [
                    tg.create_task(
                        self._stage(
                            step,
                            plan,
                            queues[i] if i > 0 else None,
                            queues[i + 1],
                            _make_recorder(i),
                        ),
                    )
                    for i, step in enumerate(steps)
                ]
                await self._consume_final_queue(tg, queues[-1])
                for task in stage_tasks:
                    task.result()
        except BaseExceptionGroup as eg:
            return (
                self._extract_error(eg),
                sum(c for c in stage_costs if c is not None),
                list(stage_metrics),
            )
        return None, sum(c for c in stage_costs if c is not None), list(stage_metrics)

    async def _consume_final_queue(self, tg: asyncio.TaskGroup, final_queue: asyncio.Queue[JobResult | None]) -> None:
        """Drain the final queue until _END; spawn a background drain task."""
        await final_queue.get()

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
        per_step_metrics: list[JobMetrics | None],
        last_error: AcheronError | None,
        start: float,
    ) -> PlanResult:
        duration = time.monotonic() - start
        total_cost_basis = aggregate_cost_basis(per_step_metrics)
        if last_error is None:
            return PlanResult(
                plan_id=plan.plan_id,
                status=PlanStatus.COMPLETED,
                completed_steps=len(steps),
                total_steps=len(steps),
                outputs=outputs,
                total_cost=total_cost,
                total_duration_seconds=duration,
                errors=(),
                total_cost_basis=total_cost_basis,
            )
        return PlanResult(
            plan_id=plan.plan_id,
            status=PlanStatus.FAILED,
            completed_steps=completed_count,
            total_steps=len(steps),
            outputs=outputs,
            total_cost=total_cost,
            total_duration_seconds=duration,
            errors=(str(last_error),),
            total_cost_basis=total_cost_basis,
        )

    async def _stage(
        self,
        step: PlanStep,
        plan: Plan,
        upstream: asyncio.Queue[JobResult | None] | None,
        downstream: asyncio.Queue[JobResult | None],
        record_cost: Callable[[float, JobMetrics], None],
    ) -> None:
        """Run a single stage. Records cost via ``record_cost`` before any status check.

        ``record_cost`` is a closure capturing the stage's index into a shared
        cost list, so the cost survives ``TaskGroup`` cancellation — a return
        value would be lost when a task raises after recording the cost.

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
                    return

            if await self._cache.step_has_valid_cache(plan.job_id, step.step_id):
                outputs = await self._cache.load_outputs(plan.job_id, step.step_id)
                result = JobResult(
                    job_id=plan.job_id,
                    status=JobStatus.SUCCESS,
                    outputs=outputs,
                    metrics=JobMetrics(duration_seconds=0.0),
                )
                record_cost(0.0, result.metrics)
                self._notify_step_complete(step, plan, result)
                await downstream.put(result)
                return

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

            # Capture cost before any status check so failed/partial steps
            # still report what they spent.
            record_cost(result.metrics.cost_estimate or 0.0, result.metrics)

            if result.status is not JobStatus.SUCCESS:
                msg = f"step {step.step_id} returned {result.status.value}: {result.error or 'unknown error'}"
                raise WorkerError(msg)

            try:
                await self._cache.save_outputs(plan.job_id, step.step_id, result.outputs)
            except Exception as exc:
                msg = f"save_outputs failed for step {step.step_id}"
                raise PipelineError(msg) from exc

            self._notify_step_complete(step, plan, result)
            await downstream.put(result)
        finally:
            await downstream.put(_END)

    def _notify_step_complete(self, step: PlanStep, plan: Plan, result: JobResult) -> None:
        if self._on_step_complete is not None:
            self._on_step_complete(step, plan, result)

    def _empty_result(self, plan: Plan, start: float) -> PlanResult:
        return PlanResult(
            plan_id=plan.plan_id,
            status=PlanStatus.COMPLETED,
            completed_steps=0,
            total_steps=0,
            outputs=(),
            total_cost=0.0,
            total_duration_seconds=time.monotonic() - start,
            errors=(),
        )
