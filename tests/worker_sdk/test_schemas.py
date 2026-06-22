"""Tests for the /execute request/response pydantic schemas."""

import pytest
from pydantic import ValidationError

from acheron.worker_sdk.schemas import ExecuteError, ExecuteRequest


class TestExecuteRequest:
    def test_full_payload_validates(self) -> None:
        body = ExecuteRequest.model_validate(
            {
                "job_id": "j-1",
                "job_type": "tts",
                "payload": {
                    "chapter_id": "ch1",
                    "chunks": [{"text": "hola"}],
                    "target_language": "es",
                },
                "chapter_id": "ch1",
                "sequence_ids": [0, 1],
            }
        )
        assert body.job_id == "j-1"
        assert body.job_type == "tts"
        assert body.chapter_id == "ch1"
        assert body.sequence_ids == [0, 1]

    def test_sequence_ids_optional(self) -> None:
        body = ExecuteRequest.model_validate(
            {"job_id": "j-1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"}
        )
        assert body.sequence_ids is None

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            ExecuteRequest.model_validate(
                {
                    "job_id": "j",
                    "job_type": "tts",
                    "payload": {},
                    "chapter_id": "c",
                    "boom": 1,
                }
            )


class TestExecuteError:
    def test_shape(self) -> None:
        e = ExecuteError.model_validate({"status": "failed", "error": "model OOM"})
        assert e.status == "failed"
        assert e.error == "model OOM"
