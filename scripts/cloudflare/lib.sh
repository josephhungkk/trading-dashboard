#!/usr/bin/env bash
# Shared CF API helpers. Source this at the top of every CF script.
#
# Required env vars (export before running):
#   CF_API_TOKEN     — token with Zone.Read, Zone.DNS.Edit, Zone.ZoneSettings.Edit,
#                      Account.CloudflareTunnel.Edit, Account.Access:{Apps,ServiceTokens}.Edit
#   CF_ZONE_ID       — zone ID of kiusinghung.com (CF dashboard sidebar)
#   CF_ACCOUNT_ID    — account ID (CF dashboard sidebar)
#
# State files (not committed): scripts/cloudflare/.state/*
set -euo pipefail

CF_API="${CF_API:-https://api.cloudflare.com/client/v4}"

: "${CF_API_TOKEN:?Set CF_API_TOKEN — see scripts/cloudflare/README.md}"
: "${CF_ZONE_ID:?Set CF_ZONE_ID — zone ID of kiusinghung.com}"
: "${CF_ACCOUNT_ID:?Set CF_ACCOUNT_ID — your CF account ID}"

SCRIPT_DIR="${SCRIPT_DIR:-$(cd "$(dirname "${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}")" && pwd)}"
STATE_DIR="$SCRIPT_DIR/.state"
mkdir -p "$STATE_DIR"

jq_require() {
    command -v jq >/dev/null || { echo "jq required; apt install -y jq"; exit 1; }
}

cf() {
    # Usage: cf METHOD PATH [--data '{...}' ...]
    local method="$1"; shift
    local path="$1"; shift
    curl -sSL -X "$method" \
        -H "Authorization: Bearer $CF_API_TOKEN" \
        -H "Content-Type: application/json" \
        "$@" \
        "$CF_API$path"
}

die() { echo "✗ $*" >&2; exit 1; }
ok()  { echo "✓ $*"; }
log() { echo "==> $*"; }
