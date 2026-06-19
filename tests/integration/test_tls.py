"""Integration tests: orchestrator and workers communicating over TLS."""

from __future__ import annotations

import os
import socket
import ssl
import subprocess
import sys
import time
from collections.abc import Generator
from pathlib import Path

import httpx
import pytest

# Serialize: this test binds to dynamic ports and a TOCTOU race would cause
# flakes under pytest-xdist. Tests in this module share a single xdist group.
pytestmark = pytest.mark.xdist_group(name="tls_integration")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_healthy(url: str, cafile: Path, timeout: float = 20.0) -> None:
    ctx = ssl.create_default_context(cafile=str(cafile))
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with httpx.Client(verify=ctx) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        time.sleep(0.2)
    msg = f"service at {url} did not become healthy: {last_exc}"
    raise RuntimeError(msg)


@pytest.fixture(scope="module")
def tls_stack(tmp_path_factory: pytest.TempPathFactory) -> Generator[dict[str, object]]:
    """Bring up orchestrator, tts-stub, and tts-grpc-stub over TLS."""
    certs_dir = tmp_path_factory.mktemp("certs")
    script = Path(__file__).resolve().parents[2] / "scripts" / "generate_dev_certs.py"
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
    base_env["ACHERON_REGISTRATION_TOKEN"] = "test-token"
    base_env["ACHERON_TLS_CA_FILE"] = str(ca)
    base_env["ACHERON_STORE_BACKEND"] = "memory"
    base_env["PYTHONPATH"] = (
        str(Path(__file__).resolve().parents[2] / "src")
        + os.pathsep
        + str(Path(__file__).resolve().parents[2] / "stubs")
        + os.pathsep
        + base_env.get("PYTHONPATH", "")
    )

    orch_env = {
        **base_env,
        "ACHERON_TLS_CERT_FILE": str(certs_dir / "orchestrator.crt"),
        "ACHERON_TLS_KEY_FILE": str(certs_dir / "orchestrator.key"),
    }
    tts_env = {
        **base_env,
        "WORKER_TYPE": "TTS",
        "WORKER_ENDPOINT": f"https://127.0.0.1:{tts_port}",
        "ORCHESTRATOR_URL": f"https://127.0.0.1:{orch_port}",
        "WORKER_PORT": str(tts_port),
        "ACHERON_TLS_CERT_FILE": str(certs_dir / "tts-stub.crt"),
        "ACHERON_TLS_KEY_FILE": str(certs_dir / "tts-stub.key"),
    }
    grpc_env = {
        **base_env,
        "WORKER_ENDPOINT": f"127.0.0.1:{grpc_port}",
        "ORCHESTRATOR_URL": f"https://127.0.0.1:{orch_port}",
        "WORKER_PORT": str(grpc_port),
        "WORKER_HTTP_PORT": str(grpc_http_port),
        "ACHERON_TLS_CERT_FILE": str(certs_dir / "tts-grpc-stub.crt"),
        "ACHERON_TLS_KEY_FILE": str(certs_dir / "tts-grpc-stub.key"),
    }

    procs: list[subprocess.Popen[bytes]] = []
    try:
        orch_proc = subprocess.Popen(
            [str(venv_python), "-m", "acheron.shell.api", "--port", str(orch_port)],
            env=orch_env,
        )
        procs.append(orch_proc)
        tts_proc = subprocess.Popen(
            [str(venv_python), "-m", "stubs.worker_stub"],
            env=tts_env,
        )
        procs.append(tts_proc)
        grpc_proc = subprocess.Popen(
            [str(venv_python), "-m", "stubs.grpc_worker_stub"],
            env=grpc_env,
        )
        procs.append(grpc_proc)
        _wait_healthy(f"https://127.0.0.1:{orch_port}/health", ca)
        _wait_healthy(f"https://127.0.0.1:{tts_port}/health", ca)
        # The gRPC HTTP /health sidecar is plain HTTP — it's a healthcheck-only endpoint
        # that doesn't have TLS. Docker healthchecks are internal and don't need encryption.
        _wait_healthy(f"http://127.0.0.1:{grpc_http_port}/health", ca)
        time.sleep(2)
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


def test_http_worker_registers_over_https(tls_stack: dict[str, object]) -> None:
    ca = tls_stack["ca"]
    port = tls_stack["orch_port"]
    ctx = ssl.create_default_context(cafile=str(ca))
    with httpx.Client(verify=ctx) as client:
        resp = client.get(f"https://127.0.0.1:{port}/workers")
        assert resp.status_code == 200
        workers = resp.json()["workers"]
        ids = {w["worker_id"] for w in workers}
        assert "tts-stub" in ids


def test_grpc_worker_registers(tls_stack: dict[str, object]) -> None:
    ca = tls_stack["ca"]
    port = tls_stack["orch_port"]
    ctx = ssl.create_default_context(cafile=str(ca))
    with httpx.Client(verify=ctx) as client:
        resp = client.get(f"https://127.0.0.1:{port}/workers")
        workers = resp.json()["workers"]
        ids = {w["worker_id"] for w in workers}
        assert "tts-grpc-stub" in ids
