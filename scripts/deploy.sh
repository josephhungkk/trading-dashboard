#!/usr/bin/env bash
# Manual deploy (for when you want to bypass GitHub Actions).
# Usage: ./scripts/deploy.sh
set -euo pipefail

# Assert .env is not world-readable
ENV_PERMS=$(stat -c '%a' .env 2>/dev/null || echo "000")
if [[ "$ENV_PERMS" != "600" ]]; then
    echo "ERROR: .env permissions are ${ENV_PERMS} — must be 600. Run: chmod 600 .env"
    exit 1
fi

VPS_HOST="${VPS_HOST:-88.208.197.219}"
VPS_USER="${VPS_USER:-trader}"
VPS_PORT="${VPS_PORT:-2222}"
VPS_PATH="${VPS_PATH:-/home/trader/trading-dashboard}"

echo "==> Syncing to $VPS_USER@$VPS_HOST:$VPS_PATH"
rsync -avz --delete \
    --exclude '.git/' \
    --exclude 'node_modules/' \
    --exclude '__pycache__/' \
    --exclude '.venv/' \
    --exclude '*.pyc' \
    --exclude '.env' \
    --exclude 'secrets/' \
    --exclude 'frontend/dist/' \
    --exclude 'tests/e2e/test-results/' \
    --exclude 'tests/e2e/playwright-report/' \
    --exclude 'scripts/cloudflare/.state/' \
    -e "ssh -p $VPS_PORT" \
    ./ "$VPS_USER@$VPS_HOST:$VPS_PATH/"

echo "==> Remote build + up + nginx reload"
ssh -p "$VPS_PORT" "$VPS_USER@$VPS_HOST" <<EOF
  set -e
  cd "$VPS_PATH"
  # Phase 10a.5.1 ops debt: prune dangling BuildKit cache before build.
  # The VPS / volume filled at 67 GB during Phase 10a close-out; without
  # this step every deploy accretes another generation of cache layers.
  # --filter "until=168h" keeps the last week of warm cache; older
  # layers are recoverable from registry anyway.
  echo "--> Pruning BuildKit cache older than 7 days..."
  docker buildx prune --force --filter "until=168h" || true
  docker compose -f docker-compose.prod.yml build
  docker compose -f docker-compose.prod.yml up -d
  # Post-recreate 502 storm fix: nginx caches backend IP; reload re-resolves.
  # See memory nginx_backend_recreate_502.md
  echo "--> Reloading nginx..."
  docker compose -f docker-compose.prod.yml exec -T nginx nginx -s reload
  docker compose -f docker-compose.prod.yml ps
EOF

echo "==> Waiting for backend health..."
for i in $(seq 1 30); do
    if ssh -p "$VPS_PORT" "$VPS_USER@$VPS_HOST" 'curl -sf -H "Host: dashboard.kiusinghung.com" http://127.0.0.1/health' >/dev/null; then
        echo "✓ Backend healthy"
        break
    fi
    sleep 2
done

echo "==> Done. Run tests/e2e smoke to verify public domain:"
echo "   cd tests/e2e && pnpm test:smoke"
