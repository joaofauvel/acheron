"""Shared helpers used by both HttpWorker (multipart/mixed) and GrpcWorker (Artifact parts)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from email.message import Message
from email.parser import BytesParser
from email.policy import default as default_policy
from pathlib import Path

import aiofiles
from pydantic import TypeAdapter

from acheron.core.errors import WorkerError
from acheron.core.models import JobMetrics, JobResult, JobStatus, OutputFile

_METRICS_PART_NAME = "metrics"
_MAX_CONTENT_TYPE_LENGTH = 256
_SAFE_CONTENT_TYPE_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+/[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_CONTROL_CHARACTER_LIMIT = 32
_DELETE_CHARACTER = 127

_metrics_adapter = TypeAdapter(JobMetrics)


@dataclass(frozen=True)
class ParsedPart:
    """A single non-metrics part of a parsed ``multipart/mixed`` body."""

    content_type: str
    filename: str
    data: bytes
    metadata: dict[str, str]


def _parse_multipart_parts(  # noqa: C901
    content_type: str,
    body: bytes,
) -> tuple[list[ParsedPart], JobMetrics]:
    """Parse a ``multipart/mixed`` body into non-metrics parts and a metrics part.

    Returns ``(parts, metrics)`` where ``parts`` is one entry per non-JSON
    part and ``metrics`` is the selected ``JobMetrics``. The metrics part is
    identified by an ``X-Acheron-Part-Name: metrics`` header; if no part
    carries the header the first ``application/json`` part is used; if
    multiple parts carry the header a ``WorkerError`` is raised.

    Raises ``WorkerError`` when ``content_type`` has no ``boundary=`` parameter
    or when the body is not actually multipart.
    """
    if "boundary=" not in content_type:
        msg = f"missing boundary in Content-Type: {content_type}"
        raise WorkerError(msg)
    boundary_part = content_type.split("boundary=", 1)[1]
    boundary = boundary_part.split(";", 1)[0].strip().strip('"')
    full_body = (f"Content-Type: multipart/mixed; boundary={boundary}\r\nMIME-Version: 1.0\r\n\r\n").encode() + body
    message = BytesParser(policy=default_policy).parsebytes(full_body)
    if not message.is_multipart():
        msg = f"Multipart/mixed body was not multipart: {content_type!r}"
        raise WorkerError(msg)

    parts: list[ParsedPart] = []
    named_metrics_raw: bytes | None = None
    fallback_metrics_raw: bytes | None = None
    for part in message.get_payload():
        # `message.get_payload()` is typed as the union of `str | Message | list[...]`
        # by email.message; at runtime in a multipart body it returns a list of
        # ``Message`` instances.
        if not isinstance(part, Message):
            continue
        unknown_headers = {
            key.casefold()
            for key in part
            if key.casefold()
            not in {"content-type", "content-disposition", "x-acheron-metadata", "x-acheron-part-name"}
        }
        if unknown_headers:
            raise WorkerError("Worker returned unsupported multipart headers")
        part_ctype = part.get_content_type()
        if (
            len(part_ctype) > _MAX_CONTENT_TYPE_LENGTH
            or any(ord(char) < _CONTROL_CHARACTER_LIMIT or ord(char) == _DELETE_CHARACTER for char in part_ctype)
            or _SAFE_CONTENT_TYPE_RE.fullmatch(part_ctype) is None
        ):
            raise WorkerError("Worker returned an invalid artifact content type")
        if part_ctype == "application/json":
            raw = part.get_payload(decode=True)
            payload_bytes = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
            if part.get("X-Acheron-Part-Name") == _METRICS_PART_NAME:
                if named_metrics_raw is not None:
                    msg = "multiple parts with X-Acheron-Part-Name: metrics"
                    raise WorkerError(msg)
                named_metrics_raw = payload_bytes
            elif fallback_metrics_raw is None:
                fallback_metrics_raw = payload_bytes
            continue
        filename = part.get_filename() or "artifact.bin"
        raw = part.get_payload(decode=True)
        data = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
        metadata = _decode_metadata(part.get("X-Acheron-Metadata"))
        parts.append(
            ParsedPart(
                content_type=part_ctype,
                filename=filename,
                data=data,
                metadata=metadata,
            )
        )

    metrics_raw = named_metrics_raw or fallback_metrics_raw
    if metrics_raw is not None:
        metrics = _metrics_adapter.validate_json(metrics_raw)
    else:
        metrics = JobMetrics(duration_seconds=0.0)
    return parts, metrics


def _decode_metadata(header: str | None) -> dict[str, str]:
    if not header:
        return {}
    parsed = json.loads(header)
    return {str(k): str(v) for k, v in parsed.items()}


def _safe_join(dest_dir: Path, filename: str) -> Path:
    """Join ``dest_dir / filename`` and reject any escape from ``dest_dir``.

    A worker-supplied ``filename`` of ``../../etc/passwd`` (or any absolute
    path) would otherwise let the worker write anywhere on the orchestrator
    host. This helper resolves the candidate path and refuses it if the
    resolved location is not under ``dest_dir``.
    """
    if not filename or filename != filename.strip():
        msg = f"Refusing artifact with blank or padded filename: {filename!r}"
        raise WorkerError(msg)
    if filename != Path(filename).name:
        msg = f"Refusing artifact with path-traversal filename: {filename!r}"
        raise WorkerError(msg)
    if "\x00" in filename:
        msg = f"Refusing artifact with NUL byte in filename: {filename!r}"
        raise WorkerError(msg)
    dest_dir_resolved = dest_dir.resolve()
    candidate = (dest_dir / filename).resolve()
    try:
        candidate.relative_to(dest_dir_resolved)
    except ValueError as exc:
        msg = f"Refusing artifact that escapes dest_dir: {filename!r} -> {candidate}"
        raise WorkerError(msg) from exc
    return candidate


async def _materialize_artifact(  # noqa: PLR0913
    *,
    data: bytes,
    filename: str,
    content_type: str,
    dest_dir: Path,
    metadata: dict[str, str] | None = None,
    root_dir: Path | None = None,
) -> OutputFile:
    """Write ``bytes`` to ``dest_dir/filename`` and return an ``OutputFile``.

    ``filename`` is validated against path-traversal attacks — see
    :func:`_safe_join`. ``checksum`` (SHA-256) and ``size_bytes`` are
    computed locally on the orchestrator side so the worker doesn't need
    to be trusted on them. ``metadata`` is the per-artifact dict carried
    in the ``X-Acheron-Metadata`` part header.
    """
    resolved_root = (root_dir or dest_dir).resolve()
    current = dest_dir
    while True:
        if current.is_symlink():
            raise WorkerError("Refusing artifact destination symlink")
        if current.exists() and not current.is_dir():
            raise WorkerError("Refusing non-directory artifact destination")
        if current in (resolved_root, current.parent):
            break
        current = current.parent
    dest_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    resolved_dest = dest_dir.resolve()  # noqa: ASYNC240
    try:
        resolved_dest.relative_to(resolved_root)
    except ValueError as exc:
        raise WorkerError("Refusing artifact destination outside data directory") from exc
    out_path = _safe_join(dest_dir, filename)
    async with aiofiles.open(out_path, "wb") as f:
        await f.write(data)
    checksum = hashlib.sha256(data).hexdigest()
    return OutputFile(
        path=str(out_path),
        filename=Path(filename).name,
        size_bytes=len(data),
        checksum=checksum,
        content_type=content_type,
        metadata=metadata or {},
    )


def _build_result(
    *,
    job_id: str,
    outputs: tuple[OutputFile, ...],
    metrics: JobMetrics,
) -> JobResult:
    """Assemble a success ``JobResult`` from materialized outputs + metrics."""
    return JobResult(
        job_id=job_id,
        status=JobStatus.SUCCESS,
        outputs=outputs,
        metrics=metrics,
        error=None,
    )
