import asyncio
import json
import struct
import zipfile
from pathlib import Path
from typing import Any

import pytest

from acheron.core.models import Job, OutputFile, WorkerType
from acheron.shell.cache import StepCache
from acheron.shell.local_handlers import ChunkingHandler, ExtractionHandler, PackagingHandler


def _create_dummy_epub(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as z:
        container_xml = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
        z.writestr("META-INF/container.xml", container_xml)

        # F-07: Use percent-encoded href to verify unquoting
        opf = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uuid_id" version="2.0">
  <manifest>
    <item href="ch%201.xhtml" id="html1" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="html1"/>
  </spine>
</package>"""
        z.writestr("OEBPS/content.opf", opf)

        # F-06: Check block tag boundaries to ensure words do not merge
        ch1 = "<html><body><h1>Chapter 1</h1><p>Hello World!</p></body></html>"
        z.writestr("OEBPS/ch 1.xhtml", ch1)


def _create_dummy_wav(path: Path, duration_sec: float = 1.0) -> None:
    sample_rate = 22050
    num_samples = int(sample_rate * duration_sec)
    data_size = num_samples * 2
    header = (
        b"RIFF"
        + struct.pack("<I", 36 + data_size)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
        + b"data"
        + struct.pack("<I", data_size)
        + b"\x00" * data_size
    )
    path.write_bytes(header)


@pytest.mark.asyncio
async def test_extraction_handler_epub(tmp_path: Path) -> None:
    epub_path = tmp_path / "book.epub"
    _create_dummy_epub(epub_path)

    handler = ExtractionHandler(tmp_path)
    job = Job(
        job_id="job1-extract", job_type=WorkerType.EXTRACTION, payload={"source_path": str(epub_path)}, chapter_id=""
    )

    result = await handler(job)
    assert len(result.outputs) == 1
    out_file = Path(result.outputs[0].path)
    assert out_file.exists()
    assert out_file.name == "chapter_001.txt"
    # Verify block tag space addition
    assert out_file.read_text(encoding="utf-8") == "Chapter 1 Hello World!"


@pytest.mark.asyncio
async def test_chunking_handler(tmp_path: Path) -> None:
    job_id = "job2"
    extract_dir = tmp_path / job_id / "extract"
    extract_dir.mkdir(parents=True)
    txt_file = extract_dir / "chapter_001.txt"
    txt_file.write_text("Hello World! This is a test chapter that should be chunked.", encoding="utf-8")

    cache = StepCache(tmp_path)
    await cache.save_outputs(
        job_id,
        "extract",
        (
            OutputFile(
                path=str(txt_file),
                filename="chapter_001.txt",
                size_bytes=txt_file.stat().st_size,
                checksum="",
                content_type="text/plain",
            ),
        ),
    )

    handler = ChunkingHandler(tmp_path, max_chunk_length=30)
    job = Job(job_id=f"{job_id}-chunk", job_type=WorkerType.CHUNKING, payload={}, chapter_id="")
    result = await handler(job)
    assert len(result.outputs) == 1
    chunks_file = Path(result.outputs[0].path)
    assert chunks_file.exists()
    assert chunks_file.name == "chunks.json"

    chunks_data = json.loads(chunks_file.read_text(encoding="utf-8"))
    assert len(chunks_data) > 0
    assert chunks_data[0]["chapter_id"] == "chapter_001"
    assert chunks_data[0]["sequence_id"] == 0
    assert "Hello" in chunks_data[0]["text"]


@pytest.mark.asyncio
async def test_packaging_handler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = "job3"
    synthesize_dir = tmp_path / job_id / "synthesize"
    synthesize_dir.mkdir(parents=True)

    _create_dummy_wav(synthesize_dir / "chapter_001_000.wav", 1.0)
    _create_dummy_wav(synthesize_dir / "chapter_002_000.wav", 1.5)

    cache = StepCache(tmp_path)
    wav_outputs = (
        OutputFile(
            path=str(synthesize_dir / "chapter_001_000.wav"),
            filename="chapter_001_000.wav",
            size_bytes=44144,
            checksum="",
            content_type="audio/wav",
        ),
        OutputFile(
            path=str(synthesize_dir / "chapter_002_000.wav"),
            filename="chapter_002_000.wav",
            size_bytes=66216,
            checksum="",
            content_type="audio/wav",
        ),
    )
    await cache.save_outputs(job_id, "synthesize", wav_outputs)

    mock_run_called = []

    class MockProcess:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def mock_create_subprocess_exec(*args: Any, **kwargs: Any) -> MockProcess:
        mock_run_called.append(args)
        out_path = Path(args[-1])
        out_path.write_bytes(b"dummy m4b")
        return MockProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_create_subprocess_exec)

    handler = PackagingHandler(tmp_path, bitrate="128k", codec="aac")
    job = Job(job_id=f"{job_id}-package", job_type=WorkerType.PACKAGING, payload={}, chapter_id="")
    result = await handler(job)
    assert len(result.outputs) == 1
    assert Path(result.outputs[0].path).exists()
    assert mock_run_called
    cmd = mock_run_called[0]
    assert "-b:a" in cmd
    assert "128k" in cmd


@pytest.mark.asyncio
async def test_packaging_handler_rejects_huge_fmt_chunk(tmp_path: Path) -> None:
    path = tmp_path / "huge_fmt.wav"
    header = b"RIFF" + struct.pack("<I", 36 + 100) + b"WAVE" + b"fmt " + struct.pack("<I", 0x7FFFFFFF)
    path.write_bytes(header)

    from acheron.core.errors import WorkerError
    from acheron.shell.local_handlers import read_wav_duration

    with pytest.raises(WorkerError, match="Invalid format chunk size"):
        read_wav_duration(path, max_fmt_chunk_length=65536)
