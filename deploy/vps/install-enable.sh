#!/usr/bin/env bash
# PART 2 of VPS bootstrap. Run as root on VPS AFTER:
#   - install-prep.sh has run
#   - the new repo is deployed and `docker compose -f docker-compose.prod.yml up -d` is green
#   - the credentials JSON from the NUC has been SCP'd to /etc/cloudflared/<UUID>.json
#
# Args:
#   $1 = tunnel UUID (same as the credentials file name without .json)
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Run as root"; exit 1; }

TUNNEL_ID="${1:-}"
[[ -n "$TUNNEL_ID" ]] || { echo "Usage: $0 <tunnel-uuid>"; exit 1; }

CRED_PATH="/etc/cloudflared/$TUNNEL_ID.json"
# Accept NUC-side naming (10-tunnel-create.sh writes cloudflared-<UUID>.json) and rename on-the-fly.
if [[ ! -f "$CRED_PATH" && -f "/etc/cloudflared/cloudflared-$TUNNEL_ID.json" ]]; then
    mv "/etc/cloudflared/cloudflared-$TUNNEL_ID.json" "$CRED_PATH"
fi
[[ -f "$CRED_PATH" ]] || { echo "Credentials file missing: $CRED_PATH"; exit 1; }

chown root:root "$CRED_PATH"
chmod 0600 "$CRED_PATH"

REPO="/home/trader/trading-dashboard"
[[ -f "$REPO/deploy/vps/cloudflared.config.yml.template" ]] || {
    echo "$REPO/deploy/vps/cloudflared.config.yml.template missing — repo not deployed?"
    exit 1
}

echo "==> Writing /etc/cloudflared/config.yml..."
sed "s|__TUNNEL_ID__|$TUNNEL_ID|g" \
    "$REPO/deploy/vps/cloudflared.config.yml.template" \
    > /etc/cloudflared/config.yml
chmod 0644 /etc/cloudflared/config.yml

echo "==> Installing systemd unit..."
cp -f "$REPO/deploy/vps/cloudflared.service" /etc/systemd/system/cloudflared.service
systemctl daemon-reload

echo "==> Verifying backend reachable on loopback..."
if ! curl -sf -H "Host: dashboard.kiusinghung.com" http://127.0.0.1/health -o /dev/null; then
    echo "✗ http://127.0.0.1/health not responding. Start the compose stack first."
    exit 1
fi
echo "✓ Loopback health endpoint responds"

echo "==> Enabling + starting cloudflared.service..."
systemctl enable --now cloudflared
sleep 3
systemctl status cloudflared --no-pager | head -20

echo "==> cloudflared started. Tunnel should be live within ~30s."
echo "   Verify from NUC: curl -sf https://dashboard.kiusinghung.com/health \\"
echo "                    -H \"CF-Access-Client-Id: \$CF_ACCESS_CLIENT_ID\" \\"
echo "                    -H \"CF-Access-Client-Secret: \$CF_ACCESS_CLIENT_SECRET\""
