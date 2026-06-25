"""Direct unit tests for the multipart parser used by ``HttpWorker._parse_multipart``."""

from __future__ import annotations

import json

import pytest

from acheron.core.errors import WorkerError
from acheron.shell.transports._multipart import _parse_multipart_parts

_BOUNDARY = "acheron-test"


def _build_body(parts: list[tuple[str, str, bytes, dict[str, str] | None]]) -> bytes:
    """Build a ``multipart/mixed`` body from a list of ``(filename, content_type, data, metadata)`` tuples."""
    out = b""
    for filename, ctype, data, metadata in parts:
        meta_header = ""
        if metadata is not None:
            meta_header = f"X-Acheron-Metadata: {json.dumps(metadata, separators=(',', ':'))}\r\n"
        out += (
            (
                f"--{_BOUNDARY}\r\n"
                f'Content-Disposition: attachment; filename="{filename}"\r\n'
                f"Content-Type: {ctype}\r\n"
                f"{meta_header}\r\n"
            ).encode()
            + data
            + b"\r\n"
        )
    out += f"--{_BOUNDARY}--\r\n".encode()
    return out


class TestParseMultipartPartsMetadata:
    """CORR-013 — per-part ``X-Acheron-Metadata`` header is preserved on the parsed part."""

    def test_parses_x_acheron_metadata_header(self) -> None:
        body = _build_body(
            [
                ("ch1.txt", "text/plain", b"hello", {"key": "value"}),
            ]
        )
        parts, _ = _parse_multipart_parts(
            f"multipart/mixed; boundary={_BOUNDARY}",
            body,
        )
        assert len(parts) == 1
        assert parts[0].metadata == {"key": "value"}
        assert parts[0].filename == "ch1.txt"
        assert parts[0].content_type == "text/plain"
        assert parts[0].data == b"hello"

    def test_missing_metadata_header_yields_empty_dict(self) -> None:
        body = _build_body(
            [
                ("ch1.txt", "text/plain", b"hello", None),
            ]
        )
        parts, _ = _parse_multipart_parts(
            f"multipart/mixed; boundary={_BOUNDARY}",
            body,
        )
        assert len(parts) == 1
        assert parts[0].metadata == {}


class TestParseMultipartPartsMetricsSelection:
    """CORR-030 — metrics part is identified by ``X-Acheron-Part-Name: metrics`` header."""

    def test_selects_metrics_part_by_part_name_header(self) -> None:
        body = (
            f"--{_BOUNDARY}\r\n"
            f'Content-Disposition: attachment; filename="ch1.txt"\r\n'
            f"Content-Type: text/plain\r\n\r\n"
            f"transcript\r\n"
            f"--{_BOUNDARY}\r\n"
            f"Content-Type: application/json\r\n\r\n"
            f'{{"chapter_id":"ch1"}}\r\n'
            f"--{_BOUNDARY}\r\n"
            f"Content-Type: application/json\r\n"
            f"X-Acheron-Part-Name: metrics\r\n\r\n"
            f'{{"duration_seconds": 1.5}}\r\n'
            f"--{_BOUNDARY}--\r\n"
        ).encode()
        parts, metrics = _parse_multipart_parts(
            f"multipart/mixed; boundary={_BOUNDARY}",
            body,
        )
        assert len(parts) == 1
        assert parts[0].content_type == "text/plain"
        assert metrics.duration_seconds == 1.5

    def test_falls_back_to_first_json_when_no_part_name_header(self) -> None:
        body = (
            f'--{_BOUNDARY}\r\nContent-Type: application/json\r\n\r\n{{"duration_seconds": 2.0}}\r\n--{_BOUNDARY}--\r\n'
        ).encode()
        _, metrics = _parse_multipart_parts(
            f"multipart/mixed; boundary={_BOUNDARY}",
            body,
        )
        assert metrics.duration_seconds == 2.0

    def test_multiple_metrics_parts_with_header_raises(self) -> None:
        body = (
            f"--{_BOUNDARY}\r\n"
            f"Content-Type: application/json\r\n"
            f"X-Acheron-Part-Name: metrics\r\n\r\n"
            f'{{"duration_seconds": 1.0}}\r\n'
            f"--{_BOUNDARY}\r\n"
            f"Content-Type: application/json\r\n"
            f"X-Acheron-Part-Name: metrics\r\n\r\n"
            f'{{"duration_seconds": 2.0}}\r\n'
            f"--{_BOUNDARY}--\r\n"
        ).encode()
        with pytest.raises(WorkerError, match="metrics"):
            _parse_multipart_parts(
                f"multipart/mixed; boundary={_BOUNDARY}",
                body,
            )


class TestParseMultipartPartsBoundary:
    """CORR-028 — missing ``boundary=`` raises ``WorkerError``."""

    def test_missing_boundary_raises_worker_error(self) -> None:
        with pytest.raises(WorkerError, match="missing boundary"):
            _parse_multipart_parts(
                "multipart/mixed; charset=utf-8",
                b"--x\r\nContent-Type: text/plain\r\n\r\nhello\r\n--x--\r\n",
            )
