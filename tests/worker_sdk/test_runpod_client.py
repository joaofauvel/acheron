"""Tests for the internal RunPod client wrapper.

Uses an injected fake ``runpod.Endpoint`` to avoid the heavy SDK dependency.
"""

import httpx
import pytest

from acheron.worker_sdk._runpod_client import RunPodClient, RunPodJobResult


class _FakeEndpoints:
    """Simulates runpod.Endpoint(id).run + status + output()."""

    def __init__(self, *, output: object | None = None, exc: Exception | None = None) -> None:
        self._output = output
        self._exc = exc
        self.status_calls = 0

    def run(self, input: dict) -> _FakeRun:  # noqa: A002
        return _FakeRun(output=self._output, exc=self._exc)


class _FakeRun:
    def __init__(self, *, output: object | None, exc: Exception | None) -> None:
        self._output = output
        self._exc = exc

    def status(self) -> str:
        return "COMPLETED"

    def output(self, timeout: float | None = None) -> object:
        if self._exc:
            raise self._exc
        return self._output


def _patch_endpoint(monkeypatch: pytest.MonkeyPatch, fake: _FakeEndpoints) -> None:
    import acheron.worker_sdk._runpod_client as mod

    def _factory(endpoint_id: str, *, api_key: str, base_url: str | None = None) -> _FakeEndpoints:
        return fake

    monkeypatch.setattr(mod, "_open_endpoint", _factory)


class TestRunPodClient:
    @pytest.mark.asyncio
    async def test_returns_artifacts_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeEndpoints(output={"artifacts": [{"filename": "out.wav", "data": "AAEC"}]})
        _patch_endpoint(monkeypatch, fake)
        client = RunPodClient(api_key="k", endpoint_id="eid", execution_timeout_s=60.0)
        result = await client.run(payload={"text": "hi"})
        assert isinstance(result, RunPodJobResult)
        assert result.artifacts[0]["filename"] == "out.wav"
        assert result.gpu_seconds is not None
        assert result.gpu_seconds > 0.0

    @pytest.mark.asyncio
    async def test_propagates_timeout_as_error_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeEndpoints(exc=TimeoutError("slow"))
        _patch_endpoint(monkeypatch, fake)
        client = RunPodClient(api_key="k", endpoint_id="eid", execution_timeout_s=0.0)
        with pytest.raises(TimeoutError):
            await client.run(payload={"text": "hi"})

    @pytest.mark.asyncio
    async def test_endpoint_id_and_api_key_passed_to_factory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen_args: dict[str, str] = {}

        def _factory(endpoint_id: str, *, api_key: str, base_url: str | None = None) -> _FakeEndpoints:
            seen_args["endpoint_id"] = endpoint_id
            seen_args["api_key"] = api_key
            seen_args["base_url"] = base_url  # type: ignore[assignment]
            return _FakeEndpoints(output={"artifacts": []})

        import acheron.worker_sdk._runpod_client as mod

        monkeypatch.setattr(mod, "_open_endpoint", _factory)
        client = RunPodClient(api_key="rk_secret", endpoint_id="eid", execution_timeout_s=60.0)
        await client.run(payload={})
        assert seen_args == {"endpoint_id": "eid", "api_key": "rk_secret", "base_url": None}

    @pytest.mark.asyncio
    async def test_failed_status_raises_worker_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from acheron.core.errors import WorkerError

        fake = _FakeEndpoints(output={"status": "FAILED", "error": "GPU OOM"})
        _patch_endpoint(monkeypatch, fake)
        client = RunPodClient(api_key="k", endpoint_id="eid", execution_timeout_s=60.0)
        with pytest.raises(WorkerError, match="GPU OOM"):
            await client.run(payload={})

    @pytest.mark.asyncio
    async def test_cancelled_status_raises_worker_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from acheron.core.errors import WorkerError

        fake = _FakeEndpoints(output={"status": "CANCELLED"})
        _patch_endpoint(monkeypatch, fake)
        client = RunPodClient(api_key="k", endpoint_id="eid", execution_timeout_s=60.0)
        with pytest.raises(WorkerError, match="CANCELLED"):
            await client.run(payload={})

    @pytest.mark.asyncio
    async def test_open_endpoint_failure_logs_and_reraises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """OBS-006: when ``_open_endpoint`` raises (e.g. a misconfigured key or
        a typo'd endpoint id), the error is logged with the endpoint_id and
        exception class, and the original exception is re-raised so the caller
        can decide how to recover.
        """
        import logging

        def _factory(endpoint_id: str, *, api_key: str, base_url: str | None = None) -> _FakeEndpoints:
            msg = "endpoint id not found"
            raise httpx.HTTPError(msg)

        import acheron.worker_sdk._runpod_client as mod

        monkeypatch.setattr(mod, "_open_endpoint", _factory)
        client = RunPodClient(api_key="rk", endpoint_id="eid-bad", execution_timeout_s=60.0)
        with (
            caplog.at_level(logging.ERROR, logger="acheron.worker_sdk._runpod_client"),
            pytest.raises(httpx.HTTPError, match="endpoint id not found"),
        ):
            await client.run(payload={})
        assert any("eid-bad" in r.message and "HTTPError" in r.message for r in caplog.records), (
            f"expected log with endpoint_id+exc_class, got: {[r.message for r in caplog.records]}"
        )

    @pytest.mark.asyncio
    async def test_endpoint_run_failure_logs_and_reraises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """OBS-006: when ``endpoint.run(payload)`` raises (transport / SDK
        failure), the error is logged with the endpoint_id and exception
        class, and re-raised.
        """
        import logging

        class _BoomEndpoint:
            def run(self, payload: dict) -> _FakeRun:
                raise httpx.HTTPError("transient")

        def _factory(endpoint_id: str, *, api_key: str, base_url: str | None = None) -> _BoomEndpoint:
            return _BoomEndpoint()

        import acheron.worker_sdk._runpod_client as mod

        monkeypatch.setattr(mod, "_open_endpoint", _factory)
        client = RunPodClient(api_key="rk", endpoint_id="eid-boom", execution_timeout_s=60.0)
        with (
            caplog.at_level(logging.ERROR, logger="acheron.worker_sdk._runpod_client"),
            pytest.raises(httpx.HTTPError, match="transient"),
        ):
            await client.run(payload={})
        assert any("eid-boom" in r.message and "HTTPError" in r.message for r in caplog.records), (
            f"expected log with endpoint_id+exc_class, got: {[r.message for r in caplog.records]}"
        )
