default:
    @just --list

# Auto-format, fix, and check Python for errors
lint-strict:
    uv run ruff format .
    uv run ruff check --fix .
    uv run ruff check .

# Run static type analysis
type-check:
    uv run mypy src/ tests/ workers/qwen3tts/ workers/granite_speech/ workers/translategemma/ workers/_shared/

# Run Python unit tests
test:
    uv run pytest

# Enforce import boundaries via import-linter
lint-imports:
    uv run lint-imports >/dev/null

# Run basedpyright type analysis (matches editor LSP)
type-check-pyright:
    uv run basedpyright

# Compile protobuf definitions
proto:
    uv run python -m grpc_tools.protoc \
        -I proto \
        --python_out=src/acheron/proto \
        --grpc_python_out=src/acheron/proto \
        proto/synthesis.proto
    sed -i 's/^import synthesis_pb2/from . import synthesis_pb2/' src/acheron/proto/synthesis_pb2_grpc.py

# Full validation pipeline: lint, type-check, then test
validate: lint-strict lint-imports type-check type-check-pyright test

# Install all dependencies including dev
install:
    uv sync --all-extras --all-packages

# Generate local Acheron CA + per-service dev certs in ./certs/
certs:
    uv run python scripts/generate_dev_certs.py

# Build a worker image locally for dev iteration. CI does the real publish.
build-worker name:
    uv build --package acheron --out-dir dist
    docker build -f workers/{{name}}/Dockerfile.runpod -t acheron-{{name}}-runpod:dev .

# Build the generic edge image (acheron-worker-edge).
build-edge:
    uv build --package acheron --out-dir dist
    docker build -f Dockerfile.edge -t acheron-worker-edge:dev .

# Validate the docs/ux_review/ rubric against the schema and HEAD.
# Asserts: (1) YAML frontmatter in deploy/ops/maint.md parses and matches the spec §3.1 schema,
# (2) all `files[].path` exist at HEAD, (3) all `files[].lines` ranges are within file length,
# (4) `discovered_via: simulation` stories have a `sim/scenarios/*.py` that references the story ID,
# (5) `discovered_via: first-run` stories have a `tests/first_run/test_*.py` that references the story ID,
# (6) `on-call` / `user-feedback` channels have their `incident_ref` / `feedback_ref` populated,
# (7) `status: wontfix` stories have `wontfix_reason` populated.
# Exits non-zero on any failure when run with --strict (default for CI).
ux-validate:
    uv run python -m acheron.ux_review.validate --root docs/ux_review --head "$(git rev-parse HEAD)" --strict

# Verify a single story is mechanically verified. Used by ux-review-tackle's post-merge
# verification gate. Returns PASS / PARTIAL / FAIL with reason.
ux-verify story-id:
    uv run python -m acheron.ux_review.verify --root docs/ux_review --id {{story-id}} --head "$(git rev-parse HEAD)"

# Boot the RunPod mock simulator (mock_runpod on 127.0.0.1:8999).
# Profile-gated; uses compose/sim.yml overlay.
runpod-sim:
    docker compose -f docker-compose.yml -f compose/sim.yml up -d runpod-sim

# Run all Phase 3a scenarios end-to-end. Requires 'just runpod-sim' first.
runpod-bootstrap:
    uv run python -m sim.run --all
