# Phase 1 — VPS Cutover & Security Hardening — Design Spec

- **Status:** Design-approved 2026-04-21. Ready for implementation plan (`superpowers:writing-plans`).
- **Owner:** Joseph Hung (GitHub: `josephhungkk`).
- **Parent roadmap:** Phase 0 (repo scaffold, v0.0.1) complete; Phases 2–9 follow after Phase 1.
- **Supersedes:** Phase 0 spec §12 item 8 ("Phase 0 touches no live services; cutover is a later deliberate phase") — Phase 1 IS the cutover. See memory `phase1_cutover_decision.md`.

---

## 1. Scope & intent

Phase 1 takes the new rebuild (currently v0.0.1 on GitHub, only runnable locally) and replaces the existing live `Dashboard_old` deployment entirely. Output: `https://dashboard.kiusinghung.com` serves the new stack, gated by Cloudflare Access with Google login, with no direct-IP bypass possible, and with a verified post-deploy smoke check.

This is a full-stop cutover: the old stack is torn down, the legacy `trading` DB is dropped, all Cloudflare config is recreated from scratch, and the new architecture is a Cloudflare-Tunnel-fronted, defense-in-depth-hardened stack.

### What Phase 1 ships

1. **Live production deployment** at `https://dashboard.kiusinghung.com` serving the Phase 0 `/health`-only app.
2. **Cloudflare Tunnel** terminating at nginx on the VPS loopback — no public 80/443 ports anywhere.
3. **Cloudflare Access** with two allowed Google accounts + service-token bypass for CI.
4. **Multi-layer hardening**: IONOS firewall + UFW + fail2ban + nginx security headers + CF WAF + CF Bot Fight + container non-root + pinned image digests + secret scanning + dep audits.
5. **Real `scripts/deploy.sh`** replacing the Phase 0 stub. rsync + remote `docker compose up -d --build` + post-deploy Playwright smoke check.
6. **GitHub Actions deploy workflow** (`deploy.yml`) triggered on push-to-main.
7. **Idempotent Cloudflare config scripts** (`scripts/cloudflare/*.sh`) that drive CF via the CF API (primary) + CF MCP (where available at impl time).
8. **Spec + implementation plan** committed to `docs/superpowers/`.

### What Phase 1 does NOT ship

- Admin UI / DB-backed config / `app_config` + `app_secrets` tables / JWT auth — Phase 2.
- Any broker code — Phases 4–8 (BrokerAdapter base lands with first concrete adapter, not speculatively).
- Backups / uptime monitoring / log aggregation — Phase 7+.
- WireGuard peer config changes — verified but untouched.
- `deploy/vps/restart-server-agent.py` from `Dashboard_old` — port later when admin API exists.
- Any NUC-side changes to the live broker ops glue in `C:\Dashboard_old\deploy\nuc\*` — those stay active on the NUC, untouched.

---

## 2. Decisions locked in (from brainstorming)

| Area | Decision | Notes |
|---|---|---|
| **Cutover shape** | Full replacement (option **C** from brainstorming). No parallel coexistence. | Reverses Phase 0 `infra) X` decision. |
| **Target domain** | `dashboard.kiusinghung.com` (same as old). | No subdomain split. Same deploy path `/home/trader/trading-dashboard`. |
| **Apex + www** | **No DNS record** for `kiusinghung.com` or `www.kiusinghung.com` (option **A**). | Strangers get NXDOMAIN; maximum privacy. |
| **VPS ↔ Internet** | **Cloudflare Tunnel** (option **A** Q1). No public 80/443 ports. certbot eliminated entirely. | Direct-IP bypass closed. |
| **Tunnel termination** | **α** — Tunnel → nginx on `127.0.0.1:80` → backend/frontend containers. Nginx stays as defense-in-depth (headers, rate limits, gzip). | |
| **Access gate** | Cloudflare Access with Google personal-account IdP. | |
| **Allowed emails** | `josephhungkk@gmail.com`, `ispyling@gmail.com`. Two only. | |
| **Dev bypass** | Option **C** — CF Access service token (CI) + WireGuard direct (NUC-local dev). | |
| **CF automation** | Option **B** — CF MCP primary + `curl` API scripts where MCP coverage is incomplete. Package TBD at impl time (several CF MCPs exist). | |
| **IONOS firewall** | User-managed change at end of cutover: remove 80, 443, 8443, 8447; keep 51820 + 2222. Not automatable — user clicks through IONOS panel. | |
| **UFW on VPS** | default-deny inbound, allow 2222/tcp + 51820/udp. | Belt with IONOS. |
| **fail2ban** | SSH jail, 3-strike / 1-hour ban. | |
| **Legacy DB** | `dropdb trading` on NUC PG18 during cutover. Only `dashboard` DB remains. | Irreversible. |
| **Certbot** | Removed entirely. CF Tunnel handles TLS at edge. | Simpler ops. |
| **Nginx** | Kept, but stripped of cert-reload logic. Listens only on `127.0.0.1:80`. | |
| **Secret scanning** | `gitleaks` pre-commit hook + CI. | |
| **Dep audits** | `pnpm audit --audit-level=high` + `uv pip audit --strict` in CI. Fails on high/critical. | |
| **Post-deploy gate** | Playwright smoke test hitting the URL via service token; Lighthouse audit with baselines. | |
| **Rollback contingency** | `C:\Dashboard_old\` on NUC kept as-is during and after Phase 1. | Available if cutover fails catastrophically. |

---

## 3. Architecture

```
                  Internet
                     │
                     ▼
         ┌───────────────────────┐
         │      Cloudflare       │
         │  edge (global PoPs)   │
         │                       │
         │  • TLS termination    │
         │  • DDoS / WAF / rate  │
         │  • Bot Fight Mode     │
         │  • AI-scraper block   │
         │  • CF Access gate:    │
         │    - Google OAuth     │
         │      for 2 emails,    │
         │    - OR service token │
         │      for CI bypass    │
         └──────────┬────────────┘
                    │
                    │  CF Tunnel (outbound-initiated,
                    │  persistent QUIC/HTTP2 on :7844
                    │  from VPS → CF edge)
                    │
                    ▼
  ┌─────────────────────────────────────────┐
  │  IONOS VPS  88.208.197.219              │
  │  Public ports: 2222/tcp (SSH) + 51820/udp (WG) only
  │                                         │
  │  ┌────────────┐                         │
  │  │ cloudflared│  systemd service        │
  │  │  (daemon)  │  /etc/cloudflared/*     │
  │  └──────┬─────┘                         │
  │         │ forwards to loopback          │
  │         ▼                               │
  │  ┌────────────┐                         │
  │  │   nginx    │  binds 127.0.0.1:80     │
  │  │ (container)│  + security headers     │
  │  │            │  + rate limits          │
  │  │            │  + gzip / caching       │
  │  │            │  + Host-match drop      │
  │  └──┬──────┬──┘                         │
  │     │      │                            │
  │     │      └─→ frontend (container)     │
  │     ▼                                   │
  │  ┌─────────┐   ┌────────┐               │
  │  │ backend │──→│  redis │               │
  │  │ (uvicorn│   │ (cache)│               │
  │  │  :8000) │   └────────┘               │
  │  └────┬────┘                            │
  │       │                                 │
  │       │  PG over WireGuard              │
  │       ▼                                 │
  └───────┼─────────────────────────────────┘
          │   wg0 interface
          │   VPS 10.10.0.1 ↔ NUC 10.10.0.2
          ▼
  ┌────────────────────────────┐
  │  NUC15PRO  10.10.0.2       │
  │  • native Windows PG18     │
  │    — `dashboard` DB only   │
  │    — `trading` DB dropped  │
  │  • IB Gateway × 4 accounts │
  │  • FutuOpenD               │
  │  • light Ollama            │
  │  • broker TOTP secrets     │
  │  • C:\Dashboard_old\ kept  │
  │    as rollback reference   │
  └────────────────────────────┘
```

### Access paths

| Who | How | Auth |
|---|---|---|
| Human user (2 allowed emails) | `https://dashboard.kiusinghung.com` via browser | Google login (CF Access) |
| CI (GitHub Actions smoke test) | Same URL + `CF-Access-Client-Id` + `CF-Access-Client-Secret` headers | CF Access service token |
| Dev (on NUC, local iteration) | `http://10.10.0.1/` over WireGuard — reaches nginx's wg0-interface bind (see §6 docker-compose dual port binding) | None (WG peer trust; UFW allows `in on wg0 to any port 80`) |
| VPS ops (SSH'd in, debugging) | `curl http://localhost/` | None (loopback) |
| Stranger with VPS IP | `curl https://88.208.197.219` | **Connection timeout** — IONOS + UFW drop it, no inbound 443 |
| Bare apex/www | `kiusinghung.com`, `www.kiusinghung.com` | **NXDOMAIN** — no DNS record |

### Invariants the architecture enforces

1. No direct-IP bypass. VPS drops all inbound HTTP/S at IONOS + UFW.
2. CF Access is the only auth gate for humans.
3. Service token bypass is CI-scoped; WG bypass is dev-scoped.
4. Postgres never leaves the NUC.
5. Redis is cache-only (no persistence volume in Phase 1).
6. Nginx is defense-in-depth; even if CF is misconfigured, nginx refuses non-`dashboard.kiusinghung.com` `Host:` headers.

---

## 4. Cutover sequence

Exact order. Each step is reviewable; if any step fails, stop and investigate before advancing.

```
0.  PRE-CHECK (user): note the current Let's Encrypt cert expiry in case rollback is needed:
                 openssl s_client -connect dashboard.kiusinghung.com:443 2>/dev/null \
                   | openssl x509 -noout -dates
                 If <30 days to expiry, renew the OLD cert first (in the Dashboard_old
                 certbot container) so rollback remains viable. Proceed once cert is
                 safe or decision is made to accept no-rollback.

1.  SSH to VPS:  cd /home/trader/trading-dashboard ; docker compose down -v
2.  SSH to VPS:  rm -rf /home/trader/trading-dashboard

3.  Drop legacy `trading` DB. The `postgres` superuser is local-only on Windows PG18;
    `pg_hba.conf` allows `trader` over WG, but `trader` may not own `trading`.
    Two working options — pick whichever is easier:
     a) Run locally on the NUC (Windows PowerShell or WSL localhost):
        psql -U postgres -h localhost -c 'DROP DATABASE trading;'
     b) Transfer ownership first, then drop over WG:
        psql -h 10.10.0.2 -U postgres -c 'ALTER DATABASE trading OWNER TO trader;'
        (requires a `local all postgres md5` line in pg_hba.conf OR run option (a) for
        the ALTER too)
        psql -h 10.10.0.2 -U trader -d postgres -c 'DROP DATABASE trading;'

4.  From NUC:    scripts/cloudflare/99-teardown.sh
                 — deletes old DNS record for dashboard; deletes old CF Access app.
                 Script scopes to resources matching name "dashboard" + the legacy
                 Access app UUID captured via a `list → filter by name` API call;
                 never touches the new Phase-1 resources.
5.  From NUC:    scripts/cloudflare/00-check-token.sh
                 — verifies CF_API_TOKEN env var has the needed scopes before committing.
6.  From NUC:    scripts/cloudflare/10-tunnel-create.sh
                 — creates Tunnel named "dashboard-prod"; writes credentials JSON
                 to ~/.secrets/cloudflared-<TUNNEL-UUID>.json (mode 0600, owner NUC user).
                 NEVER writes to /tmp (world-readable).
7.  From NUC:    scripts/cloudflare/11-dns-cname.sh
                 — creates CNAME dashboard.kiusinghung.com → <TUNNEL-UUID>.cfargotunnel.com,
                 proxied: true.
8.  From NUC:    scripts/cloudflare/20-access-app.sh
                 — creates Zero Trust Access app for dashboard.kiusinghung.com.
9.  From NUC:    scripts/cloudflare/21-access-policy-google.sh
                 — creates "allow Google emails" policy with the 2-email allowlist.
10. From NUC:    scripts/cloudflare/22-access-policy-bypass.sh
                 — creates "bypass for service token" policy.
11. From NUC:    scripts/cloudflare/23-service-token.sh
                 — creates service token; prints client-id + client-secret ONCE.
                 User saves both to GitHub secrets + local .env.
12. From NUC:    scripts/cloudflare/30-security-hardening.sh
                 — enables Bot Fight Mode + Block AI Scrapers + DNSSEC + Always Use HTTPS.

13. SSH to VPS:  deploy/vps/install-prep.sh   (part 1 of 2)
                 — adds CF apt repo (cloudflared is NOT in default Ubuntu apt):
                     curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
                       | sudo gpg --dearmor -o /usr/share/keyrings/cloudflare-main.gpg
                     echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] \
                       https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" \
                       | sudo tee /etc/apt/sources.list.d/cloudflared.list
                 — apt update; apt install cloudflared ufw fail2ban;
                 — configure UFW (default deny in, allow 2222/tcp, 51820/udp, 80 on wg0);
                 — enable fail2ban with SSH jail.
                 DOES NOT start cloudflared yet (service left disabled).

14. SSH to VPS:  harden sshd_config (PasswordAuth no, PubkeyAuth yes, PermitRootLogin no,
                 AllowUsers trader, MaxAuthTries 3, ClientAlive* set). Require
                 password for sudo (not passwordless).
                 Verify: sshd -t before reload.
                 Reload: systemctl reload sshd.
                 SAFETY: open a second SSH session and confirm it works BEFORE
                 closing the first. Do not proceed if the second login fails.

15. From NUC:    VPS_PATH=/home/trader/trading-dashboard scripts/deploy.sh
                 — rsync new repo to VPS; ssh + docker compose up -d --build;
                 remote `curl http://127.0.0.1/health` sanity check INSIDE VPS.
                 Backend + frontend + redis + nginx all up. nginx binds
                 127.0.0.1:80 AND 10.10.0.1:80 (dual-homed for Tunnel + WG dev).

16. SSH to VPS:  deploy/vps/install-enable.sh   (part 2 of 2, run ONLY after 15 green)
                 — drop credentials JSON to /etc/cloudflared/<UUID>.json
                   (SCP from NUC ~/.secrets/cloudflared-<UUID>.json, mode 0600, root:root);
                 — write /etc/cloudflared/config.yml with ingress to
                   http://127.0.0.1:80;
                 — systemctl enable --now cloudflared.
                 Now and only now is the domain live end-to-end.

17. From NUC:    cd frontend && pnpm install --frozen-lockfile
                 pnpm exec playwright install chromium --with-deps
                 (Playwright chromium binary is NOT shipped with pnpm install; required
                 for the smoke test. One-time ~300 MB download per machine; cached.)

18. From NUC:    cd tests/e2e && pnpm exec playwright test smoke.spec.ts
                 — smoke test via service token.
                 Asserts 200 + "Backend: ok" + Lighthouse baselines.

19. Manual verify (user): open https://dashboard.kiusinghung.com in browser,
                          complete Google login, see "Backend: ok" page.

—— new stack verified working ——

20. USER ACTION: log into IONOS control panel → Server → Network → Firewall policy:
                 - Remove:  TCP 80, TCP 443, TCP 8443, TCP 8447
                 - Keep:    UDP 51820, TCP 2222
                 Save.
21. From NUC:    curl -vk --connect-timeout 5 https://88.208.197.219 → MUST time out.
                 curl -vk --connect-timeout 5 http://88.208.197.219  → MUST time out.
                 If anything responds, IONOS rules didn't save — go back to step 20.
22. Manual verify (user): domain still works via browser after IONOS change.
23. From NUC:    Commit the new spec + plan + all Phase 1 artifacts to main.
                 Tag v0.1.0.
```

Total downtime: from step 1 to step 19, ~20–40 min. Domain returns CF's error page between step 1 and step 16 (Tunnel enable), then returns the new stack.

**Cutover ordering invariant (per architect review):** Tunnel is enabled (step 16) only AFTER the new stack is deployed and verified green (step 15). This eliminates the "domain returns 502 because backend isn't up yet" window.

---

## 5. Repo artifacts (new files in `/mnt/c/dashboard/`)

```
scripts/
├── deploy.sh                       REPLACE Phase 0 stub — real rsync + remote compose up
└── cloudflare/
    ├── 00-check-token.sh
    ├── 10-tunnel-create.sh
    ├── 11-dns-cname.sh
    ├── 12-tunnel-config.sh         (optional — write VPS-side config locally for review)
    ├── 20-access-app.sh
    ├── 21-access-policy-google.sh
    ├── 22-access-policy-bypass.sh
    ├── 23-service-token.sh
    ├── 30-security-hardening.sh
    ├── 40-smoke-from-ci.sh         (helper invoked by GitHub Actions)
    ├── 99-teardown.sh              (idempotent — for rollback or retry)
    └── README.md                   (order + env-var list + scopes needed)

deploy/vps/
├── install.sh                      one-shot bootstrap
├── cloudflared.service             systemd unit template
├── cloudflared-config.yml.template ingress config template
├── ufw-rules.sh                    idempotent UFW config
├── sshd-hardening.sh               idempotent sshd_config edits with backup + sshd -t
└── README.md

nginx/
├── nginx.conf                      REPLACE — ported from Dashboard_old, certbot stripped
├── conf.d/
│   └── dashboard.conf              server block; listens 127.0.0.1:80 only
└── start.sh                        simpler — no cert-reload watcher

docker-compose.prod.yml             NEW — production variant:
                                    • nginx DUAL-bound via compose ports:
                                      - "127.0.0.1:80:80"   (cloudflared → loopback)
                                      - "10.10.0.1:80:80"   (WG dev bypass from NUC)
                                      Inside the container nginx listens on 0.0.0.0:80;
                                      Docker's port binding does the interface filtering.
                                    • no port exposure on redis/backend/frontend
                                    • backend CMD drops `--reload` (prod-mode uvicorn)
                                    • healthchecks on all services
                                    • non-root users (uid 1000)
                                    • resource limits (cpu/mem)
                                    • cap_drop: [ALL], no-new-privileges
                                    • read_only: true where possible, PLUS per-service tmpfs:
                                      - nginx:  /var/log/nginx (20m), /var/cache/nginx
                                                (20m), /var/run (1m)
                                      - frontend (if dev-mode): /tmp, /.vite
                                      - backend: /tmp
                                    • pinned image digests (not :latest)
                                    • json-file log driver with max-size + max-file

tests/e2e/
├── smoke.spec.ts                   Playwright smoke: service token, 200, "Backend: ok",
│                                   Lighthouse baselines (perf 85, a11y 95, best-practices 95)
├── playwright.config.ts
└── package.json                    (workspace under frontend/ or top-level — impl choice)

.github/workflows/
└── deploy.yml                      NEW — on push-to-main: rsync + build + smoke test.
                                    Secrets: VPS_SSH_KEY, CF_ACCESS_CLIENT_ID,
                                    CF_ACCESS_CLIENT_SECRET.

.github/workflows/ci.yml            MODIFY — add `pnpm audit --audit-level=high` +
                                    `uv pip audit --strict` steps; fail on high/critical.

.pre-commit-config.yaml             MODIFY — add gitleaks secret-scan hook.

frontend/index.html                 MODIFY — add <meta name="robots" content="noindex, nofollow">.

CLAUDE.md                           MODIFY — document post-cutover state:
                                    - certbot removed; CF Tunnel handles TLS
                                    - IONOS firewall reduced to 2 ports
                                    - CF Access Google + service token paths
                                    - dev bypass via WG

CHANGELOG.md                        APPEND [0.1.0] — 2026-04-21 entry.

TASKS.md                            MODIFY — check off Phase 1; Phase 2 becomes current.
```

### Env-var contract for the new stack (`.env` on VPS)

Same 8 bootstrap vars as Phase 0, production values:
```
APP_ENV=prod
APP_SECRET_KEY=<openssl rand -base64 32>
APP_CORS_ORIGINS=["https://dashboard.kiusinghung.com"]
DATABASE_URL=postgresql+asyncpg://trader:<pw>@10.10.0.2:5432/dashboard
POSTGRES_POOL_SIZE=10
POSTGRES_MAX_OVERFLOW=20
REDIS_PASSWORD=<openssl rand -base64 24, URL-safe>
REDIS_URL=redis://:<same>@redis:6379/0
```

`APP_ENV=prod` triggers structlog JSON output and disables debug features in later phases.

### Secret flow

| Secret | Generated | Stored | Consumed |
|---|---|---|---|
| CF API token | You, in CF dashboard (browser, one-time) | `~/.secrets/cloudflare.token` (NUC, mode 0600), `gh secret` (CI) | `scripts/cloudflare/*.sh` reads `$CF_API_TOKEN` |
| CF Tunnel credentials | `10-tunnel-create.sh` | `/etc/cloudflared/<UUID>.json` (VPS, mode 0600) | `cloudflared` systemd |
| CF Access service token | `23-service-token.sh` | `gh secret set CF_ACCESS_CLIENT_ID`/`_SECRET`, local dev `.env` | CI workflow, Playwright tests |
| `APP_SECRET_KEY` | `openssl rand -base64 32` | VPS `/home/trader/trading-dashboard/.env` (0600) | Backend container |
| PG `trader` password | Already set on NUC | VPS `.env` | Backend |
| `REDIS_PASSWORD` | `openssl rand -base64 24` | VPS `.env` | Redis + backend |

Nothing secret ever enters git history.

### VPS artifacts (created by `deploy/vps/install.sh`)

```
/etc/systemd/system/cloudflared.service     enabled + started
/etc/cloudflared/config.yml                 ingress config
/etc/cloudflared/<TUNNEL-UUID>.json         credentials (mode 0600, root:root)
/etc/ufw/                                   firewall rules (allow 2222 + 51820)
/etc/fail2ban/jail.local                    SSH jail config
/home/trader/trading-dashboard/             fresh rsync
/home/trader/trading-dashboard/.env         production env (not rsync'd — created by install)
```

---

## 6. Security hardening

### Network layer
- **IONOS firewall:** only 51820/udp + 2222/tcp after cutover. User-managed.
- **UFW on VPS:** default-deny inbound; allow 2222/tcp + 51820/udp. Belt with IONOS.
- **fail2ban:** SSH jail, `maxretry 3`, `bantime 1h`.
- **WireGuard:** existing mesh untouched; verify AllowedIPs are minimal.
- **No public 80/443:** cloudflared dials out on tcp/7844 to CF edge.

### SSH layer (`/etc/ssh/sshd_config` — `deploy/vps/sshd-hardening.sh`)
```
Port 2222
PasswordAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
AllowUsers trader
MaxAuthTries 3
ClientAliveInterval 60
ClientAliveCountMax 3
UsePAM yes
```

### Nginx layer

**`nginx/nginx.conf` http{} block** must include real-IP + rate-limit zones at the global level so `dashboard.conf` can reference them:
```nginx
http {
    # ... (default settings, mime types, etc.)

    # Trust cloudflared on loopback; use CF-Connecting-IP as the real client IP
    # set by the CF edge. Without this, every request appears to come from
    # 127.0.0.1 and rate limits + access logs become useless.
    set_real_ip_from 127.0.0.1;
    real_ip_header CF-Connecting-IP;
    real_ip_recursive on;

    # Rate limit zones (bind to $binary_remote_addr which is now the real IP)
    limit_req_zone $binary_remote_addr zone=api:10m     rate=10r/s;
    limit_req_zone $binary_remote_addr zone=general:10m rate=30r/s;

    # Logging includes the real IP for forensics
    log_format main '$remote_addr - $remote_user [$time_local] '
                    '"$request" $status $body_bytes_sent '
                    '"$http_referer" "$http_user_agent"';
    access_log /var/log/nginx/access.log main;

    server_tokens off;

    # include site configs
    include /etc/nginx/conf.d/*.conf;
}
```

**`nginx/conf.d/dashboard.conf`** server block:
```nginx
server {
    listen 80 default_server;
    # (Docker compose port binding limits what interfaces 0.0.0.0:80 is reachable from:
    # "127.0.0.1:80:80" for the Tunnel + "10.10.0.1:80:80" for the WG dev path.)
    server_name dashboard.kiusinghung.com;

    # Drop requests with any other Host: header without responding
    if ($host != "dashboard.kiusinghung.com") { return 444; }

    # Hide version
    server_tokens off;

    # Security headers (applied to all responses)
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Robots-Tag "noindex, nofollow, noarchive, nosnippet" always;
    add_header Referrer-Policy "no-referrer" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=(), usb=(), magnetometer=(), accelerometer=(), gyroscope=()" always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" always;

    # Body size limit
    client_max_body_size 1m;

    # Rate limits (zones defined in nginx.conf http{} block)
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

    location / {
        limit_req zone=general burst=60 nodelay;
        proxy_pass http://frontend;
        proxy_set_header Host $host;
        proxy_connect_timeout 5s;
        proxy_read_timeout 30s;
    }
}
```

### Cloudflare edge
- CF Access: Google IdP + 2-email allowlist policy + service-token bypass policy.
- CF WAF: Managed Ruleset enabled (OWASP Core Rules).
- Bot Fight Mode: on.
- Block AI Scrapers: on.
- TLS: minimum 1.2, prefer 1.3.
- Always Use HTTPS: on.
- HSTS: on (CF edge + nginx reinforce).
- DNSSEC: on.
- Security Level: high.
- Challenge Passage: 30 min.

### Container layer (`docker-compose.prod.yml`)
- All services: `USER appuser` (uid 1000) in Dockerfile.
- `read_only: true` where possible; `tmpfs: [/tmp, /var/cache/nginx]`.
- `cap_drop: [ALL]`, no additions.
- `security_opt: ["no-new-privileges:true"]`.
- `healthcheck:` on every service.
- `restart: unless-stopped`.
- `mem_limit: 512m` (backend), `1g` (frontend), `256m` (redis), `128m` (nginx).
- `cpus: "1.0"`.
- Image digests pinned (exact SHA) — NOT `:latest`. Pinned at impl time via `docker buildx imagetools inspect`.
- Log driver: `json-file` with `max-size: 10m, max-file: 3`.
- No port exposure except nginx 127.0.0.1:80; redis is container-only.

### Secrets layer
- `.env` on VPS: `0600`, owned by `trader:trader`.
- `gitleaks` pre-commit hook: scans every commit for AWS keys, GitHub tokens, generic API keys, CF tokens, private keys.
- GitHub Actions: `gitleaks` workflow runs on every PR.
- `.env.example` never contains real values (placeholder strings only).

### Dependency hygiene
- `pnpm audit --audit-level=high` in CI — fails on high/critical.
- `uv pip audit --strict` in CI — fails on any known vuln.
- Dependabot: enabled (auto-PR for upgrades).

### App layer (Phase 1 scope)
- Structlog JSON output in prod (`APP_ENV=prod`).
- Secret-redaction processor already in place from Phase 0 (Phase 2 expands redaction patterns).
- Backend `/health` endpoint returns DB probe status (already shipped in Phase 0).
- No app-level auth in Phase 1 — relies entirely on CF Access. Phase 2 adds admin-role middleware.

### Audit / observability (Phase 1 scope)
- `docker compose logs` is the inspection tool. Logs live on VPS; max 30 MB per service (3 × 10 MB).
- nginx access log includes `CF-Connecting-IP` header for real client IPs.
- cloudflared logs to journald via systemd.
- No log aggregation or alerting in Phase 1 — Phase 7+.

---

## 7. Verification gate (Phase 1 "done")

All of the following MUST pass after the cutover sequence:

### A. Access validation
```bash
# Human path (manual)
Browser → https://dashboard.kiusinghung.com
Expect: 302 → Google login → after auth, page renders "Backend: ok"

# CI/service-token path
curl -sf https://dashboard.kiusinghung.com/health \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET"
Expect: {"status":"ok","env":"prod","db":"ok"}

# WG dev path (from NUC)
curl -sf http://10.10.0.1/health
Expect: {"status":"ok","env":"prod","db":"ok"}   (no auth)

# Direct-IP bypass MUST fail
curl -vk --connect-timeout 5 \
  --resolve dashboard.kiusinghung.com:443:88.208.197.219 \
  https://dashboard.kiusinghung.com/
Expect: connection timeout or refused

# Apex + www MUST NOT resolve
dig +short kiusinghung.com          → empty
dig +short www.kiusinghung.com      → empty
```

### B. Security header spot-check
```bash
curl -sI https://dashboard.kiusinghung.com \
  -H "CF-Access-Client-Id: …" -H "CF-Access-Client-Secret: …"

Expect response headers include:
  Strict-Transport-Security: max-age=31536000; includeSubDomains; preload
  X-Frame-Options: DENY
  X-Content-Type-Options: nosniff
  X-Robots-Tag: noindex, nofollow, noarchive, nosnippet
  Referrer-Policy: no-referrer
  Content-Security-Policy: default-src 'self'; ...
  Permissions-Policy: camera=(), microphone=(), ...
```

### C. Playwright smoke (`tests/e2e/smoke.spec.ts`)
- Visits URL with service token.
- Asserts `<title>Trading Dashboard</title>`.
- Asserts "Backend: ok" text present.
- Screenshot saved to CI artifacts.
- Lighthouse: perf ≥ 85, a11y ≥ 95, best-practices ≥ 95.

### D. Negative tests
```
curl https://dashboard.kiusinghung.com/health           → 302 to CF Access login
curl https://dashboard.kiusinghung.com/ -H "CF-Access-Client-Id: wrong" -H "CF-Access-Client-Secret: wrong"
                                                        → 403 Forbidden
nmap -p- 88.208.197.219                                 → only 2222/tcp + 51820/udp
```

### E. CI validation
- Push a trivial PR: `deploy.yml` dry-run (rsync dry-run, smoke prep) green.
- Merge to main: full deploy + smoke check green.
- `pnpm audit` + `uv pip audit` green (or flagged advisories acknowledged).
- `gitleaks` finds nothing.

### F. Rollback check (readiness, not destructive)
Confirm `C:\Dashboard_old\` is still intact on the NUC (spot-check a few files exist). Memory `dashboard_old_reuse_inventory.md` and `infra_state.md` still resolve. No accidental deletion of the v1 tree.

---

## 8. Rollback plan

### During cutover (before step 20 IONOS change)
- CF Tunnel setup fails: `scripts/cloudflare/99-teardown.sh` removes what was created. Restart at step 4.
- CF Access misconfig (you get locked out): add an IP-allowlist bypass policy in the CF dashboard for your home IP. Fix the Google policy later.
- rsync fails: `ssh vps 'ls /home/trader/trading-dashboard'` confirms state; re-run `deploy.sh`.
- Compose up fails: `ssh vps 'cd /home/trader/trading-dashboard && docker compose logs'` shows errors. Fix, re-run.
- Smoke test fails post-deploy: `docker compose down` on VPS. Old state (dropped `trading` DB, dropped repo tree) is already gone — but no partial-state exposure because CF Access still blocks humans even when upstream is broken.

### Catastrophic — Phase 1 fundamentally broken
1. Add IONOS rules back (TCP 80 + 443): ~30 sec in the panel.
2. Disable UFW on VPS: `sudo ufw disable`.
3. Rsync `/mnt/c/Dashboard_old/` → VPS at `/home/trader/trading-dashboard`: ~3 min.
4. **Check Let's Encrypt cert expiry BEFORE bringing nginx up:**
   ```
   ssh vps 'docker run --rm \
     -v $(pwd)/certbot-certs:/etc/letsencrypt:ro \
     certbot/certbot:latest certificates'
   ```
   If any cert is within 30 days of expiry or already expired:
   ```
   ssh vps 'docker compose run --rm certbot renew \
     --dns-cloudflare \
     --dns-cloudflare-credentials /etc/letsencrypt/cloudflare.ini'
   ```
   (Assumes the old certbot sidecar config is still in `docker-compose.yml` and
   the `cloudflare.ini` credentials file is still on the VPS. If those are gone
   from the VPS, rsyncing `Dashboard_old` in step 3 brings them back.)
5. `docker compose up -d` old stack: ~5 min.
6. Re-point CF DNS for `dashboard.kiusinghung.com` from CNAME-to-tunnel back to
   A-record-to-VPS-IP: 1 min via CF dashboard or MCP.
7. Old domain returns to old behavior.

Total RTO: ~15–20 min (add 2–5 min if cert renewal is needed).

**Pre-cutover guarantee** (step 0 in §4): user captures cert expiry date BEFORE
teardown, so we know up-front whether rollback requires renewal.

**Irrecoverable losses in this rollback:**
- The legacy `trading` DB is dropped (decided at step 3 of cutover). Old admin token + Schwab refresh token + any stored positions are gone. Schwab OAuth must be re-done when Phase 8 lands.
- No rollback required for those losses — user accepted them at brainstorming (option C).

---

## 9. Known acceptable weaknesses

1. **Gmail password strength is your responsibility.** Enable 2FA on both `josephhungkk@gmail.com` and `ispyling@gmail.com`.
2. **CF Access service token theft** via GitHub Actions secret compromise would let attacker call all endpoints. Mitigation: scope policy, rotate yearly, enable GitHub's required review for workflow-file changes.
3. **`ispyling@gmail.com` full access** = whoever controls that account controls trading. Accepted.
4. **`trader` sudo rights on VPS.** `install.sh` configures `sudo` to REQUIRE password for sudo (not passwordless).
5. **No app-level auth in Phase 1.** Any authenticated CF Access user can hit every endpoint. Phase 2 adds role-based middleware.
6. **DNS history for `dashboard.kiusinghung.com`** is permanently public via CT logs and DNS history services. Not fixable; architecture compensates by not relying on hostname secrecy.
7. **Container read-only FS isn't universal** — frontend dev mode needs write for Vite HMR. Mitigated via specific tmpfs mounts.
8. **No backup strategy in Phase 1.** PG on NUC is NOT backed up yet. Phase 7+ adds pg_dump + off-box storage.
9. **No WAF custom rules yet** — only CF Managed Ruleset. Custom rules added later as attack patterns emerge.
10. **`cloudflared` runs as root** on VPS by default (to bind ports). Mitigation: it binds nothing public; still, running as a dedicated `cloudflared` user is a later hardening.
11. **CSP allows `style-src 'self' 'unsafe-inline'`.** Tailwind v4 needs `'unsafe-inline'` until we wire nonce-based CSP (Phase 3+ when real UI lands). Current CSP is a functional baseline for the Phase 1 health-check page, NOT the final posture. **Action:** revisit CSP in Phase 3 impl plan; add nonce support to nginx + Vite when we ship the shell.
12. **Rate limits use `$binary_remote_addr`** which, with the real_ip module wired (§6), is the CF-Connecting-IP. Still, CF itself has rate-limit rules; these nginx limits are a secondary layer. If CF's rate limiter is already strict enough we may loosen nginx's — revisit after a week of production data.

---

## Architect review — applied

This spec underwent an adversarial architecture review on 2026-04-21 (via the `ARCHITECT-REVIEW` skill). All 10 findings were applied to the spec:

| # | Severity | Finding | Where fixed |
|---|---|---|---|
| 1 | CRITICAL | WG bypass vs nginx bind-127.0.0.1 contradiction | §3 access-paths + §5 compose dual-port + §6 nginx listen |
| 2 | CRITICAL | `DROP DATABASE trading` via `postgres` over WG fails | §4 step 3 — two working paths provided |
| 3 | HIGH | `apt install cloudflared` without repo-add | §4 step 13 — explicit CF apt repo setup |
| 4 | HIGH | Missing `real_ip_header CF-Connecting-IP` | §6 nginx.conf http{} block added |
| 5 | HIGH | DNS+Tunnel active before backend deployed | §4 — reordered: rsync+compose (step 15) BEFORE cloudflared enable (step 16) |
| 6 | HIGH | Rollback cert-expiry unhandled | §4 step 0 pre-check + §8 catastrophic rollback step 4 |
| 7 | HIGH | Playwright chromium install missing from cutover | §4 step 17 added |
| 8 | MEDIUM | `read_only: true` breaks nginx | §5 compose — explicit tmpfs for nginx paths |
| 9 | MEDIUM | Tunnel credentials in `/tmp` (world-readable) | §4 step 6 — use `~/.secrets/` (0600) instead |
| 10 | MEDIUM | Over-engineered CSP with `unsafe-inline` | §9 weakness #11 added — documented as Phase-3 follow-up |

---

## 10. Out of scope (deferred)

| Item | Phase |
|---|---|
| Auth, sessions, JWT, admin-role middleware | 2 |
| `app_config` + `app_secrets` tables; DB-backed ConfigService | 2 |
| `/api/admin/config` + `/api/admin/secrets` + admin UI | 2 |
| OpenAPI → TypeScript generator | 2 |
| Noto fonts; `langForMarket()` real mapping | 3 |
| React Router + Zustand + WebSocket client | 3 |
| AppShell + Sidebar + BottomTabBar + SplitPane + Panel | 3 |
| `BrokerAdapter` abstract base class | 4 |
| IBKR adapter | 4 |
| Trade-execution endpoints + nonce/confirmation tokens | 5 |
| Futu adapter + CJK font polish | 6 |
| Alerts engine + Telegram + Ollama router + WoL | 7 |
| PG backups, log aggregation, uptime monitoring | 7 |
| `deploy/vps/restart-server-agent` from `Dashboard_old` | 7 (port when admin API exists) |
| Schwab adapter + OAuth path bypass rule | 8 |
| Bots service | 9 |

---

## 11. Deltas from prior specs / memories

1. **Phase 0 spec §12 item 8** ("Phase 0 touches no live services; cutover is a later deliberate phase") is **reversed**. Phase 1 IS the cutover. Memory `phase1_cutover_decision.md` records this.
2. **Infra memory `infra_state.md`** (dated 2026-04-17) describes the state BEFORE Phase 1 cutover. After Phase 1, the live admin token, Schwab refresh token, `trading` DB, and certbot automation described there no longer exist. Future sessions should read memory `phase1_cutover_decision.md` as the newer source of truth.
3. **CLAUDE.md "Stack" section** says "Reverse proxy: Nginx with Let's Encrypt DNS-01 via Cloudflare". After Phase 1, Let's Encrypt + certbot are gone; CF Tunnel provides TLS at edge. Nginx still exists as defense-in-depth. Update CLAUDE.md during Phase 1 impl.
4. **CLAUDE.md "Common Commands" section** should note the new deploy flow is `scripts/deploy.sh` (real) not a stub, and add the WG-bypass URL for local dev.
5. **`.env.example`** doesn't change — bootstrap var names are the same. Only the runtime values in production `.env` change.

---

## 12. Next step

After user reviews this spec:
1. Resolve any changes requested.
2. Invoke `superpowers:writing-plans` to produce `docs/superpowers/plans/2026-04-21-phase1-vps-cutover-plan.md`.
3. Plan execution (not this session) follows subagent-driven-development flow.

---

*End of spec.*
