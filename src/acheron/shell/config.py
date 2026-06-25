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

_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class UnsetEnvVarError(ValueError):
    """Raised when a ${VAR} reference in config YAML resolves to an unset env var.

    ${VAR:-default} syntax is the supported escape hatch for optional vars.
    """


def _resolve_env_var(match: re.Match[str]) -> str:
    """Resolve a single ${VAR} or ${VAR:-default} reference against os.environ."""
    name, default = match.group(1), match.group(2)
    if name in os.environ:
        return os.environ[name]
    if default is not None:
        return default
    msg = (
        f"Config references ${{{name}}} but the env var is not set. "
        f"Set {name} or use ${{{name}:-<default>}} to provide a fallback."
    )
    raise UnsetEnvVarError(msg)


def _expand_env_vars(value: Any) -> Any:  # noqa: ANN401
    """Recursively expand ${VAR} references in string values from os.environ."""
    if isinstance(value, str):
        return _ENV_VAR_PATTERN.sub(_resolve_env_var, value)
    if isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(v) for v in value]
    return value


class OrchestratorSettings(BaseModel):
    """Orchestrator-level settings."""

    data_dir: Path = Field(default=Path("/data/jobs"))
    registration_token: str | None = Field(default=None)
    open_registration: bool = False
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
    """Maps flat ACHERON_* vars to their nested settings.

    Placed below env_settings so the structured form (e.g.
    ``ACHERON_ORCHESTRATOR__DATA_DIR``) wins over the flat alias
    (``ACHERON_DATA_DIR``), and above YAML so the alias overrides file
    config.
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
        open_reg = os.environ.get("ACHERON_OPEN_REGISTRATION")
        if open_reg == "1":
            res.setdefault("orchestrator", {})["open_registration"] = True
        return res


class Settings(BaseSettings):
    """Top-level settings loaded from YAML, env vars, or defaults."""

    model_config = SettingsConfigDict(env_nested_delimiter="__", env_prefix="ACHERON_", extra="ignore")

    orchestrator: OrchestratorSettings = Field(default_factory=OrchestratorSettings)
    workers: WorkerSettings = Field(default_factory=WorkerSettings)
    providers: ProvidersSettings = Field(default_factory=ProvidersSettings)
    chars_per_token: int = Field(
        default=1
    )  # chars-per-token estimate used by compile_plan chunking validation; 1 is the CJK worst case

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
