#!/usr/bin/env bash
# Phase 11a CI-debt: run backend tests against the dedicated test_postgres
# container, NOT the prod NUC DB. The test_admin_api guardrail will refuse
# to run if DATABASE_URL points at 10.10.0.2.
#
# Usage:
#   ./scripts/run-tests.sh                  # full suite
#   ./scripts/run-tests.sh tests/services/  # subset
#   ./scripts/run-tests.sh -m no_db -q      # markers + flags pass through
#
# Prerequisite: docker compose --profile test up -d test_postgres

set -euo pipefail

TEST_DB_URL="${TEST_DATABASE_URL:-postgresql+asyncpg://test:test@test_postgres:5432/test}"

# Verify test PG is reachable before launching pytest — the typical first-time
# failure mode is "I forgot to start the test_postgres profile". Bail loud.
if ! docker compose exec -T test_postgres pg_isready -U test -d test > /dev/null 2>&1; then
    echo "ERROR: test_postgres is not reachable." >&2
    echo "Start it with: docker compose --profile test up -d test_postgres" >&2
    exit 2
fi

exec docker compose exec -T \
    -e DATABASE_URL="$TEST_DB_URL" \
    backend /app/.venv/bin/pytest --timeout=120 -p no:cacheprovider "$@"
