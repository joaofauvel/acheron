# Public Output Download URL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the public filesystem `OutputSummary.path` field with a server-relative `download_url` and make downloads serve the persisted artifact path from real step directories.

**Architecture:** Keep `OutputFile.path` as internal persistence/orchestration data. Expose each public output by its stable zero-based position in the persisted `PlanResult.outputs` tuple, producing URLs such as `/jobs/{job_id}/outputs/0`. The download route selects that stored output, verifies its resolved path remains beneath the configured job directory, and serves it with the stored metadata. Dashboard links use the orchestrator URL plus the canonical download URL.

**Tech Stack:** Python 3.14, FastAPI, Pydantic, Starlette `FileResponse`, pytest, Jinja2 dashboard templates.

## Global Constraints

- The project is greenfield; public wire contracts may change directly without compatibility defaults or legacy aliases.
- Operator-visible responses must not expose internal filesystem paths, credentials, URLs, or tracebacks.
- `OutputFile.path` remains internal and is never serialized into `JobResponse`.
- Output downloads remain unauthenticated in this change; authentication is a separate product decision and must not be introduced implicitly.
- The existing memory and Redis job stores remain supported without changing their internal `OutputFile` representation.
- Use TDD: write each failing behavior test, run it to confirm the expected failure, then implement the minimal fix.
- Run focused tests first, then the repository quality gates from `Justfile`.

---

### Task 1: Rename the public output contract and update consumers

**Files:**
- Modify: `src/acheron/core/schemas.py:19-25` — rename `OutputSummary.path` to `download_url`.
- Modify: `src/acheron/shell/api/routes/jobs.py:433-487` — emit `/jobs/{job_id}/outputs/{index}` for each persisted output.
- Modify: `src/acheron/cli.py:493-494` — render the download URL instead of a server filesystem path.
- Modify: `tests/core/test_schemas.py` — update public output model construction and serialization assertions.
- Modify: `tests/shell/api/test_jobs.py` — assert output URLs and update response fixtures.
- Modify: `tests/shell/test_cli.py` and `tests/test_api_client.py` — update output payload fixtures to use `download_url`.
- Modify: `docs/superpowers/specs/2026-07-29-phase-4c-job-visibility-control-design.md` — document `download_url` as the public field and state that `OutputFile.path` is internal.
- Modify: `docs/superpowers/plans/2026-07-29-phase-4c-job-visibility-control.md` — update the plan's response mapping and route contract to match the approved design.

**Interfaces:**
- Consumes: `TrackedJob.result.outputs`, whose order is persisted by the existing `PlanResult` stores.
- Produces: `OutputSummary(download_url: str, filename: str, size_bytes: int, content_type: str)` and URLs of the exact form `/jobs/{job_id}/outputs/{index}`.

- [ ] **Step 1: Write the failing contract tests**

Add assertions equivalent to:

```python
output = OutputSummary(
    download_url="/jobs/job-1/outputs/0",
    filename="result.m4b",
    size_bytes=5,
    content_type="audio/mp4",
)
assert output.model_dump()["download_url"] == "/jobs/job-1/outputs/0"
assert "path" not in output.model_dump()
```

Update the API response test to assert:

```python
assert response.json()["outputs"][0]["download_url"] == "/jobs/job-measured/outputs/0"
assert "path" not in response.json()["outputs"][0]
```

Update CLI/API fixtures so every public output object contains `download_url` and no `path`.

- [ ] **Step 2: Run the focused tests and verify the expected red failure**

Run:

```bash
uv run pytest --no-cov tests/core/test_schemas.py tests/shell/api/test_jobs.py tests/shell/test_cli.py tests/test_api_client.py -q
```

Expected: failures identify the removed `path` field and the old filesystem-path response/rendering assertions.

- [ ] **Step 3: Implement the public mapping**

Change `_tracked_to_response()` so it enumerates persisted outputs and constructs only the server-relative URL:

```python
outputs=[
    OutputSummary(
        download_url=f"/jobs/{tracked.job_id}/outputs/{index}",
        filename=output.filename,
        size_bytes=output.size_bytes,
        content_type=output.content_type,
    )
    for index, output in enumerate(result.outputs)
]
```

Do not read or serialize `output.path` in this public mapping. Change CLI rendering to label the field as a download URL. Update the design and plan documents so they no longer require a public filesystem path.

- [ ] **Step 4: Run the focused tests and verify green**

Run:

```bash
uv run pytest --no-cov tests/core/test_schemas.py tests/shell/api/test_jobs.py tests/shell/test_cli.py tests/test_api_client.py -q
```

Expected: PASS, with all public output payloads using `download_url`.

- [ ] **Step 5: Commit the contract change**

```bash
git add src/acheron/core/schemas.py src/acheron/shell/api/routes/jobs.py src/acheron/cli.py tests/core/test_schemas.py tests/shell/api/test_jobs.py tests/shell/test_cli.py tests/test_api_client.py docs/superpowers/specs/2026-07-29-phase-4c-job-visibility-control-design.md docs/superpowers/plans/2026-07-29-phase-4c-job-visibility-control.md
git commit -m "feat(api): expose output download URLs"
```

---

### Task 2: Serve persisted artifacts safely and update dashboard links

**Files:**
- Modify: `src/acheron/shell/api/routes/job_outputs.py:23-63` — select by output index and validate the stored `OutputFile.path` beneath the job directory.
- Modify: `dashboard/templates/partials/job_detail.html:28-34` — link to `orchestrator_url + output.download_url`.
- Modify: `dashboard/app.py:63-74,122-124` — remove the obsolete filename-based proxy route once the template uses the canonical orchestrator URL.
- Modify: `tests/shell/api/test_job_outputs.py` — test real nested artifacts, output-index selection, traversal/symlink rejection, and missing artifacts.
- Modify: `dashboard/tests/test_job_detail.py` — update payloads and assert the canonical download URL is rendered; replace the obsolete proxy test.

**Interfaces:**
- Consumes: `job_id`, integer `output_index`, and the selected persisted `OutputFile.path`.
- Produces: `GET /jobs/{job_id}/outputs/{output_index}` serving the selected artifact, or the existing structured 404 response.

- [ ] **Step 1: Write failing route tests for the real artifact layout**

Change the fixture artifact to a real step directory and request its index:

```python
output_path = tmp_path / "job-1" / "package" / "result.m4b"
output_path.parent.mkdir(parents=True)
output_path.write_bytes(b"audio")
# Store OutputFile(path=str(output_path), filename="result.m4b", ...)
response = await client_with_output.get("/jobs/job-1/outputs/0")
assert response.status_code == 200
assert response.content == b"audio"
```

Add a test proving an output whose stored path is outside `data_dir/job-1` returns 404, and a test proving a symlinked output path outside the job directory returns 404. Add a test for an out-of-range output index returning the structured 404 response.

- [ ] **Step 2: Run the focused route/dashboard tests and verify red**

Run:

```bash
uv run pytest --no-cov tests/shell/api/test_job_outputs.py dashboard/tests/test_job_detail.py -q
```

Expected: failures show the old filename route and flat-path resolver do not serve the nested stored artifact or parse `download_url` payloads.

- [ ] **Step 3: Implement safe stored-path serving**

Replace the flat filename resolver with a resolver that accepts the stored path:

```python
def safe_output_path(data_dir: Path, job_id: str, stored_path: str) -> Path:
    data_root = data_dir.resolve()
    job_root = (data_root / job_id).resolve(strict=True)
    resolved = Path(stored_path).resolve(strict=True)
    resolved.relative_to(job_root)
    if not resolved.is_file():
        raise FileNotFoundError(stored_path)
    return resolved
```

Translate `FileNotFoundError`, `OSError`, and `ValueError` into the existing structured `OutputNotFoundError` response. Use an integer output index to select `tracked.result.outputs[index]`, then pass that record's stored path to `safe_output_path`; never reconstruct a path from the public filename. Keep the existing content type and download filename from the selected `OutputFile`.

Update the dashboard detail template to use `{{ orchestrator_url }}{{ output.download_url }}` and remove the now-unused filename-based dashboard proxy implementation and its tests.

- [ ] **Step 4: Run the focused tests and verify green**

Run:

```bash
uv run pytest --no-cov tests/shell/api/test_job_outputs.py dashboard/tests/test_job_detail.py -q
```

Expected: PASS, including nested real artifacts and rejection of paths outside the job directory.

- [ ] **Step 5: Commit the route and dashboard change**

```bash
git add src/acheron/shell/api/routes/job_outputs.py dashboard/app.py dashboard/templates/partials/job_detail.html tests/shell/api/test_job_outputs.py dashboard/tests/test_job_detail.py
git commit -m "fix(api): serve outputs from stored artifact paths"
```

---

## Final validation

After both tasks:

```bash
just lint-strict
just type-check
just test
just validate
```

Also run a repository search confirming no public response fixture or consumer still expects `outputs[].path`, and confirm `git status --short` contains only the intentionally untracked review artifacts plus the committed implementation changes.
