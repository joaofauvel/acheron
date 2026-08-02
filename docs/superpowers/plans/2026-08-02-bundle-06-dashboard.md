# Dashboard URL Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `06-dashboard` for `OPS-033`: preserve HTMX partial navigation while making job-detail URLs reloadable and shareable as complete dashboard pages.

**Architecture:** The existing `/partials/jobs/{job_id}` endpoint remains an HTMX fragment. A new `/jobs/{job_id}` route renders the full `index.html` shell with the selected detail. Job links use the durable URL for normal navigation while retaining the partial URL for `hx-get` and explicit history pushes.

**Tech Stack:** Python 3.14, FastAPI/Starlette, Jinja2, HTMX templates, httpx, pytest-asyncio, optional Firefox manual verification.

## Global Constraints

- Keep partial navigation and `#job-detail` swapping unchanged for in-page use.
- A direct request, browser reload, copied URL, or fresh client must return the full dashboard shell and selected detail.
- Preserve existing unavailable/error rendering and browser URL handling for output links.
- Do not introduce a browser automation dependency; deterministic HTTP tests plus manual Firefox verification are the approved gate.
- Use TDD, `just validate`, dashboard first-run checks, `just ux-validate`, and independent operator evidence.

## File Map

- `dashboard/app.py` — shared shell context, durable route, and existing partial route.
- `dashboard/templates/index.html` — optional selected-detail rendering.
- `dashboard/templates/partials/jobs.html` — durable `href` plus partial `hx-get`.
- `dashboard/tests/test_dashboard.py` — link/history contract.
- `dashboard/tests/test_job_detail.py` — direct shell/reload/fresh-client behavior.
- `docs/ux_review/ops.md`, `docs/ux_review/summary.md` — metadata after evidence.

## Route Contract

```text
GET /                         -> complete shell, no selected job detail by default
GET /partials/jobs/{job_id}   -> detail fragment for HTMX target #job-detail
GET /jobs/{job_id}            -> complete shell with selected detail
```

The job-row markup must retain both navigation paths:

```html
<a href="/jobs/{{ j.job_id }}"
   hx-get="/partials/jobs/{{ j.job_id }}"
   hx-push-url="/jobs/{{ j.job_id }}"
   hx-target="#job-detail"
   hx-swap="innerHTML">
```

## Tasks

### Task 1: Add failing durable-link tests

**Files:**
- Test: `dashboard/tests/test_dashboard.py`

- [ ] Update the jobs-link assertion to require `href="/jobs/job-1"`, `hx-get="/partials/jobs/job-1"`, and explicit `hx-push-url="/jobs/job-1"`.
- [ ] Add `test_jobs_partial_uses_durable_browser_href_and_partial_htmx_request`.
- [ ] Assert `hx-target="#job-detail"` and `hx-swap="innerHTML"` remain unchanged.
- [ ] Run `uv run pytest --no-cov dashboard/tests/test_dashboard.py -q`; confirm the new durable URL assertions fail before template changes.

### Task 2: Add failing direct reload/share tests

**Files:**
- Test: `dashboard/tests/test_job_detail.py`

- [ ] Add `test_job_detail_page_renders_shell_and_selected_detail_on_direct_request`; mock `/version` and `/jobs/job-1`, GET `/jobs/job-1`, and assert full HTML contains `#jobs`, `#job-detail`, `#workers`, `#cost`, and selected job fields.
- [ ] Add `test_job_detail_page_is_shareable_from_fresh_client` with two independent `AsyncClient` instances against the same app.
- [ ] Add `test_partial_job_detail_route_remains_fragment_for_htmx_navigation`; assert the partial response does not contain the full shell.
- [ ] Add a direct-request unavailable case and assert the existing “Job details unavailable” state remains inside the shell.
- [ ] Run `uv run pytest --no-cov dashboard/tests/test_job_detail.py -q`; confirm direct route tests fail before implementation.

### Task 3: Extract shared shell context without changing root behavior

**Files:**
- Modify: `dashboard/app.py`
- Test: `dashboard/tests/test_dashboard.py`
- Test: `dashboard/tests/test_job_detail.py`

**Interfaces:**
- Add a private helper that builds the existing index context and accepts `selected_job: Mapping[str, object] | None`.
- Keep the existing orchestrator fetch/normalization and `browser_url` handling in one seam.

- [ ] Extract the root route’s version/user/browser URL context into the helper.
- [ ] Keep `/` rendering `index.html` with no selected detail and existing loading fragments.
- [ ] Keep `_job_detail_partial` rendering only `partials/job_detail.html`.
- [ ] Run the existing dashboard suite and the new root-shell tests to confirm no unrelated shell changes.
- [ ] Commit with `git commit -m "refactor(OPS-033): share dashboard shell context"`.

### Task 4: Add the durable full-shell route

**Files:**
- Modify: `dashboard/app.py`
- Modify: `dashboard/templates/index.html`
- Test: `dashboard/tests/test_job_detail.py`

- [ ] Add `GET /jobs/{job_id}` with route name `job_detail_page`.
- [ ] Fetch the selected job server-side using the existing orchestrator client seam.
- [ ] Render `index.html` with selected job context, not the fragment template.
- [ ] Add conditional selected-detail rendering inside `#job-detail`; root `/` remains empty/loading by default.
- [ ] Preserve the existing shell sections and `browser_url` output-link behavior.
- [ ] On orchestrator failure, render the shell and the existing unavailable-detail state.
- [ ] Run direct reload/share/partial tests and confirm all pass.
- [ ] Commit with `git commit -m "fix(OPS-033): add durable dashboard job pages"`.

### Task 5: Separate durable anchor navigation from HTMX history

**Files:**
- Modify: `dashboard/templates/partials/jobs.html`
- Test: `dashboard/tests/test_dashboard.py`

- [ ] Set the normal anchor `href` to `/jobs/{{ j.job_id }}`.
- [ ] Keep `hx-get="/partials/jobs/{{ j.job_id }}"`, `hx-target="#job-detail"`, and `hx-swap="innerHTML"`.
- [ ] Set `hx-push-url="/jobs/{{ j.job_id }}"` explicitly.
- [ ] Run the template/link tests and `git diff --check`.
- [ ] Commit with `git commit -m "fix(OPS-033): push durable dashboard detail URLs"`.

### Task 6: Run dashboard gates and independent journey

**Files:**
- Modify: `docs/ux_review/ops.md`
- Modify: `docs/ux_review/summary.md`

- [ ] Run `uv run pytest --no-cov dashboard/tests/test_dashboard.py dashboard/tests/test_job_detail.py -q`.
- [ ] Run `just validate`, `just first-run --step 2`, `just first-run --step 3`, and `just ux-validate`.
- [ ] Manually verify in Firefox or equivalent HTTP-plus-browser flow:
  1. open `/` with a known job;
  2. click the job row;
  3. confirm the address is `/jobs/{id}`;
  4. confirm shell/detail are visible;
  5. reload;
  6. open the copied URL in a fresh/private session;
  7. confirm shell/detail remain visible;
  8. confirm Back/Forward retains partial navigation behavior.
- [ ] Refresh OPS-033 citations and set lifecycle fields only after independent evidence; retain `bundle: 06-dashboard`.
- [ ] Run `just ux-verify OPS-033` and `git diff --check`.
- [ ] Commit with `git commit -m "docs(ux-review): close dashboard URL bundle evidence"`.

## Completion Gate

- [ ] `/jobs/{job_id}` is a complete document with selected detail.
- [ ] `/partials/jobs/{job_id}` remains a fragment.
- [ ] HTMX clicks still target `#job-detail` while browser URLs remain durable.
- [ ] Reload/share/fresh-client journey passes.
- [ ] `just validate`, `just first-run`, `just ux-validate`, and `just ux-verify OPS-033` pass.
