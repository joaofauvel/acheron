"""Shared utilities for all worker handlers (8a TTS, 8b ASR, 8c translation)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from acheron.core.errors import WorkerError

if TYPE_CHECKING:
    from acheron.worker_sdk.inputs import Input

MAX_CHAPTER_ID_LEN = 128


def safe_chapter_id(cid: str) -> str:
    r"""Sanitise a chapter_id for use as a filename component.

    Rejects blank, whitespace-only, NUL-byte, newline, tab,
    path-separator (``/`` / ``\``), absolute-path, and ``..``-component
    values. The orchestrator's ``_safe_join`` defends the orchestrator
    boundary; this is defense-in-depth so the worker also fails fast on
    malicious input.
    """
    if not cid or not cid.strip():
        msg = f"chapter_id is blank: {cid!r}"
        raise WorkerError(msg)
    if any(c in cid for c in "\x00\n\r\t"):
        msg = f"chapter_id contains illegal whitespace/NUL: {cid!r}"
        raise WorkerError(msg)
    if len(cid) > MAX_CHAPTER_ID_LEN:
        msg = f"chapter_id too long ({len(cid)} > {MAX_CHAPTER_ID_LEN}): {cid!r}"
        raise WorkerError(msg)
    if "/" in cid or "\\" in cid or cid in {".", ".."} or ".." in cid.split("/") or ".." in cid.split("\\"):
        msg = f"chapter_id contains a path component: {cid!r}"
        raise WorkerError(msg)
    return cid


@dataclass(frozen=True)
class Chunk:
    """One unit of upstream chunked text shared by the TTS and translation handlers."""

    chapter_id: str
    sequence_id: int
    text: str
    instruct: str = ""


def validate_chunk_fields(c: object) -> Chunk:
    """Validate a raw chunks.json element and return a :class:`Chunk`.

    Raises :class:`WorkerError` for non-dict input, missing required fields, or
    wrong types. The optional ``instruct`` field defaults to ``""`` when absent
    but must be a ``str`` when present.
    """
    if not isinstance(c, dict):
        msg = f"chunk must be a dict, got {type(c).__name__}"
        raise WorkerError(msg)
    if "chapter_id" not in c or not isinstance(c["chapter_id"], str):
        msg = "chunk.chapter_id is required (str)"
        raise WorkerError(msg)
    if "sequence_id" not in c or not isinstance(c["sequence_id"], int):
        msg = "chunk.sequence_id is required (int)"
        raise WorkerError(msg)
    if "text" not in c or not isinstance(c["text"], str):
        msg = "chunk.text is required (str)"
        raise WorkerError(msg)
    instruct = c.get("instruct", "")
    if not isinstance(instruct, str):
        msg = f"chunk.instruct must be a str, got {type(instruct).__name__}"
        raise WorkerError(msg)
    return Chunk(
        chapter_id=c["chapter_id"],
        sequence_id=c["sequence_id"],
        text=c["text"],
        instruct=instruct,
    )


async def parse_chunks_json(input: Input) -> list[Chunk]:  # noqa: A002
    """Parse the JSON-serialised ``chunks.json`` body from ``input`` into :class:`Chunk` objects.

    Returns an empty list when the body is empty. Raises :class:`WorkerError` on
    malformed JSON, a non-list top-level value, or any element that fails
    :func:`validate_chunk_fields`.
    """
    chunks_json_bytes = b"".join([chunk async for chunk in input.stream()])
    if not chunks_json_bytes:
        return []
    try:
        raw_chunks = json.loads(chunks_json_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        msg = f"chunks.json is not valid JSON: {exc}"
        raise WorkerError(msg) from exc
    if not isinstance(raw_chunks, list):
        msg = "chunks.json must be a JSON array of chunk dicts"
        raise WorkerError(msg)
    return [validate_chunk_fields(c) for c in raw_chunks]
