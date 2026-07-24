"""Pydantic schemas for the UX review story YAML."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["high", "medium", "low"]
Effort = Literal["S", "M", "L"]
Status = Literal[
    "open",
    "in-progress",
    "fixed",
    "verified",
    "partial",
    "stale",
    "obsolete",
    "broken-yaml",
    "wontfix",
]
UserFacingSurface = Literal[
    "cli",
    "dashboard",
    "compose",
    "worker-image",
    "runpod-api",
    "certs",
    "quickstart",
    "internal",
]
JourneyStage = Literal["t0", "t1", "t2", "cross_cutting"]
DiscoveryChannel = Literal[
    "code-review",
    "simulation",
    "first-run",
    "on-call",
    "audit",
    "user-feedback",
]
WontfixReason = Literal[
    "out-of-scope",
    "wontfix-product",
    "wontfix-cost",
    "wontfix-ux-traded-off",
    "duplicate",
]


class FileRef(BaseModel):
    """A single file:line reference inside a story's `files` list."""

    path: str
    lines: str


class Story(BaseModel):
    """A single UX review story."""

    id: str
    title: str
    status: Status
    severity: Severity
    effort: Effort
    discovered_via: list[DiscoveryChannel] = Field(min_length=1)
    user_facing_surface: UserFacingSurface
    silent: bool
    journey_stage: JourneyStage
    user_journey: str
    files: list[FileRef] = Field(min_length=1)
    related: list[str] = Field(default_factory=list)
    bundle: str | None = None
    fixed_in: list[str] = Field(default_factory=list)
    verified_in: list[str] = Field(default_factory=list)
    last_verified_at: dict[str, str] = Field(default_factory=dict)
    verified_by: str = ""
    incident_ref: str | None = None
    feedback_ref: str | None = None
    wontfix_reason: WontfixReason | None = None
