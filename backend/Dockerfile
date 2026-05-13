FROM python:3.14-slim AS base
# python:3.14-slim ships without /usr/share/zoneinfo so ZoneInfo("America/New_York")
# raises ZoneInfoNotFoundError. app/services/ibkr_maintenance.py needs ET / CET /
# HKT for the IBKR reset-window helpers (Phase 4 M20). Install tzdata explicitly.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./

# `dev` stage: includes the [dependency-groups.dev] block (alpaca-py,
# schwabdev, pytest, fakeredis, ruff, etc.) so the dev compose + local
# pytest invocations have the broker SDKs + tooling on the venv. The
# dev compose pins target=dev; prod compose lets the build fall through
# to the prod stage below so the production image stays slim.
FROM base AS dev
# tests/ is in .dockerignore (kept out of prod image); dev compose
# bind-mounts ./backend/tests -> /app/tests at runtime so pytest sees
# whatever is on disk without a rebuild cycle.
RUN uv sync --frozen
COPY app/ ./app/
COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY scripts/ ./scripts/
RUN chmod +x ./scripts/entrypoint.sh
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
ENTRYPOINT ["./scripts/entrypoint.sh"]
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# `prod` stage (default): prod-deps-only, no tests/ directory, slim.
FROM base AS prod
RUN uv sync --frozen --no-dev
COPY app/ ./app/
COPY alembic.ini ./
COPY alembic/ ./alembic/
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
COPY scripts/ ./scripts/
RUN chmod +x ./scripts/entrypoint.sh

ENTRYPOINT ["./scripts/entrypoint.sh"]
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
