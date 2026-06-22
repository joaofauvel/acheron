"""Acheron worker SDK — the blueprint for Layer 8 real GPU workers.

Public surface re-exports are filled in by later tasks as modules land.
Importing this package must not require runpod/torch/etc. — those deps are
imported lazily by the modules that need them so unit tests of pure types
(handler, artifacts, settings) work without GPU SDKs installed.
"""

from acheron.worker_sdk.app import create_worker_app
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact, FileArtifact, StreamArtifact
from acheron.worker_sdk.cloud import make_runpod_handler
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
    "StreamArtifact",
    "WorkerHandler",
    "WorkerSettings",
    "PriceSource",
    "PriceEstimate",
    "RunPodPrice",
    "StaticPrice",
    "ZeroPrice",
    "create_worker_app",
    "make_runpod_handler",
    "register_with_orchestrator",
]
