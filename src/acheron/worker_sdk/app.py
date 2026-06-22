"""Public ``create_worker_app`` factory building the edge FastAPI app."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import httpx
from fastapi import FastAPI

from acheron.worker_sdk._edge_http import EdgeApp
from acheron.worker_sdk.pricing import (
    PriceSource,
    RunPodPrice,
    StaticPrice,
    ZeroPrice,
)
from acheron.worker_sdk.registration import register_with_orchestrator

if TYPE_CHECKING:
    from acheron.worker_sdk.handler import WorkerHandler
    from acheron.worker_sdk.settings import WorkerSettings

logger = logging.getLogger(__name__)


def _build_price_source(settings: "WorkerSettings") -> PriceSource:
    match settings.price_source:
        case "runpod":
            if not settings.runpod_api_key or not settings.runpod_endpoint_id:
                logger.warning(
                    "price_source=runpod but RUNPOD_API_KEY/RUNPOD_ENDPOINT_ID "
                    "not set; prices will be unknown"
                )
                return ZeroPrice()
            return RunPodPrice(
                api_key=settings.runpod_api_key,
                endpoint_id=settings.runpod_endpoint_id,
                secure_cloud=settings.secure_cloud,
                cache_ttl_s=settings.price_cache_ttl_s,
            )
        case "static":
            if settings.dollars_per_hour is None:
                logger.warning(
                    "price_source=static but dollars_per_hour not set; "
                    "falling back to ZeroPrice"
                )
                return ZeroPrice()
            return StaticPrice(dollars_per_hour=settings.dollars_per_hour)
        case _:
            return ZeroPrice()


def _endpoint_url(settings: "WorkerSettings") -> str:
    """The URL the orchestrator will use to reach this edge container."""
    return f"http://{os.environ.get('WORKER_HOST', 'localhost')}:{settings.listen_port}"


def create_worker_app(
    *,
    handler: "WorkerHandler",
    settings: "WorkerSettings",
    disable_registration: bool = False,
) -> FastAPI:
    """Build the edge FastAPI app wired with registration + price refresh."""
    caps = handler.capabilities()
    price_source = _build_price_source(settings)
    inner = EdgeApp(handler=handler, capabilities=caps, price_source=price_source)

    async def _register() -> None:
        async with httpx.AsyncClient() as client:
            await register_with_orchestrator(
                client=client,
                orchestrator_url=settings.orchestrator_url,
                token=settings.registration_token,
                worker_id=settings.worker_id,
                endpoint=_endpoint_url(settings),
                transport="http",
                capabilities=caps,
            )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # 1. startup hook (model load, etc.)
        await handler.startup()
        # 2. eager price refresh — fault-tolerant, never blocks
        try:
            await price_source.refresh()
        except Exception:
            logger.warning(
                "Price refresh raised at startup; worker will register anyway",
                exc_info=True,
            )
        # 3. register with orchestrator (skipped in tests / when explicitly disabled)
        if not disable_registration:
            await _register()
        try:
            yield
        finally:
            await handler.shutdown()

    app = FastAPI(title="acheron-worker-edge", lifespan=lifespan)
    # Mount the inner app's routes manually so we don't run the inner lifespan.
    inner_paths = {"/health", "/capabilities", "/execute"}
    for route in inner.app.routes:
        path = getattr(route, "path", None)
        if path in inner_paths:
            app.routes.append(route)
    return app
