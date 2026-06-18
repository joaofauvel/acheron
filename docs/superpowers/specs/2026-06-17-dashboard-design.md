# HTMX Dashboard — Design Spec

**Sub-project 2 of Layer 5: GPU Workers + Dashboard**

## Overview

Read-only monitoring dashboard for the Acheron orchestrator. Single-page layout with three sections (Jobs, Workers, Cost), each polling independently via HTMX.

## Stack

- FastAPI + Jinja2 + HTMX
- httpx for orchestrator API calls
- Separate Docker container

## Layout

Single scrollable page with three sections:

### Jobs Section

Table columns:
- Job ID
- Status badge (running=blue, completed=green, failed=red)
- Progress bar (`completed_steps / total_steps`)
- Step pipeline (✓ complete, ⏳ running, ○ pending icons)
- Cost estimate
- Duration

### Workers Section

Table columns:
- Worker ID
- Type (TTS, ASR, Translation, etc.)
- Endpoint
- Transport (http, grpc)
- Health status (green dot = healthy, red dot = unhealthy)
- Consecutive failures

### Cost Section

Table columns:
- Worker ID
- Type
- Cost estimate
- GPU seconds
- Tokens in/out

## Polling

Each section uses HTMX polling:
```html
<div hx-get="/partials/jobs" hx-trigger="every 2s" hx-swap="innerHTML">
  <!-- jobs table loaded here -->
</div>
```

Server returns Jinja partials (HTML fragments), not full pages. Each section polls independently — a slow response in one section doesn't block others.

## Auth

Forward auth only. Dashboard reads `X-Forwarded-User` header from reverse proxy (nginx, Caddy). No auth logic in the application.

## Routes

| Route | Returns |
|-------|---------|
| `GET /` | Full page with all three sections |
| `GET /partials/jobs` | Jobs table HTML partial |
| `GET /partials/workers` | Workers table HTML partial |
| `GET /partials/cost` | Cost table HTML partial |

## Files

| File | Responsibility |
|------|---------------|
| `dashboard/app.py` | FastAPI app, routes, orchestrator API client |
| `dashboard/templates/index.html` | Main page layout with HTMX polling |
| `dashboard/templates/partials/jobs.html` | Jobs table partial |
| `dashboard/templates/partials/workers.html` | Workers table partial |
| `dashboard/templates/partials/cost.html` | Cost table partial |
| `dashboard/static/style.css` | Minimal CSS (or inline in templates) |
| `dashboard/Dockerfile` | Container for dashboard |
| `dashboard/tests/test_dashboard.py` | Tests using httpx ASGI transport |

## Dependencies

- Python 3.12+
- FastAPI or Starlette
- Jinja2
- HTMX (CDN or bundled)
- httpx (API client)

## Testing

Tests use httpx ASGI transport (same pattern as API tests). Mock orchestrator responses, verify HTML output contains expected data.
