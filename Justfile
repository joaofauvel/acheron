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

# Full validation pipeline: lint, type-check, then test
validate: lint-strict lint-imports type-check test

# Install all dependencies including dev
install:
    uv sync --all-extras
