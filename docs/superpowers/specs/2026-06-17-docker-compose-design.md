# Docker Compose — Layer 5 Final Step

**Date:** 2026-06-17
**Status:** Draft
**Depends on:** Layer 4 (API + CLI), HttpWorker, Dashboard

## Overview

Containerize the orchestrator and dashboard, add stub TTS/ASR workers for local development, and wire everything together with Docker Compose. Stubs self-register with the orchestrator on startup using a shared secret for authentication.

## Services

| Service | Image | Port | Notes |
|---------|-------|------|-------|
| `redis` | `redis:7-alpine` | 6379 | Registry + job store backing (future use) |
| `orchestrator` | `Dockerfile.orchestrator` | 8000 | FastAPI app |
| `dashboard` | `dashboard/Dockerfile` | 8080 | HTMX UI, polls orchestrator |
| `tts-stub` | `Dockerfile.worker-stub` | 8001 | Instant mock TTS, self-registers |
| `asr-stub` | `Dockerfile.worker-stub` | 8002 | Instant mock ASR, self-registers |

## Orchestrator Container

Same pattern as dashboard Dockerfile (`python:3.14-slim`, `uv sync`). Entrypoint: `uvicorn acheron.shell.api.app:create_app --factory --host 0.0.0.0 --port 8000`.

Env vars:
- `REDIS_URL=redis://redis:6379` (future use, not consumed yet)

## Stub Worker Container

Single `Dockerfile.worker-stub` reused by both TTS and ASR stubs. A minimal FastAPI app (`stubs/worker_stub.py`).

### Startup

1. Wait for orchestrator to be healthy (`GET /health` with retry)
2. `POST /workers` to orchestrator with:
   - `Authorization: Bearer <ACHERON_REGISTRATION_TOKEN>` header
   - Body: worker ID, type, endpoint, capabilities

### Endpoints

- `GET /health` — returns 200
- `POST /submit` — returns instant mock `JobResult`:
  - TTS: `status=COMPLETED`, `output_path` = tiny silent WAV bytes (valid RIFF header, ~100 bytes)
  - ASR: `status=COMPLETED`, `output_path` = `b"mock transcription"`

### Configuration (env vars)

- `WORKER_TYPE` — `TTS` or `ASR`
- `WORKER_ENDPOINT` — e.g. `http://tts-stub:8001`
- `ORCHESTRATOR_URL` — `http://orchestrator:8000`
- `WORKER_PORT` — port to listen on (8001 or 8002)
- `ACHERON_REGISTRATION_TOKEN` — shared secret for registration

## Registration Security

Shared secret model:
- Orchestrator has `ACHERON_REGISTRATION_TOKEN` env var
- `POST /workers` requires `Authorization: Bearer <token>` header
- Missing or invalid token → 401 Unauthorized
- Docker Compose sets the same token across all services
- Default token for local dev: `dev-registration-token` (overridable)

## Docker Compose

```yaml
services:
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      retries: 3

  orchestrator:
    build:
      context: .
      dockerfile: Dockerfile.orchestrator
    ports: ["8000:8000"]
    environment:
      REDIS_URL: redis://redis:6379
      ACHERON_REGISTRATION_TOKEN: ${ACHERON_REGISTRATION_TOKEN:-dev-registration-token}
    depends_on:
      redis: { condition: service_healthy }

  dashboard:
    build:
      context: .
      dockerfile: dashboard/Dockerfile
    ports: ["8080:8080"]
    environment:
      ACHERON_URL: http://orchestrator:8000
    depends_on: [orchestrator]

  tts-stub:
    build:
      context: .
      dockerfile: Dockerfile.worker-stub
    ports: ["8001:8001"]
    environment:
      WORKER_TYPE: TTS
      WORKER_ENDPOINT: http://tts-stub:8001
      ORCHESTRATOR_URL: http://orchestrator:8000
      WORKER_PORT: "8001"
      ACHERON_REGISTRATION_TOKEN: ${ACHERON_REGISTRATION_TOKEN:-dev-registration-token}
    depends_on: [orchestrator]

  asr-stub:
    build:
      context: .
      dockerfile: Dockerfile.worker-stub
    ports: ["8002:8002"]
    environment:
      WORKER_TYPE: ASR
      WORKER_ENDPOINT: http://asr-stub:8002
      ORCHESTRATOR_URL: http://orchestrator:8000
      WORKER_PORT: "8002"
      ACHERON_REGISTRATION_TOKEN: ${ACHERON_REGISTRATION_TOKEN:-dev-registration-token}
    depends_on: [orchestrator]
```

## File Layout

```
Dockerfile.orchestrator
Dockerfile.worker-stub
docker-compose.yml
stubs/
  __init__.py
  worker_stub.py          # FastAPI app: health, submit, self-registration
stubs/tests/
  test_worker_stub.py     # Unit tests for stub worker
```

## Orchestrator Changes

The orchestrator's `POST /workers` endpoint must validate the `Authorization: Bearer <token>` header against `ACHERON_REGISTRATION_TOKEN`. If the env var is unset, registration is open (backward compatible with current tests).

## What This Doesn't Do

- No real TTS/ASR — stubs return mock data only
- No persistent storage — no volume mounts (dev-only)
- No production config — no TLS, resource limits, scaling
- Redis included but not consumed until Redis-backed stores are added
- No healthcheck on orchestrator/stubs (stubs retry registration on startup instead)
