"""Phase 3a runtime simulation — extends the RunPod control plane mock and runs scenarios."""

from __future__ import annotations

import json
from typing import Any

import httpx

from acheron.worker_sdk import pricing as pricing_mod

DEFAULT_ARTIFACTS: dict[str, list[dict[str, str]]] = {"artifacts": [{"filename": "out.wav", "data": "AAEC"}]}


class GraphQLForwardingTransport(httpx.AsyncBaseTransport):
    """Forwards api.runpod.io/graphql calls to a local mock server.

    The pricing module hardcodes ``https://api.runpod.io/graphql``; the sim
    tests need those calls to hit the in-process mock instead. Non-GraphQL
    calls pass through to the real network so admin toggles against the
    mock at 127.0.0.1 still work.
    """

    def __init__(self, mock_url: str) -> None:
        self._mock_url = mock_url.rstrip("/")
        self._default = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.runpod.io/graphql" in url:
            fwd_headers: dict[str, str] = {}
            if auth := request.headers.get("authorization"):
                fwd_headers["authorization"] = auth
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self._mock_url}/graphql",
                    content=request.content,
                    headers=fwd_headers,
                    timeout=10.0,
                )
                return httpx.Response(resp.status_code, headers=resp.headers, content=resp.content)
        return await self._default.handle_async_request(request)


def patch_pricing_transport(mock_url: str) -> Any:
    """Route :class:`RunPodPrice` GraphQL through the mock. Return original init."""
    original = pricing_mod.RunPodPrice.__post_init__

    def _patched(self: Any) -> None:
        self._client = httpx.AsyncClient(transport=GraphQLForwardingTransport(mock_url))

    pricing_mod.RunPodPrice.__post_init__ = _patched
    return original


def restore_pricing_transport(original: Any) -> None:
    """Restore the original :class:`RunPodPrice.__post_init__`."""
    pricing_mod.RunPodPrice.__post_init__ = original


class FakeRun:
    """Stub for the runpod SDK's run-returned request object."""

    def __init__(self, output: dict[str, Any]) -> None:
        self._output = output

    def output(self, timeout: float | None = None) -> dict[str, Any]:
        return self._output


class FakeEndpoint:
    """Drop-in for :class:`runpod.Endpoint`: POSTs to the mock's /run on .run()."""

    def __init__(self, base_url: str, endpoint_id: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._endpoint_id = endpoint_id

    def run(self, payload: dict[str, Any]) -> FakeRun:
        resp = httpx.post(
            f"{self._base_url}/run",
            json={"input": payload, "endpoint_id": self._endpoint_id},
            timeout=30.0,
        )
        resp.raise_for_status()
        body = resp.json()
        return FakeRun(
            output={
                "status": body.get("status", "COMPLETED"),
                "artifacts": body.get("output", {}).get("artifacts", []),
            }
        )


def patch_runpod_endpoint(mock_url: str) -> Any:
    """Monkey-patch :func:`_open_endpoint` to return a :class:`FakeEndpoint`. Return original."""
    from acheron.worker_sdk import _runpod_client as rpd

    original = rpd._open_endpoint
    rpd._open_endpoint = lambda endpoint_id, *, _api_key, _base_url=None: FakeEndpoint(mock_url, endpoint_id)
    return original


def restore_runpod_endpoint(original: Any) -> None:
    """Restore the original :func:`_open_endpoint`."""
    from acheron.worker_sdk import _runpod_client as rpd

    rpd._open_endpoint = original


def parse_multipart_metrics(content_type: str, body: bytes) -> dict[str, Any]:
    """Extract the application/json metrics part from a multipart/mixed body."""
    boundary = content_type.split("boundary=", 1)[1].split(";", 1)[0].strip().strip('"').encode("latin-1")
    closing = b"--" + boundary + b"--"
    end_idx = body.find(closing)
    if end_idx < 0:
        msg = f"no closing boundary found in multipart body (boundary={boundary!r})"
        raise ValueError(msg)
    sep = b"--" + boundary + b"\r\n"
    for part in body[:end_idx].split(sep)[1:]:
        if b"application/json" in part:
            header_end = part.find(b"\r\n\r\n")
            if header_end < 0:
                continue
            return json.loads(part[header_end + 4 :].rstrip(b"\r\n"))
    msg = "no application/json metrics part found in multipart body"
    raise ValueError(msg)


def satisfies_endpoint(obj: object) -> bool:
    """Duck-type check that ``obj`` quacks like :class:`runpod.Endpoint`."""
    return callable(getattr(obj, "run", None))
