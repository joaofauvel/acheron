"""Tests for the step handler."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from acheron.core.errors import VoiceSelectionError
from acheron.core.models import (
    ExecutorStrategy,
    Job,
    JobMetrics,
    JobResult,
    JobStatus,
    OutputFile,
    Plan,
    PlanStep,
    StepStatus,
    WorkerCapabilities,
    WorkerStatus,
    WorkerType,
)
from acheron.shell.cache import StepCache
from acheron.shell.local_handlers import LocalJobHandler
from acheron.shell.registry import RegisteredWorker
from acheron.shell.step_handler import CachingStepHandler, create_step_handler, default_worker_factory
from acheron.shell.stores.memory import InMemoryWorkerStore
from acheron.shell.transports.http import HttpWorker
from acheron.shell.transports.local import LocalWorker

_TEST_DATA_DIR = Path("/tmp/acheron-test-step-handler")


async def _echo_job_result(job: object) -> JobResult:
    return JobResult(
        job_id="j-1",
        status=JobStatus.SUCCESS,
        outputs=(),
        metrics=JobMetrics(duration_seconds=0.1),
    )


def _tts_caps() -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TTS,
        supported_languages_in=frozenset({"es"}),
        supported_languages_out=frozenset({"es"}),
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"wav"}),
        max_payload_bytes=None,
        batch_capable=True,
        model_source=None,
    )


def _voice_tts_caps(*voices: str) -> WorkerCapabilities:
    return WorkerCapabilities(**{**_tts_caps().__dict__, "metadata": {"speakers": list(voices)}})


def _make_plan() -> Plan:
    return Plan(
        plan_id="plan-1",
        job_id="job-1",
        source_type="epub",
        source_language="en",
        target_language="es",
        executor_strategy=ExecutorStrategy.STREAMING,
        steps=(
            PlanStep(
                step_id="synthesize",
                type=WorkerType.TTS,
                depends_on=(),
                status=StepStatus.PENDING,
                payload={"target_language": "es", "chapter_id": "ch1"},
                selected_worker_id="tts-1",
            ),
        ),
    )


class TestStepHandler:
    @pytest.mark.asyncio
    async def test_dispatches_to_planner_selected_worker(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", _tts_caps())
        await reg.register("tts-2", "http://127.0.0.1:2", "http", _tts_caps())
        chosen: list[str] = []

        def _factory(registered: RegisteredWorker) -> LocalWorker:
            chosen.append(registered.worker_id)
            return LocalWorker(
                worker_type=WorkerType.TTS,
                handler=_echo_job_result,
                supported_languages_in=frozenset({"es"}),
                supported_languages_out=frozenset({"es"}),
            )

        handler = create_step_handler(reg, worker_factory=_factory, data_dir=_TEST_DATA_DIR)
        plan = _make_plan()
        result = await handler(plan.steps[0], plan)
        assert result.worker_id == "tts-1"
        assert chosen == ["tts-1"]

    @pytest.mark.asyncio
    async def test_offline_selected_tts_worker_is_rejected(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", _tts_caps())
        await reg.set_worker_status("tts-1", WorkerStatus.OFFLINE, "down")
        handler = create_step_handler(reg, data_dir=_TEST_DATA_DIR)

        with pytest.raises(VoiceSelectionError, match="offline"):
            await handler(_make_plan().steps[0], _make_plan())

    @pytest.mark.asyncio
    async def test_non_tts_dispatch_refreshes_worker_status_between_steps(self) -> None:
        reg = InMemoryWorkerStore()
        translation_caps = WorkerCapabilities(
            worker_type=WorkerType.TRANSLATION,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"es"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"text"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
        )
        await reg.register("trans-2", "http://127.0.0.1:2", "http", translation_caps)
        await reg.register("trans-1", "http://127.0.0.1:1", "http", translation_caps)
        chosen: list[str] = []

        def _factory(registered: RegisteredWorker) -> LocalWorker:
            chosen.append(registered.worker_id)
            return LocalWorker(
                worker_type=WorkerType.TRANSLATION,
                handler=_echo_job_result,
                supported_languages_in=frozenset({"en"}),
                supported_languages_out=frozenset({"es"}),
            )

        handler = create_step_handler(reg, worker_factory=_factory, data_dir=_TEST_DATA_DIR)
        plan = _make_plan()
        step = replace(
            plan.steps[0],
            step_id="translate",
            type=WorkerType.TRANSLATION,
            payload={"text": "hello"},
            selected_worker_id=None,
        )
        await handler(step, plan)
        await reg.set_worker_status("trans-1", WorkerStatus.OFFLINE, "down")
        await handler(replace(step, step_id="translate-2"), plan)

        assert chosen == ["trans-1", "trans-2"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "unsafe_voice",
        [
            "https://example.test/?token=secret",
            "/private/secret",
            "token=secret",
            "../../secrets/token",
            r"..\..\Users\alice\secret",
        ],
    )
    async def test_tts_errors_redact_unsafe_voice_labels(self, unsafe_voice: str) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", _voice_tts_caps("Vivian"))
        handler = create_step_handler(reg, data_dir=_TEST_DATA_DIR)
        step = replace(
            _make_plan().steps[0],
            payload={"target_language": "es", "voice": unsafe_voice},
        )
        with pytest.raises(VoiceSelectionError) as raised:
            await handler(step, _make_plan())
        assert unsafe_voice not in str(raised.value)

    @pytest.mark.asyncio
    async def test_tts_capabilities_are_refetched_after_registration_change(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", _voice_tts_caps("Vivian"))
        worker = LocalWorker(
            worker_type=WorkerType.TTS,
            handler=_echo_job_result,
            supported_languages_in=frozenset({"es"}),
            supported_languages_out=frozenset({"es"}),
        )
        handler = create_step_handler(reg, worker_factory=lambda _registered: worker, data_dir=_TEST_DATA_DIR)
        plan = replace(
            _make_plan(),
            steps=(replace(_make_plan().steps[0], payload={"target_language": "es", "voice": "Vivian"}),),
        )
        await handler(plan.steps[0], plan)
        await reg.register("tts-1", "http://127.0.0.1:1", "http", _voice_tts_caps("Ryan"))
        with pytest.raises(VoiceSelectionError):
            await handler(plan.steps[0], plan)

    @pytest.mark.asyncio
    async def test_tts_capabilities_are_refetched_after_worker_removal(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", _voice_tts_caps("Vivian"))
        handler = create_step_handler(
            reg,
            worker_factory=lambda _registered: LocalWorker(
                worker_type=WorkerType.TTS,
                handler=_echo_job_result,
                supported_languages_in=frozenset({"es"}),
                supported_languages_out=frozenset({"es"}),
            ),
            data_dir=_TEST_DATA_DIR,
        )
        plan = replace(
            _make_plan(),
            steps=(replace(_make_plan().steps[0], payload={"target_language": "es", "voice": "Vivian"}),),
        )
        await handler(plan.steps[0], plan)
        await reg.unregister("tts-1")
        with pytest.raises(VoiceSelectionError):
            await handler(plan.steps[0], plan)

    @pytest.mark.asyncio
    async def test_dispatches_to_matching_worker(self) -> None:
        reg = InMemoryWorkerStore()
        local_worker = LocalWorker(
            worker_type=WorkerType.TTS,
            handler=_echo_job_result,
            supported_languages_in=frozenset({"es"}),
            supported_languages_out=frozenset({"es"}),
        )
        await reg.register("tts-1", "http://127.0.0.1:1", "http", _tts_caps())
        handler = create_step_handler(reg, worker_factory=lambda _reg: local_worker, data_dir=_TEST_DATA_DIR)
        plan = _make_plan()
        step = plan.steps[0]
        result = await handler(step, plan)
        assert result.status == JobStatus.SUCCESS
        assert result.worker_id == "tts-1"

    @pytest.mark.asyncio
    async def test_raises_when_no_worker_found(self) -> None:
        reg = InMemoryWorkerStore()
        handler = create_step_handler(reg, data_dir=_TEST_DATA_DIR)
        plan = _make_plan()
        step = plan.steps[0]
        with pytest.raises(VoiceSelectionError, match="selected TTS worker"):
            await handler(step, plan)

    @pytest.mark.asyncio
    async def test_asr_worker_missing_output_language_is_skipped(self) -> None:
        """When two ASR workers are registered and the first has the source
        language in its input set but NOT in its output set, the handler must
        skip it and select the second worker (matching the planner's check)."""
        reg = InMemoryWorkerStore()
        chosen_worker_id: list[str] = []

        async def _echo(_job: object) -> JobResult:
            return JobResult(
                job_id="j-1",
                status=JobStatus.SUCCESS,
                outputs=(),
                metrics=JobMetrics(duration_seconds=0.0),
            )

        def _factory(registered: object) -> LocalWorker:
            wid = getattr(registered, "worker_id", "")
            chosen_worker_id.append(wid)
            caps: WorkerCapabilities = registered.capabilities  # type: ignore[attr-defined]
            return LocalWorker(
                worker_type=WorkerType.ASR,
                handler=_echo,
                supported_languages_in=caps.supported_languages_in,
                supported_languages_out=caps.supported_languages_out,
            )

        await reg.register(
            "asr-bad",
            "http://127.0.0.1:1",
            "http",
            WorkerCapabilities(
                worker_type=WorkerType.ASR,
                supported_languages_in=frozenset({"en"}),
                supported_languages_out=frozenset(),
                supported_formats_in=frozenset({"wav"}),
                supported_formats_out=frozenset({"text"}),
                max_payload_bytes=None,
                batch_capable=False,
                model_source=None,
            ),
        )
        await reg.register(
            "asr-good",
            "http://127.0.0.1:2",
            "http",
            WorkerCapabilities(
                worker_type=WorkerType.ASR,
                supported_languages_in=frozenset({"en"}),
                supported_languages_out=frozenset({"en"}),
                supported_formats_in=frozenset({"wav"}),
                supported_formats_out=frozenset({"text"}),
                max_payload_bytes=None,
                batch_capable=False,
                model_source=None,
            ),
        )

        handler = create_step_handler(reg, worker_factory=_factory, data_dir=_TEST_DATA_DIR)
        plan = Plan(
            plan_id="p",
            job_id="j",
            source_type="audio",
            source_language="en",
            target_language="en",
            executor_strategy=ExecutorStrategy.STREAMING,
            steps=(
                PlanStep(
                    step_id="transcribe",
                    type=WorkerType.ASR,
                    depends_on=(),
                    status=StepStatus.PENDING,
                    payload={},
                ),
            ),
        )
        await handler(plan.steps[0], plan)
        assert chosen_worker_id == ["asr-good"]

    @pytest.mark.asyncio
    async def test_list_all_refreshes_status_per_step(self) -> None:
        """registry.list_all() refreshes worker status before each dispatch."""
        call_count = 0

        class CountingStore(InMemoryWorkerStore):
            async def list_all(self) -> tuple[RegisteredWorker, ...]:
                nonlocal call_count
                call_count += 1
                return await super().list_all()

        reg = CountingStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", _tts_caps())
        local_worker = LocalWorker(
            worker_type=WorkerType.TTS,
            handler=_echo_job_result,
            supported_languages_in=frozenset({"es"}),
            supported_languages_out=frozenset({"es"}),
        )
        handler = create_step_handler(reg, worker_factory=lambda _reg: local_worker, data_dir=_TEST_DATA_DIR)
        plan = Plan(
            plan_id="plan-1",
            job_id="job-1",
            source_type="epub",
            source_language="en",
            target_language="es",
            executor_strategy=ExecutorStrategy.STREAMING,
            steps=(
                PlanStep(
                    step_id="s1",
                    type=WorkerType.TTS,
                    depends_on=(),
                    status=StepStatus.PENDING,
                    payload={"target_language": "es", "chapter_id": "ch1"},
                    selected_worker_id="tts-1",
                ),
                PlanStep(
                    step_id="s2",
                    type=WorkerType.TTS,
                    depends_on=("s1",),
                    status=StepStatus.PENDING,
                    payload={"target_language": "es", "chapter_id": "ch2"},
                    selected_worker_id="tts-1",
                ),
            ),
        )
        await handler(plan.steps[0], plan)
        await handler(plan.steps[1], plan)
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_worker_factory_reuses_instances_across_jobs_until_generation_changes(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", _tts_caps())

        class ClosableLocalWorker(LocalWorker):
            def __init__(self) -> None:
                super().__init__(
                    worker_type=WorkerType.TTS,
                    handler=_echo_job_result,
                    supported_languages_in=frozenset({"es"}),
                    supported_languages_out=frozenset({"es"}),
                )
                self.close_calls = 0

            async def close(self) -> None:
                self.close_calls += 1

        created: list[ClosableLocalWorker] = []

        def _factory(_registered: RegisteredWorker) -> ClosableLocalWorker:
            worker = ClosableLocalWorker()
            created.append(worker)
            return worker

        handler = CachingStepHandler(reg, worker_factory=_factory, data_dir=_TEST_DATA_DIR)
        plan_a = replace(_make_plan(), job_id="job-a", plan_id="plan-a")
        plan_b = replace(_make_plan(), job_id="job-b", plan_id="plan-b")
        await handler(plan_a.steps[0], plan_a)
        await handler(plan_b.steps[0], plan_b)
        assert len(created) == 1

        await reg.register("tts-1", "http://127.0.0.2:1", "http", _tts_caps())
        plan_c = replace(_make_plan(), job_id="job-c", plan_id="plan-c")
        await handler(plan_c.steps[0], plan_c)
        assert len(created) == 2
        await handler.release_job(plan_a.job_id)
        await handler.release_job(plan_b.job_id)
        assert created[0].close_calls == 1

    @pytest.mark.asyncio
    async def test_worker_factory_called_once_per_worker_id(self) -> None:
        """worker_factory is called once per worker_id across multiple steps."""
        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", _tts_caps())
        factory_calls: list[str] = []
        worker = LocalWorker(
            worker_type=WorkerType.TTS,
            handler=_echo_job_result,
            supported_languages_in=frozenset({"es"}),
            supported_languages_out=frozenset({"es"}),
        )

        def _factory(registered: object) -> LocalWorker:
            factory_calls.append(getattr(registered, "worker_id", ""))
            return worker

        handler = create_step_handler(reg, worker_factory=_factory, data_dir=_TEST_DATA_DIR)
        plan = Plan(
            plan_id="plan-1",
            job_id="job-1",
            source_type="epub",
            source_language="en",
            target_language="es",
            executor_strategy=ExecutorStrategy.STREAMING,
            steps=(
                PlanStep(
                    step_id="s1",
                    type=WorkerType.TTS,
                    depends_on=(),
                    status=StepStatus.PENDING,
                    payload={"target_language": "es", "chapter_id": "ch1"},
                    selected_worker_id="tts-1",
                ),
                PlanStep(
                    step_id="s2",
                    type=WorkerType.TTS,
                    depends_on=("s1",),
                    status=StepStatus.PENDING,
                    payload={"target_language": "es", "chapter_id": "ch2"},
                    selected_worker_id="tts-1",
                ),
            ),
        )
        await handler(plan.steps[0], plan)
        await handler(plan.steps[1], plan)
        assert factory_calls == ["tts-1"]

    @pytest.mark.asyncio
    async def test_invalidate_worker_cache_drops_instance_pool(self) -> None:
        """CORR-009: invalidating the cache clears both the worker list snapshot and the pool."""

        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", _tts_caps())
        factory_calls: list[RegisteredWorker] = []

        def _factory(registered: RegisteredWorker) -> LocalWorker:
            factory_calls.append(registered)
            return LocalWorker(
                worker_type=WorkerType.TTS,
                handler=_echo_job_result,
                supported_languages_in=frozenset({"es"}),
                supported_languages_out=frozenset({"es"}),
            )

        handler = CachingStepHandler(reg, worker_factory=_factory, data_dir=_TEST_DATA_DIR)
        plan = _make_plan()
        step = plan.steps[0]
        await handler(step, plan)
        assert len(factory_calls) == 1

        # CORR-009 behavior: after invalidation, the next call must re-fetch
        # the worker list (factory invoked again, fresh instance pool built).
        await handler._invalidate_worker_cache()  # noqa: SLF001
        await handler(step, plan)
        assert len(factory_calls) == 2
        assert "tts-1" in handler._worker_instances  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_invalidation_defers_close_until_job_release(self) -> None:
        """Active jobs keep retired worker clients alive during cache invalidation."""

        class ClosableLocalWorker(LocalWorker):
            def __init__(self) -> None:
                super().__init__(WorkerType.TTS, _echo_job_result)
                self.close_calls = 0

            async def close(self) -> None:
                self.close_calls += 1

        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", _tts_caps())
        workers: list[ClosableLocalWorker] = []

        def _factory(_registered: RegisteredWorker) -> ClosableLocalWorker:
            worker = ClosableLocalWorker()
            workers.append(worker)
            return worker

        handler = CachingStepHandler(reg, worker_factory=_factory, data_dir=_TEST_DATA_DIR)
        plan = _make_plan()
        await handler(plan.steps[0], plan)
        await handler._invalidate_worker_cache()  # noqa: SLF001

        assert workers[0].close_calls == 0
        await handler.release_job(plan.job_id)
        assert workers[0].close_calls == 1

    @pytest.mark.asyncio
    async def test_failed_retired_worker_close_is_retried(self) -> None:
        class FlakyLocalWorker(LocalWorker):
            def __init__(self) -> None:
                super().__init__(WorkerType.TTS, _echo_job_result)
                self.close_calls = 0

            async def close(self) -> None:
                self.close_calls += 1
                if self.close_calls == 1:
                    raise RuntimeError("close failed")

        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", _tts_caps())
        worker = FlakyLocalWorker()
        handler = CachingStepHandler(reg, worker_factory=lambda _registered: worker, data_dir=_TEST_DATA_DIR)
        plan = _make_plan()
        await handler(plan.steps[0], plan)

        await handler._invalidate_worker_cache()  # noqa: SLF001
        assert id(worker) in handler._retired_worker_instances  # noqa: SLF001
        await handler.release_job(plan.job_id)
        assert worker.close_calls == 1

        await handler._close_retired_workers()  # noqa: SLF001
        assert id(worker) not in handler._retired_worker_instances  # noqa: SLF001
        assert worker.close_calls == 2

    @pytest.mark.asyncio
    async def test_shared_retired_worker_closes_after_last_job_release(self) -> None:
        class ClosableLocalWorker(LocalWorker):
            def __init__(self) -> None:
                super().__init__(WorkerType.TTS, _echo_job_result)
                self.close_calls = 0

            async def close(self) -> None:
                self.close_calls += 1

        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", _tts_caps())
        workers: list[ClosableLocalWorker] = []

        def _factory(_registered: RegisteredWorker) -> ClosableLocalWorker:
            worker = ClosableLocalWorker()
            workers.append(worker)
            return worker

        handler = CachingStepHandler(reg, worker_factory=_factory, data_dir=_TEST_DATA_DIR)
        plan_a = replace(_make_plan(), job_id="job-a", plan_id="plan-a")
        plan_b = replace(_make_plan(), job_id="job-b", plan_id="plan-b")
        await handler(plan_a.steps[0], plan_a)
        await handler(plan_b.steps[0], plan_b)
        await handler._invalidate_worker_cache()  # noqa: SLF001

        await handler.release_job("job-a")
        assert workers[0].close_calls == 0
        await handler.release_job("job-b")
        assert workers[0].close_calls == 1


class TestHttpWorkerStepCache:
    @pytest.mark.asyncio
    async def test_custom_http_worker_factory_receives_configured_cache(self, tmp_path: Path) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("tts-x", "http://worker:8000", "http", _tts_caps())
        cache = StepCache(tmp_path)
        worker = HttpWorker("http://worker:8000", data_dir=tmp_path)
        worker.execute = _echo_job_result  # type: ignore[method-assign]
        handler = CachingStepHandler(
            reg,
            worker_factory=lambda _registered: worker,
            data_dir=tmp_path,
        )
        handler.configure_step_cache(cache)

        plan = _make_plan()
        step = replace(plan.steps[0], selected_worker_id="tts-x")
        await handler(step, plan)

        assert worker._step_cache is cache  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_default_factory_worker_reads_shared_upstream_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registered = RegisteredWorker(
            worker_id="tts-x",
            endpoint="http://worker:8000",
            transport="http",
            capabilities=_tts_caps(),
        )
        cache = StepCache(tmp_path)
        chunks = b"shared chunks"
        chunks_path = tmp_path / "chunks.json"
        chunks_path.write_bytes(chunks)
        await cache.save_outputs(
            "job-1",
            "chunk",
            (
                OutputFile(
                    path=str(chunks_path),
                    filename=chunks_path.name,
                    size_bytes=len(chunks),
                    checksum="x" * 64,
                    content_type="application/json",
                ),
            ),
        )
        worker = default_worker_factory(registered, data_dir=tmp_path, step_cache=cache)
        assert isinstance(worker, HttpWorker)
        seen: dict[str, bytes] = {}

        async def _request(_method: str, _path: str, **kwargs: Any) -> httpx.Response:
            content = kwargs["content"]
            seen["body"] = b"".join([part async for part in content])
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=b'{"job_id":"job-1-synthesize","status":"success","outputs":[],"metrics":{"duration_seconds":0.0},"error":null}',
            )

        monkeypatch.setattr(worker, "_request", _request)
        result = await worker.execute(
            Job(job_id="job-1-synthesize", job_type=WorkerType.TTS, payload={}, chapter_id="ch1")
        )
        assert result.status is JobStatus.SUCCESS
        assert chunks in seen["body"]

    """ARCH-027: the factory passes one orchestrator-owned ``StepCache``."""

    def test_default_worker_factory_passes_shared_step_cache(self, tmp_path: Path) -> None:
        from acheron.shell.registry import RegisteredWorker

        reg = RegisteredWorker(
            worker_id="tts-x",
            endpoint="http://worker:8000",
            transport="http",
            capabilities=_tts_caps(),
        )
        cache = StepCache(tmp_path)
        worker = default_worker_factory(reg, data_dir=tmp_path, step_cache=cache)
        assert isinstance(worker, HttpWorker)
        assert worker._step_cache is cache  # noqa: SLF001

    def test_factory_reads_registration_token_at_worker_creation(self) -> None:
        from acheron.shell.registry import RegisteredWorker

        reg = RegisteredWorker(
            worker_id="tts-token",
            endpoint="http://worker:8000",
            transport="http",
            capabilities=_tts_caps(),
        )
        token: str | None = None
        worker_before = default_worker_factory(
            reg,
            data_dir=_TEST_DATA_DIR,
            registration_token_provider=lambda: token,
        )
        assert isinstance(worker_before, HttpWorker)
        assert worker_before._registration_token is None  # noqa: SLF001
        token = "generated-after-start"
        worker_after = default_worker_factory(
            reg,
            data_dir=_TEST_DATA_DIR,
            registration_token_provider=lambda: token,
        )
        assert isinstance(worker_after, HttpWorker)
        assert worker_after._registration_token == token  # noqa: SLF001

    def test_factory_scopes_insecure_http_to_explicit_worker_ids(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ACHERON_INSECURE_HTTP_WORKER_IDS", "tts-local-stub, asr-local-stub")
        allowed = RegisteredWorker(
            worker_id="tts-local-stub",
            endpoint="http://tts-local-stub:8001",
            transport="http",
            capabilities=_tts_caps(),
        )
        remote = RegisteredWorker(
            worker_id="remote-tts",
            endpoint="http://remote.example:8000",
            transport="http",
            capabilities=_tts_caps(),
        )

        allowed_worker = default_worker_factory(allowed, data_dir=tmp_path, registration_token="secret")
        remote_worker = default_worker_factory(remote, data_dir=tmp_path, registration_token="secret")

        assert isinstance(allowed_worker, HttpWorker)
        assert isinstance(remote_worker, HttpWorker)
        assert allowed_worker._allow_insecure is True  # noqa: SLF001
        assert remote_worker._allow_insecure is False  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_create_step_handler_default_lambda_passes_shared_step_cache(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The default factory receives the orchestrator-owned step cache."""
        import acheron.shell.step_handler as sh

        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", _tts_caps())
        cache = StepCache(tmp_path)
        handler = create_step_handler(reg, data_dir=tmp_path, step_cache=cache)
        original_default = default_worker_factory
        captured: dict = {}

        def _capturing_default(  # noqa: PLR0913
            registered: RegisteredWorker,
            local_handlers: dict[str, LocalJobHandler] | None = None,
            *,
            data_dir: Path | str = _TEST_DATA_DIR,
            registration_token: str | None = None,
            registration_token_provider: Callable[[], str | None] | None = None,
            step_cache: StepCache | None = None,
        ) -> object:
            worker = original_default(
                registered,
                local_handlers,
                data_dir=data_dir,
                registration_token=registration_token,
                registration_token_provider=registration_token_provider,
                step_cache=step_cache,
            )
            captured["worker"] = worker
            worker.execute = _echo_job_result  # type: ignore[method-assign]
            return worker

        monkeypatch.setattr(sh, "default_worker_factory", _capturing_default)
        await handler(_make_plan().steps[0], _make_plan())
        assert isinstance(captured["worker"], HttpWorker)
        assert captured["worker"]._step_cache is cache  # noqa: SLF001
        assert captured["worker"]._step_cache is not None  # noqa: SLF001
        assert sh.default_worker_factory is _capturing_default
        # monkeypatch restores the module-level original on test teardown.
        assert captured["worker"]._step_cache.data_dir == tmp_path.resolve()  # noqa: SLF001
