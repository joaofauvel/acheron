"""FastAPI dependencies."""

from __future__ import annotations

import os
import secrets
from typing import Annotated, cast

from fastapi import Depends, Header, HTTPException, Request

from acheron.shell.orchestrator import Orchestrator


def get_orchestrator(request: Request) -> Orchestrator:
    """FastAPI dependency for injecting the orchestrator."""
    return cast("Orchestrator", request.app.state.orchestrator)


OrchestratorDep = Annotated[Orchestrator, Depends(get_orchestrator)]


def verify_registration_token(authorization: str | None = Header(None)) -> None:
    """Validate registration token.

    - If ``ACHERON_REGISTRATION_TOKEN`` is set: require a matching bearer token.
    - If unset: require the explicit opt-in flag ``ACHERON_OPEN_REGISTRATION=1``
      to enable open registration. Without the flag, registration is rejected
      so a missing token does not silently fail open in production.
    """
    token = os.environ.get("ACHERON_REGISTRATION_TOKEN")
    if not token:
        if os.environ.get("ACHERON_OPEN_REGISTRATION") == "1":
            return
        raise HTTPException(
            status_code=503,
            detail=(
                "ACHERON_REGISTRATION_TOKEN is unset; set it to require auth, "
                "or set ACHERON_OPEN_REGISTRATION=1 to opt into open registration."
            ),
        )
    if authorization is None:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    scheme, _, provided = authorization.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(provided, token):
        raise HTTPException(status_code=401, detail="Invalid registration token")


RegistrationTokenDep = Annotated[None, Depends(verify_registration_token)]
