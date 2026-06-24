"""HTTP transport for remote workers (RunPod, HuggingFace Inference Endpoints)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from email.message import Message
from email.parser import BytesParser
from email.policy import default as default_policy
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import TypeAdapter

from acheron.core.errors import CacheMissError, WorkerError, WorkerUnavailableError
from acheron.core.interfaces import Worker
from acheron.core.models import (
    Job,
    JobMetrics,
    JobResult,
    OutputFile,
    WorkerCapabilities,
    WorkerType,
)
from acheron.shell.cache import StepCache
from acheron.shell.transports._multipart import _build_result, _materialize_artifact

if TYPE_CHECKING:
    from collections.abc import Mapping

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
        if job.job_type == WorkerType.ASR:
            return await self._execute_asr_multipart(job)
        # Existing JSON / multipart-mixed response path (unchanged).
        resp = await self._request("POST", "/execute", json=_job_to_dict(job))
        ctype = resp.headers.get("content-type", "")
        if ctype.startswith("multipart/mixed"):
            return await self._parse_multipart(resp, job.job_id)
        # Legacy JSON path — backward-compatible with existing HTTP stubs.
        return _result_adapter.validate_json(resp.content)

    async def _execute_asr_multipart(self, job: Job) -> JobResult:
        """Read the upstream extract step's audio file and POST multipart to the worker.

        The orchestrator's ``StepCache`` holds the manifest of the previous
        step's outputs; the audio file is at the path recorded in the manifest.
        We POST ``multipart/form-data`` with one ``application/json`` part (the
        ``ExecuteRequest`` envelope) + one binary part (the audio). The
        worker's response is ``multipart/mixed`` and is parsed the same way
        as the TTS path.
        """
        plan_job_id = job.job_id.rsplit("-", 1)[0]
        try:
            extract_outputs = await self._step_cache.load_outputs(plan_job_id, "extract")
        except CacheMissError as exc:
            msg = f"ASR step {job.job_id}: no extract step output for {plan_job_id}"
            raise WorkerError(msg) from exc
        audio_out = next(
            (o for o in extract_outputs if o.content_type.startswith("audio/")),
            None,
        )
        if audio_out is None:
            msg = f"ASR step {job.job_id}: no audio file in extract output"
            raise WorkerError(msg)
        audio_path = Path(audio_out.path)
        if not await asyncio.to_thread(audio_path.exists):
            msg = f"ASR step {job.job_id}: audio file missing: {audio_path}"
            raise WorkerError(msg)

        form = {
            "request": (None, json.dumps(_job_to_dict(job)).encode("utf-8"), "application/json"),
            "audio": (
                audio_path.name,
                await asyncio.to_thread(audio_path.read_bytes),
                audio_out.content_type,
            ),
        }
        resp = await self._post_multipart(form)
        ctype = resp.headers.get("content-type", "")
        if ctype.startswith("multipart/mixed"):
            return await self._parse_multipart(resp, job.job_id)
        return _result_adapter.validate_json(resp.content)

    async def _post_multipart(self, form: Mapping[str, tuple[str | None, bytes, str]]) -> httpx.Response:
        """POST multipart/form-data to /execute with the same error-conversion contract as ``_request``.

        ``httpx.ConnectError`` → ``WorkerUnavailableError``;
        ``httpx.HTTPStatusError`` → ``WorkerError``.
        """
        url = f"{self._base_url}/execute"
        try:
            if self._client is not None:
                resp = await self._client.post(url, files=form)
            else:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(url, files=form)
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
