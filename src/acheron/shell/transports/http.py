"""HTTP transport for remote workers (RunPod, HuggingFace Inference Endpoints)."""

from __future__ import annotations

import logging
from typing import Any, cast

import httpx
from pydantic import TypeAdapter

from acheron.core.errors import WorkerError, WorkerUnavailableError
from acheron.core.interfaces import StreamingWorker
from acheron.core.models import (
    BatchJob,
    BatchStatus,
    Job,
    JobResult,
    WorkerCapabilities,
)

_caps_adapter = TypeAdapter(WorkerCapabilities)
_result_adapter = TypeAdapter(JobResult)
_batch_status_adapter = TypeAdapter(BatchStatus)

logger = logging.getLogger(__name__)


class HttpWorker(StreamingWorker):
    """Worker that delegates execution to a remote HTTP endpoint."""

    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        poll_interval: float = 5.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._poll_interval = poll_interval

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:  # noqa: ANN401
        """Make an HTTP request, raising WorkerError on failure."""
        url = f"{self._base_url}{path}"
        try:
            if self._client is not None:
                resp = await self._client.request(method, url, **kwargs)
            else:
                async with httpx.AsyncClient() as client:
                    resp = await client.request(method, url, **kwargs)
            resp.raise_for_status()
        except httpx.ConnectError as exc:
            msg = f"Worker unreachable: {self._base_url}"
            raise WorkerUnavailableError(msg) from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            msg = f"Worker error {exc.response.status_code}: {detail}"
            raise WorkerError(msg) from exc
        else:
            return resp

    async def capabilities(self) -> WorkerCapabilities:  # noqa: D102
        resp = await self._request("GET", "/capabilities")
        return _caps_adapter.validate_json(resp.content)

    async def execute(self, job: Job) -> JobResult:  # noqa: D102
        resp = await self._request("POST", "/execute", json=_job_to_dict(job))
        return _result_adapter.validate_json(resp.content)

    async def health(self) -> bool:  # noqa: D102
        try:
            resp = await self._request("GET", "/health")
        except WorkerError, WorkerUnavailableError:
            return False
        else:
            return resp.status_code == httpx.codes.OK

    async def submit_batch(self, batch: BatchJob) -> str:  # noqa: D102
        resp = await self._request(
            "POST",
            "/submit-batch",
            json={"batch_id": batch.batch_id, "jobs": [_job_to_dict(j) for j in batch.jobs]},
        )
        return cast("str", resp.json()["batch_handle"])

    async def poll_batch(self, batch_handle: str) -> BatchStatus:  # noqa: D102
        resp = await self._request("GET", f"/poll/{batch_handle}")
        return _batch_status_adapter.validate_json(resp.content)

    async def collect_results(self, batch_handle: str) -> tuple[JobResult, ...]:  # noqa: D102
        resp = await self._request("GET", f"/poll/{batch_handle}")
        status = _batch_status_adapter.validate_json(resp.content)
        return status.results


def _job_to_dict(job: Job) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "job_type": job.job_type.value,
        "payload": job.payload,
        "chapter_id": job.chapter_id,
        "sequence_ids": list(job.sequence_ids) if job.sequence_ids else None,
    }
