#!/bin/sh
# Container entrypoint: migrate, then exec whatever CMD / compose command was passed.
# Alembic uses pg_advisory_lock internally so multi-worker starts serialize.
set -eu

echo "==> alembic upgrade head"
/app/.venv/bin/alembic upgrade head

echo "==> exec: $*"
exec "$@"
