# Acheron

Distributed asynchronous audio-transformation pipeline. Converts EPUB or audio input into offline chapterized audiobooks in a target language.

## Quick Start

```bash
# Clone and enter the project
git clone <repo-url> && cd acheron

# Start the full stack
cp .env.example .env
docker compose up --build

# Submit a job (in another terminal)
acheron submit book.epub --src en --dest es
```

Services:
- Orchestrator: `http://localhost:8000`
- Dashboard: `http://localhost:8080`
- Redis: `localhost:6379`

## Development

### Prerequisites

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) (package manager)
- [just](https://just.systems/) (command runner)
- [direnv](https://direnv.net/) (optional, auto-activates venv)

### Setup

```bash
# With direnv (recommended):
direnv allow
# venv is created and activated automatically

# Without direnv:
uv sync --all-extras
source .venv/bin/activate
```

### Commands

```bash
just                # list available commands
just validate       # full pipeline: lint, type-check, test
just lint-strict    # auto-format + ruff check
just type-check     # mypy
just type-check-pyright  # basedpyright (matches editor LSP)
just test           # pytest
just lint-imports   # enforce import boundaries
just proto          # compile protobuf definitions
```

### Architecture

```
src/acheron/
  core/             # Domain models, interfaces, planner (no shell imports)
  shell/            # API, CLI, transports, orchestrator, executors
    api/            # FastAPI routes
    stores/         # WorkerStore + JobStore ABCs; memory + redis impls
    transports/     # HttpWorker, GrpcWorker, LocalWorker
    executors/      # Sequential, Async, BatchAsync, and Streaming execution strategies (Streaming is the default)
  proto/            # Generated protobuf code (gitignored)
dashboard/          # HTMX monitoring dashboard (separate package)
stubs/              # Stub workers for local dev (HTTP + gRPC)
tests/              # Unit + integration tests (mirrors src/)
  shell/stores/     # Store tests (memory + redis-via-testcontainers)
proto/              # Proto definitions
```

The `core/` package never imports from `shell/` — enforced by import-linter.

**Storage backends** — `WorkerStore` and `JobStore` are abstract; the orchestrator picks between `InMemoryWorkerStore`/`InMemoryJobStore` (dev) and `RedisWorkerStore`/`RedisJobStore` (production) via `ACHERON_STORE_BACKEND=memory|redis`. In-memory local handlers for `EXTRACTION`/`CHUNKING`/`PACKAGING` are auto-registered by the orchestrator on startup.

### Testing

```bash
just test                    # all tests
uv run pytest tests/shell/   # shell tests only
uv run pytest tests/integration/  # integration tests only
uv run pytest -k "test_name" # single test
```

Integration tests start real HTTP/gRPC stub servers and verify the full orchestrator → worker dispatch path. Redis store tests use `testcontainers[redis]` to spin up real Redis containers.

### Proto Compilation

After editing `proto/synthesis.proto`:

```bash
just proto
```

Generated files go to `src/acheron/proto/` (gitignored).

## Deployment

### Docker Compose

```bash
docker compose up --build
```

Services: redis, orchestrator, dashboard, tts-stub, asr-stub, tts-grpc-stub, translation-stub.

All services are built from a single `Dockerfile` with multiple targets. The builder stage compiles a wheel with `uv build`, then each runtime stage installs it with plain pip — no uv or hatchling in the final images. Docker Compose shares the builder stage across services.

**Production hardening** is built in: every service has a `healthcheck`, dependencies use `condition: service_healthy`, and state persists across restarts via named volumes (`acheron-data` for the orchestrator's plan cache, `redis-data` for Redis). The orchestrator fails fast at startup if `ACHERON_DATA_DIR` is unwritable.

Stub workers return mock data. Replace with real GPU workers (Layer 8) for production.

### TLS

Acheron services serve TLS when configured. Three env vars control it:

- `ACHERON_TLS_CERT_FILE` + `ACHERON_TLS_KEY_FILE` — server-side; both must be set together
- `SSL_CERT_FILE` — client-side (used by httpx and stdlib `ssl`); set to the Acheron CA

**Local dev (self-signed).** Run `just certs` to generate a local Acheron CA and per-service certs in `certs/`. The compose file mounts `certs/` into every service and sets the env vars. The CA is trusted by all services via `SSL_CERT_FILE`.

**Production.** Generate real certs (Let's Encrypt via cert-manager, your CA, etc.) with the right SANs. Mount them into each service and set the env vars. No Acheron code change.

**Reverse proxy (optional).** Acheron doesn't ship a proxy. To put nginx, Caddy, or anything else in front, point it at the orchestrator (HTTPS) and dashboard (HTTP) and terminate TLS there. Acheron's `ACHERON_TLS_*` env vars are independent of any proxy you add.

**Disabling TLS.** Leave `ACHERON_TLS_CERT_FILE` and `ACHERON_TLS_KEY_FILE` unset. All services fall back to HTTP. Useful for local dev without certs.

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ACHERON_URL` | `https://localhost:8000` | CLI and dashboard: orchestrator URL (use `http://` to skip TLS) |
| `ACHERON_REGISTRATION_TOKEN` | `dev-registration-token` | Worker registration shared secret |
| `ACHERON_DATA_DIR` | `/data/jobs` | Orchestrator: plan and step-output cache directory (must be writable) |
| `ACHERON_STORE_BACKEND` | `memory` | Orchestrator: `memory` (in-process) or `redis` (persistent) |
| `REDIS_URL` | `redis://localhost:6379` | Orchestrator: Redis connection (used when `ACHERON_STORE_BACKEND=redis`) |
| `WORKER_HTTP_PORT` | `9002` | gRPC stub: HTTP `/health` sidecar port for Docker healthchecks |
| `ACHERON_TLS_CERT_FILE` | (unset) | Server: path to PEM-encoded server cert (set with `ACHERON_TLS_KEY_FILE` to enable HTTPS) |
| `ACHERON_TLS_KEY_FILE` | (unset) | Server: path to PEM-encoded server key (set with `ACHERON_TLS_CERT_FILE` to enable HTTPS) |
| `ACHERON_TLS_CA_FILE` | (unset) | gRPC and CLI clients: path to PEM-encoded CA bundle to verify peer certs (falls back to `SSL_CERT_FILE`, then `./certs/acheron-ca.crt` in the CLI's CWD) |

## CLI

```bash
acheron submit book.epub --src en --dest es
acheron submit podcast.mp3 --src en --dest es --asr whisper-v3
acheron status job-xyz
acheron status job-xyz --verbose
acheron jobs --active
acheron jobs --completed
acheron workers
acheron capabilities --src en --dest es
```

## License

GPL-3.0-only
