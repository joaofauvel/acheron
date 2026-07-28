# Phase 4B Submission Preflight Design

**Stories:** OPS-015, OPS-018, OPS-024, OPS-025, OPS-029  
**Status:** Approved design

## Goal

Make job submission trustworthy before execution starts: the CLI transfers its local source file to the orchestrator, the API validates the server-side input reference and source-specific options, and capability discovery distinguishes worker-type inventory from language-pair support and unknown language filters.

## Scope

This bundle adds a small input-upload foundation followed by the five existing submission-preflight behaviors. It preserves the JSON job-submission contract: uploaded inputs return a server-relative `source_path`, and `POST /jobs` continues to accept `SubmitJobRequest` with that path.

Plan preview (`OPS-011`, `OPS-016`) remains a separate subsequent bundle. This bundle does not add job cancellation, model targeting, object-storage integration, or input-retention cleanup.

## Architecture

### Input upload

Add an authenticated `POST /inputs` endpoint accepting one multipart file. The endpoint streams the file into a temporary file below the configured data directory, enforces a fixed 2 GiB upper bound, then atomically renames it into:

```text
<data_dir>/inputs/<random-input-id>/<safe-filename>
```

The generated input ID prevents filename collisions. The stored filename is the basename only; directory components supplied by a client never become part of the destination path. The endpoint returns an `InputResponse` containing the relative POSIX `source_path`, original-safe filename, byte size, and optional content type.

The input store owns path resolution and atomic persistence. `resolve_source_path()` accepts only a non-empty relative path, resolves it below `data_dir`, rejects traversal and non-regular files, and returns the resolved path for preflight. Uploaded inputs remain available for subsequent job submission and are not deleted by this bundle; retention and cleanup belong to Phase 4D maintenance work.

The upload route uses the existing registration-token dependency. Open registration continues to require the existing explicit opt-in. No new dependency is needed because `python-multipart` is already installed.

### CLI and API client flow

`AcheronClient.upload_input(path)` streams a local file with its basename and content type to `POST /inputs`, validates the `InputResponse`, and returns the server-relative path. The existing `submit_job()` method remains JSON-based and receives that returned path.

`acheron job submit` keeps its local `click.Path(exists=True, file_okay=True, dir_okay=False)` check, uploads the file first, then submits the returned path. Source-type detection still uses the original local filename or an explicit `--type` override. Upload errors use the existing CLI HTTP-error handling and no job is submitted when upload fails.

Direct API callers may reference an already-mounted relative file under `ACHERON_DATA_DIR`; clients on another host should use `POST /inputs` first.

### Submission preflight

`POST /jobs` validates, in order, the source type, source-specific ASR option, and source path before invoking `Orchestrator.submit_job()`:

- `source_type == "audio"` requires a non-empty `asr_model`.
- `source_type != "audio"` rejects a supplied `asr_model` with status 422 and detail `asr_model is only valid for source_type='audio'`.
- The source path must resolve to a regular file under the orchestrator data directory. Missing, absolute, traversal, and directory paths return 422 with a detail identifying the requested path and expected data-directory location.

These checks are local and deterministic. A rejected request never compiles or persists a plan and never creates a tracked job. Existing strategy, language-path, authentication, and warning behavior remain unchanged.

### Capability discovery

Extend the capability response with a typed worker-inventory list:

```python
class WorkerCapability(BaseModel):
    worker_id: str
    worker_type: str
    model_source: str | None
    metadata: dict[str, JsonValue]
```

`GET /capabilities` retains its current language-pair response. With `type=tts|asr|translation`, it returns matching worker inventory in `workers` and an empty `language_pairs` list. Invalid type values return 422. The route filters registered workers by capability type and preserves deterministic worker-ID ordering.

When `src` or `dest` is supplied for language-pair discovery, validate each value against the union of all registered workers' input and output languages. An unknown value returns 422 with a structured detail naming the invalid language and sorted supported languages. A known but currently unachievable language pair still returns the existing empty result.

The API client keeps `get_capabilities()` for language pairs and adds `get_worker_capabilities(worker_type)`. The CLI adds `acheron capabilities --type tts|asr|translation`; typed output is a table with Worker ID, Model, and Voice, where Voice comes from `metadata.voice` and is rendered as `-` when absent. Existing `--src`/`--dest` language-pair output is unchanged.

## Error handling and invariants

- Uploads are written to a temporary file and atomically renamed only after the stream completes; failed or oversized uploads remove their temporary file.
- The upload destination is always generated below `data_dir`; client filenames cannot select directories or overwrite another input.
- The source-path resolver rejects absolute paths and symlink-resolved paths outside `data_dir`.
- Upload and job mutation routes use the existing registration-token dependency.
- Preflight failures use HTTP 422 and do not call the orchestrator submission method.
- Unknown capability languages are distinguishable from known-but-empty capability results.
- No existing `JobResponse`, worker readiness, TLS, Compose, or dashboard contracts change.

## Testing

Use in-process ASGI tests and TDD. Add focused tests for:

- Atomic input upload round trips, safe generated paths, content type/size reporting, traversal filenames, oversized streams, failed writes, and upload authentication.
- `AcheronClient.upload_input()` multipart request construction and response validation.
- CLI upload-then-submit ordering, source-type detection, upload failures, and unchanged successful/error exit behavior.
- Job preflight rejection for missing/traversing/absolute/directory paths, missing audio ASR, and ASR supplied for EPUB; assert the orchestrator is not invoked.
- Successful submission from an uploaded source and preservation of existing booting warnings and strategy errors.
- Typed capability inventory for TTS/ASR/translation, deterministic worker ordering, model/voice rendering, invalid type rejection, unknown source/destination language errors, and existing language-pair filters.
- Regression coverage for API auth and all existing job/capability tests, updating test fixtures to create or upload inputs under the test data directory.

Run the focused submission/API/CLI tests, then `just validate`, `just ux-validate`, and the relevant first-run step. The final UX verification must exercise the five journeys recorded by OPS-015, OPS-018, OPS-024, OPS-025, and OPS-029.

## Expected file boundaries

Modify:

- `src/acheron/shell/api/app.py` — include the input route.
- `src/acheron/shell/api/routes/jobs.py` — source-option and source-path preflight.
- `src/acheron/shell/api/routes/capabilities.py` — typed inventory and language validation.
- `src/acheron/shell/api/schemas.py` and `src/acheron/core/schemas.py` — upload and capability wire models.
- `src/acheron/api_client.py` — upload and typed capability methods.
- `src/acheron/cli.py` — upload-before-submit and typed capability table.
- `docs/ux_review/ops.md` — story metadata and verification references after implementation.

Create:

- `src/acheron/shell/input_store.py` — safe atomic input persistence and path resolution.
- `src/acheron/shell/api/routes/inputs.py` — authenticated upload endpoint.
- `tests/shell/test_input_store.py` — storage behavior.
- `tests/shell/api/test_inputs.py` — upload route behavior.
- `tests/test_api_client.py` — upload and typed-capability client behavior where appropriate.

Update mirrored tests under `tests/shell/api/`, `tests/shell/test_cli.py`, and `tests/core/test_schemas.py` as needed.
