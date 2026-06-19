"""Tests for the worker registry."""

import pytest

from acheron.core.models import WorkerCapabilities, WorkerType
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
        await reg.register("w-1", "http://new", "http", _tts_caps())
        w = await reg.get("w-1")
        assert w is not None
        assert w.endpoint == "http://new"

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
    async def test_health_success_resets_counter(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w-1", "http://a", "http", _tts_caps())
        await reg.record_health_failure("w-1")
        await reg.record_health_failure("w-1")
        w = await reg.get("w-1")
        assert w is not None
        assert w.consecutive_failures == 2
        await reg.record_health_success("w-1")
        w = await reg.get("w-1")
        assert w is not None
        assert w.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_health_failure_increments(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w-1", "http://a", "http", _tts_caps())
        removed = await reg.record_health_failure("w-1")
        assert not removed
        w = await reg.get("w-1")
        assert w is not None
        assert w.consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_removed_after_max_failures(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w-1", "http://a", "http", _tts_caps())
        await reg.record_health_failure("w-1")
        await reg.record_health_failure("w-1")
        removed = await reg.record_health_failure("w-1")
        assert removed
        assert await reg.get("w-1") is None

    @pytest.mark.asyncio
    async def test_health_failure_nonexistent(self) -> None:
        reg = InMemoryWorkerStore()
        removed = await reg.record_health_failure("nope")
        assert not removed

    @pytest.mark.asyncio
    async def test_health_success_nonexistent(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.record_health_success("nope")
