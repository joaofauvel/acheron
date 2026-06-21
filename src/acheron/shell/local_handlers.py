"""Direct built-in local handlers for EXTRACTION, CHUNKING, and PACKAGING."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import re
import shutil
import struct
import time
import urllib.parse
import zipfile
from collections.abc import Awaitable, Callable
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from defusedxml import ElementTree as DefusedET

from acheron.core.chunking import chunk_text
from acheron.core.errors import CacheCorruptedError, CacheMissError, WorkerError
from acheron.core.models import (
    SUPPORTED_LANGUAGES,
    Chunk,
    Job,
    JobMetrics,
    JobResult,
    JobStatus,
    OutputFile,
    WorkerCapabilities,
    WorkerType,
)
from acheron.shell.cache import StepCache

if TYPE_CHECKING:
    import xml.etree.ElementTree as ET

type LocalJobHandler = Callable[[Job], Awaitable[JobResult]]

_RIFF_HEADER_LEN = 12
_WAVE_MAGIC = b"WAVE"
_RIFF_MAGIC = b"RIFF"
_FMT_CHUNK_MIN_LEN = 12
_CHUNK_HEADER_LEN = 8
_PCM_AUDIO_FORMAT = 1


def all_languages_caps(worker_type: WorkerType) -> WorkerCapabilities:
    """Capabilities advertising every built-in language for a built-in worker."""
    return WorkerCapabilities(
        worker_type=worker_type,
        supported_languages_in=SUPPORTED_LANGUAGES,
        supported_languages_out=SUPPORTED_LANGUAGES,
        supported_formats_in=frozenset(),
        supported_formats_out=frozenset(),
        max_payload_bytes=None,
        batch_capable=False,
        model_source=None,
    )


class HTMLStripper(HTMLParser):
    """HTML parser that strips tags while preserving block boundaries."""

    BLOCK_TAGS: frozenset[str] = frozenset({"p", "h1", "h2", "h3", "h4", "h5", "h6", "div", "br", "li"})

    def __init__(self) -> None:
        super().__init__()
        self.reset()
        self.convert_charrefs = True
        self.text: list[str] = []

    def handle_data(self, data: str) -> None:
        """Append text data from the parser."""
        self.text.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Insert a space at block tag openings to prevent word merging."""
        if tag in self.BLOCK_TAGS:
            self.text.append(" ")
        _ = attrs

    def handle_endtag(self, tag: str) -> None:
        """Insert a space at block tag closings to prevent word merging."""
        if tag in self.BLOCK_TAGS:
            self.text.append(" ")

    def get_data(self) -> str:
        """Return the accumulated text."""
        return "".join(self.text)


def strip_html_tags(html: str) -> str:
    """Strip HTML tags while preserving block-level spacing."""
    s = HTMLStripper()
    s.feed(html)
    return s.get_data()


def _sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8192), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    """Compute SHA-256 hex digest of a byte string."""
    hasher = hashlib.sha256()
    hasher.update(data)
    return hasher.hexdigest()


def _resolve_opf_path(z: zipfile.ZipFile) -> str:
    """Locate the OPF package document path inside an EPUB archive."""
    try:
        container_xml = z.read("META-INF/container.xml")
        root = DefusedET.fromstring(container_xml)
        rootfile_el = root.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
        if rootfile_el is None:
            rootfile_el = root.find(".//rootfile")
    except Exception as e:
        opf_files = [name for name in z.namelist() if name.endswith(".opf")]
        if not opf_files:
            msg = "No OPF file found in EPUB"
            raise WorkerError(msg) from e
        return opf_files[0]

    if rootfile_el is None:
        msg = "container.xml has no rootfile"
        raise WorkerError(msg)
    opf_path: str | None = rootfile_el.get("full-path")
    if not opf_path:
        msg = "Failed to find OPF path"
        raise WorkerError(msg)
    return str(opf_path)


def _resolve_spine_hrefs(opf_root: ET.Element) -> list[str]:
    """Resolve spine itemref idrefs to hrefs via the manifest."""
    manifest_map: dict[str, str] = {}
    items = opf_root.findall(".//{http://www.idpf.org/2007/opf}item")
    if not items:
        items = opf_root.findall(".//item")
    for item in items:
        item_id = item.get("id")
        href = item.get("href")
        if item_id and href:
            manifest_map[item_id] = urllib.parse.unquote(href)

    itemrefs = opf_root.findall(".//{http://www.idpf.org/2007/opf}itemref")
    if not itemrefs:
        itemrefs = opf_root.findall(".//itemref")

    spine_hrefs: list[str] = []
    for itemref in itemrefs:
        idref = itemref.get("idref")
        if idref in manifest_map:
            spine_hrefs.append(manifest_map[idref])

    if not spine_hrefs:
        msg = "No chapters in spine"
        raise WorkerError(msg)
    return spine_hrefs


def _read_chapter_html(z: zipfile.ZipFile, opf_dir: Path, href: str) -> str:
    """Read a chapter XHTML file from the EPUB archive."""
    clean_href = href.split("#", maxsplit=1)[0]
    zip_path = (opf_dir / clean_href).as_posix()
    zip_path = zip_path.replace("./", "").replace("//", "/")
    zip_path = zip_path.removeprefix("/")
    try:
        return z.read(zip_path).decode("utf-8", errors="ignore")
    except KeyError:
        try:
            return z.read(clean_href).decode("utf-8", errors="ignore")
        except KeyError as e_inner:
            msg = f"Chapter file not found in ZIP: {zip_path}"
            raise WorkerError(msg) from e_inner


def _output_file(path: Path, filename: str, content_type: str, data: bytes | None = None) -> OutputFile:
    """Build an OutputFile with size and checksum from a path."""
    size = path.stat().st_size
    checksum = _sha256_file(path) if data is None else _sha256_bytes(data)
    return OutputFile(
        path=str(path),
        filename=filename,
        size_bytes=size,
        checksum=checksum,
        content_type=content_type,
    )


def _extract_epub(source_path: Path, extract_dir: Path) -> list[OutputFile]:
    """Extract chapters from an EPUB archive into plain text files."""
    outputs: list[OutputFile] = []
    try:
        with zipfile.ZipFile(source_path, "r") as z:
            opf_path = _resolve_opf_path(z)
            opf_bytes = z.read(opf_path)
            opf_root = DefusedET.fromstring(opf_bytes)
            opf_dir = Path(opf_path).parent
            spine_hrefs = _resolve_spine_hrefs(opf_root)

            chapter_num = 1
            for href in spine_hrefs:
                html_content = _read_chapter_html(z, opf_dir, href)
                text_content = strip_html_tags(html_content).strip()
                if not text_content:
                    continue

                out_filename = f"chapter_{chapter_num:03d}.txt"
                out_path = extract_dir / out_filename
                cleaned_text = " ".join(text_content.split())
                out_path.write_text(cleaned_text, encoding="utf-8")

                outputs.append(_output_file(out_path, out_filename, "text/plain"))
                chapter_num += 1
    except WorkerError:
        raise
    except Exception as e:
        msg = f"EPUB extraction failed: {e}"
        raise WorkerError(msg) from e
    return outputs


def _copy_audio(source_path: Path, extract_dir: Path) -> list[OutputFile]:
    """Copy an audio source file into the extract directory."""
    try:
        dest_path = extract_dir / source_path.name
        shutil.copy2(source_path, dest_path)
        ext = dest_path.suffix.lower()
        content_type = "audio/mpeg" if ext == ".mp3" else "audio/wav" if ext == ".wav" else "application/octet-stream"
        return [_output_file(dest_path, dest_path.name, content_type)]
    except Exception as e:
        msg = f"Audio copying failed: {e}"
        raise WorkerError(msg) from e


class ExtractionHandler:
    """Local handler for EPUB and audio extraction."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    async def __call__(self, job: Job) -> JobResult:
        """Run extraction: EPUB chapters to text or audio file copy."""
        start_time = time.monotonic()
        source_path_str = str(job.payload.get("source_path", ""))
        source_path = Path(source_path_str)
        if not await asyncio.to_thread(source_path.exists):
            msg = f"Source file not found: {source_path}"
            raise WorkerError(msg)

        plan_job_id = job.job_id.rsplit("-", 1)[0]
        extract_dir = self.data_dir / plan_job_id / "extract"
        await asyncio.to_thread(extract_dir.mkdir, parents=True, exist_ok=True)

        is_epub = source_path.suffix.lower() == ".epub"
        if is_epub:
            outputs = await asyncio.to_thread(_extract_epub, source_path, extract_dir)
        else:
            outputs = await asyncio.to_thread(_copy_audio, source_path, extract_dir)

        duration = time.monotonic() - start_time
        return JobResult(
            job_id=job.job_id,
            status=JobStatus.SUCCESS,
            outputs=tuple(outputs),
            metrics=JobMetrics(duration_seconds=duration),
        )


class ChunkingHandler:
    """Local handler for text chunking."""

    def __init__(self, data_dir: Path, max_chunk_length: int) -> None:
        self.data_dir = data_dir
        self.max_chunk_length = max_chunk_length

    async def __call__(self, job: Job) -> JobResult:
        """Chunk extracted text files using the configured max length."""
        start_time = time.monotonic()
        plan_job_id = job.job_id.rsplit("-", 1)[0]
        chunk_dir = self.data_dir / plan_job_id / "chunk"
        await asyncio.to_thread(chunk_dir.mkdir, parents=True, exist_ok=True)

        cache = StepCache(self.data_dir)
        upstream_outputs: tuple[OutputFile, ...] | None = None
        for step_dep in ("transcribe", "extract"):
            try:
                upstream_outputs = await cache.load_outputs(plan_job_id, step_dep)
                break
            except CacheMissError, CacheCorruptedError, OSError:
                continue

        if not upstream_outputs:
            msg = "No upstream text files found for chunking"
            raise WorkerError(msg)

        all_chunks = await asyncio.to_thread(self._chunk_outputs, upstream_outputs)

        chunks_json_path = chunk_dir / "chunks.json"
        serialized = [dataclasses.asdict(c) for c in all_chunks]
        manifest_data = json.dumps(serialized, indent=2).encode("utf-8")
        await asyncio.to_thread(chunks_json_path.write_bytes, manifest_data)

        outputs = (
            OutputFile(
                path=str(chunks_json_path),
                filename="chunks.json",
                size_bytes=chunks_json_path.stat().st_size,
                checksum=_sha256_bytes(manifest_data),
                content_type="application/json",
            ),
        )

        duration = time.monotonic() - start_time
        return JobResult(
            job_id=job.job_id,
            status=JobStatus.SUCCESS,
            outputs=outputs,
            metrics=JobMetrics(duration_seconds=duration),
        )

    def _chunk_outputs(self, upstream_outputs: tuple[OutputFile, ...]) -> list[Chunk]:
        """Chunk text from upstream output files."""
        all_chunks: list[Chunk] = []
        for out in upstream_outputs:
            if out.content_type != "text/plain":
                continue
            file_path = Path(out.path)
            if not file_path.exists():
                msg = f"Upstream text file does not exist: {file_path}"
                raise WorkerError(msg)
            chapter_id = file_path.stem
            text = file_path.read_text(encoding="utf-8")
            chunks = chunk_text(text, chapter_id, max_length=self.max_chunk_length)
            all_chunks.extend(chunks)
        return all_chunks


def _read_wav_chunks(f: BinaryIO) -> tuple[bytes | None, int | None]:
    """Read fmt and data chunks from a WAV file object.

    Returns (fmt_chunk, data_size); either may be None if not found.
    """
    fmt_chunk: bytes | None = None
    data_size: int | None = None
    while True:
        chunk_header = f.read(_CHUNK_HEADER_LEN)
        if len(chunk_header) < _CHUNK_HEADER_LEN:
            break
        chunk_id, chunk_len = struct.unpack("<4sI", chunk_header)
        if chunk_id == b"fmt ":
            fmt_chunk = f.read(chunk_len)
            if len(fmt_chunk) < chunk_len:
                msg = "Corrupted fmt chunk in WAV"
                raise WorkerError(msg)
        elif chunk_id == b"data":
            data_size = chunk_len
            break
        else:
            f.seek(chunk_len, 1)
    return fmt_chunk, data_size


def _validate_wav_format(fmt_chunk: bytes, path: Path) -> int:
    """Validate the fmt chunk and return the byte rate."""
    if len(fmt_chunk) < _FMT_CHUNK_MIN_LEN:
        msg = f"Corrupted fmt chunk (too short) in WAV: {path}"
        raise WorkerError(msg)
    audio_format, _num_channels, _sample_rate, byte_rate = struct.unpack("<HHII", fmt_chunk[:_FMT_CHUNK_MIN_LEN])
    if audio_format != _PCM_AUDIO_FORMAT:
        msg = f"Unsupported non-PCM WAV format: {path}"
        raise WorkerError(msg)
    if byte_rate == 0:
        msg = f"Invalid byte rate in WAV format: {path}"
        raise WorkerError(msg)
    return int(byte_rate)


def _validate_riff_header(riff_header: bytes, path: Path) -> None:
    """Validate the RIFF/WAVE magic header."""
    if (
        len(riff_header) < _RIFF_HEADER_LEN
        or riff_header[0:4] != _RIFF_MAGIC
        or riff_header[8:_RIFF_HEADER_LEN] != _WAVE_MAGIC
    ):
        msg = f"Invalid WAV file format (missing RIFF/WAVE magic): {path}"
        raise WorkerError(msg)


def _require_chunks(fmt_chunk: bytes | None, data_size: int | None, path: Path) -> tuple[bytes, int]:
    """Ensure both fmt and data chunks were found; return the validated pair."""
    if fmt_chunk is None or data_size is None:
        msg = f"Missing fmt or data chunk in WAV: {path}"
        raise WorkerError(msg)
    return fmt_chunk, data_size


def _parse_wav_header(path: Path) -> tuple[int, int]:
    """Parse a WAV file header and return (data_size, byte_rate).

    Raises:
        WorkerError: If the file is not a valid PCM WAV.
    """
    try:
        with path.open("rb") as f:
            riff_header = f.read(_RIFF_HEADER_LEN)
            _validate_riff_header(riff_header, path)

            fmt_chunk, data_size = _read_wav_chunks(f)
            fmt_chunk, data_size = _require_chunks(fmt_chunk, data_size, path)

            byte_rate = _validate_wav_format(fmt_chunk, path)
            return data_size, byte_rate
    except WorkerError:
        raise
    except Exception as e:
        msg = f"Failed to read WAV duration: {e}"
        raise WorkerError(msg) from e


def read_wav_duration(path: Path) -> float:
    """Read the duration of a PCM WAV file from its header."""
    data_size, byte_rate = _parse_wav_header(path)
    return data_size / byte_rate


def _wav_sort_key(output_file: OutputFile) -> tuple[int, int]:
    """Sort key for WAV files by chapter and sequence number."""
    filename = output_file.filename
    m = re.match(r"chapter_(\d+)_(\d+)", filename)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m2 = re.match(r"chapter_(\d+)", filename)
    if m2:
        return (int(m2.group(1)), 0)
    return (0, 0)


def _extract_chapter_id(filename: str) -> str:
    """Extract a chapter_id prefix from a WAV filename."""
    m = re.match(r"(chapter_[a-zA-Z0-9]+)", filename)
    if m:
        return m.group(1)
    return "chapter_001"


def _build_ffmetadata(plan_job_id: str, chapter_durations: dict[str, float]) -> str:
    """Build an FFMETADATA file string from chapter durations."""
    lines = [";FFMETADATA1", f"title=Audiobook {plan_job_id}"]
    current_ms = 0
    for chapter_id, duration_sec in chapter_durations.items():
        duration_ms = round(duration_sec * 1000)
        start_ms = current_ms
        end_ms = start_ms + duration_ms
        chapter_title = chapter_id.replace("_", " ").title()
        lines.extend(["[CHAPTER]", "TIMEBASE=1/1000", f"START={start_ms}", f"END={end_ms}", f"title={chapter_title}"])
        current_ms = end_ms
    return "\n".join(lines)


class PackagingHandler:
    """Local handler for audiobook packaging via FFmpeg concat demuxer."""

    def __init__(self, data_dir: Path, bitrate: str, codec: str) -> None:
        self.data_dir = data_dir
        self.bitrate = bitrate
        self.codec = codec

    async def __call__(self, job: Job) -> JobResult:
        """Package synthesized WAV files into a single M4B audiobook."""
        start_time = time.monotonic()
        plan_job_id = job.job_id.rsplit("-", 1)[0]

        cache = StepCache(self.data_dir)
        try:
            synthesize_outputs = await cache.load_outputs(plan_job_id, "synthesize")
        except (CacheMissError, CacheCorruptedError, OSError) as e:
            msg = f"Packaging failed: could not load outputs of synthesize step: {e}"
            raise WorkerError(msg) from e

        sorted_outputs = sorted(synthesize_outputs, key=_wav_sort_key)
        if not sorted_outputs:
            msg = "No WAV files registered in synthesize manifest"
            raise WorkerError(msg)

        package_dir = self.data_dir / plan_job_id / "package"
        await asyncio.to_thread(package_dir.mkdir, parents=True, exist_ok=True)

        chapter_durations = await asyncio.to_thread(self._compute_durations, sorted_outputs)

        ffmetadata = _build_ffmetadata(plan_job_id, chapter_durations)
        ffmetadata_path = package_dir / "FFMETADATA"
        await asyncio.to_thread(ffmetadata_path.write_text, ffmetadata, "utf-8")

        concat_lines = await asyncio.to_thread(self._build_concat_lines, sorted_outputs)
        inputs_txt_path = package_dir / "inputs.txt"
        await asyncio.to_thread(inputs_txt_path.write_text, "\n".join(concat_lines), "utf-8")

        output_m4b_path = package_dir / "output.m4b"
        await self._run_ffmpeg(inputs_txt_path, ffmetadata_path, output_m4b_path)

        checksum = await asyncio.to_thread(_sha256_file, output_m4b_path)
        outputs = (
            OutputFile(
                path=str(output_m4b_path),
                filename="output.m4b",
                size_bytes=output_m4b_path.stat().st_size,
                checksum=checksum,
                content_type="audio/mp4",
            ),
        )

        duration = time.monotonic() - start_time
        return JobResult(
            job_id=job.job_id,
            status=JobStatus.SUCCESS,
            outputs=outputs,
            metrics=JobMetrics(duration_seconds=duration),
        )

    def _compute_durations(self, sorted_outputs: list[OutputFile]) -> dict[str, float]:
        """Compute per-chapter durations by reading WAV headers."""
        chapter_durations: dict[str, float] = {}
        for out in sorted_outputs:
            wav_path = Path(out.path)
            duration = read_wav_duration(wav_path)
            chapter_id = _extract_chapter_id(out.filename)
            chapter_durations[chapter_id] = chapter_durations.get(chapter_id, 0.0) + duration
        return chapter_durations

    @staticmethod
    def _build_concat_lines(sorted_outputs: list[OutputFile]) -> list[str]:
        """Build concat demuxer input lines with absolute paths."""
        return [f"file '{Path(out.path).resolve().as_posix()}'" for out in sorted_outputs]

    async def _run_ffmpeg(self, inputs_path: Path, ffmetadata_path: Path, output_path: Path) -> None:
        """Invoke ffmpeg to concatenate WAVs into an M4B."""
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(inputs_path),
            "-i",
            str(ffmetadata_path),
            "-map_metadata",
            "1",
            "-c:a",
            self.codec,
            "-b:a",
            self.bitrate,
            str(output_path),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await proc.communicate()
        except Exception as e:
            msg = f"Failed to start FFmpeg process: {e}"
            raise WorkerError(msg) from e

        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="ignore")
            msg = f"FFmpeg concatenation failed with status {proc.returncode}: {err_msg}"
            raise WorkerError(msg)
