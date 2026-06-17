"""Executor implementations and factory."""

from acheron.core.interfaces import Executor  # noqa: TC001
from acheron.core.models import ExecutorStrategy
from acheron.shell.executors._utils import StepHandler  # noqa: TC001
from acheron.shell.executors.async_executor import AsyncExecutor
from acheron.shell.executors.batch_async import BatchAsyncExecutor
from acheron.shell.executors.sequential import SequentialExecutor


def create_executor(strategy: ExecutorStrategy, handler: StepHandler) -> Executor:
    """Create an executor instance for the given strategy."""
    match strategy:
        case ExecutorStrategy.SEQUENTIAL:
            return SequentialExecutor(handler)
        case ExecutorStrategy.ASYNC:
            return AsyncExecutor(handler)
        case ExecutorStrategy.BATCH_ASYNC:
            return BatchAsyncExecutor(handler)


__all__ = [
    "AsyncExecutor",
    "BatchAsyncExecutor",
    "SequentialExecutor",
    "create_executor",
]
