"""Worker configuration discovery and loading."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import ValidationError

from acheron.worker_sdk.settings import ENV_ONLY_FIELDS, WorkerSettings


def _candidate_paths() -> list[Path]:
    """Return the ordered list of candidate YAML config paths."""
    candidates: list[Path] = []
    explicit = os.environ.get("WORKER_CONFIG")
    if explicit:
        candidates.append(Path(explicit))
    worker_name = os.environ.get("WORKER_NAME") or Path.cwd().name
    name_yaml = Path.cwd() / f"{worker_name}.worker.yaml"
    if name_yaml not in candidates:
        candidates.append(name_yaml)
    generic_yaml = Path.cwd() / "worker.yaml"
    if generic_yaml not in candidates:
        candidates.append(generic_yaml)
    return candidates


def _load_yaml(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        msg = f"Worker config {path} must be a YAML mapping, got {type(data).__name__}"
        raise TypeError(msg)
    return data


def load_settings() -> WorkerSettings:
    """Discover the worker config and build :class:`WorkerSettings`."""
    yaml_data: dict[str, object] = {}
    for path in _candidate_paths():
        if path.is_file():
            yaml_data = _load_yaml(path)
            break

    offenders = ENV_ONLY_FIELDS & yaml_data.keys()
    if offenders:
        msg = (
            "Fields are env-only and cannot be set via constructor or YAML: "
            f"{sorted(offenders)}. Set them via ACHERON_WORKER_* env vars."
        )
        raise ValueError(msg)

    try:
        # The constructor preserves BaseSettings environment-source precedence.
        return WorkerSettings(**cast("dict[str, Any]", yaml_data))
    except ValidationError as exc:
        for err in exc.errors():
            if err.get("type") == "value_error":
                raise ValueError(err["msg"]) from exc
        raise
