"""GPU switch scenario — price updates after PATCH /endpoints/{id}.

STORY_REF: MAINT-002
STORY_REF: MAINT-014
MAINT-014 — `uninterruptablePrice` is the lowest available rate, not what was paid.

user_journey: "On-call changes the GPU on the RunPod endpoint, sees the dashboard
reflect the new price within `cache_ttl_s`."
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx

from acheron.core.models import Job, WorkerCapabilities, WorkerType
from acheron.worker_sdk import WorkerSettings
from acheron.worker_sdk.app import create_worker_app
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler
from sim import MOCK_URL, parse_multipart_metrics, patched_runpod_transports, reset_mock, reset_mock_best_effort


class SlowTTSHandler(WorkerHandler):
    """TTS handler that sleeps 0.5s so the cost is large enough to assert on."""

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"en"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav"}),
            max_payload_bytes=None,
            batch_capable=True,
            model_source=None,
        )

    async def handle(self, job: Job, input: Any = None) -> list[Artifact]:  # noqa: A002
        time.sleep(0.5)
        return [BytesArtifact(filename="out.wav", content_type="audio/wav", data=b"\x00" * 100, metadata={})]


def _set_env() -> None:
    os.environ["ACHERON_WORKER__RUNPOD_API_KEY"] = "rk_test"
    os.environ["ACHERON_WORKER__RUNPOD_ENDPOINT_ID"] = "qwen-edge"


def _build_app() -> Any:
    _set_env()
    settings = WorkerSettings(
        worker_id="tts-runpod-stub",
        orchestrator_url="http://orch:8000",
        price_source="runpod",
        price_cache_ttl_s=1.0,
        secure_cloud=True,
    )
    return create_worker_app(handler=SlowTTSHandler(), settings=settings, disable_registration=True)


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
        response = await admin.post(f"{MOCK_URL}/_admin/control", json={"toggle": toggle, "value": value})
        response.raise_for_status()


def _estimate(metrics: dict[str, Any]) -> dict[str, Any]:
    estimate = metrics.get("cost_estimate")
    if not isinstance(estimate, dict):
        msg = f"metrics did not contain a structured cost estimate: {metrics}"
        raise TypeError(msg)
    return estimate


def _assert_quote(metrics: dict[str, Any], *, gpu_type: str, rate: float) -> None:
    estimate = _estimate(metrics)
    if estimate.get("basis") != "unknown":
        msg = f"fresh provider quote must remain unknown, got {estimate}"
        raise AssertionError(msg)
    if estimate.get("gpu_type") != gpu_type or estimate.get("rate_per_hour") != rate:
        msg = f"expected quote metadata gpu={gpu_type!r}, rate={rate}, got {estimate}"
        raise AssertionError(msg)
    if metrics.get("gpu_seconds", 0.0) <= 0.0:
        msg = f"expected measurable GPU seconds, got {metrics}"
        raise AssertionError(msg)


async def _run() -> int:
    try:
        async with httpx.AsyncClient() as admin:
            await reset_mock(admin)

        with patched_runpod_transports(MOCK_URL):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=_build_app()), base_url="http://test"
            ) as client:
                m1 = await _submit(client, "j1")
                _assert_quote(m1, gpu_type="NVIDIA L4", rate=1.39)

                async with httpx.AsyncClient() as admin:
                    response = await admin.patch(
                        f"{MOCK_URL}/endpoints/qwen-edge",
                        json={"gpu_id": "NVIDIA A40"},
                    )
                    response.raise_for_status()
                time.sleep(1.5)

                m2 = await _submit(client, "j2")
                _assert_quote(m2, gpu_type="NVIDIA A40", rate=2.49)

                await _admin("pricing_api_down", value=True)
                time.sleep(1.5)
                m3 = await _submit(client, "j3")
                estimate3 = _estimate(m3)
                if estimate3.get("basis") != "cached":
                    msg = f"outage estimate must be CACHED, got {estimate3}"
                    raise AssertionError(msg)
                if estimate3.get("gpu_type") != "NVIDIA A40" or estimate3.get("rate_per_hour") != 2.49:
                    msg = f"outage must retain refreshed A40 metadata, got {estimate3}"
                    raise AssertionError(msg)
                if not float(estimate3.get("cache_age_seconds") or 0.0) > 0.0:
                    msg = f"outage estimate must expose positive cache age, got {estimate3}"
                    raise AssertionError(msg)
                if estimate3.get("cost") is None or estimate3.get("cost") <= 0.0:
                    msg = f"outage estimate must retain a positive cached cost, got {estimate3}"
                    raise AssertionError(msg)
    finally:
        await reset_mock_best_effort()

    print("STORY_REF: MAINT-002 ... OK")
    return 0


def main() -> int:
    return asyncio.run(_run())
