"""Tests for the internal RunPod client wrapper.

Uses an injected fake ``runpod.Endpoint`` to avoid the heavy SDK dependency.
"""

import pytest

from acheron.worker_sdk._runpod_client import RunPodClient, RunPodJobResult


class _FakeEndpoints:
    """Simulates runpod.Endpoint(id).run + status + output()."""

    def __init__(self, *, output: object | None = None, exc: Exception | None = None) -> None:
        self._output = output
        self._exc = exc
        self.status_calls = 0

    def run(self, input: dict) -> "_FakeRun":  # noqa: A002
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

    def _factory(endpoint_id: str, *, api_key: str) -> _FakeEndpoints:
        return fake

    monkeypatch.setattr(mod, "_open_endpoint", _factory)


class TestRunPodClient:
    @pytest.mark.asyncio
    async def test_returns_artifacts_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeEndpoints(output={"artifacts": [{"filename": "out.wav", "data": "AAEC"}]})
        _patch_endpoint(monkeypatch, fake)
        client = RunPodClient(
            api_key="k", endpoint_id="eid", execution_timeout_s=60.0
        )
        result = await client.run(input={"text": "hi"})
        assert isinstance(result, RunPodJobResult)
        assert result.artifacts[0]["filename"] == "out.wav"
        assert result.gpu_seconds is not None
        assert result.gpu_seconds > 0.0

    @pytest.mark.asyncio
    async def test_propagates_timeout_as_error_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _FakeEndpoints(exc=TimeoutError("slow"))
        _patch_endpoint(monkeypatch, fake)
        client = RunPodClient(
            api_key="k", endpoint_id="eid", execution_timeout_s=0.0
        )
        with pytest.raises(TimeoutError):
            await client.run(input={"text": "hi"})

    @pytest.mark.asyncio
    async def test_endpoint_id_and_api_key_passed_to_factory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen_args: dict[str, str] = {}

        def _factory(endpoint_id: str, *, api_key: str) -> _FakeEndpoints:
            seen_args["endpoint_id"] = endpoint_id
            seen_args["api_key"] = api_key
            return _FakeEndpoints(output={"artifacts": []})

        import acheron.worker_sdk._runpod_client as mod

        monkeypatch.setattr(mod, "_open_endpoint", _factory)
        client = RunPodClient(
            api_key="rk_secret", endpoint_id="eid", execution_timeout_s=60.0
        )
        await client.run(input={})
        assert seen_args == {"endpoint_id": "eid", "api_key": "rk_secret"}
