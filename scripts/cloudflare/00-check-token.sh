#!/usr/bin/env bash
# Verify CF_API_TOKEN has the needed scopes.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
jq_require

log "Verifying CF_API_TOKEN..."
resp=$(cf GET /user/tokens/verify)
if echo "$resp" | jq -e '.success' >/dev/null; then
    status=$(echo "$resp" | jq -r '.result.status')
    ok "Token is $status"
else
    die "Token invalid: $(echo "$resp" | jq -c .)"
fi

log "Listing zones visible to token..."
cf GET /zones | jq '.result[] | {id, name, status}'
echo
log "Confirm zone id $CF_ZONE_ID appears above. Account id $CF_ACCOUNT_ID:"
cf GET "/accounts/$CF_ACCOUNT_ID" | jq '.result | {id, name}' || die "Account not accessible by this token"
ok "All checks passed"
