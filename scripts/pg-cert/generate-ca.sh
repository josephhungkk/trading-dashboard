#!/usr/bin/env bash
# Generates the WSL dev CA for local PG client-cert auth.
# Output: ~/.dashboard-pg-ca/dev-ca.key (mode 0400), dev-ca.crt
# This CA must NEVER be used for the NUC prod instance.
set -euo pipefail

CA_DIR="${HOME}/.dashboard-pg-ca"
mkdir -p "${CA_DIR}"
chmod 700 "${CA_DIR}"

if [[ -f "${CA_DIR}/dev-ca.key" ]]; then
    echo "Dev CA already exists at ${CA_DIR}/dev-ca.key — remove manually to regenerate."
    exit 0
fi

openssl genrsa -out "${CA_DIR}/dev-ca.key" 4096
chmod 0400 "${CA_DIR}/dev-ca.key"

openssl req -new -x509 -days 3650 \
    -key "${CA_DIR}/dev-ca.key" \
    -out "${CA_DIR}/dev-ca.crt" \
    -subj "/CN=DashboardDevCA/O=DashboardDev"

echo "Dev CA generated at ${CA_DIR}/"
echo "  ca.key: ${CA_DIR}/dev-ca.key (mode 0400)"
echo "  ca.crt: ${CA_DIR}/dev-ca.crt"
