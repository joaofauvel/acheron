"""Acheron worker SDK — the blueprint for Layer 8 real GPU workers."""

from typing import TYPE_CHECKING

from acheron.worker_sdk.artifacts import Artifact, BytesArtifact, FileArtifact, StreamArtifact
from acheron.worker_sdk.cloud import RunPodForwarderHandler, make_runpod_handler
from acheron.worker_sdk.handler import WorkerHandler
from acheron.worker_sdk.inputs import BytesInput, FileInput, StreamInput
from acheron.worker_sdk.pricing import (
    PriceEstimate,
    PriceSource,
    RunPodPrice,
    StaticPrice,
    ZeroPrice,
)
from acheron.worker_sdk.registration import register_with_orchestrator
from acheron.worker_sdk.settings import WorkerSettings

if TYPE_CHECKING:
    from acheron.worker_sdk.app import create_worker_app

__all__ = [
    "Artifact",
    "BytesArtifact",
    "BytesInput",
    "FileArtifact",
    "FileInput",
    "PriceEstimate",
    "PriceSource",
    "RunPodForwarderHandler",
    "RunPodPrice",
    "StaticPrice",
    "StreamArtifact",
    "StreamInput",
    "WorkerHandler",
    "WorkerSettings",
    "ZeroPrice",
    "create_worker_app",
    "make_runpod_handler",
    "register_with_orchestrator",
]


def __getattr__(name: str) -> object:
    if name == "create_worker_app":
        from acheron.worker_sdk.app import create_worker_app  # noqa: PLC0415

        return create_worker_app
    message = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(message)
