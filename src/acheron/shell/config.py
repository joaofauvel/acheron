"""Pydantic Settings schema for acheron.yaml configuration."""

import logging
import os
import re
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

_logger = logging.getLogger(__name__)

_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand_env_vars(value: Any) -> Any:  # noqa: ANN401
    """Recursively expand ${VAR} references in string values from os.environ."""
    if isinstance(value, str):
        return _ENV_VAR_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(v) for v in value]
    return value


class OrchestratorSettings(BaseModel):
    """Orchestrator-level settings."""

    data_dir: Path = Field(default=Path("/data/jobs"))
    registration_token: str | None = Field(default=None)
    health_check_interval_seconds: int = Field(default=30)


class ChunkingSettings(BaseModel):
    """Chunking worker settings."""

    max_chunk_length: int = Field(default=250)


class PackagingSettings(BaseModel):
    """Packaging worker settings."""

    bitrate: str = Field(default="128k")
    codec: str = Field(default="aac")
    max_fmt_chunk_length: int = Field(default=65536)


class WorkerSettings(BaseModel):
    """Container for all worker-type-specific settings."""

    chunking: ChunkingSettings = Field(default_factory=ChunkingSettings)
    packaging: PackagingSettings = Field(default_factory=PackagingSettings)


class RunPodProviderSettings(BaseModel):
    """RunPod API credentials for platform health checks."""

    api_key: str | None = None


class HuggingFaceProviderSettings(BaseModel):
    """Hugging Face API credentials for platform health checks."""

    api_key: str | None = None


class ProvidersSettings(BaseModel):
    """Platform provider credentials for decoupled health checks."""

    runpod: RunPodProviderSettings = Field(default_factory=RunPodProviderSettings)
    huggingface: HuggingFaceProviderSettings = Field(default_factory=HuggingFaceProviderSettings)


class _YamlConfigSettingsSource(PydanticBaseSettingsSource):
    """Loads settings from acheron.yaml with search path precedence."""

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:  # noqa: ANN401, ARG002
        return None, "", False

    def __call__(self) -> dict[str, Any]:
        config_path_env = os.environ.get("ACHERON_CONFIG_PATH")
        search_paths: list[Path] = []
        if config_path_env:
            search_paths.append(Path(config_path_env))
        search_paths.extend(
            [
                Path("./acheron.yaml"),
                Path("./acheron.yml"),
                Path("/etc/acheron/acheron.yaml"),
                Path("/etc/acheron/acheron.yml"),
            ]
        )

        for path in search_paths:
            if not path.is_file():
                continue
            try:
                with path.open("r", encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}
                    return cast("dict[str, Any]", _expand_env_vars(raw))
            except yaml.YAMLError:
                _logger.warning("Failed to parse YAML config at %s; ignoring", path)
            except OSError:
                pass
        return {}


class _EnvAliasSettingsSource(PydanticBaseSettingsSource):
    """Maps flat ACHERON_DATA_DIR to nested orchestrator.data_dir.

    Placed below env_settings so ACHERON_ORCHESTRATOR__DATA_DIR (structured
    form) wins over ACHERON_DATA_DIR (flat alias), and above YAML so the
    alias overrides file config.
    """

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:  # noqa: ANN401, ARG002
        return None, "", False

    def __call__(self) -> dict[str, Any]:
        res: dict[str, Any] = {}
        data_dir = os.environ.get("ACHERON_DATA_DIR")
        if data_dir:
            res.setdefault("orchestrator", {})["data_dir"] = Path(data_dir)
        token = os.environ.get("ACHERON_REGISTRATION_TOKEN")
        if token:
            res.setdefault("orchestrator", {})["registration_token"] = token
        return res


class Settings(BaseSettings):
    """Top-level settings loaded from YAML, env vars, or defaults."""

    model_config = SettingsConfigDict(env_nested_delimiter="__", env_prefix="ACHERON_", extra="ignore")

    orchestrator: OrchestratorSettings = Field(default_factory=OrchestratorSettings)
    workers: WorkerSettings = Field(default_factory=WorkerSettings)
    providers: ProvidersSettings = Field(default_factory=ProvidersSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Register custom YAML and env-alias sources below the default env source."""
        return (
            init_settings,
            env_settings,
            _EnvAliasSettingsSource(settings_cls),
            _YamlConfigSettingsSource(settings_cls),
        )


def load_settings() -> Settings:
    """Load settings from env vars, YAML config, or defaults."""
    return Settings()
