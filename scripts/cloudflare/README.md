# Cloudflare automation scripts

Idempotent CF API driver scripts. Run in numeric order.

## Required env vars

    export CF_API_TOKEN=$(cat ~/.secrets/cloudflare.token)
    export CF_ZONE_ID=<zone-id-of-kiusinghung.com>
    export CF_ACCOUNT_ID=<your-account-id>

Find the IDs in the CF dashboard → zone overview → right sidebar.

## Required scopes on the API token

- Zone → Zone → Read
- Zone → DNS → Edit
- Zone → Zone Settings → Edit
- Account → Cloudflare Tunnel → Edit
- Account → Access: Apps and Policies → Edit
- Account → Access: Service Tokens → Edit

## Execution order (normal cutover)

    ./99-teardown.sh                 # delete OLD dashboard DNS + Access app
    ./00-check-token.sh              # verify scopes
    ./10-tunnel-create.sh            # create tunnel, save credentials JSON
    ./11-dns-cname.sh                # CNAME → tunnel.cfargotunnel.com
    ./20-access-app.sh               # Zero Trust Access app
    ./21-access-policy-google.sh     # Google IdP + email allowlist policy
    ./22-access-policy-bypass.sh     # service-token bypass policy (placeholder)
    ./23-service-token.sh            # generate service token (prints once)
    ./22-access-policy-bypass.sh     # re-run to wire service token into policy
    ./30-security-hardening.sh       # Bot Fight, Block AI, DNSSEC, Always-Use-HTTPS

## State files

`.state/` holds small files (tunnel ID, access app ID, etc.) so downstream
scripts can find what upstream scripts created. Gitignored.

## Local secrets

`~/.secrets/cloudflared-<TUNNEL-UUID>.json` — tunnel credentials (mode 0600).
Transferred to the VPS at `/etc/cloudflared/<UUID>.json` during Task 35.
