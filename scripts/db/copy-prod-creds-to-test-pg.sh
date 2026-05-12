#!/usr/bin/env bash
# scripts/db/copy-prod-creds-to-test-pg.sh
#
# Phase 11a CI-debt (2026-05-12): copy operator-tuned tables from the prod
# PG 18 at 10.10.0.2 to the test_postgres PG 16 docker container, so
# real-broker integration tests can read creds via ConfigService against
# test_postgres (matching production code paths) without needing CF
# Access service tokens.
#
# Tables copied (data-only, INSERTs):
#   - app_config       — runtime settings (broker hosts, ports, flags)
#   - app_secrets      — Fernet-encrypted creds (broker app keys, CF tokens)
#   - risk_limits      — operator-tuned risk caps
#
# Decryption: ciphertext rows in app_secrets stay encrypted on the wire
# and decrypt cleanly in test_postgres because APP_SECRET_KEY is the
# same in both environments.
#
# Idempotent: TRUNCATE target tables then INSERT each row. Safe to re-run.
# Bails loud if either DB is unreachable.
#
# Implementation note: prod is PG 18, test_postgres is PG 16. pg_dump's
# server-version-check rejects this combo, so we use `COPY ... TO STDOUT`
# / `COPY ... FROM STDIN` instead — server-side serialization, no client
# version dependency.

set -euo pipefail

# Tables to copy.
TABLES=(app_config app_secrets risk_limits)

# Extract prod password from main .env DATABASE_URL.
PROD_PG_PASSWORD="$(grep '^DATABASE_URL=' /home/joseph/dashboard/.env | sed -nE 's|.*://[^:]+:([^@]+)@.*|\1|p')"
if [[ -z "$PROD_PG_PASSWORD" ]]; then
    echo "ERROR: could not extract prod PG password from /home/joseph/dashboard/.env DATABASE_URL" >&2
    exit 2
fi

# Verify prod PG reachable.
if ! docker compose exec -T -e PGPASSWORD="$PROD_PG_PASSWORD" test_postgres \
    pg_isready -h 10.10.0.2 -U trader -d dashboard -t 3 > /dev/null 2>&1; then
    echo "ERROR: prod PG at 10.10.0.2:5432 is not reachable from test_postgres container." >&2
    exit 2
fi

# Verify test_postgres reachable.
if ! docker compose exec -T test_postgres pg_isready -U test -d test -t 3 > /dev/null 2>&1; then
    echo "ERROR: test_postgres is not reachable." >&2
    exit 2
fi

echo "==> Copying operator-tuned tables: prod (10.10.0.2 PG18) -> test_postgres (PG16)"
for table in "${TABLES[@]}"; do
    # Count source rows.
    count=$(docker compose exec -T -e PGPASSWORD="$PROD_PG_PASSWORD" test_postgres \
        psql -h 10.10.0.2 -U trader -d dashboard -At -c "SELECT count(*) FROM $table" 2>/dev/null || echo "0")
    count="${count//[[:space:]]/}"
    echo "  prod.$table: $count rows"

    # Always TRUNCATE so an emptied prod table also clears test_postgres.
    docker compose exec -T test_postgres \
        psql -U test -d test -q -c "TRUNCATE $table" > /dev/null

    if [[ "$count" == "0" ]]; then
        continue
    fi

    # COPY-out from prod, COPY-in to test. The COPY format is text by
    # default; we use the same on both sides so PG 18->16 stays compatible.
    docker compose exec -T -e PGPASSWORD="$PROD_PG_PASSWORD" test_postgres \
        sh -c "psql -h 10.10.0.2 -U trader -d dashboard -c '\\copy (SELECT * FROM $table) TO STDOUT' | psql -U test -d test -c '\\copy $table FROM STDIN'"

    # Verify target.
    target_count=$(docker compose exec -T test_postgres \
        psql -U test -d test -At -c "SELECT count(*) FROM $table" 2>/dev/null || echo "?")
    target_count="${target_count//[[:space:]]/}"
    echo "    -> test.$table: $target_count rows"
done

echo "==> Done."
