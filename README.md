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
    transports/     # HttpWorker, GrpcWorker, LocalWorker
    executors/      # Sequential, async, batch execution strategies
  proto/            # Generated protobuf code (gitignored)
dashboard/          # HTMX monitoring dashboard (separate package)
stubs/              # Stub workers for local dev (HTTP + gRPC)
tests/              # Unit + integration tests (mirrors src/)
proto/              # Proto definitions
```

The `core/` package never imports from `shell/` — enforced by import-linter.

### Testing

```bash
just test                    # all tests
uv run pytest tests/shell/   # shell tests only
uv run pytest tests/integration/  # integration tests only
uv run pytest -k "test_name" # single test
```

Integration tests start real HTTP/gRPC stub servers and verify the full orchestrator → worker dispatch path.

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

Stub workers return mock data. Replace with real GPU workers (Layer 8) for production.

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ACHERON_URL` | `http://localhost:8000` | CLI: orchestrator URL |
| `ACHERON_REGISTRATION_TOKEN` | `dev-registration-token` | Worker registration shared secret |
| `REDIS_URL` | `redis://redis:6379` | Orchestrator: Redis connection |

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
