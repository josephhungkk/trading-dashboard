#!/usr/bin/env bash
# Allow policy: Google login + email in {josephhungkk@gmail.com, ispyling@gmail.com}.
# Requires Google IdP configured in CF Zero Trust dashboard first.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
jq_require

APP_ID=$(cat "$STATE_DIR/access-app-id" 2>/dev/null || die "Run 20-access-app.sh first")
POLICY_NAME="allow-google-emails"
EMAILS='["josephhungkk@gmail.com","ispyling@gmail.com"]'

log "Finding Google IdP..."
GOOGLE_IDP_ID=$(cf GET "/accounts/$CF_ACCOUNT_ID/access/identity_providers" \
    | jq -r '.result[]? | select(.type=="google") | .id' | head -1)

if [[ -z "$GOOGLE_IDP_ID" || "$GOOGLE_IDP_ID" == "null" ]]; then
    die "Google IdP not configured. Add it via CF dashboard → Zero Trust → Settings → Authentication → Login methods → Add new → Google (paste OAuth client id+secret from Google Cloud Console)."
fi
ok "Google IdP id=$GOOGLE_IDP_ID"

log "Looking for existing policy '$POLICY_NAME'..."
existing=$(cf GET "/accounts/$CF_ACCOUNT_ID/access/apps/$APP_ID/policies" \
    | jq -r --arg n "$POLICY_NAME" '.result[]? | select(.name==$n) | .id' | head -1)

body=$(jq -n \
    --arg n "$POLICY_NAME" \
    --arg idp "$GOOGLE_IDP_ID" \
    --argjson emails "$EMAILS" \
    '{name:$n,
      decision:"allow",
      include:[{email:{email:$emails[0]}},{email:{email:$emails[1]}}],
      require:[{login_method:{id:$idp}}],
      precedence:1,
      session_duration:"24h"}')

if [[ -n "$existing" && "$existing" != "null" ]]; then
    log "Updating existing policy..."
    resp=$(cf PUT "/accounts/$CF_ACCOUNT_ID/access/apps/$APP_ID/policies/$existing" --data "$body")
else
    log "Creating new policy..."
    resp=$(cf POST "/accounts/$CF_ACCOUNT_ID/access/apps/$APP_ID/policies" --data "$body")
fi

echo "$resp" | jq -e '.success' >/dev/null || die "Policy op failed: $(echo "$resp" | jq -c .)"
ok "Policy '$POLICY_NAME' applied"
