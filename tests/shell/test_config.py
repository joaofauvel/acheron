import os
from pathlib import Path
import pytest
from acheron.shell.config import Settings, load_settings

def test_default_settings() -> None:
    settings = Settings()
    assert settings.orchestrator.data_dir == Path("/data/jobs")
    assert settings.workers.chunking.max_chunk_length == 250
    assert settings.workers.packaging.bitrate == "128k"

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

def test_settings_env_var_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    yaml_content = "orchestrator:\n  data_dir: '/tmp/yaml_dir'"
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    monkeypatch.setenv("ACHERON_CONFIG_PATH", str(config_file))
    monkeypatch.setenv("ACHERON_ORCHESTRATOR__DATA_DIR", "/tmp/env_dir")
    
    settings = load_settings()
    assert settings.orchestrator.data_dir == Path("/tmp/env_dir")
