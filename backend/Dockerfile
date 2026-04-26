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
