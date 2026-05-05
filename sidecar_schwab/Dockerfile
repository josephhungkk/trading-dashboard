FROM python:3.14-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
# Copy package contents into /app/sidecar_schwab/ so `python -m sidecar_schwab.main`
# resolves against PYTHONPATH=/app. Without the subdir, COPY . . flattens the
# layout and Python can't find the package.
COPY . ./sidecar_schwab/
ENV PYTHONPATH=/app
EXPOSE 9090
CMD ["uv", "run", "python", "-m", "sidecar_schwab.main"]
