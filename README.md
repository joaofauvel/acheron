# Acheron

## What is Acheron

Acheron is a distributed asynchronous audio-transformation pipeline that converts EPUB or audio input into chapterized audiobooks in a target language.

## Prerequisites

**System:**

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) (package manager)
- [just](https://just.systems/) (command runner)
- [direnv](https://direnv.net/) (optional, auto-activates the local venv via `.envrc`)
- Docker and Docker Compose

**CLI:**

- `acheron` (for submitting and monitoring jobs)
- `runpodctl` (operators only, for creating RunPod serverless endpoints)

## Quick Start

```bash
cp .env.example .env
docker compose up --build
```

The stack comes up with these default services:

- **Orchestrator** at `https://localhost:8000`. TLS is auto-enabled because the `certs-init` one-shot service generates a self-signed CA and per-service certs into `./certs/` on first run, and the compose file mounts them into every container.
- **Dashboard** at `http://localhost:8080`.
- **Redis** on `localhost:6379`.
- **Local stub workers** (TTS, ASR, translation, gRPC) auto-register with the orchestrator and return mock data. Replace with real GPU workers for production.

## Basic CLI Commands

```bash
# Submit an EPUB
acheron job submit book.epub --src en --dest es

# Submit an audio file (requires an ASR model)
acheron job submit podcast.mp3 --src en --dest es --asr whisper-v3

# Check job status
acheron job status job-xyz
acheron job status job-xyz --verbose

# Resume a job (reuses cached step outputs)
acheron job resume job-xyz
# Resume from scratch (discards the step cache)
acheron job resume job-xyz --force-fresh

# System overview
acheron status
acheron jobs --active
acheron jobs --completed

# Registered workers
acheron workers

# Supported language pairs
acheron capabilities --src en --dest es
```

## Dashboard

The dashboard is an HTMX-based web UI for live monitoring at `http://localhost:8080`. It polls the orchestrator for job status, worker health, and cost.

## Development

The `Justfile` defines the development workflow. Run `just` to list all targets.

- `just validate` — full pipeline: `lint-strict`, `lint-imports`, `type-check` (mypy), `type-check-pyright` (basedpyright), `test`.
- `just lint-strict` — auto-format and ruff check.
- `just lint-imports` — enforce import boundaries (no `core/` → `shell/`, no `worker_sdk/` → `shell/`, no `workers/` → `shell/`).
- `just type-check` — mypy on `src/`, `tests/`, and worker packages.
- `just type-check-pyright` — basedpyright (matches editor LSP).
- `just test` — pytest.
- `just proto` — regenerate protobuf code after editing `proto/synthesis.proto`.
- `just certs` — regenerate the dev TLS CA and per-service certs in `./certs/`. Not needed for `docker compose up`; the `certs-init` service does this automatically.
- `just build-worker <name>` — build a RunPod worker image locally for dev iteration. CI publishes images to `ghcr.io` on pushes to `main` and version tags.
- `just build-edge` — build the generic edge image (`acheron-worker-edge`).
