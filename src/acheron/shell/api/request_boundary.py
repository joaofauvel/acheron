"""Bound non-upload HTTP request bodies before JSON parsing."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

_MAX_JSON_BODY_BYTES = 8 * 1024 * 1024


class RequestBodyBoundary:
    """Reject oversized non-upload request bodies before application parsing."""

    def __init__(self, app: ASGIApp, *, limit: int = _MAX_JSON_BODY_BYTES) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:  # noqa: C901
        """Apply the bound to JSON and registration requests."""
        if (
            scope["type"] != "http"
            or scope["method"] not in {"POST", "PUT", "PATCH"}
            or scope["path"] in {"/inputs", "/inputs/"}
        ):
            await self.app(scope, receive, send)
            return
        content_length = _header(scope, b"content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                declared = self.limit + 1
            if declared > self.limit:
                await _too_large_response(scope, receive, send)
                return
        messages: list[Message] = []
        received = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request":
                break
            received += len(message.get("body", b""))
            if received > self.limit:
                await _too_large_response(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        position = 0

        async def replay_receive() -> Message:
            nonlocal position
            if position >= len(messages):
                return {"type": "http.disconnect"}
            message = messages[position]
            position += 1
            return message

        await self.app(scope, replay_receive, send)


async def _too_large_response(scope: Scope, receive: Receive, send: Send) -> None:
    await JSONResponse(status_code=413, content={"detail": "request body exceeds the supported limit"})(
        scope, receive, send
    )


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope["headers"]:
        if key.lower() == name:
            return cast("str", value.decode("latin-1"))
    return None
