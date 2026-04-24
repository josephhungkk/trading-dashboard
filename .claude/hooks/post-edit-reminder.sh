#!/usr/bin/env bash
# Project-scope PostToolUse reminder hook.
#
# Reads PostToolUse JSON from stdin. If the touched file matches a high-impact
# path (Alembic migrations, lockfiles, docker-compose, broker adapter base,
# CLAUDE.md/TASKS.md), emits one short reminder line that Claude sees.
#
# Stays silent for everything else, so it does not add chatter on routine edits.
# Always exits 0 so a hook failure can never block a tool call.

set -uo pipefail

input=$(cat)
file_path=$(printf '%s' "$input" | python3 -c '
import sys, json
try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)
ti = payload.get("tool_input") or {}
print(ti.get("file_path") or ti.get("notebook_path") or "")
' 2>/dev/null || true)

[ -z "$file_path" ] && exit 0

case "$file_path" in
  */backend/alembic/versions/*.py | */migrations/versions/*.py)
    echo "[reminder] alembic migration touched — verify locally: docker compose exec backend alembic upgrade head (then alembic downgrade -1; alembic upgrade head)"
    ;;
  */pnpm-lock.yaml)
    echo "[reminder] pnpm-lock.yaml changed — confirm the dependency change was intentional; rerun pnpm typecheck + lint"
    ;;
  */uv.lock)
    echo "[reminder] uv.lock changed — confirm intentional; rerun cd backend && uv run pytest"
    ;;
  */docker-compose*.yml | */docker-compose*.yaml)
    echo "[reminder] docker-compose changed — recreate the affected service: docker compose up -d --force-recreate <service> (and bounce nginx if the backend was touched)"
    ;;
  */backend/app/brokers/base.py)
    echo "[reminder] BrokerAdapter base modified — every concrete adapter under backend/app/brokers/ must be updated in the same commit"
    ;;
  */CLAUDE.md)
    echo "[reminder] CLAUDE.md edited — if a phase is closing, also update TASKS.md + CHANGELOG.md before tagging"
    ;;
  */TASKS.md)
    echo "[reminder] TASKS.md edited — if a phase just closed, also bump CHANGELOG.md and tag vN.N.N"
    ;;
  */frontend/eslint.config.mjs)
    echo "[reminder] eslint config changed — boundaries rules are the source of truth; rerun pnpm lint to surface any new violations"
    ;;
  *.env.example)
    echo "[reminder] .env.example changed — most runtime config now lives in app_config / app_secrets via /api/admin (see CLAUDE.md §Configuration Storage); confirm this key really belongs in bootstrap .env"
    ;;
esac

exit 0
