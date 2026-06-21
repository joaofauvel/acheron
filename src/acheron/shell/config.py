from pathlib import Path
import os
import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict, PydanticBaseSettingsSource

from typing import Any

class OrchestratorSettings(BaseModel):
    data_dir: Path = Field(default=Path("/data/jobs"))
    registration_token: str | None = Field(default=None)
    health_check_interval_seconds: int = Field(default=30)

class ChunkingSettings(BaseModel):
    max_chunk_length: int = Field(default=250)

class PackagingSettings(BaseModel):
    bitrate: str = Field(default="128k")
    codec: str = Field(default="aac")

class WorkerSettings(BaseModel):
    chunking: ChunkingSettings = Field(default_factory=ChunkingSettings)
    packaging: PackagingSettings = Field(default_factory=PackagingSettings)

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_prefix="ACHERON_",
        extra="ignore"
    )

    orchestrator: OrchestratorSettings = Field(default_factory=OrchestratorSettings)
    workers: WorkerSettings = Field(default_factory=WorkerSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        class YamlConfigSettingsSource(PydanticBaseSettingsSource):
            def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
                return None, "", False

            def __call__(self) -> dict[str, Any]:
                config_path_env = os.environ.get("ACHERON_CONFIG_PATH")
                search_paths = []
                if config_path_env:
                    search_paths.append(Path(config_path_env))
                search_paths.extend([Path("./acheron.yaml"), Path("/etc/acheron/acheron.yaml")])

                for path in search_paths:
                    if path.is_file():
                        try:
                            with path.open("r", encoding="utf-8") as f:
                                return yaml.safe_load(f) or {}
                        except Exception:
                            pass
                return {}

        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls),
        )

def load_settings() -> Settings:
    return Settings()
