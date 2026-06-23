"""Tests for the StreamingExecutor."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
import pytest_asyncio

from acheron.core.models import (
    CostBasis,
    ExecutorStrategy,
    JobMetrics,
    JobResult,
    JobStatus,
    OutputFile,
    Plan,
    PlanStatus,
    PlanStep,
    StepStatus,
    WorkerType,
)
from acheron.shell.cache import StepCache
from acheron.shell.executors._utils import StepHandler
from acheron.shell.executors.streaming import StreamingExecutor


def _real_output(tmp_path: Path, name: str, body: bytes = b"x" * 16) -> OutputFile:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)
    return OutputFile(
        path=str(p),
        filename=name,
        size_bytes=len(body),
        checksum=hashlib.sha256(body).hexdigest(),
        content_type="audio/wav",
    )


def _make_handler(
    outputs: dict[str, list[OutputFile]],
) -> tuple[StepHandler, list[str]]:
    calls: list[str] = []

    async def handler(step: PlanStep, plan: Plan) -> JobResult:
        calls.append(step.step_id)
        await asyncio.sleep(0)
        return JobResult(
            job_id=plan.job_id,
            status=JobStatus.SUCCESS,
            outputs=tuple(outputs[step.step_id]),
            metrics=JobMetrics(duration_seconds=0.0),
        )

    return handler, calls


def _linear_plan(job_id: str = "job-1", plan_id: str = "plan-1") -> Plan:
    return Plan(
        plan_id=plan_id,
        job_id=job_id,
        source_type="epub",
        source_language="en",
        target_language="es",
        executor_strategy=ExecutorStrategy.STREAMING,
        steps=(
            PlanStep(
                step_id="extract",
                type=WorkerType.EXTRACTION,
                depends_on=(),
                status=StepStatus.PENDING,
                payload={"source_path": "/tmp/x"},
            ),
            PlanStep(
                step_id="chunk",
                type=WorkerType.CHUNKING,
                depends_on=("extract",),
                status=StepStatus.PENDING,
                payload={},
            ),
            PlanStep(
                step_id="package",
                type=WorkerType.PACKAGING,
                depends_on=("chunk",),
                status=StepStatus.PENDING,
                payload={},
            ),
        ),
    )


@pytest_asyncio.fixture
async def step_cache(tmp_path: Path) -> StepCache:
    return StepCache(tmp_path)


class TestNormalCompletion:
    @pytest.mark.asyncio
    async def test_three_step_plan_completes(self, tmp_path: Path, step_cache: StepCache) -> None:
        plan = _linear_plan()
        outputs = {
            "extract": [_real_output(tmp_path, "extracted.txt")],
            "chunk": [_real_output(tmp_path, "chunked.txt")],
            "package": [_real_output(tmp_path, "out.wav", body=b"audio-bytes")],
        }
        handler, calls = _make_handler(outputs)
        executor = StreamingExecutor(handler, step_cache)

        result = await executor.run(plan)

        assert result.status == PlanStatus.COMPLETED
        assert result.completed_steps == 3
        assert result.total_steps == 3
        assert calls == ["extract", "chunk", "package"]
        assert len(result.outputs) == 3
        filenames = {o.filename for o in result.outputs}
        assert filenames == {"extracted.txt", "chunked.txt", "out.wav"}

    @pytest.mark.asyncio
    async def test_valid_cached_step_is_not_dispatched(self, tmp_path: Path, step_cache: StepCache) -> None:
        plan = _linear_plan(job_id="job-cache-test")
        cached = (_real_output(tmp_path, "cached-extract.txt"),)
        await step_cache.save_outputs(plan.job_id, "extract", cached)
        outputs = {
            "chunk": [_real_output(tmp_path, "chunked.txt")],
            "package": [_real_output(tmp_path, "out.wav", body=b"audio-bytes")],
        }
        handler, calls = _make_handler(outputs)
        executor = StreamingExecutor(handler, step_cache)

        result = await executor.run(plan)

        assert result.status == PlanStatus.COMPLETED
        assert calls == ["chunk", "package"]
        assert {out.filename for out in result.outputs} == {"cached-extract.txt", "chunked.txt", "out.wav"}


class TestStepTimeout:
    @pytest.mark.asyncio
    async def test_slow_handler_raises_worker_error(self, tmp_path: Path, step_cache: StepCache) -> None:
        plan = _linear_plan()

        async def slow_handler(step: PlanStep, plan: Plan) -> JobResult:
            await asyncio.sleep(0.5)
            return JobResult(
                job_id=plan.job_id,
                status=JobStatus.SUCCESS,
                outputs=(_real_output(tmp_path, "out.wav"),),
                metrics=JobMetrics(duration_seconds=0.0),
            )

        executor = StreamingExecutor(slow_handler, step_cache, step_timeout=0.05)
        result = await executor.run(plan)

        assert result.status == PlanStatus.FAILED
        assert "timed out" in result.errors[0].lower()


class TestWorkerError:
    @pytest.mark.asyncio
    async def test_worker_unavailable_propagates(self, tmp_path: Path, step_cache: StepCache) -> None:
        from acheron.core.errors import WorkerUnavailableError

        plan = _linear_plan()

        async def failing_handler(step: PlanStep, plan: Plan) -> JobResult:
            msg = f"no worker for {step.step_id}"
            raise WorkerUnavailableError(msg)

        executor = StreamingExecutor(failing_handler, step_cache)
        result = await executor.run(plan)

        assert result.status == PlanStatus.FAILED
        assert any("no worker" in e.lower() for e in result.errors)


class TestNonSuccessResult:
    @pytest.mark.asyncio
    async def test_handler_returning_failed_status_marks_plan_failed(
        self,
        tmp_path: Path,
        step_cache: StepCache,
    ) -> None:
        """A handler that returns JobResult(status=FAILED) without raising
        must mark the plan as failed, skip downstream stages, and surface
        the worker's error message in the result."""
        plan = _linear_plan()
        downstream_called: list[str] = []

        async def handler(step: PlanStep, plan: Plan) -> JobResult:
            if step.step_id == "extract":
                return JobResult(
                    job_id=plan.job_id,
                    status=JobStatus.FAILED,
                    outputs=(),
                    metrics=JobMetrics(duration_seconds=0.0),
                    error="worker reported failure",
                )
            downstream_called.append(step.step_id)
            return JobResult(
                job_id=plan.job_id,
                status=JobStatus.SUCCESS,
                outputs=(_real_output(tmp_path, f"{step.step_id}.out"),),
                metrics=JobMetrics(duration_seconds=0.0),
            )

        executor = StreamingExecutor(handler, step_cache)
        result = await executor.run(plan)

        assert result.status == PlanStatus.FAILED
        assert result.total_steps == 3
        assert result.completed_steps == 0
        assert downstream_called == []
        assert any("worker reported failure" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_partial_result_also_treated_as_failure(
        self,
        tmp_path: Path,
        step_cache: StepCache,
    ) -> None:
        """PARTIAL status is not SUCCESS, so it must mark the plan as failed
        and skip downstream stages, matching AsyncExecutor's semantics."""
        plan = _linear_plan()

        async def handler(step: PlanStep, plan: Plan) -> JobResult:
            return JobResult(
                job_id=plan.job_id,
                status=JobStatus.PARTIAL,
                outputs=(_real_output(tmp_path, f"{step.step_id}.out"),),
                metrics=JobMetrics(duration_seconds=0.0),
                error="some outputs missing",
            )

        executor = StreamingExecutor(handler, step_cache)
        result = await executor.run(plan)

        assert result.status == PlanStatus.FAILED
        assert any("partial" in e.lower() for e in result.errors)

    @pytest.mark.asyncio
    async def test_failed_status_preserves_cost_estimate(
        self,
        tmp_path: Path,
        step_cache: StepCache,
    ) -> None:
        """A handler that returns FAILED with a non-zero cost_estimate must
        preserve that cost in the final PlanResult, matching AsyncExecutor."""
        plan = _linear_plan()

        async def handler(step: PlanStep, plan: Plan) -> JobResult:
            if step.step_id == "extract":
                return JobResult(
                    job_id=plan.job_id,
                    status=JobStatus.FAILED,
                    outputs=(),
                    metrics=JobMetrics(duration_seconds=0.0, cost_estimate=0.42),
                    error="worker reported failure",
                )
            return JobResult(
                job_id=plan.job_id,
                status=JobStatus.SUCCESS,
                outputs=(_real_output(tmp_path, f"{step.step_id}.out"),),
                metrics=JobMetrics(duration_seconds=0.0, cost_estimate=0.1),
            )

        executor = StreamingExecutor(handler, step_cache)
        result = await executor.run(plan)

        assert result.status == PlanStatus.FAILED
        assert result.total_cost == 0.42  # failed step's cost preserved


class TestUnexpectedException:
    @pytest.mark.asyncio
    async def test_unhandled_exception_wrapped_as_pipeline_error(self, tmp_path: Path, step_cache: StepCache) -> None:
        plan = _linear_plan()

        async def bad_handler(step: PlanStep, plan: Plan) -> JobResult:
            msg = "boom"
            raise RuntimeError(msg)

        executor = StreamingExecutor(bad_handler, step_cache)
        result = await executor.run(plan)

        assert result.status == PlanStatus.FAILED
        assert any("unexpected failure in stage" in e.lower() for e in result.errors)


class TestCacheFailure:
    @pytest.mark.asyncio
    async def test_save_outputs_failure_wrapped_as_pipeline_error(self, tmp_path: Path, step_cache: StepCache) -> None:
        plan = _linear_plan()

        async def ok_handler(step: PlanStep, plan: Plan) -> JobResult:
            return JobResult(
                job_id=plan.job_id,
                status=JobStatus.SUCCESS,
                outputs=(_real_output(tmp_path, "out.wav"),),
                metrics=JobMetrics(duration_seconds=0.0),
            )

        executor = StreamingExecutor(ok_handler, step_cache)

        async def broken_save(*_args: object, **_kwargs: object) -> None:
            msg = "disk full"
            raise OSError(msg)

        step_cache.save_outputs = broken_save  # type: ignore[method-assign]

        result = await executor.run(plan)

        assert result.status == PlanStatus.FAILED
        assert any("save_outputs" in e.lower() for e in result.errors)


class TestTaskGroupCancellation:
    """Note: in a linear pipeline, the failing stage is necessarily the
    latest to start dispatching (the queue serializes them), so a stage
    cannot be in the middle of a handler dispatch when cancellation arrives.
    Branchy plans would change this. The current observable is that
    downstream stages' ``await upstream.get()`` is interrupted — covered
    by TestSentinelDrain (the sentinel is what causes the early exit).
    """


class TestSentinelDrain:
    @pytest.mark.asyncio
    async def test_sentinel_propagates_downstream(self, tmp_path: Path, step_cache: StepCache) -> None:
        from acheron.core.errors import WorkerUnavailableError

        plan = _linear_plan()
        completed: list[str] = []

        async def handler(step: PlanStep, plan: Plan) -> JobResult:
            if step.step_id == "extract":
                raise WorkerUnavailableError("extract failed")
            completed.append(step.step_id)
            return JobResult(
                job_id=plan.job_id,
                status=JobStatus.SUCCESS,
                outputs=(_real_output(tmp_path, f"{step.step_id}.out"),),
                metrics=JobMetrics(duration_seconds=0.0),
            )

        executor = StreamingExecutor(handler, step_cache)
        result = await executor.run(plan)

        assert result.status == PlanStatus.FAILED
        assert completed == []


class TestCostAccumulation:
    @pytest.mark.asyncio
    async def test_total_cost_sums_step_metrics(self, tmp_path: Path, step_cache: StepCache) -> None:
        """PlanResult.total_cost is the sum of completed step metrics.cost_estimate."""
        plan = _linear_plan()

        async def handler(step: PlanStep, plan: Plan) -> JobResult:
            return JobResult(
                job_id=plan.job_id,
                status=JobStatus.SUCCESS,
                outputs=(_real_output(tmp_path, f"{step.step_id}.out"),),
                metrics=JobMetrics(duration_seconds=0.0, cost_estimate=0.5),
            )

        executor = StreamingExecutor(handler, step_cache)
        result = await executor.run(plan)

        assert result.status == PlanStatus.COMPLETED
        # 3 steps * 0.5 each = 1.5
        assert result.total_cost == 1.5


class TestCompletedStepsCount:
    @pytest.mark.asyncio
    async def test_completed_steps_counts_successful_only(self, tmp_path: Path, step_cache: StepCache) -> None:
        """When the last stage fails, completed_steps reflects the steps that
        actually wrote a manifest, not 0."""
        from acheron.core.errors import WorkerUnavailableError

        plan = _linear_plan()

        async def handler(step: PlanStep, plan: Plan) -> JobResult:
            if step.step_id == "package":
                raise WorkerUnavailableError("package failed")
            return JobResult(
                job_id=plan.job_id,
                status=JobStatus.SUCCESS,
                outputs=(_real_output(tmp_path, f"{step.step_id}.out"),),
                metrics=JobMetrics(duration_seconds=0.0),
            )

        executor = StreamingExecutor(handler, step_cache)
        result = await executor.run(plan)

        assert result.status == PlanStatus.FAILED
        assert result.total_steps == 3
        assert result.completed_steps == 2  # extract + chunk succeeded


class TestCacheCorruptionResilience:
    @pytest.mark.asyncio
    async def test_corrupted_manifest_does_not_crash_executor(self, tmp_path: Path, step_cache: StepCache) -> None:
        """A partial/invalid manifest on disk is treated as 'no outputs for this step'
        and does not propagate the error. The plan still runs to completion."""
        plan = _linear_plan()

        async def handler(step: PlanStep, plan: Plan) -> JobResult:
            return JobResult(
                job_id=plan.job_id,
                status=JobStatus.SUCCESS,
                outputs=(_real_output(tmp_path, f"{step.step_id}.out"),),
                metrics=JobMetrics(duration_seconds=0.0),
            )

        executor = StreamingExecutor(handler, step_cache)
        await executor.run(plan)  # populate the cache

        # Now corrupt the extract manifest.
        extract_manifest = step_cache.data_dir / plan.job_id / "extract" / "manifest.json"
        extract_manifest.write_text("not valid json {")

        # Re-run with a different handler. The corrupted manifest is just skipped.
        async def fresh_handler(step: PlanStep, plan: Plan) -> JobResult:
            return JobResult(
                job_id=plan.job_id,
                status=JobStatus.SUCCESS,
                outputs=(_real_output(tmp_path, f"fresh-{step.step_id}.out"),),
                metrics=JobMetrics(duration_seconds=0.0),
            )

        fresh_executor = StreamingExecutor(fresh_handler, step_cache)
        result = await fresh_executor.run(plan)

        assert result.status == PlanStatus.COMPLETED
        assert result.completed_steps == 3


class TestCacheCorruptionTolerantLoad:
    """A corrupt manifest on disk is treated as 'no outputs for this step'."""

    @pytest.mark.asyncio
    async def test_corrupt_manifest_load_returns_zero_outputs(self, tmp_path: Path, step_cache: StepCache) -> None:
        """If load_outputs raises CacheCorruptedError, the executor skips that
        step's outputs rather than crashing the whole run."""
        # Pre-write a corrupt manifest.
        step_dir = step_cache.data_dir / "job-1" / "extract"
        step_dir.mkdir(parents=True, exist_ok=True)
        (step_dir / "manifest.json").write_text("not valid json {")

        plan = Plan(
            plan_id="plan-x",
            job_id="job-1",
            source_type="epub",
            source_language="en",
            target_language="es",
            executor_strategy=ExecutorStrategy.STREAMING,
            steps=(
                PlanStep(
                    step_id="extract",
                    type=WorkerType.EXTRACTION,
                    depends_on=(),
                    status=StepStatus.PENDING,
                    payload={},
                ),
            ),
        )

        # The corrupt manifest load is silently skipped.
        outputs, completed = await StreamingExecutor(  # noqa: SLF001
            _make_handler({"extract": []})[0], step_cache
        )._collect_outputs(list(plan.steps), plan)
        assert outputs == ()
        assert completed == 0


class TestOutputsFromCache:
    @pytest.mark.asyncio
    async def test_outputs_sourced_from_cache_not_in_memory(self, tmp_path: Path, step_cache: StepCache) -> None:
        """If the cache is patched to return different outputs than the handler wrote,
        the PlanResult must reflect the cache (proving cache is the source, not the
        in-memory handler return)."""
        plan = _linear_plan()
        handler_outputs = {
            "extract": [_real_output(tmp_path, "real-extracted.txt")],
            "chunk": [_real_output(tmp_path, "real-chunked.txt")],
            "package": [_real_output(tmp_path, "real-out.wav")],
        }
        handler, _ = _make_handler(handler_outputs)
        executor = StreamingExecutor(handler, step_cache)
        await executor.run(plan)  # populate the cache

        # Replace the cache with a stub that returns decoy outputs.
        decoy = [_real_output(tmp_path, "decoy.txt", body=b"D" * 16)]
        real_load = step_cache.load_outputs

        async def patched_load(job_id: str, step_id: str) -> tuple[OutputFile, ...]:
            return tuple(decoy)

        step_cache.load_outputs = patched_load  # type: ignore[method-assign]
        try:
            # Re-run with a fresh handler that returns yet more outputs.
            fresh_outputs = {
                "extract": [_real_output(tmp_path, "fresh-extracted.txt")],
                "chunk": [_real_output(tmp_path, "fresh-chunked.txt")],
                "package": [_real_output(tmp_path, "fresh-out.wav")],
            }
            fresh_handler, _ = _make_handler(fresh_outputs)
            fresh_executor = StreamingExecutor(fresh_handler, step_cache)
            result = await fresh_executor.run(plan)

            # The cache is patched to return decoy for any step, so the
            # result must be composed entirely of decoys — proving the
            # executor sources outputs from the cache, not from the
            # in-memory handler returns.
            assert len(result.outputs) == 3
            for output in result.outputs:
                assert output.filename == "decoy.txt"
        finally:
            step_cache.load_outputs = real_load  # type: ignore[method-assign]


class TestTotalCostBasis:
    """P2-C1: StreamingExecutor must populate PlanResult.total_cost_basis
    via aggregate_cost_basis() across per-step metrics."""

    @pytest.mark.asyncio
    async def test_three_step_plan_least_confident_basis(self, tmp_path: Path, step_cache: StepCache) -> None:
        # extract=MEASURED, chunk=CACHED, package=STATIC → least-confident is CACHED.
        per_step_basis = {
            "extract": CostBasis.MEASURED,
            "chunk": CostBasis.CACHED,
            "package": CostBasis.STATIC,
        }

        async def handler(step: PlanStep, plan: Plan) -> JobResult:
            return JobResult(
                job_id=plan.job_id,
                status=JobStatus.SUCCESS,
                outputs=(_real_output(tmp_path, f"{step.step_id}.wav"),),
                metrics=JobMetrics(duration_seconds=0.1, cost_estimate=0.01, cost_basis=per_step_basis[step.step_id]),
            )

        plan = _linear_plan()
        executor = StreamingExecutor(handler, step_cache)
        result = await executor.run(plan)
        # Confidence order: MEASURED=0, CACHED=1, STATIC=2, UNKNOWN=3.
        # least-confident wins → STATIC.
        assert result.total_cost_basis == CostBasis.STATIC

    @pytest.mark.asyncio
    async def test_unknown_step_dominates(self, tmp_path: Path, step_cache: StepCache) -> None:
        # extract=MEASURED, chunk=UNKNOWN → UNKNOWN dominates.
        per_step_basis = {
            "extract": CostBasis.MEASURED,
            "chunk": CostBasis.UNKNOWN,
            "package": CostBasis.MEASURED,
        }

        async def handler(step: PlanStep, plan: Plan) -> JobResult:
            return JobResult(
                job_id=plan.job_id,
                status=JobStatus.SUCCESS,
                outputs=(_real_output(tmp_path, f"{step.step_id}.wav"),),
                metrics=JobMetrics(duration_seconds=0.1, cost_estimate=0.01, cost_basis=per_step_basis[step.step_id]),
            )

        plan = _linear_plan()
        executor = StreamingExecutor(handler, step_cache)
        result = await executor.run(plan)
        assert result.total_cost_basis == CostBasis.UNKNOWN

    @pytest.mark.asyncio
    async def test_no_basis_anywhere_yields_none(self, tmp_path: Path, step_cache: StepCache) -> None:
        async def handler(step: PlanStep, plan: Plan) -> JobResult:
            return JobResult(
                job_id=plan.job_id,
                status=JobStatus.SUCCESS,
                outputs=(_real_output(tmp_path, f"{step.step_id}.wav"),),
                metrics=JobMetrics(duration_seconds=0.1),
            )

        plan = _linear_plan()
        executor = StreamingExecutor(handler, step_cache)
        result = await executor.run(plan)
        assert result.total_cost_basis is None
