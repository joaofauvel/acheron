# HTMX Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only monitoring dashboard with HTMX polling for jobs, workers, and cost views.

**Architecture:** Single-page FastAPI app with Jinja2 templates. Three sections (Jobs, Workers, Cost) each poll independently via HTMX `hx-trigger="every 2s"`. Server returns HTML partials. Talks to orchestrator API via httpx.

**Tech Stack:** FastAPI, Jinja2, HTMX (CDN), httpx

---

## File Structure

| File | Responsibility |
|------|---------------|
| `dashboard/app.py` | FastAPI app, routes, orchestrator API client |
| `dashboard/templates/index.html` | Main page with three HTMX polling sections |
| `dashboard/templates/partials/jobs.html` | Jobs table partial |
| `dashboard/templates/partials/workers.html` | Workers table partial |
| `dashboard/templates/partials/cost.html` | Cost table partial |
| `dashboard/tests/test_dashboard.py` | Tests using httpx ASGI transport |
| `dashboard/Dockerfile` | Container for dashboard |

---

### Task 1: Dashboard app skeleton

**Files:**
- Create: `dashboard/app.py`
- Create: `dashboard/templates/index.html`
- Create: `dashboard/tests/test_dashboard.py`

- [ ] **Step 1: Write failing test for index page**

```python
# dashboard/tests/test_dashboard.py
"""Tests for the HTMX dashboard."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from dashboard.app import create_app


@pytest.fixture()
def app():
    return create_app(orchestrator_url="http://orchestrator:8000")


@pytest.fixture()
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestIndexPage:
    @pytest.mark.asyncio
    async def test_index_returns_200(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_index_contains_jobs_section(self, client):
        resp = await client.get("/")
        assert "id=\"jobs\"" in resp.text

    @pytest.mark.asyncio
    async def test_index_contains_workers_section(self, client):
        resp = await client.get("/")
        assert "id=\"workers\"" in resp.text

    @pytest.mark.asyncio
    async def test_index_contains_cost_section(self, client):
        resp = await client.get("/")
        assert "id=\"cost\"" in resp.text

    @pytest.mark.asyncio
    async def test_index_includes_htmx(self, client):
        resp = await client.get("/")
        assert "htmx" in resp.text.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest dashboard/tests/test_dashboard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dashboard'`

- [ ] **Step 3: Create dashboard app with index route**

```python
# dashboard/app.py
"""HTMX dashboard for the Acheron orchestrator."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

_TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")


def create_app(orchestrator_url: str = "http://localhost:8000") -> FastAPI:
    app = FastAPI(title="Acheron Dashboard")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return _TEMPLATES.TemplateResponse("index.html", {"request": request})

    return app
```

- [ ] **Step 4: Create index template**

```html
<!-- dashboard/templates/index.html -->
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Acheron Dashboard</title>
  <script src="https://unpkg.com/htmx.org@2.0.4"></script>
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem; background: #0d1117; color: #c9d1d9; }
    h1 { color: #58a6ff; }
    h2 { color: #8b949e; border-bottom: 1px solid #30363d; padding-bottom: 0.5rem; }
    table { width: 100%; border-collapse: collapse; margin: 1rem 0; }
    th, td { text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid #21262d; }
    th { color: #8b949e; font-weight: 600; }
    .badge { padding: 0.15rem 0.5rem; border-radius: 999px; font-size: 0.8rem; font-weight: 500; }
    .badge-running { background: #1f6feb33; color: #58a6ff; }
    .badge-completed { background: #23863633; color: #3fb950; }
    .badge-failed { background: #da363433; color: #f85149; }
    .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; }
    .dot-green { background: #3fb950; }
    .dot-red { background: #f85149; }
    .section { margin-bottom: 2rem; }
  </style>
</head>
<body>
  <h1>Acheron</h1>

  <div class="section" id="jobs">
    <h2>Jobs</h2>
    <div hx-get="/partials/jobs" hx-trigger="load, every 2s" hx-swap="innerHTML">
      <p style="color:#8b949e">Loading jobs...</p>
    </div>
  </div>

  <div class="section" id="workers">
    <h2>Workers</h2>
    <div hx-get="/partials/workers" hx-trigger="load, every 2s" hx-swap="innerHTML">
      <p style="color:#8b949e">Loading workers...</p>
    </div>
  </div>

  <div class="section" id="cost">
    <h2>Cost</h2>
    <div hx-get="/partials/cost" hx-trigger="load, every 2s" hx-swap="innerHTML">
      <p style="color:#8b949e">Loading cost data...</p>
    </div>
  </div>
</body>
</html>
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest dashboard/tests/test_dashboard.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add dashboard/app.py dashboard/templates/index.html dashboard/tests/test_dashboard.py
git commit -m "feat(dashboard): add app skeleton with index page"
```

---

### Task 2: Jobs partial

**Files:**
- Create: `dashboard/templates/partials/jobs.html`
- Modify: `dashboard/app.py`
- Modify: `dashboard/tests/test_dashboard.py`

- [ ] **Step 1: Write failing tests for jobs partial**

Append to `dashboard/tests/test_dashboard.py`:

```python
import httpx
import respx


class TestJobsPartial:
    @respx.mock
    @pytest.mark.asyncio
    async def test_jobs_partial_returns_table(self, client):
        respx.get("http://orchestrator:8000/jobs").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jobs": [
                        {"job_id": "job-1", "status": "running", "plan_id": "p1", "completed_steps": 2, "total_steps": 5, "errors": []},
                    ]
                },
            )
        )
        resp = await client.get("/partials/jobs")
        assert resp.status_code == 200
        assert "job-1" in resp.text
        assert "running" in resp.text

    @respx.mock
    @pytest.mark.asyncio
    async def test_jobs_partial_empty(self, client):
        respx.get("http://orchestrator:8000/jobs").mock(
            return_value=httpx.Response(200, json={"jobs": []})
        )
        resp = await client.get("/partials/jobs")
        assert resp.status_code == 200
        assert "No jobs" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest dashboard/tests/test_dashboard.py::TestJobsPartial -v`
Expected: FAIL (route not found)

- [ ] **Step 3: Add orchestrator API client and jobs partial route**

In `dashboard/app.py`, add the API client and partial route:

```python
import httpx


def create_app(orchestrator_url: str = "http://localhost:8000") -> FastAPI:
    app = FastAPI(title="Acheron Dashboard")

    async def _fetch(path: str) -> dict:
        async with httpx.AsyncClient(base_url=orchestrator_url) as client:
            resp = await client.get(path)
            resp.raise_for_status()
            return resp.json()

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return _TEMPLATES.TemplateResponse("index.html", {"request": request})

    @app.get("/partials/jobs", response_class=HTMLResponse)
    async def jobs_partial(request: Request) -> HTMLResponse:
        data = await _fetch("/jobs")
        return _TEMPLATES.TemplateResponse("partials/jobs.html", {"request": request, "jobs": data["jobs"]})

    return app
```

- [ ] **Step 4: Create jobs partial template**

```html
<!-- dashboard/templates/partials/jobs.html -->
{% if jobs %}
<table>
  <thead>
    <tr><th>Job ID</th><th>Status</th><th>Progress</th><th>Steps</th></tr>
  </thead>
  <tbody>
    {% for j in jobs %}
    <tr>
      <td>{{ j.job_id }}</td>
      <td><span class="badge badge-{{ j.status }}">{{ j.status }}</span></td>
      <td>{{ j.completed_steps }}/{{ j.total_steps }}</td>
      <td>{{ j.completed_steps }}/{{ j.total_steps }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% else %}
<p style="color:#8b949e">No jobs found.</p>
{% endif %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest dashboard/tests/test_dashboard.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add dashboard/app.py dashboard/templates/partials/ dashboard/tests/test_dashboard.py
git commit -m "feat(dashboard): add jobs partial with HTMX polling"
```

---

### Task 3: Workers partial

**Files:**
- Create: `dashboard/templates/partials/workers.html`
- Modify: `dashboard/app.py`
- Modify: `dashboard/tests/test_dashboard.py`

- [ ] **Step 1: Write failing tests for workers partial**

Append to `dashboard/tests/test_dashboard.py`:

```python
class TestWorkersPartial:
    @respx.mock
    @pytest.mark.asyncio
    async def test_workers_partial_returns_table(self, client):
        respx.get("http://orchestrator:8000/workers").mock(
            return_value=httpx.Response(
                200,
                json={
                    "workers": [
                        {"worker_id": "tts-1", "worker_type": "tts", "endpoint": "http://tts:8000", "transport": "http", "consecutive_failures": 0},
                    ]
                },
            )
        )
        resp = await client.get("/partials/workers")
        assert resp.status_code == 200
        assert "tts-1" in resp.text
        assert "tts" in resp.text

    @respx.mock
    @pytest.mark.asyncio
    async def test_workers_partial_empty(self, client):
        respx.get("http://orchestrator:8000/workers").mock(
            return_value=httpx.Response(200, json={"workers": []})
        )
        resp = await client.get("/partials/workers")
        assert resp.status_code == 200
        assert "No workers" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest dashboard/tests/test_dashboard.py::TestWorkersPartial -v`
Expected: FAIL (route not found)

- [ ] **Step 3: Add workers partial route**

In `dashboard/app.py`, add the workers route:

```python
    @app.get("/partials/workers", response_class=HTMLResponse)
    async def workers_partial(request: Request) -> HTMLResponse:
        data = await _fetch("/workers")
        return _TEMPLATES.TemplateResponse("partials/workers.html", {"request": request, "workers": data["workers"]})
```

- [ ] **Step 4: Create workers partial template**

```html
<!-- dashboard/templates/partials/workers.html -->
{% if workers %}
<table>
  <thead>
    <tr><th>Worker ID</th><th>Type</th><th>Endpoint</th><th>Transport</th><th>Health</th><th>Failures</th></tr>
  </thead>
  <tbody>
    {% for w in workers %}
    <tr>
      <td>{{ w.worker_id }}</td>
      <td>{{ w.worker_type }}</td>
      <td>{{ w.endpoint }}</td>
      <td>{{ w.transport }}</td>
      <td><span class="dot {{ 'dot-green' if w.consecutive_failures == 0 else 'dot-red' }}"></span></td>
      <td>{{ w.consecutive_failures }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% else %}
<p style="color:#8b949e">No workers registered.</p>
{% endif %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest dashboard/tests/test_dashboard.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add dashboard/app.py dashboard/templates/partials/ dashboard/tests/test_dashboard.py
git commit -m "feat(dashboard): add workers partial"
```

---

### Task 4: Cost partial

**Files:**
- Create: `dashboard/templates/partials/cost.html`
- Modify: `dashboard/app.py`
- Modify: `dashboard/tests/test_dashboard.py`

- [ ] **Step 1: Write failing tests for cost partial**

Append to `dashboard/tests/test_dashboard.py`:

```python
class TestCostPartial:
    @respx.mock
    @pytest.mark.asyncio
    async def test_cost_partial_returns_table(self, client):
        respx.get("http://orchestrator:8000/jobs").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jobs": [
                        {"job_id": "job-1", "status": "completed", "plan_id": "p1", "completed_steps": 5, "total_steps": 5, "errors": []},
                    ]
                },
            )
        )
        resp = await client.get("/partials/cost")
        assert resp.status_code == 200
        assert "job-1" in resp.text

    @respx.mock
    @pytest.mark.asyncio
    async def test_cost_partial_empty(self, client):
        respx.get("http://orchestrator:8000/jobs").mock(
            return_value=httpx.Response(200, json={"jobs": []})
        )
        resp = await client.get("/partials/cost")
        assert resp.status_code == 200
        assert "No cost" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest dashboard/tests/test_dashboard.py::TestCostPartial -v`
Expected: FAIL (route not found)

- [ ] **Step 3: Add cost partial route**

In `dashboard/app.py`, add the cost route:

```python
    @app.get("/partials/cost", response_class=HTMLResponse)
    async def cost_partial(request: Request) -> HTMLResponse:
        data = await _fetch("/jobs")
        return _TEMPLATES.TemplateResponse("partials/cost.html", {"request": request, "jobs": data["jobs"]})
```

- [ ] **Step 4: Create cost partial template**

```html
<!-- dashboard/templates/partials/cost.html -->
{% if jobs %}
<table>
  <thead>
    <tr><th>Job ID</th><th>Status</th><th>Steps Completed</th></tr>
  </thead>
  <tbody>
    {% for j in jobs %}
    <tr>
      <td>{{ j.job_id }}</td>
      <td><span class="badge badge-{{ j.status }}">{{ j.status }}</span></td>
      <td>{{ j.completed_steps }}/{{ j.total_steps }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% else %}
<p style="color:#8b949e">No cost data available.</p>
{% endif %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest dashboard/tests/test_dashboard.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add dashboard/app.py dashboard/templates/partials/ dashboard/tests/test_dashboard.py
git commit -m "feat(dashboard): add cost partial"
```

---

### Task 5: Forward auth support

**Files:**
- Modify: `dashboard/app.py`
- Modify: `dashboard/tests/test_dashboard.py`

- [ ] **Step 1: Write failing test for forward auth**

Append to `dashboard/tests/test_dashboard.py`:

```python
class TestForwardAuth:
    @pytest.mark.asyncio
    async def test_reads_forwarded_user_header(self, client):
        resp = await client.get("/", headers={"X-Forwarded-User": "admin"})
        assert resp.status_code == 200
        assert "admin" in resp.text

    @pytest.mark.asyncio
    async def test_works_without_auth_header(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest dashboard/tests/test_dashboard.py::TestForwardAuth -v`
Expected: FAIL (user not shown in template)

- [ ] **Step 3: Pass user to template**

In `dashboard/app.py`, update the index route:

```python
    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        user = request.headers.get("X-Forwarded-User", "")
        return _TEMPLATES.TemplateResponse("index.html", {"request": request, "user": user})
```

In `dashboard/templates/index.html`, add user display after the h1:

```html
  <h1>Acheron</h1>
  {% if user %}<p style="color:#8b949e">Signed in as {{ user }}</p>{% endif %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest dashboard/tests/test_dashboard.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/app.py dashboard/templates/index.html dashboard/tests/test_dashboard.py
git commit -m "feat(dashboard): add forward auth support"
```

---

### Task 6: Dockerfile

**Files:**
- Create: `dashboard/Dockerfile`

- [ ] **Step 1: Create Dockerfile**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --no-dev --no-install-project

COPY src/acheron/ ./src/acheron/
COPY dashboard/ ./dashboard/

ENV PYTHONPATH=/app/src

CMD ["uvicorn", "dashboard.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/Dockerfile
git commit -m "feat(dashboard): add Dockerfile"
```

---

### Task 7: Final validation

- [ ] **Step 1: Run full validation**

Run: `just validate`
Expected: All checks pass, all tests pass, coverage >= 80%

- [ ] **Step 2: Verify dashboard starts**

Run: `uv run uvicorn dashboard.app:create_app --factory --port 8080`
Expected: Server starts, `curl http://localhost:8080/` returns HTML

- [ ] **Step 3: Commit any remaining fixes**

```bash
git add -A
git commit -m "chore: final cleanup for dashboard sub-project"
```
