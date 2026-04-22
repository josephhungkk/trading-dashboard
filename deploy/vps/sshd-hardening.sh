#!/usr/bin/env bash
# Harden sshd_config. Run as root on VPS.
# Backs up original; edits in place; `sshd -t` guards the reload.
# SAFETY: user must open a second SSH session and confirm it works
# BEFORE closing the first.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Run as root"; exit 1; }

SSHD_CONFIG=/etc/ssh/sshd_config
BACKUP=/etc/ssh/sshd_config.pre-phase1-$(date +%Y%m%d-%H%M%S)

echo "==> Backing up $SSHD_CONFIG → $BACKUP"
cp -p "$SSHD_CONFIG" "$BACKUP"

echo "==> Applying hardening..."

harden_set() {
    local key="$1"; local value="$2"
    sed -i -E "s/^#?\s*${key}\s+.*/# &/" "$SSHD_CONFIG"
    echo "$key $value" >> "$SSHD_CONFIG"
}

harden_set Port 2222
harden_set PasswordAuthentication no
harden_set PubkeyAuthentication yes
harden_set PermitRootLogin no
harden_set AllowUsers trader
harden_set MaxAuthTries 3
harden_set ClientAliveInterval 60
harden_set ClientAliveCountMax 3
harden_set UsePAM yes
harden_set X11Forwarding no
harden_set PermitEmptyPasswords no

echo "==> Verifying config with sshd -t..."
if ! sshd -t; then
    echo "✗ sshd -t FAILED. Restoring backup."
    cp -p "$BACKUP" "$SSHD_CONFIG"
    exit 1
fi

echo "==> Config valid. Reloading sshd..."
systemctl reload sshd

echo
echo "============================================================"
echo "  SAFETY CHECK — DO NOT CLOSE THIS SESSION YET"
echo
echo "  Open a second SSH session from the NUC:"
echo "    ssh -p 2222 trader@88.208.197.219"
echo
echo "  Verify login works. THEN close the first session."
echo "  If second login fails, restore with:"
echo "    sudo cp $BACKUP $SSHD_CONFIG && sudo systemctl reload sshd"
echo "============================================================"
