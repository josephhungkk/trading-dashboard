#!/usr/bin/env bash
# Enable CF security toggles: Always Use HTTPS, Min TLS, TLS 1.3, Security Level,
# Challenge TTL, HSTS, Bot Fight Mode, DNSSEC.
# Block AI Scrapers toggle is unreliable via API — user verifies in dashboard.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
jq_require

apply_setting() {
    local path="$1"; local body="$2"; local label="$3"
    resp=$(cf PATCH "/zones/$CF_ZONE_ID/settings/$path" --data "$body")
    if echo "$resp" | jq -e '.success' >/dev/null; then
        ok "$label"
    else
        echo "✗ $label failed:"
        echo "$resp" | jq -c .
    fi
}

log "Enabling security settings on zone $CF_ZONE_ID..."

apply_setting "always_use_https"    '{"value":"on"}'   "Always Use HTTPS: on"
apply_setting "min_tls_version"     '{"value":"1.2"}'  "Min TLS: 1.2"
apply_setting "tls_1_3"             '{"value":"on"}'   "TLS 1.3: on"
apply_setting "security_level"      '{"value":"high"}' "Security Level: high"
apply_setting "challenge_ttl"       '{"value":1800}'   "Challenge TTL: 30min"

apply_setting "security_header" \
    '{"value":{"strict_transport_security":{"enabled":true,"max_age":31536000,"include_subdomains":true,"preload":true,"nosniff":true}}}' \
    "HSTS enabled"

log "Enabling Bot Fight Mode..."
resp=$(cf PATCH "/zones/$CF_ZONE_ID/bot_management" --data '{"fight_mode":true}')
if echo "$resp" | jq -e '.success' >/dev/null; then
    ok "Bot Fight Mode: on"
else
    echo "  (may already be on, or requires paid plan — check dashboard)"
fi

log "Block AI Scrapers: verify in CF dashboard → Security → Bots → 'Block AI Scrapers and Crawlers' — flip to ON if not already."

log "Enabling DNSSEC..."
resp=$(cf PATCH "/zones/$CF_ZONE_ID/dnssec" --data '{"status":"active"}')
if echo "$resp" | jq -e '.success' >/dev/null; then
    DS=$(echo "$resp" | jq -r '.result | "\(.algorithm) \(.digest_type) \(.digest)"')
    ok "DNSSEC active. DS record (add at registrar if needed): $DS"
else
    echo "  DNSSEC op: $(echo "$resp" | jq -c .)"
fi

log "Done. Review in CF dashboard → Security → Settings."
