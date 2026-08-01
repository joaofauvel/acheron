"""Allowlisted projections for unauthenticated API responses."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from acheron.core.errors import sanitise_public_message

if TYPE_CHECKING:
    from collections.abc import Iterable

_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_LANGUAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,31}$")
_SAFE_FORMAT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,31}(?:/[A-Za-z0-9][A-Za-z0-9.+_-]{0,31})?$")
_REDACTED = "<redacted>"


def public_worker_id(value: object) -> str:
    """Return a safe worker identifier for public responses."""
    if not isinstance(value, str) or _SAFE_IDENTIFIER_RE.fullmatch(value) is None:
        return _REDACTED
    return value


def public_capability_values(values: Iterable[object], *, kind: str) -> list[str]:
    """Return sorted, allowlisted capability labels."""
    pattern = {"language": _SAFE_LANGUAGE_RE, "format": _SAFE_FORMAT_RE}[kind]
    safe: set[str] = set()
    for value in values:
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            continue
        if sanitise_public_message(value, fallback=_REDACTED) == _REDACTED:
            continue
        safe.add(value)
    return sorted(safe)
