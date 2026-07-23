# Docker Compose — Layer 5 Final Step

**Date:** 2026-06-17
**Status:** Done
**Depends on:** Layer 4 (API + CLI), HttpWorker, Dashboard

## Overview

Containerize the orchestrator and dashboard, add stub TTS/ASR workers for local development, and wire everything together with Docker Compose. Stubs self-register with the orchestrator on startup using a shared secret for authentication.

## Services

| Service | Image | Port | Notes |
|---------|-------|------|-------|
| `redis` | `redis:7-alpine` | 6379 | Registry + job store backing |
| `certs-init` | `Dockerfile` target `certs-init` | — | Generates development certificates |
| `orchestrator` | `Dockerfile` target `orchestrator` | 8000 | FastAPI app |
| `dashboard` | `Dockerfile` target `dashboard` | 8080 | HTMX UI, polls orchestrator |
| `tts-local-stub` | `Dockerfile` target `worker-stub-base` | 8001 | Instant mock TTS, self-registers |
| `asr-local-stub` | `Dockerfile` target `worker-stub-base` | 8002 | Instant mock ASR, self-registers |
| `translation-local-stub` | `Dockerfile` target `worker-stub-base` | 8003 | Instant mock translation, self-registers |
| `tts-runpod-stub` | `Dockerfile` target `worker-stub-base` | 8006 | Static-price TTS stub |
| `translation-runpod-stub` | `Dockerfile` target `worker-stub-base` | 8007 | Static-price translation stub |
| `tts-grpc-stub` | `Dockerfile` target `worker-stub-base` | 9002 | gRPC TTS stub with HTTP health edge |
| `qwen3tts-edge` | `Dockerfile.edge` | 8001 | Optional RunPod TTS edge |
| `granite-speech-edge` | `Dockerfile.edge` | 8001 | Optional RunPod ASR edge |
| `translategemma-edge` | `Dockerfile.edge` | 8001 | Optional RunPod translation edge |

## Orchestrator Container

The `orchestrator` target in `Dockerfile` runs the FastAPI service on port 8000.

Env vars:
- `REDIS_URL=redis://redis:6379`

## Stub Worker Container

The `worker-stub-base` target is reused by the local TTS, ASR, and translation stubs. Each uses the worker SDK edge app.

### Startup

1. Wait for orchestrator to be healthy (`GET /health` with retry)
2. `POST /workers` to orchestrator with:
   - `Authorization: Bearer <ACHERON_REGISTRATION_TOKEN>` header
   - Body: worker ID, type, endpoint, capabilities

### Endpoints

- `GET /health` — returns 200
- `POST /execute` — returns an instant mock `JobResult`:
  - TTS: `status=completed`, `output_data` = base64-encoded silent WAV (valid RIFF header, ~100 bytes)
  - ASR: `status=completed`, `output_data` = base64-encoded `b"mock transcription"`

Note: stubs return inline `output_data` (base64) instead of `output_path` since there is no shared file storage in the dev Compose setup.

### Configuration (env vars)

- `WORKER_TYPE` — `TTS` or `ASR`
- `WORKER_ENDPOINT` — e.g. `http://tts-stub:8001`
- `ACHERON_WORKER__ORCHESTRATOR_URL` — `http://orchestrator:8000`
- `ACHERON_WORKER__WORKER_HOST` — service hostname used by the orchestrator
- `WORKER_PORT` — port to listen on (8001 or 8002)
- `ACHERON_REGISTRATION_TOKEN` — orchestrator registration secret
- `ACHERON_WORKER__REGISTRATION_TOKEN` — worker-side copy of the registration secret

## Registration Security

Shared secret model:
- Orchestrator has `ACHERON_REGISTRATION_TOKEN` env var
- `POST /workers` requires `Authorization: Bearer <token>` header
- Missing or invalid token → 401 Unauthorized
- Docker Compose sets the same token across all services
- The token is required; generate one with `openssl rand -hex 32`.

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
      target: orchestrator
    ports: ["8000:8000"]
    environment:
      REDIS_URL: redis://redis:6379
      ACHERON_REGISTRATION_TOKEN: ${ACHERON_REGISTRATION_TOKEN:?ACHERON_REGISTRATION_TOKEN must be set}
    depends_on:
      redis: { condition: service_healthy }

  dashboard:
    build:
      context: .
      target: dashboard
    ports: ["8080:8080"]
    environment:
      ACHERON_URL: http://orchestrator:8000
    depends_on: [orchestrator]

  tts-local-stub:
    build:
      context: .
      target: worker-stub-base
    ports: ["8001:8001"]
    environment:
      WORKER_NAME: tts-local-stub
      ACHERON_WORKER__WORKER_ID: tts-local-stub
      ACHERON_WORKER__WORKER_HOST: tts-local-stub
      ACHERON_WORKER__ORCHESTRATOR_URL: https://orchestrator:8000
      ACHERON_WORKER__REGISTRATION_TOKEN: ${ACHERON_REGISTRATION_TOKEN:?ACHERON_REGISTRATION_TOKEN must be set}
      ACHERON_WORKER__PRICE_SOURCE: zero
      ACHERON_WORKER__LISTEN_PORT: "8001"
    depends_on:
      orchestrator: { condition: service_healthy }

  asr-local-stub:
    build:
      context: .
      target: worker-stub-base
    ports: ["8002:8002"]
    environment:
      WORKER_NAME: asr-local-stub
      ACHERON_WORKER__WORKER_ID: asr-local-stub
      ACHERON_WORKER__WORKER_HOST: asr-local-stub
      ACHERON_WORKER__ORCHESTRATOR_URL: https://orchestrator:8000
      ACHERON_WORKER__REGISTRATION_TOKEN: ${ACHERON_REGISTRATION_TOKEN:?ACHERON_REGISTRATION_TOKEN must be set}
      ACHERON_WORKER__PRICE_SOURCE: zero
      ACHERON_WORKER__LISTEN_PORT: "8002"
    depends_on:
      orchestrator: { condition: service_healthy }
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

The orchestrator's `POST /workers` endpoint must validate the `Authorization: Bearer <token>` header against the configured registration token. If the token is unset, it is auto-generated on orchestrator startup. To enable open registration, set `ACHERON_OPEN_REGISTRATION=1`.

## What This Doesn't Do

- No real TTS/ASR — stubs return mock data only (→ Layer 8)
- No persistent storage — no volume mounts (dev-only) (→ Layer 7)
- No production config — no TLS, resource limits, scaling (→ Layer 7)
- Redis included but not consumed until Redis-backed stores are added (→ Layer 7)
- No healthcheck on orchestrator/stubs (stubs retry registration on startup instead)
