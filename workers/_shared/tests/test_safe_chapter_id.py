"""Tests for workers._shared.safe_chapter_id (8b)."""

from __future__ import annotations

import pytest
from workers._shared_utils import safe_chapter_id

from acheron.core.errors import WorkerError


class TestSafeChapterId:
    @pytest.mark.parametrize("chapter_id", ["第1章", "café", "Ω"])
    def test_unicode_chapter_id_passes(self, chapter_id: str) -> None:
        assert safe_chapter_id(chapter_id) == chapter_id

    def test_plain_chapter_id_passes(self) -> None:
        assert safe_chapter_id("ch1") == "ch1"
        assert safe_chapter_id("chapter_001") == "chapter_001"

    def test_blank_raises(self) -> None:
        with pytest.raises(WorkerError):
            safe_chapter_id("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(WorkerError):
            safe_chapter_id("   ")

    def test_nul_byte_raises(self) -> None:
        with pytest.raises(WorkerError):
            safe_chapter_id("ch\x001")

    def test_newline_raises(self) -> None:
        with pytest.raises(WorkerError):
            safe_chapter_id("ch1\n")

    def test_tab_raises(self) -> None:
        with pytest.raises(WorkerError):
            safe_chapter_id("ch1\t")

    @pytest.mark.parametrize("sep", ["/", "\\"])
    def test_path_separator_raises(self, sep: str) -> None:
        with pytest.raises(WorkerError):
            safe_chapter_id(f"ch{sep}1")

    def test_dot_raises(self) -> None:
        with pytest.raises(WorkerError):
            safe_chapter_id(".")

    def test_double_dot_raises(self) -> None:
        with pytest.raises(WorkerError):
            safe_chapter_id("..")

    def test_double_dot_component_raises(self) -> None:
        with pytest.raises(WorkerError):
            safe_chapter_id("ch1/..")

    def test_too_long_raises(self) -> None:
        with pytest.raises(WorkerError):
            safe_chapter_id("a" * 200)
