"""Safe atomic input store for streaming uploads to disk."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import aiofiles

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

MAX_INPUT_BYTES: int = 2 * 1024 * 1024 * 1024

_TEMP_DIR_NAME: str = ".inputs-tmp"
_INPUTS_DIR_NAME: str = "inputs"
_TEMP_SUFFIX: str = ".part"
_DEFAULT_BASENAME: str = "input"


@dataclass(frozen=True, slots=True)
class StoredInput:
    """Metadata for a successfully stored input file."""

    source_path: str
    filename: str
    size_bytes: int
    content_type: str | None


class InputTooLargeError(ValueError):
    """The upload exceeded the configured maximum size."""


class InputPathError(ValueError):
    """The user-supplied source path did not resolve to a regular file inside the data directory."""


def _check_size(current_size: int, chunk: bytes, filename: str) -> None:
    """Raise :class:`InputTooLargeError` if writing ``chunk`` would push the total over the limit."""
    if current_size + len(chunk) > MAX_INPUT_BYTES:
        msg = f"Input exceeds maximum size of {MAX_INPUT_BYTES} bytes: {filename!r}"
        raise InputTooLargeError(msg)


def _safe_basename(filename: str) -> str:
    """Return the last path component, stripping both POSIX and Windows client directory components."""
    return Path(filename.replace("\\", "/")).name or _DEFAULT_BASENAME


class InputStore:
    """Streams uploads to disk and resolves user-supplied paths against the data directory."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir.resolve()
        self._data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def data_dir(self) -> Path:
        """The data directory under which ``inputs/`` and ``.inputs-tmp/`` live."""
        return self._data_dir

    def _ensure_storage_root(self, name: str) -> Path:
        """Ensure ``data_dir / name`` exists and resolves inside ``data_dir``.

        Resolves symlinks before validating containment so a pre-existing
        symlink at the storage root cannot redirect writes outside the data
        directory.
        """
        subdir = self._data_dir / name
        subdir.mkdir(parents=True, exist_ok=True)
        resolved = subdir.resolve()
        try:
            resolved.relative_to(self._data_dir)
        except ValueError as exc:
            msg = f"Storage root {name!r} resolves outside data directory {self._data_dir}: {resolved}"
            raise InputPathError(msg) from exc
        return resolved

    async def save(
        self,
        filename: str,
        content_type: str | None,
        chunks: AsyncIterator[bytes],
    ) -> StoredInput:
        """Stream ``chunks`` to a temp file and atomically move it into ``inputs/``.

        Raises:
            InputTooLargeError: If the accumulated byte count would exceed ``MAX_INPUT_BYTES``.
            InputPathError: If a storage root resolves outside the data directory.
        """
        basename = _safe_basename(filename)
        random_id = secrets.token_hex(16)

        inputs_root = self._ensure_storage_root(_INPUTS_DIR_NAME)
        dest_dir = inputs_root / random_id
        dest_dir.mkdir()
        dest_path = dest_dir / basename

        temp_dir = self._ensure_storage_root(_TEMP_DIR_NAME)
        temp_path = temp_dir / f"{random_id}{_TEMP_SUFFIX}"

        size = 0
        try:
            async with aiofiles.open(temp_path, "wb") as f:
                async for chunk in chunks:
                    _check_size(size, chunk, filename)
                    await f.write(chunk)
                    size += len(chunk)
            os.replace(temp_path, dest_path)  # noqa: PTH105  # spec: brief requires os.replace
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise

        return StoredInput(
            source_path=dest_path.relative_to(self._data_dir).as_posix(),
            filename=basename,
            size_bytes=size,
            content_type=content_type,
        )

    def resolve_source_path(self, source_path: str) -> Path:
        """Resolve ``source_path`` to a regular file under the data directory.

        Raises:
            InputPathError: If the path is empty, absolute, escapes the data
                directory, refers to a missing or non-file entry, or follows a
                symlink whose target lies outside the data directory.
        """
        if not source_path or Path(source_path).is_absolute():
            msg = f"Invalid source path {source_path!r}: must be a non-empty relative path under {self._data_dir}"
            raise InputPathError(msg)
        resolved = (self._data_dir / source_path).resolve()
        try:
            resolved.relative_to(self._data_dir)
        except ValueError as exc:
            msg = f"Invalid source path {source_path!r}: must resolve to a regular file under {self._data_dir}"
            raise InputPathError(msg) from exc
        if not resolved.is_file():
            msg = f"Invalid source path {source_path!r}: must resolve to a regular file under {self._data_dir}"
            raise InputPathError(msg)
        return resolved
