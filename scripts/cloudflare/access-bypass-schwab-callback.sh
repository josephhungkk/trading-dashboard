#!/usr/bin/env bash
# Phase 7a — CF Access bypass for /api/oauth/schwab/callback.
# Idempotent: re-runs are no-ops if the policy already exists.
#
# The public Schwab OAuth callback must be reachable without a CF Access JWT
# because Schwab's redirect comes from outside CF. Authentication is enforced
# by the HMAC-signed state nonce instead (H1 invariant).
set -euo pipefail

ZONE_ID="${CF_ZONE_ID:?CF_ZONE_ID env var required}"
ACCOUNT_ID="${CF_ACCOUNT_ID:?CF_ACCOUNT_ID env var required}"
TOKEN="${CF_ACCESS_API_TOKEN:?CF_ACCESS_API_TOKEN env var required}"
APP_NAME="${CF_ACCESS_APP_NAME:-dashboard-kiusinghung}"

POLICY_NAME="bypass-schwab-callback"
POLICY_PRECEDENCE=1

API_BASE="https://api.cloudflare.com/client/v4"

APP_ID="$(
  curl -sf -H "Authorization: Bearer $TOKEN" \
    "$API_BASE/accounts/$ACCOUNT_ID/access/apps?name=$APP_NAME" \
    | jq -r '.result[0].id // empty'
)"

if [[ -z "$APP_ID" || "$APP_ID" == "null" ]]; then
  echo "ERROR: CF Access app '$APP_NAME' not found under account $ACCOUNT_ID" >&2
  exit 1
fi

EXISTING="$(
  curl -sf -H "Authorization: Bearer $TOKEN" \
    "$API_BASE/accounts/$ACCOUNT_ID/access/apps/$APP_ID/policies" \
    | jq -r ".result[] | select(.name==\"$POLICY_NAME\") | .id"
)"

if [[ -n "$EXISTING" ]]; then
  echo "Policy '$POLICY_NAME' already exists ($EXISTING) - no action."
  exit 0
fi

curl -sf -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data @- \
  "$API_BASE/accounts/$ACCOUNT_ID/access/apps/$APP_ID/policies" <<EOF
{
  "name": "$POLICY_NAME",
  "decision": "bypass",
  "precedence": $POLICY_PRECEDENCE,
  "include": [{"everyone": {}}]
}
EOF

echo
echo "Created CF Access bypass policy '$POLICY_NAME' (precedence=$POLICY_PRECEDENCE)."
