"""Tests for the Acheron CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest
import respx
from click.testing import CliRunner

from acheron import cli as cli_module
from acheron.cli import main

_BASE_URL = "http://test.local:8000"


@pytest.fixture(autouse=True)
def _stable_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ACHERON_URL so the CLI default doesn't leak into tests."""
    monkeypatch.setenv("ACHERON_URL", _BASE_URL)


@respx.mock
def test_submit_epub(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    respx.post(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(201, json={"job_id": "job-abc", "status": "running", "plan_id": "plan-1"})
    )
    runner = CliRunner()
    result = runner.invoke(main, ["submit", str(epub), "--src", "en", "--dest", "es"])
    assert result.exit_code == 0
    assert "job-abc" in result.output
    assert "running" in result.output


@respx.mock
def test_submit_audio(tmp_path: Path) -> None:
    mp3 = tmp_path / "podcast.mp3"
    mp3.touch()
    respx.post(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(201, json={"job_id": "job-def", "status": "running"})
    )
    runner = CliRunner()
    result = runner.invoke(main, ["submit", str(mp3), "--src", "en", "--dest", "es", "--asr", "whisper-v3"])
    assert result.exit_code == 0
    assert "job-def" in result.output


@respx.mock
def test_submit_with_type_override(tmp_path: Path) -> None:
    unknown = tmp_path / "input.dat"
    unknown.touch()
    respx.post(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(201, json={"job_id": "job-xyz", "status": "running"})
    )
    runner = CliRunner()
    result = runner.invoke(main, ["submit", str(unknown), "--src", "en", "--dest", "es", "--type", "epub"])
    assert result.exit_code == 0
    assert "job-xyz" in result.output


def test_submit_unknown_type(tmp_path: Path) -> None:
    unknown = tmp_path / "input.dat"
    unknown.touch()
    runner = CliRunner()
    result = runner.invoke(main, ["submit", str(unknown), "--src", "en", "--dest", "es"])
    assert result.exit_code == 1
    assert "Cannot detect source type" in result.output


def test_submit_missing_file() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["submit", "/nonexistent.epub", "--src", "en", "--dest", "es"])
    assert result.exit_code != 0


@respx.mock
def test_status() -> None:
    respx.get(f"{_BASE_URL}/jobs/job-abc").mock(
        return_value=httpx.Response(
            200,
            json={
                "job_id": "job-abc",
                "status": "running",
                "plan_id": "plan-1",
                "completed_steps": 2,
                "total_steps": 5,
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["status", "job-abc"])
    assert result.exit_code == 0
    assert "job-abc" in result.output
    assert "2/5" in result.output


@respx.mock
def test_status_verbose() -> None:
    respx.get(f"{_BASE_URL}/jobs/job-abc").mock(
        return_value=httpx.Response(
            200,
            json={"job_id": "job-abc", "status": "failed", "errors": ["Worker timeout"]},
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["status", "job-abc", "-v"])
    assert result.exit_code == 0
    assert "Worker timeout" in result.output


@respx.mock
def test_status_not_found() -> None:
    respx.get(f"{_BASE_URL}/jobs/nonexistent").mock(return_value=httpx.Response(404, json={"detail": "Job not found"}))
    runner = CliRunner()
    result = runner.invoke(main, ["status", "nonexistent"])
    assert result.exit_code != 0
    assert "404" in result.output
    assert "Job not found" in result.output


@respx.mock
def test_jobs_empty() -> None:
    respx.get(f"{_BASE_URL}/jobs").mock(return_value=httpx.Response(200, json={"jobs": []}))
    runner = CliRunner()
    result = runner.invoke(main, ["jobs"])
    assert result.exit_code == 0
    assert "No jobs found" in result.output


@respx.mock
def test_jobs_list() -> None:
    respx.get(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "job_id": "job-1",
                        "status": "running",
                        "plan_id": "plan-1",
                        "completed_steps": 1,
                        "total_steps": 3,
                    },
                    {
                        "job_id": "job-2",
                        "status": "completed",
                        "plan_id": "plan-2",
                        "completed_steps": 3,
                        "total_steps": 3,
                    },
                ]
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["jobs"])
    assert result.exit_code == 0
    assert "job-1" in result.output
    assert "job-2" in result.output


@respx.mock
def test_jobs_filter_active() -> None:
    respx.get(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {"job_id": "job-1", "status": "running", "completed_steps": 0, "total_steps": 0},
                    {"job_id": "job-2", "status": "completed", "completed_steps": 3, "total_steps": 3},
                ]
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["jobs", "--active"])
    assert result.exit_code == 0
    assert "job-1" in result.output
    assert "job-2" not in result.output


@respx.mock
def test_jobs_filter_completed() -> None:
    respx.get(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {"job_id": "job-1", "status": "running", "completed_steps": 0, "total_steps": 0},
                    {"job_id": "job-2", "status": "completed", "completed_steps": 3, "total_steps": 3},
                ]
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["jobs", "--completed"])
    assert result.exit_code == 0
    assert "job-1" not in result.output
    assert "job-2" in result.output


@respx.mock
def test_workers_empty() -> None:
    respx.get(f"{_BASE_URL}/workers").mock(return_value=httpx.Response(200, json={"workers": []}))
    runner = CliRunner()
    result = runner.invoke(main, ["workers"])
    assert result.exit_code == 0
    assert "No workers registered" in result.output


@respx.mock
def test_workers_list() -> None:
    respx.get(f"{_BASE_URL}/workers").mock(
        return_value=httpx.Response(
            200,
            json={
                "workers": [
                    {
                        "worker_id": "tts-1",
                        "endpoint": "http://tts:8000",
                        "transport": "http",
                        "worker_type": "tts",
                        "consecutive_failures": 0,
                    }
                ]
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["workers"])
    assert result.exit_code == 0
    assert "tts-1" in result.output
    assert "http://tts:8000" in result.output


@respx.mock
def test_capabilities_empty() -> None:
    respx.get(f"{_BASE_URL}/capabilities").mock(return_value=httpx.Response(200, json={"language_pairs": []}))
    runner = CliRunner()
    result = runner.invoke(main, ["capabilities"])
    assert result.exit_code == 0
    assert "No language pairs" in result.output


@respx.mock
def test_capabilities_list() -> None:
    respx.get(f"{_BASE_URL}/capabilities").mock(
        return_value=httpx.Response(
            200,
            json={
                "language_pairs": [
                    {"src": "en", "dst": "es", "workers": ["tts-1", "trans-1"]},
                    {"src": "en", "dst": "fr", "workers": ["tts-2"]},
                ]
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["capabilities"])
    assert result.exit_code == 0
    assert "en" in result.output
    assert "es" in result.output


@respx.mock
def test_capabilities_filter_src() -> None:
    respx.get(f"{_BASE_URL}/capabilities", params={"src": "en"}).mock(
        return_value=httpx.Response(
            200,
            json={"language_pairs": [{"src": "en", "dst": "es", "workers": ["tts-1"]}]},
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["capabilities", "--src", "en"])
    assert result.exit_code == 0
    assert "en" in result.output


@respx.mock
def test_capabilities_filter_dest() -> None:
    respx.get(f"{_BASE_URL}/capabilities", params={"dest": "es"}).mock(
        return_value=httpx.Response(
            200,
            json={"language_pairs": [{"src": "en", "dst": "es", "workers": ["tts-1"]}]},
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["capabilities", "--dest", "es"])
    assert result.exit_code == 0
    assert "es" in result.output


@respx.mock
def test_submit_server_error_shows_friendly_message(tmp_path: Path) -> None:
    respx.post(f"{_BASE_URL}/jobs").mock(return_value=httpx.Response(500, json={"detail": "Internal server error"}))
    epub = tmp_path / "book.epub"
    epub.touch()
    runner = CliRunner()
    result = runner.invoke(main, ["submit", str(epub), "--src", "en", "--dest", "es"])
    assert result.exit_code != 0
    assert "500" in result.output
    assert "Internal server error" in result.output


@respx.mock
def test_jobs_server_error_shows_friendly_message() -> None:
    respx.get(f"{_BASE_URL}/jobs").mock(return_value=httpx.Response(503, json={"detail": "Service unavailable"}))
    runner = CliRunner()
    result = runner.invoke(main, ["jobs"])
    assert result.exit_code != 0
    assert "503" in result.output
    assert "Service unavailable" in result.output


@respx.mock
def test_workers_server_error_shows_friendly_message() -> None:
    respx.get(f"{_BASE_URL}/workers").mock(return_value=httpx.Response(500, json={"detail": "Registry failure"}))
    runner = CliRunner()
    result = runner.invoke(main, ["workers"])
    assert result.exit_code != 0
    assert "500" in result.output
    assert "Registry failure" in result.output


@respx.mock
def test_submit_validation_error_shows_detail(tmp_path: Path) -> None:
    respx.post(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(422, json={"detail": "Invalid language path: en→xx"})
    )
    epub = tmp_path / "book.epub"
    epub.touch()
    runner = CliRunner()
    result = runner.invoke(main, ["submit", str(epub), "--src", "en", "--dest", "xx"])
    assert result.exit_code != 0
    assert "422" in result.output
    assert "Invalid language path" in result.output


@respx.mock
def test_connect_error_shows_friendly_message() -> None:
    respx.get(f"{_BASE_URL}/jobs").mock(side_effect=httpx.ConnectError("Connection refused"))
    runner = CliRunner()
    result = runner.invoke(main, ["jobs"])
    assert result.exit_code != 0
    assert "Cannot connect" in result.output
    assert "server running" in result.output.lower()


@respx.mock
def test_ssl_verification_error_shows_trust_store_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """TLS verification failure points the user at the trust store env vars.

    httpx wraps SSLCertVerificationError as a ConnectError (the failure
    happens during start_tls, which is part of the connection phase). The
    CLI distinguishes that from a real connect-refused and tells the user
    how to fix the trust store.
    """
    import ssl

    def _make_connect_with_ssl_cause() -> httpx.ConnectError:
        ssl_exc = ssl.SSLCertVerificationError("certificate verify failed")
        return ssl_exc_to_connect(ssl_exc)

    def ssl_exc_to_connect(ssl_exc: ssl.SSLCertVerificationError) -> httpx.ConnectError:
        inner = httpx.ConnectError("inner")
        inner.__cause__ = ssl_exc
        outer = httpx.ConnectError("TLS failed")
        outer.__cause__ = inner
        return outer

    class _FakeClient:
        async def list_jobs(self) -> list[dict[str, Any]]:
            raise _make_connect_with_ssl_cause()

    monkeypatch.setattr(cli_module, "_get_client", _FakeClient)
    runner = CliRunner()
    result = runner.invoke(main, ["jobs"])
    assert result.exit_code != 0
    assert "TLS" in result.output or "SSL" in result.output or "certificate" in result.output.lower()
    assert "SSL_CERT_FILE" in result.output or "ACHERON_TLS_CA_FILE" in result.output


def test_is_ssl_error_walks_cause_chain() -> None:
    """The walker follows __cause__ and __context__ to find SSLError causes."""
    import ssl

    ssl_exc = ssl.SSLCertVerificationError("verify failed")
    inner = httpx.ConnectError("inner")
    inner.__cause__ = ssl_exc
    outer = httpx.ConnectError("outer")
    outer.__cause__ = inner
    assert cli_module._is_ssl_error(outer)  # noqa: SLF001


def test_is_ssl_error_returns_false_for_plain_connect_error() -> None:
    plain = httpx.ConnectError("Connection refused")
    assert not cli_module._is_ssl_error(plain)  # noqa: SLF001


class _CapturedClient:
    """Sentinel object that records the kwargs AcheronClient was called with."""

    instances: ClassVar[list[_CapturedClient]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.instances.append(self)

    def __getattr__(self, name: str) -> Any:
        msg = f"_CapturedClient used as a real client: .{name}"
        raise AssertionError(msg)


@pytest.fixture
def captured_client(monkeypatch: pytest.MonkeyPatch) -> list[_CapturedClient]:
    captured: list[_CapturedClient] = []
    monkeypatch.setattr(_CapturedClient, "instances", captured)
    monkeypatch.setattr(cli_module, "AcheronClient", _CapturedClient)
    return captured


def test_default_url_is_https(monkeypatch: pytest.MonkeyPatch, captured_client: list[_CapturedClient]) -> None:
    """CLI defaults to https:// so it works against the dev/HTTPS orchestrator."""
    monkeypatch.delenv("ACHERON_URL", raising=False)
    cli_module._get_client()  # noqa: SLF001
    assert captured_client[0].args[0] == "https://localhost:8000"


def test_verify_uses_acheron_ca_file(
    monkeypatch: pytest.MonkeyPatch, captured_client: list[_CapturedClient], tmp_path: Path
) -> None:
    monkeypatch.delenv("ACHERON_URL", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    ca = tmp_path / "ca.crt"
    ca.touch()
    monkeypatch.setenv("ACHERON_TLS_CA_FILE", str(ca))
    cli_module._get_client()  # noqa: SLF001
    assert captured_client[0].kwargs["verify"] == str(ca)


def test_verify_falls_back_to_ssl_cert_file(
    monkeypatch: pytest.MonkeyPatch, captured_client: list[_CapturedClient], tmp_path: Path
) -> None:
    monkeypatch.delenv("ACHERON_URL", raising=False)
    monkeypatch.delenv("ACHERON_TLS_CA_FILE", raising=False)
    ca = tmp_path / "ca.crt"
    ca.touch()
    monkeypatch.setenv("SSL_CERT_FILE", str(ca))
    cli_module._get_client()  # noqa: SLF001
    assert captured_client[0].kwargs["verify"] == str(ca)


def test_verify_defaults_to_true_when_no_ca_env(
    monkeypatch: pytest.MonkeyPatch, captured_client: list[_CapturedClient], tmp_path: Path
) -> None:
    monkeypatch.delenv("ACHERON_URL", raising=False)
    monkeypatch.delenv("ACHERON_TLS_CA_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    # Chdir to a directory with no dev CA so auto-discovery doesn't kick in.
    monkeypatch.chdir(tmp_path)
    cli_module._get_client()  # noqa: SLF001
    assert captured_client[0].kwargs["verify"] is True


def test_acheron_ca_takes_precedence_over_ssl_cert_file(
    monkeypatch: pytest.MonkeyPatch, captured_client: list[_CapturedClient], tmp_path: Path
) -> None:
    """ACHERON_TLS_CA_FILE is the explicit override and wins over SSL_CERT_FILE."""
    monkeypatch.delenv("ACHERON_URL", raising=False)
    acheron_ca = tmp_path / "acheron-ca.crt"
    other_ca = tmp_path / "other.crt"
    acheron_ca.touch()
    other_ca.touch()
    monkeypatch.setenv("ACHERON_TLS_CA_FILE", str(acheron_ca))
    monkeypatch.setenv("SSL_CERT_FILE", str(other_ca))
    cli_module._get_client()  # noqa: SLF001
    assert captured_client[0].kwargs["verify"] == str(acheron_ca)


def test_verify_auto_discovers_dev_ca_in_certs_dir(
    monkeypatch: pytest.MonkeyPatch, captured_client: list[_CapturedClient], tmp_path: Path
) -> None:
    """Dev convenience: ./certs/acheron-ca.crt is picked up when no env var is set.

    Lets the host CLI work out of the box against the dev/HTTPS orchestrator.
    """
    monkeypatch.delenv("ACHERON_URL", raising=False)
    monkeypatch.delenv("ACHERON_TLS_CA_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    dev_ca = tmp_path / "certs" / "acheron-ca.crt"
    dev_ca.parent.mkdir(parents=True)
    dev_ca.touch()
    monkeypatch.chdir(tmp_path)
    cli_module._get_client()  # noqa: SLF001
    assert captured_client[0].kwargs["verify"] == str(dev_ca)


def test_env_var_overrides_dev_ca(
    monkeypatch: pytest.MonkeyPatch, captured_client: list[_CapturedClient], tmp_path: Path
) -> None:
    """Env vars win over dev auto-discovery — they're the explicit override."""
    monkeypatch.delenv("ACHERON_URL", raising=False)
    dev_ca = tmp_path / "certs" / "acheron-ca.crt"
    dev_ca.parent.mkdir(parents=True)
    dev_ca.touch()
    monkeypatch.chdir(tmp_path)
    explicit = tmp_path / "explicit.crt"
    explicit.touch()
    monkeypatch.setenv("SSL_CERT_FILE", str(explicit))
    cli_module._get_client()  # noqa: SLF001
    assert captured_client[0].kwargs["verify"] == str(explicit)
