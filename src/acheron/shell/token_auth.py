"""Persisted registration-token lifecycle and audit records."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import secrets
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager, suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from acheron.core.errors import AcheronError, sanitise_public_message

TokenSource = Literal["environment", "file"]
_MAX_AUDIT_RECORDS = 100
_REGISTRATION_FILE_NAME = ".registration_token"
_AUDIT_FILE_NAME = ".registration_token.audit.jsonl"
_METADATA_FILE_NAME = ".registration_token.metadata.json"
_LOCK_FILE_NAME = ".registration_token.lock"
_TOKEN_MODE = 0o600


class TokenStoreError(AcheronError):
    """A persisted registration-token operation failed."""


class TokenRotationError(TokenStoreError):
    """A registration-token rollout failed and was rolled back."""


@dataclass(frozen=True, slots=True)
class RolloutResult:
    """Result returned by a worker-token rollout callback."""

    success: bool
    worker_ids: tuple[str, ...] = ()
    message: str | None = None
    remediation: str | None = None


@dataclass(frozen=True, slots=True)
class RegistrationTokenStatus:
    """Secret-free registration-token state."""

    source: TokenSource
    created_at: datetime | None
    last_rotation_at: datetime | None
    rotation_count: int
    fingerprint: str | None


@dataclass(frozen=True, slots=True)
class RegistrationTokenAudit:
    """Secret-free registration-token audit entry."""

    timestamp: datetime
    reason: str
    old_fingerprint: str | None
    new_fingerprint: str | None
    worker_ids: tuple[str, ...]
    result: str
    request_id: str


@dataclass(frozen=True, slots=True)
class _LifecycleMetadata:
    created_at: datetime
    last_rotation_at: datetime | None
    rotation_count: int


Rollout = Callable[[str], Awaitable[RolloutResult]]


def _fingerprint(token: str) -> str:
    """Return a short fingerprint without exposing token material."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _now() -> datetime:
    return datetime.now(UTC)


def _string_value(value: object) -> str:
    return value if isinstance(value, str) else ""


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


class _FileLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, _TOKEN_MODE)
        try:
            os.fchmod(fd, _TOKEN_MODE)
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError:
            os.close(fd)
            raise
        self._fd = fd

    def release(self) -> None:
        fd = self._fd
        self._fd = None
        if fd is None:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


class RegistrationTokenStore:
    """Persist and rotate a registration token without exposing its value."""

    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)
        self.token_path = self.data_dir / _REGISTRATION_FILE_NAME
        self.audit_path = self.data_dir / _AUDIT_FILE_NAME
        self.metadata_path = self.data_dir / _METADATA_FILE_NAME
        self.lock_path = self.data_dir / _LOCK_FILE_NAME
        self._configured_token: str | None = None

    def load_or_create(self, configured_token: str | None) -> str:
        """Load an explicit or persisted token, creating the file-backed value when absent."""
        self._configured_token = configured_token or None
        if self._configured_token is not None:
            return self._configured_token
        try:
            with self._file_lock():
                return self._load_or_create_locked()
        except TokenStoreError:
            raise
        except OSError as exc:
            raise TokenStoreError(
                "Unable to initialize the registration token",
                remediation="Check the orchestrator data directory permissions",
            ) from exc

    def read_current(self) -> str:
        """Read the active token from the configured source."""
        if self._configured_token is not None:
            return self._configured_token
        try:
            with self._file_lock():
                return self._read_current_locked()
        except TokenStoreError:
            raise
        except OSError as exc:
            raise TokenStoreError(
                "Unable to read the registration token",
                remediation="Check the orchestrator data directory permissions",
            ) from exc

    def status(self) -> RegistrationTokenStatus:
        """Return secret-free source, timing, rotation, and fingerprint metadata."""
        source: TokenSource = "environment" if self._configured_token is not None else "file"
        if source == "environment":
            return RegistrationTokenStatus(
                source=source,
                created_at=None,
                last_rotation_at=None,
                rotation_count=0,
                fingerprint=_fingerprint(self._configured_token or ""),
            )
        try:
            with self._file_lock():
                token = self._read_current_locked()
                metadata = self._read_lifecycle_locked()
        except TokenStoreError:
            raise
        except OSError as exc:
            raise TokenStoreError(
                "Unable to inspect registration token metadata",
                remediation="Check the orchestrator data directory permissions",
            ) from exc
        return RegistrationTokenStatus(
            source=source,
            created_at=metadata.created_at,
            last_rotation_at=metadata.last_rotation_at,
            rotation_count=metadata.rotation_count,
            fingerprint=_fingerprint(token),
        )

    async def rotate(self, reason: str, request_id: str, rollout: Rollout) -> RegistrationTokenStatus:
        """Rotate a file-backed token and roll it back if worker rollout fails."""
        if self._configured_token is not None:
            raise TokenRotationError(
                "Environment registration tokens cannot rotate in place",
                remediation="Update the worker environment and restart workers externally",
            )
        async with self._async_file_lock():
            return await self._rotate_locked(reason, request_id, rollout)

    def _load_or_create_locked(self) -> str:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.token_path.is_file():
            token = self._read_file_token()
            self._ensure_mode(self.token_path)
            return token

        token = secrets.token_hex(16)
        metadata = _LifecycleMetadata(created_at=_now(), last_rotation_at=None, rotation_count=0)
        try:
            self._atomic_write_secret(token)
            self._write_lifecycle_locked(metadata)
            self._append_audit_locked(
                RegistrationTokenAudit(
                    timestamp=metadata.created_at,
                    reason="created",
                    old_fingerprint=None,
                    new_fingerprint=_fingerprint(token),
                    worker_ids=(),
                    result="created",
                    request_id="",
                )
            )
        except (OSError, TokenStoreError) as exc:
            self._remove_file_best_effort(self.token_path)
            self._remove_file_best_effort(self.metadata_path)
            raise TokenStoreError(
                "Unable to persist the registration token",
                remediation="Check the orchestrator data directory permissions",
            ) from exc
        return token

    async def _rotate_locked(self, reason: str, request_id: str, rollout: Rollout) -> RegistrationTokenStatus:
        old_token = self._read_current_locked()
        self._validate_metadata(reason, request_id, old_token)
        old_audit = self._snapshot_file(self.audit_path)
        old_metadata = self._snapshot_file(self.metadata_path)
        old_lifecycle = self._read_lifecycle_locked()
        candidate = secrets.token_hex(16)
        timestamp = _now()
        safe_reason = self._safe_metadata(reason, old_token, "operator rotation")
        safe_request_id = self._safe_metadata(request_id, old_token, "unknown-request")
        try:
            self._atomic_write_secret(candidate)
        except (OSError, TokenStoreError) as exc:
            raise TokenStoreError(
                "Unable to stage the replacement registration token",
                remediation="Check the orchestrator data directory permissions",
            ) from exc
        try:
            result = await rollout(candidate)
        except asyncio.CancelledError:
            await self._rollback_state(rollout, old_token, old_audit, old_metadata)
            raise
        except TokenRotationError, TokenStoreError:
            await self._rollback_state(rollout, old_token, old_audit, old_metadata)
            raise
        except BaseException as exc:
            await self._rollback_state(rollout, old_token, old_audit, old_metadata)
            raise TokenRotationError(
                "Registration token rollout failed; the previous token was restored",
                remediation="Check worker connectivity and retry the rotation",
            ) from exc

        safe_worker_ids = tuple(
            self._safe_metadata(
                self._safe_metadata(worker_id, candidate, "redacted-worker"),
                old_token,
                "redacted-worker",
            )
            for worker_id in result.worker_ids
        )
        if not result.success:
            await self._rollback_workers(rollout, old_token)
            self._restore_secret_best_effort(old_token)
            self._record_failure_best_effort(
                RegistrationTokenAudit(
                    timestamp=timestamp,
                    reason=safe_reason,
                    old_fingerprint=_fingerprint(old_token),
                    new_fingerprint=_fingerprint(candidate),
                    worker_ids=safe_worker_ids,
                    result="failed",
                    request_id=safe_request_id,
                )
            )
            raise TokenRotationError(
                "Registration token rollout failed; the previous token was restored",
                remediation=result.remediation or "Check worker connectivity and retry the rotation",
            )

        new_lifecycle = _LifecycleMetadata(
            created_at=old_lifecycle.created_at,
            last_rotation_at=timestamp,
            rotation_count=old_lifecycle.rotation_count + 1,
        )
        success_audit = RegistrationTokenAudit(
            timestamp=timestamp,
            reason=safe_reason,
            old_fingerprint=_fingerprint(old_token),
            new_fingerprint=_fingerprint(candidate),
            worker_ids=safe_worker_ids,
            result="success",
            request_id=safe_request_id,
        )
        try:
            self._append_audit_locked(success_audit)
            self._write_lifecycle_locked(new_lifecycle)
        except asyncio.CancelledError:
            await self._rollback_state(rollout, old_token, old_audit, old_metadata)
            raise
        except TokenStoreError as exc:
            await self._rollback_state(rollout, old_token, old_audit, old_metadata)
            raise TokenStoreError(
                "Unable to finalize registration token rotation; the previous token was restored",
                remediation="Check the orchestrator data directory permissions and retry the rotation",
            ) from exc
        except OSError as exc:
            await self._rollback_state(rollout, old_token, old_audit, old_metadata)
            raise TokenStoreError(
                "Unable to finalize registration token rotation; the previous token was restored",
                remediation="Check the orchestrator data directory permissions and retry the rotation",
            ) from exc
        except BaseException as exc:
            await self._rollback_state(rollout, old_token, old_audit, old_metadata)
            raise TokenStoreError(
                "Unable to finalize registration token rotation; the previous token was restored",
                remediation="Check the orchestrator data directory permissions and retry the rotation",
            ) from exc
        return RegistrationTokenStatus(
            source="file",
            created_at=new_lifecycle.created_at,
            last_rotation_at=new_lifecycle.last_rotation_at,
            rotation_count=new_lifecycle.rotation_count,
            fingerprint=_fingerprint(candidate),
        )

    def _read_current_locked(self) -> str:
        if not self.token_path.is_file():
            raise TokenStoreError(
                "Registration token is not initialized",
                remediation="Start the orchestrator before reading its registration token",
            )
        return self._read_file_token()

    def _read_file_token(self) -> str:
        try:
            token = self.token_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise TokenStoreError(
                "Unable to read the registration token",
                remediation="Check the orchestrator data directory permissions",
            ) from exc
        if not token:
            raise TokenStoreError(
                "Registration token file is empty",
                remediation="Remove the empty token file and restart the orchestrator",
            )
        return token

    def _read_lifecycle_locked(self) -> _LifecycleMetadata:
        if self.metadata_path.is_file():
            try:
                data = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise TokenStoreError(
                    "Unable to read registration token metadata",
                    remediation="Check the orchestrator data directory permissions",
                ) from exc
            if isinstance(data, Mapping):
                created = _parse_datetime(data.get("created_at"))
                count = data.get("rotation_count")
                last_rotation = _parse_datetime(data.get("last_rotation_at"))
                if created is not None and isinstance(count, int) and count >= 0:
                    return _LifecycleMetadata(created, last_rotation, count)
        try:
            created = datetime.fromtimestamp(self.token_path.stat().st_mtime, UTC)
        except OSError as exc:
            raise TokenStoreError(
                "Unable to inspect registration token metadata",
                remediation="Check the orchestrator data directory permissions",
            ) from exc
        return _LifecycleMetadata(created, None, 0)

    def _write_lifecycle_locked(self, metadata: _LifecycleMetadata) -> None:
        payload = {
            "created_at": metadata.created_at.isoformat(),
            "last_rotation_at": metadata.last_rotation_at.isoformat() if metadata.last_rotation_at else None,
            "rotation_count": metadata.rotation_count,
        }
        self._atomic_write_text(self.metadata_path, json.dumps(payload, sort_keys=True) + "\n")

    def _atomic_write_secret(self, token: str) -> None:
        self._atomic_write_text(self.token_path, token)

    def _atomic_write_text(self, path: Path, content: str) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f"{path.name}.", dir=self.data_dir)
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(fd, _TOKEN_MODE)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary_path.replace(path)
            self._ensure_mode(path)
        except OSError:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)
            raise

    def _restore_secret_best_effort(self, token: str) -> None:
        with suppress(BaseException):
            self._atomic_write_secret(token)

    async def _rollback_workers(self, rollout: Rollout, token: str) -> None:
        with suppress(BaseException):
            await asyncio.shield(rollout(token))

    async def _rollback_state(
        self,
        rollout: Rollout,
        old_token: str,
        old_audit: bytes | None,
        old_metadata: bytes | None,
    ) -> None:
        await self._rollback_workers(rollout, old_token)
        self._restore_secret_best_effort(old_token)
        self._restore_snapshot_best_effort(self.audit_path, old_audit)
        self._restore_snapshot_best_effort(self.metadata_path, old_metadata)

    def _record_failure_best_effort(self, audit: RegistrationTokenAudit) -> None:
        with suppress(BaseException):
            self._append_audit_locked(audit)

    def _append_audit_locked(self, audit: RegistrationTokenAudit) -> None:
        records = self._read_audits()
        records.append(audit)
        records = records[-_MAX_AUDIT_RECORDS:]
        payload = "".join(
            json.dumps(
                {
                    **asdict(record),
                    "timestamp": record.timestamp.isoformat(),
                    "worker_ids": list(record.worker_ids),
                },
                sort_keys=True,
            )
            + "\n"
            for record in records
        )
        self._atomic_write_text(self.audit_path, payload)

    def _read_audits(self) -> list[RegistrationTokenAudit]:
        if not self.audit_path.is_file():
            return []
        try:
            lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise TokenStoreError(
                "Unable to read registration token audit history",
                remediation="Check the orchestrator data directory permissions",
            ) from exc
        records: list[RegistrationTokenAudit] = []
        for line in lines[-_MAX_AUDIT_RECORDS:]:
            try:
                data = json.loads(line)
                if not isinstance(data, Mapping):
                    continue
                timestamp = _parse_datetime(data.get("timestamp"))
                worker_values = data.get("worker_ids", ())
                if timestamp is None or not isinstance(worker_values, (list, tuple)):
                    continue
                worker_ids = tuple(value for value in worker_values if isinstance(value, str))
                records.append(
                    RegistrationTokenAudit(
                        timestamp=timestamp,
                        reason=_string_value(data.get("reason")),
                        old_fingerprint=(
                            data.get("old_fingerprint") if isinstance(data.get("old_fingerprint"), str) else None
                        ),
                        new_fingerprint=(
                            data.get("new_fingerprint") if isinstance(data.get("new_fingerprint"), str) else None
                        ),
                        worker_ids=worker_ids,
                        result=_string_value(data.get("result")),
                        request_id=_string_value(data.get("request_id")),
                    )
                )
            except TypeError, ValueError, json.JSONDecodeError:
                continue
        return records

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        lock = _FileLock(self.lock_path)
        try:
            lock.acquire()
        except OSError as exc:
            raise TokenStoreError(
                "Unable to lock registration token state",
                remediation="Check the orchestrator data directory permissions",
            ) from exc
        try:
            yield
        except BaseException:
            with suppress(BaseException):
                lock.release()
            raise
        else:
            try:
                lock.release()
            except OSError as exc:
                raise TokenStoreError(
                    "Unable to release registration token state lock",
                    remediation="Check the orchestrator data directory permissions",
                ) from exc

    @asynccontextmanager
    async def _async_file_lock(self) -> AsyncIterator[None]:
        lock = _FileLock(self.lock_path)
        try:
            await asyncio.to_thread(lock.acquire)
        except OSError as exc:
            raise TokenStoreError(
                "Unable to lock registration token state",
                remediation="Check the orchestrator data directory permissions",
            ) from exc
        try:
            yield
        except BaseException:
            with suppress(BaseException):
                await asyncio.to_thread(lock.release)
            raise
        else:
            try:
                await asyncio.to_thread(lock.release)
            except OSError as exc:
                raise TokenStoreError(
                    "Unable to release registration token state lock",
                    remediation="Check the orchestrator data directory permissions",
                ) from exc

    @staticmethod
    def _safe_metadata(value: str, secret: str, fallback: str) -> str:
        if secret and secret in value:
            return fallback
        return sanitise_public_message(value[:256], fallback=fallback)

    @classmethod
    def _validate_metadata(cls, reason: str, request_id: str, secret: str) -> None:
        if secret and (secret in reason or secret in request_id):
            raise TokenRotationError(
                "Registration token rotation metadata is invalid",
                remediation="Use a reason and request ID that do not contain secret material",
            )

    @staticmethod
    def _snapshot_file(path: Path) -> bytes | None:
        try:
            return path.read_bytes() if path.is_file() else None
        except OSError as exc:
            raise TokenStoreError(
                "Unable to snapshot registration token state",
                remediation="Check the orchestrator data directory permissions",
            ) from exc

    @staticmethod
    def _restore_snapshot(path: Path, snapshot: bytes | None) -> None:
        if snapshot is None:
            path.unlink(missing_ok=True)
            return
        path.write_bytes(snapshot)
        path.chmod(_TOKEN_MODE)

    def _restore_snapshot_best_effort(self, path: Path, snapshot: bytes | None) -> None:
        with suppress(BaseException):
            self._restore_snapshot(path, snapshot)

    @staticmethod
    def _remove_file_best_effort(path: Path) -> None:
        with suppress(OSError):
            path.unlink()

    @staticmethod
    def _ensure_mode(path: Path) -> None:
        path.chmod(_TOKEN_MODE)


__all__ = [
    "RegistrationTokenAudit",
    "RegistrationTokenStatus",
    "RegistrationTokenStore",
    "RolloutResult",
    "TokenRotationError",
    "TokenSource",
    "TokenStoreError",
]
