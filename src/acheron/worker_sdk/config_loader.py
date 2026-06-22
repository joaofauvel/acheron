"""Worker configuration discovery and loading.

Resolution order (first match wins):
  1. ``WORKER_CONFIG`` env var → explicit path (absolute or relative).
  2. ``<cwd>/<worker_name>.worker.yaml`` — ``worker_name`` from
     ``WORKER_NAME`` env var or the current directory's basename.
  3. ``<cwd>/worker.yaml``.
  4. Env vars only (no file).

Env vars override YAML values on conflict. Secrets are rejected when
present in YAML (fail-loud to keep them out of image layers).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from acheron.worker_sdk.settings import _ENV_ONLY_FIELDS, WorkerSettings


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


def _load_yaml(path: Path) -> dict[str, Any]:
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
    yaml_data: dict[str, Any] = {}
    for path in _candidate_paths():
        if path.is_file():
            yaml_data = _load_yaml(path)
            break

    offenders = _ENV_ONLY_FIELDS & yaml_data.keys()
    if offenders:
        msg = (
            "Fields are env-only and cannot be set via constructor or YAML: "
            f"{sorted(offenders)}. Set them via ACHERON_WORKER_* env vars."
        )
        raise ValueError(msg)

    try:
        return WorkerSettings(**yaml_data)
    except ValidationError as exc:
        for err in exc.errors():
            if err.get("type") == "value_error":
                raise ValueError(err["msg"]) from exc
        raise
