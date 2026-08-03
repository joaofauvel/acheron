import asyncio
import json
import stat
from pathlib import Path

import pytest

from acheron.shell.token_auth import (
    RegistrationTokenStore,
    RolloutResult,
    TokenRotationError,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _readme() -> str:
    return (REPO_ROOT / "README.md").read_text(encoding="utf-8")


def test_token_source_matrix_is_explicit_and_distinct() -> None:
    readme = _readme().casefold()
    expected = (
        "acheron_registration_token",
        "static, externally managed",
        "file-backed auto-mint",
        ".registration_token",
        "acheron_worker__registration_token_file",
        "reload-aware",
    )
    for phrase in expected:
        assert phrase in readme, f"token source contract omits {phrase!r}"


def test_token_status_contract_names_source_without_plaintext() -> None:
    readme = _readme().casefold()
    assert "source=environment" in readme
    assert "source=file" in readme
    assert "fingerprint" in readme
    assert "status output never contains the token" in readme
    assert "named `acheron-data` volume" in readme
    assert "/data/jobs/.registration_token" in readme
    assert "0123456789abcdef" not in readme


def test_environment_rotation_contract_has_external_remediation() -> None:
    readme = _readme().casefold()
    assert "acheron token rotate --reason" in readme
    assert "cannot rotate in place" in readme
    assert "exits nonzero" in readme
    assert "update/restart workers externally" in readme


def test_file_backed_token_is_created_with_metadata_and_0600_permissions(tmp_path: Path) -> None:
    store = RegistrationTokenStore(tmp_path)

    token = store.load_or_create(None)
    status = store.status()

    assert len(token) == 32
    assert store.token_path.read_text(encoding="utf-8") == token
    assert stat.S_IMODE(store.token_path.stat().st_mode) == 0o600
    assert store.audit_path.is_file()
    assert stat.S_IMODE(store.audit_path.stat().st_mode) == 0o600
    assert status.source == "file"
    assert status.fingerprint is not None
    assert token not in store.audit_path.read_text(encoding="utf-8")


def test_existing_file_backed_token_is_reused_without_regeneration(tmp_path: Path) -> None:
    store = RegistrationTokenStore(tmp_path)
    first = store.load_or_create(None)

    second = RegistrationTokenStore(tmp_path).load_or_create(None)

    assert second == first
    assert store.token_path.read_text(encoding="utf-8") == first


def test_environment_token_is_not_written_or_rotated(tmp_path: Path) -> None:
    configured = "environment-token-value"
    store = RegistrationTokenStore(tmp_path)

    assert store.load_or_create(configured) == configured
    assert not store.token_path.exists()
    with pytest.raises(TokenRotationError, match="cannot rotate in place"):
        asyncio.run(store.rotate("operator request", "request-1", _successful_rollout))
    assert not store.token_path.exists()


def test_status_redacts_token(tmp_path: Path) -> None:
    token = "legacy-single-line-token"
    (tmp_path / ".registration_token").write_text(token, encoding="utf-8")
    store = RegistrationTokenStore(tmp_path)

    store.load_or_create(None)
    status = store.status()

    assert token not in repr(status)
    assert status.fingerprint is not None
    assert status.fingerprint != token


def test_rotation_writes_secret_free_audit(tmp_path: Path) -> None:
    store = RegistrationTokenStore(tmp_path)
    old_token = store.load_or_create(None)
    observed: list[str] = []

    async def rollout(token: str) -> RolloutResult:
        observed.append(token)
        return RolloutResult(success=True, worker_ids=("worker-a",))

    status = asyncio.run(store.rotate("planned rotation", "request-123", rollout))
    audit = store.audit_path.read_text(encoding="utf-8")
    records = [json.loads(line) for line in audit.splitlines()]

    assert observed
    assert observed[0] != old_token
    assert status.rotation_count == 1
    assert records[-1]["result"] == "success"
    assert records[-1]["worker_ids"] == ["worker-a"]
    assert observed[0] not in audit
    assert old_token not in audit


def test_rotation_rolls_back_and_audits_failure(tmp_path: Path) -> None:
    store = RegistrationTokenStore(tmp_path)
    old_token = store.load_or_create(None)

    async def rollout(_: str) -> RolloutResult:
        return RolloutResult(success=False, worker_ids=("worker-a",), message="worker unavailable")

    with pytest.raises(TokenRotationError, match="previous token was restored"):
        asyncio.run(store.rotate("failed rotation", "request-456", rollout))

    assert store.read_current() == old_token
    audit = store.audit_path.read_text(encoding="utf-8")
    assert '"result": "failed"' in audit
    assert old_token not in audit


async def _successful_rollout(token: str) -> RolloutResult:
    return RolloutResult(success=True, worker_ids=("worker-a",))
