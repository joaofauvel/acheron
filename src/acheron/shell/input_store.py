"""Safe atomic input store for streaming uploads to disk."""

from __future__ import annotations

import os
import re
import secrets
from contextlib import suppress
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
_CONTROL_CHARACTER_LIMIT = 32
_DELETE_CHARACTER = 127
_FORBIDDEN_SOURCE_COMPONENTS = frozenset(
    {
        ".env",
        ".git",
        ".inputs-tmp",
        ".registration_token",
        "credentials",
        "secrets",
    }
)
_FORBIDDEN_SOURCE_SUFFIXES = frozenset({".crt", ".cer", ".key", ".pem", ".p12", ".pfx"})


@dataclass(frozen=True, slots=True)
class StoredInput:
    """Metadata for a successfully stored input file."""

    input_id: str
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
    """Return a valid final path component from a client-supplied filename."""
    if any(ord(char) < _CONTROL_CHARACTER_LIMIT or ord(char) == _DELETE_CHARACTER for char in filename):
        msg = "Invalid input filename: control characters are not allowed"
        raise ValueError(msg)
    normalized = filename.replace("\\", "/")
    raw_basename = normalized.rsplit("/", 1)[-1]
    basename = Path(normalized).name or _DEFAULT_BASENAME
    if re.search(
        r"(?i)(?:https?://|[@:]|(?:token|secret|password|credential|authorization|api[-_ ]?key)[-_:= ])", basename
    ):
        basename = _DEFAULT_BASENAME
    if raw_basename in {".", ".."} or basename in {".", ".."}:
        msg = f"Invalid input filename {filename!r}: basename must name a file"
        raise ValueError(msg)
    return basename


class InputStore:
    """Streams uploads to disk and resolves user-supplied paths against the data directory."""

    def __init__(self, data_dir: Path, *, create: bool = True) -> None:
        self._data_dir = data_dir.resolve()
        if create:
            self._data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def data_dir(self) -> Path:
        """The data directory under which ``inputs/`` and ``.inputs-tmp/`` live."""
        return self._data_dir

    def _ensure_storage_root(self, name: str) -> Path:
        """Ensure ``data_dir / name`` exists and resolves inside ``data_dir``.

        Rejects any symlink at the storage root, including a target that
        remains inside ``data_dir`` (e.g. ``data_dir/inputs -> data_dir/elsewhere``).
        Allowing such a symlink would silently redirect writes to the
        target and break the public ``inputs/<id>/<name>`` source-path
        invariant promised to API callers.
        """
        subdir = self._data_dir / name
        if subdir.is_symlink():
            msg = f"Storage root {name!r} is a symlink in data directory {self._data_dir}"
            raise InputPathError(msg)
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

        dest_dir: Path | None = None
        dest_path: Path | None = None
        temp_path: Path | None = None
        destination_created = False
        committed = False
        size = 0
        try:
            inputs_root = self._ensure_storage_root(_INPUTS_DIR_NAME)
            dest_dir = inputs_root / random_id
            dest_dir.mkdir()
            destination_created = True
            dest_path = dest_dir / basename

            temp_dir = self._ensure_storage_root(_TEMP_DIR_NAME)
            temp_path = temp_dir / f"{random_id}{_TEMP_SUFFIX}"

            async with aiofiles.open(temp_path, "wb") as f:
                async for chunk in chunks:
                    _check_size(size, chunk, filename)
                    await f.write(chunk)
                    size += len(chunk)
            os.replace(temp_path, dest_path)  # noqa: PTH105  # spec: brief requires os.replace
            committed = True
        except BaseException:
            if temp_path is not None:
                with suppress(OSError):
                    temp_path.unlink(missing_ok=True)
            if destination_created and not committed and dest_dir is not None:
                with suppress(OSError):
                    dest_dir.rmdir()
            raise

        if dest_path is None:
            raise RuntimeError("input destination was not created")
        return StoredInput(
            input_id=random_id,
            source_path=dest_path.relative_to(self._data_dir).as_posix(),
            filename=basename,
            size_bytes=size,
            content_type=content_type,
        )

    def promote(self, input_id: str, source_path: str) -> None:
        """Validate that an uploaded input is eligible to attach to a job."""
        if not source_path.startswith(f"{_INPUTS_DIR_NAME}/{input_id}/"):
            raise InputPathError("source path does not belong to input identity")
        self.resolve_source_path(source_path)

    def delete(self, input_id: str) -> None:
        """Delete an uploaded input directory, treating an absent input as success."""
        if not input_id or Path(input_id).name != input_id or not re.fullmatch(r"[0-9a-f]{32}", input_id):
            raise InputPathError("invalid input identity")
        root = self._data_dir / _INPUTS_DIR_NAME / input_id
        try:
            root.resolve().relative_to((self._data_dir / _INPUTS_DIR_NAME).resolve())
        except ValueError as exc:
            raise InputPathError("input identity escapes the input directory") from exc
        if not root.exists():
            return
        if not root.is_dir() or root.is_symlink():
            raise InputPathError("input identity does not name an input directory")
        for child in root.iterdir():
            if child.is_file() or child.is_symlink():
                child.unlink()
            elif child.is_dir():
                raise InputPathError("input directory contains unexpected nested data")
        root.rmdir()

    @staticmethod
    def _validate_source_components(source_path: str) -> None:
        parts = Path(source_path).parts
        if any(part in _FORBIDDEN_SOURCE_COMPONENTS for part in parts):
            raise InputPathError("source path refers to an internal file")
        basename = Path(source_path).name.casefold()
        if basename in {".env", ".registration_token"} or basename.endswith(tuple(_FORBIDDEN_SOURCE_SUFFIXES)):
            raise InputPathError("source path refers to an internal file")

    def normalize_source_path(self, source_path: str) -> str:
        """Return the canonical POSIX identity of a source relative to ``data_dir``."""
        candidate = Path(source_path)
        if not source_path:
            raise InputPathError("source path must not be empty")
        self._validate_source_components(source_path)
        resolved = (candidate if candidate.is_absolute() else self._data_dir / candidate).resolve(strict=False)
        try:
            return resolved.relative_to(self._data_dir).as_posix()
        except ValueError as exc:
            raise InputPathError("source path is outside the data directory") from exc

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
        self._validate_source_components(source_path)
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
