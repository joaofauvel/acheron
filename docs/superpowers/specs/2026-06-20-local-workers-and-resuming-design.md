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
* Worker-specific settings (`max_chunk_length`, `bitrate`, `codec`) are threaded into the local handler constructors directly — handlers must accept these as constructor parameters.
* **Job ID Parsing in Handlers**: Since job IDs are suffixed per-step (e.g. `job_id-extract`), local handlers must resolve the parent plan's job ID by parsing `plan_job_id = job.job_id.rsplit("-", 1)[0]` to reference the correct cache directories under `{data_dir}/{plan_job_id}/`.


## 2. Built-in Local Workers Implementation

The built-in workers execute as `local` transport handlers registered by the orchestrator.

### A. EXTRACTION Worker
* **EPUB Source**:
  * Parses the EPUB archive using standard Python `zipfile`.
  * Locates the Package Document (`.opf`) file using standard `xml.etree.ElementTree`.
  * Resolves spine order: the `<spine>` element lists `<itemref idref="...">` entries referencing `id` attributes in the `<manifest>`. Resolve each `idref` to a `href` via the manifest before reading the XHTML content document. Do not assume `idref` == filename.
  * Strips HTML tags and writes each chapter as a plain text file (`chapter_001.txt`, `chapter_002.txt`, etc.) in the step cache directory under `{data_dir}/{plan_job_id}/extract/` (use configured `data_dir`, not a hardcoded `/data/jobs`).
* **Audio Source**:
  * Copies the source audio file directly to `{data_dir}/{plan_job_id}/extract/`.

### B. CHUNKING Worker
* Reads the chapter text files from `{data_dir}/{plan_job_id}/extract/`.
* Runs the chunking engine (`chunk_text()`) using configured `max_chunk_length`.
* Writes `chunks.json` (a list of serialized `Chunk` objects) to `{data_dir}/{plan_job_id}/chunk/`.

### C. PACKAGING Worker (Option B: Concat Demuxer)
* Reads the WAV outputs of the `synthesize` step from the step cache.
* Calculates the duration of each WAV file by reading the standard WAV RIFF header (sample rate, channel count, bytes per second, data block size). Assumes standard PCM WAV output from TTS workers — add an assertion or header validation on the `RIFF`/`WAVE` magic bytes to fail fast on unexpected formats.
* Sums the durations of all WAV files grouped by their `chapter_id` to determine chapter start/end timestamps.
* Generates an `FFMETADATA` file containing chapter titles and timestamps.
* Writes an `inputs.txt` concat demuxer file listing all WAV file paths.
* Invokes `ffmpeg` asynchronously via `asyncio.create_subprocess_exec`. Capture stdout/stderr. On non-zero exit code, raise `WorkerError` with the captured stderr:
  ```bash
  ffmpeg -f concat -safe 0 -i inputs.txt -i FFMETADATA -map_metadata 1 -c:a aac -b:a 128k output.m4b
  ```

## 3. Resuming Flow

### A. Step Cache Inspection
* In `StreamingExecutor._stage`, before executing a step, the executor runs:
  `await self._cache.step_has_valid_cache(plan.job_id, step.step_id)`
* If `True`, it loads outputs from the manifest and puts them downstream immediately, skipping worker dispatch.
* **Note:** This behavior is not present in the current (9a) implementation. The resume-aware `_stage` is the deliverable of this spec; 9a's `_stage` always dispatches unconditionally.

### B. API Endpoint `POST /jobs/{job_id}/resume`
* Accepts query parameter `force_fresh: bool = False`.
* If `force_fresh=True`, deletes the job's step-cache folder (`{data_dir}/{job_id}`). Log the deletion path before removing.
* If the job is already `RUNNING`, returns `400 Bad Request`.
* Status update and `_execute` spawning must be treated as an atomic unit: update status to `RUNNING` in the store **and immediately** spawn `_execute(tracked)` without any `await` in between. A crash between the two would leave the job permanently `RUNNING`. An idempotency guard in `_execute` (e.g. check that status is still `RUNNING` before proceeding) provides a safety net.
* Concurrent `resume` calls: two concurrent requests that both read `PENDING` before either writes `RUNNING` can race. If this becomes a concern, guard with a compare-and-swap update in the store or a per-job asyncio lock.

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
* Test stub workers (such as the ASR stub in `tests/integration/test_worker_integration.py` and the TTS stub in `stubs/worker_stub.py`) must write real, mock files to the filesystem in order to test resuming and step validation correctly.
* The `wired_orchestrator` test fixture must export the `ACHERON_DATA_DIR` environment variable to point to the pytest `tmp_path`, ensuring uvicorn stub processes write to the exact directory monitored by the orchestrator.


