#!/usr/bin/env bash
# Generates the dashboard_backend client cert signed by the dev CA.
# Output: ~/.dashboard-pg-ca/client.key + client.crt
set -euo pipefail

CA_DIR="${HOME}/.dashboard-pg-ca"
PG_USER="dashboard_user"

[[ -f "${CA_DIR}/dev-ca.key" ]] || { echo "Run generate-ca.sh first"; exit 1; }

openssl genrsa -out "${CA_DIR}/client.key" 4096
chmod 0400 "${CA_DIR}/client.key"

openssl req -new \
    -key "${CA_DIR}/client.key" \
    -out "${CA_DIR}/client.csr" \
    -subj "/CN=${PG_USER}"

openssl x509 -req -days 3650 \
    -in "${CA_DIR}/client.csr" \
    -CA "${CA_DIR}/dev-ca.crt" \
    -CAkey "${CA_DIR}/dev-ca.key" \
    -CAcreateserial \
    -out "${CA_DIR}/client.crt"

rm "${CA_DIR}/client.csr"

echo "Client cert generated:"
echo "  ${CA_DIR}/client.key (mode 0400)"
echo "  ${CA_DIR}/client.crt"
echo ""
echo "Add to backend .env:"
echo "  PG_SSL_CERT_PATH=${CA_DIR}/client.crt"
echo "  PG_SSL_KEY_PATH=${CA_DIR}/client.key"
echo "  PG_SSL_CA_PATH=${CA_DIR}/dev-ca.crt"
