"""Tests for the Acheron CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest
import respx
from click.testing import CliRunner

from acheron import cli as cli_module
from acheron.cli import main

_BASE_URL = "http://test.local:8000"

_UPLOAD_PATH = "inputs/abc/book.epub"


def _job_payload(job_id: str = "job-1", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "job_id": job_id,
        "status": "running",
        "plan_id": "plan-1",
        "label": None,
        "retries_from": None,
        "source_type": "epub",
        "source_language": "en",
        "target_language": "es",
        "asr_model": None,
        "executor_strategy": "streaming",
        "created_at": "2026-07-29T12:00:00Z",
        "last_persisted_at": "2026-07-29T12:00:01Z",
        "progress": {
            "completed_steps": 0,
            "total_steps": 0,
            "current_step_id": None,
            "current_worker_type": None,
            "current_worker_id": None,
            "eta_seconds": None,
        },
        "total_cost": 0.0,
        "total_duration_seconds": 0.0,
        "total_cost_basis": None,
        "outputs": [],
        "errors": [],
        "warnings": [],
    }
    payload.update(overrides)
    return payload


def _mock_upload_success(filename: str = "book.epub", size: int = 10) -> respx.Route:
    """Mock a successful ``POST /inputs`` returning a server-relative ``source_path``."""
    return respx.post(f"{_BASE_URL}/inputs").mock(
        return_value=httpx.Response(
            201,
            json={
                "source_path": f"inputs/abc/{filename}",
                "filename": filename,
                "size_bytes": size,
                "content_type": "application/epub+zip",
            },
        )
    )


@pytest.fixture(autouse=True)
def _stable_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ACHERON_URL so the CLI default doesn't leak into tests."""
    monkeypatch.setenv("ACHERON_URL", _BASE_URL)


@respx.mock
def test_submit_epub(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    warning = "BOOTING TTS workers: tts-1 (3s elapsed); cold start typically takes 30\u201390 seconds."
    _mock_upload_success(filename="book.epub")
    jobs_route = respx.post(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(
            201,
            json=_job_payload("job-abc", plan_id="plan-1", warnings=[warning]),
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "es"])
    assert result.exit_code == 0, result.output
    assert "job-abc" in result.output
    assert "running" in result.output
    assert "Warning:" in result.output
    assert "tts-1" in result.output
    assert "3s elapsed" in result.output
    assert "30\u201390 seconds" in result.output
    assert result.output.index("Plan: plan-1") < result.output.index("Warning:")
    # Job request must reference the server-relative path from the upload response,
    # not the local filesystem path the user provided.
    job_body = json.loads(jobs_route.calls.last.request.content)
    assert job_body["source_path"] == _UPLOAD_PATH


@respx.mock
def test_submit_audio(tmp_path: Path) -> None:
    """Submitting an audio file uploads it and forwards ``--asr`` to ``/jobs``."""
    mp3 = tmp_path / "podcast.mp3"
    mp3.touch()
    _mock_upload_success(filename="podcast.mp3")
    jobs_route = respx.post(f"{_BASE_URL}/jobs").mock(return_value=httpx.Response(201, json=_job_payload("job-def")))
    runner = CliRunner()
    result = runner.invoke(main, ["job", "submit", str(mp3), "--src", "en", "--dest", "es", "--asr", "whisper-v3"])
    assert result.exit_code == 0, result.output
    assert "job-def" in result.output
    job_body = json.loads(jobs_route.calls.last.request.content)
    assert job_body["asr_model"] == "whisper-v3"
    assert job_body["source_type"] == "audio"
    assert job_body["source_path"] == "inputs/abc/podcast.mp3"


@respx.mock
def test_submit_with_type_override(tmp_path: Path) -> None:
    """``--type`` override is preserved end-to-end and not inferred from the filename suffix."""
    unknown = tmp_path / "input.dat"
    unknown.touch()
    _mock_upload_success(filename="input.dat")
    jobs_route = respx.post(f"{_BASE_URL}/jobs").mock(return_value=httpx.Response(201, json=_job_payload("job-xyz")))
    runner = CliRunner()
    result = runner.invoke(main, ["job", "submit", str(unknown), "--src", "en", "--dest", "es", "--type", "epub"])
    assert result.exit_code == 0, result.output
    assert "job-xyz" in result.output
    job_body = json.loads(jobs_route.calls.last.request.content)
    assert job_body["source_type"] == "epub"
    assert job_body["source_path"] == "inputs/abc/input.dat"


def test_submit_unknown_type(tmp_path: Path) -> None:
    unknown = tmp_path / "input.dat"
    unknown.touch()
    runner = CliRunner()
    result = runner.invoke(main, ["job", "submit", str(unknown), "--src", "en", "--dest", "es"])
    assert result.exit_code == 1
    assert "Cannot detect source type" in result.output


def test_submit_missing_file() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["job", "submit", "/nonexistent.epub", "--src", "en", "--dest", "es"])
    assert result.exit_code != 0


@respx.mock
def test_submit_upload_http_error_skips_jobs_request(tmp_path: Path) -> None:
    """When the upload fails, the CLI must exit before calling ``/jobs``.

    Only ``/inputs`` is mocked; if the CLI tries to call ``/jobs``,
    respx raises ``RequestNotConfigured`` and the test fails. The exit
    code must be non-zero and the output must surface the upload error.
    """
    respx.post(f"{_BASE_URL}/inputs").mock(return_value=httpx.Response(500, json={"detail": "Upload failed"}))
    epub = tmp_path / "book.epub"
    epub.touch()
    runner = CliRunner()
    result = runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "es"])
    assert result.exit_code != 0
    assert "500" in result.output
    assert "Upload failed" in result.output


@respx.mock
def test_submit_sends_bearer_token_on_upload_and_jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The registration token is forwarded as a bearer header on both the
    upload and the job submission.
    """
    token = "test-registration-token-must-be-32-chars-or-more"
    monkeypatch.setenv("ACHERON_REGISTRATION_TOKEN", token)
    upload_route = _mock_upload_success(filename="book.epub")
    jobs_route = respx.post(f"{_BASE_URL}/jobs").mock(return_value=httpx.Response(201, json=_job_payload("job-abc")))
    epub = tmp_path / "book.epub"
    epub.touch()
    runner = CliRunner()
    result = runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "es"])
    assert result.exit_code == 0, result.output
    assert upload_route.calls.last.request.headers["authorization"] == f"Bearer {token}"
    assert jobs_route.calls.last.request.headers["authorization"] == f"Bearer {token}"


@respx.mock
@respx.mock
def test_job_cost_explain_renders_execution_estimate_and_gpu_details() -> None:
    respx.get(f"{_BASE_URL}/jobs/job-1/cost").mock(
        return_value=httpx.Response(
            200,
            json={
                "job_id": "job-1",
                "total_cost": 0.34,
                "total_cost_basis": "measured",
                "cost_breakdown": [
                    {
                        "step_id": "synthesize",
                        "worker_type": "tts",
                        "worker_id": "tts-1",
                        "gpu_seconds": 1800.0,
                        "cost": 0.34,
                        "basis": "measured",
                        "rate_per_hour": 0.69,
                        "gpu_type": "L4",
                        "secure_cloud": False,
                        "queried_at": "2026-07-30T12:00:00Z",
                        "cache_age_seconds": 0.0,
                    }
                ],
            },
        )
    )
    result = CliRunner().invoke(main, ["job", "cost", "job-1", "--explain"])
    assert result.exit_code == 0, result.output
    assert "execution-time estimate" in result.output
    assert "not invoice amounts" in result.output
    assert "L4" in result.output
    assert "0.69" in result.output


@respx.mock
def test_status() -> None:
    respx.get(f"{_BASE_URL}/jobs/job-abc").mock(
        return_value=httpx.Response(
            200,
            json=_job_payload(
                "job-abc",
                plan_id="plan-1",
                progress={
                    "completed_steps": 2,
                    "total_steps": 5,
                    "current_step_id": None,
                    "current_worker_type": None,
                    "current_worker_id": None,
                    "eta_seconds": None,
                },
            ),
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["job", "status", "job-abc"])
    assert result.exit_code == 0
    assert "job-abc" in result.output
    assert "2/5" in result.output
    assert "Current step: -" in result.output
    assert "Current worker type: -" in result.output
    assert "Current worker ID: -" in result.output
    assert "ETA: Unknown" in result.output


@respx.mock
def test_job_status_renders_output_and_step_error() -> None:
    respx.get(f"{_BASE_URL}/jobs/job-1").mock(
        return_value=httpx.Response(
            200,
            json=_job_payload(
                "job-1",
                status="failed",
                progress={
                    "completed_steps": 2,
                    "total_steps": 5,
                    "current_step_id": "step-3",
                    "current_worker_type": "tts",
                    "current_worker_id": "tts-1",
                    "eta_seconds": 12.5,
                },
                outputs=[
                    {
                        "download_url": "/jobs/job-1/outputs/0",
                        "filename": "result.m4b",
                        "size_bytes": 123,
                        "content_type": "audio/mp4",
                    }
                ],
                errors=[
                    {
                        "step_id": "step-3",
                        "worker_type": "tts",
                        "worker_id": "tts-1",
                        "message": "malformed audio",
                        "timestamp": "2026-07-29T12:00:02Z",
                    }
                ],
            ),
        )
    )
    result = CliRunner().invoke(main, ["job", "status", "job-1", "--verbose"])
    assert result.exit_code == 0, result.output
    for value in (
        "Plan: plan-1",
        "Retries from: -",
        "Source type: epub",
        "Source language: en",
        "Target language: es",
        "ASR model: -",
        "Executor strategy: streaming",
        "Created: 2026-07-29T12:00:00+00:00",
        "Last persisted: 2026-07-29T12:00:01+00:00",
        "Progress: 2/5",
        "Current step: step-3",
        "Current worker type: tts",
        "Current worker ID: tts-1",
        "ETA: 12.5s",
        "Estimated cost (execution-time estimate): unknown",
        "Duration: 0.0s",
        "Download URL: /jobs/job-1/outputs/0",
        "step=step-3",
        "worker_id=tts-1",
    ):
        assert value in result.output


@respx.mock
def test_retry_cli_sends_overrides_and_reports_link() -> None:
    route = respx.post(f"{_BASE_URL}/jobs/job-old/retry").mock(
        return_value=httpx.Response(
            201,
            json=_job_payload("job-new", retries_from="job-old", label="atlas-retry"),
        )
    )

    result = CliRunner().invoke(
        main,
        ["job", "retry", "job-old", "--src", "en", "--dest", "fr", "--asr", "whisper-tiny", "--label", "atlas-retry"],
    )

    assert result.exit_code == 0, result.output
    assert "job-new" in result.output
    assert "job-old" in result.output
    assert json.loads(route.calls.last.request.content) == {
        "source_language": "en",
        "target_language": "fr",
        "asr_model": "whisper-tiny",
        "label": "atlas-retry",
    }


@respx.mock
def test_cancel_cli_returns_success() -> None:
    respx.post(f"{_BASE_URL}/jobs/job-1/cancel").mock(
        return_value=httpx.Response(200, json=_job_payload("job-1", status="failed"))
    )

    result = CliRunner().invoke(main, ["job", "cancel", "job-1"])

    assert result.exit_code == 0, result.output
    assert "cancelled" in result.output.lower()


@respx.mock
def test_cancel_cli_returns_failure_with_remediation() -> None:
    respx.post(f"{_BASE_URL}/jobs/job-1/cancel").mock(
        return_value=httpx.Response(
            409,
            json={
                "detail": {
                    "type": "JobNotCancellableError",
                    "message": "Job job-1 is already completed",
                    "remediation": "acheron job status job-1",
                }
            },
        )
    )

    result = CliRunner().invoke(main, ["job", "cancel", "job-1"])

    assert result.exit_code == 1
    assert "acheron job status job-1" in result.output


@respx.mock
def test_jobs_accepts_recovery_filters_and_renders_archive_metadata() -> None:
    route = respx.get(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    _job_payload(
                        "job-old",
                        status="running",
                        archived_at="2026-07-30T12:34:56Z",
                        last_persisted_at="2026-07-29T12:00:00Z",
                    )
                ]
            },
        )
    )

    result = CliRunner().invoke(
        main,
        ["jobs", "--since", "24h", "--status", "running", "--older-than", "30m", "--include-archived"],
    )

    assert result.exit_code == 0, result.output
    params = route.calls.last.request.url.params
    assert params["status"] == "running"
    assert params["older_than_seconds"] == "1800.0"
    assert params["include_archived"] == "true"
    assert "job-old" in result.output
    assert "archived" in result.output.lower()
    assert "2026-07-30T12:34:56+00:00" in result.output
    assert "stale age" in result.output.lower()


@respx.mock
def test_job_archive_renders_preserved_record_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACHERON_ADMIN_TOKEN", "admin-token")
    route = respx.post(f"{_BASE_URL}/admin/jobs/job-old/archive").mock(
        return_value=httpx.Response(
            200,
            json={
                "job": _job_payload(
                    "job-old",
                    status="failed",
                    plan_id="plan-old",
                    archived_at="2026-07-30T12:34:56Z",
                    outputs=[
                        {
                            "download_url": "/jobs/job-old/outputs/0",
                            "filename": "result.wav",
                            "size_bytes": 10,
                            "content_type": "audio/wav",
                        }
                    ],
                    total_cost=0.25,
                    total_cost_basis="measured",
                )
            },
        )
    )

    result = CliRunner().invoke(main, ["job", "archive", "job-old"])

    assert result.exit_code == 0, result.output
    assert route.called
    assert "archived at=2026-07-30T12:34:56+00:00" in result.output
    assert "record preserved" in result.output.lower()


@respx.mock
def test_jobs_accepts_label_filter() -> None:
    route = respx.get(f"{_BASE_URL}/jobs", params={"label": "atlas-*"}).mock(
        return_value=httpx.Response(200, json={"jobs": [_job_payload("job-1", label="atlas-ch1")]})
    )
    result = CliRunner().invoke(main, ["jobs", "--label", "atlas-*"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert "atlas-ch1" in result.output


@respx.mock
def test_job_plan_by_plan_id() -> None:
    respx.get(f"{_BASE_URL}/plans/plan-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "plan_id": "plan-1",
                "job_id": "job-1",
                "source_type": "epub",
                "source_language": "en",
                "target_language": "es",
                "executor_strategy": "streaming",
                "steps": [{"step_id": "extract", "worker_type": "extraction", "depends_on": [], "status": "pending"}],
            },
        )
    )

    result = CliRunner().invoke(main, ["job", "plan", "plan-1"])

    assert result.exit_code == 0, result.output
    assert "extract" in result.output
    assert "extraction" in result.output


@respx.mock
def test_job_plan_by_job_id() -> None:
    """`--job` resolves the plan ID from the job before fetching the plan."""
    respx.get(f"{_BASE_URL}/jobs/job-abc").mock(
        return_value=httpx.Response(
            200,
            json=_job_payload("job-abc", plan_id="plan-1"),
        )
    )
    respx.get(f"{_BASE_URL}/plans/plan-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "plan_id": "plan-1",
                "job_id": "job-1",
                "source_type": "epub",
                "source_language": "en",
                "target_language": "es",
                "executor_strategy": "streaming",
                "steps": [{"step_id": "extract", "worker_type": "extraction", "depends_on": [], "status": "pending"}],
            },
        )
    )

    result = CliRunner().invoke(main, ["job", "plan", "--job", "job-abc"])

    assert result.exit_code == 0, result.output
    assert "extract" in result.output
    assert "extraction" in result.output


def test_job_plan_requires_exactly_one_selector() -> None:
    """Supplying neither or both `PLAN_ID` and `--job` returns a Click usage error."""
    runner = CliRunner()

    neither = runner.invoke(main, ["job", "plan"])
    assert neither.exit_code != 0
    assert "exactly one" in neither.output.lower()

    both = runner.invoke(main, ["job", "plan", "plan-1", "--job", "job-abc"])
    assert both.exit_code != 0
    assert "exactly one" in both.output.lower()


@respx.mock
def test_submit_dry_run_previews_without_submitting(tmp_path: Path) -> None:
    """`--dry-run` uploads, calls `/jobs:preview`, and never calls `/jobs`."""
    epub = tmp_path / "book.epub"
    epub.touch()
    _mock_upload_success()
    preview_route = respx.post(f"{_BASE_URL}/jobs:preview").mock(
        return_value=httpx.Response(
            200,
            json={
                "plan_id": "plan-preview",
                "job_id": "job-preview",
                "source_type": "epub",
                "source_language": "en",
                "target_language": "es",
                "executor_strategy": "streaming",
                "steps": [{"step_id": "extract", "worker_type": "extraction", "depends_on": [], "status": "pending"}],
            },
        )
    )

    result = CliRunner().invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "es", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "no job submitted" in result.output.lower()
    assert "plan-preview" in result.output
    assert preview_route.called


@respx.mock
def test_status_verbose() -> None:
    respx.get(f"{_BASE_URL}/jobs/job-abc").mock(
        return_value=httpx.Response(
            200,
            json=_job_payload(
                "job-abc",
                status="failed",
                errors=[
                    {
                        "step_id": "step-3",
                        "worker_type": "tts",
                        "worker_id": "tts-1",
                        "message": "Worker timeout",
                        "timestamp": "2026-07-29T12:00:02Z",
                    }
                ],
            ),
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["job", "status", "job-abc", "-v"])
    assert result.exit_code == 0
    assert "Worker timeout" in result.output


@respx.mock
def test_status_not_found() -> None:
    respx.get(f"{_BASE_URL}/jobs/nonexistent").mock(return_value=httpx.Response(404, json={"detail": "Job not found"}))
    runner = CliRunner()
    result = runner.invoke(main, ["job", "status", "nonexistent"])
    assert result.exit_code != 0
    assert "404" in result.output
    assert "Job not found" in result.output


@respx.mock
def test_status_service() -> None:
    respx.get(f"{_BASE_URL}/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    respx.get(f"{_BASE_URL}/workers").mock(
        return_value=httpx.Response(
            200,
            json={
                "workers": [
                    {
                        "worker_id": "w1",
                        "worker_type": "tts",
                        "endpoint": "http://w1",
                        "transport": "http",
                        "consecutive_failures": 0,
                    }
                ]
            },
        )
    )
    respx.get(f"{_BASE_URL}/capabilities").mock(
        return_value=httpx.Response(200, json={"language_pairs": [{"src": "en", "dst": "es", "workers": ["w1"]}]})
    )
    runner = CliRunner()
    result = runner.invoke(main, ["status"])
    assert result.exit_code == 0
    assert "ok" in result.output
    assert "tts" in result.output
    assert "Capabilities: 1" in result.output


@respx.mock
def test_job_resume() -> None:
    route = respx.post(f"{_BASE_URL}/jobs/job-abc/resume").mock(
        return_value=httpx.Response(200, json=_job_payload("job-abc"))
    )
    runner = CliRunner()
    result = runner.invoke(main, ["job", "resume", "job-abc", "--invalidate-step", "step-47"])
    assert result.exit_code == 0
    assert json.loads(route.calls[0].request.content) == {
        "invalidate_steps": ["step-47"],
        "invalidate_chapters": [],
    }
    assert "job-abc" in result.output


@respx.mock
def test_admin_reap_stuck_renders_ids_and_uses_admin_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACHERON_ADMIN_TOKEN", "admin-token")
    route = respx.post(f"{_BASE_URL}/admin/jobs/reap-stale").mock(
        return_value=httpx.Response(200, json={"reaped": 2, "job_ids": ["job-1", "job-2"]})
    )

    result = CliRunner().invoke(main, ["admin", "reap-stuck", "--older-than", "60s", "--reason", "restart"])

    assert result.exit_code == 0, result.output
    assert "reaped=2" in result.output
    assert "job-1" in result.output
    assert "job-2" in result.output
    assert route.calls.last.request.headers["authorization"] == "Bearer admin-token"
    assert json.loads(route.calls.last.request.content) == {"older_than_seconds": 60.0, "reason": "restart"}


@respx.mock
def test_archive_requires_admin_token_before_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACHERON_ADMIN_TOKEN", raising=False)
    route = respx.post(f"{_BASE_URL}/admin/jobs/job-1/archive").mock(return_value=httpx.Response(200))

    result = CliRunner().invoke(main, ["job", "archive", "job-1"])

    assert result.exit_code != 0
    assert "ACHERON_ADMIN_TOKEN" in result.output
    assert not route.called


@respx.mock
def test_jobs_empty() -> None:
    respx.get(f"{_BASE_URL}/jobs").mock(return_value=httpx.Response(200, json={"jobs": []}))
    runner = CliRunner()
    result = runner.invoke(main, ["jobs"])
    assert result.exit_code == 0
    assert "No jobs found" in result.output


@respx.mock
def test_jobs_list() -> None:
    respx.get(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    _job_payload(
                        "job-1",
                        plan_id="plan-1",
                        progress={
                            "completed_steps": 1,
                            "total_steps": 3,
                            "current_step_id": None,
                            "current_worker_type": None,
                            "current_worker_id": None,
                            "eta_seconds": None,
                        },
                    ),
                    _job_payload(
                        "job-2",
                        status="completed",
                        plan_id="plan-2",
                        progress={
                            "completed_steps": 3,
                            "total_steps": 3,
                            "current_step_id": None,
                            "current_worker_type": None,
                            "current_worker_id": None,
                            "eta_seconds": None,
                        },
                    ),
                ]
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["jobs"])
    assert result.exit_code == 0
    assert "job-1" in result.output
    assert "job-2" in result.output


@respx.mock
def test_jobs_filter_active() -> None:
    respx.get(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    _job_payload("job-1"),
                    _job_payload(
                        "job-2",
                        status="completed",
                        progress={
                            "completed_steps": 3,
                            "total_steps": 3,
                            "current_step_id": None,
                            "current_worker_type": None,
                            "current_worker_id": None,
                            "eta_seconds": None,
                        },
                    ),
                ]
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["jobs", "--active"])
    assert result.exit_code == 0
    assert "job-1" in result.output
    assert "job-2" not in result.output


@respx.mock
def test_jobs_filter_completed() -> None:
    respx.get(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    _job_payload("job-1"),
                    _job_payload(
                        "job-2",
                        status="completed",
                        progress={
                            "completed_steps": 3,
                            "total_steps": 3,
                            "current_step_id": None,
                            "current_worker_type": None,
                            "current_worker_id": None,
                            "eta_seconds": None,
                        },
                    ),
                ]
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["jobs", "--completed"])
    assert result.exit_code == 0
    assert "job-1" not in result.output
    assert "job-2" in result.output


@respx.mock
def test_workers_empty() -> None:
    respx.get(f"{_BASE_URL}/workers").mock(return_value=httpx.Response(200, json={"workers": []}))
    runner = CliRunner()
    result = runner.invoke(main, ["workers"])
    assert result.exit_code == 0
    assert "No workers registered" in result.output


@respx.mock
def test_workers_list() -> None:
    respx.get(f"{_BASE_URL}/workers").mock(
        return_value=httpx.Response(
            200,
            json={
                "workers": [
                    {
                        "worker_id": "tts-1",
                        "endpoint": "http://tts:8000",
                        "transport": "http",
                        "worker_type": "tts",
                        "consecutive_failures": 0,
                    }
                ]
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["workers"])
    assert result.exit_code == 0
    assert "tts-1" in result.output
    assert "http://tts:8000" in result.output


@respx.mock
def test_capabilities_empty() -> None:
    respx.get(f"{_BASE_URL}/capabilities").mock(return_value=httpx.Response(200, json={"language_pairs": []}))
    runner = CliRunner()
    result = runner.invoke(main, ["capabilities"])
    assert result.exit_code == 0
    assert "No language pairs" in result.output


@respx.mock
def test_capabilities_list() -> None:
    respx.get(f"{_BASE_URL}/capabilities").mock(
        return_value=httpx.Response(
            200,
            json={
                "language_pairs": [
                    {"src": "en", "dst": "es", "workers": ["tts-1", "trans-1"]},
                    {"src": "en", "dst": "fr", "workers": ["tts-2"]},
                ]
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["capabilities"])
    assert result.exit_code == 0
    assert "en" in result.output
    assert "es" in result.output


@respx.mock
def test_capabilities_filter_src() -> None:
    respx.get(f"{_BASE_URL}/capabilities", params={"src": "en"}).mock(
        return_value=httpx.Response(
            200,
            json={"language_pairs": [{"src": "en", "dst": "es", "workers": ["tts-1"]}]},
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["capabilities", "--src", "en"])
    assert result.exit_code == 0
    assert "en" in result.output


@respx.mock
def test_capabilities_filter_dest() -> None:
    respx.get(f"{_BASE_URL}/capabilities", params={"dest": "es"}).mock(
        return_value=httpx.Response(
            200,
            json={"language_pairs": [{"src": "en", "dst": "es", "workers": ["tts-1"]}]},
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["capabilities", "--dest", "es"])
    assert result.exit_code == 0
    assert "es" in result.output


@respx.mock
def test_capabilities_typed_tts_renders_voice_table() -> None:
    """``--type tts`` calls the typed capabilities endpoint and renders a
    Worker ID / Model / Voice table that includes the worker id, model name,
    and the metadata voice.
    """
    respx.get(f"{_BASE_URL}/capabilities", params={"type": "tts"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "language_pairs": [],
                "workers": [
                    {
                        "worker_id": "tts-1",
                        "worker_type": "tts",
                        "model_source": "Qwen/Qwen3-TTS",
                        "metadata": {"voice": "vivian"},
                    }
                ],
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["capabilities", "--type", "tts"])
    assert result.exit_code == 0, result.output
    assert "Worker ID" in result.output
    assert "Model" in result.output
    assert "Voice" in result.output
    assert "tts-1" in result.output
    assert "Qwen/Qwen3-TTS" in result.output
    assert "vivian" in result.output


@respx.mock
def test_capabilities_typed_absent_voice_renders_dash() -> None:
    """A worker with no ``metadata.voice`` (or a non-string one) renders ``-``.

    The model source is also rendered as ``-`` when absent, so a worker
    with neither is still printable without crashing the table.
    """
    respx.get(f"{_BASE_URL}/capabilities", params={"type": "tts"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "language_pairs": [],
                "workers": [{"worker_id": "tts-2", "worker_type": "tts", "metadata": {}}],
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["capabilities", "--type", "tts"])
    assert result.exit_code == 0, result.output
    assert "tts-2" in result.output
    # The dash must appear at least once; the absent-voice column is the
    # one this regression test is asserting.
    assert "-" in result.output


@respx.mock
def test_capabilities_typed_with_src_filter_is_rejected() -> None:
    """``--type`` and ``--src`` are mutually exclusive: typed output is
    workers, not language pairs.
    """
    runner = CliRunner()
    result = runner.invoke(main, ["capabilities", "--type", "tts", "--src", "en"])
    assert result.exit_code != 0
    assert "type" in result.output.lower()
    assert "src" in result.output.lower()


@respx.mock
def test_capabilities_typed_with_dest_filter_is_rejected() -> None:
    """``--type`` and ``--dest`` are mutually exclusive."""
    runner = CliRunner()
    result = runner.invoke(main, ["capabilities", "--type", "tts", "--dest", "es"])
    assert result.exit_code != 0
    assert "type" in result.output.lower()
    assert "dest" in result.output.lower()


@respx.mock
def test_submit_server_error_shows_friendly_message(tmp_path: Path) -> None:
    _mock_upload_success(filename="book.epub")
    respx.post(f"{_BASE_URL}/jobs").mock(return_value=httpx.Response(500, json={"detail": "Internal server error"}))
    epub = tmp_path / "book.epub"
    epub.touch()
    runner = CliRunner()
    result = runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "es"])
    assert result.exit_code != 0
    assert "500" in result.output
    assert "Internal server error" in result.output


@respx.mock
def test_jobs_server_error_shows_friendly_message() -> None:
    respx.get(f"{_BASE_URL}/jobs").mock(return_value=httpx.Response(503, json={"detail": "Service unavailable"}))
    runner = CliRunner()
    result = runner.invoke(main, ["jobs"])
    assert result.exit_code != 0
    assert "503" in result.output
    assert "Service unavailable" in result.output


@respx.mock
def test_workers_server_error_shows_friendly_message() -> None:
    respx.get(f"{_BASE_URL}/workers").mock(return_value=httpx.Response(500, json={"detail": "Registry failure"}))
    runner = CliRunner()
    result = runner.invoke(main, ["workers"])
    assert result.exit_code != 0
    assert "500" in result.output
    assert "Registry failure" in result.output


@respx.mock
def test_submit_validation_error_shows_detail(tmp_path: Path) -> None:
    _mock_upload_success(filename="book.epub")
    respx.post(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(422, json={"detail": "Invalid language path: en→xx"})
    )
    epub = tmp_path / "book.epub"
    epub.touch()
    runner = CliRunner()
    result = runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "xx"])
    assert result.exit_code != 0
    assert "422" in result.output
    assert "Invalid language path" in result.output


@respx.mock
def test_submit_invalid_language_shows_supported_targets(tmp_path: Path) -> None:
    _mock_upload_success(filename="book.epub")
    respx.post(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(
            422,
            json={"detail": "InvalidLanguagePathError: No translation worker supports: en → xx"},
        )
    )
    capabilities = respx.get(f"{_BASE_URL}/capabilities", params={"src": "en"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "language_pairs": [
                    {"src": "en", "dst": "fr", "workers": ["translation-1"]},
                    {"src": "en", "dst": "es", "workers": ["translation-2"]},
                    {"src": "en", "dst": "fr", "workers": ["translation-3"]},
                ]
            },
        )
    )
    epub = tmp_path / "book.epub"
    epub.touch()
    runner = CliRunner()
    result = runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "xx"])
    assert result.exit_code != 0
    assert capabilities.called
    assert "No worker can translate en→xx" in result.output
    assert "Supported targets from en: es, fr" in result.output
    assert "acheron capabilities --src en" in result.output


@respx.mock
def test_submit_invalid_language_capability_lookup_failure_preserves_remediation(tmp_path: Path) -> None:
    _mock_upload_success(filename="book.epub")
    respx.post(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(
            422,
            json={"detail": "InvalidLanguagePathError: No translation worker supports: en → xx"},
        )
    )
    respx.get(f"{_BASE_URL}/capabilities", params={"src": "en"}).mock(
        return_value=httpx.Response(503, json={"detail": "Capabilities unavailable"})
    )
    epub = tmp_path / "book.epub"
    epub.touch()
    runner = CliRunner()
    result = runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "xx"])
    assert result.exit_code != 0
    assert "No worker can translate en→xx" in result.output
    assert "acheron capabilities --src en" in result.output


@respx.mock
def test_submit_invalid_language_capability_malformed_response_preserves_remediation(tmp_path: Path) -> None:
    _mock_upload_success(filename="book.epub")
    respx.post(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(
            422,
            json={"detail": "InvalidLanguagePathError: No translation worker supports: en → xx"},
        )
    )
    respx.get(f"{_BASE_URL}/capabilities", params={"src": "en"}).mock(return_value=httpx.Response(200, json=[]))
    epub = tmp_path / "book.epub"
    epub.touch()
    runner = CliRunner()
    result = runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "xx"])
    assert result.exit_code != 0
    assert "No worker can translate en→xx" in result.output
    assert "acheron capabilities --src en" in result.output
    assert "ValidationError" not in result.output


@respx.mock
def test_submit_invalid_language_capability_timeout_preserves_remediation(tmp_path: Path) -> None:
    _mock_upload_success(filename="book.epub")
    respx.post(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(
            422,
            json={"detail": "InvalidLanguagePathError: No translation worker supports: en → xx"},
        )
    )
    respx.get(f"{_BASE_URL}/capabilities", params={"src": "en"}).mock(
        side_effect=httpx.ReadTimeout("capabilities timed out")
    )
    epub = tmp_path / "book.epub"
    epub.touch()
    runner = CliRunner()
    result = runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "xx"])
    assert result.exit_code != 0
    assert "No worker can translate en→xx" in result.output
    assert "Request to Acheron timed out" in result.output
    assert "acheron capabilities --src en" in result.output


@respx.mock
def test_submit_unknown_domain_error_shows_generic_remediation(tmp_path: Path) -> None:
    _mock_upload_success(filename="book.epub")
    respx.post(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(
            422,
            json={"detail": "WorkerUnavailableError: no translation worker is healthy"},
        )
    )
    epub = tmp_path / "book.epub"
    epub.touch()
    runner = CliRunner()
    result = runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "es"])
    assert result.exit_code != 0
    assert "Job submission failed: no translation worker is healthy" in result.output
    assert "WorkerUnavailableError" not in result.output
    assert "worker capabilities" in result.output


@respx.mock
def test_submit_class_only_domain_error_does_not_leak_class_name(tmp_path: Path) -> None:
    _mock_upload_success(filename="book.epub")
    respx.post(f"{_BASE_URL}/jobs").mock(return_value=httpx.Response(422, json={"detail": "WorkerUnavailableError"}))
    epub = tmp_path / "book.epub"
    epub.touch()
    runner = CliRunner()
    result = runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "es"])
    assert result.exit_code != 0
    assert "WorkerUnavailableError" not in result.output
    assert "unspecified" in result.output


@respx.mock
def test_submit_chunking_error_shows_remediation(tmp_path: Path) -> None:
    _mock_upload_success(filename="book.epub")
    respx.post(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(
            422,
            json={"detail": "ChunkingTooLongForWorkerError: max length exceeds worker budget"},
        )
    )
    epub = tmp_path / "book.epub"
    epub.touch()
    runner = CliRunner()
    result = runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "es"])
    assert result.exit_code != 0
    assert "Job cannot be submitted: max length exceeds worker budget" in result.output
    assert "larger token limit" in result.output


@pytest.mark.parametrize("body", [b"{", b"[]"])
@respx.mock
def test_generic_http_error_handles_non_object_json(body: bytes) -> None:
    respx.get(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(500, content=body, headers={"content-type": "application/json"})
    )
    runner = CliRunner()
    result = runner.invoke(main, ["jobs"])
    assert result.exit_code != 0
    assert "500" in result.output
    assert "JSONDecodeError" not in result.output
    assert "AttributeError" not in result.output


@respx.mock
def test_job_resume_already_running_shows_remediation() -> None:
    respx.post(f"{_BASE_URL}/jobs/job-abc/resume").mock(
        return_value=httpx.Response(
            409,
            json={
                "detail": {
                    "type": "JobAlreadyRunningError",
                    "message": "Job job-abc is already running",
                    "remediation": "acheron job cancel job-abc",
                }
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["job", "resume", "job-abc"])
    assert result.exit_code != 0
    assert "Job job-abc is already running" in result.output
    assert "acheron job cancel job-abc" in result.output


@respx.mock
def test_job_resume_no_plan_shows_resubmit_remediation() -> None:
    respx.post(f"{_BASE_URL}/jobs/job-abc/resume").mock(
        return_value=httpx.Response(
            422,
            json={
                "detail": {
                    "type": "NoPlanToResumeError",
                    "message": "Job job-abc has no saved plan to resume",
                    "remediation": "acheron job submit <source> --src ... --dest ...",
                }
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["job", "resume", "job-abc"])
    assert result.exit_code != 0
    assert "Job resume failed: Job job-abc has no saved plan to resume" in result.output
    assert "NoPlanToResumeError" not in result.output
    assert "Try: acheron job submit" in result.output


@respx.mock
def test_connect_error_shows_friendly_message() -> None:
    respx.get(f"{_BASE_URL}/jobs").mock(side_effect=httpx.ConnectError("Connection refused"))
    runner = CliRunner()
    result = runner.invoke(main, ["jobs"])
    assert result.exit_code != 0
    assert "Cannot connect" in result.output
    assert "server running" in result.output.lower()


@respx.mock
def test_ssl_verification_error_shows_trust_store_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """TLS verification failure points the user at the trust store env vars.

    httpx wraps SSLCertVerificationError as a ConnectError (the failure
    happens during start_tls, which is part of the connection phase). The
    CLI distinguishes that from a real connect-refused and tells the user
    how to fix the trust store.
    """
    import ssl

    def _make_connect_with_ssl_cause() -> httpx.ConnectError:
        ssl_exc = ssl.SSLCertVerificationError("certificate verify failed")
        return ssl_exc_to_connect(ssl_exc)

    def ssl_exc_to_connect(ssl_exc: ssl.SSLCertVerificationError) -> httpx.ConnectError:
        inner = httpx.ConnectError("inner")
        inner.__cause__ = ssl_exc
        outer = httpx.ConnectError("TLS failed")
        outer.__cause__ = inner
        return outer

    class _FakeClient:
        async def list_jobs(self, *, label: str | None = None) -> list[dict[str, Any]]:
            raise _make_connect_with_ssl_cause()

    monkeypatch.setattr(cli_module, "_get_client", _FakeClient)
    runner = CliRunner()
    result = runner.invoke(main, ["jobs"])
    assert result.exit_code != 0
    assert "TLS" in result.output or "SSL" in result.output or "certificate" in result.output.lower()
    assert "SSL_CERT_FILE" in result.output or "ACHERON_TLS_CA_FILE" in result.output


def test_is_ssl_error_walks_cause_chain() -> None:
    """The walker follows __cause__ and __context__ to find SSLError causes."""
    import ssl

    ssl_exc = ssl.SSLCertVerificationError("verify failed")
    inner = httpx.ConnectError("inner")
    inner.__cause__ = ssl_exc
    outer = httpx.ConnectError("outer")
    outer.__cause__ = inner
    assert cli_module._is_ssl_error(outer)  # noqa: SLF001


def test_is_ssl_error_returns_false_for_plain_connect_error() -> None:
    plain = httpx.ConnectError("Connection refused")
    assert not cli_module._is_ssl_error(plain)  # noqa: SLF001


class _CapturedClient:
    """Sentinel object that records the kwargs AcheronClient was called with."""

    instances: ClassVar[list[_CapturedClient]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.instances.append(self)

    def __getattr__(self, name: str) -> Any:
        msg = f"_CapturedClient used as a real client: .{name}"
        raise AssertionError(msg)


@pytest.fixture
def captured_client(monkeypatch: pytest.MonkeyPatch) -> list[_CapturedClient]:
    captured: list[_CapturedClient] = []
    monkeypatch.setattr(_CapturedClient, "instances", captured)
    monkeypatch.setattr(cli_module, "AcheronClient", _CapturedClient)
    return captured


def test_default_url_is_https(monkeypatch: pytest.MonkeyPatch, captured_client: list[_CapturedClient]) -> None:
    """CLI defaults to https:// so it works against the dev/HTTPS orchestrator."""
    monkeypatch.delenv("ACHERON_URL", raising=False)
    cli_module._get_client()  # noqa: SLF001
    assert captured_client[0].args[0] == "https://localhost:8000"


def test_verify_uses_acheron_ca_file(
    monkeypatch: pytest.MonkeyPatch, captured_client: list[_CapturedClient], tmp_path: Path
) -> None:
    monkeypatch.delenv("ACHERON_URL", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    ca = tmp_path / "ca.crt"
    ca.touch()
    monkeypatch.setenv("ACHERON_TLS_CA_FILE", str(ca))
    cli_module._get_client()  # noqa: SLF001
    assert captured_client[0].kwargs["verify"] == str(ca)


def test_verify_falls_back_to_ssl_cert_file(
    monkeypatch: pytest.MonkeyPatch, captured_client: list[_CapturedClient], tmp_path: Path
) -> None:
    monkeypatch.delenv("ACHERON_URL", raising=False)
    monkeypatch.delenv("ACHERON_TLS_CA_FILE", raising=False)
    ca = tmp_path / "ca.crt"
    ca.touch()
    monkeypatch.setenv("SSL_CERT_FILE", str(ca))
    cli_module._get_client()  # noqa: SLF001
    assert captured_client[0].kwargs["verify"] == str(ca)


def test_verify_defaults_to_true_when_no_ca_env(
    monkeypatch: pytest.MonkeyPatch, captured_client: list[_CapturedClient], tmp_path: Path
) -> None:
    monkeypatch.delenv("ACHERON_URL", raising=False)
    monkeypatch.delenv("ACHERON_TLS_CA_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    # Chdir to a directory with no dev CA so auto-discovery doesn't kick in.
    monkeypatch.chdir(tmp_path)
    cli_module._get_client()  # noqa: SLF001
    assert captured_client[0].kwargs["verify"] is True


def test_acheron_ca_takes_precedence_over_ssl_cert_file(
    monkeypatch: pytest.MonkeyPatch, captured_client: list[_CapturedClient], tmp_path: Path
) -> None:
    """ACHERON_TLS_CA_FILE is the explicit override and wins over SSL_CERT_FILE."""
    monkeypatch.delenv("ACHERON_URL", raising=False)
    acheron_ca = tmp_path / "acheron-ca.crt"
    other_ca = tmp_path / "other.crt"
    acheron_ca.touch()
    other_ca.touch()
    monkeypatch.setenv("ACHERON_TLS_CA_FILE", str(acheron_ca))
    monkeypatch.setenv("SSL_CERT_FILE", str(other_ca))
    cli_module._get_client()  # noqa: SLF001
    assert captured_client[0].kwargs["verify"] == str(acheron_ca)


def test_verify_auto_discovers_dev_ca_in_certs_dir(
    monkeypatch: pytest.MonkeyPatch, captured_client: list[_CapturedClient], tmp_path: Path
) -> None:
    """Dev convenience: ./certs/acheron-ca.crt is picked up when no env var is set.

    Lets the host CLI work out of the box against the dev/HTTPS orchestrator.
    """
    monkeypatch.delenv("ACHERON_URL", raising=False)
    monkeypatch.delenv("ACHERON_TLS_CA_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    dev_ca = tmp_path / "certs" / "acheron-ca.crt"
    dev_ca.parent.mkdir(parents=True)
    dev_ca.touch()
    monkeypatch.chdir(tmp_path)
    cli_module._get_client()  # noqa: SLF001
    assert captured_client[0].kwargs["verify"] == str(dev_ca)


def test_env_var_overrides_dev_ca(
    monkeypatch: pytest.MonkeyPatch, captured_client: list[_CapturedClient], tmp_path: Path
) -> None:
    """Env vars win over dev auto-discovery — they're the explicit override."""
    monkeypatch.delenv("ACHERON_URL", raising=False)
    dev_ca = tmp_path / "certs" / "acheron-ca.crt"
    dev_ca.parent.mkdir(parents=True)
    dev_ca.touch()
    monkeypatch.chdir(tmp_path)
    explicit = tmp_path / "explicit.crt"
    explicit.touch()
    monkeypatch.setenv("SSL_CERT_FILE", str(explicit))
    cli_module._get_client()  # noqa: SLF001
    assert captured_client[0].kwargs["verify"] == str(explicit)


@respx.mock
def test_job_watch_exits_zero_on_completion() -> None:
    """watch exits 0 when job completes."""
    import time

    call_count = 0

    def _mock_get(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            return httpx.Response(200, json=_job_payload("job-1", status="completed"))
        return httpx.Response(200, json=_job_payload("job-1", status="running"))

    respx.get(f"{_BASE_URL}/jobs/job-1").mock(side_effect=_mock_get)
    original_sleep = time.sleep
    time.sleep = lambda _s: None
    try:
        result = CliRunner().invoke(main, ["job", "watch", "job-1"])
        assert result.exit_code == 0, result.output
    finally:
        time.sleep = original_sleep


@respx.mock
def test_job_watch_exits_one_on_failure() -> None:
    """watch exits 1 when job fails."""
    import time

    call_count = 0

    def _mock_get(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            return httpx.Response(200, json=_job_payload("job-1", status="failed"))
        return httpx.Response(200, json=_job_payload("job-1", status="running"))

    respx.get(f"{_BASE_URL}/jobs/job-1").mock(side_effect=_mock_get)
    original_sleep = time.sleep
    time.sleep = lambda _s: None
    try:
        result = CliRunner().invoke(main, ["job", "watch", "job-1"])
        assert result.exit_code == 1, result.output
        assert "failed" in result.output.lower()
    finally:
        time.sleep = original_sleep


@respx.mock
def test_job_tail_streams_events() -> None:
    """tail streams NDJSON events and exits 0."""
    from datetime import UTC, datetime

    from acheron.core.models import PlanStatus
    from acheron.core.schemas import JobLogEvent, JobProgress

    events = [
        JobLogEvent(
            job_id="job-1",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            status=PlanStatus.RUNNING,
            progress=JobProgress(),
            message="step extract started",
        ),
        JobLogEvent(
            job_id="job-1",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            status=PlanStatus.COMPLETED,
            progress=JobProgress(),
            message="job completed",
        ),
    ]
    ndjson = ("\n".join(e.model_dump_json() for e in events) + "\n").encode()

    respx.get(f"{_BASE_URL}/jobs/job-1/logs").mock(return_value=httpx.Response(200, content=ndjson))
    result = CliRunner().invoke(main, ["job", "tail", "job-1"])
    assert result.exit_code == 0, result.output
    assert "running" in result.output.lower()
    assert "completed" in result.output.lower()
