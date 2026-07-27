"""Pricing outage scenario — cost_basis flips to CACHED when RunPod GraphQL fails.

STORY_REF: MAINT-002
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
from sim import (
    MOCK_URL,
    parse_multipart_metrics,
    patch_pricing_transport,
    patch_runpod_endpoint,
    reset_mock,
    restore_pricing_transport,
    restore_runpod_endpoint,
)


def _set_env() -> None:
    os.environ["ACHERON_WORKER__RUNPOD_API_KEY"] = "rk_test"
    os.environ["ACHERON_WORKER__RUNPOD_ENDPOINT_ID"] = "qwen-edge"


def _build_app() -> Any:
    _set_env()
    settings = WorkerSettings(
        worker_id="tts-runpod-stub",
        orchestrator_url="http://orch:8000",
        price_source="runpod",
        price_cache_ttl_s=0.0,
    )
    return create_worker_app(handler=StubTTSHandler(settings), settings=settings, disable_registration=True)


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


async def _admin(toggle: str, value: Any) -> None:
    async with httpx.AsyncClient() as admin:
        r = await admin.post(f"{MOCK_URL}/_admin/control", json={"toggle": toggle, "value": value})
        if not r.json().get("ok"):
            msg = f"admin toggle {toggle} failed: {r.json()}"
            raise AssertionError(msg)


async def _run() -> int:
    async with httpx.AsyncClient() as admin:
        await reset_mock(admin)

    original_open = patch_runpod_endpoint(MOCK_URL)
    original_init = patch_pricing_transport(MOCK_URL)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_build_app()), base_url="http://test") as client:
            await _admin("pricing_api_down", value=False)
            m1 = await _submit(client, "j1")
            if m1.get("cost_basis") != "measured":
                msg = f"job 1: expected cost_basis=measured, got {m1.get('cost_basis')!r}"
                raise AssertionError(msg)

            await _admin("pricing_api_down", value=True)
            m2 = await _submit(client, "j2")
            if m2.get("cost_basis") != "cached":
                msg = f"job 2 (pricing down): expected cost_basis=cached, got {m2.get('cost_basis')!r}"
                raise AssertionError(msg)

            await _admin("pricing_api_down", value=False)
            m3 = await _submit(client, "j3")
            if m3.get("cost_basis") != "measured":
                msg = f"job 3 (pricing up): expected cost_basis=measured, got {m3.get('cost_basis')!r}"
                raise AssertionError(msg)
    finally:
        restore_runpod_endpoint(original_open)
        restore_pricing_transport(original_init)

    print("STORY_REF: MAINT-002 ... OK")
    return 0


def main() -> int:
    return asyncio.run(_run())
