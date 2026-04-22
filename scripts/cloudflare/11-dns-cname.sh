#!/usr/bin/env bash
# Create (or update) CNAME dashboard.kiusinghung.com → <TUNNEL-UUID>.cfargotunnel.com
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
jq_require

NAME="dashboard.kiusinghung.com"
TUNNEL_ID=$(cat "$STATE_DIR/tunnel-id" 2>/dev/null || die "Run 10-tunnel-create.sh first")
TARGET="$TUNNEL_ID.cfargotunnel.com"

log "Ensuring CNAME $NAME → $TARGET (proxied)..."
existing=$(cf GET "/zones/$CF_ZONE_ID/dns_records?type=CNAME&name=$NAME" \
    | jq -r '.result[0]?.id')

body=$(jq -n --arg n "$NAME" --arg t "$TARGET" \
    '{type:"CNAME",name:$n,content:$t,proxied:true,ttl:1}')

if [[ -n "$existing" && "$existing" != "null" ]]; then
    log "Updating existing record (id=$existing)..."
    resp=$(cf PUT "/zones/$CF_ZONE_ID/dns_records/$existing" --data "$body")
else
    log "Creating new record..."
    resp=$(cf POST "/zones/$CF_ZONE_ID/dns_records" --data "$body")
fi

echo "$resp" | jq -e '.success' >/dev/null || die "DNS record op failed: $(echo "$resp" | jq -c .)"
ok "DNS record applied"
echo "$resp" | jq '.result | {id, name, content, proxied}'
