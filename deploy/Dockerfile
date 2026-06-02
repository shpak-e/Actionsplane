# Image for the Python services. The compose file overrides `command:` to select which
# process runs (api / ingestor / worker / migrate); the default is the API.
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# uv for fast, reproducible installs.
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv pip install --system .

# Drop privileges — this service holds GitHub credentials.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000
# Console scripts (uvicorn/arq/alembic) are on PATH after the system install.
CMD ["uvicorn", "actionsplane.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
