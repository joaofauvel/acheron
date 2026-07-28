"""Tests for the Acheron HTTP client."""

import httpx
import pytest
import respx

from acheron.api_client import AcheronClient


@pytest.mark.asyncio
@respx.mock
async def test_submit_job_round_trips_warnings() -> None:
    warning = "BOOTING TTS workers: tts-1 (3s elapsed); cold start typically takes 30\u201390 seconds."
    respx.post("http://test/jobs").mock(
        return_value=httpx.Response(
            201,
            json={"job_id": "job-1", "status": "running", "warnings": [warning]},
        )
    )

    result = await AcheronClient("http://test").submit_job(
        source_type="epub",
        source_path="/input/book.epub",
        source_language="en",
        target_language="es",
    )

    assert result.job_id == "job-1"
    assert result.warnings == [warning]
