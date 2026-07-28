# Phase 4B Submission Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upload local inputs to the orchestrator, validate submission prerequisites before plan compilation, and expose typed worker capability discovery for OPS-015, OPS-018, OPS-024, OPS-025, and OPS-029.

**Architecture:** A filesystem-only `InputStore` owns safe relative-path resolution and atomic streamed uploads below `ACHERON_DATA_DIR`. The authenticated `/inputs` route returns a server-relative source reference; the CLI uploads first and then uses the existing JSON `/jobs` contract. Job-route preflight resolves that reference to an allowlisted regular file and rejects source/ASR mismatches before calling the orchestrator. Capability discovery retains language-pair output while adding an optional typed worker-inventory view.

**Tech Stack:** Python 3.14, FastAPI multipart uploads, Pydantic v2, httpx, Click/Rich, pytest/pytest-asyncio, respx, existing in-process ASGI transports.

## Global Constraints

- Follow strict TDD: each behavior starts with a failing focused test, then the minimal implementation, then a focused green run.
- Add no dependencies; `python-multipart~=0.0` is already present in `pyproject.toml`.
- Define `MAX_INPUT_BYTES = 2 * 1024 * 1024 * 1024` as a fixed storage constant; do not add a configuration knob in this bundle.
- Upload destinations are generated below `<data_dir>/inputs/`; client filenames contribute only their basename and never select a directory or overwrite an existing input.
- `source_path` in `SubmitJobRequest` is a non-empty relative path below the orchestrator's configured data directory. Reject absolute paths, traversal, symlink escapes, missing paths, and directories with HTTP 422.
- The job route must resolve a valid relative source path to its absolute allowlisted path before constructing `EpubRequest` or `AudioRequest`, because local extraction resolves paths from the orchestrator process.
- `source_type == "audio"` requires a non-empty `asr_model`; a supplied `asr_model` for any other source type returns HTTP 422 with `asr_model is only valid for source_type='audio'`.
- Upload and job mutation routes use `RegistrationTokenDep`; CLI clients read `ACHERON_REGISTRATION_TOKEN` and send the bearer token for mutation requests so the documented secured Compose flow works.
- `GET /capabilities` without `type` preserves the current language-pair response. `type` supports only `tts`, `asr`, and `translation`; typed responses use `workers` and an empty `language_pairs` list.
- Unknown `src`/`dest` values are errors; known languages with no achievable pair retain the existing empty result. Supported-language lists are sorted and deterministic.
- Do not change plan preview, cancellation, model targeting, object-storage integration, input retention cleanup, worker readiness, TLS, Compose, dashboard, or `src/acheron/shell/api/routes/partials.py`.
- Preserve existing `JobResponse`, strategy validation, invalid-language-path handling, booting-warning behavior, API authentication, CLI remediation, and non-root contracts.
- Update README/UX metadata for user-visible behavior in the implementation/metadata workflow; do not modify unrelated story blocks.

---

## File Map

### Create

- `src/acheron/shell/input_store.py` — fixed-limit streamed input persistence, safe generated paths, and relative source resolution.
- `src/acheron/shell/api/routes/inputs.py` — authenticated multipart upload endpoint.
- `tests/shell/test_input_store.py` — input-store behavior and failure cleanup.
- `tests/shell/api/test_inputs.py` — upload route, auth, response, and submit integration.

### Modify

- `src/acheron/core/schemas.py` — add `InputResponse`, `WorkerCapability`, and `CapabilitiesResponse.workers`.
- `src/acheron/shell/api/schemas.py` — re-export the new wire schemas.
- `src/acheron/shell/api/app.py` — include the `/inputs` router.
- `src/acheron/shell/api/routes/jobs.py` — source-option and source-path preflight.
- `src/acheron/shell/api/routes/capabilities.py` — typed worker inventory and unknown-language validation.
- `src/acheron/api_client.py` — bearer headers, `upload_input()`, and typed capability retrieval.
- `src/acheron/cli.py` — upload-before-submit and `capabilities --type` output.
- `tests/core/test_schemas.py` — upload/capability response defaults and serialization.
- `tests/shell/api/test_jobs.py` — preflight behavior, valid relative fixtures, and regression cases.
- `tests/shell/api/test_capabilities.py` — typed views and unknown-language errors.
- `tests/shell/conftest.py` — create a data-directory input fixture for API submission tests.
- `tests/test_api_client.py` — upload, auth header, and typed-capability round trips.
- `tests/shell/test_cli.py` — upload-before-submit and typed capability rendering.
- `README.md` — document input upload behavior and typed capability command.
- `docs/ux_review/ops.md` — update OPS-015, OPS-018, OPS-024, OPS-025, and OPS-029 metadata/prose after implementation.

### Verify only

- `src/acheron/core/planner.py` — confirm direct planner behavior remains unchanged; planner tests continue to use arbitrary fixture paths because API preflight is a shell concern.
- `src/acheron/shell/local_handlers.py` — confirm absolute resolved paths remain within its existing allowlist.

---

## Ordered Implementation Tasks

### Task 1: Build the safe atomic input store

**Files:**
- Create: `src/acheron/shell/input_store.py`
- Create: `tests/shell/test_input_store.py`

**Interfaces:**

```python
MAX_INPUT_BYTES = 2 * 1024 * 1024 * 1024

@dataclass(frozen=True, slots=True)
class StoredInput:
    source_path: str
    filename: str
    size_bytes: int
    content_type: str | None

class InputTooLargeError(ValueError): ...
class InputPathError(ValueError): ...

class InputStore:
    def __init__(self, data_dir: Path) -> None: ...

    async def save(
        self,
        filename: str,
        content_type: str | None,
        chunks: AsyncIterator[bytes],
    ) -> StoredInput: ...

    def resolve_source_path(self, source_path: str) -> Path: ...
```

- [ ] **Step 1: Write failing store tests.**

  Add tests with an async chunk generator. Cover the exact behavior:

  ```python
  async def test_save_writes_generated_relative_path_and_metadata(tmp_path: Path) -> None:
      store = InputStore(tmp_path)

      async def chunks() -> AsyncIterator[bytes]:
          yield b"first"
          yield b" second"

      result = await store.save("nested/book.epub", "application/epub+zip", chunks())

      assert result.filename == "book.epub"
      assert result.source_path.startswith("inputs/")
      assert result.source_path.endswith("/book.epub")
      assert result.size_bytes == 12
      assert result.content_type == "application/epub+zip"
      assert (tmp_path / result.source_path).read_bytes() == b"first second"
  ```

  Also test that `resolve_source_path(result.source_path)` returns the same regular file; absolute paths, `../outside.epub`, empty strings, missing paths, directories, and symlink escapes raise `InputPathError`; a stream whose size exceeds `MAX_INPUT_BYTES` raises `InputTooLargeError` and leaves no file under `inputs/`; and an exception raised by the chunk iterator removes the temporary upload file.

- [ ] **Step 2: Run the focused tests and verify they fail.**

  Run:

  ```bash
  uv run pytest --no-cov tests/shell/test_input_store.py -q
  ```

  Expected: collection fails because `acheron.shell.input_store` and `InputStore` do not exist.

- [ ] **Step 3: Implement the minimal store.**

  Resolve and create `data_dir` in `__init__`. In `save()`, strip all client directory components with `Path(filename).name`, use `input` when the basename is empty, create a random input directory below `data_dir / "inputs"`, and write chunks to a temporary file below `data_dir / ".inputs-tmp"`. Count bytes while streaming; reject the write before it exceeds `MAX_INPUT_BYTES`; close and unlink the temporary file on every exception. After a complete stream, atomically move it with `os.replace()` to the generated destination and return a POSIX relative `source_path`.

  In `resolve_source_path()`, reject empty or absolute values, resolve `(data_dir / source_path)` without requiring it to exist, require `resolved.is_relative_to(data_dir)`, require `resolved.is_file()`, and reject symlink targets outside the data directory. Keep the error messages stable enough for the API route to render the requested path and expected data-directory path.

- [ ] **Step 4: Run the focused tests and verify they pass.**

  Run:

  ```bash
  uv run pytest --no-cov tests/shell/test_input_store.py -q
  uv run ruff check src/acheron/shell/input_store.py tests/shell/test_input_store.py
  ```

  Expected: all store behavior tests pass without adding a dependency.

### Task 2: Add upload and capability wire schemas

**Files:**
- Modify: `src/acheron/core/schemas.py`
- Modify: `src/acheron/shell/api/schemas.py`
- Modify: `tests/core/test_schemas.py`

**Interfaces:**

```python
class InputResponse(BaseModel):
    source_path: str
    filename: str
    size_bytes: int
    content_type: str | None = None

class WorkerCapability(BaseModel):
    worker_id: str
    worker_type: str
    model_source: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

class CapabilitiesResponse(BaseModel):
    language_pairs: list[LanguagePair]
    workers: list[WorkerCapability] = Field(default_factory=list)
```

- [ ] **Step 1: Add failing schema tests.**

  Add tests for the new defaults and serialization:

  ```python
  def test_input_response_preserves_upload_metadata() -> None:
      response = InputResponse(
          source_path="inputs/id/book.epub",
          filename="book.epub",
          size_bytes=12,
          content_type="application/epub+zip",
      )
      assert InputResponse.model_validate(response.model_dump()) == response

  def test_capabilities_response_defaults_worker_inventory_to_empty() -> None:
      response = CapabilitiesResponse(language_pairs=[])
      assert response.workers == []

  def test_worker_capability_preserves_model_and_metadata() -> None:
      response = WorkerCapability(
          worker_id="tts-1",
          worker_type="tts",
          model_source="Qwen/Qwen3-TTS",
          metadata={"voice": "vivian"},
      )
      assert response.model_dump()["metadata"] == {"voice": "vivian"}
  ```

- [ ] **Step 2: Run the schema tests and verify they fail.**

  Run:

  ```bash
  uv run pytest --no-cov tests/core/test_schemas.py -q
  ```

  Expected: import/constructor failures for the new schema types.

- [ ] **Step 3: Implement and re-export the schemas.**

  Import `JsonValue` into `core.schemas`, add the three models with the exact defaults above, add `workers` to `CapabilitiesResponse`, and re-export `InputResponse` and `WorkerCapability` from `shell/api/schemas.py` alongside the existing response models.

- [ ] **Step 4: Run the schema/type gate.**

  Run:

  ```bash
  uv run pytest --no-cov tests/core/test_schemas.py -q
  uv run mypy src/acheron/core/schemas.py src/acheron/shell/api/schemas.py tests/core/test_schemas.py
  ```

  Expected: all schema tests pass and no new type errors appear.

### Task 3: Implement the authenticated `/inputs` route

**Files:**
- Create: `src/acheron/shell/api/routes/inputs.py`
- Modify: `src/acheron/shell/api/app.py`
- Create: `tests/shell/api/test_inputs.py`

**Interfaces:**

```python
@router.post("", response_model=InputResponse, status_code=201)
async def upload_input(
    file: UploadFile,
    orch: OrchestratorDep,
    _token: RegistrationTokenDep,
) -> InputResponse: ...
```

- [ ] **Step 1: Write failing route tests.**

  Use `httpx.AsyncClient(transport=ASGITransport(app=app))` and the existing `create_app()` test setup. Test that posting `files={"file": ("nested/book.epub", b"epub-bytes", "application/epub+zip")}` returns 201, the returned relative path is under `inputs/`, and the stored bytes are exact. Test that a token-protected app returns 401 without `Authorization: Bearer <token>` and succeeds with the configured token; test that an oversized stream maps to 413 and does not leave a temporary file; test that the route closes the upload and reports the original content type and byte count.

  Keep job submission integration and source-option tests in Task 6, after the source resolver is wired into the job route.

- [ ] **Step 2: Run the route tests and verify they fail.**

  Run:

  ```bash
  uv run pytest --no-cov tests/shell/api/test_inputs.py -q
  ```

  Expected: import/route-not-found failures because `/inputs` is not registered.

- [ ] **Step 3: Implement the route and register it.**

  Create a 1 MiB chunk async generator around `UploadFile.read()`, pass it to `InputStore(orch.settings.orchestrator.data_dir).save()`, and close the upload in `finally`. Map `InputTooLargeError` to `HTTPException(status_code=413, detail="input exceeds the 2 GiB upload limit")`; map `ValueError` from an invalid filename to 422; return `InputResponse` from the `StoredInput` dataclass. Include the router in `app.py` with `prefix="/inputs"` and the existing auth dependency on the route.

- [ ] **Step 4: Run the route tests and verify they pass.**

  Run:

  ```bash
  uv run pytest --no-cov tests/shell/api/test_inputs.py -q
  uv run pytest --no-cov tests/shell/api/test_inputs.py tests/shell/test_input_store.py -q
  ```

  Expected: upload round trips, auth, size, cleanup, and route registration pass.

### Task 4: Add API-client upload/auth support

**Files:**
- Modify: `src/acheron/api_client.py`
- Modify: `tests/test_api_client.py`

**Interfaces:**

```python
class AcheronClient:
    def __init__(
        self,
        base_url: str = "https://localhost:8000",
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        verify: bool | str | Path = True,
        registration_token: str | None = None,
    ) -> None: ...

    async def upload_input(self, path: str | Path) -> InputResponse: ...
    async def get_worker_capabilities(self, worker_type: str) -> list[WorkerCapability]: ...
```

- [ ] **Step 1: Write failing client tests.**

  Add a respx test that mocks `POST http://test/inputs`, calls `AcheronClient("http://test", registration_token="secret").upload_input(tmp_path / "book.epub")`, and asserts the multipart filename/content plus `Authorization: Bearer secret`; assert the returned `InputResponse.source_path`. Add a test that `submit_job()` sends the same bearer header when a token is configured. Add a typed-capability response test for `GET /capabilities?type=tts` returning `workers` and assert `get_worker_capabilities("tts")` returns a `WorkerCapability` list. Keep the existing warning round-trip test unchanged except for its optional auth setup.

- [ ] **Step 2: Run the client tests and verify they fail.**

  Run:

  ```bash
  uv run pytest --no-cov tests/test_api_client.py -q
  ```

  Expected: `upload_input` and `get_worker_capabilities` are missing and mutation requests have no auth header.

- [ ] **Step 3: Implement the client methods and bearer headers.**

  Store the optional registration token and expose a private `_mutation_headers()` returning `{}` when absent or `{"Authorization": f"Bearer {token}"}` when present. Apply it to `upload_input()`, `submit_job()`, and `resume_job()`; leave GET methods unchanged. Open the local file with a context manager, derive the MIME type with `mimetypes.guess_type()` falling back to `application/octet-stream`, send it as multipart, call `raise_for_status()`, and validate `InputResponse.model_validate(resp.json())`. Add `get_worker_capabilities()` using the `type` query and `CapabilitiesResponse.model_validate(resp.json()).workers`.

- [ ] **Step 4: Run the client tests and verify they pass.**

  Run:

  ```bash
  uv run pytest --no-cov tests/test_api_client.py -q
  uv run ruff check src/acheron/api_client.py tests/test_api_client.py
  ```

  Expected: upload multipart, auth, warning, and typed-capability client tests pass.

### Task 5: Make the CLI upload before submitting

**Files:**
- Modify: `src/acheron/cli.py`
- Modify: `tests/shell/test_cli.py`
- Modify: `README.md`

- [ ] **Step 1: Write failing CLI tests.**

  Update successful submit tests to mock `POST /inputs` before `POST /jobs`; assert the job request contains the server-relative path returned by the upload response and that both requests carry the configured bearer token. Add an audio test proving `--asr` is preserved after upload, a `--type epub` test proving detection uses the original filename, and an upload HTTP-error test proving no `/jobs` request is made. Add a typed capability test mocking `GET /capabilities?type=tts` and assert the output table contains `Worker ID`, `Model`, `Voice`, `tts-1`, `Qwen/Qwen3-TTS`, and `vivian`; assert absent voice renders `-`. Preserve all existing CLI remediation/error tests by adding the successful upload mock before their expected job response.

- [ ] **Step 2: Run the CLI tests and verify they fail.**

  Run:

  ```bash
  uv run pytest --no-cov tests/shell/test_cli.py -q
  ```

  Expected: request mocks do not match because the CLI currently posts directly to `/jobs`, and `--type` does not select typed capability output.

- [ ] **Step 3: Implement upload-before-submit and typed capability output.**

  Change the submit argument to `click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path)`. Pass `ACHERON_REGISTRATION_TOKEN` from `_get_client()` into `AcheronClient`. In `submit()`, call `_run(_get_client().upload_input(file))` first; pass `uploaded.source_path` to the existing `submit_job()` call; preserve the original suffix for `_detect_source_type()` and the explicit `--type` override. Use the existing generic `_print_http_error` callback for upload failures so `_run()` exits before submission.

  Add `@click.option("--type", "worker_type", type=click.Choice(("tts", "asr", "translation")))` to `capabilities()`. Reject simultaneous `--type` and `--src/--dest` with a Click usage error. For typed output, call `get_worker_capabilities()`, print `Worker ID`, `Model`, and `Voice`, and convert non-string/missing `metadata["voice"]` to `-`. Keep the existing language-pair table and empty message for calls without `--type`.

  Update README Basic CLI Commands with `acheron capabilities --type tts`, and document that the CLI uploads the local source before submitting it to the orchestrator.

- [ ] **Step 4: Run the CLI tests and verify they pass.**

  Run:

  ```bash
  uv run pytest --no-cov tests/shell/test_cli.py -q
  uv run ruff check src/acheron/cli.py tests/shell/test_cli.py
  ```

  Expected: all existing success/error/remediation tests plus upload and typed-capability tests pass with unchanged exit semantics.

### Task 6: Add job-route source and ASR preflight

**Files:**
- Modify: `src/acheron/shell/api/routes/jobs.py`
- Modify: `tests/shell/api/test_jobs.py`
- Modify: `tests/shell/conftest.py`
- Modify: `tests/shell/api/test_inputs.py`

**Interfaces:**

```python
def _resolve_submission_source(orch: Orchestrator, source_path: str) -> Path: ...
```

- [ ] **Step 1: Prepare valid API test inputs and write failing preflight tests.**

  In `tests/shell/conftest.py`, have `make_app(tmp_path)` create `tmp_path / "input" / "book.epub"` with deterministic bytes and update API submission payloads from absolute `/input/book.epub` to relative `input/book.epub`. For custom-app tests, create their input below that app's `tmp_path` explicitly. Do not change direct planner, local-handler, store, or transport fixtures that intentionally use arbitrary filesystem paths outside the HTTP API.

  Add an upload-to-submit integration test using the `/inputs` response as the `source_path`, then add route tests that post:

  ```python
  {"source_type": "epub", "source_path": "missing.epub", "source_language": "en", "target_language": "es"}
  {"source_type": "epub", "source_path": "../outside.epub", "source_language": "en", "target_language": "es"}
  {"source_type": "epub", "source_path": "/tmp/book.epub", "source_language": "en", "target_language": "es"}
  {"source_type": "audio", "source_path": "input/book.mp3", "source_language": "en", "target_language": "es"}
  {"source_type": "epub", "source_path": "input/book.epub", "source_language": "en", "target_language": "es", "asr_model": "whisper-v3"}
  ```

  Assert HTTP 422, the exact ASR detail strings, source-path detail containing `source_path` and `expected at`, and that a spy/fake orchestrator's `submit_job()` was never called. Add a successful relative-path test asserting the fake orchestrator receives an absolute path below `tmp_path`. Add directory and symlink-escape cases. Preserve the existing invalid strategy/source-type/auth/booting-warning tests.

- [ ] **Step 2: Run the focused job tests and verify the new assertions fail.**

  Run:

  ```bash
  uv run pytest --no-cov tests/shell/api/test_jobs.py tests/shell/api/test_inputs.py -q
  ```

  Expected: existing tests using missing/absolute paths fail after the fixture updates until the route preflight and internal path resolution are implemented; the new source/ASR assertions are red.

- [ ] **Step 3: Implement deterministic preflight.**

  Before constructing the request, retain the existing invalid strategy and source-type checks, then reject `body.asr_model is None` for audio and `body.asr_model is not None` for non-audio with HTTP 422. Call `InputStore(orch.settings.orchestrator.data_dir).resolve_source_path(body.source_path)`. Map `InputPathError` to HTTP 422 with `source_path not found: <requested>; expected at <data_dir>/<requested>` for missing/regular-file failures and a clear relative-path error for absolute/traversal failures. Construct `EpubRequest`/`AudioRequest` with `source_path=str(resolved_path)` so the existing `ExtractionHandler` allowlist can open the uploaded file from the orchestrator process.

  Keep `orch.submit_job()` as the first operation that can compile/persist a plan. Preserve the current post-acceptance worker-warning collection and exception isolation. Do not move readiness inspection ahead of preflight or alter response status codes for existing errors.

- [ ] **Step 4: Run the preflight tests and verify they pass.**

  Run:

  ```bash
  uv run pytest --no-cov tests/shell/api/test_jobs.py tests/shell/api/test_inputs.py -q
  uv run ruff check src/acheron/shell/api/routes/jobs.py tests/shell/api/test_jobs.py tests/shell/conftest.py
  ```

  Expected: missing, traversal, absolute, directory, symlink, ASR mismatch, valid upload, warning, and auth behavior pass.

### Task 7: Add typed capability inventory and unknown-language errors

**Files:**
- Modify: `src/acheron/shell/api/routes/capabilities.py`
- Modify: `tests/shell/api/test_capabilities.py`
- Modify: `tests/test_api_client.py`
- Modify: `tests/core/test_schemas.py`

- [ ] **Step 1: Write failing capability tests.**

  Extend the shared test registry with deterministic metadata/model fixtures and add tests for:

  ```python
  response = await client.get("/capabilities", params={"type": "tts"})
  assert response.status_code == 200
  assert response.json()["language_pairs"] == []
  assert [worker["worker_id"] for worker in response.json()["workers"]] == ["tts-1", "tts-2"]
  assert response.json()["workers"][0]["metadata"]["voice"] == "vivian"
  ```

  Add equivalent ASR and translation filters, invalid `type=bogus` → 422, and `type` combined with `src`/`dest` → 422. Add `src=xx` and `dest=xx` tests asserting 422 with the invalid value and sorted supported-language list. Add a known-but-empty pair test that remains 200 with `language_pairs: []`. Keep the current `src=en` and `dest=es` tests.

- [ ] **Step 2: Run capability tests and verify the new assertions fail.**

  Run:

  ```bash
  uv run pytest --no-cov tests/shell/api/test_capabilities.py tests/core/test_schemas.py tests/test_api_client.py -q
  ```

  Expected: the response has no `workers` inventory, `type` is ignored/unrecognized, and unknown languages return the old empty response.

- [ ] **Step 3: Implement capability route behavior.**

  Accept `type` through a query alias such as `worker_type: str | None = Query(None, alias="type")`. Load a single deterministic worker snapshot with `await orch.list_workers()`. For typed mode, validate the requested value against `tts`, `asr`, and `translation`, reject language filters, sort matching workers by `worker_id`, and map each to `WorkerCapability(worker_id, worker_type.value, model_source, metadata)`. Return `CapabilitiesResponse(language_pairs=[], workers=...)`.

  For language-pair mode, compute `supported_languages = sorted(union of every worker capability's input and output languages)`. Before calling `orch.get_capabilities()`, reject unknown `src` or `dest` with HTTP 422 detail strings such as `source language 'xx' is not supported by any registered worker; supported sources: de, en, es, fr`; preserve the existing pair aggregation for known values. Return `workers=[]` in the normal language-pair response.

- [ ] **Step 4: Run the capability tests and verify they pass.**

  Run:

  ```bash
  uv run pytest --no-cov tests/shell/api/test_capabilities.py tests/core/test_schemas.py tests/test_api_client.py -q
  uv run mypy src/acheron/shell/api/routes/capabilities.py src/acheron/core/schemas.py tests/shell/api/test_capabilities.py
  ```

  Expected: typed inventories, deterministic ordering, unknown-language errors, known-empty results, and client round trips pass.

### Task 8: Refresh UX metadata and perform independent review

**Files:**
- Modify: `docs/ux_review/ops.md`
- Verify: all files in Tasks 1–7 and `README.md`

- [ ] **Step 1: Update the five story blocks after behavior is complete.**

  In `docs/ux_review/ops.md`, update only OPS-015, OPS-018, OPS-024, OPS-025, and OPS-029. Set each story to `status: fixed`, `fixed_in: [pending]`, `verified_in: []`, `last_verified_at: {}`, and `verified_by: ""`. Refresh the cited paths/line ranges and recommendation/verification prose to describe the implemented upload, typed capability, source-path, and ASR behavior. Preserve each story ID, severity, discovery channels, journey stage, related IDs, and feedback reference; do not add a `pending` value to any field other than `fixed_in`.

- [ ] **Step 2: Validate documentation before review.**

  Run:

  ```bash
  uv run python -m acheron.ux_review.validate --root docs/ux_review --head "$(git rev-parse HEAD)" --strict
  just ux-validate
  ```

  Expected: all cited files and line ranges resolve, and the five stories are valid `fixed` records awaiting post-merge journey verification.

- [ ] **Step 3: Run two fresh-context reviews against the complete working-tree diff.**

  Review `git diff -- .` independently for:

  - upload atomicity, fixed limit, cleanup, basename/path traversal, symlink escape, and relative response references;
  - bearer-token behavior for `/inputs`, `/jobs`, and CLI mutation calls;
  - absolute internal path resolution and unchanged local-handler allowlisting;
  - audio/EPUB ASR guards and the guarantee that rejected requests never call `submit_job()`;
  - typed capability response shape, worker ordering, voice/model rendering, unknown-language distinction, and existing pair filters;
  - no accidental changes to readiness warnings, TLS, Compose, dashboard, planner semantics, or direct non-HTTP fixtures;
  - README and all five UX story blocks matching the actual behavior.

  Resolve every blocker in the relevant file, rerun the affected focused tests, and repeat the affected review before creating the implementation commit.

- [ ] **Step 4: Inspect scope and run pre-commit gates.**

  Run:

  ```bash
  git diff --check
  git diff --stat
  git status --short
  uv run pytest --no-cov tests/shell/test_input_store.py tests/shell/api/test_inputs.py tests/shell/api/test_jobs.py tests/shell/api/test_capabilities.py tests/test_api_client.py tests/shell/test_cli.py tests/core/test_schemas.py -q
  just validate
  just ux-validate
  just first-run --step 3
  ```

  Confirm that `src/acheron/shell/api/routes/partials.py` is unchanged and no plan-preview files or new dependencies are staged.

### Task 9: Commit implementation, resolve metadata, and verify journeys

**Files:**
- Implementation commit: all production, test, README, and UX files from Tasks 1–8 except the plan file.
- Metadata commit: `docs/ux_review/ops.md` only.

- [ ] **Step 1: Create the immutable implementation commit.**

  Stage exactly the files from the implementation file map, not `docs/superpowers/plans/2026-07-28-phase-4b-submission-preflight.md`, and verify the staged list. Require the five story blocks to contain `fixed_in: [pending]` and empty verification fields. Commit with:

  ```bash
  git add src/acheron/core/schemas.py src/acheron/shell/input_store.py src/acheron/shell/api/schemas.py src/acheron/shell/api/routes/inputs.py src/acheron/shell/api/routes/jobs.py src/acheron/shell/api/routes/capabilities.py src/acheron/shell/api/app.py src/acheron/api_client.py src/acheron/cli.py tests/shell/test_input_store.py tests/shell/api/test_inputs.py tests/shell/api/test_jobs.py tests/shell/api/test_capabilities.py tests/shell/conftest.py tests/test_api_client.py tests/shell/test_cli.py tests/core/test_schemas.py README.md docs/ux_review/ops.md
  git diff --cached --name-only
  git commit -m "fix(OPS-015,OPS-018,OPS-024,OPS-025,OPS-029): add submission preflight"
  ```

  The implementation commit must not include the plan file or any `last_verified_at.commit: pending` value.

- [ ] **Step 2: Resolve `fixed_in` in a metadata-only commit.**

  Capture the implementation SHA and replace exactly the five story-local `fixed_in: [pending]` values with that SHA. Do not amend the implementation commit or modify unrelated story blocks:

  ```bash
  IMPLEMENTATION_SHA="$(git rev-parse HEAD)"
  uv run python - "$IMPLEMENTATION_SHA" <<'PY'
  from pathlib import Path
  import sys

  sha = sys.argv[1]
  path = Path("docs/ux_review/ops.md")
  text = path.read_text(encoding="utf-8")
  for story_id in ("OPS-015", "OPS-018", "OPS-024", "OPS-025", "OPS-029"):
      start = text.index(f"## {story_id} ")
      end = text.find("\n## ", start + 1)
      end = len(text) if end < 0 else end
      block = text[start:end]
      if block.count("fixed_in: [pending]") != 1:
          raise SystemExit(f"expected one pending fixed_in in {story_id}")
      block = block.replace("fixed_in: [pending]", f"fixed_in: [{sha}]", 1)
      text = text[:start] + block + text[end:]
  path.write_text(text, encoding="utf-8")
  PY
  git diff --check
  git add docs/ux_review/ops.md
  git commit -m "docs(ux-review): record submission preflight fix"
  ```

- [ ] **Step 3: Run post-metadata verification.**

  Run:

  ```bash
  uv run pytest --no-cov tests/shell/test_input_store.py tests/shell/api/test_inputs.py tests/shell/api/test_jobs.py tests/shell/api/test_capabilities.py tests/test_api_client.py tests/shell/test_cli.py tests/core/test_schemas.py -q
  just validate
  just ux-validate
  just first-run --step 3
  just ux-verify OPS-015
  just ux-verify OPS-018
  just ux-verify OPS-024
  just ux-verify OPS-025
  just ux-verify OPS-029
  git status --short
  git diff --cached --quiet
  ```

  Expected: all focused tests, repository gates, first-run coverage, and five story verifiers pass; the final working tree is clean and the metadata commit contains only `docs/ux_review/ops.md`.

---

## Plan Self-Review Checklist

- **Spec coverage:** upload foundation, atomic storage, fixed limit, auth, CLI flow, JSON job contract, source-path resolution, ASR validation, typed inventory, unknown-language errors, deterministic ordering, documentation, TDD, and verification are covered by Tasks 1–9.
- **Placeholder scan:** the plan contains no unfinished markers or implementation placeholders.
- **Type consistency:** `StoredInput`, `InputStore.save()`, `InputResponse`, `WorkerCapability`, `AcheronClient.upload_input()`, and `get_worker_capabilities()` are defined before their route/client/CLI consumers.
- **Scope check:** plan preview and unrelated recovery/deployment features are explicitly excluded; direct planner and transport tests remain untouched except for verification.
- **Commit safety:** the plan file is never staged in the implementation commit; UX `fixed_in` is resolved only in the separate metadata commit.
