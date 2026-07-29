"""Tests for the bounded per-job progress event broker."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from acheron.core.models import PlanStatus
from acheron.core.schemas import JobLogEvent, JobProgress
from acheron.shell.job_events import JobEventBroker, iter_events


def _event(job_id: str, status: str, step_id: str | None) -> JobLogEvent:
    return JobLogEvent(
        job_id=job_id,
        timestamp=datetime.now(tz=UTC),
        status=PlanStatus(status),
        step_id=step_id,
        progress=JobProgress(),
        message=f"step {step_id} {status}",
    )


class TestJobEventBroker:
    @pytest.mark.asyncio
    async def test_subscriber_receives_snapshot_and_terminal(self) -> None:
        broker = JobEventBroker(max_events=8)
        await broker.publish(_event("job-1", "running", "step-1"))
        stream = broker.subscribe("job-1")
        await broker.publish(_event("job-1", "completed", None))
        await broker.finish("job-1")

        statuses = [item.status async for item in iter_events(stream)]
        assert statuses == [PlanStatus.RUNNING, PlanStatus.COMPLETED]

    @pytest.mark.asyncio
    async def test_bounded_buffer_drops_old_events(self) -> None:
        broker = JobEventBroker(max_events=3)
        for i in range(5):
            await broker.publish(_event("job-1", "running", f"step-{i}"))
        stream = broker.subscribe("job-1")
        await broker.finish("job-1")

        events = [item async for item in iter_events(stream)]
        # Only the last 3 should be retained
        assert len(events) == 3
        assert events[0].step_id == "step-2"
        assert events[1].step_id == "step-3"
        assert events[2].step_id == "step-4"

    @pytest.mark.asyncio
    async def test_unknown_job_subscribe_yields_nothing(self) -> None:
        broker = JobEventBroker(max_events=8)
        stream = broker.subscribe("unknown")
        await broker.finish("unknown")
        events = [item async for item in iter_events(stream)]
        assert events == []

    @pytest.mark.asyncio
    async def test_multiple_subscribers_independent(self) -> None:
        broker = JobEventBroker(max_events=8)
        await broker.publish(_event("job-1", "running", "step-1"))
        s1 = broker.subscribe("job-1")
        await broker.publish(_event("job-1", "running", "step-2"))
        s2 = broker.subscribe("job-1")
        await broker.finish("job-1")

        e1 = [item async for item in iter_events(s1)]
        e2 = [item async for item in iter_events(s2)]
        # s1 joined before step-2, so it gets step-1 + step-2 (sentinel ends iteration)
        assert len(e1) == 2
        # s2 joined after step-2 but buffer has both; subscriber gets full buffer
        assert len(e2) == 2

    @pytest.mark.asyncio
    async def test_publish_after_finish_noop(self) -> None:
        broker = JobEventBroker(max_events=8)
        await broker.publish(_event("job-1", "running", "step-1"))
        stream = broker.subscribe("job-1")
        await broker.finish("job-1")
        # Publish after finish should not crash and subscriber is already removed
        await broker.publish(_event("job-1", "completed", None))
        events = [item async for item in iter_events(stream)]
        assert len(events) == 1  # Only the pre-finish event
        assert events[0].status == PlanStatus.RUNNING

    @pytest.mark.asyncio
    async def test_empty_subscribe_gets_terminal_only(self) -> None:
        broker = JobEventBroker(max_events=8)
        stream = broker.subscribe("job-1")
        await broker.publish(_event("job-1", "completed", None))
        await broker.finish("job-1")
        events = [item async for item in iter_events(stream)]
        assert len(events) == 1
        assert events[0].status == PlanStatus.COMPLETED
