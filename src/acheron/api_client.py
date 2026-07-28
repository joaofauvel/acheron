"""HTTP client for the Acheron orchestrator API."""

from __future__ import annotations

import mimetypes
import ssl
from pathlib import Path
from typing import cast

import aiofiles
import httpx

from acheron.core.schemas import (
    CapabilitiesResponse,
    InputResponse,
    JobListResponse,
    JobResponse,
    LanguagePair,
    WorkerCapability,
    WorkerListResponse,
    WorkerResponse,
)


def _ssl_context_for(verify: bool | str | Path) -> bool | ssl.SSLContext:  # noqa: FBT001
    """Resolve ``verify`` to an ``ssl.SSLContext`` for httpx.

    httpx deprecated ``verify=<str>`` (causes a deprecation warning). The
    recommended replacement is ``verify=ssl.create_default_context(cafile=...)``.
    """
    if isinstance(verify, bool):
        return verify
    return ssl.create_default_context(cafile=str(verify))


class AcheronClient:
    """Thin async wrapper around the Acheron orchestrator REST API."""

    def __init__(
        self,
        base_url: str = "https://localhost:8000",
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        verify: bool | str | Path = True,
        registration_token: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        # Keep the original for callers that want to introspect the request.
        self._verify: bool | str | Path = verify
        self._ssl_verify: bool | ssl.SSLContext = _ssl_context_for(verify)
        self._registration_token: str | None = registration_token

    def _mutation_headers(self) -> dict[str, str]:
        """Headers applied to mutating (POST) requests when a registration token is configured; empty otherwise."""
        if self._registration_token is None:
            return {}
        return {"Authorization": f"Bearer {self._registration_token}"}

    async def submit_job(  # noqa: PLR0913
        self,
        source_type: str,
        source_path: str,
        source_language: str,
        target_language: str,
        executor_strategy: str = "streaming",
        asr_model: str | None = None,
    ) -> JobResponse:
        """Submit a new job for processing."""
        payload: dict[str, str | None] = {
            "source_type": source_type,
            "source_path": source_path,
            "source_language": source_language,
            "target_language": target_language,
            "executor_strategy": executor_strategy,
            "asr_model": asr_model,
        }
        async with httpx.AsyncClient(
            base_url=self._base_url, transport=self._transport, verify=self._ssl_verify
        ) as client:
            resp = await client.post("/jobs", json=payload, headers=self._mutation_headers())
            resp.raise_for_status()
            return JobResponse.model_validate(resp.json())

    async def get_job(self, job_id: str) -> JobResponse:
        """Get job status and result."""
        async with httpx.AsyncClient(
            base_url=self._base_url, transport=self._transport, verify=self._ssl_verify
        ) as client:
            resp = await client.get(f"/jobs/{job_id}")
            resp.raise_for_status()
            return JobResponse.model_validate(resp.json())

    async def resume_job(self, job_id: str, *, force_fresh: bool = False) -> JobResponse:
        """Resume a saved job."""
        async with httpx.AsyncClient(
            base_url=self._base_url, transport=self._transport, verify=self._ssl_verify
        ) as client:
            resp = await client.post(
                f"/jobs/{job_id}/resume",
                params={"force_fresh": force_fresh},
                headers=self._mutation_headers(),
            )
            resp.raise_for_status()
            return JobResponse.model_validate(resp.json())

    async def upload_input(self, path: str | Path) -> InputResponse:
        """Upload a local file to the orchestrator's input store."""
        source = Path(path)
        content_type, _ = mimetypes.guess_type(source.name)
        if content_type is None:
            content_type = "application/octet-stream"
        async with aiofiles.open(source, "rb") as fp:
            data = await fp.read()
        async with httpx.AsyncClient(
            base_url=self._base_url, transport=self._transport, verify=self._ssl_verify
        ) as client:
            resp = await client.post(
                "/inputs",
                files={"file": (source.name, data, content_type)},
                headers=self._mutation_headers(),
            )
            resp.raise_for_status()
            return InputResponse.model_validate(resp.json())

    async def get_health(self) -> dict[str, str]:
        """Get orchestrator health."""
        async with httpx.AsyncClient(
            base_url=self._base_url, transport=self._transport, verify=self._ssl_verify
        ) as client:
            resp = await client.get("/health")
            resp.raise_for_status()
            return cast("dict[str, str]", resp.json())

    async def list_jobs(self) -> list[JobResponse]:
        """List all jobs."""
        async with httpx.AsyncClient(
            base_url=self._base_url, transport=self._transport, verify=self._ssl_verify
        ) as client:
            resp = await client.get("/jobs")
            resp.raise_for_status()
            return JobListResponse.model_validate(resp.json()).jobs

    async def list_workers(self) -> list[WorkerResponse]:
        """List all registered workers."""
        async with httpx.AsyncClient(
            base_url=self._base_url, transport=self._transport, verify=self._ssl_verify
        ) as client:
            resp = await client.get("/workers")
            resp.raise_for_status()
            return WorkerListResponse.model_validate(resp.json()).workers

    async def get_capabilities(
        self,
        src: str | None = None,
        dest: str | None = None,
    ) -> list[LanguagePair]:
        """Get supported language pairs."""
        params: dict[str, str] = {}
        if src is not None:
            params["src"] = src
        if dest is not None:
            params["dest"] = dest
        async with httpx.AsyncClient(
            base_url=self._base_url, transport=self._transport, verify=self._ssl_verify
        ) as client:
            resp = await client.get("/capabilities", params=params)
            resp.raise_for_status()
            return CapabilitiesResponse.model_validate(resp.json()).language_pairs

    async def get_worker_capabilities(self, worker_type: str) -> list[WorkerCapability]:
        """Get registered workers of a given type as a typed list."""
        async with httpx.AsyncClient(
            base_url=self._base_url, transport=self._transport, verify=self._ssl_verify
        ) as client:
            resp = await client.get("/capabilities", params={"type": worker_type})
            resp.raise_for_status()
            return CapabilitiesResponse.model_validate(resp.json()).workers
