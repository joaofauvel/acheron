"""HTTP transport for remote workers (RunPod, HuggingFace Inference Endpoints)."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

import aiofiles
import httpx
from pydantic import TypeAdapter

from acheron.core.errors import CacheMissError, WorkerError, WorkerUnavailableError
from acheron.core.interfaces import Worker
from acheron.core.models import (
    Job,
    JobResult,
    OutputFile,
    WorkerCapabilities,
    WorkerType,
)
from acheron.shell.cache import StepCache
from acheron.shell.transports._multipart import (
    _build_result,
    _materialize_artifact,
    _parse_multipart_parts,
)

_caps_adapter = TypeAdapter(WorkerCapabilities)
_result_adapter = TypeAdapter(JobResult)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StepDispatch:
    """How an upstream step's output maps onto a multipart upload.

    Bundles the three magic strings that the legacy
    ``_execute_with_upstream_input(upstream_step=..., content_type_predicate=...,
    form_field=...)`` signature had: the upstream ``step_id`` to read from
    the cache, the predicate that picks a single ``OutputFile`` out of the
    upstream step's outputs, and the form field name used when posting
    the picked file as a multipart part.
    """

    upstream_step: str
    content_type_predicate: Callable[[str], bool]
    form_field: str


MATCHES_BY_TYPE: dict[WorkerType, StepDispatch] = {
    WorkerType.ASR: StepDispatch(
        upstream_step="extract",
        content_type_predicate=lambda c: c.startswith("audio/"),
        form_field="audio",
    ),
    WorkerType.TRANSLATION: StepDispatch(
        upstream_step="chunk",
        content_type_predicate=lambda c: c == "application/json",
        form_field="chunks",
    ),
    WorkerType.TTS: StepDispatch(
        upstream_step="chunk",
        content_type_predicate=lambda c: c == "application/json",
        form_field="chunks",
    ),
}


class HttpWorker(Worker):
    """Worker that delegates execution to a remote HTTP endpoint.

    Response dispatch is data-driven via ``Content-Type``: a ``multipart/mixed``
    body is parsed into ``OutputFile``s materialized into ``data_dir``; an
    ``application/json`` body is the legacy path that round-trips a
    pre-materialized ``JobResult`` with absolute ``OutputFile.path`` entries
    (used by the HTTP stubs until Plan 3 replaces them).

    ``data_dir`` is required — the orchestrator (which owns settings) passes
    it explicitly so the same transport works against the configured data
    dir without reading env vars directly.
    """

    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        *,
        data_dir: Path | str,
        step_cache: StepCache | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
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
        dispatch = MATCHES_BY_TYPE.get(job.job_type)
        if dispatch is not None:
            return await self._execute_with_upstream_input(job, dispatch)
        resp = await self._request("POST", "/execute", json=_job_to_dict(job))
        ctype = resp.headers.get("content-type", "")
        if ctype.startswith("multipart/mixed"):
            return await self._parse_multipart(resp, job.job_id)
        return _result_adapter.validate_json(resp.content)

    async def _execute_with_upstream_input(self, job: Job, dispatch: StepDispatch) -> JobResult:
        """Read the upstream step's matching output and POST it as multipart to the worker."""
        plan_job_id = job.job_id.rsplit("-", 1)[0]
        try:
            upstream_outputs = await self._step_cache.load_outputs(plan_job_id, dispatch.upstream_step)
        except CacheMissError as exc:
            msg = f"{job.job_type.value} step {job.job_id}: no {dispatch.upstream_step} step output for {plan_job_id}"
            raise WorkerError(msg) from exc
        matching = [o for o in upstream_outputs if dispatch.content_type_predicate(o.content_type)]
        if not matching:
            msg = f"{job.job_type.value} step {job.job_id}: no matching file in {dispatch.upstream_step} output"
            raise WorkerError(msg)
        if len(matching) > 1:
            msg = (
                f"{job.job_type.value} step {job.job_id}: multiple matching files in "
                f"{dispatch.upstream_step} output (orchestrator supports only one); "
                f"got {len(matching)} files"
            )
            raise WorkerError(msg)
        first_match = matching[0]
        file_path = Path(first_match.path)
        if not await asyncio.to_thread(file_path.exists):
            msg = f"{job.job_type.value} step {job.job_id}: file missing: {file_path}"
            raise WorkerError(msg)

        content_iter, boundary = await _stream_multipart_request(
            job=job,
            file_path=file_path,
            form_field=dispatch.form_field,
            content_type=first_match.content_type,
        )
        resp = await self._request(
            "POST",
            "/execute",
            content=content_iter,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        ctype = resp.headers.get("content-type", "")
        if ctype.startswith("multipart/mixed"):
            return await self._parse_multipart(resp, job.job_id)
        return _result_adapter.validate_json(resp.content)

    async def _parse_multipart(self, resp: httpx.Response, job_id: str) -> JobResult:
        """Parse the multipart/mixed body emitted by the SDK edge."""
        ctype = resp.headers.get("content-type", "")
        # The job_id embeds plan_id:plan_job_id-step_id; the step_id suffix is the dir.
        # Keep parity with the stub convention /data/jobs/<plan_job_id>/<step_id>/.
        plan_job_id = "-".join(job_id.split("-")[:-1]) if "-" in job_id else job_id
        step_id = job_id.rsplit("-", maxsplit=1)[-1] if "-" in job_id else "execute"
        dest_dir = self._data_dir / plan_job_id / step_id

        parts, metrics = _parse_multipart_parts(ctype, resp.content)
        outputs: list[OutputFile] = []
        for part in parts:
            out = await _materialize_artifact(
                data=part.data,
                filename=part.filename,
                content_type=part.content_type,
                dest_dir=dest_dir,
                metadata=part.metadata,
            )
            outputs.append(out)
        return _build_result(job_id=job_id, outputs=tuple(outputs), metrics=metrics)

    async def health(self) -> bool:  # noqa: D102
        try:
            resp = await self._request("GET", "/health")
        except (WorkerError, WorkerUnavailableError):
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


async def _stream_multipart_request(
    *,
    job: Job,
    file_path: Path,
    form_field: str,
    content_type: str,
    chunk_size: int = 64 * 1024,
) -> tuple[AsyncIterator[bytes], str]:
    """Build a streaming ``multipart/form-data`` body for the ASR / TRANSLATION / TTS upload.

    The body carries two parts: a ``request`` JSON envelope and the upstream
    output file streamed from disk in ``chunk_size`` bytes. The full body is
    never materialised in memory — the iterator yields header + file chunks
    on demand as httpx reads from it.

    Returns ``(body_iterator, boundary)``. The caller passes them to
    :meth:`httpx.AsyncClient.request` via ``content=`` and ``headers=``
    respectively.
    """
    boundary = f"acheron-{secrets.token_hex(16)}"
    envelope = json.dumps(_job_to_dict(job)).encode("utf-8")

    async def _gen() -> AsyncIterator[bytes]:
        yield (
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="request"\r\n'
                f"Content-Type: application/json\r\n\r\n"
            ).encode()
            + envelope
            + b"\r\n"
        )
        yield (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{form_field}"; filename="{file_path.name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode()
        async with aiofiles.open(file_path, "rb") as f:
            while chunk := await f.read(chunk_size):
                yield chunk
        yield b"\r\n"
        yield f"--{boundary}--\r\n".encode()

    return _gen(), boundary
