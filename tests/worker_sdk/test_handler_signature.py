"""Verify WorkerHandler.handle gains the optional input parameter (8b)."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from acheron.core.models import Job, WorkerType
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler
from acheron.worker_sdk.inputs import BytesInput, Input


class _DummyHandler(WorkerHandler):
    """Concrete handler that accepts both call styles."""

    def capabilities(self) -> Any:  # noqa: ANN401, D102
        return None

    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:
        return [
            BytesArtifact(
                filename="dummy.txt",
                content_type="text/plain",
                data=b"ok",
            )
        ]


def test_handle_signature_accepts_input_kwarg() -> None:
    sig = inspect.signature(_DummyHandler.handle)
    params = sig.parameters
    assert "input" in params
    assert params["input"].default is None
    assert params["input"].annotation == "Input | None"


def test_abstract_handle_signature_includes_input_param() -> None:
    """The WorkerHandler ABC itself declares the new ``input`` parameter
    so all subclasses see the updated contract (8b)."""
    sig = inspect.signature(WorkerHandler.handle)
    params = sig.parameters
    assert "input" in params, "WorkerHandler.handle must declare an 'input' parameter (8b)"
    assert params["input"].default is None, "input parameter must default to None for TTS backward compat"


def test_call_without_input_works() -> None:
    """TTS-style call: handle(job) — input defaults to None."""
    h = _DummyHandler()
    job = Job(job_id="j-1", job_type=WorkerType.TTS, payload={}, chapter_id="ch1")
    out = asyncio.run(h.handle(job))
    assert len(out) == 1
    assert out[0].content_type == "text/plain"


def test_call_with_input_kwarg_works() -> None:
    """ASR-style call: handle(job, input=BytesInput(...))."""
    h = _DummyHandler()
    job = Job(job_id="j-1", job_type=WorkerType.ASR, payload={}, chapter_id="ch1")
    out = asyncio.run(h.handle(job, input=BytesInput(content_type="audio/wav", data=b"RIFF")))
    assert len(out) == 1
