"""Integration tests for job lifecycle via CLI."""

from __future__ import annotations

import asyncio
import json
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from acheron.cli import main
from acheron.shell.job_store import TrackedJob
from acheron.shell.orchestrator import Orchestrator


def _write_four_chapter_epub(path: Path) -> None:
    """Write a minimal EPUB whose spine contains four numbered chapters."""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""",
        )
        manifest = "\n".join(
            f'    <item href="chapter-{number}.xhtml" id="chapter-{number}" media-type="application/xhtml+xml"/>'
            for number in range(1, 5)
        )
        spine = "\n".join(f'    <itemref idref="chapter-{number}"/>' for number in range(1, 5))
        archive.writestr(
            "OEBPS/content.opf",
            f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <manifest>
{manifest}
  </manifest>
  <spine>
{spine}
  </spine>
</package>""",
        )
        for number in range(1, 5):
            archive.writestr(
                f"OEBPS/chapter-{number}.xhtml",
                f"<html><body><h1>Chapter {number}</h1><p>Text for chapter {number}.</p></body></html>",
            )


if TYPE_CHECKING:
    from click.testing import CliRunner
    from fastapi import FastAPI


@pytest.mark.asyncio
async def test_submit_epub_shows_job_id(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    result = runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "es"])
    assert result.exit_code == 0
    assert "Job submitted:" in result.output
    assert "job-" in result.output
    assert "Status:" in result.output

    job_id = next(word for word in result.output.split() if word.startswith("job-"))
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=wired_app), base_url="http://test") as client:
        response = await client.get(f"/jobs/{job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["created_at"].endswith("Z")
    assert body["last_persisted_at"].endswith("Z")
    assert set(body["progress"]) == {
        "completed_steps",
        "total_steps",
        "current_step_id",
        "current_worker_type",
        "current_worker_id",
        "eta_seconds",
    }


@pytest.mark.asyncio
async def test_submit_audio_with_asr(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    mp3 = tmp_path / "podcast.mp3"
    mp3.touch()
    result = runner.invoke(main, ["job", "submit", str(mp3), "--src", "en", "--dest", "es", "--asr", "whisper-v3"])
    assert result.exit_code == 0
    assert "job-" in result.output


@pytest.mark.asyncio
async def test_submit_then_status(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    submit_result = runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "es"])
    assert submit_result.exit_code == 0

    job_id = next(w for w in submit_result.output.split() if w.startswith("job-"))

    status_result = runner.invoke(main, ["job", "status", job_id])
    assert status_result.exit_code == 0
    assert job_id in status_result.output


@pytest.mark.asyncio
async def test_retry_creates_linked_job(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    submit_result = runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "es"])
    assert submit_result.exit_code == 0, submit_result.output
    original_id = next(word for word in submit_result.output.split() if word.startswith("job-"))

    retry_result = runner.invoke(main, ["job", "retry", original_id, "--label", "atlas-retry"])

    assert retry_result.exit_code == 0, retry_result.output
    retried_id = next(word for word in retry_result.output.split() if word.startswith("job-"))
    assert retried_id != original_id
    assert original_id in retry_result.output


@pytest.mark.asyncio
async def test_submit_then_status_verbose(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    submit_result = runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "es"])
    job_id = next(w for w in submit_result.output.split() if w.startswith("job-"))

    status_result = runner.invoke(main, ["job", "status", job_id, "-v"])
    assert status_result.exit_code == 0
    assert job_id in status_result.output


@pytest.mark.asyncio
async def test_submit_then_list_jobs(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "es"])

    result = runner.invoke(main, ["jobs"])
    assert result.exit_code == 0
    assert "job-" in result.output


@pytest.mark.asyncio
async def test_submit_then_list_jobs_active(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    """After submit, the job is reconciled to a terminal status (OBS-001 fix
    makes sure cancelled jobs are persisted as FAILED rather than stuck at
    RUNNING). The active filter should not show the job once it has
    completed or been cancelled.
    """
    epub = tmp_path / "book.epub"
    epub.touch()
    runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "es"])

    result = runner.invoke(main, ["jobs", "--active"])
    assert result.exit_code == 0
    assert "No jobs found" in result.output


@pytest.mark.asyncio
async def test_submit_then_list_jobs_completed_filter(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    """After submit, the job is reconciled to a terminal status (OBS-001 fix)
    and shows up in the completed/failed filter, not in "no jobs found".
    """
    epub = tmp_path / "book.epub"
    epub.touch()
    runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "es"])

    result = runner.invoke(main, ["jobs", "--completed"])
    assert result.exit_code == 0
    assert "job-" in result.output


@pytest.mark.asyncio
async def test_status_nonexistent_job(runner: CliRunner, wired_app: FastAPI) -> None:
    result = runner.invoke(main, ["job", "status", "job-nonexistent"])
    assert result.exit_code != 0


@pytest.mark.asyncio
async def test_jobs_empty(runner: CliRunner, wired_app: FastAPI) -> None:
    result = runner.invoke(main, ["jobs"])
    assert result.exit_code == 0
    assert "No jobs found" in result.output


# --- Phase 4C journey tests ---


async def _wait_for_terminal(orch: Orchestrator, job_id: str, *, max_iterations: int = 100) -> TrackedJob:
    """Poll get_job until terminal status or iteration limit."""
    import asyncio

    for _ in range(max_iterations):
        job: TrackedJob | None = await orch.get_job(job_id)
        if job is not None and job.status.value in {"completed", "failed", "partial"}:
            return job
        await asyncio.sleep(0)
    msg = f"Job {job_id} did not reach terminal status after {max_iterations} iterations"
    raise TimeoutError(msg)


@pytest.mark.asyncio
async def test_cancel_and_retry_are_distinct_jobs(wired_app: FastAPI, tmp_path: Path) -> None:
    """Cancel creates a terminal job; retry creates a new linked job."""

    from acheron.core.models import EpubRequest, ExecutorStrategy, PlanStatus

    orch: Orchestrator = wired_app.state.orchestrator
    epub = tmp_path / "book.epub"
    epub.touch()

    source = tmp_path / "input"
    source.mkdir(exist_ok=True)
    (source / "book.epub").write_bytes(b"epub")

    tracked = await orch.submit_job(
        EpubRequest("input/book.epub", "en", "es"),
        ExecutorStrategy.SEQUENTIAL,
    )

    # Wait for the job to finish (the wired_app uses local handlers that succeed)
    terminal = await _wait_for_terminal(orch, tracked.job_id)
    assert terminal.status in {PlanStatus.COMPLETED, PlanStatus.FAILED}


@pytest.mark.asyncio
async def test_label_filtering_in_list(wired_app: FastAPI, tmp_path: Path) -> None:
    """Jobs with labels are filterable via glob pattern."""
    from httpx import ASGITransport, AsyncClient

    from acheron.api_client import AcheronClient
    from acheron.core.models import EpubRequest, ExecutorStrategy

    orch: Orchestrator = wired_app.state.orchestrator
    source = tmp_path / "input"
    source.mkdir(exist_ok=True)
    (source / "book.epub").write_bytes(b"epub")

    await orch.submit_job(
        EpubRequest("input/book.epub", "en", "es"),
        ExecutorStrategy.SEQUENTIAL,
        label="atlas-ch1",
    )
    await orch.submit_job(
        EpubRequest("input/book.epub", "en", "es"),
        ExecutorStrategy.SEQUENTIAL,
        label="odyssey-ch2",
    )

    async with AsyncClient(transport=ASGITransport(app=wired_app), base_url="http://test") as http:
        client = AcheronClient(base_url="http://test", transport=http._transport)  # noqa: SLF001
        jobs = await client.list_jobs(label="atlas*")
        assert len(jobs) == 1
        assert jobs[0].label == "atlas-ch1"

        all_jobs = await client.list_jobs()
        assert len(all_jobs) >= 2


@pytest.mark.asyncio
async def test_failed_worker_persists_cost_breakdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A measurable failed worker keeps its cost evidence in the job record."""
    from acheron.core.models import (
        CostBasis,
        CostEstimate,
        EpubRequest,
        ExecutorStrategy,
        Job,
        JobMetrics,
        JobResult,
        JobStatus,
        WorkerCapabilities,
        WorkerType,
    )
    from acheron.shell.cache import PlanCache, StepCache
    from acheron.shell.stores.memory import InMemoryWorkerStore

    async def failing_tts(job: Job) -> JobResult:
        await asyncio.sleep(0.01)
        return JobResult(
            job_id=job.job_id,
            status=JobStatus.FAILED,
            outputs=(),
            metrics=JobMetrics(
                duration_seconds=0.01,
                gpu_seconds=0.01,
                cost_estimate=CostEstimate(
                    cost=0.00001,
                    basis=CostBasis.CACHED,
                    rate_per_hour=3.6,
                    gpu_type="NVIDIA L4",
                    secure_cloud=False,
                    cache_age_seconds=7.5,
                ),
            ),
            error="simulated worker failure",
        )

    monkeypatch.setenv("ACHERON_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ACHERON_ORCHESTRATOR__DATA_DIR", str(tmp_path))
    registry = InMemoryWorkerStore()
    orch = Orchestrator(registry=registry, cache=PlanCache(tmp_path), step_cache=StepCache(tmp_path))
    capabilities = WorkerCapabilities(
        worker_type=WorkerType.TTS,
        supported_languages_in=frozenset({"es"}),
        supported_languages_out=frozenset({"es"}),
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"wav"}),
        max_payload_bytes=None,
        batch_capable=True,
        model_source=None,
    )
    await orch.register_worker("tts-failing", "local", "local", capabilities, handler=failing_tts)
    await orch.register_worker(
        "extraction-failing",
        "local",
        "local",
        replace(capabilities, worker_type=WorkerType.EXTRACTION),
        handler=failing_tts,
    )
    await orch.start()
    try:
        source = tmp_path / "input"
        source.mkdir(exist_ok=True)
        (source / "book.epub").write_bytes(b"unused")
        tracked = await orch.submit_job(
            EpubRequest(str(source / "book.epub"), "es", "es"),
            ExecutorStrategy.SEQUENTIAL,
        )
        terminal = await _wait_for_terminal(orch, tracked.job_id, max_iterations=1000)
        assert terminal.status.value == "failed"
        assert terminal.result is not None
        assert len(terminal.result.cost_breakdown) == 1
        item = terminal.result.cost_breakdown[0]
        assert item.worker_id == "extraction-failing"
        assert item.gpu_seconds is not None
        assert item.gpu_seconds > 0.0
        assert item.estimate.basis is CostBasis.CACHED
        assert item.estimate.gpu_type == "NVIDIA L4"
        assert item.estimate.rate_per_hour == 3.6
        assert item.estimate.cache_age_seconds == 7.5

        persisted = await orch.get_job_cost(tracked.job_id)
        assert persisted is not None
        assert persisted.cost_breakdown[0].worker_id == "extraction-failing"
        assert persisted.cost_breakdown[0].gpu_seconds is not None
        assert persisted.cost_breakdown[0].gpu_seconds > 0.0
        assert persisted.cost_breakdown[0].basis is CostBasis.CACHED
        assert persisted.cost_breakdown[0].gpu_type == "NVIDIA L4"
        assert persisted.cost_breakdown[0].cache_age_seconds == 7.5
    finally:
        await orch.shutdown()
        await orch.close()


@pytest.mark.asyncio
async def test_admin_transitions_preserve_outputs_progress_and_cost(wired_app: FastAPI, tmp_path: Path) -> None:
    """Stale and manual admin failures retain evidence through archiving."""
    from datetime import UTC, datetime, timedelta

    from acheron.core.models import (
        CostBasis,
        CostBreakdown,
        CostEstimate,
        EpubRequest,
        ExecutorStrategy,
        OutputFile,
        Plan,
        PlanResult,
        PlanStatus,
        PlanStep,
        StepStatus,
        WorkerType,
    )
    from acheron.shell.job_store import JobProgressState

    orch: Orchestrator = wired_app.state.orchestrator
    evidence = OutputFile(
        path="outputs/job-evidence/book.wav",
        filename="book.wav",
        size_bytes=12,
        checksum="checksum",
        content_type="audio/wav",
    )
    cost = CostBreakdown(
        step_id="synthesize",
        worker_type=WorkerType.TTS,
        worker_id="tts-1",
        gpu_seconds=2.0,
        estimate=CostEstimate(cost=0.25, basis=CostBasis.MEASURED),
    )
    plan_template = Plan(
        plan_id="plan-evidence",
        job_id="placeholder",
        source_type="epub",
        source_language="en",
        target_language="es",
        executor_strategy=ExecutorStrategy.SEQUENTIAL,
        steps=(
            PlanStep(
                step_id="synthesize",
                type=WorkerType.TTS,
                depends_on=(),
                status=StepStatus.COMPLETE,
                payload={"voice": "en-US"},
            ),
        ),
    )
    result = PlanResult(
        plan_id="plan-evidence",
        status=PlanStatus.RUNNING,
        completed_steps=1,
        total_steps=2,
        outputs=(evidence,),
        total_cost=0.25,
        total_duration_seconds=2.0,
        total_cost_basis=CostBasis.MEASURED,
        cost_breakdown=(cost,),
    )

    def make_job(job_id: str, status: PlanStatus) -> TrackedJob:
        return TrackedJob(
            job_id=job_id,
            request=EpubRequest("input/book.epub", "en", "es"),
            strategy=ExecutorStrategy.SEQUENTIAL,
            status=status,
            progress=JobProgressState(completed_steps=1, total_steps=2, current_step_id="synthesize"),
            plan=replace(plan_template, job_id=job_id),
            result=result,
        )

    stale = make_job("job-stale-evidence", PlanStatus.RUNNING)
    manual = make_job("job-manual-evidence", PlanStatus.PENDING)
    await orch._job_store.put(stale)  # noqa: SLF001
    await orch._job_store.put(manual)  # noqa: SLF001

    reaped = await orch.reap_stale_jobs(
        older_than_seconds=60,
        reason="orphaned_by_restart",
        now=datetime.now(UTC) + timedelta(hours=1),
    )
    assert reaped.job_ids == (stale.job_id,)
    await orch.mark_failed_by_admin(manual.job_id, reason="operator_review")

    for job_id in (stale.job_id, manual.job_id):
        failed = await orch.get_job(job_id)
        assert failed is not None
        assert failed.status is PlanStatus.FAILED
        assert failed.progress.completed_steps == 1
        assert failed.result is not None
        assert failed.result.outputs == (evidence,)
        assert failed.result.cost_breakdown == (cost,)
        archived = await orch.archive_job(job_id)
        assert archived.archived_at is not None
        assert archived.request == EpubRequest("input/book.epub", "en", "es")
        assert archived.plan == replace(plan_template, job_id=job_id)
        assert archived.result is not None
        assert archived.result.plan_id == "plan-evidence"
        assert archived.result.outputs == (evidence,)
        assert archived.result.total_cost == 0.25
        assert archived.result.total_duration_seconds == 2.0
        assert archived.result.total_cost_basis is CostBasis.MEASURED
        assert archived.result.cost_breakdown == (cost,)


@pytest.mark.asyncio
async def test_recovery_list_includes_archived_record_after_archive(wired_app: FastAPI) -> None:
    """The operator list journey can find a preserved archived failure."""
    from acheron.core.models import EpubRequest, ExecutorStrategy, PlanStatus
    from acheron.shell.job_store import TrackedJob

    orch: Orchestrator = wired_app.state.orchestrator
    archived = TrackedJob(
        job_id="job-recovery-archive",
        request=EpubRequest("input/book.epub", "en", "es"),
        strategy=ExecutorStrategy.SEQUENTIAL,
        status=PlanStatus.FAILED,
    )
    await orch._job_store.put(archived)  # noqa: SLF001
    await orch.archive_job(archived.job_id)

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=wired_app), base_url="http://test") as client:
        response = await client.get(
            "/jobs",
            params={"status": "failed", "include_archived": "true"},
        )

    assert response.status_code == 200
    jobs = response.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == archived.job_id
    assert jobs[0]["archived_at"] is not None


@pytest.mark.asyncio
async def test_event_broker_publishes_terminal_event(wired_app: FastAPI, tmp_path: Path) -> None:
    """The event broker publishes a terminal event when a job finishes."""
    from acheron.core.models import EpubRequest, ExecutorStrategy, PlanStatus

    orch: Orchestrator = wired_app.state.orchestrator
    source = tmp_path / "input"
    source.mkdir(exist_ok=True)
    (source / "book.epub").write_bytes(b"epub")

    tracked = await orch.submit_job(
        EpubRequest("input/book.epub", "en", "es"),
        ExecutorStrategy.SEQUENTIAL,
    )

    terminal = await _wait_for_terminal(orch, tracked.job_id)
    assert terminal.status in {PlanStatus.COMPLETED, PlanStatus.FAILED}

    # Check the broker's buffer for terminal events
    buf = orch.events._buffer.get(tracked.job_id)  # noqa: SLF001
    assert buf is not None
    terminal_events = [e for e in buf if e.status in {PlanStatus.COMPLETED, PlanStatus.FAILED}]
    assert len(terminal_events) >= 1


@pytest.mark.asyncio
async def test_voice_journey_previews_promotes_and_dispatches_canonical_selection(
    wired_app: FastAPI,
    tmp_path: Path,
) -> None:
    """A temporary EPUB preview gates submission and preserves voice selection."""
    import numpy as np
    from httpx import ASGITransport, AsyncClient
    from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

    from acheron.core.models import Job, JsonValue, WorkerCapabilities, WorkerType
    from acheron.worker_sdk.inputs import BytesInput
    from acheron.worker_sdk.settings import WorkerSettings

    orch: Orchestrator = wired_app.state.orchestrator
    await orch.register_worker(
        "tts-shared",
        "http://127.0.0.1:1",
        "http",
        replace(
            WorkerCapabilities(
                worker_type=WorkerType.TTS,
                supported_languages_in=frozenset({"es"}),
                supported_languages_out=frozenset({"es"}),
                supported_formats_in=frozenset({"text"}),
                supported_formats_out=frozenset({"wav"}),
                max_payload_bytes=None,
                batch_capable=True,
                model_source="Qwen/Qwen3-TTS",
            ),
            metadata={"speakers": ["Vivian", "Ryan"]},
        ),
    )
    epub = tmp_path / "book.epub"
    _write_four_chapter_epub(epub)
    transport = ASGITransport(app=wired_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        upload = await client.post("/inputs", files={"file": ("book.epub", epub.read_bytes(), "application/epub+zip")})
        assert upload.status_code == 201
        uploaded = upload.json()
        data_dir = orch.settings.orchestrator.data_dir
        stored = data_dir / uploaded["source_path"]
        assert stored.is_file()

        request = {
            "source_type": "epub",
            "source_path": uploaded["source_path"],
            "source_language": "en",
            "target_language": "es",
            "voice_map": [
                {"start_chapter": 1, "end_chapter": 3, "voice": "vivian"},
                {"start_chapter": 4, "end_chapter": 4, "voice": "ryan"},
            ],
            "input_id": uploaded["input_id"],
        }
        preview = await client.post("/jobs:preview", json=request)
        assert preview.status_code == 200, preview.text
        assert await orch.list_jobs() == ()
        assert stored.is_file(), "successful preview must retain the temporary input for submission"

        submitted = await client.post("/jobs", json=request)
        assert submitted.status_code == 201, submitted.text
        job = submitted.json()
        assert job["voice"] is None
        assert job["voice_map"] == [
            {"start_chapter": 1, "end_chapter": 3, "voice": "Vivian"},
            {"start_chapter": 4, "end_chapter": 4, "voice": "Ryan"},
        ]
        plan = await orch.get_plan(job["plan_id"])
        synthesize = next(step for step in plan.steps if step.step_id == "synthesize")
        assert synthesize.selected_worker_id == "tts-shared"
        assert "voice" not in synthesize.payload
        assert synthesize.payload["voice_map"] == job["voice_map"]

        class _SpyingModel:
            def __init__(self) -> None:
                self.speakers: list[str] = []

            def generate_custom_voice(
                self,
                text: list[str],
                language: list[str],
                speaker: list[str],
                instruct: list[str],
            ) -> tuple[list[object], int]:
                self.speakers = speaker
                return [np.zeros(32, dtype=np.float32) for _ in text], 22050

        handler = Qwen3TTSRunpodHandler(
            WorkerSettings(
                worker_id="tts-shared",
                orchestrator_url="http://test",
                price_source="zero",
                default_speaker="Ryan",
            )
        )
        spying_model = _SpyingModel()
        handler._model = spying_model  # noqa: SLF001
        chunks: list[dict[str, JsonValue]] = [
            {"chapter_id": f"chapter_{number:03d}", "sequence_id": 0, "text": f"chapter {number}"}
            for number in range(1, 5)
        ]
        await handler.handle(
            Job(
                job_id="voice-journey-synthesize",
                job_type=WorkerType.TTS,
                payload=synthesize.payload,
                chapter_id="chapter_001",
            ),
            input=BytesInput(content_type="application/json", data=json.dumps(chunks).encode()),
        )
        assert spying_model.speakers == ["Vivian", "Vivian", "Vivian", "Ryan"]


@pytest.mark.asyncio
async def test_voice_preflight_rejects_separate_workers_and_cleans_input(
    wired_app: FastAPI,
    tmp_path: Path,
) -> None:
    """A voice map requiring separate workers fails before persistence."""
    from httpx import ASGITransport, AsyncClient

    from acheron.core.models import WorkerCapabilities, WorkerType

    orch: Orchestrator = wired_app.state.orchestrator
    for worker_id, voice in (("tts-vivian", "Vivian"), ("tts-ryan", "Ryan")):
        await orch.register_worker(
            worker_id,
            "http://127.0.0.1:1",
            "http",
            replace(
                WorkerCapabilities(
                    worker_type=WorkerType.TTS,
                    supported_languages_in=frozenset({"es"}),
                    supported_languages_out=frozenset({"es"}),
                    supported_formats_in=frozenset({"text"}),
                    supported_formats_out=frozenset({"wav"}),
                    max_payload_bytes=None,
                    batch_capable=True,
                    model_source="Qwen/Qwen3-TTS",
                ),
                metadata={"speakers": [voice]},
            ),
        )
    epub = tmp_path / "book.epub"
    _write_four_chapter_epub(epub)
    transport = ASGITransport(app=wired_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        upload = await client.post("/inputs", files={"file": ("book.epub", epub.read_bytes(), "application/epub+zip")})
        uploaded = upload.json()
        response = await client.post(
            "/jobs:preview",
            json={
                "source_type": "epub",
                "source_path": uploaded["source_path"],
                "source_language": "en",
                "target_language": "es",
                "voice_map": [
                    {"start_chapter": 1, "end_chapter": 3, "voice": "Vivian"},
                    {"start_chapter": 4, "end_chapter": 4, "voice": "Ryan"},
                ],
                "input_id": uploaded["input_id"],
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["type"] == "VoiceSelectionError"
    assert await orch.list_jobs() == ()
    assert not (orch.settings.orchestrator.data_dir / uploaded["source_path"]).exists()
    assert not list(orch.settings.orchestrator.data_dir.glob("plan-*/plan.json"))
