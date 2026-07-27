"""GPU switch scenario — price updates after PATCH /endpoints/{id}.

STORY_REF: MAINT-002
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


def _implied_rate(metrics: dict[str, Any]) -> float:
    cost = metrics.get("cost_estimate")
    gpu_s = metrics.get("gpu_seconds") or 0.0
    if not cost or not gpu_s:
        msg = f"cannot compute implied rate from metrics: {metrics}"
        raise AssertionError(msg)
    return float(cost) * 3600.0 / float(gpu_s)


async def _run() -> int:
    try:
        async with httpx.AsyncClient() as admin:
            await reset_mock(admin)

        with patched_runpod_transports(MOCK_URL):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=_build_app()), base_url="http://test"
            ) as client:
                m1 = await _submit(client, "j1")
                rate1 = _implied_rate(m1)
                if abs(rate1 - 1.39) > 0.01:
                    msg = f"job 1 (L4 secure): expected rate ~1.39, got {rate1:.4f}"
                    raise AssertionError(msg)

                async with httpx.AsyncClient() as admin:
                    response = await admin.patch(
                        f"{MOCK_URL}/endpoints/qwen-edge",
                        json={"gpu_id": "NVIDIA A40"},
                    )
                    response.raise_for_status()
                time.sleep(1.5)

                m2 = await _submit(client, "j2")
                rate2 = _implied_rate(m2)
                if abs(rate2 - 2.49) > 0.01:
                    msg = f"job 2 (A40 secure): expected rate ~2.49, got {rate2:.4f}"
                    raise AssertionError(msg)
    finally:
        await reset_mock_best_effort()

    print("STORY_REF: MAINT-002 ... OK")
    return 0


def main() -> int:
    return asyncio.run(_run())
