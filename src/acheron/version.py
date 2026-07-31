"""Resolve the deployed package and build identity."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version as package_version


@dataclass(frozen=True, slots=True)
class VersionInfo:
    """Immutable package and deployment identity."""

    version: str
    sha: str | None
    build_time: datetime | None
    branch: str | None
    dirty: bool | None
    image: str | None
    registry: str | None


def _build_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        msg = "ACHERON_BUILD_TIME must be an ISO 8601 timestamp"
        raise ValueError(msg) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        msg = "ACHERON_BUILD_TIME must include a timezone"
        raise ValueError(msg)
    return parsed.astimezone(UTC)


def _dirty(value: str | None) -> bool | None:
    if not value:
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    msg = "ACHERON_BUILD_DIRTY must be exactly 'true' or 'false'"
    raise ValueError(msg)


def build_version() -> VersionInfo:
    """Return package version plus the explicitly supplied build identity."""
    return VersionInfo(
        version=package_version("acheron"),
        sha=os.environ.get("ACHERON_BUILD_SHA") or None,
        build_time=_build_time(os.environ.get("ACHERON_BUILD_TIME")),
        branch=os.environ.get("ACHERON_BUILD_BRANCH") or None,
        dirty=_dirty(os.environ.get("ACHERON_BUILD_DIRTY")),
        image=os.environ.get("ACHERON_BUILD_IMAGE") or None,
        registry=os.environ.get("ACHERON_BUILD_REGISTRY") or None,
    )


__all__ = ["VersionInfo", "build_version"]
