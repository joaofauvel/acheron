"""Tests for the worker registry."""

import pytest

from acheron.core.models import WorkerCapabilities, WorkerStatus, WorkerType
from acheron.shell.stores.memory import InMemoryWorkerStore


def _tts_caps(
    langs_in: frozenset[str] = frozenset({"en"}), langs_out: frozenset[str] = frozenset({"es"})
) -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TTS,
        supported_languages_in=langs_in,
        supported_languages_out=langs_out,
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"wav"}),
        max_payload_bytes=None,
        batch_capable=True,
        model_source=None,
    )


def _asr_caps() -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.ASR,
        supported_languages_in=frozenset({"en", "es"}),
        supported_languages_out=frozenset({"en", "es"}),
        supported_formats_in=frozenset({"mp3", "wav"}),
        supported_formats_out=frozenset({"text"}),
        max_payload_bytes=None,
        batch_capable=False,
        model_source=None,
    )


class TestInMemoryWorkerStore:
    @pytest.mark.asyncio
    async def test_register_and_get(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w-1", "http://localhost:8001", "http", _tts_caps())
        w = await reg.get("w-1")
        assert w is not None
        assert w.worker_id == "w-1"
        assert w.endpoint == "http://localhost:8001"
        assert w.transport == "http"
        assert w.booting_since is None

    @pytest.mark.asyncio
    async def test_get_nonexistent(self) -> None:
        reg = InMemoryWorkerStore()
        assert await reg.get("nope") is None

    @pytest.mark.asyncio
    async def test_unregister(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w-1", "http://localhost:8001", "http", _tts_caps())
        await reg.unregister("w-1")
        assert await reg.get("w-1") is None

    @pytest.mark.asyncio
    async def test_unregister_nonexistent(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.unregister("nope")

    @pytest.mark.asyncio
    async def test_list_all(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w-1", "http://a", "http", _tts_caps())
        await reg.register("w-2", "http://b", "http", _asr_caps())
        workers = await reg.list_all()
        assert len(workers) == 2
        ids = {w.worker_id for w in workers}
        assert ids == {"w-1", "w-2"}

    @pytest.mark.asyncio
    async def test_reregistration_overwrites(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w-1", "http://old", "http", _tts_caps())
        await reg.set_worker_status("w-1", WorkerStatus.BOOTING, "starting")
        await reg.record_health_failure("w-1")
        await reg.register("w-1", "http://new", "http", _tts_caps())
        w = await reg.get("w-1")
        assert w is not None
        assert w.endpoint == "http://new"
        assert w.status == WorkerStatus.HEALTHY
        assert w.last_error is None
        assert w.consecutive_failures == 0
        assert w.booting_since is None

    @pytest.mark.asyncio
    async def test_find_by_type(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://a", "http", _tts_caps())
        await reg.register("asr-1", "http://b", "http", _asr_caps())
        await reg.register("tts-2", "http://c", "http", _tts_caps())
        tts_workers = await reg.find_by_type(WorkerType.TTS)
        assert len(tts_workers) == 2
        asr_workers = await reg.find_by_type(WorkerType.ASR)
        assert len(asr_workers) == 1

    @pytest.mark.asyncio
    async def test_find_by_language(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w-1", "http://a", "http", _tts_caps(frozenset({"en"}), frozenset({"es"})))
        await reg.register("w-2", "http://b", "http", _tts_caps(frozenset({"en"}), frozenset({"fr"})))
        await reg.register("w-3", "http://c", "http", _tts_caps(frozenset({"es"}), frozenset({"en"})))
        en_to_es = await reg.find_by_language("en", "es")
        assert len(en_to_es) == 1
        assert en_to_es[0].worker_id == "w-1"

    @pytest.mark.asyncio
    async def test_find_by_language_no_match(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w-1", "http://a", "http", _tts_caps(frozenset({"en"}), frozenset({"es"})))
        result = await reg.find_by_language("ja", "ko")
        assert len(result) == 0


class TestHealthTracking:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [
            "https://user:password@example.internal:8443/health",
            "worker.internal:443 connection refused",
            'Traceback (most recent call last):\\n  File \\"/srv/worker.py\\", line 1',
            "Authorization: Bearer super-secret-token",
            "provider request_id=req-123 body=private",
        ],
    )
    async def test_failure_history_redacts_sensitive_diagnostics(self, error: str) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w-1", "http://a", "http", _tts_caps())
        worker = await reg.get("w-1")
        assert worker is not None
        await reg.record_health_failure("w-1", generation=worker.registration_generation, error=error)
        stored = await reg.get("w-1")
        assert stored is not None
        message = stored.error_history[-1].message
        assert all(
            secret not in message for secret in ("password", "example.internal", "super-secret-token", "req-123")
        )
        assert "Traceback" not in message

    @pytest.mark.asyncio
    async def test_health_success_resets_counter_and_preserves_history(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w-1", "http://a", "http", _tts_caps())
        worker = await reg.get("w-1")
        assert worker is not None
        await reg.record_health_failure("w-1", generation=worker.registration_generation, error="connection refused")
        await reg.record_health_failure("w-1", generation=worker.registration_generation, error="Bearer secret")
        w = await reg.get("w-1")
        assert w is not None
        assert w.consecutive_failures == 2
        await reg.record_health_success("w-1", generation=w.registration_generation)
        w = await reg.get("w-1")
        assert w is not None
        assert w.consecutive_failures == 0
        assert w.last_error is None
        assert len(w.error_history) == 2
        assert "secret" not in w.error_history[-1].message

    @pytest.mark.asyncio
    async def test_health_failure_increments(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w-1", "http://a", "http", _tts_caps())
        worker = await reg.get("w-1")
        assert worker is not None
        removed = await reg.record_health_failure("w-1", generation=worker.registration_generation, error="down")
        assert not removed
        w = await reg.get("w-1")
        assert w is not None
        assert w.consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_removed_after_max_failures(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w-1", "http://a", "http", _tts_caps())
        worker = await reg.get("w-1")
        assert worker is not None
        removed = False
        for _ in range(3):
            removed = await reg.record_health_failure("w-1", generation=worker.registration_generation, error="down")
        assert removed
        assert await reg.get("w-1") is None
        await reg.register("w-1", "http://b", "http", _tts_caps())
        restored = await reg.get("w-1")
        assert restored is not None
        assert restored.consecutive_failures == 0
        assert len(restored.error_history) == 3

    @pytest.mark.asyncio
    async def test_stale_generation_is_ignored_after_reregistration(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w-1", "http://a", "http", _tts_caps())
        old = await reg.get("w-1")
        assert old is not None
        await reg.register("w-1", "http://b", "http", _tts_caps())
        current = await reg.get("w-1")
        assert current is not None
        assert not await reg.record_health_failure("w-1", generation=old.registration_generation, error="stale")
        assert current.consecutive_failures == 0
        assert current.error_history == ()

    @pytest.mark.asyncio
    async def test_health_failure_nonexistent(self) -> None:
        reg = InMemoryWorkerStore()
        removed = await reg.record_health_failure("nope")
        assert not removed

    @pytest.mark.asyncio
    async def test_health_success_nonexistent(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.record_health_success("nope")


class TestWorkerStatusTracking:
    @pytest.mark.asyncio
    async def test_set_worker_status_persists_booting_lifecycle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w-1", "http://a", "http", _tts_caps())
        monkeypatch.setattr("acheron.shell.stores.memory.time.time", lambda: 100.0)
        await reg.set_worker_status("w-1", WorkerStatus.BOOTING, "cold start")
        w = await reg.get("w-1")
        assert w is not None
        assert w.status == WorkerStatus.BOOTING
        assert w.last_error == "cold start"
        assert w.booting_since == 100.0

        monkeypatch.setattr("acheron.shell.stores.memory.time.time", lambda: 200.0)
        await reg.set_worker_status("w-1", WorkerStatus.BOOTING, "still starting")
        w = await reg.get("w-1")
        assert w is not None
        assert w.booting_since == 100.0
        assert w.last_error == "still starting"

        await reg.set_worker_status("w-1", WorkerStatus.HEALTHY, None)
        w = await reg.get("w-1")
        assert w is not None
        assert w.booting_since is None
        await reg.set_worker_status("w-1", WorkerStatus.OFFLINE, "down")
        w = await reg.get("w-1")
        assert w is not None
        assert w.booting_since is None
        await reg.unregister("w-1")
        assert await reg.get("w-1") is None

    @pytest.mark.asyncio
    async def test_set_worker_status_nonexistent_is_noop(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.set_worker_status("nope", WorkerStatus.OFFLINE, "err")

    @pytest.mark.asyncio
    async def test_record_health_success_resets_status_and_error(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w-1", "http://a", "http", _tts_caps())
        await reg.set_worker_status("w-1", WorkerStatus.OFFLINE, "boom")
        await reg.record_health_success("w-1")
        w = await reg.get("w-1")
        assert w is not None
        assert w.status == WorkerStatus.HEALTHY
        assert w.last_error is None

    @pytest.mark.asyncio
    async def test_new_worker_defaults_to_healthy(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w-1", "http://a", "http", _tts_caps())
        w = await reg.get("w-1")
        assert w is not None
        assert w.status == WorkerStatus.HEALTHY
        assert w.last_error is None
