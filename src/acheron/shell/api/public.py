"""Allowlisted projections for unauthenticated API responses."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from acheron.core.errors import sanitise_public_message

if TYPE_CHECKING:
    from collections.abc import Iterable

_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_CREDENTIAL_IDENTIFIER_RE = re.compile(
    r"(?:^|[^A-Za-z0-9])(?:authorization|credential|api(?:[ _-]?key)?|token|password|secret|bearer)"
    r"(?=$|[^A-Za-z0-9])",
    re.IGNORECASE,
)
_SAFE_LANGUAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,31}$")
_SAFE_FORMAT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,31}(?:/[A-Za-z0-9][A-Za-z0-9.+_-]{0,31})?$")
_SAFE_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+:-]{0,63}(?:/[A-Za-z0-9][A-Za-z0-9_.+:-]{0,63}){0,3}$")
_SAFE_REVISION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_REDACTED = "<redacted>"
_SAFE_MIME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+/[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_UNSAFE_PUBLIC_TEXT_RE = re.compile(
    r"(?:[\x00-\x1f\x7f]|://|[@/\\]|\.\.|"
    r"(?:authorization|credential|api(?:[ _-]?key)?|token|password|secret|bearer)"
    r"(?:\s*[:=]|\s+|[-_])?[A-Za-z0-9])",
    re.IGNORECASE,
)
_PUBLIC_TRANSPORTS = frozenset({"grpc", "grpcs", "http", "https", "local"})
_MAX_PUBLIC_TEXT_LENGTH = 256
_CONTROL_CHARACTER_LIMIT = 32
_DELETE_CHARACTER = 127


def public_worker_id(value: object) -> str:
    """Return a safe worker identifier for public responses."""
    if (
        not isinstance(value, str)
        or _SAFE_IDENTIFIER_RE.fullmatch(value) is None
        or _CREDENTIAL_IDENTIFIER_RE.search(value) is not None
    ):
        return _REDACTED
    return value


def public_optional_worker_id(value: object) -> str | None:
    """Return a safe optional worker identifier for public responses."""
    return None if value is None else public_worker_id(value)


def public_transport(value: object) -> str:
    """Return an allowlisted transport name for public responses."""
    if not isinstance(value, str) or value not in _PUBLIC_TRANSPORTS:
        return _REDACTED
    return value


def public_content_type(value: object) -> str:
    """Return a safe MIME type for response headers."""
    if not isinstance(value, str) or any(
        ord(char) < _CONTROL_CHARACTER_LIMIT or ord(char) == _DELETE_CHARACTER for char in value
    ):
        return "application/octet-stream"
    media_type = value.split(";", 1)[0].strip()
    if _SAFE_MIME_RE.fullmatch(media_type) is None:
        return "application/octet-stream"
    return media_type


def _safe_public_text(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > _MAX_PUBLIC_TEXT_LENGTH:
        return None
    if _UNSAFE_PUBLIC_TEXT_RE.search(value) is not None or _CREDENTIAL_IDENTIFIER_RE.search(value) is not None:
        return None
    safe = sanitise_public_message(value, fallback=_REDACTED)
    return None if safe == _REDACTED else value


def public_label(value: object) -> str | None:
    """Return a safe public label, preserving ordinary labels."""
    return _safe_public_text(value)


def public_filename(value: object) -> str:
    """Return a safe basename for public output responses."""
    safe = _safe_public_text(value)
    if safe is None or safe in {".", ".."} or ":" in safe or '"' in safe:
        return "output.bin"
    return safe


def public_gpu_type(value: object) -> str | None:
    """Return a safe public GPU label."""
    return public_label(value)


def public_language(value: object) -> str:
    """Return a bounded language identifier or a neutral fallback."""
    values = public_capability_values([value], kind="language")
    return values[0] if values else "und"


def public_model(value: object) -> str | None:
    """Return a bounded model identifier without URLs or credentials."""
    if not isinstance(value, str) or _SAFE_MODEL_RE.fullmatch(value) is None:
        return None
    if _CREDENTIAL_IDENTIFIER_RE.search(value) is not None:
        return None
    safe = sanitise_public_message(value, fallback=_REDACTED)
    return value if safe != _REDACTED else None


def public_revision(value: object) -> str | None:
    """Return a bounded build revision or branch name."""
    if value is None:
        return None
    if not isinstance(value, str) or _SAFE_REVISION_RE.fullmatch(value) is None:
        return "unknown"
    if _CREDENTIAL_IDENTIFIER_RE.search(value) is not None:
        return "unknown"
    safe = sanitise_public_message(value, fallback=_REDACTED)
    return value if safe != _REDACTED else "unknown"


def public_capability_values(values: Iterable[object], *, kind: str) -> list[str]:
    """Return sorted, allowlisted capability labels."""
    pattern = {"language": _SAFE_LANGUAGE_RE, "format": _SAFE_FORMAT_RE}[kind]
    safe: set[str] = set()
    for value in values:
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            continue
        if (
            _CREDENTIAL_IDENTIFIER_RE.search(value) is not None
            or sanitise_public_message(value, fallback=_REDACTED) == _REDACTED
        ):
            continue
        safe.add(value)
    return sorted(safe)
