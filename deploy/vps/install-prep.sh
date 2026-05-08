#!/usr/bin/env bash
# PART 1 of VPS bootstrap. Run as root on VPS.
# Adds CF apt repo, installs cloudflared + ufw + fail2ban + jq,
# configures UFW, enables fail2ban SSH jail.
# Does NOT start cloudflared yet.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Run as root"; exit 1; }

echo "==> Adding Cloudflare apt repo..."
install -d -m 0755 /usr/share/keyrings

# Verified 2026-05-08 — re-verify with:
#   curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sha256sum
EXPECTED_GPG_SHA256="1bd95f4082b320d541bee351560fc2765aa9f9cd8efa4c9e32135e63f252721d"
TMP_GPG=$(mktemp)
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o "$TMP_GPG"
echo "$EXPECTED_GPG_SHA256  $TMP_GPG" | sha256sum --check --status \
    || { echo "ERROR: cloudflare GPG key checksum mismatch — possible MITM"; rm -f "$TMP_GPG"; exit 1; }
gpg --dearmor < "$TMP_GPG" | sudo tee /usr/share/keyrings/cloudflare-main.gpg > /dev/null
rm -f "$TMP_GPG"
chmod 0644 /usr/share/keyrings/cloudflare-main.gpg

CODENAME=$(lsb_release -cs)
cat > /etc/apt/sources.list.d/cloudflared.list <<EOF
deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $CODENAME main
EOF

echo "==> apt update..."
apt-get update

echo "==> Installing cloudflared + ufw + fail2ban + jq..."
DEBIAN_FRONTEND=noninteractive apt-get install -y cloudflared ufw fail2ban jq

echo "==> Configuring UFW..."
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 2222/tcp  comment 'SSH on non-default port'
ufw allow 51820/udp comment 'WireGuard'
ufw allow in on wg0 to any port 80 proto tcp comment 'nginx WG dev bypass'
ufw --force enable
ufw status verbose

echo "==> Configuring fail2ban..."
cat > /etc/fail2ban/jail.local <<'EOF'
[DEFAULT]
bantime  = 3600
findtime = 600
maxretry = 3
backend  = systemd

[sshd]
enabled  = true
port     = 2222
filter   = sshd
logpath  = %(sshd_log)s
maxretry = 3
EOF

systemctl enable --now fail2ban
sleep 2
fail2ban-client status sshd

echo "==> Ensuring cloudflared system user exists..."
if ! id cloudflared > /dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin cloudflared
fi

echo "==> install-prep complete."
echo "  Next: run deploy/vps/sshd-hardening.sh (interactively, with safety gate)."
echo "  Then run install-enable.sh AFTER the new stack is deployed."
