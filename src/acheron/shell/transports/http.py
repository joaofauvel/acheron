"""HTTP transport for remote workers (RunPod, HuggingFace Inference Endpoints)."""

from __future__ import annotations

import logging
import os
from email.message import Message
from email.parser import BytesParser
from email.policy import default as default_policy
from pathlib import Path
from typing import Any

import httpx
from pydantic import TypeAdapter

from acheron.core.errors import WorkerError, WorkerUnavailableError
from acheron.core.interfaces import Worker
from acheron.core.models import (
    Job,
    JobMetrics,
    JobResult,
    OutputFile,
    WorkerCapabilities,
)
from acheron.shell.cache import StepCache
from acheron.shell.transports._multipart import _build_result, _materialize_artifact

_caps_adapter = TypeAdapter(WorkerCapabilities)
_result_adapter = TypeAdapter(JobResult)
_metrics_adapter = TypeAdapter(JobMetrics)

logger = logging.getLogger(__name__)


class HttpWorker(Worker):
    """Worker that delegates execution to a remote HTTP endpoint.

    Response dispatch is data-driven via ``Content-Type``: a ``multipart/mixed``
    body is parsed into ``OutputFile``s materialized into ``data_dir``; an
    ``application/json`` body is the legacy path that round-trips a
    pre-materialized ``JobResult`` with absolute ``OutputFile.path`` entries
    (used by the HTTP stubs until Plan 3 replaces them).
    """

    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        *,
        data_dir: Path | str | None = None,
        step_cache: StepCache | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        if data_dir is None:
            data_dir = Path(os.environ.get("ACHERON_DATA_DIR", "/data/jobs"))
        self._data_dir = Path(data_dir)
        self._step_cache = step_cache if step_cache is not None else StepCache(self._data_dir)

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
        ctype = resp.headers.get("content-type", "")
        if ctype.startswith("multipart/mixed"):
            return await self._parse_multipart(resp, job.job_id)
        # Legacy JSON path — backward-compatible with existing HTTP stubs.
        return _result_adapter.validate_json(resp.content)

    async def _parse_multipart(self, resp: httpx.Response, job_id: str) -> JobResult:
        """Parse the multipart/mixed body emitted by the SDK edge."""
        ctype = resp.headers["content-type"]
        # Extract boundary from the Content-Type header.
        boundary_part = ctype.split("boundary=", 1)[1]
        # Strip any trailing params / quotes.
        boundary = boundary_part.split(";", 1)[0].strip().strip('"')
        full_body = (
            f"Content-Type: multipart/mixed; boundary={boundary}\r\nMIME-Version: 1.0\r\n\r\n"
        ).encode() + resp.content
        # Use email.parser to split the multipart body.
        parser = BytesParser(policy=default_policy)
        message = parser.parsebytes(full_body)
        if not message.is_multipart():
            msg = f"Multipart/mixed response from {self._base_url} was not multipart"
            raise WorkerError(msg)

        # The job_id embeds plan_id:plan_job_id-step_id; the step_id suffix is the dir.
        # Keep parity with the stub convention /data/jobs/<plan_job_id>/<step_id>/.
        plan_job_id = "-".join(job_id.split("-")[:-1]) if "-" in job_id else job_id
        step_id = job_id.rsplit("-", maxsplit=1)[-1] if "-" in job_id else "execute"
        dest_dir = self._data_dir / plan_job_id / step_id

        outputs: list[OutputFile] = []
        metrics: JobMetrics | None = None

        for part in message.get_payload():
            # `message.get_payload()` is typed as the union of `str | Message | list[...]`
            # by email.message; at runtime in a multipart body it returns a list of
            # ``Message`` instances. Narrow via cast for the type-checker.
            if not isinstance(part, Message):
                continue
            part_ctype = part.get_content_type()
            if part_ctype == "application/json":
                raw = part.get_payload(decode=True)
                metrics = _metrics_adapter.validate_json(raw if isinstance(raw, bytes) else str(raw).encode("utf-8"))
                continue
            # Binary artifact part.
            filename = part.get_filename() or "artifact.bin"
            raw = part.get_payload(decode=True)
            data = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
            _ = part.get("X-Acheron-Metadata")
            out = await _materialize_artifact(
                data=data,
                filename=filename,
                content_type=part_ctype,
                dest_dir=dest_dir,
            )
            outputs.append(out)
        if metrics is None:
            metrics = JobMetrics(duration_seconds=0.0)
        return _build_result(job_id=job_id, outputs=tuple(outputs), metrics=metrics)

    async def health(self) -> bool:  # noqa: D102
        try:
            resp = await self._request("GET", "/health")
        except WorkerError, WorkerUnavailableError:
            return False
        else:
            return resp.status_code == httpx.codes.OK


def _job_to_dict(job: Job) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "job_type": job.job_type.value,
        "payload": job.payload,
        "chapter_id": job.chapter_id,
        "sequence_ids": list(job.sequence_ids) if job.sequence_ids else None,
    }
