"""Acheron worker SDK — the blueprint for Layer 8 real GPU workers.

The sub-package is intentionally GPU-SDK free at import time: importing
``acheron.worker_sdk`` does not transitively load ``runpod`` (that import
lives in ``_runpod_client``, which is not part of the public re-exports).
This lets tests of pure types (handler, artifacts, settings) run without
the runpod SDK installed.
"""

from acheron.worker_sdk.app import create_worker_app
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact, FileArtifact, StreamArtifact
from acheron.worker_sdk.cloud import RunPodForwarderHandler, make_runpod_handler
from acheron.worker_sdk.handler import WorkerHandler
from acheron.worker_sdk.pricing import (
    PriceEstimate,
    PriceSource,
    RunPodPrice,
    StaticPrice,
    ZeroPrice,
)
from acheron.worker_sdk.registration import register_with_orchestrator
from acheron.worker_sdk.settings import WorkerSettings

__all__ = [
    "Artifact",
    "BytesArtifact",
    "FileArtifact",
    "PriceEstimate",
    "PriceSource",
    "RunPodForwarderHandler",
    "RunPodPrice",
    "StaticPrice",
    "StreamArtifact",
    "WorkerHandler",
    "WorkerSettings",
    "ZeroPrice",
    "create_worker_app",
    "make_runpod_handler",
    "register_with_orchestrator",
]
