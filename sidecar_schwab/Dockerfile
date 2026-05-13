FROM python:3.14-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN pip install --no-cache-dir uv
WORKDIR /app

# Build context = repo root (per docker-compose.prod.yml so the COPY of
# proto/ below resolves). Reach into sidecar_schwab/ for pyproject.toml +
# uv.lock for the dep install, then copy the package source separately.
COPY sidecar_schwab/pyproject.toml sidecar_schwab/uv.lock ./
RUN uv sync --frozen --no-dev

# Copy package contents into /app/sidecar_schwab/ so `python -m sidecar_schwab.main`
# resolves against PYTHONPATH=/app.
COPY sidecar_schwab/ ./sidecar_schwab/

# sidecar_schwab/_generated/ is gitignored, so a fresh clone (or any deploy
# that rsyncs only tracked files) ships the source tree without the protoc
# output. Regenerate inside the image so the build is self-contained —
# without this step `from sidecar_schwab._generated.broker.v1 import broker_pb2`
# raises ImportError at startup and the container restart-loops.
COPY proto/broker/v1/broker.proto ./_proto/broker/v1/broker.proto
RUN mkdir -p ./sidecar_schwab/_generated/broker/v1 \
 && uv run python -m grpc_tools.protoc \
      -I./_proto \
      --python_out=./sidecar_schwab/_generated \
      --grpc_python_out=./sidecar_schwab/_generated \
      --pyi_out=./sidecar_schwab/_generated \
      ./_proto/broker/v1/broker.proto \
 && touch ./sidecar_schwab/_generated/__init__.py \
          ./sidecar_schwab/_generated/broker/__init__.py \
          ./sidecar_schwab/_generated/broker/v1/__init__.py \
 && rm -rf ./_proto

ENV PYTHONPATH=/app
EXPOSE 9090
CMD ["uv", "run", "python", "-m", "sidecar_schwab.main"]
