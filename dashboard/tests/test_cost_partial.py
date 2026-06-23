"""Tests for the cost basis + note rendering in the cost partial."""

from __future__ import annotations

import httpx
import pytest
import respx

_ORCH_URL = "http://orchestrator:8000"


def _jobs_response(jobs: list[dict]) -> dict:
    return {"jobs": jobs}


class TestCostPartialBasis:
    @respx.mock
    @pytest.mark.asyncio
    async def test_measured_basis_renders_measured_badge(self, client) -> None:
        respx.get(f"{_ORCH_URL}/jobs").mock(
            return_value=httpx.Response(
                200,
                json=_jobs_response(
                    [
                        {
                            "job_id": "j-measured",
                            "status": "completed",
                            "total_cost": 0.42,
                            "total_duration_seconds": 100.0,
                            "completed_steps": 5,
                            "total_steps": 5,
                            "total_cost_basis": "measured",
                        }
                    ]
                ),
            )
        )
        resp = await client.get("/partials/cost")
        assert resp.status_code == 200
        assert "Measured" in resp.text
        assert "basis-measured" in resp.text
        assert "$0.42" in resp.text

    @respx.mock
    @pytest.mark.asyncio
    async def test_unknown_basis_renders_dash_not_zero(self, client) -> None:
        """Unknown cost basis must NOT render as $0.00 — that conflates
        "we don't know" with "it was free".
        """
        respx.get(f"{_ORCH_URL}/jobs").mock(
            return_value=httpx.Response(
                200,
                json=_jobs_response(
                    [
                        {
                            "job_id": "j-unknown",
                            "status": "completed",
                            "total_cost": 0.0,
                            "total_duration_seconds": 0.0,
                            "completed_steps": 4,
                            "total_steps": 4,
                            "total_cost_basis": "unknown",
                        }
                    ]
                ),
            )
        )
        resp = await client.get("/partials/cost")
        assert resp.status_code == 200
        assert "Unknown" in resp.text
        assert "basis-unknown" in resp.text
        # The cost cell must show the dash glyph, NOT "$0.00".
        assert "cost-unknown" in resp.text
        assert "$0.00" not in resp.text

    @respx.mock
    @pytest.mark.asyncio
    async def test_cached_basis_renders_cached_badge_and_runpod_note(self, client) -> None:
        respx.get(f"{_ORCH_URL}/jobs").mock(
            return_value=httpx.Response(
                200,
                json=_jobs_response(
                    [
                        {
                            "job_id": "j-cached",
                            "status": "completed",
                            "total_cost": 0.21,
                            "total_duration_seconds": 50.0,
                            "completed_steps": 3,
                            "total_steps": 3,
                            "total_cost_basis": "cached",
                        }
                    ]
                ),
            )
        )
        resp = await client.get("/partials/cost")
        assert resp.status_code == 200
        assert "Cached" in resp.text
        assert "basis-cached" in resp.text
        assert "RunPod pricing API unavailable" in resp.text

    @respx.mock
    @pytest.mark.asyncio
    async def test_static_basis_renders_static_badge(self, client) -> None:
        respx.get(f"{_ORCH_URL}/jobs").mock(
            return_value=httpx.Response(
                200,
                json=_jobs_response(
                    [
                        {
                            "job_id": "j-static",
                            "status": "completed",
                            "total_cost": 0.10,
                            "total_duration_seconds": 20.0,
                            "completed_steps": 2,
                            "total_steps": 2,
                            "total_cost_basis": "static",
                        }
                    ]
                ),
            )
        )
        resp = await client.get("/partials/cost")
        assert resp.status_code == 200
        assert "Static" in resp.text
        assert "basis-static" in resp.text

    @respx.mock
    @pytest.mark.asyncio
    async def test_missing_basis_renders_unknown_fallback(self, client) -> None:
        """Jobs without ``total_cost_basis`` (older orchestrator) should
        render as Unknown with the dash glyph — never as $0.00.
        """
        respx.get(f"{_ORCH_URL}/jobs").mock(
            return_value=httpx.Response(
                200,
                json=_jobs_response(
                    [
                        {
                            "job_id": "j-old",
                            "status": "completed",
                            "total_cost": 0.0,
                            "total_duration_seconds": 0.0,
                            "completed_steps": 1,
                            "total_steps": 1,
                        }
                    ]
                ),
            )
        )
        resp = await client.get("/partials/cost")
        assert resp.status_code == 200
        assert "Unknown" in resp.text
        assert "$0.00" not in resp.text
