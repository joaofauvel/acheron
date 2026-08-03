"""Integration tests: orchestrator and workers communicating over TLS."""

from __future__ import annotations

import datetime
import ipaddress
import os
import socket
import ssl
import subprocess
import sys
import time
from collections.abc import Generator
from pathlib import Path
from typing import cast

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

# Serialize: this test binds to dynamic ports and a TOCTOU race would cause
# flakes under pytest-xdist. Tests in this module share a single xdist group.
pytestmark = pytest.mark.xdist_group(name="tls_integration")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_healthy(url: str, cafile: Path | None, timeout: float = 20.0) -> None:
    """Poll `url` until it returns 200 or `timeout` elapses.

    `cafile=None` skips TLS verification (use for plain HTTP). For HTTPS,
    pass the path to the CA bundle.
    """
    verify: ssl.SSLContext | bool = ssl.create_default_context(cafile=str(cafile)) if cafile is not None else True
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with httpx.Client(verify=verify) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        time.sleep(0.2)
    msg = f"service at {url} did not become healthy: {last_exc}"
    raise RuntimeError(msg)


def _wait_for_workers_registered(orch_port: int, expected_ids: set[str], ca: Path, timeout: float = 20.0) -> None:
    """Poll the orchestrator's /workers endpoint until all expected worker IDs appear."""
    ctx = ssl.create_default_context(cafile=str(ca))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with httpx.Client(verify=ctx) as client:
                resp = client.get(f"https://127.0.0.1:{orch_port}/workers")
                if resp.status_code == 200:
                    ids = {w["worker_id"] for w in resp.json().get("workers", [])}
                    if expected_ids.issubset(ids):
                        return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)
    msg = f"workers {expected_ids} did not register within {timeout}s"
    raise RuntimeError(msg)


def _peer_certificate(port: int, ca: Path) -> dict[str, object]:
    """Return the peer certificate presented by an HTTPS listener."""
    context = ssl.create_default_context(cafile=str(ca))
    with (
        socket.create_connection(("127.0.0.1", port), timeout=5) as raw,
        context.wrap_socket(raw, server_hostname="localhost") as wrapped,
    ):
        return cast("dict[str, object]", wrapped.getpeercert())


def _replace_orchestrator_certificate(certs_dir: Path) -> None:
    """Issue a replacement orchestrator certificate from the test CA."""
    ca_cert = x509.load_pem_x509_certificate((certs_dir / "acheron-ca.crt").read_bytes())
    ca_key = cast(
        "rsa.RSAPrivateKey",
        serialization.load_pem_private_key((certs_dir / "acheron-ca.key").read_bytes(), password=None),
    )
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "orchestrator-reloaded")])
    now = datetime.datetime.now(datetime.UTC)
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(
                ca_cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
            ),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("orchestrator-reloaded"),
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    (certs_dir / "orchestrator.crt").write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    (certs_dir / "orchestrator.key").write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )


@pytest.fixture(scope="module")
def tls_stack(tmp_path_factory: pytest.TempPathFactory, repo_root: Path) -> Generator[dict[str, object]]:
    """Bring up orchestrator, tts-stub, and tts-grpc-stub over TLS."""
    certs_dir = tmp_path_factory.mktemp("certs")
    script = repo_root / "scripts" / "generate_dev_certs.py"
    subprocess.run(
        [sys.executable, str(script), "--out-dir", str(certs_dir)],
        check=True,
        capture_output=True,
    )
    ca = certs_dir / "acheron-ca.crt"

    orch_port = _free_port()
    tts_port = _free_port()
    grpc_port = _free_port()
    grpc_http_port = _free_port()

    venv_python = Path(sys.executable)
    assert venv_python.exists()

    base_env = os.environ.copy()
    base_env["SSL_CERT_FILE"] = str(ca)
    base_env["ACHERON_DATA_DIR"] = str(certs_dir / "data")
    base_env["ACHERON_REGISTRATION_TOKEN"] = "test-registration-token-must-be-32-chars-or-more"
    base_env["ACHERON_TLS_CA_FILE"] = str(ca)
    base_env["ACHERON_STORE_BACKEND"] = "memory"
    base_env["ACHERON_ALLOW_INSECURE"] = "1"
    base_env["ACHERON_ADMIN_TOKEN"] = "test-admin-token-must-be-32-chars-or-more"
    base_env["PYTHONPATH"] = (
        str(repo_root / "src") + os.pathsep + str(repo_root / "stubs") + os.pathsep + base_env.get("PYTHONPATH", "")
    )

    orch_env = {
        **base_env,
        "ACHERON_TLS_CERT_FILE": str(certs_dir / "orchestrator.crt"),
        "ACHERON_TLS_KEY_FILE": str(certs_dir / "orchestrator.key"),
    }
    tts_env = {
        **base_env,
        "WORKER_CONFIG": str(repo_root / "stubs" / "tts_local_stub" / "worker.yaml"),
        "ACHERON_WORKER__LISTEN_PORT": str(tts_port),
        "ACHERON_WORKER__ORCHESTRATOR_URL": f"https://127.0.0.1:{orch_port}",
        "ACHERON_WORKER__REGISTRATION_TOKEN": "test-registration-token-must-be-32-chars-or-more",
        "ACHERON_TLS_CERT_FILE": str(certs_dir / "tts-stub.crt"),
        "ACHERON_TLS_KEY_FILE": str(certs_dir / "tts-stub.key"),
    }
    grpc_env = {
        **base_env,
        "WORKER_CONFIG": str(repo_root / "stubs" / "tts_grpc_stub" / "worker.yaml"),
        "ACHERON_WORKER__LISTEN_PORT": str(grpc_port),
        "ACHERON_WORKER__ORCHESTRATOR_URL": f"https://127.0.0.1:{orch_port}",
        "ACHERON_WORKER__REGISTRATION_TOKEN": "test-registration-token-must-be-32-chars-or-more",
        "ACHERON_TLS_CERT_FILE": str(certs_dir / "tts-grpc-stub.crt"),
        "ACHERON_TLS_KEY_FILE": str(certs_dir / "tts-grpc-stub.key"),
    }

    procs: list[subprocess.Popen[bytes]] = []
    try:
        orch_proc = subprocess.Popen(
            [str(venv_python), "-m", "acheron.shell.api", "--port", str(orch_port)],
            env=orch_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        procs.append(orch_proc)
        tts_proc = subprocess.Popen(
            [str(venv_python), "-m", "stubs.tts_local_stub.main"],
            env=tts_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        procs.append(tts_proc)
        grpc_proc = subprocess.Popen(
            [str(venv_python), "-m", "stubs.tts_grpc_stub.main"],
            env=grpc_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        procs.append(grpc_proc)
        _wait_healthy(f"https://127.0.0.1:{orch_port}/health", ca)
        _wait_healthy(f"https://127.0.0.1:{tts_port}/health", ca)
        _wait_healthy(f"https://127.0.0.1:{grpc_port}/health", ca)
        _wait_for_workers_registered(orch_port, {"tts-local-stub", "tts-grpc-stub"}, ca)
    except Exception:
        for p in procs:
            p.terminate()
        raise

    yield {
        "ca": ca,
        "orch_port": orch_port,
        "tts_port": tts_port,
        "grpc_port": grpc_port,
        "grpc_http_port": grpc_http_port,
        "orch_pid": orch_proc.pid,
        "orch_process": orch_proc,
        "certs_dir": certs_dir,
    }
    for p in procs:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


def test_orchestrator_health_over_https(tls_stack: dict[str, object]) -> None:
    ca = tls_stack["ca"]
    port = tls_stack["orch_port"]
    ctx = ssl.create_default_context(cafile=str(ca))
    with httpx.Client(verify=ctx) as client:
        resp = client.get(f"https://127.0.0.1:{port}/health")
        assert resp.status_code == 200


def test_orchestrator_version_over_https(tls_stack: dict[str, object]) -> None:
    ca = tls_stack["ca"]
    port = tls_stack["orch_port"]
    ctx = ssl.create_default_context(cafile=str(ca))
    with httpx.Client(verify=ctx) as client:
        resp = client.get(f"https://127.0.0.1:{port}/version")
        assert resp.status_code == 200
        assert resp.json()["version"]


def test_http_worker_registers_over_https(tls_stack: dict[str, object]) -> None:
    ca = tls_stack["ca"]
    port = tls_stack["orch_port"]
    ctx = ssl.create_default_context(cafile=str(ca))
    with httpx.Client(verify=ctx) as client:
        resp = client.get(f"https://127.0.0.1:{port}/workers")
        assert resp.status_code == 200
        workers = resp.json()["workers"]
        ids = {w["worker_id"] for w in workers}
        assert "tts-local-stub" in ids


def test_grpc_worker_registers(tls_stack: dict[str, object]) -> None:
    ca = tls_stack["ca"]
    port = tls_stack["orch_port"]
    ctx = ssl.create_default_context(cafile=str(ca))
    with httpx.Client(verify=ctx) as client:
        resp = client.get(f"https://127.0.0.1:{port}/workers")
        workers = resp.json()["workers"]
        ids = {w["worker_id"] for w in workers}
        assert "tts-grpc-stub" in ids


def test_orchestrator_cert_reload_keeps_pid_and_worker_connectivity(tls_stack: dict[str, object]) -> None:
    ca = tls_stack["ca"]
    port = tls_stack["orch_port"]
    certs_dir = tls_stack["certs_dir"]
    orch_pid = tls_stack["orch_pid"]
    orch_process = tls_stack["orch_process"]
    assert isinstance(ca, Path)
    assert isinstance(certs_dir, Path)
    assert isinstance(port, int)
    assert isinstance(orch_pid, int)
    assert isinstance(orch_process, subprocess.Popen)
    assert orch_process.pid == orch_pid

    before = _peer_certificate(port, ca)
    _replace_orchestrator_certificate(certs_dir)
    with httpx.Client(verify=ssl.create_default_context(cafile=str(ca))) as client:
        reload_response = client.post(
            f"https://127.0.0.1:{port}/admin/certs/reload",
            headers={"Authorization": "Bearer test-admin-token-must-be-32-chars-or-more"},
        )
        assert reload_response.status_code == 200
        assert reload_response.json()["reloaded"] is True
        health_response = client.get(f"https://127.0.0.1:{port}/health")
        assert health_response.status_code == 200
        workers_response = client.get(f"https://127.0.0.1:{port}/workers")
        assert workers_response.status_code == 200
        worker_ids = {worker["worker_id"] for worker in workers_response.json()["workers"]}
        assert {"tts-local-stub", "tts-grpc-stub"}.issubset(worker_ids)

    after = _peer_certificate(port, ca)
    assert before["serialNumber"] != after["serialNumber"]
    assert before["subject"] != after["subject"]
    os.kill(orch_pid, 0)
    assert orch_process.poll() is None
