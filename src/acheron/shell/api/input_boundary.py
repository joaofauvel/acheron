"""Early authentication and bounded-body protection for input uploads."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, cast

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from acheron.shell import input_store

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

_MULTIPART_OVERHEAD_BYTES = 1 << 20


class _InputBodyTooLarge(HTTPException):
    """The raw input request body exceeded the bounded upload envelope."""

    def __init__(self) -> None:
        super().__init__(status_code=413, detail="input exceeds the 2 GiB upload limit")


class InputRequestBoundary:
    """Authenticate and bound ``POST /inputs`` before FastAPI parses multipart data."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Apply authentication and body limits before passing the request inward."""
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] not in {"/inputs", "/inputs/"}:
            await self.app(scope, receive, send)
            return

        unauthorized = self._authorization_error(scope)
        if unauthorized is not None:
            await unauthorized(scope, receive, send)
            return

        limit = input_store.MAX_INPUT_BYTES + _MULTIPART_OVERHEAD_BYTES
        content_length = _header(scope, b"content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                declared_length = 0
            if declared_length > limit:
                await _too_large_response(scope, receive, send)
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _InputBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _InputBodyTooLarge:
            await _too_large_response(scope, receive, send)

    @staticmethod
    def _authorization_error(scope: Scope) -> JSONResponse | None:
        """Return the auth response without consuming the request body when denied."""
        orchestrator = scope["app"].state.orchestrator
        settings = orchestrator.settings.orchestrator
        if settings.open_registration:
            return None
        token = settings.registration_token
        if not token:
            return JSONResponse(
                status_code=503,
                content={
                    "detail": (
                        "ACHERON_REGISTRATION_TOKEN is unset; set it to require auth, "
                        "or set ACHERON_OPEN_REGISTRATION=1 to opt into open registration."
                    )
                },
            )
        authorization = _header(scope, b"authorization")
        if authorization is None:
            return JSONResponse(status_code=401, content={"detail": "Missing Authorization header"})
        scheme, _, provided = authorization.partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(provided, token):
            return JSONResponse(status_code=401, content={"detail": "Invalid registration token"})
        return None


async def _too_large_response(scope: Scope, receive: Receive, send: Send) -> None:
    """Send the upload limit response without invoking the multipart parser."""
    await JSONResponse(status_code=413, content={"detail": "input exceeds the 2 GiB upload limit"})(
        scope, receive, send
    )


def _header(scope: Scope, name: bytes) -> str | None:
    """Read the first case-insensitive header value from an ASGI scope."""
    for key, value in scope["headers"]:
        if key.lower() == name:
            return cast("str", value.decode("latin-1"))
    return None
