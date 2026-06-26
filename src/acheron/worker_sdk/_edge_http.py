"""Internal FastAPI app served by the edge container.

Routes: ``GET /health``, ``GET /capabilities``, ``POST /execute``.

``/execute`` emits a ``multipart/mixed`` body: one binary part per
:class:`Artifact` returned by the handler, plus a trailing
``application/json`` part carrying ``JobMetrics`` (duration, gpu_seconds,
cost_estimate, cost_basis). On handler failure the response is a plain
JSON ``ExecuteError`` body with status 500.
"""

from __future__ import annotations

import io
import json
import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from python_multipart.multipart import MultipartParser, parse_options_header

from acheron.core.errors import WorkerError, sanitise_exc_message
from acheron.core.models import (
    Job,
    JobMetrics,
    JobResult,
    JobStatus,
    JsonValue,
    WorkerCapabilities,
    WorkerType,
)
from acheron.worker_sdk._caps import caps_to_dict
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact, FileArtifact, StreamArtifact
from acheron.worker_sdk.inputs import BytesInput, Input
from acheron.worker_sdk.pricing import PriceSource, to_cost_basis
from acheron.worker_sdk.schemas import ExecuteRequest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from acheron.worker_sdk.handler import WorkerHandler

logger = logging.getLogger(__name__)


def _job_from_request(body: ExecuteRequest) -> Job:
    return Job(
        job_id=body.job_id,
        job_type=WorkerType(body.job_type),
        payload=dict(body.payload),
        chapter_id=body.chapter_id,
        sequence_ids=tuple(body.sequence_ids) if body.sequence_ids is not None else None,
    )


def _encode_metadata(metadata: dict[str, Any]) -> str:
    return json.dumps(metadata, separators=(",", ":"))


def _decode_metadata(header: str | None) -> dict[str, JsonValue]:
    if not header:
        return {}
    parsed = json.loads(header)
    return {str(k): cast("JsonValue", v) for k, v in parsed.items()}


@dataclass(frozen=True)
class _ParsedMultipartPart:
    """A single parsed part of the ``multipart/form-data`` request body.

    Built by the streaming parser in :meth:`EdgeApp._parse_multipart_request`
    from the low-level :class:`MultipartParser` callbacks. The audio part
    is later converted to a :class:`BytesInput` so the handler API is
    unchanged from the caller's perspective.
    """

    field_name: bytes
    file_name: bytes | None
    content_type: str
    data: bytes
    metadata: dict[str, str] = field(default_factory=dict)


class _MultipartStreamState:
    """Mutable state shared across the streaming multipart parser callbacks.

    Plain attributes avoid the ``nonlocal`` chain that a closure-heavy
    implementation would otherwise need.
    """

    __slots__ = (
        "field_name",
        "file_name",
        "header_name_buf",
        "header_value_buf",
        "headers",
        "part_content_type",
        "part_data",
        "part_metadata",
        "parts",
    )

    def __init__(self) -> None:
        self.headers: dict[bytes, bytes] = {}
        self.field_name: bytes | None = None
        self.file_name: bytes | None = None
        self.part_content_type: str | None = None
        self.part_data = io.BytesIO()
        self.part_metadata: dict[str, JsonValue] = {}
        self.header_name_buf: list[bytes] = []
        self.header_value_buf: list[bytes] = []
        self.parts: list[_ParsedMultipartPart] = []

    def reset_part(self) -> None:
        self.headers.clear()
        self.field_name = None
        self.file_name = None
        self.part_content_type = None
        self.part_data.seek(0)
        self.part_data.truncate()
        self.part_metadata.clear()

    def commit_part(self) -> None:
        if self.field_name is None:
            return
        self.parts.append(
            _ParsedMultipartPart(
                field_name=self.field_name,
                file_name=self.file_name,
                content_type=self.part_content_type or "application/octet-stream",
                data=self.part_data.getvalue(),
                metadata=cast("dict[str, str]", self.part_metadata),
            )
        )


def _build_streaming_multipart_parser(boundary: bytes, state: _MultipartStreamState) -> MultipartParser:
    """Wire the low-level multipart parser callbacks to a state object."""

    def _on_part_begin() -> None:
        state.reset_part()

    def _on_header_field(data: bytes, start: int, end: int) -> None:
        state.header_name_buf.append(bytes(data[start:end]))

    def _on_header_value(data: bytes, start: int, end: int) -> None:
        state.header_value_buf.append(bytes(data[start:end]))

    def _on_header_end() -> None:
        name = b"".join(state.header_name_buf).lower()
        value = b"".join(state.header_value_buf)
        state.headers[name] = value
        state.header_name_buf.clear()
        state.header_value_buf.clear()

    def _on_headers_finished() -> None:
        disp_value = state.headers.get(b"content-disposition")
        if disp_value is not None:
            _, opts = parse_options_header(disp_value)
            name = opts.get(b"name")
            fname = opts.get(b"filename")
            state.field_name = name if isinstance(name, bytes) else name.encode("latin-1") if name else None
            state.file_name = fname if isinstance(fname, bytes) else fname.encode("latin-1") if fname else None
        ct = state.headers.get(b"content-type")
        state.part_content_type = ct.decode("latin-1") if ct else None
        meta = state.headers.get(b"x-acheron-metadata")
        if meta is not None:
            state.part_metadata.update(_decode_metadata(meta.decode("latin-1")))

    def _on_part_data(data: bytes, start: int, end: int) -> None:
        state.part_data.write(bytes(data[start:end]))

    def _on_part_end() -> None:
        state.commit_part()

    return MultipartParser(
        boundary,
        {
            "on_part_begin": _on_part_begin,
            "on_header_field": _on_header_field,
            "on_header_value": _on_header_value,
            "on_header_end": _on_header_end,
            "on_headers_finished": _on_headers_finished,
            "on_part_data": _on_part_data,
            "on_part_end": _on_part_end,
        },
    )


def _build_job_and_input(parts: list[_ParsedMultipartPart]) -> tuple[Job, BytesInput | None]:
    """Classify the parsed parts into a Job + optional audio input."""
    envelope_json: bytes | None = None
    audio_input: BytesInput | None = None
    for p in parts:
        if p.field_name == b"request":
            envelope_json = p.data
            continue
        if p.field_name == b"audio" and p.content_type.startswith("audio/"):
            audio_input = BytesInput(
                content_type=p.content_type,
                data=p.data,
                metadata=cast("dict[str, JsonValue]", p.metadata),
            )
            continue
        if p.field_name == b"chunks" or (p.field_name and p.content_type == "application/json"):
            # TRANSLATION/TTS path: the second JSON part is chunks.json;
            # the handler uses the first JSON envelope's payload, not the
            # chunks file body. Skip silently — CORR-025 regression guard.
            continue
        msg = f"Multipart part has unsupported Content-Type: {p.content_type} (expected application/json or audio/*)"
        raise WorkerError(msg)

    if envelope_json is None:
        msg = "Multipart body has no application/json part"
        raise WorkerError(msg)

    body_req = ExecuteRequest.model_validate(json.loads(envelope_json))
    return _job_from_request(body_req), audio_input


def _jobresult_to_json(result: JobResult) -> dict[str, Any]:
    """Serialise a :class:`JobResult` for the error-response body.

    Returns a plain ``dict`` so :class:`JSONResponse` can dump it as
    ``application/json``.  ``Tuple[OutputFile, ...]`` round-trips as a list
    in JSON — the orchestrator's parser expects a list.
    """
    decoded: dict[str, Any] = json.loads(result.model_dump_json().decode("utf-8"))
    return decoded


async def _build_multipart_response(
    artifacts: list[Artifact],
    metrics: JobMetrics,
) -> StreamingResponse:
    """Serialize ``artifacts`` + ``metrics`` as a streaming ``multipart/mixed`` body.

    One part per artifact with its own ``Content-Type`` + filename + metadata
    header, plus a trailing ``application/json`` part carrying ``metrics``.
    Uses :meth:`JobMetrics.model_dump_json` so ``None`` values (e.g. an
    unset ``cost_basis``) are emitted as JSON ``null`` rather than the
    string ``"unknown"`` — the latter conflates "no estimate" with
    "the API was down".

    The body is yielded chunk-by-chunk through a :class:`StreamingResponse`
    so neither the full envelope nor any single artifact is materialised
    in memory. Each artifact's ``stream()`` chunks are forwarded
    directly; no per-artifact ``bytes += chunk`` accumulator is used.
    """
    boundary = f"acheron-{uuid.uuid4().hex}"
    metrics_json = metrics.model_dump_json()

    async def _body() -> AsyncIterator[bytes]:
        for a in artifacts:
            yield (
                f"--{boundary}\r\n"
                f'Content-Disposition: attachment; filename="{a.filename}"\r\n'
                f"Content-Type: {a.content_type}\r\n"
                f"X-Acheron-Metadata: {_encode_metadata(a.metadata)}\r\n\r\n"
            ).encode()
            async for chunk in a.stream():
                yield chunk
            yield b"\r\n"
        yield f"--{boundary}\r\nContent-Type: application/json\r\n\r\n".encode() + metrics_json + b"\r\n"
        yield f"--{boundary}--\r\n".encode()

    return StreamingResponse(
        content=_body(),
        media_type=f"multipart/mixed; boundary={boundary}",
    )


class EdgeApp:
    """Container for the edge FastAPI app + handler + price source."""

    def __init__(
        self,
        *,
        handler: WorkerHandler,
        capabilities: WorkerCapabilities,
        price_source: PriceSource | None = None,
        registration_token: str | None = None,
    ) -> None:
        self.handler = handler
        self.capabilities = capabilities
        self.price_source = price_source
        self.registration_token = registration_token

        async def _verify_bearer(
            authorization: str | None = Header(default=None),
        ) -> None:
            if self.registration_token is None:
                return
            if authorization is None:
                raise HTTPException(status_code=401, detail="Missing Authorization header")
            scheme, _, provided = authorization.partition(" ")
            if scheme.lower() != "bearer" or not secrets.compare_digest(provided, self.registration_token):
                raise HTTPException(status_code=401, detail="Invalid registration token")

        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001
            await handler.startup()
            try:
                yield
            finally:
                await handler.shutdown()

        app = FastAPI(title="acheron-worker-edge", lifespan=lifespan)

        @app.get("/health")
        async def health() -> dict[str, str]:
            return {"status": "ok"}

        @app.get("/capabilities")
        async def get_capabilities() -> dict[str, Any]:
            return caps_to_dict(self.capabilities)

        @app.post("/execute", dependencies=[Depends(_verify_bearer)])
        async def execute(request: Request) -> Response:
            """Accept either ``application/json`` (legacy / TTS) or ``multipart/form-data`` (8b ASR)."""
            ctype = request.headers.get("content-type", "")
            if ctype.startswith("multipart/"):
                return await self._run_execute_multipart(request)
            body = ExecuteRequest.model_validate(await request.json())
            return await self._run_execute(body)

        self.app = app

    async def _run_execute_multipart(self, request: Request) -> Response:
        """Parse a ``multipart/form-data`` body, build Job + Input, dispatch to handler."""
        try:
            job, input_obj = await self._parse_multipart_request(request)
        except (WorkerError, ValueError, KeyError) as exc:
            parser_error: WorkerError
            if isinstance(exc, WorkerError):
                parser_error = exc
            else:
                msg = f"Malformed multipart envelope: {exc}"
                parser_error = WorkerError(msg)
                parser_error.__cause__ = exc
            # Mirror _dispatch's error contract: return a JobResult-shaped body
            # so the orchestrator's TypeAdapter(JobResult).validate_json parser
            # sees a valid failure record rather than an opaque 5xx.
            result = JobResult(
                job_id="<unknown>",
                status=JobStatus.FAILED,
                outputs=(),
                metrics=JobMetrics(duration_seconds=0.0, cost_basis=None),
                error=sanitise_exc_message(parser_error),
            )
            return JSONResponse(
                status_code=500,
                content=_jobresult_to_json(result),
            )
        return await self._dispatch(job, input_obj)

    async def _parse_multipart_request(self, request: Request) -> tuple[Job, Input | None]:
        """Parse the multipart body into a Job + optional Input. Raises WorkerError on malformed input.

        Streams the request body in chunks via python-multipart's
        :class:`MultipartParser` low-level API so the body is never
        materialised in memory as a single ``bytes`` blob. Per-part
        ``X-Acheron-Metadata`` headers are captured (preserving the
        CORR-024 contract) by reading the raw header callbacks.
        """
        ctype = request.headers.get("content-type", "")
        if "boundary=" not in ctype:
            msg = "Multipart body is missing boundary"
            raise WorkerError(msg)
        boundary = ctype.split("boundary=", 1)[1].split(";", 1)[0].strip().strip('"').encode("latin-1")
        state = _MultipartStreamState()
        parser = _build_streaming_multipart_parser(boundary, state)
        async for chunk in request.stream():
            parser.write(chunk)
        parser.finalize()
        return _build_job_and_input(state.parts)

    async def _run_execute(self, body: ExecuteRequest) -> Response:
        job = _job_from_request(body)
        return await self._dispatch(job, None)

    async def _dispatch(self, job: Job, input_obj: Input | None) -> Response:
        """Common dispatch path: invoke the handler, build metrics, return multipart response."""
        start = time.monotonic()
        try:
            artifacts: list[Artifact] = await self.handler.handle(job, input_obj)
        except Exception as exc:
            duration = time.monotonic() - start
            logger.exception("%s handler failed for job %s", type(self.handler).__name__, job.job_id)
            result = JobResult(
                job_id=job.job_id,
                status=JobStatus.FAILED,
                outputs=(),
                metrics=JobMetrics(duration_seconds=duration, cost_basis=None),
                error=sanitise_exc_message(exc),
            )
            return JSONResponse(
                status_code=500,
                content=_jobresult_to_json(result),
            )
        duration = time.monotonic() - start
        gpu_seconds = duration
        if self.price_source is not None:
            est = await self.price_source.estimate(gpu_seconds)
            cost = est.cost
            basis = to_cost_basis(est)
        else:
            cost = None
            basis = None
        metrics = JobMetrics(
            duration_seconds=duration,
            gpu_seconds=gpu_seconds,
            cost_estimate=cost,
            cost_basis=basis,
        )
        return await _build_multipart_response(artifacts, metrics)


# Re-export variants for callers that want to type-narrow before passing to
# EdgeApp — they're the only Artifact subclasses today.
__all__ = [
    "BytesArtifact",
    "EdgeApp",
    "FileArtifact",
    "StreamArtifact",
]
