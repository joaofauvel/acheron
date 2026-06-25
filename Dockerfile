FROM python:3.14-slim AS builder

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY src/acheron/ ./src/acheron/

RUN uv build --out-dir /app/dist

FROM python:3.14-slim AS orchestrator

WORKDIR /app
COPY --from=builder /app/dist/*.whl ./
RUN pip install --no-cache-dir ./*.whl && rm ./*.whl
RUN useradd --create-home --shell /bin/bash acheron
USER acheron
CMD ["python", "-m", "acheron.shell.api"]

FROM python:3.14-slim AS dashboard

WORKDIR /app
COPY --from=builder /app/dist/*.whl ./
COPY dashboard/ ./dashboard/
RUN pip install --no-cache-dir ./*.whl[dashboard] && rm ./*.whl
RUN useradd --create-home --shell /bin/bash acheron
ENV PYTHONPATH=/app
USER acheron
CMD ["uvicorn", "dashboard.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]

FROM python:3.14-slim AS worker-stub-base

WORKDIR /app
COPY --from=builder /app/dist/*.whl ./
COPY stubs/ ./stubs/
RUN pip install --no-cache-dir ./*.whl && rm ./*.whl
RUN useradd --create-home --shell /bin/bash acheron
ENV PYTHONPATH=/app
USER acheron
# Per-stub CMD specified in docker-compose.yml. Override via `command:` field.

FROM python:3.14-slim AS certs-init

WORKDIR /app
RUN pip install --no-cache-dir cryptography~=49.0
COPY scripts/generate_dev_certs.py ./scripts/generate_dev_certs.py
CMD ["python", "scripts/generate_dev_certs.py", "--out-dir", "/certs"]
