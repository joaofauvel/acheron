"""The blueprint ABC every Layer 8 real GPU worker implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from acheron.core.models import Job, WorkerCapabilities
    from acheron.worker_sdk.artifacts import Artifact
    from acheron.worker_sdk.inputs import Input


class WorkerHandler(ABC):
    """Implemented by each worker.

    Loaded once at container boot. `startup()` runs before any job
    dispatch; `shutdown()` releases GPU memory at the edge container's
    lifespan teardown.
    """

    @abstractmethod
    def capabilities(self) -> WorkerCapabilities:
        """Return the worker's static description. No I/O — sync."""

    @abstractmethod
    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:  # noqa: A002
        """Run inference for `job`, consuming `input` if the step is audio-in.

        The ``input`` parameter is the new 8b addition; default ``None``
        keeps backward compatibility — TTS, translation, and stub handlers
        that don't take an input are unchanged.
        """

    async def startup(self) -> None:
        """Optional hook: load model onto GPU, warm caches. Default: no-op."""
        return

    async def shutdown(self) -> None:
        """Optional hook: free GPU memory. Default: no-op."""
        return
