"""Bounded per-job progress event broker for live monitoring."""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from acheron.core.schemas import JobLogEvent

_SENTINEL = object()


class JobEventBroker:
    """Publish-subscribe broker for per-job progress events."""

    def __init__(self, *, max_events: int = 128, max_terminal_jobs: int = 128) -> None:
        if max_events < 1:
            raise ValueError("max_events must be positive")
        if max_terminal_jobs < 1:
            raise ValueError("max_terminal_jobs must be positive")
        self._max_events = max_events
        self._max_terminal_jobs = max_terminal_jobs
        self._buffer: dict[str, deque[JobLogEvent]] = {}
        self._subscribers: dict[str, list[asyncio.Queue[object]]] = {}
        self._terminal: OrderedDict[str, deque[JobLogEvent]] = OrderedDict()
        self._active_jobs: set[str] = set()
        self._lock = asyncio.Lock()

    def _enqueue(self, queue: asyncio.Queue[object], item: object) -> None:
        if item is not _SENTINEL and queue.qsize() >= self._max_events:
            queue.get_nowait()
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(item)

    def _remember_terminal(self, job_id: str, buffer: deque[JobLogEvent] | None) -> None:
        self._terminal[job_id] = deque(buffer or ())
        self._terminal.move_to_end(job_id)
        while len(self._terminal) > self._max_terminal_jobs:
            self._terminal.popitem(last=False)

    async def start(self, job_id: str) -> tuple[JobLogEvent, ...] | None:
        """Reset broker state before a new execution starts."""
        async with self._lock:
            prior_terminal = self._terminal.get(job_id)
            prior_events = tuple(prior_terminal) if prior_terminal is not None else None
            for queue in self._subscribers.pop(job_id, ()):
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(_SENTINEL)
            self._buffer.pop(job_id, None)
            self._terminal.pop(job_id, None)
            self._active_jobs.add(job_id)
            return prior_events

    async def restore(self, job_id: str, terminal_events: tuple[JobLogEvent, ...] | None) -> None:
        """Restore terminal replay state after a failed execution start."""
        async with self._lock:
            for queue in self._subscribers.pop(job_id, ()):
                while not queue.empty():
                    queue.get_nowait()
                for event in terminal_events or ():
                    self._enqueue(queue, event)
                self._enqueue(queue, _SENTINEL)
            self._buffer.pop(job_id, None)
            self._terminal.pop(job_id, None)
            self._active_jobs.discard(job_id)
            if terminal_events is not None:
                self._remember_terminal(job_id, deque(terminal_events))

    async def publish(self, event: JobLogEvent) -> None:
        """Store the event and fan it out to active subscribers."""
        async with self._lock:
            if event.job_id in self._terminal:
                return
            self._active_jobs.add(event.job_id)
            buf = self._buffer.setdefault(event.job_id, deque(maxlen=self._max_events))
            buf.append(event)
            for queue in self._subscribers.get(event.job_id, ()):
                self._enqueue(queue, event)

    async def subscribe(self, job_id: str) -> asyncio.Queue[object]:
        """Return a bounded queue that receives buffered and live events."""
        queue: asyncio.Queue[object] = asyncio.Queue(maxsize=self._max_events + 1)
        async with self._lock:
            terminal = self._terminal.get(job_id)
            if terminal is not None:
                self._terminal.move_to_end(job_id)
                for event in terminal:
                    self._enqueue(queue, event)
                self._enqueue(queue, _SENTINEL)
                return queue
            if job_id not in self._active_jobs:
                self._enqueue(queue, _SENTINEL)
                return queue

            for event in self._buffer.get(job_id, ()):
                self._enqueue(queue, event)
            self._subscribers.setdefault(job_id, []).append(queue)
        return queue

    async def unsubscribe(self, job_id: str, queue: asyncio.Queue[object]) -> None:
        """Remove a subscriber that no longer consumes its event stream."""
        async with self._lock:
            subscribers = self._subscribers.get(job_id)
            if subscribers is None:
                return
            remaining = [item for item in subscribers if item is not queue]
            if remaining:
                self._subscribers[job_id] = remaining
            else:
                self._subscribers.pop(job_id)

    async def finish(self, job_id: str) -> None:
        """Terminate subscribers and reclaim active job state."""
        async with self._lock:
            if job_id in self._terminal:
                return
            buffer = self._buffer.pop(job_id, None)
            subscribers = self._subscribers.pop(job_id, ())
            for queue in subscribers:
                self._enqueue(queue, _SENTINEL)
            self._active_jobs.discard(job_id)
            self._remember_terminal(job_id, buffer)


async def iter_events(queue: asyncio.Queue[object]) -> AsyncIterator[JobLogEvent]:
    """Yield :class:`JobLogEvent` items from *queue* until the finish sentinel."""
    from acheron.core.schemas import JobLogEvent as _JobLogEvent  # noqa: PLC0415

    while True:
        item = await queue.get()
        if item is _SENTINEL:
            return
        if isinstance(item, _JobLogEvent):
            yield item
