"""Tests for the dev cert generator."""

from __future__ import annotations

import datetime
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

from cryptography import x509

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "generate_dev_certs.py"

SERVICES = [
    "orchestrator",
    "tts-stub",
    "asr-stub",
    "translation-stub",
    "tts-grpc-stub",
]


def _run(tmp_path: Path) -> None:
    subprocess.run(
        [sys.executable, str(SCRIPT), "--out-dir", str(tmp_path)],
        check=True,
        capture_output=True,
    )


def test_creates_ca_and_per_service_certs(tmp_path: Path) -> None:
    _run(tmp_path)
    assert (tmp_path / "acheron-ca.crt").exists()
    assert (tmp_path / "acheron-ca.key").exists()
    for svc in SERVICES:
        assert (tmp_path / f"{svc}.crt").exists(), f"missing {svc}.crt"
        assert (tmp_path / f"{svc}.key").exists(), f"missing {svc}.key"


def test_idempotent(tmp_path: Path) -> None:
    _run(tmp_path)
    first_mtime = (tmp_path / "acheron-ca.crt").stat().st_mtime_ns
    _run(tmp_path)
    second_mtime = (tmp_path / "acheron-ca.crt").stat().st_mtime_ns
    assert first_mtime != second_mtime  # overwritten


def test_san_includes_service_and_localhost(tmp_path: Path) -> None:
    _run(tmp_path)
    for svc in SERVICES:
        cert_pem = (tmp_path / f"{svc}.crt").read_bytes()
        cert = x509.load_pem_x509_certificate(cert_pem)
        san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        dns_names = set(san_ext.get_values_for_type(x509.DNSName))
        ip_addrs = {str(a) for a in san_ext.get_values_for_type(x509.IPAddress)}
        assert svc in dns_names, f"{svc} not in DNS SAN: {dns_names}"
        assert "localhost" in dns_names
        assert "127.0.0.1" in ip_addrs


def test_ca_is_self_signed_and_loadable(tmp_path: Path) -> None:
    _run(tmp_path)
    ca_pem = (tmp_path / "acheron-ca.crt").read_bytes()
    ca = x509.load_pem_x509_certificate(ca_pem)
    assert ca.issuer == ca.subject  # self-signed
    assert ca.not_valid_after_utc > datetime.datetime.now(datetime.UTC)


def test_private_keys_are_owner_only(tmp_path: Path) -> None:
    """Private keys must be 0600; the CA key in particular must not be world-readable."""
    _run(tmp_path)
    key_files = [tmp_path / "acheron-ca.key", *(tmp_path / f"{svc}.key" for svc in SERVICES)]
    for kf in key_files:
        mode = kf.stat().st_mode & 0o777
        assert mode == 0o600, f"{kf.name} has mode {oct(mode)}, expected 0o600"


def test_certs_are_world_readable(tmp_path: Path) -> None:
    """Certificates are public and stay 0644."""
    _run(tmp_path)
    cert_files = [tmp_path / "acheron-ca.crt", *(tmp_path / f"{svc}.crt" for svc in SERVICES)]
    for cf in cert_files:
        mode = cf.stat().st_mode & 0o777
        assert mode == 0o644, f"{cf.name} has mode {oct(mode)}, expected 0o644"


def test_https_handshake_succeeds(tmp_path: Path) -> None:
    """End-to-end: a server using one of the certs completes a TLS handshake
    against a client that trusts the Acheron CA.
    """
    _run(tmp_path)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(
        certfile=str(tmp_path / "orchestrator.crt"),
        keyfile=str(tmp_path / "orchestrator.key"),
    )
    client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_ctx.load_verify_locations(cafile=str(tmp_path / "acheron-ca.crt"))
    client_ctx.check_hostname = True
    client_ctx.verify_mode = ssl.CERT_REQUIRED

    port_holder: dict[str, int] = {}
    server_error: list[Exception] = []

    def serve() -> None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
                srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                srv.bind(("127.0.0.1", 0))
                srv.listen(1)
                port_holder["port"] = srv.getsockname()[1]
                with srv.accept()[0] as conn:
                    ssl_conn = server_ctx.wrap_socket(conn, server_side=True)
                    assert ssl_conn.recv(4) == b"ping"
                    ssl_conn.sendall(b"pong")
                    ssl_conn.close()
        except Exception as exc:  # noqa: BLE001
            server_error.append(exc)

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    deadline = time.monotonic() + 5
    while "port" not in port_holder and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "port" in port_holder, "server failed to start"
    port = port_holder["port"]

    with (
        socket.create_connection(("127.0.0.1", port), timeout=5) as raw,
        client_ctx.wrap_socket(raw, server_hostname="orchestrator") as ssl_client,
    ):
        ssl_client.sendall(b"ping")
        assert ssl_client.recv(4) == b"pong"

    t.join(timeout=5)
    assert not server_error, f"server error: {server_error[0]}"
