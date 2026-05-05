FROM python:3.14-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN if [ -f uv.lock ]; then uv sync --frozen --no-dev; else uv sync --no-dev; fi
# Copy package contents into /app/sidecar_alpaca/ so `python -m sidecar_alpaca.main`
# resolves against PYTHONPATH=/app. Without the subdir, COPY . . flattens the
# layout and Python can't find the package.
COPY . ./sidecar_alpaca/
ENV PYTHONPATH=/app
EXPOSE ${GRPC_PORT:-9091}
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD ["python", "-c", "import os, socket; s=socket.create_connection(('127.0.0.1', int(os.environ.get('GRPC_PORT', '9091'))), 2); s.close()"]
ENTRYPOINT ["uv", "run", "python", "-m", "sidecar_alpaca.main"]
