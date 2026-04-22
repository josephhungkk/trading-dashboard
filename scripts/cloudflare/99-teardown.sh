#!/usr/bin/env bash
# Tear down OLD dashboard CF resources BEFORE Phase 1 cutover.
# Deletes: Access apps with domain=dashboard.kiusinghung.com + DNS records for the same name.
# Does NOT delete: zone, Google IdP, service tokens, other subdomains.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
jq_require

DOMAIN="dashboard.kiusinghung.com"

log "Finding Access apps matching domain '$DOMAIN'..."
apps=$(cf GET "/accounts/$CF_ACCOUNT_ID/access/apps" \
    | jq -r --arg d "$DOMAIN" '.result[]? | select(.domain==$d) | .id')

if [[ -z "$apps" ]]; then
    ok "No Access app with domain '$DOMAIN' found"
else
    for app_id in $apps; do
        log "Deleting Access app id=$app_id..."
        resp=$(cf DELETE "/accounts/$CF_ACCOUNT_ID/access/apps/$app_id")
        echo "$resp" | jq -e '.success' >/dev/null && ok "Deleted $app_id" || echo "  failed: $(echo "$resp" | jq -c .)"
    done
fi

log "Finding DNS records for '$DOMAIN'..."
for type in A CNAME; do
    ids=$(cf GET "/zones/$CF_ZONE_ID/dns_records?type=$type&name=$DOMAIN" \
        | jq -r '.result[]?.id')
    for rec_id in $ids; do
        log "Deleting $type record id=$rec_id..."
        resp=$(cf DELETE "/zones/$CF_ZONE_ID/dns_records/$rec_id")
        echo "$resp" | jq -e '.success' >/dev/null && ok "Deleted $rec_id" || echo "  failed: $(echo "$resp" | jq -c .)"
    done
done

log "(Info) Any leftover tunnels that look legacy:"
cf GET "/accounts/$CF_ACCOUNT_ID/cfd_tunnel?is_deleted=false" \
    | jq -r '.result[]? | "\(.id)  \(.name)  \(.created_at)"' \
    | grep -iE '(dashboard|legacy|old)' || echo "  (none named suggestively)"
echo "  Review manually; delete via CF dashboard → Zero Trust → Networks → Tunnels if confirmed old."

log "Teardown complete."
