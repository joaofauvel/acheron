"""File-based caching for plans and step outputs."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import TypeAdapter, ValidationError

from acheron.core.errors import CacheCorruptedError, CacheError, CacheMissError
from acheron.core.models import OutputFile, Plan

if TYPE_CHECKING:
    from collections.abc import Collection


_plan_adapter = TypeAdapter(Plan)
_output_adapter = TypeAdapter(tuple[OutputFile, ...])

_PLAN_ID_RE = re.compile(r"\Aplan-[0-9a-f]+\Z")
_SYMLINK_MODE = stat.S_IFMT(0o120000)


def _validate_step_cache_part(value: str, field: str) -> None:
    if not value or value in {".", ".."} or Path(value).is_absolute() or "/" in value or "\\" in value:
        msg = f"Invalid {field} cache path component: {value!r}"
        raise CacheError(msg)


def _step_dir(data_dir: Path, job_id: str, step_id: str) -> Path:
    _validate_step_cache_part(job_id, "job_id")
    _validate_step_cache_part(step_id, "step_id")
    return data_dir / job_id / step_id


def _checksum(path: Path) -> str:
    """Compute SHA-256 hex digest of a file. Blocking — wrap in to_thread from async callers."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_path(data_dir: Path, relative: Path) -> Path:
    """Resolve a cache scope without permitting traversal or symlink roots."""
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        msg = f"Invalid cache path: {relative}"
        raise CacheError(msg)
    path = data_dir / relative
    try:
        path.resolve(strict=False).relative_to(data_dir.resolve())
    except ValueError as exc:
        msg = f"Cache path escapes data directory: {relative}"
        raise CacheError(msg) from exc
    return path


def _entry_size(parent_fd: int, name: str) -> int:
    """Count one directory entry through a no-follow descriptor."""
    try:
        child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except FileNotFoundError:
        return 0
    except NotADirectoryError:
        entry_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_IFMT(entry_stat.st_mode) == _SYMLINK_MODE:
            msg = f"Refusing symlink cache path: {name}"
            raise CacheError(msg) from None
        return entry_stat.st_size
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            msg = f"Refusing symlink cache path: {name}"
            raise CacheError(msg) from exc
        raise
    try:
        with os.scandir(child_fd) as entries:
            return sum(_entry_size(child_fd, entry.name) for entry in entries)
    finally:
        os.close(child_fd)


def _tree_size(path: Path) -> int:
    """Count regular-file bytes below a cache path without following symlinks."""
    try:
        entry_stat = path.lstat()
    except FileNotFoundError:
        return 0
    if stat.S_IFMT(entry_stat.st_mode) == _SYMLINK_MODE:
        msg = f"Refusing symlink cache path: {path.name}"
        raise CacheError(msg)
    if not stat.S_ISDIR(entry_stat.st_mode):
        return entry_stat.st_size
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return 0
    try:
        with os.scandir(fd) as entries:
            return sum(_entry_size(fd, entry.name) for entry in entries)
    finally:
        os.close(fd)


def _delete_entry(parent_fd: int, name: str) -> None:
    """Delete one directory entry using no-follow descriptor operations."""
    try:
        child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    except NotADirectoryError:
        entry_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_IFMT(entry_stat.st_mode) == _SYMLINK_MODE:
            msg = f"Refusing symlink cache path: {name}"
            raise CacheError(msg) from None
        os.unlink(name, dir_fd=parent_fd)
        return
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            msg = f"Refusing symlink cache path: {name}"
            raise CacheError(msg) from exc
        raise
    try:
        with os.scandir(child_fd) as entries:
            for entry in entries:
                _delete_entry(child_fd, entry.name)
    finally:
        os.close(child_fd)
    os.rmdir(name, dir_fd=parent_fd)


def _delete_tree(data_dir: Path, relative: Path) -> int:
    """Delete a root-relative scope using no-follow directory descriptors."""
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        msg = f"Invalid cache path: {relative}"
        raise CacheError(msg)
    components = relative.parts
    if not components:
        raise CacheError("Invalid cache path: empty scope")
    try:
        root_fd = os.open(data_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return 0
    try:
        parent_fd = root_fd
        opened: list[int] = []
        try:
            for component in components[:-1]:
                parent_fd = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=parent_fd,
                )
                opened.append(parent_fd)
            size = _entry_size(parent_fd, components[-1])
            _delete_entry(parent_fd, components[-1])
        finally:
            for fd in reversed(opened):
                os.close(fd)
        return size
    finally:
        os.close(root_fd)


class PlanCache:
    """Persists and loads pipeline plans to/from disk."""

    def __init__(self, data_dir: str | Path = "/data/jobs") -> None:
        self._data_dir = Path(data_dir).resolve()

    @property
    def data_dir(self) -> Path:
        """The root directory for cached plans and step outputs."""
        return self._data_dir

    def _plan_file(self, plan_id: str) -> Path:
        """Resolve a plan_id to its on-disk plan.json path.

        Raises:
            CacheMissError: If ``plan_id`` is not a ``plan-<hex>`` identifier,
                so traversal-style IDs cannot escape the cache root.
        """
        if _PLAN_ID_RE.fullmatch(plan_id) is None:
            msg = f"Plan not found: {plan_id}"
            raise CacheMissError(msg)
        return self._data_dir / plan_id / "plan.json"

    def save_plan(self, plan: Plan) -> Path:
        """Save a plan as JSON. Returns the path to the plan file."""
        plan_file = self._plan_file(plan.plan_id)
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text(_plan_adapter.dump_json(plan, indent=2).decode())
        return plan_file

    def load_plan(self, plan_id: str) -> Plan:
        """Load a plan from disk.

        Raises:
            CacheMissError: If the plan file does not exist or the plan_id
                is not a valid ``plan-<hex>`` identifier.
            CacheCorruptedError: If the plan file is malformed.
        """
        plan_file = self._plan_file(plan_id)
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
        try:
            return self._plan_file(plan_id).exists()
        except CacheMissError:
            return False

    def delete_plan(self, plan_id: str) -> int:
        """Delete one plan scope and return its reclaimed bytes."""
        self._plan_file(plan_id)
        return _delete_tree(self._data_dir, Path(plan_id))


class StepCache:
    """Persists and loads step output manifests asynchronously."""

    def __init__(self, data_dir: str | Path = "/data/jobs") -> None:
        self._data_dir = Path(data_dir).resolve()

    @property
    def data_dir(self) -> Path:
        """The root directory for cached step outputs."""
        return self._data_dir

    async def save_outputs(self, job_id: str, step_id: str, outputs: tuple[OutputFile, ...]) -> None:
        """Write output manifest. Creates the step directory if needed."""
        step_dir = _step_dir(self._data_dir, job_id, step_id)
        manifest_file = step_dir / "manifest.json"
        manifest = _output_adapter.dump_json(outputs, indent=2)
        await asyncio.to_thread(self._write_manifest, step_dir, manifest_file, manifest)

    async def load_outputs(self, job_id: str, step_id: str) -> tuple[OutputFile, ...]:
        """Load output files from a step manifest.

        Raises:
            CacheMissError: If the manifest does not exist.
            CacheCorruptedError: If the manifest is malformed.
        """
        manifest_file = _step_dir(self._data_dir, job_id, step_id) / "manifest.json"
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
        manifest_file = _step_dir(self._data_dir, job_id, step_id) / "manifest.json"
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

    async def invalidate_steps(self, job_id: str, step_ids: Collection[str]) -> None:
        """Remove selected step manifests while retaining unrelated job cache entries."""
        for step_id in step_ids:
            _validate_step_cache_part(job_id, "job_id")
            _validate_step_cache_part(step_id, "step_id")
            await asyncio.to_thread(_delete_tree, self._data_dir, Path(job_id) / step_id)

    async def job_size(self, job_id: str) -> int:
        """Return bytes in one job cache scope without following symlinks."""
        _validate_step_cache_part(job_id, "job_id")
        return await asyncio.to_thread(_tree_size, _safe_path(self._data_dir, Path(job_id)))

    async def delete_job(self, job_id: str) -> int:
        """Delete one job cache scope and return its reclaimed bytes."""
        _validate_step_cache_part(job_id, "job_id")
        return await asyncio.to_thread(_delete_tree, self._data_dir, Path(job_id))

    @staticmethod
    def _write_manifest(step_dir: Path, manifest_file: Path, manifest: bytes) -> None:
        step_dir.mkdir(parents=True, exist_ok=True)
        manifest_file.write_bytes(manifest)


class InMemoryStepCache:
    """Process-local step cache. State is lost on restart."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        root = Path(data_dir) if data_dir is not None else Path(tempfile.mkdtemp(prefix="acheron-step-"))
        self._data_dir = root.resolve()
        self._outputs: dict[tuple[str, str], tuple[OutputFile, ...]] = {}

    @property
    def data_dir(self) -> Path:
        """The root directory for the cache. Files are not materialised here."""
        return self._data_dir

    async def save_outputs(self, job_id: str, step_id: str, outputs: tuple[OutputFile, ...]) -> None:
        """Record the step's output manifest in memory."""
        _validate_step_cache_part(job_id, "job_id")
        _validate_step_cache_part(step_id, "step_id")
        self._outputs[(job_id, step_id)] = outputs

    async def load_outputs(self, job_id: str, step_id: str) -> tuple[OutputFile, ...]:
        """Return a previously-saved manifest.

        Raises:
            CacheMissError: If no manifest is recorded for ``(job_id, step_id)``.
        """
        _validate_step_cache_part(job_id, "job_id")
        _validate_step_cache_part(step_id, "step_id")
        try:
            return self._outputs[(job_id, step_id)]
        except KeyError as exc:
            msg = f"Step cache miss: {job_id}/{step_id}"
            raise CacheMissError(msg) from exc

    async def step_has_valid_cache(self, job_id: str, step_id: str) -> bool:
        """Return True iff the manifest is recorded and every file still exists on disk."""
        _validate_step_cache_part(job_id, "job_id")
        _validate_step_cache_part(step_id, "step_id")
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

    async def invalidate_steps(self, job_id: str, step_ids: Collection[str]) -> None:
        """Remove selected step manifests while retaining unrelated job cache entries."""
        _validate_step_cache_part(job_id, "job_id")
        for step_id in step_ids:
            _validate_step_cache_part(step_id, "step_id")
            self._outputs.pop((job_id, step_id), None)

    async def job_size(self, job_id: str) -> int:
        """Return bytes in one in-memory job scope."""
        _validate_step_cache_part(job_id, "job_id")
        return 0

    async def delete_job(self, job_id: str) -> int:
        """Delete one in-memory job scope."""
        _validate_step_cache_part(job_id, "job_id")
        keys = [key for key in self._outputs if key[0] == job_id]
        for key in keys:
            self._outputs.pop(key, None)
        return 0
