# Phase 1 — VPS Cutover & Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tear down the live `Dashboard_old` deployment; redeploy the new rebuild at `https://dashboard.kiusinghung.com` as the sole production stack, fronted by Cloudflare Tunnel with CF Access Google-login gate + service-token bypass, hardened across every layer, verified by Playwright smoke check.

**Architecture:** CF Tunnel (outbound-initiated, no public ports) → `cloudflared` on VPS → dual-bound nginx (`127.0.0.1:80` for tunnel, `10.10.0.1:80` for WG dev) → backend/frontend/redis containers. Postgres stays native on NUC; `trading` DB dropped; `dashboard` DB is sole survivor. IONOS firewall + UFW + fail2ban + nginx security headers + CF WAF + non-root containers + pinned image digests + gitleaks secret-scan pre-commit.

**Tech Stack:** Cloudflare API (curl + optional CF MCP), `cloudflared`, Ubuntu 24.04 VPS, systemd, UFW, fail2ban, Docker Compose, nginx 1.27-alpine, Python 3.14/uv, Node 24/pnpm, Playwright 4, gitleaks.

**Authoritative spec:** `/mnt/c/dashboard/docs/superpowers/specs/2026-04-21-phase1-vps-cutover-design.md`. Re-read for any ambiguity.

**Environment constraints:**
- Dev host: NUC15PRO in WSL2. `/mnt/c/dashboard` === `C:\dashboard`.
- Prod host: IONOS VPS `88.208.197.219` / `10.10.0.1` via WireGuard.
- NUC PG18 native; `dashboard` DB exists; `trading` DB must be dropped during cutover.
- Docker: docker-ce inside WSL on NUC (not Docker Desktop).
- pnpm at `~/.npm-global/bin/pnpm` — export PATH before every pnpm call.
- Git identity already configured repo-local (`Joseph Hung` / `josephhungkk.uk@gmail.com`).

**Pre-flight check before Chunk A:**
1. `docker version` — confirm Docker works in WSL.
2. `ssh -p 2222 trader@88.208.197.219 'echo hi'` — SSH still reaches VPS.
3. `psql -h localhost -U trader -d dashboard -c 'SELECT 1'` from NUC — PG reachable.
4. `gh auth status` — GitHub CLI authenticated as `josephhungkk`.
5. User ready to create Cloudflare API token (takes 60 sec in CF dashboard; see Task 4 steps).
6. User aware that IONOS firewall change at end of cutover is manual (Task 37).

---

## File structure

```
/mnt/c/dashboard/
├── .github/
│   └── workflows/
│       ├── ci.yml               MODIFY — add pnpm audit + uv pip audit
│       └── deploy.yml           NEW — rsync + compose up + smoke on push-to-main
├── .pre-commit-config.yaml      MODIFY — add gitleaks hook
├── CLAUDE.md                    MODIFY — post-cutover reality
├── CHANGELOG.md                 MODIFY — [0.1.0] entry
├── TASKS.md                     MODIFY — mark Phase 1 complete
├── deploy/
│   └── vps/
│       ├── install-prep.sh      NEW — add CF apt repo, install pkgs, UFW, fail2ban
│       ├── install-enable.sh    NEW — configure + start cloudflared
│       ├── cloudflared.service  NEW — systemd unit
│       ├── cloudflared.config.yml.template  NEW — ingress template
│       ├── ufw-rules.sh         NEW — idempotent firewall rules
│       ├── sshd-hardening.sh    NEW — sshd_config edits + sshd -t guard
│       └── README.md            NEW — VPS ops overview
├── docker-compose.prod.yml      NEW — production variant
├── nginx/
│   ├── nginx.conf               REPLACE — ported from Dashboard_old, certbot stripped
│   ├── conf.d/
│   │   └── dashboard.conf       REPLACE — server block
│   └── start.sh                 REPLACE — simpler (no cert-reload watcher)
├── scripts/
│   ├── deploy.sh                REPLACE Phase 0 stub with real rsync+ssh+compose
│   └── cloudflare/
│       ├── lib.sh               NEW — shared helpers (cf wrapper, state dir)
│       ├── 00-check-token.sh    NEW — verify CF_API_TOKEN scopes
│       ├── 10-tunnel-create.sh  NEW — create tunnel, save credentials
│       ├── 11-dns-cname.sh      NEW — create CNAME to tunnel
│       ├── 20-access-app.sh     NEW — create Zero Trust Access app
│       ├── 21-access-policy-google.sh  NEW — Google IdP + email allowlist
│       ├── 22-access-policy-bypass.sh  NEW — service-token bypass policy
│       ├── 23-service-token.sh  NEW — generate service token
│       ├── 30-security-hardening.sh    NEW — Bot Fight + Block AI + DNSSEC + AUH
│       ├── 40-smoke-from-ci.sh  NEW — curl smoke helper for CI
│       ├── 99-teardown.sh       NEW — delete old dashboard DNS + Access app
│       └── README.md            NEW — order + required env vars + scopes
├── tests/
│   └── e2e/
│       ├── package.json         NEW — playwright + node types
│       ├── playwright.config.ts NEW
│       └── smoke.spec.ts        NEW — hit /health via service token; Lighthouse
└── backend/
    └── Dockerfile               MINOR TWEAK — prod CMD never uses --reload (already OK)

And on the VPS (created during cutover, not in the repo):
/etc/systemd/system/cloudflared.service
/etc/cloudflared/config.yml
/etc/cloudflared/<TUNNEL-UUID>.json     (mode 0600, root:root)
/etc/ufw/                                (UFW rules applied)
/etc/fail2ban/jail.local                 (SSH jail)
/home/trader/trading-dashboard/          (fresh rsync)
/home/trader/trading-dashboard/.env      (production env, mode 0600)
```

---

## Prerequisites the user must provide

Before Task 31, the user must create a Cloudflare API token in the CF dashboard with these scopes:

- **Zone → Zone → Read** (for `kiusinghung.com`)
- **Zone → DNS → Edit** (for `kiusinghung.com`)
- **Zone → Zone Settings → Edit** (for security hardening)
- **Account → Cloudflare Tunnel → Edit**
- **Account → Access: Apps and Policies → Edit**
- **Account → Access: Service Tokens → Edit**

Click path: dash.cloudflare.com → My Profile → API Tokens → Create Token → "Custom token" template → set the 6 permissions above → scope to zone `kiusinghung.com` + the appropriate Account → Continue → Summary → Create. Copy the token once (it won't be shown again).

Then on the NUC:
```bash
mkdir -p ~/.secrets && chmod 0700 ~/.secrets
echo '<paste-token-here>' > ~/.secrets/cloudflare.token
chmod 0600 ~/.secrets/cloudflare.token
export CF_API_TOKEN=$(cat ~/.secrets/cloudflare.token)
```

Also find these once via CF dashboard (right sidebar of the zone overview page):
- `CF_ZONE_ID` (Zone ID of `kiusinghung.com`)
- `CF_ACCOUNT_ID` (Account ID)

Add to `~/.bashrc` (or export per session):
```bash
export CF_ZONE_ID=<zone-id>
export CF_ACCOUNT_ID=<account-id>
export CF_API_TOKEN=$(cat ~/.secrets/cloudflare.token)
```

---

## Chunk A: Docs baseline + tooling prep (Tasks 1–3)

Low-risk setup. Lands the groundwork + safety nets without touching live systems.

### Task 1: Mark Phase 1 in progress

**Files:**
- Modify: `/mnt/c/dashboard/TASKS.md`
- Modify: `/mnt/c/dashboard/CHANGELOG.md`

- [ ] **Step 1.1: Update TASKS.md header**

Read `/mnt/c/dashboard/TASKS.md`. Replace the line `## Phase 1 — VPS infra skeleton  *(next)*` with the expanded checklist:

```md
## Phase 1 — VPS cutover & security hardening  *(in progress)*
- [ ] Cloudflare automation scripts (scripts/cloudflare/*.sh, 10+1 helpers)
- [ ] VPS install scripts (deploy/vps/install-prep.sh + install-enable.sh + friends)
- [ ] nginx config ported from Dashboard_old, certbot stripped
- [ ] docker-compose.prod.yml with dual-bound nginx + tmpfs + pinned digests
- [ ] Playwright smoke test (tests/e2e/smoke.spec.ts)
- [ ] GitHub Actions deploy.yml + CI audit steps
- [ ] gitleaks pre-commit hook
- [ ] Real scripts/deploy.sh (replace Phase 0 stub)
- [ ] Cutover executed: old stack down, trading DB dropped, new stack live
- [ ] IONOS firewall reduced to 2 ports, direct-IP bypass confirmed closed
- [ ] Playwright smoke test passes via CF Access service token
- [ ] v0.1.0 tagged and pushed
```

- [ ] **Step 1.2: Update CHANGELOG.md with Unreleased scope**

Read `/mnt/c/dashboard/CHANGELOG.md`. Under `## [Unreleased]` add:

```md
### Planned for [0.1.0]
- Full VPS cutover: Dashboard_old torn down, new rebuild deployed at dashboard.kiusinghung.com.
- Cloudflare Tunnel replacing public 80/443 ports + certbot.
- CF Access Google-login gate with 2-email allowlist + service-token CI bypass.
- Multi-layer hardening: IONOS firewall + UFW + fail2ban + nginx security headers + CF WAF + gitleaks.
- `trading` DB dropped; `dashboard` DB is sole survivor.
```

- [ ] **Step 1.3: Commit**

```bash
cd /mnt/c/dashboard
git add TASKS.md CHANGELOG.md
git commit -m "docs: mark phase 1 in progress"
```

### Task 2: gitleaks pre-commit hook

**Files:**
- Modify: `/mnt/c/dashboard/.pre-commit-config.yaml`

- [ ] **Step 2.1: Append gitleaks hook**

Read `.pre-commit-config.yaml`. Add (before the `commitlint` hook):

```yaml
  - repo: https://github.com/gitleaks/gitleaks
    # rev pinned by `pre-commit autoupdate` at impl time
    hooks:
      - id: gitleaks
```

- [ ] **Step 2.2: Pin the rev**

```bash
cd /mnt/c/dashboard
pre-commit autoupdate
```
Expected: `.pre-commit-config.yaml` gets a real rev for `gitleaks/gitleaks`.

- [ ] **Step 2.3: Verify gitleaks catches a test secret**

Create a throwaway test file (DO NOT commit):
```bash
cd /mnt/c/dashboard
echo 'AWS_SECRET_ACCESS_KEY="AKIAIOSFODNN7EXAMPLE"' > test_secret.txt
git add test_secret.txt
git commit -m "chore: test gitleaks" && echo "FAIL — gitleaks did not catch test secret" || echo "PASS — gitleaks rejected"
git reset HEAD test_secret.txt
rm test_secret.txt
```
Expected: `PASS — gitleaks rejected`.

- [ ] **Step 2.4: Run all pre-commit hooks**

```bash
cd /mnt/c/dashboard
pre-commit run --all-files
```
Expected: all hooks pass (including the newly added gitleaks) on existing tracked files.

- [ ] **Step 2.5: Commit**

```bash
cd /mnt/c/dashboard
git add .pre-commit-config.yaml
git commit -m "chore: add gitleaks pre-commit hook for secret scanning"
```

### Task 3: CI audit steps

**Files:**
- Modify: `/mnt/c/dashboard/.github/workflows/ci.yml`
- Modify: `/mnt/c/dashboard/backend/pyproject.toml`

- [ ] **Step 3.1: Add `pnpm audit` step to frontend job**

Read `.github/workflows/ci.yml`. In the `frontend` job, insert after the `Install frontend deps` step:

```yaml
      - name: pnpm audit
        working-directory: frontend
        run: pnpm audit --audit-level=high
```

- [ ] **Step 3.2: Add pip-audit dev dep + step to backend job**

```bash
cd /mnt/c/dashboard/backend
uv add --dev pip-audit
```

Then in `.github/workflows/ci.yml` `backend` job, insert after `Install backend deps`:

```yaml
      - name: pip-audit
        working-directory: backend
        run: uv run pip-audit --strict
```

- [ ] **Step 3.3: Sanity-run locally**

```bash
cd /mnt/c/dashboard/frontend
export PATH="$HOME/.npm-global/bin:$PATH"
pnpm audit --audit-level=high
```
Expected: exit 0 OR documented advisories. If high/critical found, evaluate — patch bump or acknowledge.

```bash
cd /mnt/c/dashboard/backend
uv run pip-audit --strict
```
Expected: exit 0 OR documented advisories.

- [ ] **Step 3.4: Commit**

```bash
cd /mnt/c/dashboard
git add .github/workflows/ci.yml backend/pyproject.toml backend/uv.lock
git commit -m "ci: add pnpm audit and pip-audit fail-on-high steps"
```

---

## Chunk B: Cloudflare automation scripts (Tasks 4–13)

Idempotent scripts. Can be written + committed without Cloudflare actually being configured. Executed during the cutover (Chunk H).

### Task 4: scripts/cloudflare/lib.sh + README.md (shared infra)

**Files:**
- Create: `/mnt/c/dashboard/scripts/cloudflare/lib.sh`
- Create: `/mnt/c/dashboard/scripts/cloudflare/README.md`
- Create: `/mnt/c/dashboard/scripts/cloudflare/.state/.gitkeep`
- Modify: `/mnt/c/dashboard/.gitignore`

- [ ] **Step 4.1: Create directory**

```bash
mkdir -p /mnt/c/dashboard/scripts/cloudflare/.state
touch /mnt/c/dashboard/scripts/cloudflare/.state/.gitkeep
```

- [ ] **Step 4.2: Write `lib.sh`**

Write `/mnt/c/dashboard/scripts/cloudflare/lib.sh`:
```bash
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
```

- [ ] **Step 4.3: Write `README.md`**

Write `/mnt/c/dashboard/scripts/cloudflare/README.md`:
```md
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
```

- [ ] **Step 4.4: Update `.gitignore`**

Read `.gitignore`. Insert after `.claude/`:
```
scripts/cloudflare/.state/
!scripts/cloudflare/.state/.gitkeep
```

- [ ] **Step 4.5: Commit**

```bash
cd /mnt/c/dashboard
chmod +x scripts/cloudflare/lib.sh
git add scripts/cloudflare/lib.sh scripts/cloudflare/README.md scripts/cloudflare/.state/.gitkeep .gitignore
git commit -m "feat(cf): shared lib.sh + README + state dir for CF scripts"
```

### Task 5: 00-check-token.sh

**Files:**
- Create: `/mnt/c/dashboard/scripts/cloudflare/00-check-token.sh`

- [ ] **Step 5.1: Write script**

Write `/mnt/c/dashboard/scripts/cloudflare/00-check-token.sh`:
```bash
#!/usr/bin/env bash
# Verify CF_API_TOKEN has the needed scopes.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
jq_require

log "Verifying CF_API_TOKEN..."
resp=$(cf GET /user/tokens/verify)
if echo "$resp" | jq -e '.success' >/dev/null; then
    status=$(echo "$resp" | jq -r '.result.status')
    ok "Token is $status"
else
    die "Token invalid: $(echo "$resp" | jq -c .)"
fi

log "Listing zones visible to token..."
cf GET /zones | jq '.result[] | {id, name, status}'
echo
log "Confirm zone id $CF_ZONE_ID appears above. Account id $CF_ACCOUNT_ID:"
cf GET "/accounts/$CF_ACCOUNT_ID" | jq '.result | {id, name}' || die "Account not accessible by this token"
ok "All checks passed"
```

- [ ] **Step 5.2: chmod + syntax check + commit**

```bash
cd /mnt/c/dashboard
chmod +x scripts/cloudflare/00-check-token.sh
bash -n scripts/cloudflare/00-check-token.sh
git add scripts/cloudflare/00-check-token.sh
git commit -m "feat(cf): 00-check-token script"
```

### Task 6: 10-tunnel-create.sh

**Files:**
- Create: `/mnt/c/dashboard/scripts/cloudflare/10-tunnel-create.sh`

- [ ] **Step 6.1: Write script**

Write `/mnt/c/dashboard/scripts/cloudflare/10-tunnel-create.sh`:
```bash
#!/usr/bin/env bash
# Create (or fetch existing) CF Tunnel named "dashboard-prod".
# Writes credentials JSON to ~/.secrets/cloudflared-<UUID>.json (mode 0600).
# Idempotent: safe to re-run.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
jq_require

TUNNEL_NAME="${TUNNEL_NAME:-dashboard-prod}"
SECRETS_DIR="${SECRETS_DIR:-$HOME/.secrets}"
mkdir -p "$SECRETS_DIR"
chmod 0700 "$SECRETS_DIR"

log "Looking for existing tunnel named '$TUNNEL_NAME'..."
existing=$(cf GET "/accounts/$CF_ACCOUNT_ID/cfd_tunnel?name=$TUNNEL_NAME&is_deleted=false" \
    | jq -r --arg n "$TUNNEL_NAME" '.result[]? | select(.name==$n) | .id' | head -1)

if [[ -n "$existing" && "$existing" != "null" ]]; then
    ok "Tunnel '$TUNNEL_NAME' already exists (id=$existing)"
    TUNNEL_ID="$existing"
    CRED_FILE="$SECRETS_DIR/cloudflared-$TUNNEL_ID.json"
    if [[ ! -f "$CRED_FILE" ]]; then
        echo "  WARN: credentials file $CRED_FILE is missing."
        echo "  Run 'cloudflared tunnel token $TUNNEL_ID' on a machine with cloudflared"
        echo "  installed to regenerate, or delete tunnel via dashboard and re-run."
    fi
else
    log "Creating tunnel '$TUNNEL_NAME'..."
    SECRET=$(openssl rand -base64 32)
    resp=$(cf POST "/accounts/$CF_ACCOUNT_ID/cfd_tunnel" \
        --data "$(jq -n --arg n "$TUNNEL_NAME" --arg s "$SECRET" \
                 '{name:$n,tunnel_secret:$s,config_src:"cloudflare"}')")
    echo "$resp" | jq -e '.success' >/dev/null || die "Create failed: $(echo "$resp" | jq -c .)"
    TUNNEL_ID=$(echo "$resp" | jq -r '.result.id')
    ok "Tunnel created (id=$TUNNEL_ID)"

    CRED_FILE="$SECRETS_DIR/cloudflared-$TUNNEL_ID.json"
    jq -n --arg aid "$CF_ACCOUNT_ID" --arg tid "$TUNNEL_ID" --arg s "$SECRET" \
        '{AccountTag:$aid,TunnelID:$tid,TunnelName:"dashboard-prod",TunnelSecret:$s}' \
        > "$CRED_FILE"
    chmod 0600 "$CRED_FILE"
    ok "Credentials saved to $CRED_FILE (mode 0600)"
fi

echo "$TUNNEL_ID" > "$STATE_DIR/tunnel-id"
ok "Tunnel id written to $STATE_DIR/tunnel-id"
```

- [ ] **Step 6.2: chmod + syntax check + commit**

```bash
cd /mnt/c/dashboard
chmod +x scripts/cloudflare/10-tunnel-create.sh
bash -n scripts/cloudflare/10-tunnel-create.sh
git add scripts/cloudflare/10-tunnel-create.sh
git commit -m "feat(cf): 10-tunnel-create script"
```

### Task 7: 11-dns-cname.sh

**Files:**
- Create: `/mnt/c/dashboard/scripts/cloudflare/11-dns-cname.sh`

- [ ] **Step 7.1: Write script**

Write `/mnt/c/dashboard/scripts/cloudflare/11-dns-cname.sh`:
```bash
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
```

- [ ] **Step 7.2: chmod + syntax check + commit**

```bash
cd /mnt/c/dashboard
chmod +x scripts/cloudflare/11-dns-cname.sh
bash -n scripts/cloudflare/11-dns-cname.sh
git add scripts/cloudflare/11-dns-cname.sh
git commit -m "feat(cf): 11-dns-cname script"
```

### Task 8: 20-access-app.sh

**Files:**
- Create: `/mnt/c/dashboard/scripts/cloudflare/20-access-app.sh`

- [ ] **Step 8.1: Write script**

Write `/mnt/c/dashboard/scripts/cloudflare/20-access-app.sh`:
```bash
#!/usr/bin/env bash
# Create (or fetch) Zero Trust Access application for dashboard.kiusinghung.com.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
jq_require

DOMAIN="dashboard.kiusinghung.com"
APP_NAME="Dashboard"

log "Looking for existing Access app named '$APP_NAME'..."
existing=$(cf GET "/accounts/$CF_ACCOUNT_ID/access/apps" \
    | jq -r --arg n "$APP_NAME" '.result[]? | select(.name==$n) | .id' | head -1)

if [[ -n "$existing" && "$existing" != "null" ]]; then
    ok "Access app '$APP_NAME' exists (id=$existing)"
    APP_ID="$existing"
else
    log "Creating Access app..."
    body=$(jq -n --arg n "$APP_NAME" --arg d "$DOMAIN" \
        '{name:$n,
          domain:$d,
          type:"self_hosted",
          session_duration:"24h",
          auto_redirect_to_identity:false,
          app_launcher_visible:false,
          cors_headers:{allow_all_methods:false,allow_all_origins:false}}')
    resp=$(cf POST "/accounts/$CF_ACCOUNT_ID/access/apps" --data "$body")
    echo "$resp" | jq -e '.success' >/dev/null || die "Create failed: $(echo "$resp" | jq -c .)"
    APP_ID=$(echo "$resp" | jq -r '.result.id')
    ok "Access app created (id=$APP_ID)"
fi

echo "$APP_ID" > "$STATE_DIR/access-app-id"
ok "App id written to $STATE_DIR/access-app-id"
```

- [ ] **Step 8.2: chmod + syntax check + commit**

```bash
cd /mnt/c/dashboard
chmod +x scripts/cloudflare/20-access-app.sh
bash -n scripts/cloudflare/20-access-app.sh
git add scripts/cloudflare/20-access-app.sh
git commit -m "feat(cf): 20-access-app script"
```

### Task 9: 21-access-policy-google.sh

**Files:**
- Create: `/mnt/c/dashboard/scripts/cloudflare/21-access-policy-google.sh`

- [ ] **Step 9.1: Write script**

Write `/mnt/c/dashboard/scripts/cloudflare/21-access-policy-google.sh`:
```bash
#!/usr/bin/env bash
# Allow policy: Google login + email in {josephhungkk@gmail.com, ispyling@gmail.com}.
# Requires Google IdP configured in CF Zero Trust dashboard first.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
jq_require

APP_ID=$(cat "$STATE_DIR/access-app-id" 2>/dev/null || die "Run 20-access-app.sh first")
POLICY_NAME="allow-google-emails"
EMAILS='["josephhungkk@gmail.com","ispyling@gmail.com"]'

log "Finding Google IdP..."
GOOGLE_IDP_ID=$(cf GET "/accounts/$CF_ACCOUNT_ID/access/identity_providers" \
    | jq -r '.result[]? | select(.type=="google") | .id' | head -1)

if [[ -z "$GOOGLE_IDP_ID" || "$GOOGLE_IDP_ID" == "null" ]]; then
    die "Google IdP not configured. Add it via CF dashboard → Zero Trust → Settings → Authentication → Login methods → Add new → Google (paste OAuth client id+secret from Google Cloud Console)."
fi
ok "Google IdP id=$GOOGLE_IDP_ID"

log "Looking for existing policy '$POLICY_NAME'..."
existing=$(cf GET "/accounts/$CF_ACCOUNT_ID/access/apps/$APP_ID/policies" \
    | jq -r --arg n "$POLICY_NAME" '.result[]? | select(.name==$n) | .id' | head -1)

body=$(jq -n \
    --arg n "$POLICY_NAME" \
    --arg idp "$GOOGLE_IDP_ID" \
    --argjson emails "$EMAILS" \
    '{name:$n,
      decision:"allow",
      include:[{email:{email:$emails[0]}},{email:{email:$emails[1]}}],
      require:[{login_method:{id:$idp}}],
      precedence:1,
      session_duration:"24h"}')

if [[ -n "$existing" && "$existing" != "null" ]]; then
    log "Updating existing policy..."
    resp=$(cf PUT "/accounts/$CF_ACCOUNT_ID/access/apps/$APP_ID/policies/$existing" --data "$body")
else
    log "Creating new policy..."
    resp=$(cf POST "/accounts/$CF_ACCOUNT_ID/access/apps/$APP_ID/policies" --data "$body")
fi

echo "$resp" | jq -e '.success' >/dev/null || die "Policy op failed: $(echo "$resp" | jq -c .)"
ok "Policy '$POLICY_NAME' applied"
```

- [ ] **Step 9.2: chmod + syntax check + commit**

```bash
cd /mnt/c/dashboard
chmod +x scripts/cloudflare/21-access-policy-google.sh
bash -n scripts/cloudflare/21-access-policy-google.sh
git add scripts/cloudflare/21-access-policy-google.sh
git commit -m "feat(cf): 21-access-policy-google script"
```

### Task 10: 22-access-policy-bypass.sh + 23-service-token.sh

**Files:**
- Create: `/mnt/c/dashboard/scripts/cloudflare/22-access-policy-bypass.sh`
- Create: `/mnt/c/dashboard/scripts/cloudflare/23-service-token.sh`

- [ ] **Step 10.1: Write 22-access-policy-bypass.sh**

Write `/mnt/c/dashboard/scripts/cloudflare/22-access-policy-bypass.sh`:
```bash
#!/usr/bin/env bash
# Bypass policy for CF Access service tokens (CI smoke tests).
# Run ONCE to create placeholder. Run AGAIN after 23-service-token.sh to attach token.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
jq_require

APP_ID=$(cat "$STATE_DIR/access-app-id" 2>/dev/null || die "Run 20-access-app.sh first")
POLICY_NAME="bypass-service-token"
SVC_TOKEN_ID=$(cat "$STATE_DIR/service-token-id" 2>/dev/null || echo "")

log "Looking for existing bypass policy..."
existing=$(cf GET "/accounts/$CF_ACCOUNT_ID/access/apps/$APP_ID/policies" \
    | jq -r --arg n "$POLICY_NAME" '.result[]? | select(.name==$n) | .id' | head -1)

if [[ -z "$SVC_TOKEN_ID" ]]; then
    log "No service token id yet — creating placeholder policy."
    body=$(jq -n --arg n "$POLICY_NAME" \
        '{name:$n, decision:"bypass", include:[{everyone:{}}], precedence:2}')
    # Placeholder 'include' uses everyone: {} because CF requires at least one include.
    # This does NOT actually bypass; the real policy below attaches the token id.
    # But using {everyone:{}} here would let everyone through — use a narrower
    # placeholder: include an empty email array that matches nothing.
    # Actually safer: include a non-matching synthetic email.
    body=$(jq -n --arg n "$POLICY_NAME" \
        '{name:$n, decision:"bypass",
          include:[{email:{email:"placeholder-noreply@kiusinghung.com"}}],
          precedence:2}')
else
    log "Attaching service token $SVC_TOKEN_ID to bypass policy..."
    body=$(jq -n --arg n "$POLICY_NAME" --arg t "$SVC_TOKEN_ID" \
        '{name:$n,
          decision:"bypass",
          include:[{service_token:{token_id:$t}}],
          precedence:2}')
fi

if [[ -n "$existing" && "$existing" != "null" ]]; then
    resp=$(cf PUT "/accounts/$CF_ACCOUNT_ID/access/apps/$APP_ID/policies/$existing" --data "$body")
else
    resp=$(cf POST "/accounts/$CF_ACCOUNT_ID/access/apps/$APP_ID/policies" --data "$body")
fi

echo "$resp" | jq -e '.success' >/dev/null || die "Policy op failed: $(echo "$resp" | jq -c .)"
ok "Bypass policy applied"
if [[ -z "$SVC_TOKEN_ID" ]]; then
    echo "  Placeholder policy created. Next: run ./23-service-token.sh, then re-run THIS script to wire the token."
fi
```

- [ ] **Step 10.2: Write 23-service-token.sh**

Write `/mnt/c/dashboard/scripts/cloudflare/23-service-token.sh`:
```bash
#!/usr/bin/env bash
# Create service token. Prints client-id + client-secret ONCE; CF does not
# let you re-retrieve the secret. Save both immediately.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
jq_require

TOKEN_NAME="dashboard-ci-smoke"

log "Looking for existing service token '$TOKEN_NAME'..."
existing=$(cf GET "/accounts/$CF_ACCOUNT_ID/access/service_tokens" \
    | jq -r --arg n "$TOKEN_NAME" '.result[]? | select(.name==$n) | .id' | head -1)

if [[ -n "$existing" && "$existing" != "null" ]]; then
    echo "Service token '$TOKEN_NAME' already exists (id=$existing)."
    echo "  NOTE: CF does NOT let you re-retrieve the client_secret."
    echo "  To rotate: delete this token in CF dashboard → Zero Trust → Access → Service Auth,"
    echo "  then re-run this script."
    echo "$existing" > "$STATE_DIR/service-token-id"
    exit 0
fi

log "Creating new service token..."
body=$(jq -n --arg n "$TOKEN_NAME" '{name:$n,duration:"non-expiring"}')
resp=$(cf POST "/accounts/$CF_ACCOUNT_ID/access/service_tokens" --data "$body")
echo "$resp" | jq -e '.success' >/dev/null || die "Create failed: $(echo "$resp" | jq -c .)"

TOKEN_ID=$(echo "$resp" | jq -r '.result.id')
CLIENT_ID=$(echo "$resp" | jq -r '.result.client_id')
CLIENT_SECRET=$(echo "$resp" | jq -r '.result.client_secret')

ok "Service token created (id=$TOKEN_ID)"
echo
echo "============================================================"
echo "  SAVE THESE NOW — client_secret is not retrievable later:"
echo
echo "  CF_ACCESS_CLIENT_ID=$CLIENT_ID"
echo "  CF_ACCESS_CLIENT_SECRET=$CLIENT_SECRET"
echo "============================================================"
echo
echo "Recommended actions:"
echo "  1. gh secret set CF_ACCESS_CLIENT_ID     --body '$CLIENT_ID'"
echo "  2. gh secret set CF_ACCESS_CLIENT_SECRET --body '$CLIENT_SECRET'"
echo "  3. Append to your local ~/.bashrc for local dev:"
echo "     export CF_ACCESS_CLIENT_ID='$CLIENT_ID'"
echo "     export CF_ACCESS_CLIENT_SECRET='$CLIENT_SECRET'"
echo "  4. Re-run ./22-access-policy-bypass.sh to wire the token into the policy."
echo

echo "$TOKEN_ID" > "$STATE_DIR/service-token-id"
```

- [ ] **Step 10.3: chmod + syntax check + commit**

```bash
cd /mnt/c/dashboard
chmod +x scripts/cloudflare/22-access-policy-bypass.sh scripts/cloudflare/23-service-token.sh
bash -n scripts/cloudflare/22-access-policy-bypass.sh
bash -n scripts/cloudflare/23-service-token.sh
git add scripts/cloudflare/22-access-policy-bypass.sh scripts/cloudflare/23-service-token.sh
git commit -m "feat(cf): 22-access-policy-bypass and 23-service-token scripts"
```

### Task 11: 30-security-hardening.sh

**Files:**
- Create: `/mnt/c/dashboard/scripts/cloudflare/30-security-hardening.sh`

- [ ] **Step 11.1: Write script**

Write `/mnt/c/dashboard/scripts/cloudflare/30-security-hardening.sh`:
```bash
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
```

- [ ] **Step 11.2: chmod + syntax check + commit**

```bash
cd /mnt/c/dashboard
chmod +x scripts/cloudflare/30-security-hardening.sh
bash -n scripts/cloudflare/30-security-hardening.sh
git add scripts/cloudflare/30-security-hardening.sh
git commit -m "feat(cf): 30-security-hardening script"
```

### Task 12: 99-teardown.sh

**Files:**
- Create: `/mnt/c/dashboard/scripts/cloudflare/99-teardown.sh`

- [ ] **Step 12.1: Write script**

Write `/mnt/c/dashboard/scripts/cloudflare/99-teardown.sh`:
```bash
#!/usr/bin/env bash
# Tear down OLD dashboard CF resources BEFORE Phase 1 cutover.
# Deletes: Access apps with domain=dashboard.kiusinghung.com + DNS records for the same name.
# Does NOT delete: zone, Google IdP, service tokens, other subdomains.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
jq_require

DOMAIN="dashboard.kiusinghung.com"

log "Finding Access apps matching domain '$DOMAIN'..."
apps=$(cf GET "/accounts/$CF_ACCOUNT_ID/access/apps" \
    | jq -r --arg d "$DOMAIN" '.result[]? | select(.domain==$d) | .id')

if [[ -z "$apps" ]]; then
    ok "No Access app with domain '$DOMAIN' found"
else
    for app_id in $apps; do
        log "Deleting Access app id=$app_id..."
        resp=$(cf DELETE "/accounts/$CF_ACCOUNT_ID/access/apps/$app_id")
        echo "$resp" | jq -e '.success' >/dev/null && ok "Deleted $app_id" || echo "  failed: $(echo "$resp" | jq -c .)"
    done
fi

log "Finding DNS records for '$DOMAIN'..."
for type in A CNAME; do
    ids=$(cf GET "/zones/$CF_ZONE_ID/dns_records?type=$type&name=$DOMAIN" \
        | jq -r '.result[]?.id')
    for rec_id in $ids; do
        log "Deleting $type record id=$rec_id..."
        resp=$(cf DELETE "/zones/$CF_ZONE_ID/dns_records/$rec_id")
        echo "$resp" | jq -e '.success' >/dev/null && ok "Deleted $rec_id" || echo "  failed: $(echo "$resp" | jq -c .)"
    done
done

log "(Info) Any leftover tunnels that look legacy:"
cf GET "/accounts/$CF_ACCOUNT_ID/cfd_tunnel?is_deleted=false" \
    | jq -r '.result[]? | "\(.id)  \(.name)  \(.created_at)"' \
    | grep -iE '(dashboard|legacy|old)' || echo "  (none named suggestively)"
echo "  Review manually; delete via CF dashboard → Zero Trust → Networks → Tunnels if confirmed old."

log "Teardown complete."
```

- [ ] **Step 12.2: chmod + syntax check + commit**

```bash
cd /mnt/c/dashboard
chmod +x scripts/cloudflare/99-teardown.sh
bash -n scripts/cloudflare/99-teardown.sh
git add scripts/cloudflare/99-teardown.sh
git commit -m "feat(cf): 99-teardown script for old dashboard resources"
```

### Task 13: 40-smoke-from-ci.sh

**Files:**
- Create: `/mnt/c/dashboard/scripts/cloudflare/40-smoke-from-ci.sh`

- [ ] **Step 13.1: Write script**

Write `/mnt/c/dashboard/scripts/cloudflare/40-smoke-from-ci.sh`:
```bash
#!/usr/bin/env bash
# Curl smoke helper. Invoked by GitHub Actions after deploy.
# Requires: CF_ACCESS_CLIENT_ID + CF_ACCESS_CLIENT_SECRET env vars.
set -euo pipefail

URL="${URL:-https://dashboard.kiusinghung.com/health}"
: "${CF_ACCESS_CLIENT_ID:?Set CF_ACCESS_CLIENT_ID}"
: "${CF_ACCESS_CLIENT_SECRET:?Set CF_ACCESS_CLIENT_SECRET}"

echo "==> Smoke GET $URL (with service token)"
resp=$(curl -sf -w "\n__HTTP_%{http_code}__" \
    -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
    -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
    "$URL")

code=$(echo "$resp" | tr '\n' ' ' | grep -oE '__HTTP_[0-9]+__' | grep -oE '[0-9]+')
body=$(echo "$resp" | sed 's/__HTTP_[0-9]*__$//')

if [[ "$code" != "200" ]]; then
    echo "✗ HTTP $code"
    echo "$body"
    exit 1
fi

if echo "$body" | grep -q '"status":"ok"'; then
    echo "✓ /health returned status:ok"
    echo "$body"
else
    echo "✗ /health body missing status:ok"
    echo "$body"
    exit 1
fi
```

- [ ] **Step 13.2: chmod + syntax check + commit**

```bash
cd /mnt/c/dashboard
chmod +x scripts/cloudflare/40-smoke-from-ci.sh
bash -n scripts/cloudflare/40-smoke-from-ci.sh
git add scripts/cloudflare/40-smoke-from-ci.sh
git commit -m "feat(cf): 40-smoke-from-ci helper"
```

---

## Chunk C: VPS bootstrap scripts (Tasks 14–18)

### Task 14: deploy/vps/install-prep.sh

**Files:**
- Create: `/mnt/c/dashboard/deploy/vps/install-prep.sh`

- [ ] **Step 14.1: Write script**

```bash
mkdir -p /mnt/c/dashboard/deploy/vps
```

Write `/mnt/c/dashboard/deploy/vps/install-prep.sh`:
```bash
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
```

- [ ] **Step 14.2: chmod + syntax check + commit**

```bash
cd /mnt/c/dashboard
chmod +x deploy/vps/install-prep.sh
bash -n deploy/vps/install-prep.sh
git add deploy/vps/install-prep.sh
git commit -m "feat(vps): install-prep script (CF apt repo, UFW, fail2ban)"
```

### Task 15: deploy/vps/sshd-hardening.sh

**Files:**
- Create: `/mnt/c/dashboard/deploy/vps/sshd-hardening.sh`

- [ ] **Step 15.1: Write script**

Write `/mnt/c/dashboard/deploy/vps/sshd-hardening.sh`:
```bash
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
```

- [ ] **Step 15.2: chmod + syntax check + commit**

```bash
cd /mnt/c/dashboard
chmod +x deploy/vps/sshd-hardening.sh
bash -n deploy/vps/sshd-hardening.sh
git add deploy/vps/sshd-hardening.sh
git commit -m "feat(vps): sshd-hardening script with sshd -t safety gate"
```

### Task 16: deploy/vps/cloudflared.service + config template + install-enable.sh

**Files:**
- Create: `/mnt/c/dashboard/deploy/vps/cloudflared.service`
- Create: `/mnt/c/dashboard/deploy/vps/cloudflared.config.yml.template`
- Create: `/mnt/c/dashboard/deploy/vps/install-enable.sh`

- [ ] **Step 16.1: Write systemd unit**

Write `/mnt/c/dashboard/deploy/vps/cloudflared.service`:
```ini
[Unit]
Description=Cloudflare Tunnel (dashboard-prod)
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
ExecStart=/usr/bin/cloudflared --no-autoupdate --config /etc/cloudflared/config.yml tunnel run
Restart=on-failure
RestartSec=5s
RuntimeDirectory=cloudflared
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
AmbientCapabilities=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/etc/cloudflared
ProtectHome=read-only
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 16.2: Write config template**

Write `/mnt/c/dashboard/deploy/vps/cloudflared.config.yml.template`:
```yaml
# /etc/cloudflared/config.yml
# install-enable.sh fills in the tunnel UUID.
tunnel: __TUNNEL_ID__
credentials-file: /etc/cloudflared/__TUNNEL_ID__.json

loglevel: info

ingress:
  # Primary hostname — forward to nginx on loopback
  - hostname: dashboard.kiusinghung.com
    service: http://127.0.0.1:80
    originRequest:
      connectTimeout: 5s
      noHappyEyeballs: true
  # Catch-all: 404 everything else
  - service: http_status:404
```

- [ ] **Step 16.3: Write install-enable.sh**

Write `/mnt/c/dashboard/deploy/vps/install-enable.sh`:
```bash
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
if ! curl -sf http://127.0.0.1/health -o /dev/null; then
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
```

- [ ] **Step 16.4: chmod + commit**

```bash
cd /mnt/c/dashboard
chmod +x deploy/vps/install-enable.sh
bash -n deploy/vps/install-enable.sh
git add deploy/vps/cloudflared.service deploy/vps/cloudflared.config.yml.template deploy/vps/install-enable.sh
git commit -m "feat(vps): cloudflared systemd unit + install-enable script"
```

### Task 17: deploy/vps/README.md

**Files:**
- Create: `/mnt/c/dashboard/deploy/vps/README.md`

- [ ] **Step 17.1: Write README**

Write `/mnt/c/dashboard/deploy/vps/README.md`:
```md
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
```

- [ ] **Step 17.2: Commit**

```bash
cd /mnt/c/dashboard
git add deploy/vps/README.md
git commit -m "docs(vps): README for VPS bootstrap flow"
```

### Task 18: deploy/vps/ufw-rules.sh (standalone helper)

**Files:**
- Create: `/mnt/c/dashboard/deploy/vps/ufw-rules.sh`

- [ ] **Step 18.1: Write script**

Write `/mnt/c/dashboard/deploy/vps/ufw-rules.sh`:
```bash
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
```

- [ ] **Step 18.2: chmod + commit**

```bash
cd /mnt/c/dashboard
chmod +x deploy/vps/ufw-rules.sh
bash -n deploy/vps/ufw-rules.sh
git add deploy/vps/ufw-rules.sh
git commit -m "feat(vps): ufw-rules standalone helper"
```

---

## Chunk D: Nginx + production compose (Tasks 19–22)

### Task 19: nginx/nginx.conf (real_ip + rate-limit zones)

**Files:**
- Replace: `/mnt/c/dashboard/nginx/nginx.conf` (Phase 0 had none — may require creating)

- [ ] **Step 19.1: Write new nginx.conf**

Write `/mnt/c/dashboard/nginx/nginx.conf`:
```nginx
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events {
    worker_connections 1024;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    server_tokens off;

    # Trust cloudflared on loopback + WG peers on 10.10.0.0/24; use CF-Connecting-IP
    # as the real client IP. Without this, every request appears to come from
    # 127.0.0.1 / 10.10.0.x and rate limits + access logs become useless.
    set_real_ip_from 127.0.0.1;
    set_real_ip_from 10.10.0.0/24;
    real_ip_header CF-Connecting-IP;
    real_ip_recursive on;

    # Rate limit zones
    limit_req_zone $binary_remote_addr zone=api:10m     rate=10r/s;
    limit_req_zone $binary_remote_addr zone=general:10m rate=30r/s;

    log_format main '$remote_addr - $remote_user [$time_local] '
                    '"$request" $status $body_bytes_sent '
                    '"$http_referer" "$http_user_agent"';
    access_log /var/log/nginx/access.log main;

    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;

    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types text/plain text/css text/javascript application/javascript application/json application/xml image/svg+xml;

    client_max_body_size 1m;

    include /etc/nginx/conf.d/*.conf;
}
```

- [ ] **Step 19.2: Syntax-check via throwaway container**

If there's no `nginx/conf.d/` server block yet, create a placeholder so nginx -t doesn't error:
```bash
mkdir -p /mnt/c/dashboard/nginx/conf.d
echo 'server { listen 80 default_server; return 200 "phase1-placeholder"; }' \
    > /mnt/c/dashboard/nginx/conf.d/_placeholder.conf
```

Then:
```bash
cd /mnt/c/dashboard
docker run --rm \
    -v $PWD/nginx/nginx.conf:/etc/nginx/nginx.conf:ro \
    -v $PWD/nginx/conf.d:/etc/nginx/conf.d:ro \
    nginx:1.27-alpine nginx -t 2>&1 | tail -5
```
Expected: `nginx: configuration file /etc/nginx/nginx.conf test is successful`.

- [ ] **Step 19.3: Commit**

```bash
cd /mnt/c/dashboard
git add nginx/nginx.conf nginx/conf.d/_placeholder.conf
git commit -m "feat(nginx): ported nginx.conf with real_ip + rate limit zones"
```

### Task 20: nginx/conf.d/dashboard.conf

**Files:**
- Create: `/mnt/c/dashboard/nginx/conf.d/dashboard.conf`
- Delete: `/mnt/c/dashboard/nginx/conf.d/_placeholder.conf`

- [ ] **Step 20.1: Write dashboard.conf**

Write `/mnt/c/dashboard/nginx/conf.d/dashboard.conf`:
```nginx
# Server block for dashboard.kiusinghung.com.
# Nginx container listens on 0.0.0.0:80 INSIDE the container; docker-compose port
# binding limits which host interfaces can reach it:
#   "127.0.0.1:80:80"  for cloudflared → local loopback
#   "10.10.0.1:80:80"  for NUC WG dev bypass

server {
    listen 80 default_server;
    server_name dashboard.kiusinghung.com;

    # Drop requests with any other Host: header without responding
    if ($host != "dashboard.kiusinghung.com") {
        return 444;
    }

    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Robots-Tag "noindex, nofollow, noarchive, nosnippet" always;
    add_header Referrer-Policy "no-referrer" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=(), usb=(), magnetometer=(), accelerometer=(), gyroscope=()" always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" always;

    # API with rate limit
    location /api/ {
        limit_req zone=api burst=20 nodelay;
        proxy_pass http://backend:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_connect_timeout 5s;
        proxy_read_timeout 30s;
    }

    location = /health {
        limit_req zone=general burst=5 nodelay;
        proxy_pass http://backend:8000/health;
        proxy_set_header Host $host;
        proxy_connect_timeout 3s;
        proxy_read_timeout 5s;
    }

    location / {
        limit_req zone=general burst=60 nodelay;
        proxy_pass http://frontend;
        proxy_set_header Host $host;
        proxy_connect_timeout 5s;
        proxy_read_timeout 30s;
    }
}
```

- [ ] **Step 20.2: Remove placeholder + syntax check**

```bash
cd /mnt/c/dashboard
rm -f nginx/conf.d/_placeholder.conf
docker run --rm \
    -v $PWD/nginx/nginx.conf:/etc/nginx/nginx.conf:ro \
    -v $PWD/nginx/conf.d:/etc/nginx/conf.d:ro \
    nginx:1.27-alpine nginx -t 2>&1 | tail -5
```
Expected: `test is successful`. (Warnings about unresolvable `http://backend:8000` / `http://frontend` are fine — those are compose service names.)

- [ ] **Step 20.3: Commit**

```bash
cd /mnt/c/dashboard
git add nginx/conf.d/dashboard.conf
git rm --cached nginx/conf.d/_placeholder.conf 2>/dev/null || true
git commit -m "feat(nginx): dashboard.conf server block with rate limits + security headers"
```

### Task 21: nginx/start.sh (simpler)

**Files:**
- Create: `/mnt/c/dashboard/nginx/start.sh`

- [ ] **Step 21.1: Write start.sh**

Write `/mnt/c/dashboard/nginx/start.sh`:
```bash
#!/bin/sh
# Phase 1 nginx start script — simpler than Dashboard_old's (no cert-reload watcher,
# since CF Tunnel handles TLS at edge; no certbot volumes needed on the VPS).
set -eu

exec /docker-entrypoint.sh nginx -g 'daemon off;'
```

- [ ] **Step 21.2: chmod + commit**

```bash
cd /mnt/c/dashboard
chmod +x nginx/start.sh
git add nginx/start.sh
git commit -m "feat(nginx): simpler start.sh (no cert-reload watcher; CF handles TLS)"
```

### Task 22: docker-compose.prod.yml

**Files:**
- Create: `/mnt/c/dashboard/docker-compose.prod.yml`

- [ ] **Step 22.1: Resolve image digests**

```bash
docker pull redis:7-alpine
docker inspect --format='{{index .RepoDigests 0}}' redis:7-alpine

docker pull nginx:1.27-alpine
docker inspect --format='{{index .RepoDigests 0}}' nginx:1.27-alpine
```

Note the two `image@sha256:...` strings — we'll substitute them below.

- [ ] **Step 22.2: Write docker-compose.prod.yml**

Write `/mnt/c/dashboard/docker-compose.prod.yml` (replace `<REDIS_DIGEST>` and `<NGINX_DIGEST>` with the strings from Step 22.1; if digest-pinning breaks your flow, you can temporarily use `redis:7-alpine` / `nginx:1.27-alpine` and pin later):

```yaml
# docker-compose.prod.yml
# Run with: docker compose -f docker-compose.prod.yml up -d --build
#
# Nginx is DUAL-bound via two port mappings so cloudflared (loopback) AND the
# NUC (via WireGuard) can both reach it. UFW allows 80 on wg0 only; direct
# public access is impossible (no IONOS rule + UFW deny-all on eth0).

services:
  redis:
    image: redis:7-alpine@<REDIS_DIGEST>
    command: ["redis-server", "--requirepass", "${REDIS_PASSWORD}"]
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD}", "ping"]
      interval: 10s
      retries: 5
      start_period: 5s
    mem_limit: 256m
    cpus: "0.5"
    security_opt: ["no-new-privileges:true"]
    cap_drop: ["ALL"]
    read_only: true
    tmpfs: ["/tmp:size=50m"]
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
    networks: [td-net]

  backend:
    build: ./backend
    env_file: .env
    extra_hosts: ["host.docker.internal:host-gateway"]
    depends_on:
      redis: { condition: service_healthy }
    restart: unless-stopped
    # Prod: use the Dockerfile CMD (uvicorn WITHOUT --reload). No command: override here.
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://127.0.0.1:8000/health || exit 1"]
      interval: 15s
      retries: 5
      start_period: 30s
    mem_limit: 512m
    cpus: "1.0"
    security_opt: ["no-new-privileges:true"]
    cap_drop: ["ALL"]
    read_only: true
    tmpfs: ["/tmp:size=50m"]
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
    networks: [td-net]

  frontend:
    # Prod frontend uses the nginx:alpine stage (static serve) — the Dockerfile's
    # default final target. Phase 0 compose overrode to target: build for dev.
    build:
      context: ./frontend
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "wget -q --spider http://127.0.0.1/ || exit 1"]
      interval: 15s
      retries: 5
      start_period: 10s
    mem_limit: 128m
    cpus: "0.5"
    security_opt: ["no-new-privileges:true"]
    cap_drop: ["ALL"]
    read_only: true
    tmpfs:
      - "/var/cache/nginx:size=20m"
      - "/var/run:size=1m"
      - "/tmp:size=10m"
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
    networks: [td-net]

  nginx:
    image: nginx:1.27-alpine@<NGINX_DIGEST>
    restart: unless-stopped
    # DUAL-bound: loopback for cloudflared + wg0 for NUC dev bypass.
    ports:
      - "127.0.0.1:80:80"
      - "10.10.0.1:80:80"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx/conf.d:/etc/nginx/conf.d:ro
      - ./nginx/start.sh:/start.sh:ro
    entrypoint: ["/bin/sh", "/start.sh"]
    ulimits:
      nofile:
        soft: 8192
        hard: 16384
    depends_on:
      backend:  { condition: service_healthy }
      frontend: { condition: service_healthy }
    healthcheck:
      test: ["CMD-SHELL", "wget -q --spider http://127.0.0.1/health || exit 1"]
      interval: 15s
      retries: 5
      start_period: 20s
    mem_limit: 128m
    cpus: "0.5"
    security_opt: ["no-new-privileges:true"]
    cap_drop: ["ALL"]
    cap_add: ["NET_BIND_SERVICE"]
    read_only: true
    tmpfs:
      - "/var/log/nginx:size=20m"
      - "/var/cache/nginx:size=20m"
      - "/var/run:size=1m"
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
    networks: [td-net]

networks:
  td-net:
    driver: bridge
```

- [ ] **Step 22.3: Validate compose syntax**

```bash
cd /mnt/c/dashboard
docker compose -f docker-compose.prod.yml config > /dev/null
```
Expected: no errors.

- [ ] **Step 22.4: Commit**

```bash
cd /mnt/c/dashboard
git add docker-compose.prod.yml
git commit -m "feat: docker-compose.prod.yml with dual-bound nginx + hardened services"
```

---

## Chunk E: Tests + CI (Tasks 23–25)

### Task 23: tests/e2e/ scaffold

**Files:**
- Create: `/mnt/c/dashboard/tests/e2e/package.json`
- Create: `/mnt/c/dashboard/tests/e2e/playwright.config.ts`
- Create: `/mnt/c/dashboard/tests/e2e/tsconfig.json`
- Create: `/mnt/c/dashboard/tests/e2e/.gitignore`

- [ ] **Step 23.1: Scaffold directory**

```bash
mkdir -p /mnt/c/dashboard/tests/e2e
```

- [ ] **Step 23.2: Write package.json**

Write `/mnt/c/dashboard/tests/e2e/package.json`:
```json
{
  "name": "@dashboard/e2e",
  "version": "0.0.0",
  "private": true,
  "type": "module",
  "scripts": {
    "test": "playwright test",
    "test:smoke": "playwright test smoke.spec.ts",
    "install-browsers": "playwright install chromium --with-deps"
  },
  "devDependencies": {
    "@playwright/test": "latest",
    "@types/node": "latest",
    "typescript": "latest"
  }
}
```

- [ ] **Step 23.3: Write playwright.config.ts**

Write `/mnt/c/dashboard/tests/e2e/playwright.config.ts`:
```ts
import { defineConfig, devices } from '@playwright/test';

const CF_ACCESS_CLIENT_ID = process.env.CF_ACCESS_CLIENT_ID;
const CF_ACCESS_CLIENT_SECRET = process.env.CF_ACCESS_CLIENT_SECRET;
const BASE_URL = process.env.SMOKE_BASE_URL ?? 'https://dashboard.kiusinghung.com';

export default defineConfig({
  testDir: '.',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [['list'], ['github']] : 'list',
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    extraHTTPHeaders: CF_ACCESS_CLIENT_ID && CF_ACCESS_CLIENT_SECRET ? {
      'CF-Access-Client-Id': CF_ACCESS_CLIENT_ID,
      'CF-Access-Client-Secret': CF_ACCESS_CLIENT_SECRET,
    } : {},
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
```

- [ ] **Step 23.4: Write tsconfig.json**

Write `/mnt/c/dashboard/tests/e2e/tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "noEmit": true,
    "types": ["node"],
    "lib": ["ES2022", "DOM"]
  },
  "include": ["*.ts", "*.spec.ts"]
}
```

- [ ] **Step 23.5: Write .gitignore**

Write `/mnt/c/dashboard/tests/e2e/.gitignore`:
```
node_modules/
playwright-report/
test-results/
```

- [ ] **Step 23.6: Install**

```bash
cd /mnt/c/dashboard/tests/e2e
export PATH="$HOME/.npm-global/bin:$PATH"
pnpm install
pnpm exec playwright --version
```
Expected: Playwright version printed.

- [ ] **Step 23.7: Commit**

```bash
cd /mnt/c/dashboard
git add tests/e2e/
git commit -m "test(e2e): playwright scaffold + tsconfig"
```

### Task 24: tests/e2e/smoke.spec.ts

**Files:**
- Create: `/mnt/c/dashboard/tests/e2e/smoke.spec.ts`

- [ ] **Step 24.1: Write the test**

Write `/mnt/c/dashboard/tests/e2e/smoke.spec.ts`:
```ts
import { test, expect } from '@playwright/test';

test.describe('Phase 1 smoke', () => {
  test('GET /health returns db:ok in prod', async ({ request }) => {
    const resp = await request.get('/health');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body.status).toBe('ok');
    expect(body.env).toBe('prod');
    expect(body.db).toBe('ok');
  });

  test('root page has correct title', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle('Trading Dashboard');
    await expect(page.locator('text=/Backend:/')).toBeVisible();
  });

  test('security headers present on /', async ({ request }) => {
    const resp = await request.get('/');
    const h = resp.headers();
    expect(h['strict-transport-security']).toContain('max-age=');
    expect(h['x-frame-options']).toBe('DENY');
    expect(h['x-content-type-options']).toBe('nosniff');
    expect(h['x-robots-tag']).toContain('noindex');
    expect(h['referrer-policy']).toBe('no-referrer');
    expect(h['content-security-policy']).toContain("default-src 'self'");
  });

  test('unauthenticated requests are blocked at CF Access', async ({ browser }) => {
    const ctx = await browser.newContext({ extraHTTPHeaders: {} });
    const resp = await ctx.request.get('/health', { failOnStatusCode: false });
    expect([302, 401, 403]).toContain(resp.status());
    await ctx.close();
  });
});
```

- [ ] **Step 24.2: Commit**

```bash
cd /mnt/c/dashboard
git add tests/e2e/smoke.spec.ts
git commit -m "test(e2e): Phase 1 smoke spec (health, title, security headers, auth gate)"
```

### Task 25: .github/workflows/deploy.yml

**Files:**
- Create: `/mnt/c/dashboard/.github/workflows/deploy.yml`

- [ ] **Step 25.1: Write workflow**

Write `/mnt/c/dashboard/.github/workflows/deploy.yml`:
```yaml
name: Deploy

on:
  push:
    branches: [main]
    paths-ignore:
      - '**.md'
      - 'docs/**'
      - 'TASKS.md'
      - 'CHANGELOG.md'

concurrency:
  group: deploy-${{ github.ref }}
  cancel-in-progress: false

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install SSH key
        uses: webfactory/ssh-agent@v0.9.0
        with:
          ssh-private-key: ${{ secrets.VPS_SSH_KEY }}

      - name: Add VPS host key
        run: |
          mkdir -p ~/.ssh
          ssh-keyscan -p 2222 -H 88.208.197.219 >> ~/.ssh/known_hosts

      - name: Rsync to VPS
        run: |
          rsync -avz --delete \
            --exclude '.git/' \
            --exclude 'node_modules/' \
            --exclude '__pycache__/' \
            --exclude '.venv/' \
            --exclude '*.pyc' \
            --exclude '.env' \
            --exclude 'secrets/' \
            --exclude 'frontend/dist/' \
            --exclude 'tests/e2e/test-results/' \
            --exclude 'tests/e2e/playwright-report/' \
            --exclude 'scripts/cloudflare/.state/' \
            -e "ssh -p 2222" \
            ./ trader@88.208.197.219:/home/trader/trading-dashboard/

      - name: Remote build + up + nginx reload
        run: |
          ssh -p 2222 trader@88.208.197.219 <<'EOF'
            set -e
            cd /home/trader/trading-dashboard
            docker compose -f docker-compose.prod.yml build
            docker compose -f docker-compose.prod.yml up -d
            docker compose -f docker-compose.prod.yml exec -T nginx nginx -s reload
            docker compose -f docker-compose.prod.yml ps
          EOF

      - name: Wait for backend health (remote)
        run: |
          ssh -p 2222 trader@88.208.197.219 \
            'for i in $(seq 1 30); do curl -sf http://127.0.0.1/health && exit 0; sleep 2; done; exit 1'

      - uses: pnpm/action-setup@v4
        with: { version: latest }
      - uses: actions/setup-node@v4
        with:
          node-version: '24'
          cache: 'pnpm'
          cache-dependency-path: tests/e2e/pnpm-lock.yaml

      - name: Install e2e deps
        working-directory: tests/e2e
        run: |
          pnpm install --frozen-lockfile
          pnpm exec playwright install chromium --with-deps

      - name: Smoke test via CF Access service token
        working-directory: tests/e2e
        env:
          CF_ACCESS_CLIENT_ID:     ${{ secrets.CF_ACCESS_CLIENT_ID }}
          CF_ACCESS_CLIENT_SECRET: ${{ secrets.CF_ACCESS_CLIENT_SECRET }}
          SMOKE_BASE_URL:          https://dashboard.kiusinghung.com
        run: pnpm test:smoke

      - name: Upload smoke artifacts on failure
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: smoke-failure-${{ github.run_id }}
          path: |
            tests/e2e/playwright-report/
            tests/e2e/test-results/
```

- [ ] **Step 25.2: Validate YAML**

```bash
cd /mnt/c/dashboard
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/deploy.yml'))"
```
Expected: no output (parse OK).

- [ ] **Step 25.3: Commit**

```bash
cd /mnt/c/dashboard
git add .github/workflows/deploy.yml
git commit -m "ci: deploy workflow — rsync + compose up + Playwright smoke"
```

---

## Chunk F: Real deploy.sh (Task 26)

### Task 26: scripts/deploy.sh (replace Phase 0 stub)

**Files:**
- Replace: `/mnt/c/dashboard/scripts/deploy.sh`

- [ ] **Step 26.1: Write new deploy.sh**

Write `/mnt/c/dashboard/scripts/deploy.sh`:
```bash
#!/usr/bin/env bash
# Manual deploy (for when you want to bypass GitHub Actions).
# Usage: ./scripts/deploy.sh
set -euo pipefail

VPS_HOST="${VPS_HOST:-88.208.197.219}"
VPS_USER="${VPS_USER:-trader}"
VPS_PORT="${VPS_PORT:-2222}"
VPS_PATH="${VPS_PATH:-/home/trader/trading-dashboard}"

echo "==> Syncing to $VPS_USER@$VPS_HOST:$VPS_PATH"
rsync -avz --delete \
    --exclude '.git/' \
    --exclude 'node_modules/' \
    --exclude '__pycache__/' \
    --exclude '.venv/' \
    --exclude '*.pyc' \
    --exclude '.env' \
    --exclude 'secrets/' \
    --exclude 'frontend/dist/' \
    --exclude 'tests/e2e/test-results/' \
    --exclude 'tests/e2e/playwright-report/' \
    --exclude 'scripts/cloudflare/.state/' \
    -e "ssh -p $VPS_PORT" \
    ./ "$VPS_USER@$VPS_HOST:$VPS_PATH/"

echo "==> Remote build + up + nginx reload"
ssh -p "$VPS_PORT" "$VPS_USER@$VPS_HOST" <<EOF
  set -e
  cd "$VPS_PATH"
  docker compose -f docker-compose.prod.yml build
  docker compose -f docker-compose.prod.yml up -d
  # Post-recreate 502 storm fix: nginx caches backend IP; reload re-resolves.
  # See memory nginx_backend_recreate_502.md
  echo "--> Reloading nginx..."
  docker compose -f docker-compose.prod.yml exec -T nginx nginx -s reload
  docker compose -f docker-compose.prod.yml ps
EOF

echo "==> Waiting for backend health..."
for i in $(seq 1 30); do
    if ssh -p "$VPS_PORT" "$VPS_USER@$VPS_HOST" 'curl -sf http://127.0.0.1/health' >/dev/null; then
        echo "✓ Backend healthy"
        break
    fi
    sleep 2
done

echo "==> Done. Run tests/e2e smoke to verify public domain:"
echo "   cd tests/e2e && pnpm test:smoke"
```

- [ ] **Step 26.2: chmod + syntax check + commit**

```bash
cd /mnt/c/dashboard
chmod +x scripts/deploy.sh
bash -n scripts/deploy.sh
git add scripts/deploy.sh
git commit -m "feat(scripts): real deploy.sh — rsync + remote build + nginx reload"
```

---

## Chunk G: Pre-flight verification (Task 27)

Before the cutover, verify everything works locally.

### Task 27: Local pre-flight

- [ ] **Step 27.1: Full lint + test sweep**

```bash
cd /mnt/c/dashboard/frontend
export PATH="$HOME/.npm-global/bin:$PATH"
pnpm lint && pnpm stylelint && pnpm typecheck && pnpm test && pnpm build && pnpm build-storybook
cd /mnt/c/dashboard/backend
uv run ruff check . && uv run ruff format --check . && uv run mypy app/ && uv run pytest
```
Expected: all commands exit 0.

- [ ] **Step 27.2: Docker compose syntax check for prod**

```bash
cd /mnt/c/dashboard
docker compose -f docker-compose.prod.yml config > /dev/null
```
Expected: no errors.

- [ ] **Step 27.3: Local dev stack still works**

```bash
cd /mnt/c/dashboard
docker compose up -d
sleep 10
curl -sf http://localhost:8000/health | python3 -m json.tool
docker compose down
```
Expected: `{status:ok,env:dev,db:ok}`.

- [ ] **Step 27.4: Confirm Dashboard_old is intact**

```bash
ls /mnt/c/Dashboard_old/backend /mnt/c/Dashboard_old/nginx /mnt/c/Dashboard_old/scripts /mnt/c/Dashboard_old/deploy 2>&1 | head -20
```
Expected: directories exist with contents. If empty, rollback plan compromised — STOP.

- [ ] **Step 27.5: Capture current LE cert expiry**

```bash
openssl s_client -connect dashboard.kiusinghung.com:443 -servername dashboard.kiusinghung.com </dev/null 2>/dev/null \
    | openssl x509 -noout -dates
```
Record `notBefore` and `notAfter`. If `notAfter` < 30 days away, run `certbot renew` in `Dashboard_old` container BEFORE cutover.

- [ ] **Step 27.6: Confirm CF prerequisites are ready**

User acknowledges:
- CF API token created in CF dashboard with correct scopes.
- `CF_API_TOKEN`, `CF_ZONE_ID`, `CF_ACCOUNT_ID` exported in shell.
- Google IdP configured in CF Zero Trust (see Task 9 prerequisite).
- Google Cloud Console OAuth client created (for Google IdP).
- IONOS dashboard access ready.
- Browser ready for Google login.

- [ ] **Step 27.7: No commit — pre-flight gate**

If all pass, advance to Chunk H. If ANY fail, stop and fix.

---

## Chunk H: Cutover execution (Tasks 28–37)

**USER GATE — CRITICAL:** Tasks 28 onward tear down the live deployment and create production CF resources. Do NOT proceed until Task 27 is green AND user has confirmed all CF prerequisites.

### Task 28: Cert expiry pre-check (covered by 27.5)

Already done. Continue to Task 29.

### Task 29: Steps 1–2 — tear down old VPS stack

- [ ] **Step 29.1: SSH to VPS, stop old compose**

```bash
ssh -p 2222 trader@88.208.197.219 '
    cd /home/trader/trading-dashboard && \
    docker compose down -v && \
    docker compose ps
'
```
Expected: no containers running.

- [ ] **Step 29.2: Wipe old repo tree**

```bash
ssh -p 2222 trader@88.208.197.219 'rm -rf /home/trader/trading-dashboard && mkdir -p /home/trader/trading-dashboard'
```

- [ ] **Step 29.3: Confirm**

```bash
ssh -p 2222 trader@88.208.197.219 'ls -la /home/trader/trading-dashboard'
```
Expected: empty directory.

### Task 30: Step 3 — drop legacy `trading` DB

- [ ] **Step 30.1: Drop DB (NUC-local path recommended)**

On Windows PowerShell:
```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -c 'DROP DATABASE trading;'
```

OR from WSL localhost:
```bash
psql -U postgres -h localhost -c 'DROP DATABASE trading;'
```

Expected: `DROP DATABASE`.

If neither works (postgres superuser local-only access denied), use ALTER OWNER path:
```bash
# Run locally on NUC with postgres superuser socket access:
psql -U postgres -h localhost -c 'ALTER DATABASE trading OWNER TO trader;'
# Then from anywhere with WG access:
psql -h 10.10.0.2 -U trader -d postgres -c 'DROP DATABASE trading;'
```

- [ ] **Step 30.2: Confirm only `dashboard` remains**

```bash
psql -h 10.10.0.2 -U trader -d postgres -c '\l' | grep -E '(trading|dashboard)'
```
Expected: only `dashboard` listed.

### Task 31: Steps 4–12 — Cloudflare teardown + re-provision

- [ ] **Step 31.1: Teardown old CF resources**

```bash
cd /mnt/c/dashboard
bash scripts/cloudflare/99-teardown.sh
```

- [ ] **Step 31.2: Verify token scopes**

```bash
bash scripts/cloudflare/00-check-token.sh
```
Expected: "All checks passed".

- [ ] **Step 31.3: Create tunnel**

```bash
bash scripts/cloudflare/10-tunnel-create.sh
```
Expected: "Tunnel created (id=...)" and credentials saved to `~/.secrets/cloudflared-<UUID>.json`.

- [ ] **Step 31.4: Create DNS CNAME**

```bash
bash scripts/cloudflare/11-dns-cname.sh
```
Expected: "DNS record applied".

- [ ] **Step 31.5: Create Access app**

```bash
bash scripts/cloudflare/20-access-app.sh
```

- [ ] **Step 31.6: Create Google policy**

Prerequisite: Google IdP configured in CF Zero Trust. If not:
- CF dashboard → Zero Trust → Settings → Authentication → Login methods → Add new → Google
- Need Google Cloud Console OAuth client ID + secret

Then:
```bash
bash scripts/cloudflare/21-access-policy-google.sh
```

- [ ] **Step 31.7: Create placeholder bypass policy**

```bash
bash scripts/cloudflare/22-access-policy-bypass.sh
```

- [ ] **Step 31.8: Generate service token**

```bash
bash scripts/cloudflare/23-service-token.sh
```

**USER ACTION:** save both printed values:
```bash
export CF_ACCESS_CLIENT_ID='<printed>'
export CF_ACCESS_CLIENT_SECRET='<printed>'
gh secret set CF_ACCESS_CLIENT_ID     --body "$CF_ACCESS_CLIENT_ID"
gh secret set CF_ACCESS_CLIENT_SECRET --body "$CF_ACCESS_CLIENT_SECRET"
# Persist for local dev:
echo "export CF_ACCESS_CLIENT_ID='$CF_ACCESS_CLIENT_ID'"         >> ~/.bashrc
echo "export CF_ACCESS_CLIENT_SECRET='$CF_ACCESS_CLIENT_SECRET'" >> ~/.bashrc
```

- [ ] **Step 31.9: Wire service token into bypass policy (re-run 22)**

```bash
bash scripts/cloudflare/22-access-policy-bypass.sh
```
Expected: "Attaching service token..."; "Bypass policy applied".

- [ ] **Step 31.10: Security hardening**

```bash
bash scripts/cloudflare/30-security-hardening.sh
```

**USER ACTION:** if DS record was printed, add it at the domain registrar (IONOS domain panel) if DNSSEC was off before. Also verify in CF dashboard → Security → Bots → "Block AI Scrapers and Crawlers" is ON.

- [ ] **Step 31.11: Set VPS_SSH_KEY secret**

```bash
gh secret set VPS_SSH_KEY < ~/.ssh/id_ed25519
```
(Adjust path if your key is elsewhere.)

### Task 32: Step 13 — VPS install-prep

- [ ] **Step 32.1: Rsync repo to VPS**

```bash
cd /mnt/c/dashboard
rsync -avz --delete \
    --exclude '.git/' --exclude 'node_modules/' --exclude '__pycache__/' \
    --exclude '.venv/' --exclude '*.pyc' --exclude '.env' --exclude 'secrets/' \
    --exclude 'frontend/dist/' --exclude 'tests/e2e/test-results/' \
    --exclude 'tests/e2e/playwright-report/' \
    --exclude 'scripts/cloudflare/.state/' \
    -e "ssh -p 2222" \
    ./ trader@88.208.197.219:/home/trader/trading-dashboard/
```

- [ ] **Step 32.2: Run install-prep**

```bash
ssh -p 2222 trader@88.208.197.219 \
    'sudo bash /home/trader/trading-dashboard/deploy/vps/install-prep.sh'
```
Expected: cloudflared + ufw + fail2ban + jq installed; UFW enabled with 3 rules; fail2ban running.

### Task 33: Step 14 — SSH hardening

- [ ] **Step 33.1: Open a secondary SSH session NOW**

In a new terminal:
```bash
ssh -p 2222 trader@88.208.197.219
```
Keep open.

- [ ] **Step 33.2: Run hardening**

```bash
ssh -p 2222 trader@88.208.197.219 \
    'sudo bash /home/trader/trading-dashboard/deploy/vps/sshd-hardening.sh'
```

- [ ] **Step 33.3: Verify secondary session still works**

In the secondary terminal: `whoami`. Expected: `trader`.

- [ ] **Step 33.4: Third fresh SSH session**

```bash
ssh -p 2222 trader@88.208.197.219 'whoami'
```
Expected: `trader`. If fails, ROLLBACK via secondary session:
```bash
sudo cp /etc/ssh/sshd_config.pre-phase1-<timestamp> /etc/ssh/sshd_config
sudo systemctl reload sshd
```

### Task 34: Step 15 — deploy new stack

- [ ] **Step 34.1: Create production `.env` on VPS**

```bash
ssh -p 2222 trader@88.208.197.219
```

On VPS:
```bash
REDIS_PW=$(openssl rand -base64 24 | tr -d '+/=' | head -c 24)
APP_KEY=$(openssl rand -base64 32)

cat > /home/trader/trading-dashboard/.env <<EOF
APP_ENV=prod
APP_SECRET_KEY=$APP_KEY
APP_CORS_ORIGINS=["https://dashboard.kiusinghung.com"]
DATABASE_URL=postgresql+asyncpg://trader:REPLACE-ME@10.10.0.2:5432/dashboard
POSTGRES_POOL_SIZE=10
POSTGRES_MAX_OVERFLOW=20
REDIS_PASSWORD=$REDIS_PW
REDIS_URL=redis://:$REDIS_PW@redis:6379/0
EOF
chmod 0600 /home/trader/trading-dashboard/.env
nano /home/trader/trading-dashboard/.env
# Replace REPLACE-ME in DATABASE_URL with real trader PG password.
# Verify REDIS_URL embedded password matches REDIS_PASSWORD exactly.
```

- [ ] **Step 34.2: Build + up prod compose**

```bash
# On VPS:
cd /home/trader/trading-dashboard
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
sleep 15
docker compose -f docker-compose.prod.yml ps
```
Expected: all 4 services up + healthy.

- [ ] **Step 34.3: Verify loopback health**

```bash
# On VPS:
curl -sf http://127.0.0.1/health | python3 -m json.tool
```
Expected: `{status:ok, env:prod, db:ok}`.

### Task 35: Step 16 — transfer tunnel creds + enable cloudflared

- [ ] **Step 35.1: SCP credentials from NUC**

```bash
# On NUC:
TUNNEL_ID=$(cat /mnt/c/dashboard/scripts/cloudflare/.state/tunnel-id)
scp -P 2222 ~/.secrets/cloudflared-$TUNNEL_ID.json \
    trader@88.208.197.219:/tmp/
```

- [ ] **Step 35.2: Install + enable cloudflared**

```bash
ssh -p 2222 trader@88.208.197.219 "
    sudo mv /tmp/cloudflared-$TUNNEL_ID.json /etc/cloudflared/ && \
    sudo bash /home/trader/trading-dashboard/deploy/vps/install-enable.sh $TUNNEL_ID
"
```

- [ ] **Step 35.3: Verify tunnel from CF**

```bash
# On NUC:
curl -sf https://dashboard.kiusinghung.com/health \
    -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
    -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
    | python3 -m json.tool
```
Expected: `{status:ok, env:prod, db:ok}`.

### Task 36: Steps 17–19 — Playwright smoke + browser verify

- [ ] **Step 36.1: Install Playwright chromium on NUC**

```bash
cd /mnt/c/dashboard/tests/e2e
export PATH="$HOME/.npm-global/bin:$PATH"
pnpm install --frozen-lockfile 2>/dev/null || pnpm install
pnpm exec playwright install chromium --with-deps
```

- [ ] **Step 36.2: Run smoke test**

```bash
cd /mnt/c/dashboard/tests/e2e
pnpm test:smoke
```
Expected: 4/4 tests pass.

- [ ] **Step 36.3: Human browser verify**

Open `https://dashboard.kiusinghung.com`:
1. Expect Google login page.
2. Sign in with `josephhungkk@gmail.com`.
3. See "Trading Dashboard" + "Backend: ok".

Test 2nd email (incognito): `ispyling@gmail.com` → should also work.

Test unallowed email (optional): expect "You do not have access" page from CF Access.

### Task 37: Step 20 — IONOS firewall lockdown (USER GATE)

**USER ACTION:** log into IONOS control panel → Server → Network → Firewall policy.

- [ ] **Step 37.1: Remove inbound 80/443/8443/8447**

Remove:
```
Allow All TCP 80
Allow All TCP 443
Allow All TCP 8443
Allow All TCP 8447
```

Keep:
```
Allow All UDP 51820
Allow All TCP 2222
```

Save.

- [ ] **Step 37.2: Verify direct-IP bypass closed**

```bash
curl -vk --connect-timeout 5 https://88.208.197.219 2>&1 | tail -20
curl -vk --connect-timeout 5 http://88.208.197.219  2>&1 | tail -5
```
Expected: both fail with timeout/refused.

- [ ] **Step 37.3: Re-verify domain works via CF**

```bash
cd /mnt/c/dashboard/tests/e2e
pnpm test:smoke
```
Expected: 4/4 pass.

Browser check: reload `https://dashboard.kiusinghung.com` → still works.

---

## Chunk I: Post-cutover close-out (Tasks 38–41)

### Task 38: Update CLAUDE.md for post-cutover reality

**Files:**
- Modify: `/mnt/c/dashboard/CLAUDE.md`

- [ ] **Step 38.1: Update the Stack — Reverse proxy line**

Read `CLAUDE.md`. Find:
```
- **Reverse proxy:** Nginx with Let's Encrypt DNS-01 via Cloudflare (Phase 1+, on the VPS)
```

Replace with:
```
- **Reverse proxy & TLS:** Cloudflare Tunnel terminates TLS at CF edge (no public 80/443 on VPS); nginx runs on the VPS as defense-in-depth (rate limits, security headers, Host: strict-match). Let's Encrypt + certbot retired in Phase 1.
- **Access gate:** CF Access with Google IdP for `josephhungkk@gmail.com` + `ispyling@gmail.com`; CF Access service token for CI bypass; WireGuard route (`http://10.10.0.1/`) for NUC-local dev bypass.
```

- [ ] **Step 38.2: Update Common Commands**

Find the `Deploy to VPS` line. Replace block with:
```
    # Deploy to VPS (manual; GitHub Actions auto-deploys on push-to-main)
    ./scripts/deploy.sh

    # Dev bypass (from NUC, over WireGuard — no CF Access needed)
    curl -sf http://10.10.0.1/health

    # CI bypass (from anywhere, via service token)
    curl -sf https://dashboard.kiusinghung.com/health \
      -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
      -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET"
```

- [ ] **Step 38.3: Commit**

```bash
cd /mnt/c/dashboard
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for post-cutover CF Tunnel reality"
```

### Task 39: Update CHANGELOG.md with [0.1.0]

**Files:**
- Modify: `/mnt/c/dashboard/CHANGELOG.md`

- [ ] **Step 39.1: Replace Planned-for-[0.1.0] with real [0.1.0] release entry**

Read `CHANGELOG.md`. Replace the `### Planned for [0.1.0]` block under `## [Unreleased]` with:

```md
## [Unreleased]

## [0.1.0] — 2026-04-21
### Added
- Cloudflare Tunnel (cloudflared on VPS) replaces public 80/443.
- Cloudflare Access with Google IdP + 2-email allowlist.
- CF Access service token bypass for CI smoke tests.
- WireGuard dev-bypass route to nginx (10.10.0.1:80).
- scripts/cloudflare/ — 10 idempotent CF API driver scripts.
- deploy/vps/ — install-prep + install-enable + sshd-hardening + UFW + fail2ban.
- docker-compose.prod.yml — dual-bound nginx, tmpfs, non-root, resource limits, pinned digests.
- tests/e2e/ — Playwright smoke test; runs in CI via deploy.yml.
- .github/workflows/deploy.yml — rsync + compose up + smoke on push-to-main.
- gitleaks pre-commit hook.
- pnpm audit + pip-audit CI steps.
- Real scripts/deploy.sh (replaced Phase 0 stub).
- Architect-review workflow codified in CLAUDE.md phase workflow.

### Changed
- Nginx kept as defense-in-depth (headers, rate limits, Host: strict-match); certbot + cert-reload watcher removed.
- IONOS firewall: only 2222/tcp + 51820/udp exposed (was 80, 443, 8443, 8447, 51820, 2222).
- SSH hardened: password auth off, AllowUsers trader only, MaxAuthTries 3.

### Removed
- Dashboard_old deployment at dashboard.kiusinghung.com (torn down during cutover).
- Let's Encrypt certbot container + cert-reload sentinel.
- `trading` DB on NUC PG18.
- Public 80/443 on VPS.
```

- [ ] **Step 39.2: Commit**

```bash
cd /mnt/c/dashboard
git add CHANGELOG.md
git commit -m "docs: CHANGELOG [0.1.0] entry for Phase 1 cutover"
```

### Task 40: Update TASKS.md

**Files:**
- Modify: `/mnt/c/dashboard/TASKS.md`

- [ ] **Step 40.1: Mark Phase 1 complete**

Read `TASKS.md`. Change Phase 1 header from `*(in progress)*` to `*(complete — v0.1.0 · 2026-04-21)*`. Flip every `- [ ]` under Phase 1 to `- [x]`.

Mark Phase 2 as `*(next)*`.

- [ ] **Step 40.2: Commit**

```bash
cd /mnt/c/dashboard
git add TASKS.md
git commit -m "docs: mark phase 1 complete in TASKS.md"
```

### Task 41: Tag v0.1.0 + push + verify CI

- [ ] **Step 41.1: Push all commits**

```bash
cd /mnt/c/dashboard
git push origin main
```

- [ ] **Step 41.2: Tag v0.1.0**

```bash
cd /mnt/c/dashboard
git tag -a v0.1.0 -m "Phase 1: VPS cutover complete — CF Tunnel + Access + hardened"
git push origin v0.1.0
```

- [ ] **Step 41.3: Watch CI**

```bash
cd /mnt/c/dashboard
gh run watch
```
Expected: both `ci.yml` + `deploy.yml` green.

- [ ] **Step 41.4: Final success-criteria checklist (spec §7)**

Confirm each of A–F has evidence from Tasks 35–41:
- A (access validation): Tasks 35.3, 36.2, 37.2
- B (security headers): Task 36.2 (smoke test case)
- C (Playwright smoke): Task 36.2
- D (negative tests): Task 37.2 (direct-IP timeout) + Task 36.2 (auth-gate negative case)
- E (CI validation): Task 41.3
- F (rollback readiness): Task 27.4 (Dashboard_old still intact)

- [ ] **Step 41.5: Report back**

Report:
- Repo URL
- Last CI run URL
- Smoke test + any failure artifacts
- All 6 spec §7 sub-items green
- Next: Phase 2 — Auth + DB-backed config service

---

## Appendix A — Common failure modes & fixes

**`cf` 401 / invalid token.** Token scopes missing or expired. Run `./scripts/cloudflare/00-check-token.sh`.

**Tunnel credentials lost.** `cloudflared tunnel token <UUID>` on any machine with cloudflared regenerates. Or delete via dashboard + re-run `10-tunnel-create.sh`.

**`cloudflared.service` fails to start.** Check `journalctl -u cloudflared -e`. Usually: credentials file mode wrong (must be 0600 root:root) or config.yml syntax error.

**nginx reports `host not found in upstream "backend"` at start.** Service name lookup happens after backend is up. Compose `depends_on: backend: condition: service_healthy` prevents.

**Direct-IP bypass still works after IONOS change.** IONOS firewall may take 1–2 minutes to propagate. Wait; re-test.

**CF Access keeps redirecting even with service token.** Verify `CF_ACCESS_CLIENT_ID` + `CF_ACCESS_CLIENT_SECRET` match what `23-service-token.sh` printed. Re-run `22-access-policy-bypass.sh` if needed.

**Playwright smoke test hangs.** `extraHTTPHeaders` missing → Playwright follows the 302 redirect to CF login and times out. Check `playwright.config.ts` reads env.

**GHA deploy fails rsync with "Host key verification failed".** `ssh-keyscan` didn't capture right key, or `VPS_SSH_KEY` secret wrong.

**DNSSEC DS record rejected at registrar.** IONOS may take hours. Not urgent; DNSSEC is nicety.

**Backend reports `db: unreachable` in prod.** Same causes as Phase 0: PG18 stopped on NUC, `pg_hba.conf` reset, `.env` wrong password. See memory `wsl_docker_pg_self_nat.md`.

**Google OAuth login fails at CF Access.** Check Google Cloud Console OAuth client — Authorized redirect URI must exactly match what CF showed when adding the IdP.

**`pre-commit autoupdate` can't reach gitleaks repo.** Network blip or GitHub rate-limited. Retry.

**Image digests outdated.** Re-run `docker pull redis:7-alpine && docker inspect ...` to get current digests; update `docker-compose.prod.yml`.

---

*End of plan.*
