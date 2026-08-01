"""Tests for the authenticated upload route."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest
from fastapi import FastAPI, UploadFile
from httpx import ASGITransport, AsyncByteStream, AsyncClient
from starlette.requests import Request

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

    async def test_post_upload_projects_unsafe_filename_and_content_type(
        self,
        client: AsyncClient,
    ) -> None:
        response = await client.post(
            "/inputs",
            files={"file": ("https:secret@host", b"bytes", "https://token:secret@evil")},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["filename"] == "input"
        assert body["content_type"] == "application/octet-stream"
        assert "secret" not in response.text

    async def test_delete_is_idempotent_and_protects_referenced_input(
        self,
        client: AsyncClient,
    ) -> None:
        uploaded = await client.post(
            "/inputs",
            files={"file": ("book.epub", b"epub-bytes", "application/epub+zip")},
        )
        assert uploaded.status_code == 201
        body = uploaded.json()

        submitted = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": body["source_path"],
                "source_language": "en",
                "target_language": "es",
                "input_id": body["input_id"],
            },
        )
        assert submitted.status_code == 201

        protected = await client.delete(f"/inputs/{body['input_id']}")
        assert protected.status_code == 409
        assert protected.json() == {"detail": "input is referenced by a job"}

        # A missing input remains an idempotent success after an independent upload.
        independent = await client.post(
            "/inputs",
            files={"file": ("book.epub", b"epub-bytes", "application/epub+zip")},
        )
        independent_id = independent.json()["input_id"]
        deleted = await client.delete(f"/inputs/{independent_id}")
        assert deleted.status_code == 204
        assert (await client.delete(f"/inputs/{independent_id}")).status_code == 204

    async def test_mismatched_input_id_cleans_both_uploaded_inputs(
        self,
        client: AsyncClient,
    ) -> None:
        first = await client.post(
            "/inputs",
            files={"file": ("first.epub", b"first", "application/epub+zip")},
        )
        second = await client.post(
            "/inputs",
            files={"file": ("second.epub", b"second", "application/epub+zip")},
        )
        first_body = first.json()
        second_body = second.json()
        response = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": first_body["source_path"],
                "source_language": "en",
                "target_language": "es",
                "input_id": second_body["input_id"],
            },
        )

        assert response.status_code == 422
        transport = cast("ASGITransport", client._transport)  # noqa: SLF001
        app = cast("FastAPI", transport.app)
        data_dir = app.state.orchestrator.settings.orchestrator.data_dir
        assert not (data_dir / "inputs" / first_body["input_id"]).exists()
        assert not (data_dir / "inputs" / second_body["input_id"]).exists()

    async def test_post_storage_oserror_has_stable_public_message(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def fail_save(*_args: object, **_kwargs: object) -> None:
            raise OSError("/private/data/input.bin: disk full")

        monkeypatch.setattr("acheron.shell.input_store.InputStore.save", fail_save)
        response = await client.post(
            "/inputs",
            files={"file": ("book.epub", b"epub-bytes", "application/epub+zip")},
        )

        assert response.status_code == 503
        assert response.json() == {"detail": "input storage failed"}
        assert "/private/data" not in response.text

    async def test_promotion_oserror_has_stable_public_message(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        uploaded = await client.post(
            "/inputs",
            files={"file": ("book.epub", b"epub-bytes", "application/epub+zip")},
        )
        body = uploaded.json()

        def fail_promote(*_args: object, **_kwargs: object) -> None:
            raise OSError("/private/data/inputs: permission denied")

        monkeypatch.setattr("acheron.shell.input_store.InputStore.promote", fail_promote)
        response = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": body["source_path"],
                "source_language": "en",
                "target_language": "es",
                "input_id": body["input_id"],
            },
        )

        assert response.status_code == 422
        assert response.json() == {"detail": "input storage failed"}
        assert "/private/data" not in response.text

    async def test_delete_oserror_has_stable_public_message(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        uploaded = await client.post(
            "/inputs",
            files={"file": ("book.epub", b"epub-bytes", "application/epub+zip")},
        )
        input_id = uploaded.json()["input_id"]

        async def fail_delete(_input_id: str) -> None:
            raise OSError("/private/data/inputs: permission denied")

        transport = cast("ASGITransport", client._transport)  # noqa: SLF001
        app = cast("FastAPI", transport.app)
        monkeypatch.setattr(app.state.orchestrator, "delete_input", fail_delete)
        response = await client.delete(f"/inputs/{input_id}")

        assert response.status_code == 503
        assert response.json() == {"detail": "input deletion failed"}
        assert "/private/data" not in response.text

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

    async def test_post_rejects_dot_filename_without_leaking_destination(
        self,
        client: AsyncClient,
    ) -> None:
        """Malformed dot basenames are client errors and leave no random input directory."""
        transport = cast("ASGITransport", client._transport)  # noqa: SLF001
        app = cast("FastAPI", transport.app)
        data_dir = app.state.orchestrator.settings.orchestrator.data_dir

        response = await client.post(
            "/inputs",
            files={"file": ("..", b"data", "application/octet-stream")},
        )

        assert response.status_code == 422
        assert "Invalid input filename" in response.json()["detail"]
        inputs_dir = data_dir / "inputs"
        assert not inputs_dir.exists() or list(inputs_dir.iterdir()) == []

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


class _ChunkStream(AsyncByteStream):
    """Yield body chunks without a Content-Length header."""

    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.chunks = chunks
        self.consumed = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk


def _multipart_file_body(payload: bytes) -> tuple[bytes, int]:
    """Build a one-file multipart body and return its fixed framing size."""
    prefix = (
        b"--test\r\n"
        b'Content-Disposition: form-data; name="file"; filename="a.bin"\r\n'
        b"Content-Type: application/octet-stream\r\n\r\n"
    )
    suffix = b"\r\n--test--\r\n"
    return prefix + payload + suffix, len(prefix) + len(suffix)


class TestUploadAuth:
    async def test_post_without_token_does_not_enter_multipart_parser(
        self,
        client_with_token: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Authentication rejects before FastAPI asks Starlette to parse the form."""
        parser_calls = 0

        async def fail_form(_request: Request, *args: object, **kwargs: object) -> None:
            nonlocal parser_calls
            parser_calls += 1
            raise AssertionError("multipart parser entered before authentication")

        monkeypatch.setattr(Request, "form", fail_form)
        response = await client_with_token.post(
            "/inputs",
            files={"file": ("a.bin", b"data", "text/plain")},
        )

        assert response.status_code == 401
        assert parser_calls == 0

    async def test_post_content_length_limit_rejects_before_multipart_parser(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A declared oversized body is rejected before form parsing or spooling."""
        monkeypatch.setattr("acheron.shell.input_store.MAX_INPUT_BYTES", 4)
        monkeypatch.setattr("acheron.shell.api.input_boundary._MULTIPART_OVERHEAD_BYTES", 8)
        parser_calls = 0

        async def fail_form(_request: Request, *args: object, **kwargs: object) -> None:
            nonlocal parser_calls
            parser_calls += 1
            raise AssertionError("multipart parser entered for oversized body")

        monkeypatch.setattr(Request, "form", fail_form)
        response = await client.post(
            "/inputs",
            content=b"x" * 13,
            headers={"content-type": "multipart/form-data; boundary=test"},
        )

        assert response.status_code == 413
        assert parser_calls == 0

    async def test_post_chunked_limit_rejects_while_body_is_read(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A chunked body is bounded without buffering all chunks for multipart parsing."""
        monkeypatch.setattr("acheron.shell.input_store.MAX_INPUT_BYTES", 4)
        monkeypatch.setattr("acheron.shell.api.input_boundary._MULTIPART_OVERHEAD_BYTES", 8)
        stream = _ChunkStream(
            (
                b"--test\r\n",
                b'Content-Disposition: form-data; name="file"; filename="a"\r\n',
                b"Content-Type: text/plain\r\n\r\nbody\r\n--test--\r\n",
            )
        )
        response = await client.post(
            "/inputs",
            content=stream,
            headers={"content-type": "multipart/form-data; boundary=test"},
        )

        assert response.status_code == 413
        assert stream.consumed == 2

    async def test_post_exact_limit_content_length_uploads_successfully(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A Content-Length request with exactly the file limit reaches the route."""
        payload = b"data"
        body, overhead = _multipart_file_body(payload)
        monkeypatch.setattr("acheron.shell.input_store.MAX_INPUT_BYTES", len(payload))
        monkeypatch.setattr("acheron.shell.api.input_boundary._MULTIPART_OVERHEAD_BYTES", overhead)
        response = await client.post(
            "/inputs",
            content=body,
            headers={
                "content-type": "multipart/form-data; boundary=test",
                "content-length": str(len(body)),
            },
        )

        assert response.status_code == 201
        transport = cast("ASGITransport", client._transport)  # noqa: SLF001
        app = cast("FastAPI", transport.app)
        data_dir = app.state.orchestrator.settings.orchestrator.data_dir
        stored_path = data_dir / response.json()["source_path"]
        assert stored_path.read_bytes() == payload

    async def test_post_exact_limit_chunked_uploads_successfully(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A chunked request with exactly the file limit reaches the route."""
        payload = b"data"
        body, overhead = _multipart_file_body(payload)
        monkeypatch.setattr("acheron.shell.input_store.MAX_INPUT_BYTES", len(payload))
        monkeypatch.setattr("acheron.shell.api.input_boundary._MULTIPART_OVERHEAD_BYTES", overhead)
        split = len(body) // 2
        stream = _ChunkStream((body[:split], body[split:]))
        response = await client.post(
            "/inputs",
            content=stream,
            headers={"content-type": "multipart/form-data; boundary=test"},
        )

        assert response.status_code == 201
        assert stream.consumed == 2
        transport = cast("ASGITransport", client._transport)  # noqa: SLF001
        app = cast("FastAPI", transport.app)
        data_dir = app.state.orchestrator.settings.orchestrator.data_dir
        stored_path = data_dir / response.json()["source_path"]
        assert stored_path.read_bytes() == payload

    async def test_post_one_byte_over_content_length_limit_returns_413(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A Content-Length request one byte over the file limit is rejected early."""
        payload = b"data!"
        body, overhead = _multipart_file_body(payload)
        monkeypatch.setattr("acheron.shell.input_store.MAX_INPUT_BYTES", len(payload) - 1)
        monkeypatch.setattr("acheron.shell.api.input_boundary._MULTIPART_OVERHEAD_BYTES", overhead)
        response = await client.post(
            "/inputs",
            content=body,
            headers={
                "content-type": "multipart/form-data; boundary=test",
                "content-length": str(len(body)),
            },
        )

        assert response.status_code == 413
        assert response.json() == {"detail": "input exceeds the 2 GiB upload limit"}

    async def test_post_one_byte_over_chunked_limit_returns_413(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A chunked request one byte over the file limit is rejected while reading."""
        payload = b"data!"
        body, overhead = _multipart_file_body(payload)
        monkeypatch.setattr("acheron.shell.input_store.MAX_INPUT_BYTES", len(payload) - 1)
        monkeypatch.setattr("acheron.shell.api.input_boundary._MULTIPART_OVERHEAD_BYTES", overhead)
        split = len(body) // 2
        stream = _ChunkStream((body[:split], body[split:]))
        response = await client.post(
            "/inputs",
            content=stream,
            headers={"content-type": "multipart/form-data; boundary=test"},
        )

        assert response.status_code == 413
        assert stream.consumed == 2
        assert response.json() == {"detail": "input exceeds the 2 GiB upload limit"}

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
