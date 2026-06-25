"""File-based caching for plans and step outputs."""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from acheron.core.errors import CacheCorruptedError, CacheMissError
from acheron.core.models import OutputFile, Plan

_plan_adapter = TypeAdapter(Plan)
_output_adapter = TypeAdapter(tuple[OutputFile, ...])


def _checksum(path: Path) -> str:
    """Compute SHA-256 hex digest of a file. Blocking — wrap in to_thread from async callers."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class PlanCache:
    """Persists and loads pipeline plans to/from disk."""

    def __init__(self, data_dir: str | Path = "/data/jobs") -> None:
        self._data_dir = Path(data_dir)

    @property
    def data_dir(self) -> Path:
        """The root directory for cached plans and step outputs."""
        return self._data_dir

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
        except (OSError, UnicodeDecodeError, ValidationError) as exc:
            msg = f"Corrupted plan file: {plan_id}"
            raise CacheCorruptedError(msg) from exc

    def plan_exists(self, plan_id: str) -> bool:
        """Check whether a plan file exists on disk."""
        return (self._data_dir / plan_id / "plan.json").exists()


class StepCache:
    """Persists and loads step output manifests asynchronously."""

    def __init__(self, data_dir: str | Path = "/data/jobs") -> None:
        self._data_dir = Path(data_dir)

    @property
    def data_dir(self) -> Path:
        """The root directory for cached step outputs."""
        return self._data_dir

    async def save_outputs(self, job_id: str, step_id: str, outputs: tuple[OutputFile, ...]) -> None:
        """Write output manifest. Creates the step directory if needed."""
        step_dir = self._data_dir / job_id / step_id
        manifest_file = step_dir / "manifest.json"
        manifest = _output_adapter.dump_json(outputs, indent=2)
        await asyncio.to_thread(self._write_manifest, step_dir, manifest_file, manifest)

    async def load_outputs(self, job_id: str, step_id: str) -> tuple[OutputFile, ...]:
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
            blob = await asyncio.to_thread(manifest_file.read_bytes)
        except OSError as exc:
            msg = f"Corrupted manifest: {job_id}/{step_id}"
            raise CacheCorruptedError(msg) from exc
        try:
            return _output_adapter.validate_json(blob)
        except ValidationError as exc:
            msg = f"Corrupted manifest: {job_id}/{step_id}"
            raise CacheCorruptedError(msg) from exc

    async def step_has_valid_cache(self, job_id: str, step_id: str) -> bool:
        """Check if a step has a valid manifest with all files present and checksums matching."""
        manifest_file = self._data_dir / job_id / step_id / "manifest.json"
        if not manifest_file.exists():
            return False
        try:
            outputs = await self.load_outputs(job_id, step_id)
        except CacheMissError, CacheCorruptedError, OSError:
            return False
        for output in outputs:
            file_path = Path(output.path)
            if not await asyncio.to_thread(file_path.exists):
                return False
            checksum = await asyncio.to_thread(_checksum, file_path)
            if checksum != output.checksum:
                return False
        return True

    @staticmethod
    def _write_manifest(step_dir: Path, manifest_file: Path, manifest: bytes) -> None:
        step_dir.mkdir(parents=True, exist_ok=True)
        manifest_file.write_bytes(manifest)


class InMemoryStepCache:
    """Process-local step cache. State is lost on restart.

    Used as the orchestrator's default so that constructing an ``Orchestrator``
    does not require a writable data directory. Callers that want cross-process
    resume must pass an explicit ``StepCache`` rooted at a shared directory.
    """

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self._data_dir = Path(data_dir) if data_dir is not None else Path(tempfile.mkdtemp(prefix="acheron-step-"))
        self._outputs: dict[tuple[str, str], tuple[OutputFile, ...]] = {}

    @property
    def data_dir(self) -> Path:
        """The root directory for the cache. Files are not materialised here."""
        return self._data_dir

    async def save_outputs(self, job_id: str, step_id: str, outputs: tuple[OutputFile, ...]) -> None:
        """Record the step's output manifest in memory."""
        self._outputs[(job_id, step_id)] = outputs

    async def load_outputs(self, job_id: str, step_id: str) -> tuple[OutputFile, ...]:
        """Return a previously-saved manifest.

        Raises:
            CacheMissError: If no manifest is recorded for ``(job_id, step_id)``.
        """
        try:
            return self._outputs[(job_id, step_id)]
        except KeyError as exc:
            msg = f"Step cache miss: {job_id}/{step_id}"
            raise CacheMissError(msg) from exc

    async def step_has_valid_cache(self, job_id: str, step_id: str) -> bool:
        """Return True iff the manifest is recorded and every file still exists on disk."""
        outputs = self._outputs.get((job_id, step_id))
        if outputs is None:
            return False
        for output in outputs:
            file_path = Path(output.path)
            if not await asyncio.to_thread(file_path.exists):
                return False
            checksum = await asyncio.to_thread(_checksum, file_path)
            if checksum != output.checksum:
                return False
        return True
