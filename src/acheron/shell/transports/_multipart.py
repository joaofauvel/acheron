"""Shared helpers used by both HttpWorker (multipart/mixed) and GrpcWorker (Artifact parts).

The orchestrator materializes received bytes into its own ``ACHERON_DATA_DIR``,
so a worker needs no shared filesystem with the orchestrator.
"""

from __future__ import annotations

import hashlib
import json
from email.message import Message
from email.parser import BytesParser
from email.policy import default as default_policy
from pathlib import Path
from typing import Any

import aiofiles

from acheron.core.errors import WorkerError
from acheron.core.models import JobMetrics, JobResult, JobStatus, OutputFile


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


async def _materialize_artifact(
    *,
    data: bytes,
    filename: str,
    content_type: str,
    dest_dir: Path,
) -> OutputFile:
    """Write ``bytes`` to ``dest_dir/filename`` and return an ``OutputFile``.

    ``filename`` is validated against path-traversal attacks — see
    :func:`_safe_join`. ``checksum`` (SHA-256) and ``size_bytes`` are
    computed locally on the orchestrator side so the worker doesn't need
    to be trusted on them.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
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


def _parse_request_multipart(
    ctype: str,
    body: bytes,
) -> tuple[dict[str, Any], bytes, str]:
    """Parse a ``/execute`` request body into (envelope, audio_bytes, audio_content_type).

    Accepts either ``multipart/form-data`` (one ``application/json`` part + zero
    or more binary parts) or plain ``application/json`` (legacy / TTS path).
    For multipart with no binary part, ``audio_bytes`` is empty and
    ``audio_content_type`` is ``""``.

    The wire contract is the SDK's ``/execute`` request shape: a JSON
    ``ExecuteRequest`` envelope plus an optional binary audio part. The
    orchestrator and the SDK parse independently (each side does different
    things with the result), so a single helper is the right level of
    sharing.
    """
    if not ctype.startswith("multipart/"):
        return (json.loads(body), b"", "")
    boundary = ctype.split("boundary=", 1)[1].split(";", 1)[0].strip().strip('"')
    full_body = (
        f"Content-Type: {ctype.split(';', 1)[0].strip()}; boundary={boundary}\r\nMIME-Version: 1.0\r\n\r\n"
    ).encode() + body
    message = BytesParser(policy=default_policy).parsebytes(full_body)
    if not message.is_multipart():
        return (json.loads(body), b"", "")
    envelope: dict[str, Any] | None = None
    audio_bytes = b""
    audio_ctype = ""
    for part in message.get_payload():
        if not isinstance(part, Message):
            continue
        part_ctype = part.get_content_type()
        if part_ctype == "application/json" and envelope is None:
            raw = part.get_payload(decode=True)
            envelope = json.loads(raw if isinstance(raw, bytes) else str(raw).encode("utf-8"))
        elif not audio_bytes and part_ctype != "application/json":
            raw = part.get_payload(decode=True)
            audio_bytes = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
            audio_ctype = part_ctype
    if envelope is None:
        msg = "Multipart body has no application/json part"
        raise ValueError(msg)
    return (envelope, audio_bytes, audio_ctype)
