#!/usr/bin/env bash
# Installs client-cert auth on the local (WSL docker-compose) dev PG instance.
# Patches pg_hba.conf and copies the CA cert into the PG data dir.
# Must be run with access to the PG data directory.
set -euo pipefail

CA_DIR="${HOME}/.dashboard-pg-ca"
# Detect PG data dir from running container
PG_CONTAINER="${PG_CONTAINER:-postgres}"
PG_DATA=$(docker compose exec -T "${PG_CONTAINER}" sh -c 'echo $PGDATA')

[[ -f "${CA_DIR}/dev-ca.crt" ]] || { echo "Run generate-ca.sh first"; exit 1; }
[[ -f "${CA_DIR}/client.crt" ]] || { echo "Run generate-client-cert.sh first"; exit 1; }

echo "Copying dev CA cert into PG container at ${PG_DATA}/dev-ca.crt"
docker cp "${CA_DIR}/dev-ca.crt" "${PG_CONTAINER}:${PG_DATA}/dev-ca.crt"
docker compose exec -T "${PG_CONTAINER}" chown postgres:postgres "${PG_DATA}/dev-ca.crt"

echo "Patching postgresql.conf for SSL..."
docker compose exec -T "${PG_CONTAINER}" bash -c "
    grep -q 'ssl = on' ${PG_DATA}/postgresql.conf || echo \"ssl = on\" >> ${PG_DATA}/postgresql.conf
    grep -q 'ssl_ca_file' ${PG_DATA}/postgresql.conf \
        && sed -i \"s|#*ssl_ca_file.*|ssl_ca_file = 'dev-ca.crt'|\" ${PG_DATA}/postgresql.conf \
        || echo \"ssl_ca_file = 'dev-ca.crt'\" >> ${PG_DATA}/postgresql.conf
"

echo "Patching pg_hba.conf..."
docker compose exec -T "${PG_CONTAINER}" bash -c "
    # Comment out the password line for dashboard_user on 127.0.0.1
    sed -i \"s|^host \\+dashboard \\+dashboard_user \\+127.0.0.1/32 \\+scram-sha-256|# &|\" ${PG_DATA}/pg_hba.conf
    # Add cert auth line if not present
    grep -q 'cert clientcert=verify-full' ${PG_DATA}/pg_hba.conf \
        || echo 'hostssl dashboard dashboard_user 127.0.0.1/32 cert clientcert=verify-full' >> ${PG_DATA}/pg_hba.conf
"

echo "Reloading PG config..."
docker compose exec -T "${PG_CONTAINER}" bash -c "psql -U postgres -c 'SELECT pg_reload_conf();'"

echo ""
echo "Done. Verify with:"
echo "  psql 'postgresql://dashboard_user@127.0.0.1:5432/dashboard?sslmode=verify-full&sslcert=${CA_DIR}/client.crt&sslkey=${CA_DIR}/client.key&sslrootcert=${CA_DIR}/dev-ca.crt' -c '\\conninfo'"
echo ""
echo "ROLLBACK: uncomment the scram-sha-256 line in pg_hba.conf and run pg_reload_conf()"
