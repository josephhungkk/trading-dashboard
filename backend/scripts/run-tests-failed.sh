#!/usr/bin/env bash
# Phase 11a CI-debt: re-run ONLY tests that failed on the previous run.
# Uses pytest's --lf (last-failed) flag, which reads `.pytest_cache/`. The
# cache survives across runs as long as run-tests.sh isn't passed
# -p no:cacheprovider.
#
# Usage:
#   ./scripts/run-tests-failed.sh
#
# If no previous failures are recorded, --lf falls back to running everything.
# Pass --ff (failed-first) instead to run failed tests first then the rest.

set -euo pipefail

TEST_DB_URL="${TEST_DATABASE_URL:-postgresql+asyncpg://test:test@test_postgres:5432/test}"

if ! docker compose exec -T test_postgres pg_isready -U test -d test > /dev/null 2>&1; then
    echo "ERROR: test_postgres is not reachable." >&2
    echo "Start it with: docker compose --profile test up -d test_postgres" >&2
    exit 2
fi

exec docker compose exec -T \
    -e DATABASE_URL="$TEST_DB_URL" \
    backend /app/.venv/bin/pytest --timeout=120 --lf "$@"
