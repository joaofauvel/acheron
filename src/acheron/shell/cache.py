"""File-based caching for plans and step outputs."""

import hashlib
import json
from dataclasses import asdict
from enum import Enum
from pathlib import Path

from acheron.core.errors import CacheMissError
from acheron.core.models import JsonValue, OutputFile, Plan, PlanStep, StepStatus, WorkerType


def _checksum(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _json_default(obj: object) -> object:
    """JSON encoder fallback for enums and frozensets."""
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, frozenset):
        return list(obj)
    msg = f"Object of type {type(obj).__name__} is not JSON serializable"
    raise TypeError(msg)


class PlanCache:
    """Persists and loads pipeline plans to/from disk."""

    def __init__(self, data_dir: str | Path = "/data/jobs") -> None:
        self._data_dir = Path(data_dir)

    def save_plan(self, plan: Plan) -> Path:
        """Save a plan as JSON. Returns the path to the plan file."""
        plan_dir = self._data_dir / plan.plan_id
        plan_dir.mkdir(parents=True, exist_ok=True)
        plan_file = plan_dir / "plan.json"
        plan_file.write_text(json.dumps(asdict(plan), indent=2, default=_json_default))
        return plan_file

    def load_plan(self, plan_id: str) -> Plan:
        """Load a plan from disk.

        Raises:
            CacheMissError: If the plan file does not exist.
        """
        plan_file = self._data_dir / plan_id / "plan.json"
        if not plan_file.exists():
            msg = f"Plan not found: {plan_id}"
            raise CacheMissError(msg)
        data = json.loads(plan_file.read_text())
        return _plan_from_dict(data)

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
        manifest = [
            {
                "path": o.path,
                "filename": o.filename,
                "size_bytes": o.size_bytes,
                "checksum": o.checksum,
                "content_type": o.content_type,
            }
            for o in outputs
        ]
        manifest_file = step_dir / "manifest.json"
        manifest_file.write_text(json.dumps(manifest, indent=2))

    def load_outputs(self, job_id: str, step_id: str) -> tuple[OutputFile, ...]:
        """Load output files from a step manifest.

        Raises:
            CacheMissError: If the manifest does not exist.
        """
        manifest_file = self._data_dir / job_id / step_id / "manifest.json"
        if not manifest_file.exists():
            msg = f"Step cache miss: {job_id}/{step_id}"
            raise CacheMissError(msg)
        data = json.loads(manifest_file.read_text())
        return tuple(
            OutputFile(
                path=d["path"],
                filename=d["filename"],
                size_bytes=d["size_bytes"],
                checksum=d["checksum"],
                content_type=d["content_type"],
            )
            for d in data
        )

    def step_has_valid_cache(self, job_id: str, step_id: str) -> bool:
        """Check if a step has a valid manifest with all files present."""
        manifest_file = self._data_dir / job_id / step_id / "manifest.json"
        if not manifest_file.exists():
            return False
        try:
            outputs = self.load_outputs(job_id, step_id)
        except CacheMissError:
            return False
        for output in outputs:
            file_path = Path(output.path)
            if not file_path.exists():
                return False
            if _checksum(file_path) != output.checksum:
                return False
        return True


def _plan_from_dict(data: dict[str, JsonValue]) -> Plan:
    """Reconstruct a Plan from its JSON dict representation."""
    raw_steps: list[dict[str, JsonValue]] = data["steps"]  # type: ignore[assignment]
    steps = tuple(
        PlanStep(
            step_id=s["step_id"],  # type: ignore[arg-type]
            type=WorkerType(s["type"]),
            depends_on=tuple(s["depends_on"]),  # type: ignore[arg-type]
            status=StepStatus(s["status"]),
            payload=s["payload"],  # type: ignore[arg-type]
            batch=s.get("batch", False),  # type: ignore[arg-type]
        )
        for s in raw_steps
    )
    return Plan(
        plan_id=data["plan_id"],  # type: ignore[arg-type]
        job_id=data["job_id"],  # type: ignore[arg-type]
        source_type=data["source_type"],  # type: ignore[arg-type]
        source_language=data["source_language"],  # type: ignore[arg-type]
        target_language=data["target_language"],  # type: ignore[arg-type]
        executor_strategy=data["executor_strategy"],  # type: ignore[arg-type]
        steps=steps,
    )
