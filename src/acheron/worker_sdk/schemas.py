"""Pydantic schemas for the worker /execute request and error response."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from acheron.core.models import JsonValue  # noqa: TC001


class ExecuteRequest(BaseModel):
    """POST /execute body — mirrors core.models.Job."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    job_type: str
    payload: dict[str, JsonValue]
    chapter_id: str
    sequence_ids: list[int] | None = None


class ExecuteError(BaseModel):
    """JSON body returned when the handler raises (no artifacts emitted)."""

    model_config = ConfigDict(extra="forbid")

    status: str
    error: str
