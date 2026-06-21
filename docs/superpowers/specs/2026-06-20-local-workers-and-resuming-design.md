# Local Workers and Resuming Design Spec

This specification defines the implementation details for the built-in local workers (Extraction, Chunking, Packaging), settings configuration via `acheron.yaml`, and the job resuming mechanics across the API, CLI, and executors.

## 1. Settings Configuration (`acheron.yaml`)

We add a settings module utilizing `pydantic` in `src/acheron/shell/config.py`.

* A new `acheron.yaml` file (or custom path via `ACHERON_CONFIG_PATH`) defines the system parameters. Default search order: `$ACHERON_CONFIG_PATH` → `./acheron.yaml` → `/etc/acheron/acheron.yaml`.
  ```yaml
  orchestrator:
    data_dir: "/data/jobs"
    registration_token: "dev-registration-token"
    health_check_interval_seconds: 30

  workers:
    chunking:
      max_chunk_length: 250
    packaging:
      bitrate: "128k"
      codec: "aac"
  ```
* Settings are loaded at orchestrator startup via a `Settings` Pydantic model (using `pydantic-settings` for env-var interpolation). To ensure that environment variable overrides have priority over settings loaded from the YAML file, a custom `YamlConfigSettingsSource` is registered in `settings_customise_sources` rather than using standard initializers. The `Settings` instance is passed to `Orchestrator.__init__` rather than mutating module-level globals.
* **Source priority** (highest to lowest): init kwargs → structured env vars (`ACHERON_ORCHESTRATOR__DATA_DIR`) → flat env alias (`ACHERON_DATA_DIR` → `orchestrator.data_dir`) → YAML config file → defaults. The flat `ACHERON_DATA_DIR` alias exists for backward compatibility with existing docker-compose and test setups; the structured form takes priority.
* Worker-specific settings (`max_chunk_length`, `bitrate`, `codec`) are threaded into the local handler constructors directly — handlers must accept these as constructor parameters.
* **Job ID Parsing in Handlers**: Since job IDs are suffixed per-step (e.g. `job_id-extract`), local handlers must resolve the parent plan's job ID by parsing `plan_job_id = job.job_id.rsplit("-", 1)[0]` to reference the correct cache directories under `{data_dir}/{plan_job_id}/`.
* **Registration Token Auto-Generation & Persistence**: If `registration_token` is unset or `None` in the settings, the orchestrator checks if a persistent token file exists at `{data_dir}/.registration_token`. If it exists, the token is read and loaded. Otherwise, a secure token (`secrets.token_hex(16)`) is generated and written to `{data_dir}/.registration_token`. The `verify_registration_token` dependency in `src/acheron/shell/api/deps.py` retrieves this token from `orchestrator._settings.orchestrator.registration_token` rather than checking `os.environ` directly.
* **Malformed YAML**: If `ACHERON_CONFIG_PATH` points to a malformed YAML file, a warning is logged and the next search path is tried. `OSError` (file not found, permission denied) is silently skipped.


## 2. Built-in Local Workers Implementation

The built-in workers execute as `local` transport handlers registered by the orchestrator.

### A. EXTRACTION Worker
* **EPUB Source**:
  * Parses the EPUB archive using standard Python `zipfile`.
  * Locates the Package Document (`.opf`) file using stdlib `xml.etree.ElementTree`. EPUBs are user-uploaded, but Expat 2.7.1+ (bundled since Python 3.11) is not vulnerable to billion laughs, quadratic blowup, or large tokens (see [cpython#135294](https://github.com/python/cpython/pull/135294)). `defusedxml` was considered but is unmaintained (0.7.1 from 2021) and the Python docs removed the recommendation to use it. S314 is suppressed with a comment referencing the CPython PR.
  * Resolves spine order: the `<spine>` element lists `<itemref idref="...">` entries referencing `id` attributes in the `<manifest>`. Resolve each `idref` to a `href` via the manifest before reading the XHTML content document. Do not assume `idref` == filename.
  * Strips HTML tags and writes each chapter as a plain text file (`chapter_001.txt`, `chapter_002.txt`, etc.) in the step cache directory under `{data_dir}/{plan_job_id}/extract/` (use configured `data_dir`, not a hardcoded `/data/jobs`). Block-level HTML tags (`<p>`, `<h1>-<h6>`, `<div>`, `<br>`, `<li>`) insert spaces at both start and end tags to prevent word merging across block boundaries.
  * Manifest `href` values are URL-decoded (`urllib.parse.unquote`) to handle percent-encoded paths (e.g. `ch%201.xhtml`).
* **Audio Source**:
  * Copies the source audio file directly to `{data_dir}/{plan_job_id}/extract/`.

### B. CHUNKING Worker
* Reads upstream text outputs from the `StepCache`, trying `transcribe` (ASR) first, then `extract` (EPUB) — this allows the chunker to work for both audio and text pipelines without hardcoding `/extract`.
* Runs the chunking engine (`chunk_text()`) using configured `max_chunk_length`.
* Writes `chunks.json` (a list of serialized `Chunk` objects) to `{data_dir}/{plan_job_id}/chunk/`.

### C. PACKAGING Worker (Option B: Concat Demuxer)
* Reads the WAV outputs of the `synthesize` step from the step cache.
* Calculates the duration of each WAV file by reading the standard WAV RIFF header (sample rate, channel count, bytes per second, data block size). Assumes standard PCM WAV output from TTS workers — validates `RIFF`/`WAVE` magic bytes, PCM audio format, and non-zero byte rate to fail fast on unexpected formats.
* Sums the durations of all WAV files grouped by their `chapter_id` to determine chapter start/end timestamps.
* Generates an `FFMETADATA` file containing chapter titles and timestamps.
* Writes an `inputs.txt` concat demuxer file listing all WAV file paths resolved to **absolute** paths (FFmpeg resolves relative paths against the process CWD, not the inputs.txt location).
* Invokes `ffmpeg` asynchronously via `asyncio.create_subprocess_exec`. Capture stdout/stderr. On non-zero exit code, raise `WorkerError` with the captured stderr:
  ```bash
  ffmpeg -f concat -safe 0 -i inputs.txt -i FFMETADATA -map_metadata 1 -c:a aac -b:a 128k output.m4b
  ```

## 3. Resuming Flow

### A. Step Cache Inspection
* In `StreamingExecutor._stage`, before executing a step, the executor runs:
  `await self._cache.step_has_valid_cache(plan.job_id, step.step_id)`
* `step_has_valid_cache` validates: manifest exists, all referenced output files exist on disk, and SHA-256 checksums match. An empty checksum in the manifest always fails validation — stubs and handlers must compute real checksums.
* If `True`, it loads outputs from the manifest and puts them downstream immediately, skipping worker dispatch.
* **Note:** This behavior is not present in the current (9a) implementation. The resume-aware `_stage` is the deliverable of this spec; 9a's `_stage` always dispatches unconditionally.
* **Non-streaming executors**: `SequentialExecutor` and `AsyncExecutor` do not write to `StepCache` natively. The orchestrator wraps the step handler in a `caching_handler` for non-streaming strategies so successful step outputs are automatically saved to the cache, enabling local workers (which load from cache) to function correctly.

### B. API Endpoint `POST /jobs/{job_id}/resume`
* Accepts query parameter `force_fresh: bool = False`.
* If `force_fresh=True`, deletes the job's step-cache folder (`{data_dir}/{job_id}`) with `ignore_errors=True` to handle TOCTOU races. Log the deletion path before removing.
* If the job is already `RUNNING` **and** active in the current orchestrator process (`_active_jobs`), returns `400 Bad Request` (`JobAlreadyRunningError`).
* If the job is `RUNNING` but **not** active in the current process (stale state from a crash), the resume proceeds with a warning log. This prevents permanent lockout after orchestrator crashes.
* Status update and `_execute` spawning must be treated as an atomic unit: update status to `RUNNING` in the store **and immediately** spawn `_execute(tracked)` without any `await` in between. A crash between the two would leave the job permanently `RUNNING`. An idempotency guard in `_execute` provides a safety net.
* **Idempotency guard**: `_execute` queries the persistent job store (not in-memory state) to verify the job is still `RUNNING` before proceeding. This catches concurrent resume races.
* Concurrent `resume` calls: guarded with a per-job `asyncio.Lock` stored in a `weakref.WeakValueDictionary` (prevents memory leaks by GC-ing locks when no longer referenced).

## 4. CLI Refactoring

**Note:** Moving `acheron status <job_id>` to `acheron job status <job_id>` and `acheron submit` to `acheron job submit` are **breaking changes** for existing users. Implement the full restructure in a single commit — avoid a partially-migrated state where both old and new command forms exist simultaneously.

* **Service Status (`acheron status`)**:
  * Repurposed from per-job status to overall service health.
  * Queries the orchestrator `/health`, `/workers`, and `/capabilities` endpoints.
  * Displays orchestrator connection, count of active workers by type, and active capabilities.
* **Job Subcommands Group (`acheron job`)**:
  * Group under `acheron job`:
    * `acheron job status <job_id>`
    * `acheron job resume <job_id> [--force-fresh]`
    * `acheron job submit` (moved from `submit`)

## 5. Integration Testing Requirements
* Test stub workers (such as the ASR stub in `tests/integration/test_worker_integration.py` and the TTS stub in `stubs/worker_stub.py`) must write real, mock files to the filesystem with **real SHA-256 checksums** in order to test resuming and step validation correctly. Empty checksums cause `step_has_valid_cache` to always return `False`, defeating the cache-skip feature.
* The `wired_orchestrator` test fixture must export both `ACHERON_DATA_DIR` and `ACHERON_ORCHESTRATOR__DATA_DIR` environment variables to point to the pytest `tmp_path`, ensuring uvicorn stub processes write to the exact directory monitored by the orchestrator.
* An `epub_file` pytest fixture provides a minimal valid EPUB (with `container.xml`, OPF manifest/spine, and a chapter XHTML) for integration tests, replacing hardcoded `/tmp/test.epub` paths.


