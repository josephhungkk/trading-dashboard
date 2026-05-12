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
#
# Opt-in env vars: source .env.test if present. This is a separate dotenv
# file (gitignored) holding credentials + flags for real-service tests:
#   CI_USE_REAL_REDIS=1
#   CI_REDIS_URL=redis://:<password>@redis:6379/0
#   ALPACA_PAPER_API_KEY=...
#   ALPACA_PAPER_API_SECRET=...
#   SCHWAB_APP_KEY=...
#   SCHWAB_APP_SECRET=...
#   SCHWAB_PAPER_ACCOUNT_HASH=...
#   IBKR_PAPER_ACCOUNT=...
#   CF_ACCESS_CLIENT_ID=...
#   CF_ACCESS_CLIENT_SECRET=...
#   FUTU_HOST=10.10.0.2
#   FUTU_PORT=11111
#   E2E_BACKEND_URL=https://dashboard.kiusinghung.com
#   CI_USE_REAL_SCHWAB=1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TEST_ENV_FILE="$SCRIPT_DIR/../.env.test"
TEST_DB_URL="${TEST_DATABASE_URL:-postgresql+asyncpg://test:test@test_postgres:5432/test}"

# Verify test PG is reachable before launching pytest — the typical first-time
# failure mode is "I forgot to start the test_postgres profile". Bail loud.
if ! docker compose exec -T test_postgres pg_isready -U test -d test > /dev/null 2>&1; then
    echo "ERROR: test_postgres is not reachable." >&2
    echo "Start it with: docker compose --profile test up -d test_postgres" >&2
    exit 2
fi

# Refresh operator-tuned tables (app_config, app_secrets, risk_limits) from
# prod PG into test_postgres before running tests. Skipped when SKIP_CRED_COPY
# is set (faster iteration when you know the creds haven't changed).
if [[ "${SKIP_CRED_COPY:-0}" != "1" ]]; then
    if [[ -x "$REPO_ROOT/scripts/db/copy-prod-creds-to-test-pg.sh" ]]; then
        "$REPO_ROOT/scripts/db/copy-prod-creds-to-test-pg.sh" >&2 || {
            echo "WARN: prod->test creds copy failed; tests reading from test_postgres app_secrets will skip." >&2
        }
    fi
fi

EXTRA_ENV_ARGS=()
if [[ -f "$TEST_ENV_FILE" ]]; then
    # Inject every non-comment KEY=VALUE line into the docker exec env list.
    while IFS= read -r line; do
        [[ -z "$line" || "$line" == \#* ]] && continue
        EXTRA_ENV_ARGS+=(-e "$line")
    done < "$TEST_ENV_FILE"
fi

exec docker compose exec -T \
    -e DATABASE_URL="$TEST_DB_URL" \
    "${EXTRA_ENV_ARGS[@]}" \
    backend /app/.venv/bin/pytest --timeout=120 "$@"
