"""Async plan executor — runs independent steps concurrently."""

import asyncio
import time
from datetime import UTC, datetime

from acheron.core.errors import sanitise_exc_message
from acheron.core.interfaces import Executor
from acheron.core.models import (
    JobStatus,
    OutputFile,
    Plan,
    PlanResult,
    PlanStatus,
    StepError,
)
from acheron.shell.cost import aggregate_cost_basis, build_cost_breakdown, estimate_cost
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
        errors: list[StepError] = []
        cost_breakdown = []

        for wave in dependency_waves(plan.steps):
            runnable = [s for s in wave if not any(d in failed_steps for d in s.depends_on)]
            skipped = [s for s in wave if any(d in failed_steps for d in s.depends_on)]

            for step in skipped:
                failed_steps.add(step.step_id)
                failed += 1
                errors.append(
                    StepError(
                        step_id=step.step_id,
                        worker_type=step.type,
                        worker_id=None,
                        message="skipped (dependency failed)",
                        timestamp=datetime.now(UTC),
                    )
                )

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
                    errors.append(
                        StepError(
                            step_id=step.step_id,
                            worker_type=step.type,
                            worker_id=None,
                            message=sanitise_exc_message(result),
                            timestamp=datetime.now(UTC),
                        )
                    )
                elif result.status == JobStatus.SUCCESS:
                    completed += 1
                    outputs.extend(result.outputs)
                    item = build_cost_breakdown(step, result)
                    total_cost += estimate_cost(item)
                    if item is not None:
                        cost_breakdown.append(item)
                else:
                    failed += 1
                    failed_steps.add(step.step_id)
                    errors.append(
                        StepError(
                            step_id=step.step_id,
                            worker_type=step.type,
                            worker_id=result.worker_id,
                            message=result.error or "unknown error",
                            timestamp=datetime.now(UTC),
                        )
                    )
                    item = build_cost_breakdown(step, result)
                    total_cost += estimate_cost(item)
                    if item is not None:
                        cost_breakdown.append(item)

        duration = time.monotonic() - start
        status = PlanStatus.COMPLETED if failed == 0 else PlanStatus.FAILED if completed == 0 else PlanStatus.PARTIAL

        return PlanResult(
            plan_id=plan.plan_id,
            status=status,
            completed_steps=completed,
            total_steps=len(plan.steps),
            outputs=tuple(outputs),
            total_cost=total_cost,
            total_duration_seconds=duration,
            errors=tuple(errors),
            total_cost_basis=aggregate_cost_basis(cost_breakdown),
            cost_breakdown=tuple(cost_breakdown),
        )
