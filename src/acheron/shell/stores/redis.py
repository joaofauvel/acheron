"""Redis-backed implementations of the store ABCs."""

# redis.asyncio stubs type methods as ``Awaitable[T] | T``; we silence the
# misc for each ``await self._redis.<method>`` since the ``T`` branch is
# unreachable in async call sites.

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import redis.asyncio

from acheron.core.models import AudioRequest, EpubRequest, WorkerStatus
from acheron.shell.stores.base import JobStore, WorkerStore

if TYPE_CHECKING:
    from acheron.core.models import JsonValue, WorkerCapabilities, WorkerType
    from acheron.shell.job_store import TrackedJob
    from acheron.shell.registry import RegisteredWorker


_WORKER_KEY = "worker:{worker_id}"
_WORKERS_SET = "workers"
_JOB_KEY = "job:{job_id}"
_JOBS_SET = "jobs"


def _serialize_capabilities(cap: WorkerCapabilities) -> str:
    return json.dumps(
        {
            "worker_type": cap.worker_type.value,
            "supported_languages_in": sorted(cap.supported_languages_in),
            "supported_languages_out": sorted(cap.supported_languages_out),
            "supported_formats_in": sorted(cap.supported_formats_in),
            "supported_formats_out": sorted(cap.supported_formats_out),
            "max_payload_bytes": cap.max_payload_bytes,
            "batch_capable": cap.batch_capable,
            "model_source": cap.model_source,
            "metadata": cap.metadata,
        },
        sort_keys=True,
    )


def _deserialize_capabilities(blob: str) -> WorkerCapabilities:
    from acheron.core.errors import CacheCorruptedError  # noqa: PLC0415
    from acheron.core.models import WorkerCapabilities, WorkerType  # noqa: PLC0415

    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        msg = f"Capabilities blob is not valid JSON: {exc}"
        raise CacheCorruptedError(msg) from exc
    try:
        return WorkerCapabilities(
            worker_type=WorkerType(data["worker_type"]),
            supported_languages_in=frozenset(data["supported_languages_in"]),
            supported_languages_out=frozenset(data["supported_languages_out"]),
            supported_formats_in=frozenset(data["supported_formats_in"]),
            supported_formats_out=frozenset(data["supported_formats_out"]),
            max_payload_bytes=data["max_payload_bytes"],
            batch_capable=data["batch_capable"],
            model_source=data["model_source"],
            metadata=data["metadata"],
        )
    except (KeyError, ValueError) as exc:
        msg = f"Capabilities blob is missing or has invalid fields: {exc}"
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
        "metadata_json": json.dumps(metadata, sort_keys=True),
        "status": WorkerStatus.HEALTHY.value,
        "last_error": "",
    }


def _deserialize_worker(worker_id: str, fields: dict[str, str]) -> RegisteredWorker:
    from acheron.core.errors import CacheCorruptedError  # noqa: PLC0415
    from acheron.shell.registry import RegisteredWorker  # noqa: PLC0415

    last_hc = fields.get("last_health_check") or ""
    try:
        metadata = json.loads(fields.get("metadata_json", "{}"))
    except json.JSONDecodeError as exc:
        msg = f"Worker {worker_id} metadata is not valid JSON: {exc}"
        raise CacheCorruptedError(msg) from exc
    status_str = fields.get("status") or WorkerStatus.HEALTHY.value
    try:
        status = WorkerStatus(status_str)
    except ValueError as exc:
        msg = f"Worker {worker_id} has invalid status: {status_str}"
        raise CacheCorruptedError(msg) from exc
    last_error = fields.get("last_error") or None
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
    )


def _serialize_job(job: TrackedJob) -> str:
    from acheron.core.models import AudioRequest  # noqa: PLC0415

    plan_dict = None
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
                }
                for o in job.result.outputs
            ],
            "total_cost": job.result.total_cost,
            "total_duration_seconds": job.result.total_duration_seconds,
            "errors": list(job.result.errors),
            "total_cost_basis": (job.result.total_cost_basis.value if job.result.total_cost_basis else None),
        }

    return json.dumps(
        {
            "job_id": job.job_id,
            "source_type": source_type,
            "request": request_dict,
            "strategy": job.strategy.value,
            "status": job.status.value,
            "plan": plan_dict,
            "result": result_dict,
        },
        sort_keys=True,
    )


def _deserialize_job(blob: str) -> TrackedJob:
    from acheron.core.errors import CacheCorruptedError  # noqa: PLC0415
    from acheron.core.models import (  # noqa: PLC0415
        AudioRequest,
        EpubRequest,
        ExecutorStrategy,
        Plan,
        PlanStatus,
        PlanStep,
        StepStatus,
        WorkerType,
    )
    from acheron.shell.job_store import TrackedJob  # noqa: PLC0415

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
    result = None
    if data.get("result") is not None:
        from acheron.core.models import CostBasis, OutputFile, PlanResult  # noqa: PLC0415

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
                )
                for o in rd["outputs"]
            ),
            total_cost=rd["total_cost"],
            total_duration_seconds=rd["total_duration_seconds"],
            errors=tuple(rd["errors"]),
            total_cost_basis=CostBasis(basis_value) if basis_value else None,
        )

    return TrackedJob(
        job_id=data["job_id"],
        request=request,
        strategy=ExecutorStrategy(data["strategy"]),
        plan=plan,
        result=result,
        status=PlanStatus(data["status"]),
    )


class RedisWorkerStore(WorkerStore):
    """Redis-backed worker store. Survives orchestrator restarts.

    Requires awaiting connect() before use.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis = redis.asyncio.Redis.from_url(redis_url, decode_responses=True)

    async def connect(self) -> None:
        """Verify the Redis server is reachable. Idempotent."""
        await self._redis.ping()  # type: ignore[misc]

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
        fields: dict[str, str] = await self._redis.hgetall(_WORKER_KEY.format(worker_id=worker_id))  # type: ignore[misc]
        if not fields:
            return None
        return _deserialize_worker(worker_id, fields)

    async def list_all(self) -> tuple[RegisteredWorker, ...]:
        """Return all registered workers."""
        ids: set[str] = await self._redis.smembers(_WORKERS_SET)  # type: ignore[misc]
        if not ids:
            return ()
        async with self._redis.pipeline(transaction=False) as pipe:
            for wid in ids:
                pipe.hgetall(_WORKER_KEY.format(worker_id=wid))
            results = await pipe.execute()
        return tuple(_deserialize_worker(wid, fields) for wid, fields in zip(ids, results, strict=True) if fields)

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
        new_count: int = await self._redis.hincrby(key, "consecutive_failures", 1)  # type: ignore[misc]
        await self._redis.hset(key, "last_health_check", str(time.time()))  # type: ignore[misc]
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
            await pipe.execute()

    async def set_worker_status(
        self,
        worker_id: str,
        status: WorkerStatus,
        last_error: str | None,
    ) -> None:
        """Update the worker's status and last_error without touching the failure counter."""
        key = _WORKER_KEY.format(worker_id=worker_id)
        if not await self._redis.exists(key):
            return
        await self._redis.hset(  # type: ignore[misc]
            key,
            mapping={"status": status.value, "last_error": last_error or ""},
        )


class RedisJobStore(JobStore):
    """Redis-backed job store. Survives orchestrator restarts.

    Requires awaiting connect() before use.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis = redis.asyncio.Redis.from_url(redis_url, decode_responses=True)

    async def connect(self) -> None:
        """Verify the Redis server is reachable. Idempotent."""
        await self._redis.ping()  # type: ignore[misc]

    async def close(self) -> None:
        """Close the underlying Redis connection pool."""
        await self._redis.aclose()

    async def put(self, job: TrackedJob) -> None:
        """Store or update a tracked job."""
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.set(_JOB_KEY.format(job_id=job.job_id), _serialize_job(job))
            pipe.sadd(_JOBS_SET, job.job_id)
            await pipe.execute()

    async def get(self, job_id: str) -> TrackedJob | None:
        """Retrieve a tracked job by ID."""
        blob: str | None = await self._redis.get(_JOB_KEY.format(job_id=job_id))
        if blob is None:
            return None
        return _deserialize_job(blob)

    async def list_all(self) -> tuple[TrackedJob, ...]:
        """Return all tracked jobs."""
        ids: set[str] = await self._redis.smembers(_JOBS_SET)  # type: ignore[misc]
        if not ids:
            return ()
        async with self._redis.pipeline(transaction=False) as pipe:
            for jid in ids:
                pipe.get(_JOB_KEY.format(job_id=jid))
            results = await pipe.execute()
        return tuple(_deserialize_job(blob) for blob in results if blob is not None)
