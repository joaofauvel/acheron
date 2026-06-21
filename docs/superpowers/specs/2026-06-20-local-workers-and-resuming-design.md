# Local Workers and Resuming Design Spec

This specification defines the implementation details for the built-in local workers (Extraction, Chunking, Packaging), settings configuration via `acheron.yaml`, and the job resuming mechanics across the API, CLI, and executors.

## 1. settings Configuration (`acheron.yaml`)

We add a settings module utilizing `pydantic` in `src/acheron/shell/config.py`.

* A new `acheron.yaml` file (or custom path via `ACHERON_CONFIG_PATH`) defines the system parameters:
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
* At orchestrator startup, these settings are loaded, validated, and overwrite default variables.

## 2. Built-in Local Workers Implementation

The built-in workers execute as `local` transport handlers registered by the orchestrator.

### A. EXTRACTION Worker
* **EPUB Source**: 
  * Parses the EPUB archive using standard Python `zipfile`.
  * Locates the Package Document (`.opf`) file using standard `xml.etree.ElementTree`.
  * Extracts the XHTML documents in the order specified by the `<spine>` chapter sequence.
  * Strips HTML tags and writes each chapter as a plain text file (`chapter_001.txt`, `chapter_002.txt`, etc.) in the step cache directory `/data/jobs/{job_id}/extract/`.
* **Audio Source**: 
  * Copies the source audio file directly to `/data/jobs/{job_id}/extract/`.

### B. CHUNKING Worker
* Reads the chapter text files from `/data/jobs/{job_id}/extract/`.
* Runs the chunking engine (`chunk_text()`) using configured `max_chunk_length`.
* Writes `chunks.json` (a list of serialized `Chunk` objects) to `/data/jobs/{job_id}/chunk/`.

### C. PACKAGING Worker (Option B: Concat Demuxer)
* Reads the WAV outputs of the `synthesize` step from the step cache.
* Calculates the duration of each WAV file by reading the standard WAV RIFF header (sample rate, channel count, bytes per second, data block size).
* Sums the durations of all WAV files grouped by their `chapter_id` to determine chapter start/end timestamps.
* Generates an `FFMETADATA` file containing chapter titles and timestamps.
* Writes an `inputs.txt` concat demuxer file listing all WAV file paths.
* Invokes `ffmpeg` asynchronously:
  ```bash
  ffmpeg -f concat -safe 0 -i inputs.txt -i FFMETADATA -map_metadata 1 -c:a aac -b:a 128k output.m4b
  ```

## 3. Resuming Flow

### A. Step Cache Inspection
* In `StreamingExecutor._stage`, before executing a step, the executor runs:
  `await self._cache.step_has_valid_cache(plan.job_id, step.step_id)`
* If `True`, it loads outputs from the manifest and puts them downstream immediately, skipping worker dispatch.

### B. API Endpoint `POST /jobs/{job_id}/resume`
* Accepts query parameter `force_fresh: bool = False`.
* If `force_fresh=True`, deletes the job's step-cache folder (`/data/jobs/{job_id}`).
* If the job is already `RUNNING`, returns `400 Bad Request`.
* Otherwise, updates the job status to `RUNNING` in the store and spawns `_execute(tracked)` in the background.

## 4. CLI Refactoring

* **Service Status (`acheron status`)**:
  * Moved from checking a job's status.
  * Queries the orchestrator `/health`, `/workers`, and `/capabilities` endpoints.
  * Displays orchestrator connection, count of active workers by type, and active capabilities.
* **Job Subcommands Group (`acheron job`)**:
  * Group under `acheron job`:
    * `acheron job status <job_id>`
    * `acheron job resume <job_id> [--force-fresh]`
    * `acheron job submit` (moved from `submit`)
