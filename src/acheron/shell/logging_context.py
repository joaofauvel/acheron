"""Per-task logging context (job_id, request_id).

A :class:`logging.Filter` attaches the current :class:`ContextVar` values to
every :class:`logging.LogRecord` so existing ``logger.info(...)`` calls
emitting a free-form message can still be filtered/aggregated by job_id or
request_id downstream.
"""

from __future__ import annotations

import contextlib
import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

job_id_var: ContextVar[str | None] = ContextVar("acheron_job_id", default=None)
request_id_var: ContextVar[str | None] = ContextVar("acheron_request_id", default=None)


@contextlib.contextmanager
def bind_job_id(job_id: str) -> Iterator[None]:
    """Set ``job_id_var`` for the duration of the ``with`` block."""
    token = job_id_var.set(job_id)
    try:
        yield
    finally:
        job_id_var.reset(token)


@contextlib.contextmanager
def bind_request_id(request_id: str) -> Iterator[None]:
    """Set ``request_id_var`` for the duration of the ``with`` block."""
    token = request_id_var.set(request_id)
    try:
        yield
    finally:
        request_id_var.reset(token)


class ContextFilter(logging.Filter):
    """Attach ``job_id`` and ``request_id`` from the current context to each record."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Set ``record.job_id`` and ``record.request_id`` from the current context."""
        record.job_id = job_id_var.get()
        record.request_id = request_id_var.get()
        return True
