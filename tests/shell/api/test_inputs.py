"""Tests for the authenticated upload route."""

from __future__ import annotations

from typing import cast

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


class TestUploadRoute:
    async def test_post_uploads_file_and_returns_relative_path_and_metadata(
        self,
        client: AsyncClient,
    ) -> None:
        """POST /inputs streams the file to disk and returns the server-relative source path."""
        transport = cast("ASGITransport", client._transport)  # noqa: SLF001
        app = cast("FastAPI", transport.app)
        data_dir = app.state.orchestrator.settings.orchestrator.data_dir

        response = await client.post(
            "/inputs",
            files={"file": ("nested/book.epub", b"epub-bytes", "application/epub+zip")},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["filename"] == "book.epub"
        assert body["size_bytes"] == len(b"epub-bytes")
        assert body["content_type"] == "application/epub+zip"
        assert body["source_path"].startswith("inputs/")
        assert body["source_path"].endswith("/book.epub")

        stored_path = data_dir / body["source_path"]
        assert stored_path.is_file()
        assert stored_path.read_bytes() == b"epub-bytes"

    async def test_post_oversize_stream_returns_413_and_leaves_no_temp_file(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An oversized upload must return 413 and clean up the temp file."""
        monkeypatch.setattr("acheron.shell.input_store.MAX_INPUT_BYTES", 4)
        transport = cast("ASGITransport", client._transport)  # noqa: SLF001
        app = cast("FastAPI", transport.app)
        data_dir = app.state.orchestrator.settings.orchestrator.data_dir

        response = await client.post(
            "/inputs",
            files={"file": ("big.bin", b"hello world", "application/octet-stream")},
        )

        assert response.status_code == 413
        assert response.json() == {"detail": "input exceeds the 2 GiB upload limit"}

        temp_dir = data_dir / ".inputs-tmp"
        if temp_dir.exists():
            leftover = [p for p in temp_dir.rglob("*") if p.is_file()]
            assert leftover == [], f"temp files left after 413: {leftover}"

    async def test_post_reports_original_content_type_and_size(
        self,
        client: AsyncClient,
    ) -> None:
        """The route must report the original content type and exact byte count."""
        response = await client.post(
            "/inputs",
            files={"file": ("a.bin", b"\x00\x01\x02\x03", "application/x-custom")},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["content_type"] == "application/x-custom"
        assert body["size_bytes"] == 4


class TestUploadAuth:
    async def test_post_without_token_returns_401(self, client_with_token: AsyncClient) -> None:
        response = await client_with_token.post(
            "/inputs",
            files={"file": ("a.bin", b"data", "text/plain")},
        )
        assert response.status_code == 401

    async def test_post_with_token_returns_201(self, client_with_token: AsyncClient) -> None:
        response = await client_with_token.post(
            "/inputs",
            files={"file": ("a.bin", b"data", "text/plain")},
            headers={"Authorization": "Bearer test-registration-token-must-be-32-chars-or-more"},
        )
        assert response.status_code == 201
