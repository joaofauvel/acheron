"""Tests for the bounded per-job progress event broker."""

from __future__ import annotations

import asyncio
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
        stream = await broker.subscribe("job-1")
        await broker.publish(_event("job-1", "completed", None))
        await broker.finish("job-1")

        statuses = [item.status async for item in iter_events(stream)]
        assert statuses == [PlanStatus.RUNNING, PlanStatus.COMPLETED]

    @pytest.mark.asyncio
    async def test_bounded_buffer_drops_old_events(self) -> None:
        broker = JobEventBroker(max_events=3)
        for i in range(5):
            await broker.publish(_event("job-1", "running", f"step-{i}"))
        stream = await broker.subscribe("job-1")
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
        stream = await broker.subscribe("unknown")
        await broker.finish("unknown")
        events = [item async for item in iter_events(stream)]
        assert events == []

    @pytest.mark.asyncio
    async def test_multiple_subscribers_independent(self) -> None:
        broker = JobEventBroker(max_events=8)
        await broker.publish(_event("job-1", "running", "step-1"))
        s1 = await broker.subscribe("job-1")
        await broker.publish(_event("job-1", "running", "step-2"))
        s2 = await broker.subscribe("job-1")
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
        stream = await broker.subscribe("job-1")
        await broker.finish("job-1")
        # Publish after finish should not crash and subscriber is already removed
        await broker.publish(_event("job-1", "completed", None))
        events = [item async for item in iter_events(stream)]
        assert len(events) == 1  # Only the pre-finish event
        assert events[0].status == PlanStatus.RUNNING

    @pytest.mark.asyncio
    async def test_empty_subscribe_gets_terminal_only(self) -> None:
        broker = JobEventBroker(max_events=8)
        await broker.start("job-1")
        stream = await broker.subscribe("job-1")
        await broker.publish(_event("job-1", "completed", None))
        await broker.finish("job-1")
        events = [item async for item in iter_events(stream)]
        assert len(events) == 1
        assert events[0].status == PlanStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_late_subscribers_each_get_buffer_and_terminal(self) -> None:
        broker = JobEventBroker(max_events=8)
        await broker.publish(_event("job-1", "running", "step-1"))
        await broker.publish(_event("job-1", "completed", None))
        await broker.finish("job-1")

        first = await broker.subscribe("job-1")
        second = await broker.subscribe("job-1")

        first_events = await asyncio.wait_for(_collect(first), timeout=1.0)
        second_events = await asyncio.wait_for(_collect(second), timeout=1.0)
        expected = [PlanStatus.RUNNING, PlanStatus.COMPLETED]
        assert [event.status for event in first_events] == expected
        assert [event.status for event in second_events] == expected

    @pytest.mark.asyncio
    async def test_disconnected_subscriber_replays_buffer_after_finish(self) -> None:
        broker = JobEventBroker(max_events=3)
        await broker.start("job-1")
        stream = await broker.subscribe("job-1")
        for i in range(20):
            await broker.publish(_event("job-1", "running", f"step-{i}"))

        assert stream.maxsize == 4
        assert stream.qsize() == 3
        await broker.unsubscribe("job-1", stream)
        await broker.publish(_event("job-1", "completed", None))
        await broker.finish("job-1")

        late_stream = await broker.subscribe("job-1")
        events = await asyncio.wait_for(_collect(late_stream), timeout=1.0)
        assert [event.step_id for event in events] == ["step-18", "step-19", None]
        assert broker._buffer == {}  # noqa: SLF001
        assert broker._subscribers == {}  # noqa: SLF001
        assert len(broker._terminal) == 1  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_active_and_late_subscribers_both_replay_finished_buffer(self) -> None:
        broker = JobEventBroker(max_events=8)
        await broker.start("job-1")
        active = await broker.subscribe("job-1")
        await broker.publish(_event("job-1", "running", "step-1"))
        await broker.publish(_event("job-1", "completed", None))
        await broker.finish("job-1")
        late = await broker.subscribe("job-1")

        active_events = await asyncio.wait_for(_collect(active), timeout=1.0)
        late_events = await asyncio.wait_for(_collect(late), timeout=1.0)
        assert [event.status for event in active_events] == [PlanStatus.RUNNING, PlanStatus.COMPLETED]
        assert [event.status for event in late_events] == [PlanStatus.RUNNING, PlanStatus.COMPLETED]

    @pytest.mark.asyncio
    async def test_terminal_registry_is_bounded_without_late_subscribers(self) -> None:
        broker = JobEventBroker(max_events=2, max_terminal_jobs=3)
        for i in range(10):
            await broker.publish(_event(f"job-{i}", "completed", None))
            await broker.finish(f"job-{i}")

        assert len(broker._terminal) == 3  # noqa: SLF001
        assert list(broker._terminal) == ["job-7", "job-8", "job-9"]  # noqa: SLF001
        assert broker._active_jobs == set()  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_evicted_terminal_subscribe_terminates_without_registration(self) -> None:
        broker = JobEventBroker(max_events=2, max_terminal_jobs=1)
        await broker.publish(_event("job-old", "completed", None))
        await broker.finish("job-old")
        await broker.publish(_event("job-new", "completed", None))
        await broker.finish("job-new")

        stream = await broker.subscribe("job-old")
        events = await asyncio.wait_for(_collect(stream), timeout=1.0)

        assert events == []
        assert "job-old" not in broker._subscribers  # noqa: SLF001
        assert broker._active_jobs == set()  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_start_resets_terminal_state_for_reused_job_id(self) -> None:
        broker = JobEventBroker(max_events=8)
        stale = await broker.subscribe("job-1")
        await broker.publish(_event("job-1", "failed", "old"))
        await broker.start("job-1")

        stale_events = await asyncio.wait_for(_collect(stale), timeout=1.0)
        stream = await broker.subscribe("job-1")
        await broker.publish(_event("job-1", "running", "new"))
        await broker.finish("job-1")

        events = await asyncio.wait_for(_collect(stream), timeout=1.0)
        assert stale_events == []
        assert [event.step_id for event in events] == ["new"]
        assert broker._subscribers == {}  # noqa: SLF001


async def _collect(stream: asyncio.Queue[object]) -> list[JobLogEvent]:
    return [event async for event in iter_events(stream)]
