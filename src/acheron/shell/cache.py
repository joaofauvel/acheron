"""File-based caching for plans and step outputs."""

import hashlib
from pathlib import Path

from pydantic import TypeAdapter

from acheron.core.errors import CacheCorruptedError, CacheMissError
from acheron.core.models import OutputFile, Plan

_plan_adapter = TypeAdapter(Plan)


def _checksum(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class PlanCache:
    """Persists and loads pipeline plans to/from disk."""

    def __init__(self, data_dir: str | Path = "/data/jobs") -> None:
        self._data_dir = Path(data_dir)

    def save_plan(self, plan: Plan) -> Path:
        """Save a plan as JSON. Returns the path to the plan file."""
        plan_dir = self._data_dir / plan.plan_id
        plan_dir.mkdir(parents=True, exist_ok=True)
        plan_file = plan_dir / "plan.json"
        plan_file.write_text(_plan_adapter.dump_json(plan, indent=2).decode())
        return plan_file

    def load_plan(self, plan_id: str) -> Plan:
        """Load a plan from disk.

        Raises:
            CacheMissError: If the plan file does not exist.
            CacheCorruptedError: If the plan file is malformed.
        """
        plan_file = self._data_dir / plan_id / "plan.json"
        if not plan_file.exists():
            msg = f"Plan not found: {plan_id}"
            raise CacheMissError(msg)
        try:
            return _plan_adapter.validate_json(plan_file.read_text())
        except Exception as exc:
            msg = f"Corrupted plan file: {plan_id}"
            raise CacheCorruptedError(msg) from exc

    def plan_exists(self, plan_id: str) -> bool:
        """Check whether a plan file exists on disk."""
        return (self._data_dir / plan_id / "plan.json").exists()


class StepCache:
    """Persists and loads step output manifests."""

    def __init__(self, data_dir: str | Path = "/data/jobs") -> None:
        self._data_dir = Path(data_dir)

    def save_outputs(self, job_id: str, step_id: str, outputs: tuple[OutputFile, ...]) -> None:
        """Write output files and a manifest with checksums."""
        step_dir = self._data_dir / job_id / step_id
        step_dir.mkdir(parents=True, exist_ok=True)
        manifest_file = step_dir / "manifest.json"
        _output_adapter = TypeAdapter(tuple[OutputFile, ...])
        manifest_file.write_text(_output_adapter.dump_json(outputs, indent=2).decode())

    def load_outputs(self, job_id: str, step_id: str) -> tuple[OutputFile, ...]:
        """Load output files from a step manifest.

        Raises:
            CacheMissError: If the manifest does not exist.
            CacheCorruptedError: If the manifest is malformed.
        """
        manifest_file = self._data_dir / job_id / step_id / "manifest.json"
        if not manifest_file.exists():
            msg = f"Step cache miss: {job_id}/{step_id}"
            raise CacheMissError(msg)
        try:
            return TypeAdapter(tuple[OutputFile, ...]).validate_json(manifest_file.read_text())
        except CacheMissError:
            raise
        except Exception as exc:
            msg = f"Corrupted manifest: {job_id}/{step_id}"
            raise CacheCorruptedError(msg) from exc

    def step_has_valid_cache(self, job_id: str, step_id: str) -> bool:
        """Check if a step has a valid manifest with all files present and checksums matching."""
        manifest_file = self._data_dir / job_id / step_id / "manifest.json"
        if not manifest_file.exists():
            return False
        try:
            outputs = self.load_outputs(job_id, step_id)
        except CacheMissError, CacheCorruptedError, OSError:
            return False
        for output in outputs:
            file_path = Path(output.path)
            if not file_path.exists():
                return False
            if _checksum(file_path) != output.checksum:
                return False
        return True
