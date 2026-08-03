"""Persisted registration-token lifecycle and audit records."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from acheron.core.errors import AcheronError, sanitise_public_message

TokenSource = Literal["environment", "file"]
_MAX_AUDIT_RECORDS = 100
_REGISTRATION_FILE_NAME = ".registration_token"
_AUDIT_FILE_NAME = ".registration_token.audit.jsonl"
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


Rollout = Callable[[str], Awaitable[RolloutResult]]


def _fingerprint(token: str) -> str:
    """Return a short fingerprint without exposing token material."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


class RegistrationTokenStore:
    """Persist and rotate a registration token without exposing its value."""

    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)
        self.token_path = self.data_dir / _REGISTRATION_FILE_NAME
        self.audit_path = self.data_dir / _AUDIT_FILE_NAME
        self._configured_token: str | None = None

    def load_or_create(self, configured_token: str | None) -> str:
        """Load an explicit or persisted token, creating the file-backed value when absent."""
        self._configured_token = configured_token or None
        if self._configured_token is not None:
            return self._configured_token
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.token_path.is_file():
            token = self._read_file_token()
            self._ensure_mode(self.token_path)
            return token

        token = secrets.token_hex(16)
        try:
            self._atomic_write_secret(token)
            self._append_audit(
                RegistrationTokenAudit(
                    timestamp=_now(),
                    reason="created",
                    old_fingerprint=None,
                    new_fingerprint=_fingerprint(token),
                    worker_ids=(),
                    result="created",
                    request_id="",
                )
            )
        except OSError as exc:
            raise TokenStoreError(
                "Unable to persist the registration token",
                remediation="Check the orchestrator data directory permissions",
            ) from exc
        return token

    def read_current(self) -> str:
        """Read the active token from the configured source."""
        if self._configured_token is not None:
            return self._configured_token
        if not self.token_path.is_file():
            raise TokenStoreError(
                "Registration token is not initialized",
                remediation="Start the orchestrator before reading its registration token",
            )
        return self._read_file_token()

    def status(self) -> RegistrationTokenStatus:
        """Return secret-free source, timing, rotation, and fingerprint metadata."""
        source: TokenSource = "environment" if self._configured_token is not None else "file"
        token = self.read_current()
        records = self._read_audits()
        created = next((record.timestamp for record in records if record.result == "created"), None)
        rotations = [record for record in records if record.result == "success"]
        if created is None and source == "file":
            try:
                created = datetime.fromtimestamp(self.token_path.stat().st_mtime, UTC)
            except OSError as exc:
                raise TokenStoreError(
                    "Unable to inspect registration token metadata",
                    remediation="Check the orchestrator data directory permissions",
                ) from exc
        return RegistrationTokenStatus(
            source=source,
            created_at=created,
            last_rotation_at=rotations[-1].timestamp if rotations else None,
            rotation_count=len(rotations),
            fingerprint=_fingerprint(token),
        )

    async def rotate(self, reason: str, request_id: str, rollout: Rollout) -> RegistrationTokenStatus:
        """Rotate a file-backed token and roll it back if worker rollout fails."""
        if self._configured_token is not None:
            raise TokenRotationError(
                "Environment registration tokens cannot rotate in place",
                remediation="Update the worker environment and restart workers externally",
            )
        old_token = self.read_current()
        candidate = secrets.token_hex(16)
        timestamp = _now()
        safe_reason = sanitise_public_message(reason[:256], fallback="operator rotation")
        safe_request_id = sanitise_public_message(request_id[:128], fallback="unknown-request")
        try:
            self._atomic_write_secret(candidate)
            result = await rollout(candidate)
        except Exception as exc:
            self._restore_secret(old_token)
            self._append_audit(
                RegistrationTokenAudit(
                    timestamp=timestamp,
                    reason=safe_reason,
                    old_fingerprint=_fingerprint(old_token),
                    new_fingerprint=_fingerprint(candidate),
                    worker_ids=(),
                    request_id=safe_request_id,
                    result="failed",
                )
            )
            if isinstance(exc, TokenRotationError):
                raise
            raise TokenRotationError(
                "Registration token rollout failed; the previous token was restored",
                remediation="Check worker connectivity and retry the rotation",
            ) from exc
        if not result.success:
            self._restore_secret(old_token)
            self._append_audit(
                RegistrationTokenAudit(
                    timestamp=timestamp,
                    reason=safe_reason,
                    old_fingerprint=_fingerprint(old_token),
                    new_fingerprint=_fingerprint(candidate),
                    worker_ids=result.worker_ids,
                    request_id=safe_request_id,
                    result="failed",
                )
            )
            detail = sanitise_public_message(
                result.message or "worker rollout failed",
                fallback="worker rollout failed",
            )
            message = f"Registration token rollout failed: {detail}; the previous token was restored"
            raise TokenRotationError(message, remediation="Check worker connectivity and retry the rotation")
        self._append_audit(
            RegistrationTokenAudit(
                timestamp=timestamp,
                reason=safe_reason,
                old_fingerprint=_fingerprint(old_token),
                new_fingerprint=_fingerprint(candidate),
                worker_ids=result.worker_ids,
                request_id=safe_request_id,
                result="success",
            )
        )
        return self.status()

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

    def _atomic_write_secret(self, token: str) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f"{_REGISTRATION_FILE_NAME}.", dir=self.data_dir)
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(fd, _TOKEN_MODE)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(token)
                stream.flush()
                os.fsync(stream.fileno())
            temporary_path.replace(self.token_path)
            self._ensure_mode(self.token_path)
        except OSError:
            temporary_path.unlink(missing_ok=True)
            raise

    def _restore_secret(self, token: str) -> None:
        try:
            self._atomic_write_secret(token)
        except OSError as exc:
            raise TokenRotationError(
                "Unable to restore the previous registration token",
                remediation="Restore the token file from a protected backup before restarting workers",
            ) from exc

    def _append_audit(self, audit: RegistrationTokenAudit) -> None:
        records = self._read_audits()
        records.append(audit)
        records = records[-_MAX_AUDIT_RECORDS:]
        self.data_dir.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f"{_AUDIT_FILE_NAME}.", dir=self.data_dir)
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(fd, _TOKEN_MODE)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                for record in records:
                    payload = asdict(record)
                    payload["timestamp"] = record.timestamp.isoformat()
                    payload["worker_ids"] = list(record.worker_ids)
                    stream.write(json.dumps(payload, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary_path.replace(self.audit_path)
            self._ensure_mode(self.audit_path)
        except OSError as exc:
            temporary_path.unlink(missing_ok=True)
            raise TokenStoreError(
                "Unable to persist registration token audit history",
                remediation="Check the orchestrator data directory permissions",
            ) from exc

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
                if timestamp is None:
                    continue
                worker_values = data.get("worker_ids", ())
                worker_ids = tuple(value for value in worker_values if isinstance(value, str))
                records.append(
                    RegistrationTokenAudit(
                        timestamp=timestamp,
                        reason=str(data.get("reason", "")),
                        old_fingerprint=(
                            data.get("old_fingerprint") if isinstance(data.get("old_fingerprint"), str) else None
                        ),
                        new_fingerprint=(
                            data.get("new_fingerprint") if isinstance(data.get("new_fingerprint"), str) else None
                        ),
                        worker_ids=worker_ids,
                        result=str(data.get("result", "")),
                        request_id=str(data.get("request_id", "")),
                    )
                )
            except TypeError, ValueError, json.JSONDecodeError:
                continue
        return records

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
