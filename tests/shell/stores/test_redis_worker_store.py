"""Integration tests for the Redis worker store."""

import asyncio
from collections.abc import AsyncIterator
from typing import Self, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
import redis
import redis.asyncio as aioredis
from redis.exceptions import ConnectionError as RedisConnectionError

from acheron.core.models import JsonValue, WorkerCapabilities, WorkerStatus, WorkerType
from acheron.shell.stores.base import StoreError
from acheron.shell.stores.redis import RedisWorkerStore, _RedisAwaitable


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
        assert w.booting_since is None

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, store: RedisWorkerStore) -> None:
        result = await store.get("nope")
        assert result is None

    @pytest.mark.asyncio
    async def test_register_persists_ttl_bound_generation_key(self, store: RedisWorkerStore, redis_url: str) -> None:
        from acheron.shell.stores.redis import _WORKER_GENERATION_KEY

        redis_client = aioredis.Redis.from_url(redis_url, decode_responses=True)
        generation_key = _WORKER_GENERATION_KEY.format(worker_id="w-ttl")
        try:
            await redis_client.set(generation_key, 7, ex=60)
            assert await redis_client.ttl(generation_key) > 0
        finally:
            await redis_client.aclose()

        await store.register("w-ttl", "http://host:8001", "http", _tts_caps())
        worker = await store.get("w-ttl")
        assert worker is not None
        assert worker.registration_generation == 8

        redis_client = aioredis.Redis.from_url(redis_url, decode_responses=True)
        try:
            assert await redis_client.ttl(generation_key) == -1
        finally:
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_unregister(self, store: RedisWorkerStore, redis_url: str) -> None:
        from acheron.shell.stores.redis import _WORKER_GENERATION_KEY, _WORKER_HISTORY_TOMBSTONE_KEY

        await store.register("w-1", "http://a", "http", _tts_caps())
        worker = await store.get("w-1")
        assert worker is not None
        await store.unregister("w-1")
        result = await store.get("w-1")
        assert result is None

        redis_client = aioredis.Redis.from_url(redis_url, decode_responses=True)
        try:
            await redis_client.delete(_WORKER_HISTORY_TOMBSTONE_KEY.format(worker_id="w-1"))
            assert await redis_client.ttl(_WORKER_GENERATION_KEY.format(worker_id="w-1")) == -1
        finally:
            await redis_client.aclose()

        await store.register("w-1", "http://new", "http", _tts_caps())
        current = await store.get("w-1")
        assert current is not None
        assert current.registration_generation == worker.registration_generation + 1
        assert not await store.record_health_failure(
            "w-1", generation=worker.registration_generation, error="stale generation 1 failure"
        )
        current = await store.get("w-1")
        assert current is not None
        assert current.consecutive_failures == 0
        assert current.error_history == ()

    @pytest.mark.asyncio
    async def test_reregistration_overwrites(self, store: RedisWorkerStore) -> None:
        await store.register("w-1", "http://old", "http", _tts_caps())
        await store.set_worker_status("w-1", WorkerStatus.BOOTING, "starting")
        await store.record_health_failure("w-1")
        await store.register("w-1", "http://new", "http", _tts_caps())
        w = await store.get("w-1")
        assert w is not None
        assert w.endpoint == "http://new"
        assert w.status == WorkerStatus.HEALTHY
        assert w.last_error is None
        assert w.consecutive_failures == 0
        assert w.booting_since is None


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

    @pytest.mark.asyncio
    async def test_invalid_worker_status_raises_cache_corrupted(self, store: RedisWorkerStore, redis_url: str) -> None:
        """A ``status`` field whose value is not a valid ``WorkerStatus`` enum member
        must raise ``CacheCorruptedError`` naming the offending value — the symmetric
        contract to ``test_corrupt_worker_metadata_raises_cache_corrupted``."""
        from acheron.core.errors import CacheCorruptedError
        from acheron.shell.stores.redis import _WORKER_KEY, _serialize_capabilities

        r = aioredis.Redis.from_url(redis_url)
        await r.hset(  # type: ignore[misc]
            _WORKER_KEY.format(worker_id="w-bad-status"),
            mapping={
                "metadata_json": "{}",
                "capabilities_json": _serialize_capabilities(_tts_caps()),
                "status": "garbage",
                "endpoint": "http://h",
                "transport": "http",
            },
        )
        await r.aclose()
        with pytest.raises(CacheCorruptedError, match="invalid status: garbage"):
            await store.get("w-bad-status")

    @pytest.mark.asyncio
    async def test_list_all_invalid_worker_status_is_not_store_error(
        self, store: RedisWorkerStore, redis_url: str
    ) -> None:
        """Malformed worker hashes must preserve deserialization errors through list_all."""
        from acheron.core.errors import CacheCorruptedError
        from acheron.shell.stores.redis import _WORKER_KEY, _WORKERS_SET, _serialize_capabilities

        r = aioredis.Redis.from_url(redis_url)
        await r.hset(  # type: ignore[misc]
            _WORKER_KEY.format(worker_id="w-list-bad-status"),
            mapping={
                "metadata_json": "{}",
                "capabilities_json": _serialize_capabilities(_tts_caps()),
                "status": "garbage",
                "endpoint": "http://h",
                "transport": "http",
            },
        )
        await r.sadd(_WORKERS_SET, "w-list-bad-status")  # type: ignore[misc]
        await r.aclose()

        with pytest.raises(CacheCorruptedError, match="invalid status: garbage") as exc_info:
            await store.list_all()
        assert not isinstance(exc_info.value, StoreError)

    @pytest.mark.asyncio
    async def test_booting_without_timestamp_raises_chained_cache_corrupted(
        self, store: RedisWorkerStore, redis_url: str
    ) -> None:
        from acheron.core.errors import CacheCorruptedError
        from acheron.shell.stores.redis import _WORKER_KEY, _serialize_capabilities

        r = cast("_RedisAwaitable", aioredis.Redis.from_url(redis_url, decode_responses=True))
        await r.hset(
            _WORKER_KEY.format(worker_id="w-missing-booting-since"),
            mapping={
                "metadata_json": "{}",
                "capabilities_json": _serialize_capabilities(_tts_caps()),
                "status": WorkerStatus.BOOTING.value,
                "endpoint": "http://h",
                "transport": "http",
            },
        )
        await r.aclose()
        with pytest.raises(CacheCorruptedError, match="missing booting_since") as exc_info:
            await store.get("w-missing-booting-since")
        assert exc_info.value.__cause__ is not None

    @pytest.mark.asyncio
    async def test_missing_worker_status_defaults_to_healthy(self, store: RedisWorkerStore, redis_url: str) -> None:
        """A worker blob with no ``status`` field defaults to ``HEALTHY`` — the
        deserializer uses ``fields.get("status") or HEALTHY.value`` as a tolerant
        fallback for legacy blobs written before the ``status`` field existed.
        A regression to strict-required would silently break old deployments."""
        from acheron.core.models import WorkerStatus
        from acheron.shell.stores.redis import _WORKER_KEY, _serialize_capabilities

        r = aioredis.Redis.from_url(redis_url)
        await r.hset(  # type: ignore[misc]
            _WORKER_KEY.format(worker_id="w-no-status"),
            mapping={
                "metadata_json": "{}",
                "capabilities_json": _serialize_capabilities(_tts_caps()),
                "endpoint": "http://h",
                "transport": "http",
            },
        )
        await r.aclose()
        w = await store.get("w-no-status")
        assert w is not None
        assert w.status == WorkerStatus.HEALTHY


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
    async def test_success_resets_counter_and_preserves_history(self, store: RedisWorkerStore) -> None:
        await store.register("w-1", "http://a", "http", _tts_caps())
        worker = await store.get("w-1")
        assert worker is not None
        await store.record_health_failure("w-1", generation=worker.registration_generation, error="connection refused")
        await store.record_health_failure("w-1", generation=worker.registration_generation, error="Bearer secret")
        await store.record_health_success("w-1", generation=worker.registration_generation)
        w = await store.get("w-1")
        assert w is not None
        assert w.consecutive_failures == 0
        assert w.last_error is None
        assert len(w.error_history) == 2
        assert "secret" not in w.error_history[-1].message

    @pytest.mark.asyncio
    async def test_removal_tombstone_preserves_history_for_reregistration(self, store: RedisWorkerStore) -> None:
        await store.register("w-tombstone", "http://a", "http", _tts_caps())
        worker = await store.get("w-tombstone")
        assert worker is not None
        removed = False
        for _ in range(3):
            removed = await store.record_health_failure(
                "w-tombstone", generation=worker.registration_generation, error="down"
            )
        assert removed
        await store.register("w-tombstone", "http://b", "http", _tts_caps())
        restored = await store.get("w-tombstone")
        assert restored is not None
        assert restored.registration_generation == worker.registration_generation + 1
        assert restored.consecutive_failures == 0
        assert len(restored.error_history) == 3


class TestConcurrentStatusTransitions:
    @pytest.mark.asyncio
    async def test_concurrent_transitions_preserve_timestamp_invariant(
        self, store: RedisWorkerStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await store.register("w-concurrent", "http://a", "http", _tts_caps())
        clock = iter(float(value) for value in range(100, 120))
        monkeypatch.setattr("acheron.shell.stores.redis.time.time", lambda: next(clock))
        statuses = [
            WorkerStatus.BOOTING,
            WorkerStatus.OFFLINE,
            WorkerStatus.BOOTING,
            WorkerStatus.HEALTHY,
        ] * 5
        await asyncio.gather(*(store.set_worker_status("w-concurrent", status, None) for status in statuses))
        worker = await store.get("w-concurrent")
        assert worker is not None
        if worker.status == WorkerStatus.BOOTING:
            assert worker.booting_since is not None
        else:
            assert worker.booting_since is None


class TestFailFast:
    @pytest.mark.asyncio
    async def test_unreachable_redis_raises_on_connect(self) -> None:
        store = RedisWorkerStore("redis://localhost:1")
        with pytest.raises((RedisConnectionError, redis.RedisError)):
            await store.connect()


class TestFailureNormalization:
    @pytest.mark.asyncio
    async def test_list_all_chains_redis_error_as_store_error(self) -> None:
        class _FailingPipeline:
            async def __aenter__(self) -> Self:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            def hgetall(self, *_args: object) -> _FailingPipeline:
                return self

            async def execute(self) -> list[object]:
                raise redis.RedisError("worker registry unavailable")

        class _FailingRedis:
            async def smembers(self, _name: str) -> set[str]:
                return {"w-1"}

            def pipeline(self, **_kwargs: object) -> _FailingPipeline:
                return _FailingPipeline()

        store = object.__new__(RedisWorkerStore)
        store._redis = cast("_RedisAwaitable", _FailingRedis())  # noqa: SLF001

        with pytest.raises(StoreError, match="Failed to list workers") as exc_info:
            await store.list_all()

        assert isinstance(exc_info.value.__cause__, redis.RedisError)
        assert str(exc_info.value) == "Failed to list workers"


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
    async def test_set_worker_status_round_trips(
        self, store: RedisWorkerStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await store.register("w-1", "http://a", "http", _tts_caps())
        monkeypatch.setattr("acheron.shell.stores.redis.time.time", lambda: 100.0)
        await store.set_worker_status("w-1", WorkerStatus.BOOTING, "cold start")
        w = await store.get("w-1")
        assert w is not None
        assert w.status == WorkerStatus.BOOTING
        assert w.last_error == "cold start"
        assert w.booting_since == 100.0

        monkeypatch.setattr("acheron.shell.stores.redis.time.time", lambda: 200.0)
        await store.set_worker_status("w-1", WorkerStatus.BOOTING, "still starting")
        w = await store.get("w-1")
        assert w is not None
        assert w.booting_since == 100.0
        assert w.last_error == "still starting"

        await store.set_worker_status("w-1", WorkerStatus.HEALTHY, None)
        w = await store.get("w-1")
        assert w is not None
        assert w.booting_since is None
        await store.set_worker_status("w-1", WorkerStatus.OFFLINE, "down")
        w = await store.get("w-1")
        assert w is not None
        assert w.booting_since is None

    @pytest.mark.asyncio
    async def test_set_worker_status_nonexistent_is_noop(self, store: RedisWorkerStore) -> None:
        await store.set_worker_status("nope", WorkerStatus.OFFLINE, "err")

    @pytest.mark.asyncio
    async def test_record_health_success_resets_status_and_error(self, store: RedisWorkerStore) -> None:
        await store.register("w-1", "http://a", "http", _tts_caps())
        await store.set_worker_status("w-1", WorkerStatus.BOOTING, "cold")
        await store.record_health_success("w-1")
        w = await store.get("w-1")
        assert w is not None
        assert w.status == WorkerStatus.HEALTHY
        assert w.last_error is None
        assert w.booting_since is None

    @pytest.mark.asyncio
    async def test_new_worker_defaults_to_healthy(self, store: RedisWorkerStore) -> None:
        await store.register("w-1", "http://a", "http", _tts_caps())
        w = await store.get("w-1")
        assert w is not None
        assert w.status == WorkerStatus.HEALTHY
        assert w.last_error is None


class TestProtocolEnforcement:
    """TYPE-013: Redis Protocol surfaces are checked at construction."""

    def test_init_raises_when_client_misses_protocol_methods(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _StubClient:
            async def ping(self) -> bool:
                return True

        monkeypatch.setattr(aioredis.Redis, "from_url", lambda _url, **_kw: _StubClient())
        with pytest.raises(TypeError, match="smembers"):
            RedisWorkerStore("redis://localhost:6379")

    def test_init_raises_when_protocol_member_is_not_callable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _StubClient:
            ping = None

        monkeypatch.setattr(aioredis.Redis, "from_url", lambda _url, **_kw: _StubClient())
        with pytest.raises(TypeError, match="ping"):
            RedisWorkerStore("redis://localhost:6379")

    @staticmethod
    def _client() -> MagicMock:
        client = MagicMock()
        for name in ("ping", "aclose", "hgetall", "smembers", "hincrby", "hset", "exists", "get", "eval"):
            setattr(client, name, AsyncMock())
        pipeline = MagicMock()
        pipeline.__aenter__ = AsyncMock(return_value=pipeline)
        pipeline.__aexit__ = AsyncMock(return_value=None)
        pipeline.execute = AsyncMock(return_value=[])
        client.pipeline.return_value = pipeline
        return client

    def test_init_rejects_synchronous_redis_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = self._client()
        client.ping = lambda: True
        monkeypatch.setattr(aioredis.Redis, "from_url", lambda _url, **_kw: client)

        with pytest.raises(TypeError, match="ping"):
            RedisWorkerStore("redis://localhost:6379")

    def test_init_rejects_sync_command_without_invoking_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = self._client()
        ping = MagicMock(side_effect=AssertionError("sync command was invoked"))
        client.ping = ping
        monkeypatch.setattr(aioredis.Redis, "from_url", lambda _url, **_kw: client)

        with pytest.raises(TypeError, match="ping"):
            RedisWorkerStore("redis://localhost:6379")

        ping.assert_not_called()

    def test_init_rejects_synchronous_pipeline_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = self._client()
        execute = MagicMock(side_effect=AssertionError("sync command was invoked"))
        client.pipeline.return_value.execute = execute
        monkeypatch.setattr(aioredis.Redis, "from_url", lambda _url, **_kw: client)

        with pytest.raises(TypeError, match="execute"):
            RedisWorkerStore("redis://localhost:6379")

        execute.assert_not_called()

    def test_init_accepts_async_redis_surface(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = self._client()
        monkeypatch.setattr(aioredis.Redis, "from_url", lambda _url, **_kw: client)

        RedisWorkerStore("redis://localhost:6379")
