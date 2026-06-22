"""Integration tests for the Redis worker store."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
import redis
import redis.asyncio as aioredis
from redis.exceptions import ConnectionError as RedisConnectionError

from acheron.core.models import JsonValue, WorkerCapabilities, WorkerStatus, WorkerType
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


@pytest_asyncio.fixture
async def store(redis_url: str) -> AsyncIterator[RedisWorkerStore]:
    s = RedisWorkerStore(redis_url)
    await s.connect()
    try:
        yield s
    finally:
        await s.close()


class TestRegister:
    @pytest.mark.asyncio
    async def test_register_and_get(self, store: RedisWorkerStore) -> None:
        await store.register("w-1", "http://host:8001", "http", _tts_caps())
        w = await store.get("w-1")
        assert w is not None
        assert w.worker_id == "w-1"
        assert w.endpoint == "http://host:8001"
        assert w.transport == "http"
        assert w.capabilities.worker_type == WorkerType.TTS
        assert w.capabilities.supported_languages_in == frozenset({"en"})
        assert w.capabilities.supported_languages_out == frozenset({"es"})

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, store: RedisWorkerStore) -> None:
        result = await store.get("nope")
        assert result is None

    @pytest.mark.asyncio
    async def test_unregister(self, store: RedisWorkerStore) -> None:
        await store.register("w-1", "http://a", "http", _tts_caps())
        await store.unregister("w-1")
        result = await store.get("w-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_reregistration_overwrites(self, store: RedisWorkerStore) -> None:
        await store.register("w-1", "http://old", "http", _tts_caps())
        await store.register("w-1", "http://new", "http", _tts_caps())
        w = await store.get("w-1")
        assert w is not None
        assert w.endpoint == "http://new"


class TestListing:
    @pytest.mark.asyncio
    async def test_list_all(self, store: RedisWorkerStore) -> None:
        await store.register("w-1", "http://a", "http", _tts_caps())
        await store.register("w-2", "http://b", "http", _tts_caps())
        workers = await store.list_all()
        ids = {w.worker_id for w in workers}
        assert ids == {"w-1", "w-2"}

    @pytest.mark.asyncio
    async def test_find_by_type(self, store: RedisWorkerStore) -> None:
        await store.register("tts-1", "http://a", "http", _tts_caps())
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
        await store.register("asr-1", "http://b", "http", asr)
        tts_workers = await store.find_by_type(WorkerType.TTS)
        assert len(tts_workers) == 1
        assert tts_workers[0].worker_id == "tts-1"


class TestCorruption:
    @pytest.mark.asyncio
    async def test_corrupt_worker_metadata_raises_cache_corrupted(
        self, store: RedisWorkerStore, redis_url: str
    ) -> None:
        """A corrupt metadata_json field must raise CacheCorruptedError, not raw JSONDecodeError."""
        from acheron.core.errors import CacheCorruptedError
        from acheron.shell.stores.redis import _WORKER_KEY

        r = aioredis.Redis.from_url(redis_url)
        await r.hset(  # type: ignore[misc]
            _WORKER_KEY.format(worker_id="w-corrupt"),
            mapping={"metadata_json": "{ bad json", "capabilities_json": "{}"},
        )
        await r.aclose()
        with pytest.raises(CacheCorruptedError, match="metadata is not valid JSON"):
            await store.get("w-corrupt")


class TestMetadataRoundTrip:
    @pytest.mark.asyncio
    async def test_worker_metadata_round_trips(self, store: RedisWorkerStore) -> None:
        """Worker metadata must survive serialize/deserialize through Redis."""
        meta: dict[str, JsonValue] = {"vram_gb": 8, "version": "1.0"}
        await store.register("w-meta", "http://h", "http", _tts_caps(), metadata=meta)
        w = await store.get("w-meta")
        assert w is not None
        assert w.metadata == meta

    @pytest.mark.asyncio
    async def test_capabilities_metadata_round_trips(self, store: RedisWorkerStore) -> None:
        """Capabilities.metadata must also survive the round-trip."""
        caps = WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"es"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav"}),
            max_payload_bytes=None,
            batch_capable=True,
            model_source=None,
            metadata={"runtime": "onnx", "vram_gb": 24},
        )
        await store.register("w-caps-meta", "http://h", "http", caps)
        w = await store.get("w-caps-meta")
        assert w is not None
        assert w.capabilities.metadata == {"runtime": "onnx", "vram_gb": 24}

    @pytest.mark.asyncio
    async def test_empty_metadata_defaults_to_empty_dict(self, store: RedisWorkerStore) -> None:
        """When metadata is omitted, the deserialized value must be {} not None."""
        await store.register("w-nometa", "http://h", "http", _tts_caps())
        w = await store.get("w-nometa")
        assert w is not None
        assert w.metadata == {}


class TestHealthTracking:
    @pytest.mark.asyncio
    async def test_failure_increments_and_removes(self, store: RedisWorkerStore) -> None:
        await store.register("w-1", "http://a", "http", _tts_caps())
        assert not await store.record_health_failure("w-1")
        assert not await store.record_health_failure("w-1")
        assert await store.record_health_failure("w-1")
        assert await store.get("w-1") is None

    @pytest.mark.asyncio
    async def test_success_resets_counter(self, store: RedisWorkerStore) -> None:
        await store.register("w-1", "http://a", "http", _tts_caps())
        await store.record_health_failure("w-1")
        await store.record_health_failure("w-1")
        await store.record_health_success("w-1")
        w = await store.get("w-1")
        assert w is not None
        assert w.consecutive_failures == 0


class TestFailFast:
    @pytest.mark.asyncio
    async def test_unreachable_redis_raises_on_connect(self) -> None:
        store = RedisWorkerStore("redis://localhost:1")
        with pytest.raises((RedisConnectionError, redis.RedisError)):
            await store.connect()


class TestConnectIdempotency:
    @pytest.mark.asyncio
    async def test_connect_is_idempotent(self, store: RedisWorkerStore) -> None:
        """Calling connect() twice does not raise."""
        await store.connect()
        await store.connect()


class TestCloseRobustness:
    @pytest.mark.asyncio
    async def test_close_does_not_crash_when_called_twice(self, redis_url: str) -> None:
        """close() can be called more than once without raising."""
        s = RedisWorkerStore(redis_url)
        await s.connect()
        await s.close()
        await s.close()


class TestStatusAndErrorRoundTrip:
    @pytest.mark.asyncio
    async def test_set_worker_status_round_trips(self, store: RedisWorkerStore) -> None:
        await store.register("w-1", "http://a", "http", _tts_caps())
        await store.set_worker_status("w-1", WorkerStatus.BOOTING, "cold start")
        w = await store.get("w-1")
        assert w is not None
        assert w.status == WorkerStatus.BOOTING
        assert w.last_error == "cold start"

    @pytest.mark.asyncio
    async def test_set_worker_status_nonexistent_is_noop(self, store: RedisWorkerStore) -> None:
        await store.set_worker_status("nope", WorkerStatus.OFFLINE, "err")

    @pytest.mark.asyncio
    async def test_record_health_success_resets_status_and_error(self, store: RedisWorkerStore) -> None:
        await store.register("w-1", "http://a", "http", _tts_caps())
        await store.set_worker_status("w-1", WorkerStatus.OFFLINE, "boom")
        await store.record_health_success("w-1")
        w = await store.get("w-1")
        assert w is not None
        assert w.status == WorkerStatus.HEALTHY
        assert w.last_error is None

    @pytest.mark.asyncio
    async def test_new_worker_defaults_to_healthy(self, store: RedisWorkerStore) -> None:
        await store.register("w-1", "http://a", "http", _tts_caps())
        w = await store.get("w-1")
        assert w is not None
        assert w.status == WorkerStatus.HEALTHY
        assert w.last_error is None
