"""Redis-backed implementations of the store ABCs."""

from __future__ import annotations

import inspect
import json
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, Self, cast, runtime_checkable

import redis.asyncio
from pydantic import TypeAdapter
from redis.exceptions import RedisError

from acheron.core.models import (
    AudioRequest,
    EpubRequest,
    JsonValue,
    WorkerCapabilities,
    WorkerStatus,
)
from acheron.shell.stores.base import JobStore, StoreError, WorkerStore

if TYPE_CHECKING:
    from types import TracebackType

    from acheron.core.models import WorkerType
    from acheron.shell.job_store import TrackedJob
    from acheron.shell.registry import RegisteredWorker


@runtime_checkable
class _RedisPipelineAwaitable(Protocol):
    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    async def execute(self) -> list[object]: ...

    def delete(self, *args: object, **kwargs: object) -> _RedisPipelineAwaitable: ...
    def get(self, *args: object, **kwargs: object) -> _RedisPipelineAwaitable: ...
    def hgetall(self, *args: object, **kwargs: object) -> _RedisPipelineAwaitable: ...
    def hset(self, *args: object, **kwargs: object) -> _RedisPipelineAwaitable: ...
    def sadd(self, *args: object, **kwargs: object) -> _RedisPipelineAwaitable: ...
    def set(self, *args: object, **kwargs: object) -> _RedisPipelineAwaitable: ...
    def srem(self, *args: object, **kwargs: object) -> _RedisPipelineAwaitable: ...


@runtime_checkable
class _RedisAwaitable(Protocol):
    """Subset of redis.asyncio.Redis that the stores actually use.

    The actual redis.asyncio stubs type each method as ``Awaitable[T] | T``,
    which forces a ``# type: ignore[misc]`` at every async call site even
    though the ``T`` branch is unreachable. Typing ``self._redis`` as this
    Protocol lets mypy trust the Protocol's signatures at the call sites
    and removes the per-call markers.

    Marked ``@runtime_checkable`` so stores can verify the surface they
    rely on is present on the concrete client at construction time.
    """

    async def ping(self) -> bool: ...
    async def aclose(self) -> None: ...
    async def hgetall(self, name: str) -> dict[str, str]: ...
    async def smembers(self, name: str) -> set[str]: ...
    async def hincrby(self, name: str, key: str, amount: int) -> int: ...
    async def hset(
        self,
        name: str,
        key: str | None = None,
        value: str | None = None,
        mapping: dict[str, str] | None = None,
    ) -> int | None: ...
    async def exists(self, name: str) -> bool: ...
    async def get(self, name: str) -> str | None: ...
    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> object: ...
    def pipeline(self, transaction: bool = True) -> _RedisPipelineAwaitable: ...  # noqa: FBT001, FBT002


def _protocol_method_names(protocol: type[object], *, include_private: bool = False) -> tuple[str, ...]:
    return tuple(
        name
        for name, member in vars(protocol).items()
        if callable(member) and (include_private or not name.startswith("_"))
    )


def _missing_protocol_members(value: object, protocol: type[object], *, include_private: bool = False) -> list[str]:
    return [
        name
        for name in _protocol_method_names(protocol, include_private=include_private)
        if not callable(getattr(value, name, None))
    ]


def _missing_awaitable_members(
    value: object,
    probes: tuple[_AwaitableProbe, ...],
) -> list[str]:
    missing: list[str] = []
    for name, args, kwargs in probes:
        method = getattr(value, name, None)
        if not callable(method):
            missing.append(name)
            continue
        if not inspect.iscoroutinefunction(method) and not type(value).__module__.startswith("redis."):
            missing.append(name)
            continue
        try:
            result = method(*args, **kwargs)
        except AttributeError, TypeError:
            missing.append(name)
            continue
        if not inspect.isawaitable(result):
            missing.append(name)
        elif inspect.iscoroutine(result):
            result.close()
    return missing


type _AwaitableProbe = tuple[str, tuple[object, ...], dict[str, object]]


_REDIS_AWAITABLE_PROBES: tuple[_AwaitableProbe, ...] = (
    ("ping", (), {}),
    ("aclose", (), {}),
    ("hgetall", ("name",), {}),
    ("smembers", ("name",), {}),
    ("hincrby", ("name", "key", 1), {}),
    ("hset", ("name",), {"key": "key", "value": "value"}),
    ("exists", ("name",), {}),
    ("get", ("name",), {}),
    ("eval", ("return 1", 0), {}),
)

_PIPELINE_AWAITABLE_PROBES: tuple[_AwaitableProbe, ...] = (
    ("__aenter__", (), {}),
    ("__aexit__", (None, None, None), {}),
    ("execute", (), {}),
)


_WORKER_KEY = "worker:{worker_id}"
_WORKERS_SET = "workers"
_JOB_KEY = "job:{job_id}"
_JOBS_SET = "jobs"

_SET_WORKER_STATUS_SCRIPT = """
local key = KEYS[1]
if redis.call("EXISTS", key) == 0 then
  return 0
end
local current_status = redis.call("HGET", key, "status") or "healthy"
local current_since = redis.call("HGET", key, "booting_since") or ""
local next_since = ""
if ARGV[1] == "booting" then
  if current_status == "booting" and current_since ~= "" then
    next_since = current_since
  else
    next_since = ARGV[3]
  end
end
redis.call("HSET", key,
  "status", ARGV[1],
  "last_error", ARGV[2],
  "booting_since", next_since)
return 1
"""

_capabilities_adapter: TypeAdapter[WorkerCapabilities] = TypeAdapter(WorkerCapabilities)
_metadata_adapter: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])


def _checked_redis_client(redis_url: str) -> _RedisAwaitable:
    """Build a Redis client and verify its store-facing Protocol surfaces.

    Raises:
        TypeError: If the client is missing any declared method (e.g. after a
            redis-py rename). Lists the missing attribute names.
    """
    client: object = redis.asyncio.Redis.from_url(redis_url, decode_responses=True)
    missing = _missing_protocol_members(client, _RedisAwaitable)
    if missing:
        msg = f"redis.asyncio.Redis no longer satisfies the _RedisAwaitable surface; missing: {missing}"
        raise TypeError(msg)
    missing_awaitable = _missing_awaitable_members(client, _REDIS_AWAITABLE_PROBES)
    if missing_awaitable:
        msg = f"redis.asyncio.Redis no longer provides awaitable methods; invalid: {missing_awaitable}"
        raise TypeError(msg)
    typed_client = cast("_RedisAwaitable", client)
    pipeline = typed_client.pipeline(transaction=True)
    missing_pipeline = _missing_protocol_members(pipeline, _RedisPipelineAwaitable, include_private=True)
    if missing_pipeline:
        msg = f"redis.asyncio.Redis no longer satisfies the pipeline Protocol surface; missing: {missing_pipeline}"
        raise TypeError(msg)
    missing_pipeline_awaitable = _missing_awaitable_members(pipeline, _PIPELINE_AWAITABLE_PROBES)
    if missing_pipeline_awaitable:
        msg = f"redis pipeline no longer provides awaitable methods; invalid: {missing_pipeline_awaitable}"
        raise TypeError(msg)
    return typed_client


def _serialize_capabilities(cap: WorkerCapabilities) -> str:
    return _capabilities_adapter.dump_json(cap).decode("utf-8")


def _deserialize_capabilities(blob: str) -> WorkerCapabilities:
    from acheron.core.errors import CacheCorruptedError  # noqa: PLC0415

    try:
        return _capabilities_adapter.validate_json(blob)
    except (ValueError, TypeError) as exc:
        msg = f"Capabilities blob is missing or has invalid fields: {exc}"
        raise CacheCorruptedError(msg) from exc


def _serialize_metadata(metadata: dict[str, JsonValue]) -> str:
    return _metadata_adapter.dump_json(metadata).decode("utf-8")


def _deserialize_metadata(blob: str) -> dict[str, JsonValue]:
    from acheron.core.errors import CacheCorruptedError  # noqa: PLC0415

    try:
        return _metadata_adapter.validate_json(blob)
    except (ValueError, TypeError) as exc:
        msg = f"metadata is not valid JSON: {exc}"
        raise CacheCorruptedError(msg) from exc


def _worker_fields(
    endpoint: str,
    transport: str,
    capabilities: WorkerCapabilities,
    metadata: dict[str, JsonValue],
) -> dict[str, str]:
    return {
        "endpoint": endpoint,
        "transport": transport,
        "consecutive_failures": "0",
        "last_health_check": str(time.time()),
        "capabilities_json": _serialize_capabilities(capabilities),
        "metadata_json": _serialize_metadata(metadata),
        "status": WorkerStatus.HEALTHY.value,
        "last_error": "",
        "booting_since": "",
    }


def _deserialize_worker(worker_id: str, fields: dict[str, str]) -> RegisteredWorker:
    from acheron.core.errors import CacheCorruptedError  # noqa: PLC0415
    from acheron.shell.registry import RegisteredWorker  # noqa: PLC0415

    last_hc = fields.get("last_health_check") or ""
    metadata_blob = fields.get("metadata_json", "{}")
    metadata = _deserialize_metadata(metadata_blob) if metadata_blob else {}
    status_str = fields.get("status") or WorkerStatus.HEALTHY.value
    try:
        status = WorkerStatus(status_str)
    except ValueError as exc:
        msg = f"Worker {worker_id} has invalid status: {status_str}"
        raise CacheCorruptedError(msg) from exc
    last_error = fields.get("last_error") or None
    booting_since_blob = fields.get("booting_since") or ""
    if status == WorkerStatus.BOOTING and not booting_since_blob:
        msg = f"Worker {worker_id} has missing booting_since for BOOTING status"
        raise CacheCorruptedError(msg) from ValueError("missing booting_since")
    return RegisteredWorker(
        worker_id=worker_id,
        endpoint=fields["endpoint"],
        transport=fields["transport"],
        capabilities=_deserialize_capabilities(fields["capabilities_json"]),
        consecutive_failures=int(fields.get("consecutive_failures", "0")),
        last_health_check=float(last_hc) if last_hc else None,
        metadata=metadata,
        status=status,
        last_error=last_error,
        booting_since=float(booting_since_blob) if booting_since_blob else None,
    )


def _serialize_job(job: TrackedJob) -> str:
    from acheron.core.models import AudioRequest  # noqa: PLC0415

    plan_dict: dict[str, Any] | None = None
    if job.plan is not None:
        plan_dict = {
            "plan_id": job.plan.plan_id,
            "job_id": job.plan.job_id,
            "source_type": job.plan.source_type,
            "source_language": job.plan.source_language,
            "target_language": job.plan.target_language,
            "executor_strategy": job.plan.executor_strategy.value,
            "steps": [
                {
                    "step_id": s.step_id,
                    "type": s.type.value,
                    "depends_on": list(s.depends_on),
                    "status": s.status.value,
                    "payload": s.payload,
                }
                for s in job.plan.steps
            ],
        }
    request_dict: dict[str, Any] = {
        "source_path": job.request.source_path,
        "source_language": job.request.source_language,
        "target_language": job.request.target_language,
    }
    source_type: str
    match job.request:
        case AudioRequest(asr_model=model) if model is not None:
            request_dict["asr_model"] = model
            source_type = "audio"
        case AudioRequest():
            source_type = "audio"
        case EpubRequest():
            source_type = "epub"

    result_dict: dict[str, Any] | None = None
    if job.result is not None:
        result_dict = {
            "plan_id": job.result.plan_id,
            "status": job.result.status.value,
            "completed_steps": job.result.completed_steps,
            "total_steps": job.result.total_steps,
            "outputs": [
                {
                    "path": o.path,
                    "filename": o.filename,
                    "size_bytes": o.size_bytes,
                    "checksum": o.checksum,
                    "content_type": o.content_type,
                    "metadata": dict(o.metadata),
                }
                for o in job.result.outputs
            ],
            "total_cost": job.result.total_cost,
            "total_duration_seconds": job.result.total_duration_seconds,
            "errors": [
                {
                    "step_id": error.step_id,
                    "worker_type": error.worker_type.value if error.worker_type else None,
                    "worker_id": error.worker_id,
                    "message": error.message,
                    "timestamp": error.timestamp.isoformat(),
                }
                for error in job.result.errors
            ],
            "total_cost_basis": (job.result.total_cost_basis.value if job.result.total_cost_basis else None),
        }

    return json.dumps(
        {
            "job_id": job.job_id,
            "source_type": source_type,
            "request": request_dict,
            "strategy": job.strategy.value,
            "status": job.status.value,
            "label": job.label,
            "retries_from": job.retries_from,
            "created_at": job.created_at.isoformat(),
            "last_persisted_at": job.last_persisted_at.isoformat(),
            "progress": {
                "completed_steps": job.progress.completed_steps,
                "total_steps": job.progress.total_steps,
                "current_step_id": job.progress.current_step_id,
                "current_worker_type": (
                    job.progress.current_worker_type.value if job.progress.current_worker_type else None
                ),
                "current_worker_id": job.progress.current_worker_id,
                "eta_seconds": job.progress.eta_seconds,
            },
            "plan": plan_dict,
            "result": result_dict,
        },
        sort_keys=True,
    )


def _deserialize_job(blob: str) -> TrackedJob:
    from acheron.core.errors import CacheCorruptedError  # noqa: PLC0415
    from acheron.core.models import (  # noqa: PLC0415
        AudioRequest,
        CostBasis,
        EpubRequest,
        ExecutorStrategy,
        OutputFile,
        Plan,
        PlanResult,
        PlanStatus,
        PlanStep,
        StepError,
        StepStatus,
        WorkerType,
    )
    from acheron.shell.job_store import JobProgressState, TrackedJob  # noqa: PLC0415

    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        msg = f"Job blob is not valid JSON: {exc}"
        raise CacheCorruptedError(msg) from exc
    if data["source_type"] == "epub":
        request: EpubRequest | AudioRequest = EpubRequest(
            source_path=data["request"]["source_path"],
            source_language=data["request"]["source_language"],
            target_language=data["request"]["target_language"],
        )
    else:
        request = AudioRequest(
            source_path=data["request"]["source_path"],
            source_language=data["request"]["source_language"],
            target_language=data["request"]["target_language"],
            asr_model=data["request"].get("asr_model"),
        )
    plan = None
    if data["plan"] is not None:
        plan = Plan(
            plan_id=data["plan"]["plan_id"],
            job_id=data["plan"]["job_id"],
            source_type=data["plan"]["source_type"],
            source_language=data["plan"]["source_language"],
            target_language=data["plan"]["target_language"],
            executor_strategy=ExecutorStrategy(data["plan"]["executor_strategy"]),
            steps=tuple(
                PlanStep(
                    step_id=s["step_id"],
                    type=WorkerType(s["type"]),
                    depends_on=tuple(s["depends_on"]),
                    status=StepStatus(s["status"]),
                    payload=s["payload"],
                )
                for s in data["plan"]["steps"]
            ),
        )
    result: PlanResult | None = None
    if data.get("result") is not None:
        rd = data["result"]
        basis_value = rd.get("total_cost_basis")
        result = PlanResult(
            plan_id=rd["plan_id"],
            status=PlanStatus(rd["status"]),
            completed_steps=rd["completed_steps"],
            total_steps=rd["total_steps"],
            outputs=tuple(
                OutputFile(
                    path=o["path"],
                    filename=o["filename"],
                    size_bytes=o["size_bytes"],
                    checksum=o["checksum"],
                    content_type=o["content_type"],
                    metadata=o["metadata"] if isinstance(o.get("metadata"), dict) else {},
                )
                for o in rd["outputs"]
            ),
            total_cost=rd["total_cost"],
            total_duration_seconds=rd["total_duration_seconds"],
            errors=tuple(
                StepError(
                    step_id=error["step_id"],
                    worker_type=WorkerType(error["worker_type"]) if error["worker_type"] else None,
                    worker_id=error["worker_id"],
                    message=error["message"],
                    timestamp=datetime.fromisoformat(error["timestamp"]),
                )
                for error in rd["errors"]
            ),
            total_cost_basis=CostBasis(basis_value) if basis_value else None,
        )

    progress_data = data["progress"]
    progress = JobProgressState(
        completed_steps=progress_data["completed_steps"],
        total_steps=progress_data["total_steps"],
        current_step_id=progress_data["current_step_id"],
        current_worker_type=(
            WorkerType(progress_data["current_worker_type"]) if progress_data["current_worker_type"] else None
        ),
        current_worker_id=progress_data["current_worker_id"],
        eta_seconds=progress_data["eta_seconds"],
    )
    return TrackedJob(
        job_id=data["job_id"],
        request=request,
        strategy=ExecutorStrategy(data["strategy"]),
        label=data["label"],
        retries_from=data["retries_from"],
        created_at=datetime.fromisoformat(data["created_at"]),
        last_persisted_at=datetime.fromisoformat(data["last_persisted_at"]),
        progress=progress,
        plan=plan,
        result=result,
        status=PlanStatus(data["status"]),
    )


class RedisWorkerStore(WorkerStore):
    """Redis-backed worker store. Survives orchestrator restarts.

    Requires awaiting connect() before use.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis: _RedisAwaitable = _checked_redis_client(redis_url)

    async def connect(self) -> None:
        """Verify the Redis server is reachable. Idempotent."""
        await self._redis.ping()

    async def close(self) -> None:
        """Close the underlying Redis connection pool."""
        await self._redis.aclose()

    async def register(
        self,
        worker_id: str,
        endpoint: str,
        transport: str,
        capabilities: WorkerCapabilities,
        metadata: dict[str, JsonValue] | None = None,
    ) -> None:
        """Register a new worker or re-register an existing one."""
        fields = _worker_fields(endpoint, transport, capabilities, dict(metadata or {}))
        # Per-command pipe methods buffer synchronously; only execute() awaits.
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.hset(_WORKER_KEY.format(worker_id=worker_id), mapping=fields)
            pipe.sadd(_WORKERS_SET, worker_id)
            await pipe.execute()

    async def unregister(self, worker_id: str) -> None:
        """Remove a worker from the store."""
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.srem(_WORKERS_SET, worker_id)
            pipe.delete(_WORKER_KEY.format(worker_id=worker_id))
            await pipe.execute()

    async def get(self, worker_id: str) -> RegisteredWorker | None:
        """Look up a worker by ID."""
        fields: dict[str, str] = await self._redis.hgetall(_WORKER_KEY.format(worker_id=worker_id))
        if not fields:
            return None
        return _deserialize_worker(worker_id, fields)

    async def list_all(self) -> tuple[RegisteredWorker, ...]:
        """Return all registered workers, sorted by id for deterministic ordering."""
        ids = sorted(await self._redis.smembers(_WORKERS_SET))
        if not ids:
            return ()
        async with self._redis.pipeline(transaction=False) as pipe:
            for wid in ids:
                pipe.hgetall(_WORKER_KEY.format(worker_id=wid))
            results = await pipe.execute()
        worker_results = cast("list[dict[str, str]]", results)
        return tuple(
            _deserialize_worker(wid, fields) for wid, fields in zip(ids, worker_results, strict=True) if fields
        )

    async def find_by_type(self, worker_type: WorkerType) -> tuple[RegisteredWorker, ...]:
        """Find workers matching a given WorkerType."""
        return tuple(w for w in await self.list_all() if w.capabilities.worker_type == worker_type)

    async def find_by_language(self, src: str, dst: str) -> tuple[RegisteredWorker, ...]:
        """Find workers supporting a source→target language pair."""
        workers = await self.list_all()
        return tuple(
            w
            for w in workers
            if src in w.capabilities.supported_languages_in and dst in w.capabilities.supported_languages_out
        )

    async def record_health_failure(self, worker_id: str) -> bool:
        """Record a failed health check. Returns True if the worker was removed."""
        key = _WORKER_KEY.format(worker_id=worker_id)
        if not await self._redis.exists(key):
            return False
        new_count: int = await self._redis.hincrby(key, "consecutive_failures", 1)
        await self._redis.hset(key, "last_health_check", str(time.time()))
        if new_count >= self.max_failures:
            await self.unregister(worker_id)
            return True
        return False

    async def record_health_success(self, worker_id: str) -> None:
        """Record a successful health check, resetting status and clearing last_error."""
        key = _WORKER_KEY.format(worker_id=worker_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.hset(key, "consecutive_failures", "0")
            pipe.hset(key, "last_health_check", str(time.time()))
            pipe.hset(key, "status", WorkerStatus.HEALTHY.value)
            pipe.hset(key, "last_error", "")
            pipe.hset(key, "booting_since", "")
            await pipe.execute()

    async def set_worker_status(
        self,
        worker_id: str,
        status: WorkerStatus,
        last_error: str | None,
    ) -> None:
        """Update the worker's status and last_error without touching the failure counter."""
        key = _WORKER_KEY.format(worker_id=worker_id)
        await self._redis.eval(
            _SET_WORKER_STATUS_SCRIPT,
            1,
            key,
            status.value,
            last_error or "",
            str(time.time()),
        )


class RedisJobStore(JobStore):
    """Redis-backed job store. Survives orchestrator restarts.

    Requires awaiting connect() before use.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis: _RedisAwaitable = _checked_redis_client(redis_url)

    async def connect(self) -> None:
        """Verify the Redis server is reachable. Idempotent."""
        await self._redis.ping()

    async def close(self) -> None:
        """Close the underlying Redis connection pool."""
        await self._redis.aclose()

    async def put(self, job: TrackedJob) -> None:
        """Store or update a tracked job."""
        job.last_persisted_at = datetime.now(UTC)
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.set(_JOB_KEY.format(job_id=job.job_id), _serialize_job(job))
                pipe.sadd(_JOBS_SET, job.job_id)
                await pipe.execute()
        except RedisError as exc:
            msg = f"Failed to persist job {job.job_id}"
            raise StoreError(msg) from exc

    async def get(self, job_id: str) -> TrackedJob | None:
        """Retrieve a tracked job by ID."""
        blob: str | None = await self._redis.get(_JOB_KEY.format(job_id=job_id))
        if blob is None:
            return None
        return _deserialize_job(blob)

    async def list_all(self) -> tuple[TrackedJob, ...]:
        """Return all tracked jobs, sorted by id for deterministic ordering."""
        ids = sorted(await self._redis.smembers(_JOBS_SET))
        if not ids:
            return ()
        async with self._redis.pipeline(transaction=False) as pipe:
            for jid in ids:
                pipe.get(_JOB_KEY.format(job_id=jid))
            results = await pipe.execute()
        job_results = cast("list[str | None]", results)
        return tuple(_deserialize_job(blob) for blob in job_results if blob is not None)
