"""Tests for worker registration-token providers."""

from pathlib import Path

from acheron.worker_sdk.token_auth import EnvironmentOrFileTokenProvider


def test_provider_prefers_nonempty_env_token(tmp_path: Path) -> None:
    token_file = tmp_path / ".registration_token"
    token_file.write_text("file-token", encoding="utf-8")

    provider = EnvironmentOrFileTokenProvider(" env-token ", token_file)

    assert provider.current() == "env-token"


def test_provider_reads_latest_file_value(tmp_path: Path) -> None:
    token_file = tmp_path / ".registration_token"
    token_file.write_text("first-token", encoding="utf-8")
    provider = EnvironmentOrFileTokenProvider("", token_file)

    assert provider.current() == "first-token"
    token_file.write_text("second-token", encoding="utf-8")
    assert provider.current() == "second-token"


def test_provider_treats_empty_values_as_unset(tmp_path: Path) -> None:
    token_file = tmp_path / ".registration_token"
    token_file.write_text("  ", encoding="utf-8")

    assert EnvironmentOrFileTokenProvider("", token_file).current() is None
    assert EnvironmentOrFileTokenProvider(None, None).current() is None
