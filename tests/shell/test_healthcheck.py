"""Tests for the Docker healthcheck one-liner embedded in compose YAML."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest

SCRIPT = (
    "import os, ssl, urllib.request; "
    "ctx = ssl.create_default_context(cafile=os.environ.get('SSL_CERT_FILE')); "
    "urllib.request.urlopen('https://localhost:{port}/health', context=ctx).read()"
)


def _wait_https(port: int, cafile: str, timeout: float = 5.0) -> None:
    """Wait for the test HTTPS server to start serving."""
    import ssl as _ssl

    ctx = _ssl.create_default_context(cafile=cafile)
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with ctx.wrap_socket(
                __import__("socket").create_connection(("localhost", port), timeout=0.5), server_hostname="localhost"
            ) as s:
                s.recv(1)
                return
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(0.1)
    msg = f"server on port {port} did not start: {last}"
    raise RuntimeError(msg)


@pytest.fixture
def https_server(dev_certs: object, tmp_path: object) -> Generator[tuple[int, str]]:
    """Spin up a minimal HTTPS server using a dev cert, return (port, cafile)."""
    import contextlib
    import ssl as _ssl
    from pathlib import Path

    dev_certs_path = Path(str(dev_certs))
    ca_pem = (dev_certs_path / "acheron-ca.crt").read_bytes()

    # Pick a free port
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    server = HTTPServer(("127.0.0.1", port), _Handler)
    ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(
        certfile=str(dev_certs_path / "orchestrator.crt"), keyfile=str(dev_certs_path / "orchestrator.key")
    )
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cafile_path = str(tmp_path) + "/ca.crt"
    Path(cafile_path).write_bytes(ca_pem)
    try:
        yield (port, cafile_path)
    finally:
        server.shutdown()
        with contextlib.suppress(Exception):
            server.server_close()
        thread.join(timeout=2)


def test_healthcheck_returns_zero_when_server_up(https_server: tuple[int, str]) -> None:
    port, cafile = https_server
    script = SCRIPT.format(port=port)
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "SSL_CERT_FILE": cafile},
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, f"stderr: {result.stderr.decode()}"


def test_healthcheck_returns_nonzero_when_server_down(tmp_path: object) -> None:
    """When the server is unreachable, the healthcheck exits non-zero."""
    script = SCRIPT.format(port=1)  # nothing listens on port 1 in practice
    env = {**os.environ, "SSL_CERT_FILE": "/nonexistent/ca.crt"}
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode != 0


def test_healthcheck_loads_ca_from_ssl_cert_file(https_server: tuple[int, str]) -> None:
    """The healthcheck must use SSL_CERT_FILE as the CA trust store."""
    port, cafile = https_server
    script = SCRIPT.format(port=port)
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "SSL_CERT_FILE": cafile},
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, f"stderr: {result.stderr.decode()}"
