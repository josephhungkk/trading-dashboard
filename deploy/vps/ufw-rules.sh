#!/usr/bin/env bash
# Idempotent UFW re-apply (in case install-prep.sh was run once and rules drifted).
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root"; exit 1; }

ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 2222/tcp  comment 'SSH'
ufw allow 51820/udp comment 'WireGuard'
ufw allow in on wg0 to any port 80 proto tcp comment 'WG dev bypass to nginx'
ufw --force enable
ufw status verbose
