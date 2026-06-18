"""Integration tests for the Redis worker store."""

import pytest
import redis

from acheron.core.models import WorkerCapabilities, WorkerType
from acheron.shell.stores.redis import RedisWorkerStore


def _tts_caps() -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TTS,
        supported_languages_in=frozenset({"en"}),
        supported_languages_out=frozenset({"es"}),
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"wav"}),
        max_payload_bytes=None,
        batch_capable=True,
        model_source=None,
    )


@pytest.fixture
def store(redis_url: str) -> RedisWorkerStore:
    return RedisWorkerStore(redis_url)


class TestRegister:
    def test_register_and_get(self, store: RedisWorkerStore) -> None:
        store.register("w-1", "http://host:8001", "http", _tts_caps())
        w = store.get("w-1")
        assert w is not None
        assert w.worker_id == "w-1"
        assert w.endpoint == "http://host:8001"
        assert w.transport == "http"
        assert w.capabilities.worker_type == WorkerType.TTS
        assert w.capabilities.supported_languages_in == frozenset({"en"})
        assert w.capabilities.supported_languages_out == frozenset({"es"})

    def test_get_nonexistent(self, store: RedisWorkerStore) -> None:
        result = store.get("nope")
        assert result is None

    def test_unregister(self, store: RedisWorkerStore) -> None:
        store.register("w-1", "http://a", "http", _tts_caps())
        store.unregister("w-1")
        result = store.get("w-1")
        assert result is None

    def test_reregistration_overwrites(self, store: RedisWorkerStore) -> None:
        store.register("w-1", "http://old", "http", _tts_caps())
        store.register("w-1", "http://new", "http", _tts_caps())
        w = store.get("w-1")
        assert w is not None
        assert w.endpoint == "http://new"


class TestListing:
    def test_list_all(self, store: RedisWorkerStore) -> None:
        store.register("w-1", "http://a", "http", _tts_caps())
        store.register("w-2", "http://b", "http", _tts_caps())
        workers = store.list_all()
        ids = {w.worker_id for w in workers}
        assert ids == {"w-1", "w-2"}

    def test_find_by_type(self, store: RedisWorkerStore) -> None:
        store.register("tts-1", "http://a", "http", _tts_caps())
        asr = WorkerCapabilities(
            worker_type=WorkerType.ASR,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"en"}),
            supported_formats_in=frozenset({"mp3"}),
            supported_formats_out=frozenset({"text"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
        )
        store.register("asr-1", "http://b", "http", asr)
        tts_workers = store.find_by_type(WorkerType.TTS)
        assert len(tts_workers) == 1
        assert tts_workers[0].worker_id == "tts-1"


class TestHealthTracking:
    def test_failure_increments_and_removes(self, store: RedisWorkerStore) -> None:
        store.register("w-1", "http://a", "http", _tts_caps())
        assert not store.record_health_failure("w-1")
        assert not store.record_health_failure("w-1")
        assert store.record_health_failure("w-1")
        assert store.get("w-1") is None

    def test_success_resets_counter(self, store: RedisWorkerStore) -> None:
        store.register("w-1", "http://a", "http", _tts_caps())
        store.record_health_failure("w-1")
        store.record_health_failure("w-1")
        store.record_health_success("w-1")
        w = store.get("w-1")
        assert w is not None
        assert w.consecutive_failures == 0


class TestFailFast:
    def test_unreachable_redis_raises_on_init(self) -> None:
        with pytest.raises(redis.RedisError):
            RedisWorkerStore("redis://localhost:1")
