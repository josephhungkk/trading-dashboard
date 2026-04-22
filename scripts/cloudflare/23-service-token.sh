#!/usr/bin/env bash
# Create service token. Prints client-id + client-secret ONCE; CF does not
# let you re-retrieve the secret. Save both immediately.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
jq_require

TOKEN_NAME="dashboard-ci-smoke"

log "Looking for existing service token '$TOKEN_NAME'..."
existing=$(cf GET "/accounts/$CF_ACCOUNT_ID/access/service_tokens" \
    | jq -r --arg n "$TOKEN_NAME" '.result[]? | select(.name==$n) | .id' | head -1)

if [[ -n "$existing" && "$existing" != "null" ]]; then
    echo "Service token '$TOKEN_NAME' already exists (id=$existing)."
    echo "  NOTE: CF does NOT let you re-retrieve the client_secret."
    echo "  To rotate: delete this token in CF dashboard → Zero Trust → Access → Service Auth,"
    echo "  then re-run this script."
    echo "$existing" > "$STATE_DIR/service-token-id"
    exit 0
fi

log "Creating new service token..."
# CF no longer accepts "non-expiring"; omitting duration defaults to 8760h (1 year).
# TODO: annual rotation reminder — regenerate + update GH secrets before expires_at.
body=$(jq -n --arg n "$TOKEN_NAME" '{name:$n}')
resp=$(cf POST "/accounts/$CF_ACCOUNT_ID/access/service_tokens" --data "$body")
echo "$resp" | jq -e '.success' >/dev/null || die "Create failed: $(echo "$resp" | jq -c .)"

TOKEN_ID=$(echo "$resp" | jq -r '.result.id')
CLIENT_ID=$(echo "$resp" | jq -r '.result.client_id')
CLIENT_SECRET=$(echo "$resp" | jq -r '.result.client_secret')

ok "Service token created (id=$TOKEN_ID)"
echo
echo "============================================================"
echo "  SAVE THESE NOW — client_secret is not retrievable later:"
echo
echo "  CF_ACCESS_CLIENT_ID=$CLIENT_ID"
echo "  CF_ACCESS_CLIENT_SECRET=$CLIENT_SECRET"
echo "============================================================"
echo
echo "Recommended actions:"
echo "  1. gh secret set CF_ACCESS_CLIENT_ID     --body '$CLIENT_ID'"
echo "  2. gh secret set CF_ACCESS_CLIENT_SECRET --body '$CLIENT_SECRET'"
echo "  3. Append to your local ~/.bashrc for local dev:"
echo "     export CF_ACCESS_CLIENT_ID='$CLIENT_ID'"
echo "     export CF_ACCESS_CLIENT_SECRET='$CLIENT_SECRET'"
echo "  4. Re-run ./22-access-policy-bypass.sh to wire the token into the policy."
echo

echo "$TOKEN_ID" > "$STATE_DIR/service-token-id"
