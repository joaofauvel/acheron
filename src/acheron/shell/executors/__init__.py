"""Executor implementations and factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from acheron.core.models import ExecutorStrategy
from acheron.shell.executors.async_executor import AsyncExecutor
from acheron.shell.executors.sequential import SequentialExecutor
from acheron.shell.executors.streaming import StreamingExecutor

if TYPE_CHECKING:
    from acheron.core.interfaces import Executor
    from acheron.shell.cache import StepCache
    from acheron.shell.executors._utils import StepHandler


def create_executor(
    strategy: ExecutorStrategy,
    handler: StepHandler,
    *,
    step_cache: StepCache | None = None,
) -> Executor:
    """Create an executor instance for the given strategy."""
    match strategy:
        case ExecutorStrategy.SEQUENTIAL:
            return SequentialExecutor(handler)
        case ExecutorStrategy.ASYNC:
            return AsyncExecutor(handler)
        case ExecutorStrategy.STREAMING:
            if step_cache is None:
                msg = "StreamingExecutor requires a step_cache"
                raise ValueError(msg)
            return StreamingExecutor(handler, step_cache)


__all__ = [
    "AsyncExecutor",
    "SequentialExecutor",
    "StreamingExecutor",
    "create_executor",
]
