"""Tests for the internal edge FastAPI app."""

import dataclasses
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from httpx import ASGITransport

from acheron.core.models import CostBasis, Job, WorkerCapabilities, WorkerType
from acheron.worker_sdk import _edge_http as edge_module
from acheron.worker_sdk._caps import public_caps_to_dict
from acheron.worker_sdk._edge_http import EdgeApp
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler
from acheron.worker_sdk.inputs import Input
from acheron.worker_sdk.pricing import PriceEstimate

if TYPE_CHECKING:
    from fastapi import FastAPI


class _MeasuredPrice:
    async def estimate(self, gpu_seconds: float) -> PriceEstimate:
        return PriceEstimate(
            cost=0.34,
            basis=CostBasis.MEASURED,
            rate_per_hour=0.69,
            gpu_type="L4",
            secure_cloud=False,
        )

    async def refresh(self) -> bool:
        return True

    async def close(self) -> None:
        return


class _BrokenPrice(_MeasuredPrice):
    async def estimate(self, gpu_seconds: float) -> PriceEstimate:
        raise RuntimeError("price lookup password=secret")


class _MissingStaticPrice(_MeasuredPrice):
    async def estimate(self, gpu_seconds: float) -> PriceEstimate:
        return PriceEstimate(cost=None, basis=CostBasis.STATIC)


class _UnsafeGpuPrice(_MeasuredPrice):
    async def estimate(self, gpu_seconds: float) -> PriceEstimate:
        return PriceEstimate(
            cost=0.34,
            basis=CostBasis.MEASURED,
            rate_per_hour=0.69,
            gpu_type="https://user:secret@example.invalid/gpu",
            secure_cloud=False,
        )


class _Stub(WorkerHandler):
    def __init__(self) -> None:
        self.calls = 0

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"en"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source="huggingface:test",
            metadata={
                "speakers": ["Ryan"],
                "default_speaker": "Ryan",
                "health_provider": "runpod",
                "health_endpoint_id": "endpoint-1",
                "client_id": "private-client",
                "provider_endpoint": "https://provider.invalid/private",
                "custom": "must-not-leak",
            },
        )

    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:  # noqa: A002
        self.calls += 1
        return [BytesArtifact(filename="out.wav", content_type="audio/wav", data=b"audio")]


@pytest.fixture
def app_handler() -> tuple[FastAPI, _Stub]:
    h = _Stub()
    app = EdgeApp(handler=h, capabilities=h.capabilities(), allow_unauthenticated_execute=True).app
    return app, h


class TestEdgeRoutes:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, app_handler: tuple[FastAPI, _Stub]) -> None:
        app, _ = app_handler
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_execute_rejects_oversized_json_without_content_length(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(edge_module, "_MAX_EXECUTE_BODY_BYTES", 8)
        handler = _Stub()
        app = EdgeApp(
            handler=handler,
            capabilities=handler.capabilities(),
            allow_unauthenticated_execute=True,
        ).app

        class ChunkedBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'{"job_id":"too-large"}'

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/execute",
                content=ChunkedBody(),
                headers={"content-type": "application/json"},
            )

        assert response.status_code == 413
        assert response.json() == {"detail": "execute request exceeds maximum size"}
        assert handler.calls == 0

    @pytest.mark.asyncio
    async def test_execute_rejects_oversized_content_length(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(edge_module, "_MAX_EXECUTE_BODY_BYTES", 8)
        handler = _Stub()
        app = EdgeApp(
            handler=handler,
            capabilities=handler.capabilities(),
            allow_unauthenticated_execute=True,
        ).app
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/execute",
                content=b"{}",
                headers={"content-type": "application/json", "content-length": "100"},
            )

        assert response.status_code == 413
        assert handler.calls == 0

    @pytest.mark.asyncio
    async def test_capabilities_drops_unsafe_capability_labels(self) -> None:
        handler = _Stub()
        capabilities = dataclasses.replace(
            handler.capabilities(),
            supported_languages_in=frozenset({"en", "../etc/passwd", "token TOPSECRET"}),
            supported_formats_out=frozenset({"wav", "https://secret.example"}),
        )
        app = EdgeApp(handler=handler, capabilities=capabilities, allow_unauthenticated_execute=True).app
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/capabilities")
        assert response.status_code == 200
        body = response.json()
        assert body["supported_languages_in"] == ["en"]
        assert body["supported_formats_out"] == ["wav"]
        assert "TOPSECRET" not in response.text

    @pytest.mark.asyncio
    async def test_capabilities_returns_shape(self, app_handler: tuple[FastAPI, _Stub]) -> None:
        app, _ = app_handler
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/capabilities")
        assert r.status_code == 200
        body = r.json()
        assert body["worker_type"] == "tts"
        assert body["supported_languages_in"] == ["en"]
        assert body["supported_formats_out"] == ["wav"]
        assert body["metadata"] == {
            "speakers": ["Ryan"],
            "default_speaker": "Ryan",
            "health_provider": "runpod",
            "health_endpoint_id": "endpoint-1",
        }

    def test_capabilities_reject_unsafe_public_values(self) -> None:
        caps = _Stub().capabilities()
        caps.metadata.update(
            {
                "voice": "password xyz",
                "default_speaker": "token-SECRET",
                "speakers": ["Ryan", "api key TOPSECRET"],
                "health_endpoint_id": "https://user:secret@provider.invalid?token=secret",
                "health_provider": "https://provider.invalid",
            }
        )
        body = public_caps_to_dict(caps)
        assert body["metadata"] == {}

    @pytest.mark.asyncio
    async def test_multipart_headers_reject_crlf_artifact_values(self) -> None:
        class UnsafeHandler(_Stub):
            async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:  # noqa: A002
                return [
                    BytesArtifact(
                        filename='../secret.wav"\r\nX-Injected: yes',
                        content_type="audio/wav\r\nX-Leak: yes",
                        data=b"audio",
                    )
                ]

        handler = UnsafeHandler()
        app = EdgeApp(handler=handler, capabilities=handler.capabilities(), allow_unauthenticated_execute=True).app
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/execute",
                json={
                    "job_id": "j1",
                    "job_type": "tts",
                    "payload": {"chunks": [{"text": "hi"}], "target_language": "en"},
                    "chapter_id": "ch1",
                },
            )
        assert response.status_code == 200
        assert response.headers.get("x-injected") is None
        assert b'filename="output.bin"' in response.content
        assert b"Content-Type: application/octet-stream" in response.content

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_execute_metadata_drops_untrusted_values(self) -> None:
        class _MetadataStub(_Stub):
            async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:  # noqa: A002
                return [
                    BytesArtifact(
                        filename="out.wav",
                        content_type="audio/wav",
                        data=b"audio",
                        metadata={
                            "chapter_id": "ch1",
                            "sequence_id": 0,
                            "token": "TOPSECRET",
                            "path": "/etc/passwd",
                            "nested": {"url": "https://user:pw@example/x"},
                        },
                    )
                ]

        handler = _MetadataStub()
        app = EdgeApp(handler=handler, capabilities=handler.capabilities(), allow_unauthenticated_execute=True).app
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            response = await c.post(
                "/execute",
                json={"job_id": "j1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"},
            )
        assert response.status_code == 200
        assert b'X-Acheron-Metadata: {"chapter_id":"ch1","sequence_id":0}' in response.content
        assert b"TOPSECRET" not in response.content
        assert b"/etc/passwd" not in response.content
        assert b"user:pw@example" not in response.content

    @pytest.mark.asyncio
    async def test_execute_failure_sanitizes_arbitrary_job_id(
        self,
        app_handler: tuple[FastAPI, _Stub],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        app, handler = app_handler

        async def _boom(job: Job, input: Input | None = None) -> list[Artifact]:  # noqa: A002
            raise RuntimeError("failed")

        monkeypatch.setattr(handler, "handle", _boom)
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            response = await c.post(
                "/execute",
                json={"job_id": "/tmp/token=TOPSECRET", "job_type": "tts", "payload": {}},
            )
        assert response.status_code == 500
        assert response.json()["job_id"] == "<unknown>"
        assert "TOPSECRET" not in response.text

    @pytest.mark.asyncio
    async def test_execute_returns_multipart(self, app_handler: tuple[FastAPI, _Stub]) -> None:
        app, h = app_handler
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/execute",
                json={
                    "job_id": "j1",
                    "job_type": "tts",
                    "payload": {"chunks": [{"text": "hi"}], "target_language": "en"},
                    "chapter_id": "ch1",
                },
            )
        assert r.status_code == 200
        assert "multipart/mixed" in r.headers["content-type"]
        assert h.calls == 1
        assert b"audio" in r.content

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("body", "content_type"),
        [
            ('{"job_id": "/tmp/private/token=secret",', "application/json"),
            ('{"payload": {}}', "application/json"),
        ],
    )
    async def test_execute_malformed_json_returns_sanitized_jobresult(
        self,
        app_handler: tuple[FastAPI, _Stub],
        body: str,
        content_type: str,
    ) -> None:
        app, h = app_handler
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            response = await c.post("/execute", content=body, headers={"content-type": content_type})

        assert response.status_code == 500
        result = response.json()
        assert result["status"] == "failed"
        assert result["error"] == "Malformed execute request"
        assert "/tmp/private" not in response.text
        assert "secret" not in response.text
        assert h.calls == 0

    @pytest.mark.asyncio
    async def test_execute_on_handler_error_returns_jobresult_json(
        self,
        app_handler: tuple[FastAPI, _Stub],
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """On handler error, the body is a ``JobResult`` JSON (status=failed,
        job_id echoed, error populated, no outputs) so the orchestrator's
        :class:`TypeAdapter(JobResult).validate_json` parser succeeds. The
        ``error`` field is sanitised to ``"<ClassName>: <first line>"`` so
        internal exception detail (file paths, secrets) does not leak back
        to the orchestrator.
        """
        app, h = app_handler

        async def _boom(job: Job, input: Input | None = None) -> list[BytesArtifact]:  # noqa: A002
            raise RuntimeError("OOM")

        monkeypatch.setattr(h, "handle", _boom)
        transport = ASGITransport(app=app)
        with caplog.at_level("ERROR", logger="acheron.worker_sdk._edge_http"):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                r = await c.post(
                    "/execute",
                    json={"job_id": "j1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"},
                )
        assert r.status_code == 500
        body = r.json()
        assert body["job_id"] == "j1"
        assert body["status"] == "failed"
        assert body["outputs"] == []
        assert body["error"] == "RuntimeError: OOM"
        assert body["metrics"]["duration_seconds"] >= 0.0
        assert body["metrics"]["cost_estimate"] is None
        assert any("handler failed" in r.message and "_Stub" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_unsafe_gpu_type_is_omitted_from_metrics(self) -> None:
        h = _Stub()
        app = EdgeApp(
            handler=h, capabilities=h.capabilities(), price_source=_UnsafeGpuPrice(), allow_unauthenticated_execute=True
        ).app
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            response = await c.post(
                "/execute",
                json={"job_id": "j1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"},
            )

        assert response.status_code == 200
        assert b'"gpu_type":null' in response.content
        assert b"user:secret@example.invalid" not in response.content

    @pytest.mark.asyncio
    async def test_failed_handler_retains_cost_estimate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Stub()

        async def _boom(job: Job, input: Input | None = None) -> list[BytesArtifact]:  # noqa: A002
            raise RuntimeError("OOM")

        monkeypatch.setattr(h, "handle", _boom)
        app = EdgeApp(
            handler=h, capabilities=h.capabilities(), price_source=_MeasuredPrice(), allow_unauthenticated_execute=True
        ).app
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            response = await c.post(
                "/execute",
                json={"job_id": "j1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"},
            )

        assert response.status_code == 500
        estimate = response.json()["metrics"]["cost_estimate"]
        assert estimate["basis"] == "measured"
        assert estimate["gpu_type"] == "L4"
        assert estimate["rate_per_hour"] == 0.69

    @pytest.mark.asyncio
    async def test_missing_cost_cannot_expose_static_basis(self) -> None:
        h = _Stub()
        app = EdgeApp(
            handler=h,
            capabilities=h.capabilities(),
            price_source=_MissingStaticPrice(),
            allow_unauthenticated_execute=True,
        ).app
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            response = await c.post(
                "/execute",
                json={"job_id": "j1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"},
            )

        assert response.status_code == 200
        assert b'"cost":null' in response.content
        assert b'"basis":"unknown"' in response.content

    @pytest.mark.asyncio
    async def test_pricing_failure_is_non_blocking_and_sanitised(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        h = _Stub()
        app = EdgeApp(
            handler=h, capabilities=h.capabilities(), price_source=_BrokenPrice(), allow_unauthenticated_execute=True
        ).app
        transport = ASGITransport(app=app)
        with caplog.at_level("WARNING", logger="acheron.worker_sdk._edge_http"):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                response = await c.post(
                    "/execute",
                    json={"job_id": "j1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"},
                )

        assert response.status_code == 200
        estimate = response.content
        assert b'"basis":"unknown"' in estimate
        assert b"password=secret" not in estimate
        assert any("RuntimeError: <no message>" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_execute_error_sanitises_secrets_in_message(
        self,
        app_handler: tuple[FastAPI, _Stub],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SEC-012: a handler exception whose message contains a credential
        pattern must not surface that pattern in the 500 body. The full
        message is preserved in ``logger.exception`` for the operator.
        """
        app, h = app_handler

        async def _boom(job: Job, input: Input | None = None) -> list[BytesArtifact]:  # noqa: A002
            msg = (
                "DB connect failed password=foo client_secret=bar access_token:baz "
                'AWS_SECRET_ACCESS_KEY=qux {"client_secret":"quux"} at /runpod-volume/models/qwen3'
            )
            raise RuntimeError(msg)

        monkeypatch.setattr(h, "handle", _boom)
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/execute",
                json={"job_id": "j1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"},
            )
        assert r.status_code == 500
        body = r.json()
        assert "password=foo" not in body["error"]
        assert "client_secret=bar" not in body["error"]
        assert "access_token:baz" not in body["error"]
        assert "AWS_SECRET_ACCESS_KEY=qux" not in body["error"]
        assert "quux" not in body["error"]
        assert body["error"].startswith("RuntimeError:")

    @pytest.mark.asyncio
    async def test_dispatch_propagates_keyboard_interrupt(
        self,
        app_handler: tuple[FastAPI, _Stub],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``KeyboardInterrupt`` from the handler propagates out of the
        ``/execute`` route (and ``_dispatch``) rather than being wrapped in a
        500 — the operator's Ctrl-C during a long handler must reach uvicorn's
        signal handler rather than being logged as a normal job failure.
        """
        app, h = app_handler

        async def _interrupt(job: Job, input: Input | None = None) -> list[BytesArtifact]:  # noqa: A002
            raise KeyboardInterrupt

        monkeypatch.setattr(h, "handle", _interrupt)
        transport = ASGITransport(app=app, raise_app_exceptions=True)
        with pytest.raises(KeyboardInterrupt):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                await c.post(
                    "/execute",
                    json={"job_id": "j1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"},
                )

    @pytest.mark.asyncio
    async def test_execute_metrics_part_emits_null_cost_estimate(self, app_handler: tuple[FastAPI, _Stub]) -> None:
        """When no price source is wired, metrics preserve the absence of an estimate."""
        import json

        app, _ = app_handler
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/execute",
                json={
                    "job_id": "j1",
                    "job_type": "tts",
                    "payload": {"chunks": [{"text": "hi"}]},
                    "chapter_id": "ch1",
                },
            )
        assert r.status_code == 200
        # Pull the last part (application/json metrics) out of the multipart body.
        body = r.content
        boundary = r.headers["content-type"].split("boundary=")[-1]
        parts = body.split(f"--{boundary}".encode())
        json_part = next(p for p in parts if b"application/json" in p)
        # The JSON payload begins after the headers (blank line) and ends before \r\n.
        json_bytes = json_part.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0]
        metrics = json.loads(json_bytes)
        assert metrics["cost_estimate"] is None
        assert "unknown" not in json_bytes.decode("utf-8")

    @pytest.mark.asyncio
    async def test_execute_response_carries_x_acheron_metadata_per_artifact(self) -> None:
        """TEST-013: each artifact in the multipart response must carry an
        ``X-Acheron-Metadata`` header whose value is the JSON-serialized
        ``artifact.metadata`` dict — the build-side mirror of the request-side
        parser (CORR-013)."""
        import json

        class _MetaStub(WorkerHandler):
            def capabilities(self) -> WorkerCapabilities:
                return WorkerCapabilities(
                    worker_type=WorkerType.TTS,
                    supported_languages_in=frozenset({"en"}),
                    supported_languages_out=frozenset({"en"}),
                    supported_formats_in=frozenset({"text"}),
                    supported_formats_out=frozenset({"wav"}),
                    max_payload_bytes=None,
                    batch_capable=False,
                    model_source="huggingface:test",
                )

            async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:  # noqa: A002
                return [
                    BytesArtifact(
                        filename="out.wav",
                        content_type="audio/wav",
                        data=b"audio",
                        metadata={"sequence_id": 0, "chapter_id": "ch1"},
                    )
                ]

        h = _MetaStub()
        app = EdgeApp(handler=h, capabilities=h.capabilities(), allow_unauthenticated_execute=True).app
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/execute",
                json={
                    "job_id": "j1",
                    "job_type": "tts",
                    "payload": {"chunks": [{"text": "hi"}]},
                    "chapter_id": "ch1",
                },
            )
        assert r.status_code == 200
        # Find the audio part and parse its X-Acheron-Metadata header.
        body = r.content
        boundary = r.headers["content-type"].split("boundary=")[-1]
        audio_part = next(p for p in body.split(f"--{boundary}".encode()) if b"audio/wav" in p)
        header_block = audio_part.split(b"\r\n\r\n", 1)[0].decode("utf-8")
        meta_line = next(line for line in header_block.split("\r\n") if line.startswith("X-Acheron-Metadata:"))
        payload = meta_line.split(":", 1)[1].strip()
        assert json.loads(payload) == {"sequence_id": 0, "chapter_id": "ch1"}


class TestEdgeExecuteAuth:
    """OBS-010: /execute must require a Bearer token when registration_token is configured."""

    @pytest.fixture
    def app_with_token(self) -> tuple[FastAPI, _Stub]:
        h = _Stub()
        app = EdgeApp(handler=h, capabilities=h.capabilities(), registration_token="test-secret-32-chars-min-aaaa").app
        return app, h

    @pytest.mark.asyncio
    async def test_execute_rejects_missing_authorization_header(self, app_with_token: tuple[FastAPI, _Stub]) -> None:
        app, h = app_with_token
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/execute",
                json={"job_id": "j1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"},
            )
        assert r.status_code == 401
        assert h.calls == 0

    @pytest.mark.asyncio
    async def test_execute_rejects_wrong_authorization_token(self, app_with_token: tuple[FastAPI, _Stub]) -> None:
        app, h = app_with_token
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/execute",
                headers={"Authorization": "Bearer wrong-token"},
                json={"job_id": "j1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"},
            )
        assert r.status_code == 401
        assert h.calls == 0

    @pytest.mark.asyncio
    async def test_execute_accepts_correct_bearer_token(self, app_with_token: tuple[FastAPI, _Stub]) -> None:
        app, h = app_with_token
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/execute",
                headers={"Authorization": "Bearer test-secret-32-chars-min-aaaa"},
                json={"job_id": "j1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"},
            )
        assert r.status_code == 200
        assert h.calls == 1

    @pytest.mark.asyncio
    async def test_execute_rejects_missing_configured_token(self) -> None:
        h = _Stub()
        app = EdgeApp(handler=h, capabilities=h.capabilities()).app
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/execute",
                json={"job_id": "j1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"},
            )
        assert r.status_code == 401
        assert h.calls == 0

    @pytest.mark.asyncio
    async def test_execute_open_mode_requires_explicit_opt_in(self) -> None:
        h = _Stub()
        app = EdgeApp(handler=h, capabilities=h.capabilities(), allow_unauthenticated_execute=True).app
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/execute",
                json={"job_id": "j1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"},
            )
        assert r.status_code == 200
        assert h.calls == 1

    @pytest.mark.asyncio
    async def test_health_and_capabilities_remain_unauthenticated(self, app_with_token: tuple[FastAPI, _Stub]) -> None:
        app, _ = app_with_token
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            h = await c.get("/health")
            caps = await c.get("/capabilities")
        assert h.status_code == 200
        assert caps.status_code == 200

    @pytest.mark.asyncio
    async def test_multipart_500_body_sanitised(
        self, app_handler: tuple[FastAPI, _Stub], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SEC-019: the /execute multipart 500 body must not echo the raw exception message.

        The multipart parser catches ``(WorkerError, ValueError, KeyError)`` and
        returns a JobResult-shaped JSON body. ``sanitise_exc_message`` strips
        traceback fragments and credential-shaped ``key=value`` patterns, so
        downstream consumers never see a leaked path or token.
        """
        from acheron.worker_sdk import _edge_http as edge_module

        app, _ = app_handler

        def _boom(_self: Any, _request: Any) -> Any:
            msg = (
                "/runpod-volume/secrets/api_key=abc123 not found\n"
                "  File '/etc/passwd'\nTraceback (most recent call last):"
            )
            raise ValueError(msg)

        monkeypatch.setattr(edge_module.EdgeApp, "_parse_multipart_request", _boom)
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/execute",
                files={"field": ("f.txt", b"hi", "text/plain")},
            )
        assert r.status_code == 500
        body = r.json()
        # Sanitised message retains class + first line, drops traceback + creds.
        assert "api_key=abc123" not in body["error"]
        assert "/etc/passwd" not in body["error"]
        assert "Traceback" not in body["error"]
        assert body["error"].startswith("WorkerError:")
