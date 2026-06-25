"""Tests for the shared chunks.json helpers in workers._shared (8c)."""

from __future__ import annotations

import json

import pytest
from workers._shared_utils import Chunk, parse_chunks_json, validate_chunk_fields

from acheron.core.errors import WorkerError
from acheron.worker_sdk.inputs import BytesInput


def _input(raw: bytes) -> BytesInput:
    return BytesInput(content_type="application/json", data=raw)


class TestParseChunksJson:
    @pytest.mark.asyncio
    async def test_valid_input_returns_chunks(self) -> None:
        chunks_in = [
            {"chapter_id": "ch1", "sequence_id": 0, "text": "hi"},
            {"chapter_id": "ch1", "sequence_id": 1, "text": "bye", "instruct": "calm"},
        ]
        out = await parse_chunks_json(_input(json.dumps(chunks_in).encode("utf-8")))
        assert out == [
            Chunk(chapter_id="ch1", sequence_id=0, text="hi", instruct=""),
            Chunk(chapter_id="ch1", sequence_id=1, text="bye", instruct="calm"),
        ]

    @pytest.mark.asyncio
    async def test_empty_array_returns_empty_list(self) -> None:
        out = await parse_chunks_json(_input(b"[]"))
        assert out == []

    @pytest.mark.asyncio
    async def test_malformed_json_raises(self) -> None:
        with pytest.raises(WorkerError, match="not valid JSON"):
            await parse_chunks_json(_input(b"not json {{{"))


class TestValidateChunkFields:
    def test_valid_chunk_returns_chunk(self) -> None:
        chunk = validate_chunk_fields({"chapter_id": "ch1", "sequence_id": 0, "text": "hi"})
        assert chunk == Chunk(chapter_id="ch1", sequence_id=0, text="hi", instruct="")

    def test_missing_required_field_raises(self) -> None:
        with pytest.raises(WorkerError, match="text is required"):
            validate_chunk_fields({"chapter_id": "ch1", "sequence_id": 0})

    def test_invalid_value_raises(self) -> None:
        with pytest.raises(WorkerError, match="sequence_id is required"):
            validate_chunk_fields({"chapter_id": "ch1", "sequence_id": "zero", "text": "x"})
