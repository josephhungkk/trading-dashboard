#!/usr/bin/env bash
# Create (or fetch) Zero Trust Access application for dashboard.kiusinghung.com.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
jq_require

DOMAIN="dashboard.kiusinghung.com"
APP_NAME="Dashboard"

log "Looking for existing Access app named '$APP_NAME'..."
existing=$(cf GET "/accounts/$CF_ACCOUNT_ID/access/apps" \
    | jq -r --arg n "$APP_NAME" '.result[]? | select(.name==$n) | .id' | head -1)

if [[ -n "$existing" && "$existing" != "null" ]]; then
    ok "Access app '$APP_NAME' exists (id=$existing)"
    APP_ID="$existing"
else
    log "Creating Access app..."
    body=$(jq -n --arg n "$APP_NAME" --arg d "$DOMAIN" \
        '{name:$n,
          domain:$d,
          type:"self_hosted",
          session_duration:"24h",
          auto_redirect_to_identity:false,
          app_launcher_visible:false}')
    resp=$(cf POST "/accounts/$CF_ACCOUNT_ID/access/apps" --data "$body")
    echo "$resp" | jq -e '.success' >/dev/null || die "Create failed: $(echo "$resp" | jq -c .)"
    APP_ID=$(echo "$resp" | jq -r '.result.id')
    ok "Access app created (id=$APP_ID)"
fi

echo "$APP_ID" > "$STATE_DIR/access-app-id"
ok "App id written to $STATE_DIR/access-app-id"
