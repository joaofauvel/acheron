FROM python:3.14-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --no-dev --no-install-project

COPY src/acheron/ ./src/acheron/
COPY dashboard/ ./dashboard/

ENV PYTHONPATH=/app/src

CMD ["uvicorn", "dashboard.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
