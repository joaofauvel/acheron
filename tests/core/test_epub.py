"""Tests for shared EPUB chapter discovery."""

import zipfile
from pathlib import Path

import pytest

from acheron.core.epub import read_epub_chapters


def _write_epub(path: Path) -> None:
    container = (
        """<?xml version=\"1.0\"?><container><rootfiles><rootfile full-path=\"book.opf\"/></rootfiles></container>"""
    )
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf">'
        '<manifest><item id="one" href="one.xhtml"/>'
        '<item id="empty" href="empty.xhtml"/><item id="two" href="two.xhtml"/></manifest>'
        '<spine><itemref idref="one"/><itemref idref="empty"/><itemref idref="two"/></spine></package>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("book.opf", opf)
        archive.writestr("one.xhtml", "<h1>Chapter one</h1><p>Text.</p>")
        archive.writestr("empty.xhtml", "<p> </p>")
        archive.writestr("two.xhtml", "<p>Chapter two.</p>")


def test_read_epub_chapters_matches_extraction_numbering(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    _write_epub(source)

    chapters = read_epub_chapters(source)

    assert [chapter.chapter_id for chapter in chapters] == ["chapter_001", "chapter_002"]
    assert [chapter.text for chapter in chapters] == ["Chapter one Text.", "Chapter two."]


def test_read_epub_chapters_rejects_non_epub(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    source.write_bytes(b"not an epub")

    with pytest.raises(zipfile.BadZipFile):
        read_epub_chapters(source)
