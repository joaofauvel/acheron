default:
    @just --list

# Auto-format, fix, and check Python for errors
lint-strict:
    uv run ruff format .
    uv run ruff check --fix .
    uv run ruff check .

# Run static type analysis
type-check:
    uv run mypy src/ tests/

# Run Python unit tests
test:
    uv run pytest

# Enforce import boundaries via import-linter
lint-imports:
    uv run lint-imports

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
    uv sync --all-extras

# Generate local Acheron CA + per-service dev certs in ./certs/
certs:
    uv run python scripts/generate_dev_certs.py
