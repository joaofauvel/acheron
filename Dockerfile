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
CMD ["python", "-m", "acheron.shell.api"]

FROM python:3.14-slim AS dashboard

WORKDIR /app
COPY --from=builder /app/dist/*.whl ./
COPY dashboard/ ./dashboard/
RUN pip install --no-cache-dir ./*.whl && rm ./*.whl
ENV PYTHONPATH=/app
CMD ["uvicorn", "dashboard.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]

FROM python:3.14-slim AS worker-stub

WORKDIR /app
COPY --from=builder /app/dist/*.whl ./
COPY stubs/ ./stubs/
COPY proto/ ./proto/
RUN pip install --no-cache-dir ./*.whl && rm ./*.whl
ENV PYTHONPATH=/app
CMD ["python", "-m", "stubs.worker_stub"]

FROM python:3.14-slim AS grpc-stub

WORKDIR /app
COPY --from=builder /app/dist/*.whl ./
COPY stubs/ ./stubs/
COPY proto/ ./proto/
RUN pip install --no-cache-dir ./*.whl && rm ./*.whl
ENV PYTHONPATH=/app
CMD ["python", "-m", "stubs.grpc_worker_stub"]
