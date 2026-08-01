"""Pricing outage scenario — cost_basis flips to CACHED when RunPod GraphQL fails.

STORY_REF: MAINT-002
STORY_REF: MAINT-014
STORY_REF: MAINT-015
STORY_REF: OPS-005
OPS-005 — Cost basis labels rendered without explanation.

user_journey: "On-call sees a FAILED job with `cost=$0.34`, clicks the cost row,
sees a popover: 'L4 community cloud, measured 4m ago at 9:55am; rate: $0.69/hr;
cache age: 4m 22s.'"
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
from stubs._sdk_base import StubTTSHandler

from acheron.worker_sdk import WorkerSettings
from acheron.worker_sdk.app import create_worker_app
from sim import MOCK_URL, parse_multipart_metrics, patched_runpod_transports, reset_mock, reset_mock_best_effort


def _set_env() -> None:
    os.environ["ACHERON_WORKER__RUNPOD_API_KEY"] = "rk_test"
    os.environ["ACHERON_WORKER__RUNPOD_ENDPOINT_ID"] = "qwen-edge"


def _build_app(price_source: str = "runpod") -> Any:
    _set_env()
    settings = WorkerSettings(
        worker_id="tts-runpod-stub",
        orchestrator_url="http://orch:8000",
        price_source=price_source,
        price_cache_ttl_s=0.0,
    )
    return create_worker_app(
        handler=StubTTSHandler(settings),
        settings=settings,
        disable_registration=True,
        allow_unauthenticated_execute=True,
    )


async def _submit(client: httpx.AsyncClient, job_id: str) -> dict[str, Any]:
    r = await client.post(
        "/execute",
        json={
            "job_id": job_id,
            "job_type": "tts",
            "payload": {"chunks": [{"text": "hi", "chapter_id": "ch1", "sequence_id": 0}]},
            "chapter_id": "ch1",
        },
    )
    if r.status_code != 200:
        msg = f"job {job_id} returned status {r.status_code}: {r.text!r}"
        raise AssertionError(msg)
    return parse_multipart_metrics(r.headers["content-type"], r.content)


def _estimate(metrics: dict[str, Any]) -> dict[str, Any]:
    estimate = metrics.get("cost_estimate")
    if not isinstance(estimate, dict):
        msg = f"metrics did not contain a structured cost estimate: {metrics}"
        raise TypeError(msg)
    return estimate


async def _admin(toggle: str, value: Any) -> None:
    async with httpx.AsyncClient() as admin:
        r = await admin.post(f"{MOCK_URL}/_admin/control", json={"toggle": toggle, "value": value})
        if not r.json().get("ok"):
            msg = f"admin toggle {toggle} failed: {r.json()}"
            raise AssertionError(msg)


async def _run() -> int:
    try:
        async with httpx.AsyncClient() as admin:
            await reset_mock(admin)

        with patched_runpod_transports(MOCK_URL):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=_build_app()), base_url="http://test"
            ) as client:
                await _admin("pricing_api_down", value=False)
                m1 = await _submit(client, "j1")
                e1 = _estimate(m1)
                if e1.get("basis") != "unknown":
                    msg = f"job 1: expected basis=unknown, got {e1.get('basis')!r}"
                    raise AssertionError(msg)
                if e1.get("basis") == "stub":
                    raise AssertionError("fresh provider quote must not be labeled STUB")

                await _admin("pricing_api_down", value=True)
                m2 = await _submit(client, "j2")
                e2 = _estimate(m2)
                if e2.get("basis") != "cached":
                    msg = f"job 2 (pricing down): expected basis=cached, got {e2.get('basis')!r}"
                    raise AssertionError(msg)
                if not float(e2.get("cache_age_seconds") or 0.0) > 0.0:
                    msg = f"job 2: expected positive cache age, got {e2}"
                    raise AssertionError(msg)
                if e2.get("gpu_type") != "NVIDIA L4" or e2.get("rate_per_hour") != 0.69:
                    msg = f"job 2: cached GPU/rate metadata was not retained: {e2}"
                    raise AssertionError(msg)

                cold_app = _build_app()
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=cold_app), base_url="http://test"
                ) as cold_client:
                    cold = await _submit(cold_client, "cold")
                ecold = _estimate(cold)
                if ecold.get("basis") != "unknown":
                    msg = f"cold cache: expected basis=unknown, got {ecold.get('basis')!r}"
                    raise AssertionError(msg)
                if ecold.get("basis") == "stub":
                    raise AssertionError("cold provider lookup must not be labeled STUB")

                zero_app = _build_app("zero")
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=zero_app), base_url="http://test"
                ) as zero_client:
                    stub = await _submit(zero_client, "stub")
                estub = _estimate(stub)
                if estub.get("basis") != "stub" or estub.get("cost") != 0.0:
                    msg = f"explicit price_source=zero must be STUB: {estub}"
                    raise AssertionError(msg)

                await _admin("pricing_api_down", value=False)
                m3 = await _submit(client, "j3")
                e3 = _estimate(m3)
                if e3.get("basis") != "unknown":
                    msg = f"job 3 (pricing up): expected basis=unknown, got {e3.get('basis')!r}"
                    raise AssertionError(msg)
    finally:
        await reset_mock_best_effort()

    print("STORY_REF: MAINT-002 ... OK")
    return 0


def main() -> int:
    return asyncio.run(_run())
