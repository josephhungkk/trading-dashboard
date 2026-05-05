# Network topology + dev-host paths

Moved out of CLAUDE.md (token-budget hygiene). Claude pulls this on demand.

## WireGuard / LAN topology

| Node | Role | LAN IP | WG IP |
|---|---|---|---|
| IONOS VPS | Prod HTTP host | 88.208.197.219 | 10.10.0.1 |
| NUC15PRO | **Dev host + brokers + Postgres + light Ollama (24/7)** | 192.168.50.20 | 10.10.0.2 |
| Heavy AI box | Large Ollama + ML training (WoL) | 192.168.50.30 | 10.10.0.3 |
| Router | | 192.168.50.1 | 10.10.0.254 |

**NUC = dev host.** Claude runs in WSL2 at `/home/joseph/dashboard` (native
ext4; moved 2026-04-24 from `C:\dashboard`/`/mnt/c/dashboard` for HMR).
No separate Windows dev box. Docker = docker-ce in WSL.

SSH VPS: `ssh -p 2222 trader@88.208.197.219`.

## Postgres connectivity (dev)

WSL Docker containers reach PG at `10.10.0.2:5432` (WG IP), NOT
`host.docker.internal`:

1. docker-ce in WSL doesn't tunnel `host.docker.internal` → resolves to
   bridge gateway `172.17.0.1`.
2. Container egress to `10.10.0.2:5432` is SNATed; PG sees source =
   `10.10.0.2`. `pg_hba.conf` must include
   `host all trader 10.10.0.0/24 scram-sha-256`.

## Project paths

- **NUC dev:** `/home/joseph/dashboard` (native WSL2 ext4) — claude,
  pnpm, docker, deploy.sh.
- **VPS prod:** `/home/trader/trading-dashboard` (rsync target).

## Third-party services live OUTSIDE the repo

| Service | NUC path |
|---|---|
| IB Gateway | `C:\Jts\ibgateway\<version>\` |
| FutuOpenD | `C:\FutuOpenD\` |
| PostgreSQL 18 | `C:\Program Files\PostgreSQL\18\` |
| Ollama | `%LOCALAPPDATA%\Programs\Ollama\` |

Ops glue (PowerShell + VBS) runs from Windows-side mirror at
`C:\dashboard\deploy\nuc\*` so Scheduled Tasks find `.ps1`/`.vbs`. Not in
Docker build, not rsync'd to VPS. **Phase 4+ TODO:** sync
`/home/joseph/dashboard/deploy/nuc/` ↔ `C:\dashboard\deploy\nuc\`.
