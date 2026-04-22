# deploy/vps — VPS bootstrap + ops

Run once per new VPS:

## Prep (before cutover)

As root on VPS:
```
cd /tmp && git clone https://github.com/josephhungkk/trading-dashboard.git repo-tmp
bash repo-tmp/deploy/vps/install-prep.sh
bash repo-tmp/deploy/vps/sshd-hardening.sh   # SAFETY GATE — see script comments
rm -rf /tmp/repo-tmp
```

## Enable (after new stack is up)

Transfer tunnel credentials from NUC:
```
# On NUC:
TUNNEL_ID=$(cat /mnt/c/dashboard/scripts/cloudflare/.state/tunnel-id)
scp -P 2222 ~/.secrets/cloudflared-$TUNNEL_ID.json trader@88.208.197.219:/tmp/
ssh -p 2222 trader@88.208.197.219
# On VPS:
sudo mv /tmp/cloudflared-$TUNNEL_ID.json /etc/cloudflared/$TUNNEL_ID.json
sudo bash /home/trader/trading-dashboard/deploy/vps/install-enable.sh $TUNNEL_ID
```

## Rollback

If something goes wrong post-cutover, see the spec's §8 rollback plan.
SSH is still on 2222 regardless of Tunnel state.

## Files

- `install-prep.sh`       — CF apt repo, install pkgs, UFW, fail2ban
- `install-enable.sh`     — configure + start cloudflared.service
- `sshd-hardening.sh`     — sshd_config hardening with sshd -t guard + backup
- `cloudflared.service`   — systemd unit (tight sandboxing)
- `cloudflared.config.yml.template` — ingress template (tunnel UUID substituted at install)
- `ufw-rules.sh`          — standalone UFW re-apply helper
