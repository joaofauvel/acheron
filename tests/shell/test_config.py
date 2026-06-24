from __future__ import annotations

from pathlib import Path

import pytest

from acheron.shell.config import Settings, load_settings


def test_default_settings() -> None:
    settings = Settings()
    assert settings.orchestrator.data_dir == Path("/data/jobs")
    assert settings.workers.chunking.max_chunk_length == 250
    assert settings.workers.packaging.bitrate == "128k"
    assert settings.workers.packaging.max_fmt_chunk_length == 65536
    assert settings.chars_per_token == 4


def test_chars_per_token_override_from_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    yaml_content = "chars_per_token: 3\n"
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    monkeypatch.setenv("ACHERON_CONFIG_PATH", str(config_file))
    settings = load_settings()
    assert settings.chars_per_token == 3


def test_load_settings_from_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    yaml_content = """
orchestrator:
  data_dir: "/tmp/custom_jobs"
  health_check_interval_seconds: 45
workers:
  chunking:
    max_chunk_length: 500
  packaging:
    bitrate: "192k"
    codec: "mp3"
    max_fmt_chunk_length: 1024
"""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    monkeypatch.setenv("ACHERON_CONFIG_PATH", str(config_file))

    settings = load_settings()
    assert settings.orchestrator.data_dir == Path("/tmp/custom_jobs")
    assert settings.orchestrator.health_check_interval_seconds == 45
    assert settings.workers.chunking.max_chunk_length == 500
    assert settings.workers.packaging.bitrate == "192k"
    assert settings.workers.packaging.codec == "mp3"
    assert settings.workers.packaging.max_fmt_chunk_length == 1024


def test_settings_env_var_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    yaml_content = "orchestrator:\n  data_dir: '/tmp/yaml_dir'"
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    monkeypatch.setenv("ACHERON_CONFIG_PATH", str(config_file))
    monkeypatch.setenv("ACHERON_ORCHESTRATOR__DATA_DIR", "/tmp/env_dir")

    settings = load_settings()
    assert settings.orchestrator.data_dir == Path("/tmp/env_dir")


def test_settings_acheron_data_dir_alias_overrides_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ACHERON_DATA_DIR (flat alias) overrides YAML but loses to the structured form."""
    yaml_content = "orchestrator:\n  data_dir: '/tmp/yaml_dir'"
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    monkeypatch.setenv("ACHERON_CONFIG_PATH", str(config_file))
    monkeypatch.setenv("ACHERON_DATA_DIR", str(tmp_path))

    settings = load_settings()
    assert settings.orchestrator.data_dir == tmp_path


def test_settings_structured_env_beats_flat_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ACHERON_ORCHESTRATOR__DATA_DIR takes priority over ACHERON_DATA_DIR."""
    structured = tmp_path / "structured"
    structured.mkdir()
    flat = tmp_path / "flat"
    flat.mkdir()
    monkeypatch.setenv("ACHERON_DATA_DIR", str(flat))
    monkeypatch.setenv("ACHERON_ORCHESTRATOR__DATA_DIR", str(structured))

    settings = load_settings()
    assert settings.orchestrator.data_dir == structured


def test_load_settings_default_search_path_yml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Orchestrator loads settings from ./acheron.yml if ACHERON_CONFIG_PATH is unset and no acheron.yaml is present."""
    yaml_content = "orchestrator:\n  data_dir: '/tmp/yml_default_dir'"
    yml_file = tmp_path / "acheron.yml"
    yml_file.write_text(yaml_content, encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ACHERON_CONFIG_PATH", raising=False)

    settings = load_settings()
    assert settings.orchestrator.data_dir == Path("/tmp/yml_default_dir")


def test_providers_default_empty() -> None:
    settings = Settings()
    assert settings.providers.runpod.api_key is None
    assert settings.providers.huggingface.api_key is None


def test_providers_from_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    yaml_content = """
providers:
  runpod:
    api_key: "rp-secret"
  huggingface:
    api_key: "hf-secret"
"""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    monkeypatch.setenv("ACHERON_CONFIG_PATH", str(config_file))
    settings = load_settings()
    assert settings.providers.runpod.api_key == "rp-secret"
    assert settings.providers.huggingface.api_key == "hf-secret"


def test_yaml_env_var_expansion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "expanded-rp-key")
    yaml_content = """
providers:
  runpod:
    api_key: "${RUNPOD_API_KEY}"
"""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    monkeypatch.setenv("ACHERON_CONFIG_PATH", str(config_file))
    settings = load_settings()
    assert settings.providers.runpod.api_key == "expanded-rp-key"


def test_yaml_env_var_unset_expands_to_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unset ${VAR} must expand to empty string (falsy → provider not created)."""
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    yaml_content = """
providers:
  runpod:
    api_key: "${RUNPOD_API_KEY}"
"""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    monkeypatch.setenv("ACHERON_CONFIG_PATH", str(config_file))
    settings = load_settings()
    assert settings.providers.runpod.api_key == ""


def test_yaml_env_var_expansion_nested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """${VAR} references must be expanded inside nested dicts and lists."""
    monkeypatch.setenv("MY_VAR", "hello")
    yaml_content = """
orchestrator:
  registration_token: "${MY_VAR}-suffix"
"""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    monkeypatch.setenv("ACHERON_CONFIG_PATH", str(config_file))
    settings = load_settings()
    assert settings.orchestrator.registration_token == "hello-suffix"
