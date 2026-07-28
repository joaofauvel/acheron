"""Tests for the authenticated upload route."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest
from fastapi import FastAPI, UploadFile
from httpx import ASGITransport, AsyncClient

from acheron.shell.api.routes import inputs as inputs_module

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio


class _CloseTrackingFile:
    """Wraps an ``UploadFile.file`` to observe ``close()`` calls.

    The route calls ``await file.close()`` in a ``finally`` block, which
    delegates to ``self.file.close()`` on the underlying spooled file.
    Swapping the inner file for this proxy lets a test assert that the
    route did, in fact, close the upload — on every code path, not just
    the happy one.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1
        self._inner.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


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

    async def test_post_closes_upload_on_success(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The route must close the UploadFile on the happy path."""
        real_chunks = inputs_module._chunks  # noqa: SLF001
        trackers: list[_CloseTrackingFile] = []

        async def tracking_chunks(file: UploadFile) -> AsyncIterator[bytes]:
            tracker = _CloseTrackingFile(file.file)
            file.file = tracker  # type: ignore[assignment]
            trackers.append(tracker)
            async for chunk in real_chunks(file):
                yield chunk

        monkeypatch.setattr(inputs_module, "_chunks", tracking_chunks)

        response = await client.post(
            "/inputs",
            files={"file": ("a.bin", b"hello", "application/octet-stream")},
        )

        assert response.status_code == 201
        # The route's `finally` must call close. Starlette's request teardown
        # also closes via the AsyncExitStackMiddleware, so we expect at least 2
        # total calls (route + teardown). Asserting >= 2 proves the route
        # called close — without the route's finally, teardown alone yields 1.
        assert len(trackers) == 1
        assert trackers[0].close_count >= 2

    async def test_post_closes_upload_on_oversize_error(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The route must close the UploadFile even when the store raises a size error."""
        real_chunks = inputs_module._chunks  # noqa: SLF001
        trackers: list[_CloseTrackingFile] = []

        async def tracking_chunks(file: UploadFile) -> AsyncIterator[bytes]:
            tracker = _CloseTrackingFile(file.file)
            file.file = tracker  # type: ignore[assignment]
            trackers.append(tracker)
            async for chunk in real_chunks(file):
                yield chunk

        monkeypatch.setattr(inputs_module, "_chunks", tracking_chunks)
        monkeypatch.setattr("acheron.shell.input_store.MAX_INPUT_BYTES", 4)

        response = await client.post(
            "/inputs",
            files={"file": ("big.bin", b"hello world", "application/octet-stream")},
        )

        assert response.status_code == 413
        # See `test_post_closes_upload_on_success` for why the threshold is 2:
        # the route's `finally` runs on the error path, then Starlette's
        # AsyncExitStack teardown closes the form. Without the route's
        # finally, teardown alone would yield only 1 call.
        assert len(trackers) == 1
        assert trackers[0].close_count >= 2


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


class TestUploadToSubmitRoundTrip:
    """End-to-end: a source uploaded to ``/inputs`` must be acceptable to ``/jobs``."""

    async def test_uploaded_source_path_is_resolved_and_submitted(
        self,
        client: AsyncClient,
    ) -> None:
        upload = await client.post(
            "/inputs",
            files={"file": ("book.epub", b"uploaded-epub-bytes", "application/epub+zip")},
        )
        assert upload.status_code == 201
        source_path = upload.json()["source_path"]

        submit = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": source_path,
                "source_language": "en",
                "target_language": "es",
            },
        )
        assert submit.status_code == 201
        assert submit.json()["job_id"].startswith("job-")
