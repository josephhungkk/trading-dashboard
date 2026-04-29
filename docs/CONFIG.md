# Configuration Storage

**The app keeps runtime settings in the database, not in `.env`.** (Active as of v0.2.0.)

## Bootstrap-only `.env` keys

`.env` only holds bootstrap values the app needs before it can reach the DB:
`APP_ENV`, `APP_SECRET_KEY`, `APP_SECRET_KEY_PREV`, `APP_CORS_ORIGINS`, `DATABASE_URL`, `POSTGRES_POOL_SIZE`, `POSTGRES_MAX_OVERFLOW`, `REDIS_PASSWORD`, `REDIS_URL`, `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUDIENCE`, `TRUSTED_DEV_NETS`.

Why these specifically:
- `POSTGRES_POOL_SIZE` / `POSTGRES_MAX_OVERFLOW` — SQLAlchemy reads them at engine construction, before `ConfigService` can reach the DB.
- `REDIS_PASSWORD` — split out from `REDIS_URL` so docker-compose can interpolate into `redis-server --requirepass ${REDIS_PASSWORD}`.
- `APP_SECRET_KEY_PREV` — set only during rotation windows; MultiFernet decrypts ciphertexts written under the old key and re-encrypts on next write.

## DB-stored settings

Everything else (broker hosts, Ollama URLs, Telegram tokens, API keys, WoL MAC, Schwab OAuth, etc.) lives in two tables:

- `app_config` — plain-text settings, readable by any admin-authed client
- `app_secrets` — sensitive values encrypted with Fernet (key derived from `APP_SECRET_KEY` via HKDF-SHA256)

Edited at runtime via `POST /api/admin/config` and `POST /api/admin/secrets` (CF Access — Google login for humans, service token for CI). An in-memory cache is invalidated across all backend workers via Redis pub/sub on every write, so changes take effect immediately.

## Reading config in code

**Do not add new values to `.env` beyond the bootstrap list.** When writing code that needs a setting, read it via the `get_config()` FastAPI dependency or the `ConfigService` singleton:

```python
from app.core.deps import get_config
svc = get_config()
heavy_url = await svc.get("ollama", "heavy_url", default="http://10.10.0.3:11434")
bot_token = await svc.reveal_secret("telegram", "bot_token")
```

Typed accessors (`get_int`, `get_bool`, `get_json`, `reveal_secret_int`, etc.) raise `ConfigTypeError` if the stored `value_type` does not match the accessor. Secret plaintext is only ever returned by `reveal_secret*`; `GET /api/admin/secrets/...` returns metadata only (namespace, key, value_type, timestamps). Every `reveal_secret*` hit increments `admin_secret_reveal_total` with the actor kind label.

## Key rotation

Rotating `APP_SECRET_KEY` invalidates all encrypted secrets — treat it as permanent. Plan a maintenance window and pre-set `APP_SECRET_KEY_PREV` to the outgoing key so reads keep working while the backend re-encrypts on each write.
