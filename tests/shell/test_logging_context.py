"""Tests for the per-task logging context (job_id / request_id)."""

from __future__ import annotations

import logging

import pytest

from acheron.shell.logging_context import (
    ContextFilter,
    bind_job_id,
    bind_request_id,
    job_id_var,
    request_id_var,
)


class TestContextFilter:
    def test_filter_attaches_job_id(self) -> None:
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", None, None)
        with bind_job_id("job-abc"):
            ContextFilter().filter(record)
        assert record.job_id == "job-abc"  # type: ignore[attr-defined]

    def test_filter_attaches_request_id(self) -> None:
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", None, None)
        with bind_request_id("req-xyz"):
            ContextFilter().filter(record)
        assert record.request_id == "req-xyz"  # type: ignore[attr-defined]

    def test_filter_default_to_none_when_unbound(self) -> None:
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", None, None)
        job_id_var.set(None)
        request_id_var.set(None)
        ContextFilter().filter(record)
        assert record.job_id is None  # type: ignore[attr-defined]
        assert record.request_id is None  # type: ignore[attr-defined]

    def test_logger_emits_record_with_job_id(self, caplog: pytest.LogCaptureFixture) -> None:
        logger = logging.getLogger("test.logging_context")
        logger.addFilter(ContextFilter())
        try:
            with bind_job_id("job-42"), caplog.at_level(logging.INFO, logger="test.logging_context"):
                logger.info("hello %s", "world")
        finally:
            logger.removeFilter(ContextFilter())
        assert len(caplog.records) == 1
        assert caplog.records[0].job_id == "job-42"  # type: ignore[attr-defined]
