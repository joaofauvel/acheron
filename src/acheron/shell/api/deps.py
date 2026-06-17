"""FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, Request

from acheron.shell.orchestrator import Orchestrator


def get_orchestrator(request: Request) -> Orchestrator:
    """FastAPI dependency for injecting the orchestrator."""
    return cast("Orchestrator", request.app.state.orchestrator)


OrchestratorDep = Annotated[Orchestrator, Depends(get_orchestrator)]
