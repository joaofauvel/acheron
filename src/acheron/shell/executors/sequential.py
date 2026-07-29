"""Sequential plan executor — walks steps one at a time."""

import time
from datetime import UTC, datetime

from acheron.core.errors import sanitise_exc_message
from acheron.core.interfaces import Executor
from acheron.core.models import (
    JobMetrics,
    JobStatus,
    OutputFile,
    Plan,
    PlanResult,
    PlanStatus,
    StepError,
)
from acheron.shell.cost import aggregate_cost_basis
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
        errors: list[StepError] = []
        per_step_metrics: list[JobMetrics | None] = []

        for step in topological_order(plan.steps):
            if any(dep in failed_steps for dep in step.depends_on):
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
                per_step_metrics.append(None)
                continue

            try:
                result = await self._handler(step, plan)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                failed_steps.add(step.step_id)
                errors.append(
                    StepError(
                        step_id=step.step_id,
                        worker_type=step.type,
                        worker_id=None,
                        message=sanitise_exc_message(exc),
                        timestamp=datetime.now(UTC),
                    )
                )
                per_step_metrics.append(None)
                continue

            if result.status == JobStatus.SUCCESS:
                completed += 1
                outputs.extend(result.outputs)
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
            total_cost += result.metrics.cost_estimate or 0.0
            per_step_metrics.append(result.metrics)

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
            total_cost_basis=aggregate_cost_basis(per_step_metrics),
        )
