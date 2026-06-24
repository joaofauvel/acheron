"""Shared utilities for all worker handlers (8a TTS, 8b ASR, future 8c)."""

from __future__ import annotations

from acheron.core.errors import WorkerError

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
