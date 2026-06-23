"""Configuration for Acheron worker containers.

Env vars use the ``ACHERON_WORKER_`` prefix to avoid collision with the
orchestrator's own env namespace (``ACHERON_REGISTRATION_TOKEN`` etc.).

Secrets (``registration_token``, ``runpod_api_key``, ``runpod_endpoint_id``)
are env-only — rejected when passed to the constructor so they cannot
silently land in committed ``worker.yaml`` overrides or image layers.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

_ENV_ONLY_FIELDS: frozenset[str] = frozenset({
    "registration_token",
    "runpod_api_key",
    "runpod_endpoint_id",
})


class WorkerSettings(BaseSettings):
    """Resolved worker runtime configuration."""

    worker_id: str
    orchestrator_url: str

    registration_token: str | None = None
    runpod_api_key: str | None = None
    runpod_endpoint_id: str | None = None

    listen_host: str = "0.0.0.0"
    listen_port: int = 8001

    execution_timeout_s: float = 1800.0

    output_mode: Literal["multipart", "volume"] = "multipart"
    output_volume_dir: str | None = None

    price_source: Literal["runpod", "static", "zero"] = "runpod"
    secure_cloud: bool = False
    dollars_per_hour: float | None = None
    price_cache_ttl_s: float = 3600.0

    default_speaker: str = "Ryan"
    per_language_defaults: dict[str, str] = Field(default_factory=dict)

    handler: str = ""
    model_id: str | None = None

    model_config = SettingsConfigDict(
        env_prefix="ACHERON_WORKER_",
        extra="forbid",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Order sources so explicit env vars win over YAML/init kwargs.

        Workers are configured primarily via ``worker.yaml`` (init kwargs
        supplied by ``config_loader``); the operator can override any value
        by exporting the corresponding ``ACHERON_WORKER_*`` env var on the
        container — env must take precedence so the same image can be
        retargeted at runtime without rebuilding.
        """
        return env_settings, init_settings, dotenv_settings, file_secret_settings

    @model_validator(mode="before")
    @classmethod
    def _reject_env_only_fields(cls, data: Any) -> Any:
        """Reject env-only fields when supplied via constructor / YAML.

        Runs after pydantic-settings has merged the env overlay with the
        init kwargs, so ``data`` here already contains env-derived values.
        We need to tell the two sources apart: detect when an env-only
        field has a value but its env-var equivalent is unset (i.e. it
        could only have come from explicit init kwargs).
        """
        if not isinstance(data, Mapping):
            return data
        for field_name in _ENV_ONLY_FIELDS & data.keys():
            if data[field_name] is None:
                continue
            env_var = f"ACHERON_WORKER_{field_name.upper()}"
            import os

            if env_var not in os.environ:
                msg = (
                    f"Field {field_name!r} is env-only and cannot be set via "
                    f"constructor or YAML. Set it via {env_var} env var."
                )
                raise ValueError(msg)
        return data

    @model_validator(mode="after")
    def _validate_composite(self) -> WorkerSettings:
        if self.output_mode == "volume" and not self.output_volume_dir:
            msg = "output_volume_dir is required when output_mode == 'volume'"
            raise ValueError(msg)
        if self.price_source == "static" and self.dollars_per_hour is None:
            msg = "dollars_per_hour is required when price_source == 'static'"
            raise ValueError(msg)
        return self

