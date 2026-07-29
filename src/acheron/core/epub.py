"""EPUB chapter discovery shared by planning and local extraction."""

from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path


@dataclass(frozen=True)
class EpubChapter:
    """One non-empty EPUB spine document with its stable output identity."""

    chapter_id: str
    text: str


class _HTMLText(HTMLParser):
    _BLOCK_TAGS = frozenset({"p", "h1", "h2", "h3", "h4", "h5", "h6", "div", "br", "li"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._text: list[str] = []

    def handle_data(self, data: str) -> None:
        self._text.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._BLOCK_TAGS:
            self._text.append(" ")
        _ = attrs

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCK_TAGS:
            self._text.append(" ")

    def text(self) -> str:
        return "".join(self._text)


def _strip_html_tags(html: str) -> str:
    parser = _HTMLText()
    parser.feed(html)
    return parser.text()


def _opf_path(archive: zipfile.ZipFile) -> str:
    try:
        root = ET.fromstring(archive.read("META-INF/container.xml"))  # noqa: S314
        rootfile = root.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
        if rootfile is None:
            rootfile = root.find(".//rootfile")
        if rootfile is not None and rootfile.get("full-path"):
            return rootfile.attrib["full-path"]
    except KeyError, ET.ParseError:
        pass
    opf_files = [name for name in archive.namelist() if name.endswith(".opf")]
    if not opf_files:
        raise ValueError("EPUB has no OPF package document")
    return opf_files[0]


def _spine_hrefs(root: ET.Element) -> list[str]:
    manifest: dict[str, str] = {}
    items = root.findall(".//{http://www.idpf.org/2007/opf}item") or root.findall(".//item")
    for item in items:
        item_id = item.get("id")
        href = item.get("href")
        if item_id and href:
            manifest[item_id] = urllib.parse.unquote(href)
    refs = root.findall(".//{http://www.idpf.org/2007/opf}itemref") or root.findall(".//itemref")
    hrefs = [manifest[ref.attrib["idref"]] for ref in refs if ref.get("idref") in manifest]
    if not hrefs:
        raise ValueError("EPUB has no spine documents")
    return hrefs


def _read_document(archive: zipfile.ZipFile, opf_dir: Path, href: str) -> str:
    clean_href = href.split("#", maxsplit=1)[0]
    candidates = [
        (opf_dir / clean_href).as_posix().replace("./", "").replace("//", "/").removeprefix("/"),
        clean_href,
    ]
    for candidate in candidates:
        try:
            return archive.read(candidate).decode("utf-8", errors="ignore")
        except KeyError:
            continue
    msg = f"EPUB chapter document not found: {clean_href}"
    raise ValueError(msg)


def read_epub_chapters(source_path: Path) -> tuple[EpubChapter, ...]:
    """Return non-empty spine chapters using extraction's stable IDs."""
    with zipfile.ZipFile(source_path, "r") as archive:
        opf_path = _opf_path(archive)
        root = ET.fromstring(archive.read(opf_path))  # noqa: S314
        opf_dir = Path(opf_path).parent
        chapters: list[EpubChapter] = []
        chapter_number = 1
        for href in _spine_hrefs(root):
            text = " ".join(_strip_html_tags(_read_document(archive, opf_dir, href)).split())
            if text:
                chapters.append(EpubChapter(f"chapter_{chapter_number:03d}", text))
                chapter_number += 1
    if not chapters:
        raise ValueError("EPUB has no non-empty chapters")
    return tuple(chapters)
