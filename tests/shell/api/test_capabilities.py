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
