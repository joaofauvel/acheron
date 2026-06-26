"""Internal RunPod Serverless client used by the edge container."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Protocol

import runpod

from acheron.core.errors import WorkerError

logger = logging.getLogger(__name__)


class _Run(Protocol):
    def output(self, timeout: float | None = None) -> object: ...


class _Endpoint(Protocol):
    def run(self, payload: dict[str, object]) -> _Run: ...


def _open_endpoint(endpoint_id: str, *, api_key: str, base_url: str | None = None) -> _Endpoint:
    r"""Construct a RunPod Endpoint with optional test-seam base-URL override.

    ``base_url`` (from ``WorkerSettings.runpod_base_url``) lets the in-process
    mock RunPod server in ``stubs/_sdk_base/mock_runpod.py`` intercept calls
    in tests. The real runpod SDK's ``Endpoint`` constructor doesn't take a
    ``base_url`` kwarg; if the forwarded env var is not honoured, the test
    seam is a no-op (the SDK still works against the real RunPod API).
    """
    runpod.api_key = api_key
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

    def __init__(
        self,
        *,
        api_key: str,
        endpoint_id: str,
        execution_timeout_s: float,
        base_url: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._endpoint_id = endpoint_id
        self._execution_timeout_s = execution_timeout_s
        self._base_url = base_url

    async def run(self, payload: dict[str, object]) -> RunPodJobResult:
        try:
            endpoint = await asyncio.to_thread(
                _open_endpoint, self._endpoint_id, api_key=self._api_key, base_url=self._base_url
            )
        except Exception as exc:
            logger.exception(
                "RunPod open_endpoint failed for endpoint %s: %s",
                self._endpoint_id,
                type(exc).__name__,
            )
            raise
        start = time.monotonic()
        try:
            request = await asyncio.to_thread(endpoint.run, payload)
        except Exception as exc:
            logger.exception(
                "RunPod endpoint.run failed for endpoint %s: %s",
                self._endpoint_id,
                type(exc).__name__,
            )
            raise
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
        status = output_dict.get("status")
        if status is not None and status != "COMPLETED":
            err = output_dict.get("error")
            msg = f"RunPod job did not complete (status={status}"
            if err:
                msg += f", error={err}"
            msg += f", endpoint={self._endpoint_id})"
            raise WorkerError(msg)
        artifacts = output_dict.get("artifacts", [])
        if not isinstance(artifacts, list):
            msg = f"RunPod output.artifacts must be a list, got {type(artifacts).__name__}"
            raise WorkerError(msg)
        return RunPodJobResult(artifacts=artifacts, gpu_seconds=gpu_seconds)
