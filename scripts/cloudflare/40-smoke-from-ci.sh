#!/usr/bin/env bash
# Curl smoke helper. Invoked by GitHub Actions after deploy.
# Requires: CF_ACCESS_CLIENT_ID + CF_ACCESS_CLIENT_SECRET env vars.
set -euo pipefail

URL="${URL:-https://dashboard.kiusinghung.com/health}"
: "${CF_ACCESS_CLIENT_ID:?Set CF_ACCESS_CLIENT_ID}"
: "${CF_ACCESS_CLIENT_SECRET:?Set CF_ACCESS_CLIENT_SECRET}"

echo "==> Smoke GET $URL (with service token)"
resp=$(curl -sf -w "\n__HTTP_%{http_code}__" \
    -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
    -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
    "$URL")

code=$(echo "$resp" | tr '\n' ' ' | grep -oE '__HTTP_[0-9]+__' | grep -oE '[0-9]+')
body=$(echo "$resp" | sed 's/__HTTP_[0-9]*__$//')

if [[ "$code" != "200" ]]; then
    echo "✗ HTTP $code"
    echo "$body"
    exit 1
fi

if echo "$body" | grep -q '"status":"ok"'; then
    echo "✓ /health returned status:ok"
    echo "$body"
else
    echo "✗ /health body missing status:ok"
    echo "$body"
    exit 1
fi
