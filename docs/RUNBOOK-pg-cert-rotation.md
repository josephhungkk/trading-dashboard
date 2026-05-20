# RUNBOOK: PG Cert Rotation + APP_SECRET_KEY Rotation

## PG Client Cert Rotation

### WSL dev cert rotation
1. `rm ~/.dashboard-pg-ca/client.*`
2. `bash scripts/pg-cert/generate-client-cert.sh`
3. Update `PG_SSL_CERT_PATH` / `PG_SSL_KEY_PATH` in `.env`
4. `docker compose restart backend scheduler`

### NUC prod cert rotation
1. On NUC: `Remove-Item C:\dashboard\pg-cert\client.*`
2. On NUC: `pwsh scripts/pg-cert/generate-client-cert.ps1`
3. Transfer `client.key` + `client.crt` to VPS via WireGuard SSH:
   `scp -P 2222 C:\dashboard\pg-cert\client.* trader@88.208.197.219:/run/secrets/`
4. SSH to VPS: `docker compose restart backend scheduler`

## APP_SECRET_KEY Rotation (6-step procedure)

1. Generate new key: `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
2. Set in `.env` on VPS (both `backend` and `scheduler`):
   - `APP_SECRET_KEY_OLD=<current_value>`
   - `APP_SECRET_KEY=<new_value>`
3. Restart both containers: `docker compose restart backend scheduler`
   - Both containers now decrypt with either key; encrypt with new key.
4. Run: `DATABASE_URL=... APP_SECRET_KEY=<new> APP_SECRET_KEY_OLD=<old> python scripts/reencrypt-app-secrets.py`
   - Interrupted run is safe — re-run to resume.
5. Remove `APP_SECRET_KEY_OLD` from `.env` on VPS in both containers. Restart both.
6. Verify: `GET /api/admin/secrets` for each namespace returns expected values.

## PG Connection Budget

At `UVICORN_WORKERS=4`:

| Container | Processes | Pool | Max overflow | Peak conns |
|---|---|---|---|---|
| `backend` (N=4) | 4 | `POSTGRES_POOL_SIZE` (default 5) | 5 | 4 × 10 = 40 |
| `scheduler` (N=1) | 1 | `POSTGRES_POOL_SIZE_SCHEDULER` (default 10) | 5 | 15 |
| **Total** | | | | **≤ 55** |

PG18 `max_connections` default = 100. Verify before raising workers:
```
psql -U postgres -c 'SHOW max_connections;'
```

## Rollback Procedure (cert auth wedge)

1. SSH to NUC → uncomment `scram-sha-256` line in `pg_hba.conf` → `pg_ctl reload`
2. VPS: set `PG_SSL_CERT_PATH=` (empty) in `.env` → `docker compose restart backend scheduler`
3. Time to recovery: < 2 minutes. No migration required.
