# Phase 2 — Auth + DB-backed config service (design)

**Status:** architect-review applied (pending user approval)
**Date:** 2026-04-22
**Follows:** Phase 1 `v0.1.0` (VPS cutover + CF Tunnel + CF Access, shipped 2026-04-22)
**Phase workflow step:** 1 (brainstorm) ✓ → 2 (self-review) ✓ → 3 (architect review) ✓ → 4 (user approval) **← here** → 5 (plan) → 6 (impl) → 7 (close-out)

---

## 1. Goal

Move runtime configuration out of `.env` and into two Postgres tables (`app_config` plain, `app_secrets` Fernet-encrypted), with a `ConfigService` layer that caches in-memory and invalidates across backend workers via Redis pub/sub. Protect admin-facing endpoints with CF Access JWT verification. Ship as `v0.2.0`.

## 2. Background

- Phase 1 deployed a hardened production stack (CF Tunnel + CF Access + nginx + hardened docker-compose). `.env` currently holds bootstrap values only: `APP_ENV`, `APP_SECRET_KEY`, `APP_CORS_ORIGINS`, `DATABASE_URL`, `POSTGRES_POOL_SIZE`, `POSTGRES_MAX_OVERFLOW`, `REDIS_PASSWORD`, `REDIS_URL`.
- CLAUDE.md §"Configuration Storage" commits the project to DB-backed config. Phase 2 implements that commitment.
- Backend is currently at Phase 0 scaffold + Phase 1 prod compose. `/health` is the only route. `app/api/`, `app/services/`, `app/models/` are empty packages. Alembic is wired async with `target_metadata = None`; no migrations exist yet.
- CF Access allowlist at the edge: `josephhungkk@gmail.com`, `ispyling@gmail.com`. Service token `dashboard-ci-smoke` (expires 2027-04-22) bypasses Access for CI — its JWT carries a `common_name` claim instead of `email`.

## 3. Architecture

Add a **ConfigService** sitting between FastAPI routes and the database. It owns two in-memory caches (config, secrets), reads/writes via SQLAlchemy async, and listens on a Redis pub/sub channel to invalidate sibling workers.

```
┌────────────────────────────┐       ┌────────────────────────────┐
│  FastAPI worker #1         │       │  FastAPI worker #N         │
│  ┌──────────────────────┐  │       │  ┌──────────────────────┐  │
│  │ config: ConfigService│  │       │  │ config: ConfigService│  │
│  │   _cache: dict       │◀─┼──┐ ┌──┼─▶│   _cache: dict       │  │
│  │   _secrets_cache     │  │  │ │  │  │   _secrets_cache     │  │
│  └──────────┬───────────┘  │  │ │  │  └──────────┬───────────┘  │
│  ┌──────────▼───────────┐  │  │ │  │  ┌──────────▼───────────┐  │
│  │ admin router         │  │  │ │  │  │ admin router         │  │
│  │ /api/admin/config    │  │  │ │  │  │ /api/admin/config    │  │
│  │ /api/admin/secrets   │  │  │ │  │  │ /api/admin/secrets   │  │
│  └──────────┬───────────┘  │  │ │  │  └──────────┬───────────┘  │
└─────────────┼──────────────┘  │ │  └─────────────┼──────────────┘
              │ SQL             │ │                │ SQL
              ▼                 │ │                ▼
      ┌───────────────────┐     │ │        ┌───────────────────┐
      │ Postgres          │     │ │        │ Redis             │
      │ app_config        │     │ └────────│ channel:          │
      │ app_secrets       │     │          │ config:invalidate │
      └───────────────────┘     └──────────│ SUBSCRIBE ────────│
                                           └───────────────────┘

Auth path (prod):
  curl → CF Tunnel (QUIC) → cloudflared → nginx (127.0.0.1:80)
      → proxy_pass http://backend:8000
         • CF has already attached Cf-Access-Jwt-Assertion header
         • FastAPI Depends(require_admin_jwt) validates it
         • Identity = email claim (Google login) OR common_name claim (service token)

Auth path (dev, APP_ENV=dev AND client IP in TRUSTED_DEV_NETS):
  curl → WG → 10.10.0.1:80 → nginx → backend
         • No CF JWT header present
         • require_admin_jwt short-circuits with synthetic AdminIdentity(email="dev@localhost")
         • Bypass only fires if BOTH env AND IP match — typo in .env alone cannot disable auth
```

### Key properties

- **Auth model:** CF Access JWT verification via `pyjwt[crypto]`'s `PyJWKClient`. Fetches JWKS from `https://{CF_ACCESS_TEAM_DOMAIN}/cdn-cgi/access/certs` with an internal 1-hour cache; on signature failure with unknown `kid`, forces an immediate JWKS refresh (doesn't wait for TTL). Verifies RS256 signature + `exp` + `iss` + `aud`. Extracts identity as `claims.get("email") or claims.get("common_name")` — accepts both Google-user tokens and service-token tokens. Raises 401 if neither claim is present. No secondary allowlist; CF Access's 2-email + 1-service-token at the edge is the single source of truth.
- **Dev-mode bypass — double-gated:** only fires if `APP_ENV=dev` AND the request's real client IP (from `X-Forwarded-For` resolved against trusted proxies) is in `TRUSTED_DEV_NETS` (default `10.10.0.0/24`, overridable). An `APP_ENV=dev` typo accidentally committed to prod `.env` cannot disable auth from the public edge — the IP check still rejects. If `APP_ENV=prod` AND `TRUSTED_DEV_NETS` is non-empty, emit a CRITICAL log at startup (config smell) but don't refuse boot.
- **Cache coherence:** each worker holds its own dict. On write, writer evicts its entry → DB commit → Redis PUBLISH `"{namespace}|{key}"` on `config:invalidate`. Subscribers evict the same entry. Cache entries also have a 5-minute TTL as a safety net if Redis publish is lost.
- **Fernet key derivation — with rotation support:** primary key = `base64.urlsafe_b64encode(HKDF(SHA256, length=32, salt=b"dashboard.v1", info=b"app_secrets.fernet.v1").derive(APP_SECRET_KEY.encode()))`. `Encrypt` uses the primary key. `Decrypt` uses `MultiFernet([primary, prev_if_set])` — if `APP_SECRET_KEY_PREV` is set, old ciphertexts still decrypt; a background re-encrypt-on-read job (out of Phase 2) rotates them at rest. Lets us rotate `APP_SECRET_KEY` without a forced mass-invalidation.
- **No runtime env expansion beyond bootstrap:** 2 new required keys (`CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUDIENCE`), 1 optional (`APP_SECRET_KEY_PREV`), 1 optional (`TRUSTED_DEV_NETS`). All documented in `.env.example`.

## 4. Components

Phase 2 adds these files. Each has one responsibility.

```
backend/app/
├── core/
│   ├── cf_access.py       [NEW]  PyJWKClient wrapper + identity extraction; pure logic, no DB
│   ├── crypto.py          [NEW]  Fernet key derivation (primary + prev) + encrypt/decrypt helpers
│   ├── metrics.py         [NEW]  prometheus-client counters/gauges for admin + config ops
│   └── deps.py                   Add require_admin_jwt dep; add get_config dep
├── models/
│   └── config.py          [NEW]  AppConfig + AppSecret SQLAlchemy declarative models
├── services/
│   ├── config.py          [NEW]  ConfigService public API (get/set/delete/list + typed + secrets)
│   └── config_cache.py    [NEW]  In-memory cache + Redis pub/sub SUBSCRIBE loop
├── api/
│   ├── admin.py           [NEW]  FastAPI router: /api/admin/config + /api/admin/secrets CRUD + reveal
│   └── metrics.py         [NEW]  /metrics route (prometheus text format); gated by require_admin_jwt
└── main.py                       Register admin + metrics routers; ConfigService lifespan SUBSCRIBE

backend/alembic/versions/
└── 0001_app_config_and_secrets.py   [NEW]  Both tables + indexes + CHECK constraints; reversible

backend/scripts/
└── entrypoint.sh          [NEW]  Runs `alembic upgrade head` then execs uvicorn (replaces direct CMD)

backend/tests/
├── test_cf_access.py      [NEW]  JWT decode (email + common_name paths); kid-miss force-refresh; dev-bypass (env+IP)
├── test_crypto.py         [NEW]  Fernet roundtrip; HKDF determinism; MultiFernet prev-key fallback
├── test_config_service.py [NEW]  CRUD + typed accessors + default handling + JSONB value
├── test_config_cache.py   [NEW]  Pub/sub invalidation across 2 fake workers
├── test_admin_api.py      [NEW]  Router end-to-end with mocked auth dep (CRUD + reveal + PUT-URL-vs-body)
├── test_admin_auth.py     [NEW]  Real auth dep — valid/expired/wrong signer/service-token/dev-bypass
├── test_metrics.py        [NEW]  /metrics scrape; counter increments
└── test_migration.py      [NEW]  alembic upgrade/downgrade round-trip

tests/e2e/
└── smoke.spec.ts                Extended with one `POST /api/admin/config` round-trip via service token
```

### Schema (Alembic migration `0001`)

```sql
CREATE TABLE app_config (
    namespace   TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT,           -- holds str/int/bool values as text
    value_json  JSONB,           -- holds json values natively; queryable with @>
    value_type  TEXT NOT NULL CHECK (value_type IN ('str','int','bool','json')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, key),
    CONSTRAINT value_exclusive CHECK (
        (value_type = 'json' AND value_json IS NOT NULL AND value IS NULL)
        OR
        (value_type <> 'json' AND value IS NOT NULL AND value_json IS NULL)
    )
);
CREATE INDEX ix_app_config_updated_at ON app_config (updated_at DESC);

CREATE TABLE app_secrets (
    namespace        TEXT NOT NULL,
    key              TEXT NOT NULL,
    value_encrypted  BYTEA NOT NULL,       -- Fernet ciphertext for all types (JSON gets serialized before encrypt)
    value_type       TEXT NOT NULL CHECK (value_type IN ('str','int','bool','json')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, key)
);
CREATE INDEX ix_app_secrets_updated_at ON app_secrets (updated_at DESC);
```

Both tables use `(namespace, key)` composite PK. `app_config.value` is `TEXT` for scalar types; `app_config.value_json` is `JSONB` for `value_type='json'` (enables `WHERE value_json @> '{"nested":1}'` queries for free in future). The CHECK constraint enforces exactly one of the two columns is set per row. Secrets keep `BYTEA` ciphertext regardless of value_type (JSON is serialized to bytes before encryption).

`created_at` + `updated_at` are auto-maintained via SQLAlchemy `server_default=func.now()` + `onupdate=func.now()`.

### ConfigService public API

```python
class ConfigService:
    async def get(self, ns: str, key: str, default: str | None = None) -> str | None
    async def get_int(self, ns: str, key: str, default: int | None = None) -> int | None
    async def get_bool(self, ns: str, key: str, default: bool | None = None) -> bool | None
    async def get_json(self, ns: str, key: str, default: Any = None) -> Any
    async def set(self, ns: str, key: str, value: Any, value_type: str = "str") -> AppConfig
    async def delete(self, ns: str, key: str) -> bool  # True if row existed (still 204 either way)
    async def list(self, namespace: str | None = None) -> list[AppConfig]

    # Secrets: list + get return metadata only; reveal is a separate method.
    async def get_secret_metadata(self, ns: str, key: str) -> SecretMetadata | None
    async def reveal_secret(self, ns: str, key: str) -> str | None       # plaintext str
    async def reveal_secret_int(self, ns: str, key: str) -> int | None
    async def reveal_secret_bool(self, ns: str, key: str) -> bool | None
    async def reveal_secret_json(self, ns: str, key: str) -> Any
    async def set_secret(self, ns: str, key: str, value: Any, value_type: str = "str") -> AppSecret
    async def delete_secret(self, ns: str, key: str) -> bool
    async def list_secrets(self, namespace: str | None = None) -> list[SecretMetadata]
    # ^ SecretMetadata has namespace, key, value_type, created_at, updated_at — never value/ciphertext
```

Typed accessors coerce via `value_type`. Mismatch raises `ConfigTypeError` (a `ValueError` subclass) — fail-fast over silent wrong-type return. `reveal_secret*` are the ONLY paths that return decrypted plaintext.

### Admin REST API

```
GET    /api/admin/config                         → list all, or ?namespace=X to filter
GET    /api/admin/config/{namespace}/{key}       → single entry
POST   /api/admin/config                         → create (409 if exists)
PUT    /api/admin/config/{namespace}/{key}       → upsert; body ns/key MUST match URL → 422 on mismatch
DELETE /api/admin/config/{namespace}/{key}       → 204 (idempotent — same response whether row existed)

GET    /api/admin/secrets                        → list metadata (no value, no ciphertext)
GET    /api/admin/secrets/{namespace}/{key}      → single entry metadata only (no value)
POST   /api/admin/secrets                        → create (409 if exists); body plaintext
PUT    /api/admin/secrets/{namespace}/{key}      → upsert plaintext; body ns/key MUST match URL → 422
POST   /api/admin/secrets/{namespace}/{key}/reveal → returns decrypted plaintext; response sent with:
                                                      Cache-Control: no-store, private
                                                      X-Content-Type-Options: nosniff
                                                      Pragma: no-cache
DELETE /api/admin/secrets/{namespace}/{key}      → 204 (idempotent)

GET    /metrics                                  → prometheus text format; gated by require_admin_jwt
```

**REST contract decisions (from architect-review M4):**
- **PUT semantics:** URL path is source of truth. If request body contains `namespace` or `key` fields that differ from the URL, respond **422** with detail `"body ns/key mismatch URL"`. If body omits them, fill from URL.
- **DELETE semantics:** idempotent. Always **204** whether the row existed or not. Prevents CI retries or double-clicks from causing false 404s. Log INFO with a `row_existed` boolean for auditability.
- **Reveal semantics:** POST (not GET) so the URI with `/reveal` appears in nginx access logs but not the payload. Response headers explicitly disable caching. Logged at INFO with `{actor_identity, namespace, key}` — never plaintext.

Request/response bodies (Pydantic):

```python
class ConfigIn(BaseModel):
    namespace: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    key:       str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$")
    value:     str | dict | list | int | bool  # JSON types; coerced to TEXT or JSONB via value_type
    value_type: Literal["str","int","bool","json"] = "str"

class ConfigOut(BaseModel):
    namespace: str
    key: str
    value: str | dict | list | int | bool  # returned as native JSON type when value_type='json'
    value_type: str
    created_at: datetime
    updated_at: datetime

class SecretMetadataOut(BaseModel):   # list + single-GET responses
    namespace: str
    key: str
    value_type: str
    created_at: datetime
    updated_at: datetime

class SecretRevealOut(BaseModel):      # only from POST /reveal endpoint
    namespace: str
    key: str
    value: str | dict | list | int | bool
    value_type: str
```

### New Python dependencies

Added to `pyproject.toml` `[project.dependencies]` via `uv add <pkg>` (pulls latest stable at scaffold time):

- `cryptography` — Fernet + HKDF + MultiFernet (latest stable at scaffold time)
- `redis[hiredis]` — async Redis client + C parser (latest stable at scaffold time)
- `pyjwt[crypto]` — JWT verification with RS256 via cryptography; `PyJWKClient` for JWKS caching (latest stable at scaffold time)
- `httpx` — JWKS HTTP fetch (add explicit; FastAPI may already transitively include)
- `prometheus-client` — metrics exposition (latest stable at scaffold time)

Test-only: `fakeredis[asyncio]` + a real `redis:7-alpine` service in CI for one dedicated pub/sub fidelity test.

## 5. Data flow

### 5a. Cold read (cache miss)

```
await config.get("telegram", "bot_token", default="")
    │
    ▼
ConfigService.get(ns, key, default)
    │
    ├── _cache.get((ns, key)) ──hit (and not expired)──▶ return cached value
    │
    └── miss ──▶
            │
            ▼
        SELECT value, value_json, value_type
          FROM app_config WHERE namespace=$1 AND key=$2
            │
            ├── row found  ──▶ materialize = value_json if value_type='json' else value
            │                  _cache[(ns, key)] = (materialized, value_type, now)
            │                  return materialized
            │
            └── no row     ──▶ return default  (no negative caching — YAGNI)
```

Typed accessors (`get_int`, etc.) are thin wrappers that parse `value` based on stored `value_type`. If the accessor type ≠ stored type, raise `ConfigTypeError(key, expected, found)`. No silent coercion.

### 5b. Write + invalidation

```
POST /api/admin/config  {namespace, key, value, value_type}
    │
    ▼
admin router → ConfigService.set(ns, key, value, value_type)
    │
    1. If value_type='json': serialize value to jsonb column; set value=NULL
       Else:                  set value=str(value); set value_json=NULL
    │
    2. INSERT INTO app_config (namespace, key, value, value_json, value_type)
         VALUES (...)
         ON CONFLICT (namespace, key) DO UPDATE SET
             value = EXCLUDED.value,
             value_json = EXCLUDED.value_json,
             value_type = EXCLUDED.value_type,
             updated_at = now();    ── atomic upsert
    │
    3. self._cache.pop((ns, key), None)      ── evict writer's local
    │
    4. try:
           await redis.publish("config:invalidate", f"{ns}|{key}")
       except (ConnectionError, TimeoutError):
           log.warning("config invalidation publish failed", ns=ns, key=key)
           metrics.redis_publish_fail_total.inc()
           # don't raise — DB write succeeded; TTL will recover
    │
    5. return ConfigOut
```

**Subscriber loop** (runs per worker via `asyncio.create_task` on FastAPI startup):

```python
async def _invalidation_listener(self):
    attempt = 0
    while True:
        try:
            async with redis.pubsub() as pubsub:
                await pubsub.subscribe("config:invalidate")
                attempt = 0
                async for msg in pubsub.listen():
                    if msg["type"] != "message":
                        continue
                    ns, key = msg["data"].decode().split("|", 1)
                    self._cache.pop((ns, key), None)
        except (ConnectionError, TimeoutError) as e:
            log.warning("pubsub reconnect after", error=str(e))
            metrics.redis_subscribe_reconnect_total.inc()
            await asyncio.sleep(min(2 ** attempt, 30))
            attempt += 1
```

Secrets follow the same flow on a separate channel `config:invalidate:secrets`.

### 5c. Admin request auth lifecycle

```
curl (with CF-Access-Client-Id/Secret OR Google session cookie)
    │
    ▼
CF Tunnel → cloudflared → nginx → backend
    │
    │  CF attaches: Cf-Access-Jwt-Assertion: <RS256 JWT>
    │  nginx preserves header (passthrough default)
    │
    ▼
FastAPI: @router.get(..., dependencies=[Depends(require_admin_jwt)])
    │
    ▼
require_admin_jwt(request):
    1. Dev-mode AND-gate (BOTH must be true):
         if settings.env == "dev" AND client_ip_in(settings.trusted_dev_nets):
             return AdminIdentity(email="dev@localhost", kind="dev-bypass", claims={})
    2. token = request.headers.get("Cf-Access-Jwt-Assertion")
       if not token:
           metrics.cf_jwt_verification_total.labels(result="missing_header").inc()
           raise HTTPException(401, "missing cf-access jwt")
    3. try:
           signing_key = _jwks_client.get_signing_key_from_jwt(token).key
       except (PyJWKClientError, KeyError):
           _jwks_client.invalidate_cache()         # force re-fetch on kid miss
           signing_key = _jwks_client.get_signing_key_from_jwt(token).key
    4. try:
           claims = jwt.decode(
               token, signing_key,
               algorithms=["RS256"],
               issuer=f"https://{settings.cf_access_team_domain}",
               audience=settings.cf_access_audience,
           )
       except ExpiredSignatureError: 401 "jwt expired"         (INFO)
       except InvalidSignatureError: 401 "jwt signature ..."   (WARNING)
       except InvalidIssuerError | InvalidAudienceError: 401 "jwt claims invalid" (WARNING)
       except PyJWTError as e:        401 "jwt error"           (WARNING)
    5. identity = claims.get("email") or claims.get("common_name")
       if not identity:
           metrics.cf_jwt_verification_total.labels(result="no_identity_claim").inc()
           raise HTTPException(401, "jwt missing identity claim")
       kind = "user" if "email" in claims else "service_token"
       metrics.cf_jwt_verification_total.labels(result="ok").inc()
       return AdminIdentity(email=identity, kind=kind, claims=claims)
```

Where `_jwks_client = PyJWKClient(f"https://{team_domain}/cdn-cgi/access/certs", cache_keys=True, lifespan=3600)`. The `invalidate_cache()` path forces a fresh JWKS fetch when the incoming JWT's `kid` isn't in our cache (CF rotation within the 1-hour window).

**New bootstrap settings** (added to `app/core/config.py` `Settings`):

- `cf_access_team_domain: str = Field(alias="CF_ACCESS_TEAM_DOMAIN")` — e.g. `kiusinghung.cloudflareaccess.com`
- `cf_access_audience: str = Field(alias="CF_ACCESS_AUDIENCE")` — the app's AUD tag from CF dashboard
- `app_secret_key_prev: str | None = Field(default=None, alias="APP_SECRET_KEY_PREV")` — optional; enables rolling rotation
- `trusted_dev_nets: list[str] = Field(default_factory=list, alias="TRUSTED_DEV_NETS")` — e.g. `["10.10.0.0/24"]`; empty by default = no dev bypass possible

`.env.example` gains all four. Prod `.env` sets the first two; leaves the last two empty (or the last one — PREV — only during rotation windows).

**Service-token JWT note:** CF Access issues JWTs for service-token-authenticated requests too. Those tokens carry `common_name: "dashboard-ci-smoke"` (or whatever the token was named) in place of `email`. Our identity extractor accepts both — so the CI smoke path (which uses the service token) authenticates correctly without special-casing.

## 6. Error handling

Fail-loud at boundaries (HTTP, admin writes), fail-soft internally (cache, pub/sub).

### 6a. Auth failures

| Condition | HTTP | Body | Log level | Metric label |
|---|---|---|---|---|
| Header missing | 401 | `{"detail":"missing cf-access jwt"}` | INFO | `missing_header` |
| Signature invalid | 401 | `{"detail":"jwt signature verification failed"}` | WARNING | `bad_signature` |
| Token expired | 401 | `{"detail":"jwt expired"}` | INFO | `expired` |
| Issuer/audience mismatch | 401 | `{"detail":"jwt claims invalid"}` | WARNING | `bad_claims` |
| Identity claim missing (neither `email` nor `common_name`) | 401 | `{"detail":"jwt missing identity claim"}` | WARNING | `no_identity` |
| JWKS fetch failed (network, 5xx from CF) | 503 | `{"detail":"identity service unavailable"}` | ERROR | `jwks_fetch_fail` |
| `kid` not in cache → forced refresh → still not found | 401 | `{"detail":"jwt signing key unknown"}` | WARNING | `kid_miss` |
| Dev-bypass active in prod (env=prod AND trusted_dev_nets non-empty AND IP matches) | 500 | `{"detail":"internal error"}` | CRITICAL | `dev_bypass_in_prod` |

Emails/common_names in claims are never echoed in error responses (avoid disclosing who just tried). They ARE logged at INFO level for successful auth.

### 6b. ConfigService failures

| Condition | Behavior |
|---|---|
| `get()` missing + no default | return `None` |
| `get()` missing + default given | return default |
| `get_int()` on row with `value_type="str"` | raise `ConfigTypeError` |
| `get_json()` on malformed JSON (invariant violation — only reachable if a row was written via raw SQL bypassing `set()`) | raise `ConfigTypeError` |
| `set()` with invalid `value_type` | raise `ValueError` before DB call (validated by Pydantic at HTTP boundary too) |
| `set()` with `value_type='json'` and non-serializable value | raise `ValueError`; Pydantic catches most at HTTP boundary |
| `set_secret()` Fernet encrypt failure (cryptography internals) | 500 + log + rollback |
| `get_secret()` / `reveal_secret()` Fernet decrypt failure (`InvalidToken`) with no PREV key | 500 + log with key name (never plaintext); signals `APP_SECRET_KEY` changed or row tampered |
| `reveal_secret()` decrypt with PREV key succeeds | return plaintext; metric `fernet_prev_key_hits_total` increments; log INFO (informational — still authorized read) |
| DB pool exhausted or connection drop | SQLAlchemy `pool_pre_ping` handles transient; route returns 503 on persistent |
| Redis publish fails | **log WARNING, don't raise** — DB write already succeeded; 5-min TTL recovers; metric `redis_publish_fail_total` |
| Redis subscribe channel drops | reconnect loop with exponential backoff (1s → 30s cap); each reconnect logged + `redis_subscribe_reconnect_total` |

### 6c. Cache coherence hazards

- **Race (write-then-concurrent-read)**: worker A writes + publishes; worker B is mid-`get()` and SELECTed the old value before commit was visible. B caches stale until next invalidation OR 5-min TTL. **Acceptable** — config writes are admin actions, not hot paths.
- **Cache stampede on cold start**: each worker independently queries DB on first access. Tables tiny, queries fast; no `singleflight` lock needed.
- **Orphan cache after row delete** (if publish was lost): 5-min TTL catches it.
- **Subscriber lag after reconnect**: pub/sub is fire-and-forget; messages published while a worker's pubsub connection was down are lost. 5-min TTL is the recovery mechanism. If sub-5-minute staleness becomes unacceptable, migrate to Redis Streams with consumer groups (deferred — YAGNI for solo-dev admin-write workload).

### 6d. Migration / startup

- `alembic upgrade head` failure → `entrypoint.sh` exits non-zero → container healthcheck fails → compose doesn't mark backend ready → deploy fails loud (no silent partial upgrade).
- Multi-worker startup: Alembic takes `pg_advisory_lock(0)` internally, so concurrent `alembic upgrade head` runs serialize safely. No race. (Alternative dedicated `migrate` compose service considered; see §8d.)
- `ConfigService` SUBSCRIBE fails at startup: log CRITICAL; retry in background with backoff. Routes keep serving from DB-only path (slower, degraded coherence) until Redis recovers. Degraded > offline.
- First-ever deploy: empty tables → every `get()` returns its `default` → app runs on bootstrap values → no seed data needed in migration.

### 6e. Observability (NEW — from architect review H2)

Metrics exposed at `GET /metrics` (prometheus text format, gated by `require_admin_jwt`):

| Metric | Type | Labels | Purpose |
|---|---|---|---|
| `cf_jwt_verification_total` | Counter | `result` | tracks all auth outcomes (see §6a labels) |
| `config_ops_total` | Counter | `op`, `kind`, `result` | `op` ∈ {get, set, delete, list}; `kind` ∈ {config, secret}; `result` ∈ {hit, miss, ok, error} |
| `config_cache_size` | Gauge | `kind` | number of cached entries per worker |
| `redis_publish_fail_total` | Counter | `channel` | pub/sub publish errors |
| `redis_subscribe_reconnect_total` | Counter | `channel` | sub reconnect events |
| `fernet_prev_key_hits_total` | Counter | — | reveals decrypted via PREV key (signals rotation in progress) |
| `admin_secret_reveal_total` | Counter | `actor_kind` | `user` vs `service_token`; detect excessive reveals |

Structured log fields (all ConfigService + admin router emissions):

- `namespace`, `key` (never `value`/`plaintext`)
- `actor_identity` (email or common_name — captured at auth boundary)
- `actor_kind` (`user|service_token|dev-bypass`)
- `request_id` (generated middleware; also propagated to nginx access log via `X-Request-ID`)

## 7. Testing strategy

Target ≥85% overall coverage, **100% on `cf_access.py` + `crypto.py`** (security-critical).

### 7a. Unit (pure logic)

| File | Cases |
|---|---|
| `test_cf_access.py` | Valid RS256 with `email` claim → `AdminIdentity(kind="user")`. Valid RS256 with `common_name` claim → `AdminIdentity(kind="service_token")`. Missing both → 401. Expired `exp`. Wrong `iss`. Wrong `aud`. Bad signature (flipped byte). Missing header. Unknown `kid` → JWKS force-refresh → retry succeeds. Unknown `kid` → refresh → still fails → 401. JWKS endpoint 5xx → 503. Dev-mode bypass: env=dev+IP-in-list → ok. env=dev+IP-out-of-list → falls through to real JWT verify. env=prod+trusted_dev_nets empty → bypass never fires. env=prod+trusted_dev_nets non-empty → startup CRITICAL log; runtime bypass attempted → 500. |
| `test_crypto.py` | `derive_fernet_key(APP_SECRET_KEY)` deterministic across calls. `encrypt` + `decrypt` roundtrip for `b"hello"`, empty string, 1-MB blob. `decrypt` with wrong key raises `InvalidToken`. HKDF salt change → different key. MultiFernet with `[primary, prev]`: ciphertext encrypted under PREV still decrypts; counter increments; newly-encrypted ciphertexts use primary. Rotating PREV→primary keeps old data readable. |

### 7b. ConfigService integration (real Postgres, fake Redis)

| Test class | Cases |
|---|---|
| `TestConfigCRUD` | `set → get` roundtrip for str/int/bool/json. `delete` removes row (returns True) + subsequent `delete` returns False but API 204. `list(ns)` filters correctly. `list()` returns all. |
| `TestJsonColumn` | `set(ns, k, {"a":1}, value_type="json")` stores in `value_json` (verified via raw SQL; `value` column is NULL). `get_json` returns dict. CHECK constraint violation on manually-crafted BAD row. |
| `TestTypedAccessors` | `get_int` on int-typed row → int. `get_int` on str-typed row → `ConfigTypeError`. Same for bool, json. `get()` always returns native type (TEXT or dict for json). |
| `TestDefaults` | `get("missing", "key", default=42)` → 42. `get("missing", "key")` → None. |
| `TestUpsert` | Two `set(ns, k, v1)` then `set(ns, k, v2)` — second wins; one row in DB. |
| `TestSecretRoundtrip` | `set_secret` → `reveal_secret` → plaintext. DB row has only ciphertext (verified via raw SQL). `get_secret_metadata` returns metadata without value. `list_secrets` returns metadata (no plaintext field). |
| `TestSecretPrevKey` | Insert ciphertext encrypted under PREV; `reveal_secret` decrypts; counter increments. |
| `TestCacheCoherence` | `set(ns, k, v1)`; `get(ns, k)` → v1 (cached). Direct SQL UPDATE bypassing service; `get(ns, k)` still returns v1 (stale cache). Simulate pub/sub invalidation message → next `get()` returns new value. |
| `TestInvalidationOnFailure` | Monkeypatch Redis to raise on publish. `set()` still succeeds (DB-visible). Log captured at WARNING. Service doesn't raise. `redis_publish_fail_total` increments. |

Backing: existing `DATABASE_URL` from pytest env (Phase 0 tests already use this for `/health`). `fakeredis[asyncio]` for most Redis; **one** dedicated fidelity test (`TestCacheCoherence::test_real_redis_pubsub`) uses a real `redis:7-alpine` service container in CI to verify fakeredis isn't masking a protocol mismatch.

### 7c. Admin API end-to-end

`test_admin_api.py` uses FastAPI `TestClient` with `app.dependency_overrides[require_admin_jwt] = lambda: AdminIdentity(email="test@example.com", kind="user")`.

| Endpoint | Cases |
|---|---|
| `GET /api/admin/config` | Empty → `[]`. 3 inserts → 3 entries. `?namespace=foo` filter. |
| `GET /api/admin/config/{ns}/{key}` | Exists → entry. Missing → 404. |
| `POST /api/admin/config` | Valid → 201 + entry. Invalid `value_type` → 422. Duplicate → 409. Invalid namespace pattern (uppercase) → 422. `value_type='json'` with dict value → stored in `value_json`. |
| `PUT /api/admin/config/{ns}/{key}` | Upsert — create if missing, update if exists. Body `namespace`/`key` mismatch URL → 422 with `"body ns/key mismatch URL"`. Body omitting ns/key → fills from URL. |
| `DELETE /api/admin/config/{ns}/{key}` | Always 204. Second call (already deleted) → still 204 (idempotent). |
| `GET /api/admin/secrets/{ns}/{key}` | Single-GET returns metadata only; no `value` field in response; HTTP 200. |
| `POST /api/admin/secrets/{ns}/{key}/reveal` | Returns plaintext. Response headers include `Cache-Control: no-store` and `Pragma: no-cache`. Log captured with `actor_identity`, `namespace`, `key` — no plaintext. `admin_secret_reveal_total` increments. |
| `GET /metrics` | With dep override → 200 + prometheus text. Without dep override → 401 (gated same as admin). |
| Any admin endpoint WITHOUT dep override | 401 (confirms route is gated). |

### 7d. Auth contract

`test_admin_auth.py` — real `require_admin_jwt`, no dep override:

- No header + `APP_ENV=prod` → 401
- Malformed JWT (not 3 parts) → 401
- JWT signed with test keypair + JWKS fixture → 200 with `kind=user`
- JWT with `common_name` claim only (no `email`) → 200 with `kind=service_token`
- JWT with neither claim → 401 `"jwt missing identity claim"`
- Expired → 401
- Wrong audience → 401
- Unknown `kid` → JWKS force-refresh → retry succeeds → 200
- Unknown `kid` → refresh → still unknown → 401 `"jwt signing key unknown"`
- No header + `APP_ENV=dev` + client IP in `TRUSTED_DEV_NETS` → 200 with `kind=dev-bypass`
- No header + `APP_ENV=dev` + client IP NOT in `TRUSTED_DEV_NETS` → 401 (real verify runs)
- No header + `APP_ENV=prod` + `TRUSTED_DEV_NETS` non-empty + client IP matches → 500 `dev-bypass in prod`
- Startup with `APP_ENV=prod` + `TRUSTED_DEV_NETS` non-empty → CRITICAL log captured

### 7e. Migration

`test_migration.py`:

- `alembic upgrade head` from empty DB → both tables exist with expected columns + PKs + CHECK constraints + indexes.
- CHECK constraint works: INSERT violating `value_exclusive` → `IntegrityError`.
- `alembic downgrade base` → tables gone.
- `alembic upgrade head` again → idempotent no-op (all migrations applied).

Uses the same test DB as other integration tests (pytest session fixture creates it once, drops on teardown). Alembic state is reset between tests via `downgrade base → upgrade head` in a function-scoped fixture so tests don't leak schema state.

## 8. Scope boundaries

### 8a. In scope (Phase 2)

- Two tables + one reversible Alembic migration (with CHECK constraints + JSONB column)
- ConfigService (cache + Redis pub/sub + Fernet secrets with PREV-key rotation fallback)
- CF Access JWT verification via `PyJWKClient` (accepts `email` + `common_name` claims); dev-mode bypass double-gated by env AND IP allowlist
- Admin REST API (full CRUD on both tables; **reveal endpoint for secrets — not plaintext read via GET**; idempotent DELETE; PUT URL-vs-body consistency enforcement)
- `/metrics` endpoint with prometheus-client (gated by admin auth)
- Structured logging with request-id propagation
- 8 backend test files; ≥85% coverage (100% on auth + crypto)
- Extended Playwright smoke test: adds one `POST /api/admin/config` round-trip via service token + one `POST /api/admin/secrets/.../reveal` through auth
- Update CLAUDE.md "Configuration Storage" section — wording pivots from "Phase 2+" to "active"
- Update `backend/.env.example` with 4 new keys (`CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUDIENCE`, `APP_SECRET_KEY_PREV` optional, `TRUSTED_DEV_NETS` optional)

### 8b. Out of scope (explicit deferrals)

- **Admin UI** → Phase 3 (part of the React shell)
- **Audit log** → later (when a second user role appears)
- **Role-based access** (admin vs viewer) → Phase 3+ if needed
- **Config versioning / change history** → nice-to-have, not now
- **Webhooks on change** → pub/sub covers backend; no external consumers yet
- **Bulk import** (YAML → DB) → curl loop handles it ad-hoc
- **GraphQL / gRPC** → REST only
- **Rate limiting on `/api/admin/*`** → nginx already rate-limits `/api/*` at 10r/s with burst 20 (Phase 1 `dashboard.conf`); sufficient
- **Re-encrypt-at-rest job** (rewrite PREV-key ciphertexts with primary on a schedule) → documented, not built — trigger manually when rotation completes
- **Redis Streams migration** (guaranteed delivery) → only if pub/sub lag becomes a real problem; YAGNI

### 8c. Dependencies / versions

New Python packages via `uv add`:

- `cryptography`
- `redis[hiredis]`
- `pyjwt[crypto]`
- `httpx` (explicit, in case not already pulled transitively)
- `prometheus-client`

`uv.lock` updated. No hand-picked versions.

### 8d. Rollout

- **CI path**: PR → `ci.yml` (backend tests include new suites; one test runs against real `redis:7-alpine` service) → merge → `deploy.yml` (rsync + build + compose up + Playwright extended smoke) → green.
- **Backend container start**: `scripts/entrypoint.sh` wraps startup:
  ```sh
  #!/bin/sh
  set -e
  /app/.venv/bin/alembic upgrade head       # Alembic pg_advisory_lock serializes multi-worker
  exec /app/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
  ```
  Migration failure → container exits non-zero → compose healthcheck fails → deploy stops.
- **Rollback**: `alembic downgrade -1` (drops both tables) + redeploy prior image tag. `app_config` / `app_secrets` data loss on rollback is acceptable because they're ops-written values, not user data. For `APP_SECRET_KEY` rotation rollback: set `APP_SECRET_KEY_PREV` = current primary key before rotating; keeps old data decryptable during the cutover window.

## 9. Success criteria

Phase 2 ships when **all seven** are demonstrable:

1. `alembic upgrade head` creates both tables from an empty DB with zero errors; CHECK constraints reject malformed inserts.
2. `backend/ pytest --cov` ≥ 85% overall; 100% on `core/cf_access.py` + `core/crypto.py`. One pub/sub test runs green against a real `redis:7-alpine` in CI.
3. From the NUC, curl against prod with the CI service-token headers:
   - `POST /api/admin/config` with body `{"namespace":"test","key":"phase2","value":"ok","value_type":"str"}` → 201.
   - `GET /api/admin/config/test/phase2` → 200 with same values.
   - `POST /api/admin/secrets` with body `{"namespace":"test","key":"ps2","value":"s3cr3t","value_type":"str"}` → 201.
   - `GET /api/admin/secrets/test/ps2` → 200 with metadata only (no `value` in body).
   - `POST /api/admin/secrets/test/ps2/reveal` → 200 with decrypted value; response has `Cache-Control: no-store` header.
4. From the NUC, curl against prod with NO service-token AND no Google session cookie → 401 (CF Access 302 redirect proves auth still works).
5. `docker compose up --scale backend=2` + a config write on worker-1 → worker-2 reflects the change on the next `get()` within ≤5 seconds.
6. `GET /metrics` with admin auth returns prometheus-format text with at least `cf_jwt_verification_total` and `config_ops_total` counters present.
7. `CLAUDE.md`, `CHANGELOG.md`, `TASKS.md` updated; Phase 2 tagged `v0.2.0`; both CI + Deploy workflows green.

## 10. Open items / risks to track

- **`CF_ACCESS_AUDIENCE` fragility:** if the Access app is ever torn down and recreated (e.g. via `99-teardown.sh` + rerun of `20-access-app.sh`), the AUD tag changes and all existing issued JWTs become invalid. Plan includes a backend startup smoke: fetch JWKS, confirm expected `iss` resolves; log CRITICAL if unreachable. Adding `CF_ACCESS_AUDIENCE` to the plan's pre-flight step: user must copy it from CF dashboard after any Access-app rebuild.
- **Service-token rotation:** `dashboard-ci-smoke` expires 2027-04-22. Annual rotation reminder is already in `feedback_proactive_tooling.md` memory.
- **`APP_SECRET_KEY` rotation workflow** (documented, not scripted in Phase 2):
  1. Generate new key: `NEW=$(openssl rand -base64 32)`
  2. Set `APP_SECRET_KEY_PREV=<current>` and `APP_SECRET_KEY=<new>` in `.env`; redeploy.
  3. All new writes encrypt with new primary; reads fall back to PREV transparently.
  4. Optionally: run a one-off script (out of Phase 2) that SELECTs all `app_secrets`, decrypts with PREV, re-encrypts with primary. Counter `fernet_prev_key_hits_total` indicates how many rows remain.
  5. Once zero PREV-key hits for N days, unset `APP_SECRET_KEY_PREV`.
- **Redis persistence disabled in prod** (`--save "" --appendonly no` from Phase 1): pub/sub messages are ephemeral by design. Restart of Redis loses any in-flight invalidations; 5-min cache TTL is the recovery. Acceptable.
- **First invocation pattern:** consumers (backend services added in Phases 4+) will call `await config.get(...)` inside async handlers. For sync contexts (e.g. a CLI tool), they'd need an async wrapper — out of scope here.
- **`value_json` querying opens JSONB indexing door for Phase 4+** — broker adapters that store complex configs (e.g. IBKR account map) can use `WHERE value_json @> '{"paper":true}'` without schema changes. Not needed in Phase 2; noted so we don't re-architect later.

---

## Architect review — applied (2026-04-22)

| # | Severity | Finding | Resolution |
|---|---|---|---|
| C1 | CRITICAL | `jwt.decode(token, jwks, ...)` signature wrong — pyjwt doesn't accept JWKS dict | Rewrote §5c to use `PyJWKClient` + `get_signing_key_from_jwt()`; added kid-miss force-refresh path |
| C2 | CRITICAL | `claims["email"]` KeyErrors on service-token JWTs (carry `common_name`) — would break CI smoke | Identity extraction now: `claims.get("email") or claims.get("common_name")`; both kinds accepted as admin |
| H1 | HIGH | Dev-bypass gated only on `APP_ENV` — typo in prod `.env` silently disables auth | AND-gate: bypass fires only if `APP_ENV=dev` AND client IP ∈ `TRUSTED_DEV_NETS`; prod+non-empty-list triggers startup CRITICAL and runtime 500 |
| H2 | HIGH | No observability for auth/config/cache operations | Added `core/metrics.py`, `/metrics` endpoint, §6e observability table (7 metrics), structured log fields with request-id |
| H3 | HIGH | `CF_ACCESS_AUDIENCE` change on Access-app rebuild breaks everything silently | Documented in §10; plan pre-flight requires re-copying AUD after any CF teardown/recreate |
| M1 | MEDIUM | Cache + pub/sub + TTL is 3-layer redundancy for solo-dev | **Kept as-spec'd per user decision.** Rationale: coherence model is solid; removing invalidation requires stop-gap caching discussion later. |
| M2 | MEDIUM | Plaintext secret read-back via GET leaks through access logs | Replaced with explicit `POST /.../reveal` endpoint; response has `Cache-Control: no-store`; reveals are counted + logged with actor identity |
| M3 | MEDIUM | `value TEXT` for JSON values forfeits Postgres JSONB features | Added `value_json JSONB` column + CHECK constraint that exactly one of `value`/`value_json` is set |
| M4 | MEDIUM | PUT body-vs-URL consistency undefined; DELETE-404 semantics inconsistent with REST | PUT → 422 on mismatch; DELETE → always 204 (idempotent); both documented in §4 |
| M5 | MEDIUM | `APP_SECRET_KEY` rotation is one-way door | Added `APP_SECRET_KEY_PREV` optional env + MultiFernet fallback; rotation workflow documented in §10 |
| L1 | LOW | HKDF `salt=None` ≡ zero salt; explicit constant improves clarity | HKDF now uses `salt=b"dashboard.v1"` + `info=b"app_secrets.fernet.v1"` |
| L2 | LOW | JWKS kid-miss should force refresh, not wait for 1h TTL | Implemented in §5c step 3 (`_jwks_client.invalidate_cache()` + retry) |
| L3 | LOW | fakeredis pub/sub ≠ real Redis in corner cases | One dedicated CI test runs against a real `redis:7-alpine` service |
| L4 | LOW | Migration in multi-worker compose could race | Relying on Alembic's `pg_advisory_lock(0)` (safe); dedicated `migrate` service considered but not adopted (adds compose complexity for minimal gain) |
| L5 | LOW | `/health` should report ConfigService state | Deferred — `/metrics` covers richer state; `/health` stays shape-compatible with Phase 0 contract |

---

*End of design spec. Next step per phase workflow: user approval of this spec → writing-plans.*
