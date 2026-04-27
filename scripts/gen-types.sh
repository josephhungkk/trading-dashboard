#!/usr/bin/env bash
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT/backend"
uv run python -m app.scripts.dump_openapi > /tmp/openapi.json
cd "$ROOT/frontend"
pnpm exec openapi-typescript /tmp/openapi.json -o src/services/api-generated.ts
echo "Wrote frontend/src/services/api-generated.ts"
