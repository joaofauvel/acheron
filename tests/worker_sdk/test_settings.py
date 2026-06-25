"""Tests for WorkerSettings."""

import pydantic
import pytest

from acheron.worker_sdk.settings import WorkerSettings


class TestDefaults:
    def test_minimal_settings_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_WORKER__WORKER_ID", "qwen3tts-1")
        monkeypatch.setenv("ACHERON_WORKER__ORCHESTRATOR_URL", "http://orch:8000")
        s = WorkerSettings()  # type: ignore[call-arg]
        assert s.worker_id == "qwen3tts-1"
        assert s.orchestrator_url == "http://orch:8000"
        assert s.listen_port == 8001
        assert s.price_source == "runpod"
        assert s.execution_timeout_s == 1800.0
        assert s.default_speaker == "Ryan"
        assert s.log_level == "INFO"
        assert s.worker_host is None
        assert s.runpod_base_url is None

    def test_output_mode_field_is_removed(self) -> None:
        """`output_mode` and `output_volume_dir` were dropped (CFG-007): the
        volume path is unimplemented and the edge transport is always HTTP
        multipart, so the field was a silent knob. Rejecting it via
        ``extra="forbid"`` makes the misconfiguration loud."""
        assert "output_mode" not in WorkerSettings.model_fields
        assert "output_volume_dir" not in WorkerSettings.model_fields
        with pytest.raises(pydantic.ValidationError):
            WorkerSettings(  # type: ignore[call-arg]
                worker_id="w",
                orchestrator_url="http://o:8000",
                output_mode="multipart",
            )

    def test_per_language_defaults_empty_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_WORKER__WORKER_ID", "w")
        monkeypatch.setenv("ACHERON_WORKER__ORCHESTRATOR_URL", "http://o:8000")
        s = WorkerSettings()  # type: ignore[call-arg]
        assert s.per_language_defaults == {}

    def test_log_level_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ACHERON_WORKER__LOG_LEVEL maps to log_level."""
        monkeypatch.setenv("ACHERON_WORKER__WORKER_ID", "w")
        monkeypatch.setenv("ACHERON_WORKER__ORCHESTRATOR_URL", "http://o:8000")
        monkeypatch.setenv("ACHERON_WORKER__LOG_LEVEL", "DEBUG")
        s = WorkerSettings()  # type: ignore[call-arg]
        assert s.log_level == "DEBUG"

    def test_runpod_base_url_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ACHERON_WORKER__RUNPOD_BASE_URL maps to runpod_base_url."""
        monkeypatch.setenv("ACHERON_WORKER__WORKER_ID", "w")
        monkeypatch.setenv("ACHERON_WORKER__ORCHESTRATOR_URL", "http://o:8000")
        monkeypatch.setenv("ACHERON_WORKER__RUNPOD_BASE_URL", "http://mock-runpod:8000")
        s = WorkerSettings()  # type: ignore[call-arg]
        assert s.runpod_base_url == "http://mock-runpod:8000"

    def test_worker_host_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """WORKER_HOST (no prefix) maps to worker_host."""
        monkeypatch.setenv("ACHERON_WORKER__WORKER_ID", "w")
        monkeypatch.setenv("ACHERON_WORKER__ORCHESTRATOR_URL", "http://o:8000")
        monkeypatch.setenv("WORKER_HOST", "edge-host-1")
        s = WorkerSettings()  # type: ignore[call-arg]
        assert s.worker_host == "edge-host-1"


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
        monkeypatch.setenv("ACHERON_WORKER__WORKER_ID", "w")
        monkeypatch.setenv("ACHERON_WORKER__ORCHESTRATOR_URL", "http://o:8000")
        monkeypatch.setenv("ACHERON_WORKER__RUNPOD_API_KEY", "rk_abc")
        monkeypatch.setenv("ACHERON_WORKER__RUNPOD_ENDPOINT_ID", "i02xupws")
        s = WorkerSettings()  # type: ignore[call-arg]
        assert s.runpod_api_key == "rk_abc"
        assert s.runpod_endpoint_id == "i02xupws"


class TestValidation:
    def test_worker_id_required(self) -> None:
        with pytest.raises(pydantic.ValidationError, match="worker_id"):
            WorkerSettings()  # type: ignore[call-arg]

    def test_orchestrator_url_required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_WORKER__WORKER_ID", "w")
        with pytest.raises(pydantic.ValidationError, match="orchestrator_url"):
            WorkerSettings()  # type: ignore[call-arg]
