"""Tests for WorkerSettings."""

import pydantic
import pytest

from acheron.worker_sdk.settings import WorkerSettings


class TestDefaults:
    def test_minimal_settings_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_WORKER_WORKER_ID", "qwen3tts-1")
        monkeypatch.setenv("ACHERON_WORKER_ORCHESTRATOR_URL", "http://orch:8000")
        s = WorkerSettings()  # type: ignore[call-arg]
        assert s.worker_id == "qwen3tts-1"
        assert s.orchestrator_url == "http://orch:8000"
        assert s.listen_port == 8001
        assert s.price_source == "runpod"
        assert s.output_mode == "multipart"
        assert s.execution_timeout_s == 1800.0
        assert s.default_speaker == "Ryan"

    def test_per_language_defaults_empty_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_WORKER_WORKER_ID", "w")
        monkeypatch.setenv("ACHERON_WORKER_ORCHESTRATOR_URL", "http://o:8000")
        s = WorkerSettings()  # type: ignore[call-arg]
        assert s.per_language_defaults == {}


class TestEnvOnlyFields:
    @pytest.mark.parametrize(
        "field",
        ["registration_token", "runpod_api_key", "runpod_endpoint_id"],
    )
    def test_env_only_field_rejected_by_explicit_construction(self, field: str) -> None:
        with pytest.raises(pydantic.ValidationError, match=field):
            WorkerSettings(
                worker_id="w",
                orchestrator_url="http://o:8000",
                **{field: "secret"},  # type: ignore[arg-type]
            )

    def test_env_only_field_accepted_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_WORKER_WORKER_ID", "w")
        monkeypatch.setenv("ACHERON_WORKER_ORCHESTRATOR_URL", "http://o:8000")
        monkeypatch.setenv("ACHERON_WORKER_RUNPOD_API_KEY", "rk_abc")
        monkeypatch.setenv("ACHERON_WORKER_RUNPOD_ENDPOINT_ID", "i02xupws")
        s = WorkerSettings()  # type: ignore[call-arg]
        assert s.runpod_api_key == "rk_abc"
        assert s.runpod_endpoint_id == "i02xupws"


class TestValidation:
    def test_volume_mode_requires_output_volume_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_WORKER_WORKER_ID", "w")
        monkeypatch.setenv("ACHERON_WORKER_ORCHESTRATOR_URL", "http://o:8000")
        monkeypatch.setenv("ACHERON_WORKER_OUTPUT_MODE", "volume")
        with pytest.raises(pydantic.ValidationError, match="output_volume_dir"):
            WorkerSettings()  # type: ignore[call-arg]

    def test_worker_id_required(self) -> None:
        with pytest.raises(pydantic.ValidationError, match="worker_id"):
            WorkerSettings()  # type: ignore[call-arg]

    def test_orchestrator_url_required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_WORKER_WORKER_ID", "w")
        with pytest.raises(pydantic.ValidationError, match="orchestrator_url"):
            WorkerSettings()  # type: ignore[call-arg]
