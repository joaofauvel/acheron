"""Tests for the request-schema module's response-model re-exports."""

from acheron.core.schemas import (
    CapabilitiesResponse,
    JobListResponse,
    JobResponse,
    LanguagePair,
    WorkerListResponse,
    WorkerResponse,
)
from acheron.shell.api import schemas


def test_response_models_keep_their_public_import_path() -> None:
    assert schemas.CapabilitiesResponse is CapabilitiesResponse
    assert schemas.JobListResponse is JobListResponse
    assert schemas.JobResponse is JobResponse
    assert schemas.LanguagePair is LanguagePair
    assert schemas.WorkerListResponse is WorkerListResponse
    assert schemas.WorkerResponse is WorkerResponse
