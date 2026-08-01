"""Tests for capability discovery route."""

import pytest


class TestCapabilitiesRoute:
    @pytest.mark.asyncio
    async def test_get_capabilities(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert len(data["language_pairs"]) >= 2

    @pytest.mark.asyncio
    async def test_get_capabilities_filtered_by_dest(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/capabilities", params={"dest": "es"})
        assert response.status_code == 200
        for pair in response.json()["language_pairs"]:
            assert pair["dst"] == "es"

    @pytest.mark.asyncio
    async def test_get_capabilities_filtered_by_src(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/capabilities", params={"src": "en"})
        assert response.status_code == 200
        for pair in response.json()["language_pairs"]:
            assert pair["src"] == "en"


class TestTypedCapabilitiesRoute:
    @pytest.mark.asyncio
    async def test_public_projection_drops_untrusted_registration_values(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/workers",
            json={
                "worker_id": "https://token:secret@evil/../worker",
                "endpoint": "http://worker:8000",
                "transport": "http",
                "capabilities": {
                    "worker_type": "tts",
                    "supported_languages_in": ["en", "token TOPSECRET", "../etc/passwd"],
                    "supported_languages_out": ["en"],
                    "supported_formats_in": ["audio/wav", "https://secret.example"],
                    "supported_formats_out": ["audio/wav", "api key TOPSECRET"],
                },
            },
        )
        assert response.status_code == 201
        assert response.json()["worker_id"] == "<redacted>"

        typed = await client.get("/capabilities", params={"type": "tts"})
        assert typed.status_code == 200
        worker = next(item for item in typed.json()["workers"] if item["worker_id"] == "<redacted>")
        assert worker["supported_languages_in"] == ["en"]
        assert worker["supported_formats_in"] == ["audio/wav"]
        assert worker["supported_formats_out"] == ["audio/wav"]

    @pytest.mark.asyncio
    async def test_tts_speakers_drop_unsafe_metadata(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/workers",
            json={
                "worker_id": "tts-speakers",
                "endpoint": "http://secret-worker:8000",
                "transport": "http",
                "capabilities": {
                    "worker_type": "tts",
                    "supported_languages_in": ["en"],
                    "supported_languages_out": ["en"],
                    "metadata": {
                        "speakers": [
                            "Ryan",
                            " https://provider.example/token=secret ",
                            "token=secret",
                            "provider/aws",
                            "A valid name",
                            "bad:name",
                            12,
                        ]
                    },
                },
            },
        )
        assert response.status_code == 201
        response = await client.get("/capabilities", params={"type": "tts"})
        assert response.status_code == 200
        speakers = next(
            worker["speakers"] for worker in response.json()["workers"] if worker["worker_id"] == "tts-speakers"
        )
        assert speakers == ["A valid name", "Ryan"]

    @pytest.mark.asyncio
    async def test_type_tts_returns_sorted_allowlisted_inventory(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/capabilities", params={"type": "tts"})
        assert response.status_code == 200
        data = response.json()
        assert data["language_pairs"] == []
        assert [worker["worker_id"] for worker in data["workers"]] == ["tts-1", "tts-2"]
        assert data["workers"][0]["worker_type"] == "tts"
        assert set(data["workers"][0]) == {
            "worker_id",
            "worker_type",
            "supported_languages_in",
            "supported_languages_out",
            "supported_formats_in",
            "supported_formats_out",
            "max_payload_bytes",
            "max_input_tokens",
            "batch_capable",
            "speakers",
        }
        assert "metadata" not in data["workers"][0]
        assert "model_source" not in data["workers"][0]

    @pytest.mark.asyncio
    async def test_type_asr_returns_inventory(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/capabilities", params={"type": "asr"})
        assert response.status_code == 200
        data = response.json()
        assert data["language_pairs"] == []
        assert [worker["worker_id"] for worker in data["workers"]] == ["asr-capability-1"]
        assert data["workers"][0]["worker_type"] == "asr"
        assert "model_source" not in data["workers"][0]

    @pytest.mark.asyncio
    async def test_type_translation_returns_sorted_inventory(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/capabilities", params={"type": "translation"})
        assert response.status_code == 200
        data = response.json()
        assert data["language_pairs"] == []
        assert [worker["worker_id"] for worker in data["workers"]] == ["trans-1", "trans-2"]
        for worker in data["workers"]:
            assert worker["worker_type"] == "translation"
            assert "model_source" not in worker

    @pytest.mark.asyncio
    async def test_invalid_type_returns_422(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/capabilities", params={"type": "bogus"})
        assert response.status_code == 422
        body = response.json()
        assert "detail" in body
        assert "bogus" not in body["detail"]

    @pytest.mark.asyncio
    async def test_type_with_src_returns_422(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/capabilities", params={"type": "tts", "src": "en"})
        assert response.status_code == 422
        body = response.json()
        assert "detail" in body
        assert "type" in body["detail"].lower()

    @pytest.mark.asyncio
    async def test_type_with_dest_returns_422(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/capabilities", params={"type": "tts", "dest": "es"})
        assert response.status_code == 422
        body = response.json()
        assert "detail" in body
        assert "type" in body["detail"].lower()


class TestLanguagePairCapabilitiesRoute:
    @pytest.mark.asyncio
    async def test_unknown_src_returns_422_with_sorted_supported_list(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/capabilities", params={"src": "xx"})
        assert response.status_code == 422
        body = response.json()
        assert "detail" in body
        detail = body["detail"]
        assert "xx" not in detail
        # Extract the sorted list after 'supported sources: '.
        prefix = "supported sources: "
        assert prefix in detail
        listed = detail.split(prefix, 1)[1].strip()
        assert listed == "de, en, es, fr"

    @pytest.mark.asyncio
    async def test_unknown_dest_returns_422_with_sorted_supported_list(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/capabilities", params={"dest": "xx"})
        assert response.status_code == 422
        body = response.json()
        assert "detail" in body
        detail = body["detail"]
        assert "xx" not in detail
        # Extract the sorted list after 'supported targets: '.
        prefix = "supported targets: "
        assert prefix in detail
        listed = detail.split(prefix, 1)[1].strip()
        assert listed == "de, en, es, fr"

    @pytest.mark.asyncio
    async def test_known_but_empty_pair_returns_200_with_empty_pairs(self, client) -> None:  # type: ignore[no-untyped-def]
        # en→de is a valid filter (both languages are supported) but no
        # registered worker bridges that pair, so the result is 200 with
        # language_pairs=[] rather than 422.
        response = await client.get("/capabilities", params={"src": "en", "dest": "de"})
        assert response.status_code == 200
        data = response.json()
        assert data["language_pairs"] == []
        assert data["workers"] == []
