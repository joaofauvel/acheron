"""Streaming pipeline executor — placeholder, full implementation in 9a."""

from acheron.core.interfaces import Executor
from acheron.core.models import Plan, PlanResult
from acheron.shell.executors._utils import StepHandler


class StreamingExecutor(Executor):
    """Placeholder; full implementation in 9a."""

    def __init__(self, handler: StepHandler, step_cache: object) -> None:
        self._handler = handler
        self._step_cache = step_cache

    async def run(self, plan: Plan) -> PlanResult:
        msg = "StreamingExecutor.run not yet implemented"
        raise NotImplementedError(msg)
