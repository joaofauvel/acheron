"""Internal FastAPI app served by the edge container."""

from __future__ import annotations

import json
import logging
import re
import secrets
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, BinaryIO, cast

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError
from python_multipart.multipart import MultipartParser, parse_options_header

from acheron.core.errors import WorkerError, sanitise_exc_message, sanitise_public_message
from acheron.core.models import (
    CostBasis,
    CostEstimate,
    Job,
    JobMetrics,
    JobResult,
    JobStatus,
    JsonValue,
    WorkerCapabilities,
    WorkerType,
)
from acheron.worker_sdk._caps import public_caps_to_dict
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact, FileArtifact, StreamArtifact
from acheron.worker_sdk.inputs import Input, StreamInput
from acheron.worker_sdk.pricing import PriceSource, to_cost_basis
from acheron.worker_sdk.schemas import ExecuteRequest
from acheron.worker_sdk.token_auth import EnvironmentOrFileTokenProvider, RegistrationTokenProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

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


_PUBLIC_ARTIFACT_METADATA_KEYS = frozenset(
    {
        "chapter_id",
        "sequence_id",
        "source_language",
        "target_language",
        "model",
        "speaker",
        "speaker_id",
        "speaker_hint",
        "voice",
    }
)
_MAX_METADATA_STRING_LENGTH = 256
_MAX_METADATA_HEADER_BYTES = 8 * 1024
_MAX_METADATA_ITEMS = 32
_MAX_METADATA_DEPTH = 4
_MAX_MULTIPART_PARTS = 64
_MAX_MULTIPART_FIELD_BYTES = 256
_MAX_MULTIPART_FILENAME_BYTES = 255
_MAX_MULTIPART_CONTENT_TYPE_BYTES = 256
_MAX_MULTIPART_SPOOL_MEMORY_BYTES = 1024 * 1024
_MULTIPART_CONTROL_CHARACTER_LIMIT = 32
_MULTIPART_DELETE_CHARACTER = 127
_METADATA_CONTROL_LIMIT = 32
_METADATA_DELETE_CHARACTER = 127
# Edge execute requests and streamed responses share a bounded 64 MiB envelope.
_MAX_EXECUTE_BODY_BYTES = 64 * 1024 * 1024
_MAX_EXECUTE_RESPONSE_BYTES = 64 * 1024 * 1024


def _new_part_data() -> BinaryIO:
    return cast(
        "BinaryIO",
        tempfile.SpooledTemporaryFile(max_size=_MAX_MULTIPART_SPOOL_MEMORY_BYTES, mode="w+b"),
    )


class PayloadTooLargeError(WorkerError):
    """An execute request exceeded the edge's bounded request size."""


def _encode_metadata(metadata: dict[str, JsonValue]) -> str:
    """Encode only bounded, known artifact metadata for the public header."""
    safe: dict[str, JsonValue] = {}
    for key, value in metadata.items():
        if key not in _PUBLIC_ARTIFACT_METADATA_KEYS:
            continue
        if isinstance(value, (bool, int)):
            safe[key] = value
            continue
        if not isinstance(value, str) or len(value) > _MAX_METADATA_STRING_LENGTH:
            continue
        if any(ord(char) < _METADATA_CONTROL_LIMIT or ord(char) == _METADATA_DELETE_CHARACTER for char in value):
            continue
        if sanitise_public_message(value, fallback="__invalid_metadata__") == "__invalid_metadata__":
            continue
        safe[key] = value
    return json.dumps(safe, separators=(",", ":"))


def _decode_metadata(header: str | None) -> dict[str, JsonValue]:  # noqa: C901
    if not header:
        return {}
    if len(header.encode("utf-8")) > _MAX_METADATA_HEADER_BYTES:
        raise TypeError("X-Acheron-Metadata is too large")
    try:
        parsed = json.loads(header)
    except (TypeError, ValueError, RecursionError) as exc:
        raise TypeError("X-Acheron-Metadata is invalid") from exc

    def validate(value: object, depth: int = 0) -> None:  # noqa: C901
        if depth > _MAX_METADATA_DEPTH:
            raise TypeError("X-Acheron-Metadata is too deeply nested")
        if isinstance(value, dict):
            if len(value) > _MAX_METADATA_ITEMS:
                raise TypeError("X-Acheron-Metadata has too many fields")
            for key, item in value.items():
                if not isinstance(key, str) or len(key) > _MAX_METADATA_STRING_LENGTH:
                    raise TypeError("X-Acheron-Metadata has an invalid field")
                validate(item, depth + 1)
        elif isinstance(value, list):
            if len(value) > _MAX_METADATA_ITEMS:
                raise TypeError("X-Acheron-Metadata has too many items")
            for item in value:
                validate(item, depth + 1)
        elif isinstance(value, str):
            if len(value) > _MAX_METADATA_STRING_LENGTH:
                raise TypeError("X-Acheron-Metadata has an oversized value")
        elif value is not None and not isinstance(value, (bool, int, float)):
            raise TypeError("X-Acheron-Metadata has an invalid value")

    validate(parsed)
    if not isinstance(parsed, dict):
        raise TypeError("X-Acheron-Metadata must contain a JSON object")
    return cast("dict[str, JsonValue]", parsed)


@dataclass(frozen=True)
class _ParsedMultipartPart:
    """A single parsed part backed by a bounded spooled file."""

    field_name: bytes
    file_name: bytes | None
    content_type: str
    data: BinaryIO
    metadata: dict[str, JsonValue] = field(default_factory=dict)


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
        "saw_end",
        "total_bytes",
    )

    def __init__(self) -> None:
        self.headers: dict[bytes, bytes] = {}
        self.field_name: bytes | None = None
        self.file_name: bytes | None = None
        self.part_content_type: str | None = None
        self.part_data = _new_part_data()
        self.part_metadata: dict[str, JsonValue] = {}
        self.total_bytes = 0
        self.header_name_buf = bytearray()
        self.header_value_buf = bytearray()
        self.parts: list[_ParsedMultipartPart] = []
        self.saw_end = False

    def reset_part(self) -> None:
        self.headers.clear()
        self.saw_end = False
        self.field_name = None
        self.file_name = None
        self.part_content_type = None
        self.part_data.close()
        self.part_data = _new_part_data()
        self.part_metadata.clear()

    def commit_part(self) -> None:
        if self.field_name is None:
            return
        if len(self.parts) >= _MAX_MULTIPART_PARTS:
            raise PayloadTooLargeError("execute request contains too many multipart parts")
        self.part_data.seek(0)
        self.parts.append(
            _ParsedMultipartPart(
                field_name=self.field_name,
                file_name=self.file_name,
                content_type=self.part_content_type or "application/octet-stream",
                data=self.part_data,
                metadata=dict(self.part_metadata),
            )
        )
        self.part_data = _new_part_data()

    def close(self) -> None:
        """Close all spooled part data, including the current part."""
        self.part_data.close()
        for part in self.parts:
            part.data.close()


def _build_streaming_multipart_parser(boundary: bytes, state: _MultipartStreamState) -> MultipartParser:  # noqa: C901
    """Wire the low-level multipart parser callbacks to a state object."""

    def _on_part_begin() -> None:
        state.reset_part()

    def _on_header_field(data: bytes, start: int, end: int) -> None:
        if len(state.header_name_buf) + end - start > _MAX_MULTIPART_FIELD_BYTES:
            raise WorkerError("multipart header is oversized")
        state.header_name_buf.extend(data[start:end])

    def _on_header_value(data: bytes, start: int, end: int) -> None:
        if len(state.header_value_buf) + end - start > _MAX_MULTIPART_FIELD_BYTES:
            raise WorkerError("multipart header is oversized")
        state.header_value_buf.extend(data[start:end])

    def _on_header_end() -> None:
        name = bytes(state.header_name_buf).lower()
        value = bytes(state.header_value_buf)
        if not name or len(name) > _MAX_MULTIPART_FIELD_BYTES or len(value) > _MAX_MULTIPART_FIELD_BYTES:
            raise WorkerError("multipart header is oversized")
        if any(
            byte < _MULTIPART_CONTROL_CHARACTER_LIMIT or byte == _MULTIPART_DELETE_CHARACTER for byte in name + value
        ):
            raise WorkerError("multipart header contains control characters")
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
        if state.field_name is None or len(state.field_name) > _MAX_MULTIPART_FIELD_BYTES:
            raise WorkerError("multipart field name is invalid")
        if state.file_name is not None and len(state.file_name) > _MAX_MULTIPART_FILENAME_BYTES:
            raise WorkerError("multipart filename is oversized")
        ct = state.headers.get(b"content-type")
        if ct is not None and len(ct) > _MAX_MULTIPART_CONTENT_TYPE_BYTES:
            raise WorkerError("multipart content type is oversized")
        state.part_content_type = ct.decode("latin-1") if ct else None
        meta = state.headers.get(b"x-acheron-metadata")
        if meta is not None:
            state.part_metadata.update(_decode_metadata(meta.decode("latin-1")))

    def _on_part_data(data: bytes, start: int, end: int) -> None:
        chunk_size = end - start
        if state.total_bytes + chunk_size > _MAX_EXECUTE_BODY_BYTES:
            raise PayloadTooLargeError("execute request exceeds the maximum body size")
        state.total_bytes += chunk_size
        state.part_data.write(bytes(data[start:end]))

    def _on_part_end() -> None:
        state.commit_part()

    def _on_end() -> None:
        state.saw_end = True

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
            "on_end": _on_end,
        },
    )


async def _consume_multipart_request(request: Request, parser: MultipartParser) -> None:
    """Feed bounded request chunks into the multipart parser."""
    total_bytes = 0
    async for chunk in request.stream():
        if total_bytes + len(chunk) > _MAX_EXECUTE_BODY_BYTES:
            raise PayloadTooLargeError("execute request exceeds the maximum body size")
        total_bytes += len(chunk)
        parser.write(chunk)


def _require_multipart_end(state: _MultipartStreamState) -> None:
    if not state.saw_end:
        raise WorkerError("Multipart body is missing its closing boundary")


async def _stream_part_data(data: BinaryIO) -> AsyncIterator[bytes]:
    """Yield a spooled multipart part without materializing the full payload."""
    data.seek(0)
    while chunk := data.read(64 * 1024):
        yield chunk


def _part_producer(data: BinaryIO) -> Callable[[], AsyncIterator[bytes]]:
    def produce() -> AsyncIterator[bytes]:
        return _stream_part_data(data)

    return produce


def _part_input(part: _ParsedMultipartPart) -> StreamInput:
    return StreamInput(
        content_type=part.content_type,
        producer=_part_producer(part.data),
        metadata=part.metadata,
    )


def _build_job_and_input(parts: list[_ParsedMultipartPart]) -> tuple[Job, StreamInput | None]:
    """Classify the parsed parts into a Job and optional worker input."""
    envelope_json: bytes | None = None
    input_obj: StreamInput | None = None
    for part in parts:
        content_type = part.content_type.split(";", 1)[0].strip().lower()
        if content_type == "application/json":
            if envelope_json is None:
                part.data.seek(0)
                envelope_json = part.data.read()
                continue
            if input_obj is not None:
                msg = "Multipart body contains more than one worker input part"
                raise WorkerError(msg)
            input_obj = _part_input(part)
            continue
        if content_type.startswith("audio/"):
            if input_obj is not None:
                msg = "Multipart body contains more than one worker input part"
                raise WorkerError(msg)
            input_obj = _part_input(part)
            continue
        msg = f"Multipart part has unsupported Content-Type: {part.content_type} (expected application/json or audio/*)"
        raise WorkerError(msg)

    if envelope_json is None:
        msg = "Multipart body has no application/json part"
        raise WorkerError(msg)

    body_req = ExecuteRequest.model_validate(json.loads(envelope_json))
    return _job_from_request(body_req), input_obj


def _jobresult_to_json(result: JobResult) -> dict[str, JsonValue]:
    """Serialise a :class:`JobResult` for the error-response body.

    Returns a plain ``dict`` so :class:`JSONResponse` can dump it as
    ``application/json``.  ``Tuple[OutputFile, ...]`` round-trips as a list
    in JSON — the orchestrator's parser expects a list.
    """
    decoded: dict[str, JsonValue] = json.loads(result.model_dump_json().decode("utf-8"))
    job_id = result.job_id
    safe_job_id = sanitise_public_message(job_id, fallback="<unknown>")
    if any(ord(char) < _METADATA_CONTROL_LIMIT or ord(char) == _METADATA_DELETE_CHARACTER for char in job_id):
        safe_job_id = "<unknown>"
    decoded["job_id"] = safe_job_id
    return decoded


def _malformed_execute_response() -> JSONResponse:
    """Return the public structured error for an invalid JSON execute request."""
    result = JobResult(
        job_id="<unknown>",
        status=JobStatus.FAILED,
        outputs=(),
        metrics=JobMetrics(duration_seconds=0.0),
        error="Malformed execute request",
    )
    return JSONResponse(status_code=500, content=_jobresult_to_json(result))


_SAFE_ARTIFACT_MIME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+/[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_SAFE_GPU_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+:-]{0,127}$")
_MAX_ARTIFACT_FILENAME_LENGTH = 255
_MAX_ARTIFACT_MIME_LENGTH = 256
_MAX_ARTIFACT_COUNT = 64
_CONTROL_CHARACTER_LIMIT = 32
_DELETE_CHARACTER = 127


def _safe_artifact_filename(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_ARTIFACT_FILENAME_LENGTH:
        return "output.bin"
    if (
        any(ord(char) < _CONTROL_CHARACTER_LIMIT or ord(char) == _DELETE_CHARACTER for char in value)
        or any(delimiter in value for delimiter in ("\r", "\n", '"', "\\", ";", ":"))
        or "/" in value
        or ".." in value
        or value in {".", ".."}
    ):
        return "output.bin"
    return value


def _safe_artifact_content_type(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) > _MAX_ARTIFACT_MIME_LENGTH
        or any(ord(char) < _CONTROL_CHARACTER_LIMIT or ord(char) == _DELETE_CHARACTER for char in value)
    ):
        return "application/octet-stream"
    if _SAFE_ARTIFACT_MIME_RE.fullmatch(value) is None:
        return "application/octet-stream"
    return value


async def _build_multipart_response(  # noqa: C901
    artifacts: list[Artifact],
    metrics: JobMetrics,
) -> StreamingResponse:
    """Serialize ``artifacts`` + ``metrics`` as a streaming ``multipart/mixed`` body.

    One part per artifact with its own ``Content-Type`` + filename + metadata
    header, plus a trailing ``application/json`` part carrying ``metrics``.
    Uses :meth:`JobMetrics.model_dump_json` so ``None`` values (e.g. an
    unset ``cost_estimate``) are emitted as JSON ``null`` rather than the
    string ``"unknown"`` — the latter conflates "no estimate" with
    "the API was down".

    The body is yielded chunk-by-chunk through a :class:`StreamingResponse`
    so neither the full envelope nor any single artifact is materialised
    in memory. Each artifact's ``stream()`` chunks are forwarded
    directly; no per-artifact ``bytes += chunk`` accumulator is used.
    """
    if len(artifacts) > _MAX_ARTIFACT_COUNT:
        raise PayloadTooLargeError("worker returned too many artifacts")
    boundary = f"acheron-{uuid.uuid4().hex}"
    metrics_json = metrics.model_dump_json()

    async def _body() -> AsyncIterator[bytes]:
        emitted = 0

        async def emit(chunk: bytes) -> AsyncIterator[bytes]:
            nonlocal emitted
            emitted += len(chunk)
            if emitted > _MAX_EXECUTE_RESPONSE_BYTES:
                raise PayloadTooLargeError("worker output exceeds the supported response limit")
            yield chunk

        for a in artifacts:
            filename = _safe_artifact_filename(a.filename)
            content_type = _safe_artifact_content_type(a.content_type)
            header = (
                f"--{boundary}\r\n"
                f'Content-Disposition: attachment; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n"
                f"X-Acheron-Metadata: {_encode_metadata(a.metadata)}\r\n\r\n"
            ).encode()
            async for output in emit(header):
                yield output
            async for chunk in a.stream():
                async for output in emit(chunk):
                    yield output
            async for output in emit(b"\r\n"):
                yield output
        trailer = f"--{boundary}\r\nContent-Type: application/json\r\n\r\n".encode() + metrics_json + b"\r\n"
        async for output in emit(trailer):
            yield output
        async for output in emit(f"--{boundary}--\r\n".encode()):
            yield output

    return StreamingResponse(
        content=_body(),
        media_type=f"multipart/mixed; boundary={boundary}",
    )


class EdgeApp:
    """Container for the edge FastAPI app + handler + price source.

    The :attr:`router` is the canonical set of HTTP routes (``/health``,
    ``/capabilities``, ``/execute``); :attr:`app` is a thin :class:`FastAPI`
    wrapper that includes it. Callers that need to compose additional
    routes or a custom lifespan (see :func:`acheron.worker_sdk.app.create_worker_app`)
    should ``include_router`` :attr:`router` instead of copying routes.
    """

    def __init__(  # noqa: C901, PLR0913
        self,
        *,
        handler: WorkerHandler,
        capabilities: WorkerCapabilities,
        price_source: PriceSource | None = None,
        registration_token: str | None = None,
        token_provider: RegistrationTokenProvider | None = None,
        allow_unauthenticated_execute: bool = False,
    ) -> None:
        self.handler = handler
        self.capabilities = capabilities
        self.price_source = price_source
        self.token_provider = token_provider or EnvironmentOrFileTokenProvider(registration_token, None)
        self.allow_unauthenticated_execute = allow_unauthenticated_execute

        async def _verify_bearer(
            authorization: str | None = Header(default=None),
        ) -> None:
            current_token = self.token_provider.current()
            if current_token is None:
                if self.allow_unauthenticated_execute:
                    return
                raise HTTPException(status_code=401, detail="Missing registration token")
            if authorization is None:
                raise HTTPException(status_code=401, detail="Missing Authorization header")
            scheme, _, provided = authorization.partition(" ")
            if scheme.lower() != "bearer" or not secrets.compare_digest(provided, current_token):
                raise HTTPException(status_code=401, detail="Invalid registration token")

        router = APIRouter()

        @router.get("/health")
        async def health() -> dict[str, str]:
            return {"status": "ok"}

        @router.get("/capabilities")
        async def get_capabilities() -> dict[str, JsonValue]:
            return public_caps_to_dict(self.capabilities)

        @router.get("/auth/check", dependencies=[Depends(_verify_bearer)])
        async def auth_check() -> dict[str, str]:
            return {"status": "ok"}

        @router.post("/execute", dependencies=[Depends(_verify_bearer)])
        async def execute(request: Request) -> Response:
            """Accept either ``application/json`` (legacy / TTS) or ``multipart/form-data`` (8b ASR)."""
            ctype = request.headers.get("content-type", "")
            if ctype.startswith("multipart/"):
                return await self._run_execute_multipart(request)
            try:
                body = ExecuteRequest.model_validate(json.loads(await self._read_limited_body(request)))
                return await self._run_execute(body)
            except PayloadTooLargeError:
                return JSONResponse(status_code=413, content={"detail": "execute request exceeds maximum size"})
            except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, TypeError, ValueError) as exc:
                logger.info("Malformed JSON execute request: %s", sanitise_exc_message(exc))
                return _malformed_execute_response()

        self.router = router
        self.app = FastAPI(title="acheron-worker-edge")
        self.app.include_router(router)

    async def _run_execute_multipart(self, request: Request) -> Response:
        """Parse a ``multipart/form-data`` body, build Job + Input, dispatch to handler."""
        try:
            job, input_obj, state = await self._parse_multipart_request(request)
        except PayloadTooLargeError:
            return JSONResponse(status_code=413, content={"detail": "execute request exceeds maximum size"})
        except (WorkerError, ValueError, KeyError, TypeError) as exc:
            logger.exception("Multipart request parsing failed")
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
                metrics=JobMetrics(duration_seconds=0.0),
                error=sanitise_exc_message(parser_error),
            )
            return JSONResponse(
                status_code=500,
                content=_jobresult_to_json(result),
            )
        try:
            return await self._dispatch(job, input_obj)
        finally:
            state.close()

    async def _read_limited_body(self, request: Request) -> bytes:
        """Read a JSON body without allocating beyond the edge request limit."""
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > _MAX_EXECUTE_BODY_BYTES:
                    raise PayloadTooLargeError("execute request exceeds the maximum body size")
            except ValueError:
                pass
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > _MAX_EXECUTE_BODY_BYTES:
                raise PayloadTooLargeError("execute request exceeds the maximum body size")
            body.extend(chunk)
        return bytes(body)

    async def _parse_multipart_request(self, request: Request) -> tuple[Job, StreamInput | None, _MultipartStreamState]:
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
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > _MAX_EXECUTE_BODY_BYTES:
                    raise PayloadTooLargeError("execute request exceeds the maximum body size")
            except ValueError:
                pass
        state = _MultipartStreamState()
        try:
            parser = _build_streaming_multipart_parser(boundary, state)
            await _consume_multipart_request(request, parser)
            parser.finalize()
            _require_multipart_end(state)
            job, input_obj = _build_job_and_input(state.parts)
        except BaseException:
            state.close()
            raise
        else:
            return job, input_obj, state

    async def _run_execute(self, body: ExecuteRequest) -> Response:
        job = _job_from_request(body)
        return await self._dispatch(job, None)

    async def _estimate_cost(self, gpu_seconds: float, job_id: str) -> CostEstimate | None:
        """Return structured cost evidence without making pricing fatal."""
        if self.price_source is None:
            return None
        try:
            estimate = await self.price_source.estimate(gpu_seconds)
            basis = to_cost_basis(estimate)
        except Exception as exc:  # noqa: BLE001 — pricing must never fail execution
            safe_message = sanitise_exc_message(exc)
            logger.warning("cost estimate unavailable for job %s: %s", job_id, safe_message, exc_info=True)
            return CostEstimate(cost=None, basis=CostBasis.UNKNOWN)
        gpu_type = estimate.gpu_type
        if (
            not isinstance(gpu_type, str)
            or _SAFE_GPU_LABEL_RE.fullmatch(gpu_type) is None
            or sanitise_public_message(gpu_type, fallback="__unsafe_gpu__") == "__unsafe_gpu__"
        ):
            gpu_type = None
        return CostEstimate(
            cost=estimate.cost,
            basis=basis,
            rate_per_hour=estimate.rate_per_hour,
            gpu_type=gpu_type,
            secure_cloud=estimate.secure_cloud,
            queried_at=estimate.queried_at,
            cache_age_seconds=estimate.cache_age_seconds,
        )

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
                metrics=JobMetrics(
                    duration_seconds=duration,
                    gpu_seconds=duration,
                    cost_estimate=await self._estimate_cost(duration, job.job_id),
                ),
                error=sanitise_exc_message(exc),
            )
            return JSONResponse(
                status_code=500,
                content=_jobresult_to_json(result),
            )
        duration = time.monotonic() - start
        metrics = JobMetrics(
            duration_seconds=duration,
            gpu_seconds=duration,
            cost_estimate=await self._estimate_cost(duration, job.job_id),
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
