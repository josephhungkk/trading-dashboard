#!/usr/bin/env bash
# Bypass policy for CF Access service tokens (CI smoke tests).
# Run ONCE to create placeholder. Run AGAIN after 23-service-token.sh to attach token.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
jq_require

APP_ID=$(cat "$STATE_DIR/access-app-id" 2>/dev/null || die "Run 20-access-app.sh first")
POLICY_NAME="bypass-service-token"
SVC_TOKEN_ID=$(cat "$STATE_DIR/service-token-id" 2>/dev/null || echo "")

log "Looking for existing bypass policy..."
existing=$(cf GET "/accounts/$CF_ACCOUNT_ID/access/apps/$APP_ID/policies" \
    | jq -r --arg n "$POLICY_NAME" '.result[]? | select(.name==$n) | .id' | head -1)

if [[ -z "$SVC_TOKEN_ID" ]]; then
    log "No service token id yet — creating placeholder policy."
    body=$(jq -n --arg n "$POLICY_NAME" \
        '{name:$n, decision:"bypass", include:[{everyone:{}}], precedence:2}')
    # Placeholder 'include' uses everyone: {} because CF requires at least one include.
    # This does NOT actually bypass; the real policy below attaches the token id.
    # But using {everyone:{}} here would let everyone through — use a narrower
    # placeholder: include an empty email array that matches nothing.
    # Actually safer: include a non-matching synthetic email.
    body=$(jq -n --arg n "$POLICY_NAME" \
        '{name:$n, decision:"bypass",
          include:[{email:{email:"placeholder-noreply@kiusinghung.com"}}],
          precedence:2}')
else
    log "Attaching service token $SVC_TOKEN_ID to bypass policy..."
    body=$(jq -n --arg n "$POLICY_NAME" --arg t "$SVC_TOKEN_ID" \
        '{name:$n,
          decision:"bypass",
          include:[{service_token:{token_id:$t}}],
          precedence:2}')
fi

if [[ -n "$existing" && "$existing" != "null" ]]; then
    resp=$(cf PUT "/accounts/$CF_ACCOUNT_ID/access/apps/$APP_ID/policies/$existing" --data "$body")
else
    resp=$(cf POST "/accounts/$CF_ACCOUNT_ID/access/apps/$APP_ID/policies" --data "$body")
fi

echo "$resp" | jq -e '.success' >/dev/null || die "Policy op failed: $(echo "$resp" | jq -c .)"
ok "Bypass policy applied"
if [[ -z "$SVC_TOKEN_ID" ]]; then
    echo "  Placeholder policy created. Next: run ./23-service-token.sh, then re-run THIS script to wire the token."
fi
