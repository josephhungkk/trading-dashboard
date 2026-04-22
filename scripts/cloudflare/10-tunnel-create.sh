#!/usr/bin/env bash
# Create (or fetch existing) CF Tunnel named "dashboard-prod".
# Writes credentials JSON to ~/.secrets/cloudflared-<UUID>.json (mode 0600).
# Idempotent: safe to re-run.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
jq_require

TUNNEL_NAME="${TUNNEL_NAME:-dashboard-prod}"
SECRETS_DIR="${SECRETS_DIR:-$HOME/.secrets}"
mkdir -p "$SECRETS_DIR"
chmod 0700 "$SECRETS_DIR"

log "Looking for existing tunnel named '$TUNNEL_NAME'..."
existing=$(cf GET "/accounts/$CF_ACCOUNT_ID/cfd_tunnel?name=$TUNNEL_NAME&is_deleted=false" \
    | jq -r --arg n "$TUNNEL_NAME" '.result[]? | select(.name==$n) | .id' | head -1)

if [[ -n "$existing" && "$existing" != "null" ]]; then
    ok "Tunnel '$TUNNEL_NAME' already exists (id=$existing)"
    TUNNEL_ID="$existing"
    CRED_FILE="$SECRETS_DIR/cloudflared-$TUNNEL_ID.json"
    if [[ ! -f "$CRED_FILE" ]]; then
        echo "  WARN: credentials file $CRED_FILE is missing."
        echo "  Run 'cloudflared tunnel token $TUNNEL_ID' on a machine with cloudflared"
        echo "  installed to regenerate, or delete tunnel via dashboard and re-run."
    fi
else
    log "Creating tunnel '$TUNNEL_NAME'..."
    SECRET=$(openssl rand -base64 32)
    resp=$(cf POST "/accounts/$CF_ACCOUNT_ID/cfd_tunnel" \
        --data "$(jq -n --arg n "$TUNNEL_NAME" --arg s "$SECRET" \
                 '{name:$n,tunnel_secret:$s,config_src:"cloudflare"}')")
    echo "$resp" | jq -e '.success' >/dev/null || die "Create failed: $(echo "$resp" | jq -c .)"
    TUNNEL_ID=$(echo "$resp" | jq -r '.result.id')
    ok "Tunnel created (id=$TUNNEL_ID)"

    CRED_FILE="$SECRETS_DIR/cloudflared-$TUNNEL_ID.json"
    jq -n --arg aid "$CF_ACCOUNT_ID" --arg tid "$TUNNEL_ID" --arg s "$SECRET" \
        '{AccountTag:$aid,TunnelID:$tid,TunnelName:"dashboard-prod",TunnelSecret:$s}' \
        > "$CRED_FILE"
    chmod 0600 "$CRED_FILE"
    ok "Credentials saved to $CRED_FILE (mode 0600)"
fi

echo "$TUNNEL_ID" > "$STATE_DIR/tunnel-id"
ok "Tunnel id written to $STATE_DIR/tunnel-id"
