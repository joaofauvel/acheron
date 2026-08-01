"""Deployed version identity route."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, Request

from acheron.core.schemas import VersionResponse
from acheron.shell.api.public import public_label

if TYPE_CHECKING:
    from acheron.version import VersionInfo

router = APIRouter()


@router.get("/version", response_model=VersionResponse)
async def get_version(request: Request) -> VersionResponse:
    """Return package and explicit build identity without runtime configuration."""
    version = cast("VersionInfo", request.app.state.version)
    return VersionResponse(
        version=version.version,
        sha=version.sha,
        build_time=version.build_time,
        branch=version.branch,
        dirty=version.dirty,
        image=public_label(version.image),
        registry=public_label(version.registry),
    )
