import asyncio
import json
import stat
from pathlib import Path

import pytest

from acheron.shell.token_auth import (
    RegistrationTokenStore,
    RolloutResult,
    TokenRotationError,
    TokenStoreError,
    _FileLock,
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


def test_rotation_rejects_secret_metadata_without_leaking_it(tmp_path: Path) -> None:
    store = RegistrationTokenStore(tmp_path)
    token = store.load_or_create(None)

    with pytest.raises(TokenRotationError) as raised:
        asyncio.run(store.rotate(token, "request-1", _successful_rollout))

    assert token not in str(raised.value)
    assert token not in store.audit_path.read_text(encoding="utf-8")


def test_rollout_error_and_worker_ids_never_persist_secret(tmp_path: Path) -> None:
    store = RegistrationTokenStore(tmp_path)
    old_token = store.load_or_create(None)
    seen: list[str] = []

    async def rollout(token: str) -> RolloutResult:
        seen.append(token)
        if len(seen) == 1:
            return RolloutResult(
                success=False,
                worker_ids=(old_token, token),
                message=token,
                remediation=f"retry with {old_token} or {token}",
            )
        return RolloutResult(success=True)

    with pytest.raises(TokenRotationError) as raised:
        asyncio.run(store.rotate("safe reason", "request-1", rollout))

    candidate = seen[0]
    audit = store.audit_path.read_text(encoding="utf-8")
    assert candidate not in str(raised.value)
    assert candidate not in (raised.value.remediation or "")
    assert old_token not in (raised.value.remediation or "")
    assert candidate not in audit
    assert old_token not in audit


def test_audit_failure_restores_token_and_workers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = RegistrationTokenStore(tmp_path)
    old_token = store.load_or_create(None)
    calls: list[str] = []
    original_replace = Path.replace

    def fail_audit_replace(path: Path, target: Path) -> Path:
        if target == store.audit_path:
            raise OSError("audit unavailable")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_audit_replace)

    async def rollout(token: str) -> RolloutResult:
        calls.append(token)
        return RolloutResult(success=True)

    with pytest.raises(TokenStoreError, match="previous token was restored"):
        asyncio.run(store.rotate("audit failure", "request-1", rollout))

    assert store.read_current() == old_token
    assert len(calls) == 2
    assert calls[1] == old_token


@pytest.mark.asyncio
async def test_cancellation_restores_token_and_reraises(tmp_path: Path) -> None:
    store = RegistrationTokenStore(tmp_path)
    old_token = store.load_or_create(None)
    calls: list[str] = []

    async def rollout(token: str) -> RolloutResult:
        calls.append(token)
        if len(calls) == 1:
            raise asyncio.CancelledError
        return RolloutResult(success=True)

    with pytest.raises(asyncio.CancelledError):
        await store.rotate("cancelled", "request-1", rollout)

    assert store.read_current() == old_token
    assert len(calls) == 2
    assert calls[1] == old_token


@pytest.mark.asyncio
async def test_concurrent_rotations_preserve_both_audits(tmp_path: Path) -> None:
    first = RegistrationTokenStore(tmp_path)
    second = RegistrationTokenStore(tmp_path)
    first.load_or_create(None)
    observed: list[str] = []

    async def rollout(token: str) -> RolloutResult:
        observed.append(token)
        await asyncio.sleep(0)
        return RolloutResult(success=True)

    await asyncio.gather(
        first.rotate("first", "request-1", rollout),
        second.rotate("second", "request-2", rollout),
    )

    assert len(observed) == 2
    records = [json.loads(line) for line in store_audit(tmp_path).splitlines()]
    assert [record["result"] for record in records].count("success") == 2
    assert RegistrationTokenStore(tmp_path).status().rotation_count == 2


@pytest.mark.asyncio
async def test_typed_rollout_error_is_preserved_after_rollback(tmp_path: Path) -> None:
    store = RegistrationTokenStore(tmp_path)
    store.load_or_create(None)
    expected = TokenRotationError("typed rollout failure", remediation="retry later")
    calls = 0

    async def rollout(_: str) -> RolloutResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise expected
        return RolloutResult(success=True)

    with pytest.raises(TokenRotationError) as raised:
        await store.rotate("typed failure", "request-typed", rollout)

    assert raised.value is expected
    assert str(raised.value) == "typed rollout failure"
    assert store.status().rotation_count == 0
    assert calls == 2


@pytest.mark.asyncio
async def test_candidate_write_failure_is_typed_and_preserves_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = RegistrationTokenStore(tmp_path)
    old_token = store.load_or_create(None)

    def fail_candidate(_: str) -> None:
        raise OSError("candidate write unavailable")

    monkeypatch.setattr(store, "_atomic_write_secret", fail_candidate)

    with pytest.raises(TokenStoreError) as raised:
        await store.rotate("candidate failure", "request-candidate", _successful_rollout)

    assert "candidate write unavailable" not in str(raised.value)
    assert store.read_current() == old_token


@pytest.mark.asyncio
async def test_lock_release_failure_is_typed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = RegistrationTokenStore(tmp_path)
    store.load_or_create(None)
    original_release = _FileLock.release

    def fail_release(lock: _FileLock) -> None:
        if lock.path == store.lock_path:
            raise OSError("lock release unavailable")
        original_release(lock)

    monkeypatch.setattr(_FileLock, "release", fail_release)

    with pytest.raises(TokenStoreError) as raised:
        await store.rotate("release failure", "request-release", _successful_rollout)

    assert "lock release unavailable" not in str(raised.value)


@pytest.mark.asyncio
async def test_concurrent_lock_wait_does_not_block_heartbeat(tmp_path: Path) -> None:
    first = RegistrationTokenStore(tmp_path)
    second = RegistrationTokenStore(tmp_path)
    first.load_or_create(None)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def first_rollout(_: str) -> RolloutResult:
        entered.set()
        await release.wait()
        return RolloutResult(success=True)

    first_task = asyncio.create_task(first.rotate("first", "request-heartbeat-1", first_rollout))
    await entered.wait()
    second_task = asyncio.create_task(second.rotate("second", "request-heartbeat-2", _successful_rollout))
    heartbeat = asyncio.Event()

    async def pulse() -> None:
        await asyncio.sleep(0)
        heartbeat.set()

    await asyncio.wait_for(pulse(), timeout=1)
    assert heartbeat.is_set()
    release.set()
    await asyncio.gather(first_task, second_task)


@pytest.mark.asyncio
async def test_rollback_failure_does_not_replace_original_error(tmp_path: Path) -> None:
    store = RegistrationTokenStore(tmp_path)
    store.load_or_create(None)
    calls = 0

    async def rollout(_: str) -> RolloutResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("original rollout failure")
        raise RuntimeError("rollback failure")

    with pytest.raises(TokenRotationError, match="previous token was restored") as raised:
        await store.rotate("failed", "request-1", rollout)

    assert "rollback failure" not in str(raised.value)


def test_lifecycle_metadata_is_independent_of_bounded_audit(tmp_path: Path) -> None:
    store = RegistrationTokenStore(tmp_path)
    created = store.load_or_create(None)
    initial = store.status()

    for index in range(105):
        asyncio.run(store.rotate(f"rotation-{index}", f"request-{index}", _successful_rollout))

    final = store.status()
    records = [json.loads(line) for line in store.audit_path.read_text(encoding="utf-8").splitlines()]
    assert final.created_at == initial.created_at
    assert final.rotation_count == 105
    assert len(records) == 100
    assert created not in store.audit_path.read_text(encoding="utf-8")


def store_audit(tmp_path: Path) -> str:
    return (tmp_path / ".registration_token.audit.jsonl").read_text(encoding="utf-8")


async def _successful_rollout(token: str) -> RolloutResult:
    return RolloutResult(success=True, worker_ids=("worker-a",))
