#!/usr/bin/env bash
# Quick-restart helper: restart the backend container AND reload nginx so
# /api/* doesn't 502 for ~1-2s while nginx re-resolves the new container IP.
#
# Use this instead of `docker compose restart backend` directly. See memory
# `nginx_backend_recreate_502.md` for why.
#
# Usage:
#   ./scripts/restart-backend.sh        # quick restart, no rebuild
#
# For full rebuild + deploy use ./scripts/deploy.sh.
set -euo pipefail

VPS_HOST="${VPS_HOST:-88.208.197.219}"
VPS_USER="${VPS_USER:-trader}"
VPS_PORT="${VPS_PORT:-2222}"
VPS_PATH="${VPS_PATH:-/home/trader/trading-dashboard}"

echo "==> Restarting backend on $VPS_USER@$VPS_HOST"
ssh -p "$VPS_PORT" "$VPS_USER@$VPS_HOST" <<EOF
  set -e
  cd "$VPS_PATH"
  docker compose -f docker-compose.prod.yml restart backend
  # Critical: nginx caches the backend container IP at start; without this
  # reload, /api/* 502s for ~1-2s after the container recreate.
  echo "--> Reloading nginx..."
  docker compose -f docker-compose.prod.yml exec -T nginx nginx -s reload
  docker compose -f docker-compose.prod.yml ps --format "{{.Name}}\t{{.Status}}"
EOF

echo "==> Waiting for backend health..."
for i in $(seq 1 15); do
    if ssh -p "$VPS_PORT" "$VPS_USER@$VPS_HOST" 'curl -sf -H "Host: dashboard.kiusinghung.com" http://127.0.0.1/health' >/dev/null; then
        echo "✓ Backend healthy"
        exit 0
    fi
    sleep 2
done
echo "✗ Backend failed to come up healthy in 30s"
exit 1
