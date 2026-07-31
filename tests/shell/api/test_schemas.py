"""Tests for the request-schema module's response-model re-exports."""

import pytest
from pydantic import ValidationError

from acheron.core.schemas import (
    CapabilitiesResponse,
    ErrorResponse,
    JobListResponse,
    JobLogEvent,
    JobProgress,
    JobResponse,
    LanguagePair,
    OutputSummary,
    StepError,
    WorkerListResponse,
    WorkerResponse,
)
from acheron.shell.api import schemas
from acheron.shell.api.schemas import ResumeJobRequest, RetryJobRequest, SubmitJobRequest, VoiceRangeRequest


def test_response_models_keep_their_public_import_path() -> None:
    assert schemas.CapabilitiesResponse is CapabilitiesResponse
    assert schemas.ErrorResponse is ErrorResponse
    assert schemas.JobListResponse is JobListResponse
    assert schemas.JobLogEvent is JobLogEvent
    assert schemas.JobProgress is JobProgress
    assert schemas.JobResponse is JobResponse
    assert schemas.LanguagePair is LanguagePair
    assert schemas.OutputSummary is OutputSummary
    assert schemas.StepError is StepError
    assert schemas.WorkerListResponse is WorkerListResponse
    assert schemas.WorkerResponse is WorkerResponse


def test_submit_rejects_unknown_fields_and_audio_voice_map_is_wire_strict() -> None:
    with pytest.raises(ValidationError):
        SubmitJobRequest.model_validate({"source_type": "epub", "unexpected": True})
    request = SubmitJobRequest(
        source_type="audio",
        source_path="audio.mp3",
        source_language="en",
        target_language="es",
        voice_map=[VoiceRangeRequest(start_chapter=1, end_chapter=2, voice="Vivian")],
    )
    assert request.voice_map[0].voice == "Vivian"


def test_submit_accepts_label() -> None:
    request = SubmitJobRequest(
        source_type="epub",
        source_path="book.epub",
        source_language="en",
        target_language="es",
        label="atlas-ch1",
    )

    assert request.label == "atlas-ch1"


def test_resume_request_accepts_selected_cache_entries() -> None:
    request = ResumeJobRequest(
        invalidate_steps=["step-47", "step-48"],
        invalidate_chapters=[47],
    )

    assert request.invalidate_steps == ["step-47", "step-48"]


def test_retry_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        RetryJobRequest.model_validate({"unexpected": "value"})


def test_public_schema_exports_include_phase_4c_models() -> None:
    from acheron.core import schemas as core_schemas

    assert {
        "ErrorResponse",
        "JobLogEvent",
        "JobProgress",
        "OutputSummary",
        "StepError",
    } <= set(core_schemas.__all__)
    assert {
        "ErrorResponse",
        "JobLogEvent",
        "JobProgress",
        "OutputSummary",
        "StepError",
    } <= set(schemas.__all__)


def test_plan_response_models_keep_their_public_import_path() -> None:
    from acheron.core.schemas import PlanResponse, PlanStepResponse
    from acheron.shell.api import schemas

    assert schemas.PlanResponse is PlanResponse
    assert schemas.PlanStepResponse is PlanStepResponse
