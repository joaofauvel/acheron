FROM python:3.14-slim AS builder

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY src/acheron/ ./src/acheron/

RUN uv build --out-dir /app/dist

FROM python:3.14-slim AS orchestrator

ARG ACHERON_BUILD_SHA
ARG ACHERON_BUILD_TIME
ARG ACHERON_BUILD_BRANCH
ARG ACHERON_BUILD_DIRTY
ARG ACHERON_BUILD_IMAGE
ARG ACHERON_BUILD_REGISTRY
ENV ACHERON_BUILD_SHA=${ACHERON_BUILD_SHA} \
    ACHERON_BUILD_TIME=${ACHERON_BUILD_TIME} \
    ACHERON_BUILD_BRANCH=${ACHERON_BUILD_BRANCH} \
    ACHERON_BUILD_DIRTY=${ACHERON_BUILD_DIRTY} \
    ACHERON_BUILD_IMAGE=${ACHERON_BUILD_IMAGE} \
    ACHERON_BUILD_REGISTRY=${ACHERON_BUILD_REGISTRY}

RUN apt-get update \
    && apt-get install --no-install-recommends --yes ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /app/dist/*.whl ./
RUN pip install --no-cache-dir ./*.whl && rm ./*.whl
RUN useradd --create-home --uid 1000 --shell /bin/bash acheron \
    && mkdir -p /data/jobs \
    && chown acheron:root /data /data/jobs
USER acheron
CMD ["python", "-m", "acheron.shell.api"]

FROM python:3.14-slim AS dashboard

WORKDIR /app
COPY --from=builder /app/dist/*.whl ./
COPY dashboard/ ./dashboard/
RUN set -- ./*.whl && pip install --no-cache-dir "$1[dashboard]" && rm "$1"
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
RUN pip install --no-cache-dir cryptography~=46.0
COPY scripts/generate_dev_certs.py ./scripts/generate_dev_certs.py
CMD ["python", "scripts/generate_dev_certs.py", "--out-dir", "/certs"]
