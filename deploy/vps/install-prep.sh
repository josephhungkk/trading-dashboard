#!/usr/bin/env bash
# PART 1 of VPS bootstrap. Run as root on VPS.
# Adds CF apt repo, installs cloudflared + ufw + fail2ban + jq,
# configures UFW, enables fail2ban SSH jail.
# Does NOT start cloudflared yet.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Run as root"; exit 1; }

echo "==> Adding Cloudflare apt repo..."
install -d -m 0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
    | gpg --dearmor -o /usr/share/keyrings/cloudflare-main.gpg
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
ufw allow in on wg0 to any port 80 proto tcp comment 'WG dev bypass to nginx'
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

echo "==> install-prep complete."
echo "  Next: run deploy/vps/sshd-hardening.sh (interactively, with safety gate)."
echo "  Then run install-enable.sh AFTER the new stack is deployed."
