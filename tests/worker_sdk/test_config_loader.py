"""Tests for WorkerSettings YAML discovery + env override."""

from pathlib import Path

import pytest

from acheron.worker_sdk.config_loader import load_settings


class TestDiscoveryOrder:
    def test_worker_config_env_var_wins_absolute(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml_path = tmp_path / "explicit.yaml"
        yaml_path.write_text("worker_id: fromfile\norchestrator_url: http://o:8000\n")
        monkeypatch.setenv("WORKER_CONFIG", str(yaml_path))
        s = load_settings()
        assert s.worker_id == "fromfile"

    def test_worker_name_worker_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "qwen3tts.worker.yaml").write_text(
            "worker_id: fromfile\norchestrator_url: http://o:8000\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("WORKER_NAME", "qwen3tts")
        monkeypatch.delenv("WORKER_CONFIG", raising=False)
        s = load_settings()
        assert s.worker_id == "fromfile"

    def test_worker_yaml_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "worker.yaml").write_text(
            "worker_id: fromfile\norchestrator_url: http://o:8000\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("WORKER_CONFIG", raising=False)
        monkeypatch.delenv("WORKER_NAME", raising=False)
        s = load_settings()
        assert s.worker_id == "fromfile"

    def test_env_only_fallback(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("ACHERON_WORKER__WORKER_ID", "envonly")
        monkeypatch.setenv("ACHERON_WORKER__ORCHESTRATOR_URL", "http://o:8000")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("WORKER_CONFIG", raising=False)
        monkeypatch.delenv("WORKER_NAME", raising=False)
        s = load_settings()
        assert s.worker_id == "envonly"


class TestEnvOverrideWins:
    def test_env_var_overrides_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml_path = tmp_path / "explicit.yaml"
        yaml_path.write_text(
            "worker_id: fromfile\norchestrator_url: http://o:8000\ndefault_speaker: Vivian\n"
        )
        monkeypatch.setenv("WORKER_CONFIG", str(yaml_path))
        monkeypatch.setenv("ACHERON_WORKER__DEFAULT_SPEAKER", "Ryan")
        s = load_settings()
        assert s.default_speaker == "Ryan"


class TestSecretRejection:
    def test_secret_in_yaml_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml_path = tmp_path / "explicit.yaml"
        yaml_path.write_text(
            "worker_id: fromfile\norchestrator_url: http://o:8000\nrunpod_api_key: rk_secret\n"
        )
        monkeypatch.setenv("WORKER_CONFIG", str(yaml_path))
        with pytest.raises(ValueError, match="env-only"):
            load_settings()
