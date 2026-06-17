"""Tests for plan executors."""

import pytest

from acheron.core.models import (
    ExecutorStrategy,
    JobMetrics,
    JobResult,
    JobStatus,
    Plan,
    PlanStep,
    StepStatus,
    WorkerType,
)
from acheron.shell.executors.async_executor import AsyncExecutor
from acheron.shell.executors.batch_async import BatchAsyncExecutor
from acheron.shell.executors.sequential import SequentialExecutor


def _step(step_id: str, depends_on: tuple[str, ...] = ()) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        type=WorkerType.EXTRACTION,
        depends_on=depends_on,
        status=StepStatus.PENDING,
        payload={},
    )


def _plan(steps: tuple[PlanStep, ...]) -> Plan:
    return Plan(
        plan_id="plan-1",
        job_id="job-1",
        source_type="epub",
        source_language="en",
        target_language="es",
        executor_strategy=ExecutorStrategy.SEQUENTIAL,
        steps=steps,
    )


def _success_result(cost: float = 0.01) -> JobResult:
    return JobResult(
        job_id="j-1",
        status=JobStatus.SUCCESS,
        outputs=(),
        metrics=JobMetrics(duration_seconds=0.1, cost_estimate=cost),
    )


def _fail_result() -> JobResult:
    return JobResult(
        job_id="j-1",
        status=JobStatus.FAILED,
        outputs=(),
        metrics=JobMetrics(duration_seconds=0.1),
        error="worker failed",
    )


class TestSequentialExecutor:
    @pytest.mark.asyncio
    async def test_runs_steps_in_order(self) -> None:
        order: list[str] = []

        async def handler(step: PlanStep, _plan: Plan) -> JobResult:
            order.append(step.step_id)
            return _success_result()

        plan = _plan((_step("a"), _step("b"), _step("c")))
        result = await SequentialExecutor(handler).run(plan)
        assert order == ["a", "b", "c"]
        assert result.status == "completed"
        assert result.completed_steps == 3

    @pytest.mark.asyncio
    async def test_respects_dependencies(self) -> None:
        order: list[str] = []

        async def handler(step: PlanStep, _plan: Plan) -> JobResult:
            order.append(step.step_id)
            return _success_result()

        plan = _plan((_step("b", ("a",)), _step("a"), _step("c", ("b",))))
        result = await SequentialExecutor(handler).run(plan)
        assert order.index("a") < order.index("b")
        assert order.index("b") < order.index("c")
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_failed_step_produces_partial(self) -> None:
        async def handler(step: PlanStep, _plan: Plan) -> JobResult:
            if step.step_id == "b":
                return _fail_result()
            return _success_result()

        plan = _plan((_step("a"), _step("b"), _step("c")))
        result = await SequentialExecutor(handler).run(plan)
        assert result.status == "partial"
        assert result.completed_steps == 2

    @pytest.mark.asyncio
    async def test_all_failed(self) -> None:
        async def handler(_step: PlanStep, _plan: Plan) -> JobResult:
            return _fail_result()

        plan = _plan((_step("a"), _step("b")))
        result = await SequentialExecutor(handler).run(plan)
        assert result.status == "failed"
        assert result.completed_steps == 0

    @pytest.mark.asyncio
    async def test_skips_dependents_of_failed_step(self) -> None:
        executed: list[str] = []

        async def handler(step: PlanStep, _plan: Plan) -> JobResult:
            executed.append(step.step_id)
            if step.step_id == "a":
                return _fail_result()
            return _success_result()

        plan = _plan((_step("a"), _step("b", ("a",)), _step("c", ("a",))))
        result = await SequentialExecutor(handler).run(plan)
        assert executed == ["a"]
        assert result.status == "failed"
        assert result.completed_steps == 0

    @pytest.mark.asyncio
    async def test_empty_plan(self) -> None:
        async def handler(_step: PlanStep, _plan: Plan) -> JobResult:
            return _success_result()

        result = await SequentialExecutor(handler).run(_plan(()))
        assert result.status == "completed"
        assert result.total_steps == 0

    @pytest.mark.asyncio
    async def test_single_step(self) -> None:
        async def handler(_step: PlanStep, _plan: Plan) -> JobResult:
            return _success_result()

        result = await SequentialExecutor(handler).run(_plan((_step("a"),)))
        assert result.status == "completed"
        assert result.completed_steps == 1

    @pytest.mark.asyncio
    async def test_handler_raises_exception(self) -> None:
        async def handler(_step: PlanStep, _plan: Plan) -> JobResult:
            raise RuntimeError("worker crashed")

        plan = _plan((_step("a"), _step("b", ("a",))))
        with pytest.raises(RuntimeError, match="worker crashed"):
            await SequentialExecutor(handler).run(plan)

    @pytest.mark.asyncio
    async def test_cost_accumulated(self) -> None:
        async def handler(step: PlanStep, _plan: Plan) -> JobResult:  # noqa: ARG001
            return _success_result(cost=0.5)

        plan = _plan((_step("a"), _step("b")))
        result = await SequentialExecutor(handler).run(plan)
        assert result.total_cost == 1.0


class TestAsyncExecutor:
    @pytest.mark.asyncio
    async def test_runs_independent_steps_concurrently(self) -> None:
        async def handler(_step: PlanStep, _plan: Plan) -> JobResult:
            return _success_result()

        plan = _plan((_step("a"), _step("b"), _step("c")))
        result = await AsyncExecutor(handler).run(plan)
        assert result.status == "completed"
        assert result.completed_steps == 3

    @pytest.mark.asyncio
    async def test_respects_dependencies(self) -> None:
        order: list[str] = []

        async def handler(step: PlanStep, _plan: Plan) -> JobResult:
            order.append(step.step_id)
            return _success_result()

        plan = _plan((_step("b", ("a",)), _step("a"), _step("c", ("b",))))
        await AsyncExecutor(handler).run(plan)
        assert order.index("a") < order.index("b")
        assert order.index("b") < order.index("c")

    @pytest.mark.asyncio
    async def test_failed_step_counted(self) -> None:
        async def handler(step: PlanStep, _plan: Plan) -> JobResult:
            if step.step_id == "b":
                return _fail_result()
            return _success_result()

        plan = _plan((_step("a"), _step("b"), _step("c")))
        result = await AsyncExecutor(handler).run(plan)
        assert result.status == "partial"
        assert result.completed_steps == 2

    @pytest.mark.asyncio
    async def test_skips_dependents_of_failed_step(self) -> None:
        executed: list[str] = []

        async def handler(step: PlanStep, _plan: Plan) -> JobResult:
            executed.append(step.step_id)
            if step.step_id == "a":
                return _fail_result()
            return _success_result()

        plan = _plan((_step("a"), _step("b", ("a",)), _step("c", ("a",))))
        result = await AsyncExecutor(handler).run(plan)
        assert "a" in executed
        assert "b" not in executed
        assert "c" not in executed
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_handler_raises_counts_as_failure(self) -> None:
        async def handler(step: PlanStep, _plan: Plan) -> JobResult:
            if step.step_id == "a":
                raise RuntimeError("crash")
            return _success_result()

        plan = _plan((_step("a"), _step("b"), _step("c")))
        result = await AsyncExecutor(handler).run(plan)
        assert result.status == "partial"
        assert result.completed_steps == 2

    @pytest.mark.asyncio
    async def test_empty_plan(self) -> None:
        async def handler(_step: PlanStep, _plan: Plan) -> JobResult:
            return _success_result()

        result = await AsyncExecutor(handler).run(_plan(()))
        assert result.status == "completed"


class TestBatchAsyncExecutor:
    @pytest.mark.asyncio
    async def test_runs_regular_steps(self) -> None:
        async def handler(_step: PlanStep, _plan: Plan) -> JobResult:
            return _success_result()

        plan = _plan((_step("a"), _step("b")))
        result = await BatchAsyncExecutor(handler).run(plan)
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_skips_dependents_of_failed_step(self) -> None:
        async def handler(step: PlanStep, _plan: Plan) -> JobResult:
            if step.step_id == "a":
                return _fail_result()
            return _success_result()

        plan = _plan((_step("a"), _step("b", ("a",))))
        result = await BatchAsyncExecutor(handler).run(plan)
        assert result.status == "failed"
        assert result.completed_steps == 0
