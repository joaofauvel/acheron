# Layer 10 — Local Workers and Resuming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement real built-in local workers (EPUB extraction, text chunking, audiobook packaging), YAML-based configuration settings via `acheron.yaml`, and job resuming mechanics across the executors, API, and CLI.

**Architecture:** We introduce a Pydantic Settings-based config module loaded at orchestrator startup that seamlessly merges YAML configuration with environment overrides. We swap the stub local handlers for real implementations using standard zipfile/ElementTree parsing, NLTK chunking, and FFmpeg concatenation. We update the streaming executor to skip steps with valid caches, wrap resume operations in a per-job lock for concurrency safety, and restructure Click CLI commands.

**Tech Stack:** Python 3.14, Pydantic v2, Pydantic Settings, PyYAML, FFmpeg, Click, Rich, FastAPI.

---

## File Structure Map

We will create/modify the following files:
* **Create**: `src/acheron/shell/config.py` — Settings schema using Pydantic Settings.
* **Create**: `tests/shell/test_config.py` — Configuration validation tests.
* **Modify**: `src/acheron/shell/local_handlers.py` — Real local workers (`ExtractionHandler`, `ChunkingHandler`, `PackagingHandler`).
* **Modify**: `src/acheron/shell/orchestrator.py` — Thread settings, add `resume_job(job_id, force_fresh)` method with lock, and use `data_dir` settings.
* **Modify**: `src/acheron/shell/api/app.py` — Instantiation of `Orchestrator` with loaded settings.
* **Modify**: `src/acheron/shell/api/routes/jobs.py` — Add `/jobs/{job_id}/resume` POST route.
* **Modify**: `src/acheron/shell/executors/streaming.py` — Add step cache inspection to `_stage`.
* **Modify**: `src/acheron/api_client.py` — Add client methods `get_health` and `resume_job`.
* **Modify**: `src/acheron/cli.py` — Group job subcommands under `acheron job` and repurpose top-level `status`.
* **Modify**: `tests/integration/conftest.py` — EPUB helper fixture and environment setup for uvicorn stubs.
* **Modify**: `tests/integration/test_worker_integration.py` — Update integration tests to use the EPUB helper fixture and update the ASR stub to write real files.
* **Modify**: `stubs/worker_stub.py` — Update the TTS stub worker to write real WAV output to the data directory.
* **Modify**: `tests/shell/test_local_worker.py` — Adapt tests for the new local worker handlers.
* **Modify**: `tests/shell/test_orchestrator.py` — Test job resuming.
* **Modify**: `tests/shell/test_cli.py` — Update CLI test commands.

---

## Tasks

### Task 1: Settings Module & Configuration Validation

Add the `pydantic-settings` schema in `src/acheron/shell/config.py` and PyYAML support.

**Files:**
* Create: `src/acheron/shell/config.py`
* Test: `tests/shell/test_config.py`

- [ ] **Step 1: Install PyYAML dependency**

Run:
```bash
uv add pyyaml~=6.0
```
Expected: `pyproject.toml` updated with `pyyaml`.

- [ ] **Step 2: Write settings schema test**

Create `tests/shell/test_config.py` to assert that settings load correctly from custom YAML.
```python
import os
from pathlib import Path
import pytest
from acheron.shell.config import Settings, load_settings

def test_default_settings() -> None:
    settings = Settings()
    assert settings.orchestrator.data_dir == Path("/data/jobs")
    assert settings.workers.chunking.max_chunk_length == 250
    assert settings.workers.packaging.bitrate == "128k"

def test_load_settings_from_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    yaml_content = """
orchestrator:
  data_dir: "/tmp/custom_jobs"
  health_check_interval_seconds: 45
workers:
  chunking:
    max_chunk_length: 500
  packaging:
    bitrate: "192k"
    codec: "mp3"
"""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    monkeypatch.setenv("ACHERON_CONFIG_PATH", str(config_file))
    
    settings = load_settings()
    assert settings.orchestrator.data_dir == Path("/tmp/custom_jobs")
    assert settings.orchestrator.health_check_interval_seconds == 45
    assert settings.workers.chunking.max_chunk_length == 500
    assert settings.workers.packaging.bitrate == "192k"
    assert settings.workers.packaging.codec == "mp3"

def test_settings_env_var_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # F-05: Ensure environment variables override loaded YAML settings
    yaml_content = "orchestrator:\n  data_dir: '/tmp/yaml_dir'"
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    monkeypatch.setenv("ACHERON_CONFIG_PATH", str(config_file))
    monkeypatch.setenv("ACHERON_ORCHESTRATOR__DATA_DIR", "/tmp/env_dir")
    
    settings = load_settings()
    assert settings.orchestrator.data_dir == Path("/tmp/env_dir")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/shell/test_config.py`
Expected: FAIL (ModuleNotFoundError/ImportError on `config`)

- [ ] **Step 4: Implement Settings validation in config.py**

Create `src/acheron/shell/config.py`:
```python
from pathlib import Path
import os
import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class OrchestratorSettings(BaseModel):
    data_dir: Path = Field(default=Path("/data/jobs"))
    registration_token: str | None = Field(default=None)
    health_check_interval_seconds: int = Field(default=30)

class ChunkingSettings(BaseModel):
    max_chunk_length: int = Field(default=250)

class PackagingSettings(BaseModel):
    bitrate: str = Field(default="128k")
    codec: str = Field(default="aac")

class WorkerSettings(BaseModel):
    chunking: ChunkingSettings = Field(default_factory=ChunkingSettings)
    packaging: PackagingSettings = Field(default_factory=PackagingSettings)

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_prefix="ACHERON_",
        extra="ignore"
    )

    orchestrator: OrchestratorSettings = Field(default_factory=OrchestratorSettings)
    workers: WorkerSettings = Field(default_factory=WorkerSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        class YamlConfigSettingsSource(PydanticBaseSettingsSource):
            def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
                return None, "", False

            def __call__(self) -> dict[str, Any]:
                config_path_env = os.environ.get("ACHERON_CONFIG_PATH")
                search_paths = []
                if config_path_env:
                    search_paths.append(Path(config_path_env))
                search_paths.extend([Path("./acheron.yaml"), Path("/etc/acheron/acheron.yaml")])

                for path in search_paths:
                    if path.is_file():
                        try:
                            with path.open("r", encoding="utf-8") as f:
                                return yaml.safe_load(f) or {}
                        except Exception:
                            pass
                return {}

        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls),
        )

def load_settings() -> Settings:
    return Settings()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/shell/test_config.py`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/acheron/shell/config.py tests/shell/test_config.py
git commit -m "feat: add Settings module and load_settings from yaml"
```

---

### Task 2: Real Local Extraction Worker

Replace stub extraction logic with a real ZIP+XML EPUB parser and plain audio copying handler.

**Files:**
* Modify: `src/acheron/shell/local_handlers.py`
* Test: `tests/shell/test_local_handlers.py` (we will create this test file)

- [ ] **Step 1: Write ExtractionHandler tests**

Create `tests/shell/test_local_handlers.py` and write the test cases for `ExtractionHandler`:
```python
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
import pytest
from acheron.core.models import Job, WorkerType
from acheron.shell.local_handlers import ExtractionHandler

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

@pytest.mark.asyncio
async def test_extraction_handler_epub(tmp_path: Path) -> None:
    epub_path = tmp_path / "book.epub"
    _create_dummy_epub(epub_path)
    
    handler = ExtractionHandler(tmp_path)
    job = Job(
        job_id="job1-extract",
        job_type=WorkerType.EXTRACTION,
        payload={"source_path": str(epub_path)},
        chapter_id=""
    )
    
    result = await handler(job)
    assert len(result.outputs) == 1
    out_file = Path(result.outputs[0].path)
    assert out_file.exists()
    assert out_file.name == "chapter_001.txt"
    # Verify F-06 block tag space addition
    assert out_file.read_text(encoding="utf-8") == "Chapter 1 Hello World!"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/shell/test_local_handlers.py`
Expected: FAIL (ImportError on `ExtractionHandler`)

- [ ] **Step 3: Implement ExtractionHandler class**

Modify `src/acheron/shell/local_handlers.py`:
```python
import zipfile
import xml.etree.ElementTree as ET
import shutil
import hashlib
import time
import urllib.parse
from pathlib import Path
from html.parser import HTMLParser
from acheron.core.errors import WorkerError
from acheron.core.models import Job, JobMetrics, JobResult, JobStatus, OutputFile, SUPPORTED_LANGUAGES, WorkerCapabilities, WorkerType

class HTMLStripper(HTMLParser):
    BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "div", "br", "li"}
    
    def __init__(self) -> None:
        super().__init__()
        self.reset()
        self.convert_charrefs = True
        self.text: list[str] = []

    def handle_data(self, d: str) -> None:
        self.text.append(d)

    def handle_endtag(self, tag: str) -> None:
        # F-06: Append spacer for block level elements to prevent words merging
        if tag in self.BLOCK_TAGS:
            self.text.append(" ")

    def get_data(self) -> str:
        return "".join(self.text)

def strip_html_tags(html: str) -> str:
    s = HTMLStripper()
    s.feed(html)
    return s.get_data()

class ExtractionHandler:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    async def __call__(self, job: Job) -> JobResult:
        start_time = time.monotonic()
        source_path_str = str(job.payload.get("source_path", ""))
        source_path = Path(source_path_str)
        if not source_path.exists():
            raise WorkerError(f"Source file not found: {source_path}")
            
        # F-02: Use resolved plan job ID instead of job.job_id to avoid path suffix mismatch
        plan_job_id = job.job_id.rsplit("-", 1)[0]
        extract_dir = self.data_dir / plan_job_id / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)
        
        outputs: list[OutputFile] = []
        is_epub = source_path.suffix.lower() == ".epub"
        
        if is_epub:
            try:
                with zipfile.ZipFile(source_path, "r") as z:
                    try:
                        container_xml = z.read("META-INF/container.xml")
                        root = ET.fromstring(container_xml)
                        rootfile_el = root.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
                        if rootfile_el is None:
                            rootfile_el = root.find(".//rootfile")
                        if rootfile_el is None:
                            raise WorkerError("container.xml has no rootfile")
                        opf_path = rootfile_el.get("full-path")
                    except Exception as e:
                        opf_files = [name for name in z.namelist() if name.endswith(".opf")]
                        if not opf_files:
                            raise WorkerError("No OPF file found in EPUB") from e
                        opf_path = opf_files[0]
                    
                    if not opf_path:
                        raise WorkerError("Failed to find OPF path")
                        
                    opf_bytes = z.read(opf_path)
                    opf_root = ET.fromstring(opf_bytes)
                    opf_dir = Path(opf_path).parent
                    
                    manifest_map = {}
                    items = opf_root.findall(".//{http://www.idpf.org/2007/opf}item")
                    if not items:
                        items = opf_root.findall(".//item")
                    for item in items:
                        item_id = item.get("id")
                        href = item.get("href")
                        if item_id and href:
                            # F-07: Unescape URL percent encoding in manifest paths
                            manifest_map[item_id] = urllib.parse.unquote(href)
                            
                    itemrefs = opf_root.findall(".//{http://www.idpf.org/2007/opf}itemref")
                    if not itemrefs:
                        itemrefs = opf_root.findall(".//itemref")
                        
                    spine_hrefs = []
                    for itemref in itemrefs:
                        idref = itemref.get("idref")
                        if idref in manifest_map:
                            spine_hrefs.append(manifest_map[idref])
                            
                    if not spine_hrefs:
                        raise WorkerError("No chapters in spine")
                        
                    chapter_num = 1
                    for href in spine_hrefs:
                        clean_href = href.split("#")[0]
                        zip_path = (opf_dir / clean_href).as_posix()
                        zip_path = zip_path.replace("./", "").replace("//", "/")
                        if zip_path.startswith("/"):
                            zip_path = zip_path[1:]
                            
                        try:
                            html_content = z.read(zip_path).decode("utf-8", errors="ignore")
                        except KeyError:
                            try:
                                html_content = z.read(clean_href).decode("utf-8", errors="ignore")
                            except KeyError as e_inner:
                                raise WorkerError(f"Chapter file not found in ZIP: {zip_path}") from e_inner
                                
                        text_content = strip_html_tags(html_content).strip()
                        if not text_content:
                            continue
                            
                        out_filename = f"chapter_{chapter_num:03d}.txt"
                        out_path = extract_dir / out_filename
                        cleaned_text = " ".join(text_content.split())
                        out_path.write_text(cleaned_text, encoding="utf-8")
                        
                        size = out_path.stat().st_size
                        hasher = hashlib.sha256()
                        hasher.update(cleaned_text.encode("utf-8"))
                        checksum = hasher.hexdigest()
                        
                        outputs.append(
                            OutputFile(
                                path=str(out_path),
                                filename=out_filename,
                                size_bytes=size,
                                checksum=checksum,
                                content_type="text/plain",
                            )
                        )
                        chapter_num += 1
            except Exception as e:
                if isinstance(e, WorkerError):
                    raise
                raise WorkerError(f"EPUB extraction failed: {e}") from e
        else:
            try:
                dest_path = extract_dir / source_path.name
                shutil.copy2(source_path, dest_path)
                size = dest_path.stat().st_size
                hasher = hashlib.sha256()
                with dest_path.open("rb") as f:
                    for chunk in iter(lambda: f.read(8192), b""):
                        hasher.update(chunk)
                checksum = hasher.hexdigest()
                
                ext = dest_path.suffix.lower()
                content_type = "audio/mpeg" if ext == ".mp3" else "audio/wav" if ext == ".wav" else "application/octet-stream"
                
                outputs.append(
                    OutputFile(
                        path=str(dest_path),
                        filename=dest_path.name,
                        size_bytes=size,
                        checksum=checksum,
                        content_type=content_type,
                    )
                )
            except Exception as e:
                raise WorkerError(f"Audio copying failed: {e}") from e
                
        duration = time.monotonic() - start_time
        return JobResult(
            job_id=job.job_id,
            status=JobStatus.SUCCESS,
            outputs=tuple(outputs),
            metrics=JobMetrics(duration_seconds=duration),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/shell/test_local_handlers.py -k test_extraction`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/acheron/shell/local_handlers.py tests/shell/test_local_handlers.py
git commit -m "feat: implement ExtractionHandler for local EPUB/audio extraction"
```

---

### Task 3: Real Local Chunking Worker

Replace stub chunking logic with `ChunkingHandler` loading extracted text and calling `chunk_text()`.

**Files:**
* Modify: `src/acheron/shell/local_handlers.py`
* Test: `tests/shell/test_local_handlers.py`

- [ ] **Step 1: Write ChunkingHandler test**

Append this test to `tests/shell/test_local_handlers.py`:
```python
import json
from acheron.shell.local_handlers import ChunkingHandler
from acheron.shell.cache import StepCache

@pytest.mark.asyncio
async def test_chunking_handler(tmp_path: Path) -> None:
    # Set up job dir and step cache
    job_id = "job2"
    extract_dir = tmp_path / job_id / "extract"
    extract_dir.mkdir(parents=True)
    txt_file = extract_dir / "chapter_001.txt"
    txt_file.write_text("Hello World! This is a test chapter that should be chunked.", encoding="utf-8")
    
    # Save to step cache mock output
    cache = StepCache(tmp_path)
    await cache.save_outputs(
        job_id,
        "extract",
        (OutputFile(path=str(txt_file), filename="chapter_001.txt", size_bytes=txt_file.stat().st_size, checksum="", content_type="text/plain"),)
    )
    
    handler = ChunkingHandler(tmp_path, max_chunk_length=30)
    job = Job(
        job_id=f"{job_id}-chunk",
        job_type=WorkerType.CHUNKING,
        payload={},
        chapter_id=""
    )
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
```

- [ ] **Step 2: Run tests to verify it fails**

Run: `pytest tests/shell/test_local_handlers.py -k test_chunking`
Expected: FAIL (ImportError on `ChunkingHandler`)

- [ ] **Step 3: Implement ChunkingHandler class**

Modify `src/acheron/shell/local_handlers.py`:
```python
import json
import dataclasses
from acheron.core.chunking import chunk_text
from acheron.core.errors import CacheMissError

class ChunkingHandler:
    def __init__(self, data_dir: Path, max_chunk_length: int) -> None:
        self.data_dir = data_dir
        self.max_chunk_length = max_chunk_length

    async def __call__(self, job: Job) -> JobResult:
        start_time = time.monotonic()
        # F-02: Use resolved parent plan job ID
        plan_job_id = job.job_id.rsplit("-", 1)[0]
        chunk_dir = self.data_dir / plan_job_id / "chunk"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        
        # F-03: Do not hardcode /extract, check step cache for transcribed (ASR) text or extracted (EPUB) text
        cache = StepCache(self.data_dir)
        upstream_outputs = None
        for step_dep in ("transcribe", "extract"):
            try:
                upstream_outputs = await cache.load_outputs(plan_job_id, step_dep)
                break
            except Exception:
                continue
                
        if not upstream_outputs:
            raise WorkerError("No upstream text files found for chunking")
            
        all_chunks = []
        for out in upstream_outputs:
            if out.content_type == "text/plain":
                file_path = Path(out.path)
                if not file_path.exists():
                    raise WorkerError(f"Upstream text file does not exist: {file_path}")
                chapter_id = file_path.stem
                text = file_path.read_text(encoding="utf-8")
                chunks = chunk_text(text, chapter_id, max_length=self.max_chunk_length)
                all_chunks.extend(chunks)
                
        chunks_json_path = chunk_dir / "chunks.json"
        serialized = [dataclasses.asdict(c) for c in all_chunks]
        
        manifest_data = json.dumps(serialized, indent=2).encode("utf-8")
        chunks_json_path.write_bytes(manifest_data)
        
        size = chunks_json_path.stat().st_size
        hasher = hashlib.sha256()
        hasher.update(manifest_data)
        checksum = hasher.hexdigest()
        
        outputs = (
            OutputFile(
                path=str(chunks_json_path),
                filename="chunks.json",
                size_bytes=size,
                checksum=checksum,
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/shell/test_local_handlers.py -k test_chunking`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/acheron/shell/local_handlers.py
git commit -m "feat: implement ChunkingHandler local chunking engine"
```

---

### Task 4: Real Local Packaging Worker (FFmpeg)

Replace stub packaging with a real class-based `PackagingHandler` executing `ffmpeg`.

**Files:**
* Modify: `src/acheron/shell/local_handlers.py`
* Test: `tests/shell/test_local_handlers.py`

- [ ] **Step 1: Write PackagingHandler test**

Append this test to `tests/shell/test_local_handlers.py`:
```python
import struct
from acheron.shell.local_handlers import PackagingHandler
from acheron.shell.cache import StepCache

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
            content_type="audio/wav"
        ),
        OutputFile(
            path=str(synthesize_dir / "chapter_002_000.wav"),
            filename="chapter_002_000.wav",
            size_bytes=66216,
            checksum="",
            content_type="audio/wav"
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
    job = Job(
        job_id=f"{job_id}-package",
        job_type=WorkerType.PACKAGING,
        payload={},
        chapter_id=""
    )
    result = await handler(job)
    assert len(result.outputs) == 1
    assert Path(result.outputs[0].path).exists()
    assert mock_run_called
    cmd = mock_run_called[0]
    assert "-b:a" in cmd
    assert "128k" in cmd
```

- [ ] **Step 2: Run tests to verify it fails**

Run: `pytest tests/shell/test_local_handlers.py -k test_packaging`
Expected: FAIL (ImportError on `PackagingHandler`)

- [ ] **Step 3: Implement WAV Duration Parser & PackagingHandler**

Modify `src/acheron/shell/local_handlers.py`:
```python
import struct
import asyncio
import re
from acheron.shell.cache import StepCache

def read_wav_duration(path: Path) -> float:
    try:
        with path.open("rb") as f:
            riff_header = f.read(12)
            if len(riff_header) < 12 or riff_header[0:4] != b"RIFF" or riff_header[8:12] != b"WAVE":
                raise WorkerError(f"Invalid WAV file format (missing RIFF/WAVE magic): {path}")
                
            fmt_chunk = None
            data_size = None
            
            while True:
                chunk_header = f.read(8)
                if len(chunk_header) < 8:
                    break
                chunk_id, chunk_len = struct.unpack("<4sI", chunk_header)
                if chunk_id == b"fmt ":
                    fmt_chunk = f.read(chunk_len)
                    if len(fmt_chunk) < chunk_len:
                        raise WorkerError(f"Corrupted fmt chunk in WAV: {path}")
                elif chunk_id == b"data":
                    data_size = chunk_len
                    break
                else:
                    f.seek(chunk_len, 1)
                    
            if fmt_chunk is None or data_size is None:
                raise WorkerError(f"Missing fmt or data chunk in WAV: {path}")
            
            # F-09: Add length validation before parsing fmt parameters
            if len(fmt_chunk) < 12:
                raise WorkerError(f"Corrupted fmt chunk (too short) in WAV: {path}")
                
            audio_format, num_channels, sample_rate, byte_rate = struct.unpack("<HHII", fmt_chunk[0:12])
            if audio_format != 1:
                raise WorkerError(f"Unsupported non-PCM WAV format: {path}")
            if byte_rate == 0:
                raise WorkerError(f"Invalid byte rate in WAV format: {path}")
                
            return data_size / byte_rate
    except Exception as e:
        if isinstance(e, WorkerError):
            raise
        raise WorkerError(f"Failed to read WAV duration: {e}") from e

def _wav_sort_key(output_file: OutputFile) -> tuple[int, int]:
    filename = output_file.filename
    m = re.match(r"chapter_(\d+)_(\d+)", filename)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m2 = re.match(r"chapter_(\d+)", filename)
    if m2:
        return (int(m2.group(1)), 0)
    return (0, 0)

def _extract_chapter_id(filename: str) -> str:
    m = re.match(r"(chapter_[a-zA-Z0-9]+)", filename)
    if m:
        return m.group(1)
    return "chapter_001"

class PackagingHandler:
    def __init__(self, data_dir: Path, bitrate: str, codec: str) -> None:
        self.data_dir = data_dir
        self.bitrate = bitrate
        self.codec = codec

    async def __call__(self, job: Job) -> JobResult:
        start_time = time.monotonic()
        plan_job_id = job.job_id.rsplit("-", 1)[0]
        
        cache = StepCache(self.data_dir)
        try:
            synthesize_outputs = await cache.load_outputs(plan_job_id, "synthesize")
        except Exception as e:
            raise WorkerError(f"Packaging failed: could not load outputs of synthesize step: {e}") from e
            
        sorted_outputs = sorted(synthesize_outputs, key=_wav_sort_key)
        if not sorted_outputs:
            raise WorkerError("No WAV files registered in synthesize manifest")
            
        package_dir = self.data_dir / plan_job_id / "package"
        package_dir.mkdir(parents=True, exist_ok=True)
        
        chapter_durations: dict[str, float] = {}
        for out in sorted_outputs:
            wav_path = Path(out.path)
            duration = read_wav_duration(wav_path)
            chapter_id = _extract_chapter_id(out.filename)
            chapter_durations[chapter_id] = chapter_durations.get(chapter_id, 0.0) + duration
            
        metadata_lines = [";FFMETADATA1", f"title=Audiobook {plan_job_id}"]
        current_ms = 0
        for chapter_id, duration_sec in chapter_durations.items():
            duration_ms = int(round(duration_sec * 1000))
            start_ms = current_ms
            end_ms = start_ms + duration_ms
            chapter_title = chapter_id.replace("_", " ").title()
            
            metadata_lines.extend([
                "[CHAPTER]",
                "TIMEBASE=1/1000",
                f"START={start_ms}",
                f"END={end_ms}",
                f"title={chapter_title}"
            ])
            current_ms = end_ms
            
        ffmetadata_path = package_dir / "FFMETADATA"
        ffmetadata_path.write_text("\n".join(metadata_lines), encoding="utf-8")
        
        concat_lines = []
        for out in sorted_outputs:
            concat_lines.append(f"file '{Path(out.path).resolve().as_posix()}'")
            
        inputs_txt_path = package_dir / "inputs.txt"
        inputs_txt_path.write_text("\n".join(concat_lines), encoding="utf-8")
        
        output_m4b_path = package_dir / "output.m4b"
        
        cmd = [
            "ffmpeg",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(inputs_txt_path),
            "-i", str(ffmetadata_path),
            "-map_metadata", "1",
            "-c:a", self.codec,
            "-b:a", self.bitrate,
            str(output_m4b_path)
        ]
        
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
        except Exception as e:
            raise WorkerError(f"Failed to start FFmpeg process: {e}") from e
            
        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="ignore")
            raise WorkerError(f"FFmpeg concatenation failed with status {proc.returncode}: {err_msg}")
            
        size = output_m4b_path.stat().st_size
        hasher = hashlib.sha256()
        with output_m4b_path.open("rb") as f:
            for block in iter(lambda: f.read(8192), b""):
                hasher.update(block)
        checksum = hasher.hexdigest()
        
        outputs = (
            OutputFile(
                path=str(output_m4b_path),
                filename="output.m4b",
                size_bytes=size,
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/shell/test_local_handlers.py -k test_packaging`
Expected: PASS

- [ ] **Step 5: Remove BUILT_IN_LOCAL_HANDLERS dictionary and stub handlers**

Clean up `src/acheron/shell/local_handlers.py` to export classes (`ExtractionHandler`, `ChunkingHandler`, `PackagingHandler`) and capabilities helper (`all_languages_caps`).

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/shell/test_local_handlers.py`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/acheron/shell/local_handlers.py
git commit -m "feat: implement PackagingHandler and remove stub handlers"
```

---

### Task 5: Wiring Settings & Workers in Orchestrator & App

Pass `Settings` through `create_app` to `Orchestrator`, wire settings to the local handler constructors, set environment variable in test configurations, and fix health monitor instantiation parameters.

**Files:**
* Modify: `src/acheron/shell/orchestrator.py`
* Modify: `src/acheron/shell/api/app.py`
* Modify: `tests/shell/test_local_worker.py`

- [ ] **Step 1: Write test for Orchestrator using real Settings**

Add to `tests/shell/test_orchestrator.py`:
```python
from acheron.shell.config import Settings
def test_orchestrator_initialization_with_settings(tmp_path: Path) -> None:
    settings = Settings()
    settings.orchestrator.data_dir = tmp_path
    reg = InMemoryWorkerStore()
    orch = Orchestrator(registry=reg, cache=PlanCache(tmp_path), settings=settings)
    assert orch._settings == settings
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/shell/test_orchestrator.py`
Expected: FAIL (TypeError: `Orchestrator.__init__() got an unexpected keyword argument 'settings'`)

- [ ] **Step 3: Modify Orchestrator initialization to accept settings and track active state**

Modify `src/acheron/shell/orchestrator.py`:
- Import `Settings`, `load_settings` from `acheron.shell.config`.
- Import `ExtractionHandler`, `ChunkingHandler`, `PackagingHandler` from `local_handlers`.
- Import `weakref`.
- Add `settings: Settings | None = None` to `Orchestrator.__init__` and store in `self._settings`. If None, load defaults.
- Use `self._settings.orchestrator.data_dir` to instantiate `self._step_cache` and `PlanCache` default directory.
- Initialize `self._active_jobs: set[str] = set()` to track executing jobs in-process.
- Initialize `self._job_locks = weakref.WeakValueDictionary()` for concurrency safety on resumes.
- F-08: Instantiate health monitor with correct signature using Settings:
```python
        self._health_monitor = HealthMonitor(
            registry,
            interval=float(self._settings.orchestrator.health_check_interval_seconds),
        )
```
- Wrap non-streaming executor handler dispatches to auto-cache step outputs for local workers:
```python
    async def _execute(self, tracked: TrackedJob) -> None:
        logger.info("Executing %s (%s strategy)", tracked.job_id, tracked.strategy.value)
        self._active_jobs.add(tracked.job_id)
        try:
            if tracked.plan is None:
                tracked.status = PlanStatus.FAILED
                logger.error("No plan for %s", tracked.job_id)
            else:
                # Wrap handler for non-streaming executors to cache outputs so local workers can load them
                handler = self._handler
                if tracked.strategy != ExecutorStrategy.STREAMING:
                    async def caching_handler(step: PlanStep, plan: Plan) -> JobResult:
                        res = await self._handler(step, plan)
                        if res.status == JobStatus.SUCCESS:
                            plan_job_id = plan.job_id.rsplit("-", 1)[0]
                            await self._step_cache.save_outputs(plan_job_id, step.step_id, res.outputs)
                        return res
                    handler = caching_handler

                executor = create_executor(
                    tracked.strategy,
                    handler,
                    step_cache=self._step_cache,
                )
                result = await executor.run(tracked.plan)
                tracked.result = result
                tracked.status = result.status
        ...
        finally:
            self._active_jobs.discard(tracked.job_id)
```
- Rewrite `_register_built_in_local_workers` to instantiate classes:
```python
    async def _register_built_in_local_workers(self) -> None:
        from acheron.shell.local_handlers import (
            ExtractionHandler,
            ChunkingHandler,
            PackagingHandler,
            all_languages_caps,
        )
        
        handlers = {
            WorkerType.EXTRACTION: ExtractionHandler(self._settings.orchestrator.data_dir),
            WorkerType.CHUNKING: ChunkingHandler(
                self._settings.orchestrator.data_dir,
                self._settings.workers.chunking.max_chunk_length
            ),
            WorkerType.PACKAGING: PackagingHandler(
                self._settings.orchestrator.data_dir,
                self._settings.workers.packaging.bitrate,
                self._settings.workers.packaging.codec
            ),
        }
        
        for worker_type, handler in handlers.items():
            existing = await self._registry.find_by_type(worker_type)
            if existing:
                continue
            worker_id = f"{worker_type.value}-local"
            self._local_handlers[worker_id] = handler
            await self._registry.register(
                worker_id=worker_id,
                endpoint="local",
                transport="local",
                capabilities=all_languages_caps(worker_type),
                metadata={},
            )
```

- [ ] **Step 4: Update API App app.py**

Modify `src/acheron/shell/api/app.py`:
- Import `Settings`, `load_settings` from `config.py`.
- Pass `settings` into `create_app` and forward to `Orchestrator`.
```python
def create_app(
    registry: WorkerStore | None = None,
    job_store: JobStore | None = None,
    cache: PlanCache | None = None,
    data_dir: Path | str | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    if settings is None:
        settings = load_settings()
    if registry is None:
        registry = create_worker_store()
    if job_store is None:
        job_store = create_job_store()
    if cache is None:
        if data_dir is None:
            data_dir = settings.orchestrator.data_dir
        cache = PlanCache(data_dir)

    orchestrator = Orchestrator(
        registry=registry,
        cache=cache,
        job_store=job_store,
        settings=settings,
    )
```

- [ ] **Step 5: Fix broken imports/references in test_local_worker.py**

Since `BUILT_IN_LOCAL_HANDLERS` is removed from `local_handlers.py`, fix `tests/shell/test_local_worker.py` if it imports it or expects it.
Run: `pytest tests/shell/test_local_worker.py`
Expected: Fix any missing imports, ensure all tests pass.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/shell/test_orchestrator.py` and `pytest tests/shell/test_local_worker.py`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/acheron/shell/orchestrator.py src/acheron/shell/api/app.py tests/shell/test_local_worker.py tests/shell/test_orchestrator.py
git commit -m "feat: inject Settings into Orchestrator and initialize handlers with config"
```

---

### Task 6: Integration Tests EPUB Fixture & Mock File Writing

Add a pytest fixture to write a minimal valid EPUB zip file on the fly, export environment variables in uvicorn test fixtures, and update integration tests to write real files in mock handlers.

**Files:**
* Modify: `tests/integration/conftest.py`
* Modify: `tests/integration/test_worker_integration.py`
* Modify: `stubs/worker_stub.py`

- [ ] **Step 1: Set ACHERON_DATA_DIR in wired_orchestrator test fixture**

Modify `tests/integration/conftest.py` inside `wired_orchestrator` to set the environment variable:
```python
@pytest_asyncio.fixture
async def wired_orchestrator(
    tmp_path: Path,
    http_tts_stub: str,
    http_translation_stub: str,
    grpc_tts_stub: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Orchestrator]:
    # F-04: export ACHERON_DATA_DIR so uvicorn stub processes write files to the correct temp directory
    monkeypatch.setenv("ACHERON_DATA_DIR", str(tmp_path))
    # ... (remaining registration code) ...
```

- [ ] **Step 2: Add a minimal EPUB fixture**

Add to `tests/integration/conftest.py`:
```python
@pytest.fixture
def epub_file(tmp_path: Path) -> Path:
    epub_path = tmp_path / "test.epub"
    with zipfile.ZipFile(epub_path, "w") as z:
        container_xml = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
        z.writestr("META-INF/container.xml", container_xml)
        
        opf = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uuid_id" version="2.0">
  <manifest>
    <item href="ch1.xhtml" id="html1" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="html1"/>
  </spine>
</package>"""
        z.writestr("OEBPS/content.opf", opf)
        
        ch1 = "<html><body><p>Hello chapter one text.</p></body></html>"
        z.writestr("OEBPS/ch1.xhtml", ch1)
    return epub_path
```
Ensure you import `zipfile` at the top of `conftest.py`.

- [ ] **Step 3: Update integration tests to use epub_file fixture**

Modify `tests/integration/test_worker_integration.py` to use `epub_file` instead of `"/tmp/test.epub"`.
For example:
```python
    async def test_epub_full_pipeline(self, wired_orchestrator: Orchestrator, epub_file: Path) -> None:
        orch = wired_orchestrator
        request = EpubRequest(source_path=str(epub_file), source_language="en", target_language="es")
```

- [ ] **Step 4: Update ASR test stub handler to write real text file**

Modify `_asr_handler` in `tests/integration/test_worker_integration.py` to write its output file:
```python
        async def _asr_handler(job: Job) -> JobResult:
            plan_job_id = job.job_id.rsplit("-", 1)[0]
            data_dir = Path(os.environ.get("ACHERON_DATA_DIR", "/tmp"))
            trans_dir = data_dir / plan_job_id / "transcribe"
            trans_dir.mkdir(parents=True, exist_ok=True)
            out_path = trans_dir / f"{job.job_id}.txt"
            out_path.write_text("mock transcription", encoding="utf-8")
            
            return JobResult(
                job_id=job.job_id,
                status=JobStatus.SUCCESS,
                outputs=(
                    OutputFile(
                        path=str(out_path),
                        filename=f"{job.job_id}.txt",
                        size_bytes=out_path.stat().st_size,
                        checksum="",
                        content_type="text/plain",
                    ),
                ),
                metrics=JobMetrics(duration_seconds=0.01),
            )
```

- [ ] **Step 5: Update stubs/worker_stub.py to write real WAV files**

Modify the `execute` endpoint in `stubs/worker_stub.py` for TTS:
```python
        job_id = body.get("job_id", "unknown")
        if cfg["worker_type"] == "TTS":
            audio = _silent_wav()
            # F-04: Write mock wav file to step cache path
            plan_job_id = job_id.rsplit("-", 1)[0]
            data_dir = Path(os.environ.get("ACHERON_DATA_DIR", "/data/jobs"))
            step_dir = data_dir / plan_job_id / "synthesize"
            step_dir.mkdir(parents=True, exist_ok=True)
            out_path = step_dir / f"{job_id}.wav"
            out_path.write_bytes(audio)
            
            return {
                "job_id": job_id,
                "status": "success",
                "outputs": [
                    {
                        "path": str(out_path),
                        "filename": f"{job_id}.wav",
                        "size_bytes": len(audio),
                        "checksum": "",
                        "content_type": "audio/wav",
                    }
                ],
                "metrics": {"duration_seconds": 0.01},
                "error": None,
            }
```

- [ ] **Step 6: Run integration tests**

Run: `pytest tests/integration/test_worker_integration.py`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add tests/integration/conftest.py tests/integration/test_worker_integration.py stubs/worker_stub.py
git commit -m "test: add epub_file fixture and update integration tests & stubs to write real files"
```

---

### Task 7: Streaming Executor Cache Inspection (Resuming)

Update `StreamingExecutor._stage` to check if a step output manifest is already cached and valid on disk before running it, skipping execution if so.

**Files:**
* Modify: `src/acheron/shell/executors/streaming.py`
* Test: `tests/shell/test_streaming_executor.py`

- [ ] **Step 1: Write cache inspect tests**

Add a test case in `tests/shell/test_streaming_executor.py` to check that cached steps are skipped:
```python
@pytest.mark.asyncio
async def test_streaming_executor_skips_cached_steps(tmp_path: Path, step_cache: StepCache) -> None:
    job_id = "job-cache-test"
    plan = _linear_plan(job_id=job_id)
    
    extracted_file = tmp_path / "chapter_001.txt"
    extracted_file.parent.mkdir(parents=True, exist_ok=True)
    extracted_file.write_text("Extracted content", encoding="utf-8")
    
    import hashlib
    hasher = hashlib.sha256()
    hasher.update(b"Extracted content")
    checksum = hasher.hexdigest()
    
    outputs = (
        OutputFile(
            path=str(extracted_file),
            filename="chapter_001.txt",
            size_bytes=len("Extracted content"),
            checksum=checksum,
            content_type="text/plain"
        ),
    )
    await step_cache.save_outputs(job_id, "extract", outputs)
    
    handler_outputs = {
        "extract": [], 
        "chunk": [_real_output(tmp_path, "chunks.json", b"[]")],
        "package": [_real_output(tmp_path, "output.m4b", b"m4b")],
    }
    handler, calls = _make_handler(handler_outputs)
    
    executor = StreamingExecutor(handler, step_cache)
    result = await executor.run(plan)
    
    assert result.status == PlanStatus.COMPLETED
    assert "extract" not in calls
    assert "chunk" in calls
    assert "package" in calls
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/shell/test_streaming_executor.py -k test_streaming_executor_skips_cached_steps`
Expected: FAIL (both "extract" and "chunk" are in `calls`)

- [ ] **Step 3: Modify StreamingExecutor._stage to skip cached steps**

Modify `src/acheron/shell/executors/streaming.py`:
```python
            if upstream is not None:
                upstream_value = await upstream.get()
                if upstream_value is _END:
                    return
                    
            if await self._cache.step_has_valid_cache(plan.job_id, step.step_id):
                logger.info("Step %s has valid cache, skipping execution", step.step_id)
                outputs = await self._cache.load_outputs(plan.job_id, step.step_id)
                result = JobResult(
                    job_id=plan.job_id,
                    status=JobStatus.SUCCESS,
                    outputs=outputs,
                    metrics=JobMetrics(duration_seconds=0.0, cost_estimate=0.0),
                )
            else:
                try:
                    result = await asyncio.wait_for(
                        self._handler(step, plan),
                        timeout=self._step_timeout,
                    )
                except TimeoutError as exc:
                    msg = f"step {step.step_id} timed out after {self._step_timeout}s"
                    raise WorkerError(msg) from exc
                except AcheronError:
                    raise
                except Exception as exc:
                    msg = f"unexpected failure in stage {step.step_id}: {type(exc).__name__}"
                    raise PipelineError(msg) from exc

                record_cost(result.metrics.cost_estimate or 0.0)

                if result.status is not JobStatus.SUCCESS:
                    msg = f"step {step.step_id} returned {result.status.value}: {result.error or 'unknown error'}"
                    raise WorkerError(msg)

                try:
                    await self._cache.save_outputs(plan.job_id, step.step_id, result.outputs)
                except Exception as exc:
                    msg = f"save_outputs failed for step {step.step_id}"
                    raise PipelineError(msg) from exc

            await downstream.put(result)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/shell/test_streaming_executor.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/acheron/shell/executors/streaming.py tests/shell/test_streaming_executor.py
git commit -m "feat: implement Step Cache Inspection in StreamingExecutor stage execution"
```

---

### Task 8: Resuming Route & Orchestrator Flow

Implement `POST /jobs/{job_id}/resume` route and atomic Orchestrator execution resumption with `asyncio.Lock` concurrency guards.

**Files:**
* Modify: `src/acheron/core/errors.py`
* Modify: `src/acheron/shell/orchestrator.py`
* Modify: `src/acheron/shell/api/routes/jobs.py`
* Test: `tests/shell/test_orchestrator.py`
* Test: `tests/shell/api/test_jobs.py`

- [ ] **Step 1: Define exception classes**

Modify `src/acheron/core/errors.py`:
```python
class JobNotFoundError(AcheronError):
    """Job ID was not found in the store."""

class JobAlreadyRunningError(AcheronError):
    """Cannot resume a job that is currently running."""
```

- [ ] **Step 2: Write test for resume_job**

Add to `tests/shell/test_orchestrator.py`:
```python
from acheron.core.errors import JobAlreadyRunningError, JobNotFoundError

@pytest.mark.asyncio
async def test_resume_job(tmp_path: Path) -> None:
    reg = InMemoryWorkerStore()
    cache = PlanCache(tmp_path)
    orch = Orchestrator(registry=reg, cache=cache)
    await orch.start()
    
    with pytest.raises(JobNotFoundError):
        await orch.resume_job("nonexistent")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/shell/test_orchestrator.py -k test_resume_job`
Expected: FAIL (AttributeError: 'Orchestrator' has no attribute 'resume_job')

- [ ] **Step 4: Implement Orchestrator resume_job**

Modify `src/acheron/shell/orchestrator.py`:
- Implement `resume_job(self, job_id: str, force_fresh: bool = False) -> TrackedJob`:
```python
    async def resume_job(self, job_id: str, force_fresh: bool = False) -> TrackedJob:
        from acheron.core.errors import JobAlreadyRunningError, JobNotFoundError
        
        lock = self._job_locks.get(job_id)
        if lock is None:
            lock = asyncio.Lock()
            self._job_locks[job_id] = lock
            
        async with lock:
            tracked = await self._job_store.get(job_id)
            if tracked is None:
                raise JobNotFoundError(f"Job not found: {job_id}")
                
            if tracked.status == PlanStatus.RUNNING:
                if job_id in self._active_jobs:
                    raise JobAlreadyRunningError(f"Job {job_id} is already running")
                logger.warning("Job %s status is RUNNING but not active in this process. Overriding stale state.", job_id)
                
            # F-11: Warn when resuming sequential or async executor strategy
            if tracked.strategy != ExecutorStrategy.STREAMING:
                logger.warning(
                    "Job %s was run with strategy %s; resuming will re-run all steps from scratch.",
                    job_id,
                    tracked.strategy
                )
                
            if force_fresh:
                job_dir = self._step_cache.data_dir / job_id
                logger.info("force_fresh=True: deleting job step-cache directory: %s", job_dir)
                if job_dir.exists():
                    import shutil
                    await asyncio.to_thread(shutil.rmtree, job_dir, ignore_errors=True)
                    
            tracked.result = None
            tracked.status = PlanStatus.RUNNING
            
            # F-01: Update the store first, then immediately spawn task without awaits in between
            await self._job_store.put(tracked)
            task = asyncio.create_task(self._execute(tracked))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            
            return tracked
```
- In `Orchestrator._execute`, add database-level idempotency guard:
```python
        db_job = await self._job_store.get(tracked.job_id)
        if db_job is None or db_job.status != PlanStatus.RUNNING:
            logger.warning(
                "Idempotency guard: job %s has database status %s, skipping execution",
                tracked.job_id,
                db_job.status if db_job else "None"
            )
            return
```

- [ ] **Step 5: Create tests for jobs resume API route**

Add to `tests/shell/api/test_jobs.py`:
```python
    @pytest.mark.asyncio
    async def test_resume_job_route(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": "/input/book.epub",
                "source_language": "en",
                "target_language": "es",
            },
        )
        job_id = response.json()["job_id"]
        
        # Poll until the job is no longer running or pending
        for _ in range(50):
            status_resp = await client.get(f"/jobs/{job_id}")
            if status_resp.json()["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("Job did not complete in time")
        
        resume_resp = await client.post(f"/jobs/{job_id}/resume")
        assert resume_resp.status_code == 200
        assert resume_resp.json()["status"] == "running"
```

- [ ] **Step 6: Register POST /jobs/{job_id}/resume route**

Modify `src/acheron/shell/api/routes/jobs.py`:
```python
@router.post("/{job_id}/resume", response_model=JobResponse)
async def resume_job(job_id: str, orch: OrchestratorDep, force_fresh: bool = False) -> JobResponse:
    from acheron.core.errors import JobAlreadyRunningError, JobNotFoundError
    try:
        tracked = await orch.resume_job(job_id, force_fresh=force_fresh)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobAlreadyRunningError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to resume job: {exc}") from exc
    return _tracked_to_response(tracked)
```

- [ ] **Step 7: Run all tests**

Run: `pytest tests/shell/test_orchestrator.py` and `pytest tests/shell/api/test_jobs.py`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/acheron/core/errors.py src/acheron/shell/orchestrator.py src/acheron/shell/api/routes/jobs.py tests/shell/test_orchestrator.py tests/shell/api/test_jobs.py
git commit -m "feat: add resume job API endpoint and orchestrator logic with lock"
```

---

### Task 9: CLI Refactoring & Commands Restructuring

Update `AcheronClient` and restructure Click command groups to match the new command design.

**Files:**
* Modify: `src/acheron/api_client.py`
* Modify: `src/acheron/cli.py`
* Modify: `tests/shell/test_cli.py`

- [ ] **Step 1: Add resume and health to AcheronClient**

Modify `src/acheron/api_client.py`:
- Add `get_health(self)`:
```python
    async def get_health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self._base_url, transport=self._transport, verify=self._ssl_verify
        ) as client:
            resp = await client.get("/health")
            resp.raise_for_status()
            return cast("dict[str, Any]", resp.json())
```
- Add `resume_job(self, job_id: str, force_fresh: bool = False)`:
```python
    async def resume_job(self, job_id: str, force_fresh: bool = False) -> dict[str, Any]:
        params = {"force_fresh": str(force_fresh).lower()}
        async with httpx.AsyncClient(
            base_url=self._base_url, transport=self._transport, verify=self._ssl_verify
        ) as client:
            resp = await client.post(f"/jobs/{job_id}/resume", params=params)
            resp.raise_for_status()
            return cast("dict[str, Any]", resp.json())
```

- [ ] **Step 2: Modify tests in test_cli.py for command restructure**

F-10: Update CliRunner calls in `tests/shell/test_cli.py` to match the new nested subcommand structure.
For example, change:
`runner.invoke(main, ["submit", ...])` -> `runner.invoke(main, ["job", "submit", ...])`
`runner.invoke(main, ["status", "job-abc"])` -> `runner.invoke(main, ["job", "status", "job-abc"])`
And add tests for `status` (service health) and `job resume`.

- [ ] **Step 3: Run CLI tests to verify they fail**

Run: `pytest tests/shell/test_cli.py`
Expected: FAIL on restructured commands

- [ ] **Step 4: Refactor cli.py Click structure**

Modify `src/acheron/cli.py`:
- Remove old top-level commands `@main.command() submit`, `status`, `list_jobs`.
- Implement new repurposed top-level `status` command:
```python
@main.command()
def status() -> None:
    """Check service health and active workers."""
    client = _get_client()
    health_data = _run(client.get_health())
    workers_data = _run(client.list_workers())
    caps_data = _run(client.get_capabilities())
    
    console.print(f"Orchestrator connection: [green]OK[/green] (Status: {health_data.get('status', 'ok')})")
    
    counts: dict[str, int] = {}
    for w in workers_data:
        w_type = w.get("worker_type", "unknown")
        counts[w_type] = counts.get(w_type, 0) + 1
    
    table_workers = Table(title="Active Workers by Type")
    table_workers.add_column("Worker Type")
    table_workers.add_column("Count")
    for w_type, count in counts.items():
        table_workers.add_row(w_type, str(count))
    console.print(table_workers)
    
    table_caps = Table(title="Active Capabilities")
    table_caps.add_column("Source")
    table_caps.add_column("Target")
    for pair in caps_data:
        table_caps.add_row(pair.get("src", "-"), pair.get("dst", "-"))
    console.print(table_caps)
```
- Define `job` group and register subcommands `submit`, `status`, `resume`, `list`:
```python
@main.group()
def job() -> None:
    """Manage orchestrator jobs."""

@job.command("submit")
@click.argument("file", type=click.Path(exists=True))
@click.option("--src", required=True, help="Source language (ISO 639-1)")
@click.option("--dest", required=True, help="Target language (ISO 639-1)")
@click.option("--executor", default="streaming", show_default=True, help="Executor strategy")
@click.option("--asr", "asr_model", default=None, help="ASR model (for audio input)")
@click.option("--type", "source_type", default=None, help="Source type override (epub/audio)")
def submit_job(file: str, src: str, dest: str, executor: str, asr_model: str | None, source_type: str | None) -> None:
    """Submit a new job."""
    # (Same logic as old submit)
    # ...

@job.command("status")
@click.argument("job_id")
@click.option("--verbose", "-v", is_flag=True, help="Show step details")
def job_status(job_id: str, verbose: bool) -> None:
    """Check status of a job."""
    # (Same logic as old status)
    # ...

@job.command("resume")
@click.argument("job_id")
@click.option("--force-fresh", is_flag=True, help="Discard step-cache and restart")
def job_resume(job_id: str, force_fresh: bool) -> None:
    """Resume a suspended or failed job."""
    result = _run(_get_client().resume_job(job_id, force_fresh=force_fresh))
    console.print(f"Job resumed: [bold]{result['job_id']}[/bold]")
    console.print(f"Status: {result['status']}")

@job.command("list")
@click.option("--active", is_flag=True, help="Show only running jobs")
@click.option("--completed", is_flag=True, help="Show only completed/failed jobs")
def job_list(active: bool, completed: bool) -> None:
    """List all jobs."""
    # (Same logic as old list_jobs)
    # ...
```

- [ ] **Step 5: Run tests and verify they pass**

Run: `pytest tests/shell/test_cli.py`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/acheron/api_client.py src/acheron/cli.py tests/shell/test_cli.py
git commit -m "refactor: restructure CLI command groups and add resume support"
```

---

## Plan Verification

* Run `just validate` to ensure all type-checking, linting, imports logic, and test suites are 100% green.
