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
acheron job submit book.epub --src en --dest es
```

Dev TLS certs under `certs/` are auto-generated on first `docker compose up` by a one-shot `certs-init` service; no manual `just certs` step is needed. Re-running overwrites the certs (idempotent).

Services:
- Orchestrator: `https://localhost:8000`
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
just                     # list available commands
just validate            # full pipeline: lint, import-lint, type-check (mypy + basedpyright), test
just lint-strict         # auto-format + ruff check
just lint-imports        # enforce import boundaries
just type-check          # mypy
just type-check-pyright  # basedpyright (matches editor LSP)
just test                # pytest
just proto               # compile protobuf definitions
just certs               # generate dev TLS CA + per-service certs
just build-worker <name> # build a RunPod worker image locally
just build-edge          # build the generic edge image (acheron-worker-edge)
```

### Architecture

```
src/acheron/
  core/             # Domain models, interfaces, planner (no shell imports)
  shell/            # API, CLI, transports, orchestrator, executors
    api/            # FastAPI routes (jobs, workers, capabilities, partials)
      routes/       # Route modules
    stores/         # WorkerStore + JobStore ABCs; memory + redis impls
    transports/     # HttpWorker, GrpcWorker, LocalWorker (+ multipart helpers)
    executors/      # Sequential, Async, and Streaming execution strategies
  worker_sdk/       # Worker SDK — framework for building GPU workers and edge proxies
  proto/            # Generated protobuf code (gitignored)
workers/            # Real GPU worker implementations (uv workspace members)
  qwen3tts/         # Qwen3-TTS RunPod + edge worker
stubs/              # Stub workers for local dev (SDK-based HTTP + gRPC)
dashboard/          # HTMX monitoring dashboard (separate package)
scripts/            # Dev utilities (cert generation)
tests/              # Unit + integration tests (mirrors src/)
  core/             # Domain model, planner, chunking tests
  shell/            # Orchestrator, executors, transports, stores, CLI, config, health, TLS
    stores/         # Store tests (memory + redis-via-testcontainers)
  integration/      # Full-stack integration tests (HTTP/gRPC stubs, TLS, worker registration)
  worker_sdk/       # Worker SDK unit tests
```

#### Import boundaries

Enforced by import-linter (`just lint-imports`):

- `core/` never imports from `shell/`
- `worker_sdk/` never imports from `shell/`
- `workers/` never imports from `shell/`

#### Storage backends

`WorkerStore` and `JobStore` are abstract; the orchestrator picks between `InMemoryWorkerStore`/`InMemoryJobStore` (dev) and `RedisWorkerStore`/`RedisJobStore` (production) via `ACHERON_STORE_BACKEND=memory|redis`. In-memory local handlers for `EXTRACTION`/`CHUNKING`/`PACKAGING` are auto-registered by the orchestrator on startup.

#### Health monitoring

The orchestrator periodically probes registered workers via HTTP or gRPC health checks. When a worker's direct probe fails, the system falls back to platform-specific provider health checks (RunPod, Hugging Face) configured under `providers:` in `acheron.yaml`.

#### Worker SDK

The `worker_sdk` package provides the framework for building workers:

- `WorkerHandler` — abstract handler interface
- `create_worker_app` — creates a FastAPI app that handles `/execute`, `/health`, and self-registration
- `RunPodForwarderHandler` — edge proxy that forwards `/execute` calls to RunPod serverless endpoints
- Pricing (`RunPodPrice`, `StaticPrice`, `ZeroPrice`) — pluggable per-job cost estimation
- Artifact types (`FileArtifact`, `BytesArtifact`, `StreamArtifact`) — typed step outputs
- `acheron-worker-edge` console script — generic edge entrypoint

### Testing

```bash
just test                    # all tests
uv run pytest tests/shell/   # shell tests only
uv run pytest tests/integration/  # integration tests only
uv run pytest -k "test_name" # single test
```

Test paths: `tests/`, `stubs/tests/`, `dashboard/tests/`, `workers/qwen3tts/tests/`.

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

Default services: redis, certs-init, orchestrator, dashboard, tts-local-stub, asr-local-stub, translation-local-stub, tts-volume-stub, tts-runpod-stub, translation-runpod-stub, tts-grpc-stub.

Optional profiles:
- `runpod-tts` — starts `qwen3tts-edge`, the real RunPod TTS edge proxy (requires `RUNPOD_API_KEY` and `QWEN3TTS_RUNPOD_ENDPOINT_ID` in `.env`).

All services are built from a single `Dockerfile` with multiple targets (+ `Dockerfile.edge` for the edge worker). The builder stage compiles a wheel with `uv build`, then each runtime stage installs it with plain pip — no uv or hatchling in the final images.

**Production hardening** is built in: every service has a `healthcheck`, dependencies use `condition: service_healthy`, and state persists across restarts via named volumes (`acheron-data` for the orchestrator's plan cache, `redis-data` for Redis). The orchestrator fails fast at startup if `ACHERON_DATA_DIR` is unwritable.

Stub workers return mock data. Replace with real GPU workers for production.

### CI

The `build-workers.yml` GitHub Actions workflow builds and publishes worker images to `ghcr.io` on pushes to `main` and version tags:

- `acheron-qwen3tts-runpod` — GPU worker image (from `workers/qwen3tts/Dockerfile.runpod`)
- `acheron-worker-edge` — generic edge proxy image (from `Dockerfile.edge`)

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
| `ACHERON_REGISTRATION_TOKEN` | (auto-generated) | Worker registration shared secret. If unset, the orchestrator generates a secure token on startup and writes it to `{data_dir}/.registration_token`. |
| `ACHERON_OPEN_REGISTRATION` | (unset) | Set to `1` to enable open worker registration (bypasses token checks, useful for local dev). |
| `ACHERON_CONFIG_PATH` | (unset) | Custom path to the settings configuration file (searches for `acheron.yaml` / `acheron.yml`). |
| `ACHERON_DATA_DIR` | `/data/jobs` | Orchestrator: plan and step-output cache directory (must be writable) |
| `ACHERON_STORE_BACKEND` | `memory` | Orchestrator: `memory` (in-process) or `redis` (persistent) |
| `REDIS_URL` | `redis://localhost:6379` | Orchestrator: Redis connection (used when `ACHERON_STORE_BACKEND=redis`) |
| `ACHERON_TLS_CERT_FILE` | (unset) | Server: path to PEM-encoded server cert (set with `ACHERON_TLS_KEY_FILE` to enable HTTPS) |
| `ACHERON_TLS_KEY_FILE` | (unset) | Server: path to PEM-encoded server key (set with `ACHERON_TLS_CERT_FILE` to enable HTTPS) |
| `ACHERON_TLS_CA_FILE` | (unset) | gRPC and CLI clients: path to PEM-encoded CA bundle to verify peer certs (falls back to `SSL_CERT_FILE`, then `./certs/acheron-ca.crt` in the CLI's CWD) |

### Configuration File

In addition to environment variables, Acheron can be configured using a YAML configuration file (`acheron.yaml` or `acheron.yml`).

The orchestrator searches for configuration files in the following order:
1. `$ACHERON_CONFIG_PATH` (if defined)
2. `./acheron.yaml` or `./acheron.yml` in the current working directory
3. `/etc/acheron/acheron.yaml` or `/etc/acheron/acheron.yml`

An example template configuration file is provided in [acheron.yaml.example](acheron.yaml.example). To customize settings:

```bash
cp acheron.yaml.example acheron.yaml
```

Environment variables always take precedence over values defined in the configuration file. For nested configuration keys, use a double underscore `__` prefix (e.g. `ACHERON_ORCHESTRATOR__DATA_DIR` maps to the `orchestrator.data_dir` key).

## CLI

```bash
# Submit and manage jobs
acheron job submit book.epub --src en --dest es
acheron job submit podcast.mp3 --src en --dest es --asr whisper-v3
acheron job status job-xyz
acheron job status job-xyz --verbose
acheron job resume job-xyz
acheron job resume job-xyz --force-fresh

# View system status, workers, and capabilities
acheron status
acheron jobs --active
acheron jobs --completed
acheron workers
acheron capabilities --src en --dest es
```

## License

GPL-3.0-only
