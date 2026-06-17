"""HTTP client for the Acheron orchestrator API."""

from __future__ import annotations

from typing import Any, cast

import httpx


class AcheronClient:
    """Thin async wrapper around the Acheron orchestrator REST API."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    async def submit_job(  # noqa: PLR0913
        self,
        source_type: str,
        source_path: str,
        source_language: str,
        target_language: str,
        executor_strategy: str = "batch_async",
        asr_model: str | None = None,
    ) -> dict[str, Any]:
        """Submit a new job for processing."""
        payload: dict[str, Any] = {
            "source_type": source_type,
            "source_path": source_path,
            "source_language": source_language,
            "target_language": target_language,
            "executor_strategy": executor_strategy,
        }
        if asr_model is not None:
            payload["asr_model"] = asr_model
        async with httpx.AsyncClient(base_url=self._base_url, transport=self._transport) as client:
            resp = await client.post("/jobs", json=payload)
            resp.raise_for_status()
            return cast("dict[str, Any]", resp.json())

    async def get_job(self, job_id: str) -> dict[str, Any]:
        """Get job status and result."""
        async with httpx.AsyncClient(base_url=self._base_url, transport=self._transport) as client:
            resp = await client.get(f"/jobs/{job_id}")
            resp.raise_for_status()
            return cast("dict[str, Any]", resp.json())

    async def list_jobs(self) -> list[dict[str, Any]]:
        """List all jobs."""
        async with httpx.AsyncClient(base_url=self._base_url, transport=self._transport) as client:
            resp = await client.get("/jobs")
            resp.raise_for_status()
            return cast("list[dict[str, Any]]", resp.json()["jobs"])

    async def list_workers(self) -> list[dict[str, Any]]:
        """List all registered workers."""
        async with httpx.AsyncClient(base_url=self._base_url, transport=self._transport) as client:
            resp = await client.get("/workers")
            resp.raise_for_status()
            return cast("list[dict[str, Any]]", resp.json()["workers"])

    async def get_capabilities(
        self,
        src: str | None = None,
        dest: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get supported language pairs."""
        params: dict[str, str] = {}
        if src is not None:
            params["src"] = src
        if dest is not None:
            params["dest"] = dest
        async with httpx.AsyncClient(base_url=self._base_url, transport=self._transport) as client:
            resp = await client.get("/capabilities", params=params)
            resp.raise_for_status()
            return cast("list[dict[str, Any]]", resp.json()["language_pairs"])
