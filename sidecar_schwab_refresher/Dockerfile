FROM mcr.microsoft.com/playwright/python:v1.45.0
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends xvfb && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY . .
ENV PYTHONPATH=/app
CMD ["xvfb-run", "-a", "uv", "run", "python", "-m", "sidecar_schwab_refresher.main"]
