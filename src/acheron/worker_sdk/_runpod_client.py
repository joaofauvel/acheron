"""Internal RunPod Serverless client used by the edge container.

The edge container (acheron-worker-edge image) is GPU-less: it serialises a
Job into RunPod's ``/run`` input, submits via the ``runpod`` Python SDK,
polls until COMPLETED/FAILED, and decodes the artifacts. ``gpu_seconds``
is the wall-time of the call — a fair proxy for billing when the serverless
endpoint schedules single-GPU pods per job.

The runpod SDK is a pinned main dep (``runpod~=1.9``, ``cryptography<47``),
so the import is at module load — no need for lazy resolution.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Protocol

import runpod

from acheron.core.errors import WorkerError


class _Run(Protocol):
    def output(self, timeout: float | None = None) -> object: ...


class _Endpoint(Protocol):
    def run(self, payload: dict[str, object]) -> _Run: ...


def _open_endpoint(endpoint_id: str, *, api_key: str) -> _Endpoint:
    r"""Construct a RunPod Endpoint with optional test-seam base-URL override.

    The ``ACHERON_WORKER__RUNPOD_BASE_URL`` env var (also forwarded to the
    runpod SDK's ``RUNPOD_BASE_URL`` if it honours it) lets the in-process
    mock RunPod server in ``stubs/_sdk_base/mock_runpod.py`` intercept calls
    in tests. The real runpod SDK's ``Endpoint`` constructor doesn't take a
    ``base_url`` kwarg; if neither env var is honoured, the test seam is a
    no-op (the SDK still works against the real RunPod API).
    """
    runpod.api_key = api_key
    base_url = os.environ.get("ACHERON_WORKER__RUNPOD_BASE_URL")
    if base_url:
        # Forward to the runpod SDK's own env var in case it supports
        # `RUNPOD_BASE_URL`. The SDK doesn't expose a direct kwarg; if
        # this is silently ignored, callers can still `monkeypatch.setattr`
        # `_open_endpoint` to inject a fake Endpoint (the standard test seam).
        os.environ.setdefault("RUNPOD_BASE_URL", base_url)
    return runpod.Endpoint(endpoint_id)  # type: ignore[no-any-return]


@dataclass(frozen=True)
class RunPodJobResult:
    """Decoded response from a finished RunPod job."""

    artifacts: list[dict[str, object]]
    gpu_seconds: float


class RunPodClient:
    """Wraps the runpod SDK with timeout + cost timing.

    Instantiated once per edge container lifespan; ``run()`` is called for
    each ``/execute`` request received from the orchestrator.
    """

    def __init__(self, *, api_key: str, endpoint_id: str, execution_timeout_s: float) -> None:
        self._api_key = api_key
        self._endpoint_id = endpoint_id
        self._execution_timeout_s = execution_timeout_s

    async def run(self, payload: dict[str, object]) -> RunPodJobResult:
        endpoint = await asyncio.to_thread(_open_endpoint, self._endpoint_id, api_key=self._api_key)
        start = time.monotonic()
        request = await asyncio.to_thread(endpoint.run, payload)
        try:
            output = await asyncio.wait_for(
                asyncio.to_thread(request.output, timeout=self._execution_timeout_s),
                timeout=self._execution_timeout_s,
            )
        except TimeoutError as exc:
            msg = f"RunPod job timed out after {self._execution_timeout_s}s (endpoint={self._endpoint_id})"
            raise TimeoutError(msg) from exc

        gpu_seconds = time.monotonic() - start
        output_dict = output if isinstance(output, dict) else {"artifacts": output}
        artifacts = output_dict.get("artifacts", [])
        if not isinstance(artifacts, list):
            msg = f"RunPod output.artifacts must be a list, got {type(artifacts).__name__}"
            raise WorkerError(msg)
        return RunPodJobResult(artifacts=artifacts, gpu_seconds=gpu_seconds)
