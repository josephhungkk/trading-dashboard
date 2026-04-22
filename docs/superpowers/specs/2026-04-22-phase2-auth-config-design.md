# Phase 2 — Auth + DB-backed config service (design)

**Status:** draft (pending architect review)
**Date:** 2026-04-22
**Follows:** Phase 1 `v0.1.0` (VPS cutover + CF Tunnel + CF Access, shipped 2026-04-22)
**Phase workflow step:** 1 (brainstorm) → 2 (self-review) → 3 (architect review) → 4 (user approval) → 5 (plan) → 6 (impl) → 7 (close-out)

---

## 1. Goal

Move runtime configuration out of `.env` and into two Postgres tables (`app_config` plain, `app_secrets` Fernet-encrypted), with a `ConfigService` layer that caches in-memory and invalidates across backend workers via Redis pub/sub. Protect admin-facing endpoints with CF Access JWT verification. Ship as `v0.2.0`.

## 2. Background

- Phase 1 deployed a hardened production stack (CF Tunnel + CF Access + nginx + hardened docker-compose). `.env` currently holds bootstrap values only: `APP_ENV`, `APP_SECRET_KEY`, `APP_CORS_ORIGINS`, `DATABASE_URL`, `POSTGRES_POOL_SIZE`, `POSTGRES_MAX_OVERFLOW`, `REDIS_PASSWORD`, `REDIS_URL`.
- CLAUDE.md §"Configuration Storage" commits the project to DB-backed config. Phase 2 implements that commitment.
- Backend is currently at Phase 0 scaffold + Phase 1 prod compose. `/health` is the only route. `app/api/`, `app/services/`, `app/models/` are empty packages. Alembic is wired async with `target_metadata = None`; no migrations exist yet.
- CF Access allowlist at the edge: `josephhungkk@gmail.com`, `ispyling@gmail.com`. Service token `dashboard-ci-smoke` (expires 2027-04-22) bypasses Access for CI.

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
         • Valid JWT = admin (CF Access's 2-email allowlist IS the allowlist)

Auth path (dev, APP_ENV=dev):
  curl → WG → 10.10.0.1:80 → nginx → backend
         • No CF JWT header present
         • require_admin_jwt short-circuits with synthetic AdminIdentity(email="dev@localhost")
```

### Key properties

- **Auth model:** CF Access JWT only. Backend fetches JWKS from `https://kiusinghung.cloudflareaccess.com/cdn-cgi/access/certs` (cached 1 hour), verifies RS256 signature + `exp` + `iss` + `aud`, extracts `email` claim. No secondary allowlist; no local users table; no session cookie. CF Access's 2-email allowlist at the edge is the single source of truth.
- **Cache coherence:** each worker holds its own dict. On write, writer evicts its entry → DB commit → Redis PUBLISH `"{namespace}|{key}"` on `config:invalidate`. Subscribers evict the same entry. Cache entries also have a 5-minute TTL as a safety net if Redis publish is lost.
- **Fernet key derivation:** `cryptography.hazmat.primitives.kdf.hkdf.HKDF(SHA256).derive(APP_SECRET_KEY.encode(), info=b"app_secrets.fernet.v1", salt=None, length=32)` then `base64.urlsafe_b64encode(key)` → Fernet instance. Deterministic from `APP_SECRET_KEY`, stable across restarts. Rotating `APP_SECRET_KEY` invalidates all existing encrypted secrets (permanent, per CLAUDE.md).
- **No new bootstrap env:** `APP_SECRET_KEY` is already present; two new bootstrap keys (`CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUDIENCE`) are added — see §5c.

## 4. Components

Phase 2 adds these files. Each has one responsibility.

```
backend/app/
├── core/
│   ├── cf_access.py       [NEW]  JWKS fetcher + JWT verifier; pure logic, no DB
│   ├── crypto.py          [NEW]  Fernet key derivation + encrypt/decrypt helpers
│   └── deps.py                   Add require_admin_jwt dep; add get_config dep
├── models/
│   └── config.py          [NEW]  AppConfig + AppSecret SQLAlchemy declarative models
├── services/
│   ├── config.py          [NEW]  ConfigService public API (get/set/delete/list + typed + secrets)
│   └── config_cache.py    [NEW]  In-memory cache + Redis pub/sub SUBSCRIBE loop
├── api/
│   └── admin.py           [NEW]  FastAPI router: /api/admin/config + /api/admin/secrets CRUD
└── main.py                       Register admin router; ConfigService lifespan SUBSCRIBE

backend/alembic/versions/
└── 0001_app_config_and_secrets.py   [NEW]  Both tables + indexes; reversible

backend/tests/
├── test_cf_access.py      [NEW]  JWT decode paths; JWKS cache; dev-mode bypass
├── test_crypto.py         [NEW]  Fernet roundtrip; HKDF determinism
├── test_config_service.py [NEW]  CRUD + typed accessors + default handling
├── test_config_cache.py   [NEW]  Pub/sub invalidation across 2 fake workers
├── test_admin_api.py      [NEW]  Router end-to-end with mocked auth dep
├── test_admin_auth.py     [NEW]  Real auth dep — valid/expired/wrong signer/dev-bypass
└── test_migration.py      [NEW]  alembic upgrade/downgrade round-trip
```

### Schema (Alembic migration `0001`)

```sql
CREATE TABLE app_config (
    namespace   TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    value_type  TEXT NOT NULL CHECK (value_type IN ('str','int','bool','json')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, key)
);
CREATE INDEX ix_app_config_updated_at ON app_config (updated_at DESC);

CREATE TABLE app_secrets (
    namespace        TEXT NOT NULL,
    key              TEXT NOT NULL,
    value_encrypted  BYTEA NOT NULL,
    value_type       TEXT NOT NULL CHECK (value_type IN ('str','int','bool','json')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, key)
);
CREATE INDEX ix_app_secrets_updated_at ON app_secrets (updated_at DESC);
```

Both tables use `(namespace, key)` composite PK. `value_type` drives typed accessors. `created_at` + `updated_at` are auto-maintained via SQLAlchemy `server_default=func.now()` + `onupdate=func.now()`.

### ConfigService public API

```python
class ConfigService:
    async def get(self, ns: str, key: str, default: str | None = None) -> str | None
    async def get_int(self, ns: str, key: str, default: int | None = None) -> int | None
    async def get_bool(self, ns: str, key: str, default: bool | None = None) -> bool | None
    async def get_json(self, ns: str, key: str, default: Any = None) -> Any
    async def set(self, ns: str, key: str, value: Any, value_type: str = "str") -> AppConfig
    async def delete(self, ns: str, key: str) -> bool  # True if row existed
    async def list(self, namespace: str | None = None) -> list[AppConfig]

    async def get_secret(self, ns: str, key: str, default: str | None = None) -> str | None
    async def get_secret_int(self, ns: str, key: str, default: int | None = None) -> int | None
    async def get_secret_bool(self, ns: str, key: str, default: bool | None = None) -> bool | None
    async def get_secret_json(self, ns: str, key: str, default: Any = None) -> Any
    async def set_secret(self, ns: str, key: str, value: Any, value_type: str = "str") -> AppSecret
    async def delete_secret(self, ns: str, key: str) -> bool
    async def list_secrets(self, namespace: str | None = None) -> list[SecretMetadata]
    # ^ SecretMetadata has no `value`/`value_encrypted` field — metadata only in list()
```

Typed accessors coerce via `value_type`. Mismatch raises `ConfigTypeError` (a `ValueError` subclass) — fail-fast over silent wrong-type return.

### Admin REST API

```
GET    /api/admin/config                         → list all, or ?namespace=X to filter
GET    /api/admin/config/{namespace}/{key}       → single entry
POST   /api/admin/config                         → create (409 if exists)
PUT    /api/admin/config/{namespace}/{key}       → upsert
DELETE /api/admin/config/{namespace}/{key}       → 204 or 404

GET    /api/admin/secrets                        → list metadata (no plaintext)
GET    /api/admin/secrets/{namespace}/{key}      → single entry WITH decrypted plaintext
POST   /api/admin/secrets                        → create (409 if exists); body plaintext
PUT    /api/admin/secrets/{namespace}/{key}      → upsert plaintext
DELETE /api/admin/secrets/{namespace}/{key}      → 204 or 404
```

Request/response bodies (Pydantic):

```python
class ConfigIn(BaseModel):
    namespace: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    key:       str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$")
    value:     str
    value_type: Literal["str","int","bool","json"] = "str"

class ConfigOut(BaseModel):
    namespace: str
    key: str
    value: str
    value_type: str
    created_at: datetime
    updated_at: datetime
```

Secrets mirror this; `SecretOut` includes `value` (decrypted) on single-read, `SecretMetadataOut` omits `value` on list.

### New Python dependencies

Added to `pyproject.toml` `[project.dependencies]` via `uv add <pkg>` (pulls latest stable):

- `cryptography` — Fernet + HKDF (latest stable at scaffold time)
- `redis[hiredis]` — async Redis client + C parser (latest stable at scaffold time)
- `pyjwt[crypto]` — JWT verification with RS256 via cryptography (latest stable at scaffold time)
- `httpx` — JWKS HTTP fetch (add explicit; FastAPI may already transitively include)

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
        SELECT value, value_type FROM app_config WHERE namespace=$1 AND key=$2
            │
            ├── row found  ──▶ _cache[(ns, key)] = (value, value_type, now); return value
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
    1. INSERT INTO app_config (...) VALUES (...)
         ON CONFLICT (namespace, key) DO UPDATE SET
             value = EXCLUDED.value,
             value_type = EXCLUDED.value_type,
             updated_at = now();   ── atomic upsert
    │
    2. self._cache.pop((ns, key), None)     ── evict writer's local
    │
    3. try:
           await redis.publish("config:invalidate", f"{ns}|{key}")
       except (ConnectionError, TimeoutError):
           log.warning("config invalidation publish failed", ns=ns, key=key)
           # don't raise — DB write succeeded; TTL will recover
    │
    4. return ConfigOut
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
            await asyncio.sleep(min(2 ** attempt, 30))
            attempt += 1
```

Secrets follow the same flow on a separate channel `config:invalidate:secrets` so the two caches can evolve independently.

### 5c. Admin request auth lifecycle

```
curl (with CF-Access-Client-Id/Secret OR Google session cookie)
    │
    ▼
CF Tunnel → cloudflared → nginx → backend
    │
    │  CF attaches: Cf-Access-Jwt-Assertion: <RS256 JWT>
    │  nginx preserves header (proxy_set_header not needed — passthrough default)
    │
    ▼
FastAPI: @router.get(..., dependencies=[Depends(require_admin_jwt)])
    │
    ▼
require_admin_jwt(request):
    1. if settings.env == "dev":
           return AdminIdentity(email="dev@localhost", claims={})
    2. token = request.headers.get("Cf-Access-Jwt-Assertion")
       if not token: raise HTTPException(401, "missing cf-access jwt")
    3. jwks = await _cached_jwks(team_domain)   ── httpx GET, 1hr TTL
    4. claims = jwt.decode(
           token, jwks,
           algorithms=["RS256"],
           issuer=f"https://{team_domain}",
           audience=settings.cf_access_audience,
       )   ── raises jwt.PyJWTError subclass on bad sig/exp/claims
    5. return AdminIdentity(email=claims["email"], claims=claims)
    ── catch and convert specific PyJWT errors to 401 with distinct detail strings
```

**New bootstrap settings** (added to `app/core/config.py` `Settings`):

- `cf_access_team_domain: str = Field(alias="CF_ACCESS_TEAM_DOMAIN")` (e.g. `kiusinghung.cloudflareaccess.com`)
- `cf_access_audience: str = Field(alias="CF_ACCESS_AUDIENCE")` (the app's AUD tag — found in CF dashboard → Access → Apps → Dashboard → "Application Audience Tag")

`.env.example` gains these two keys. Dev doesn't need them (bypass returns synthetic identity).

## 6. Error handling

Fail-loud at boundaries (HTTP, admin writes), fail-soft internally (cache, pub/sub).

### 6a. Auth failures

| Condition | HTTP | Body | Log level |
|---|---|---|---|
| Header missing | 401 | `{"detail":"missing cf-access jwt"}` | INFO |
| Signature invalid | 401 | `{"detail":"jwt signature verification failed"}` | WARNING |
| Token expired | 401 | `{"detail":"jwt expired"}` | INFO |
| Issuer/audience mismatch | 401 | `{"detail":"jwt claims invalid"}` | WARNING |
| JWKS fetch failed | 503 | `{"detail":"identity service unavailable"}` | ERROR |
| Dev-bypass active but `APP_ENV=prod` | 500 | `{"detail":"internal error"}` | CRITICAL |

Emails in claims are never echoed in error responses (avoid disclosing who just tried).

### 6b. ConfigService failures

| Condition | Behavior |
|---|---|
| `get()` missing + no default | return `None` |
| `get()` missing + default given | return default |
| `get_int()` on row with `value_type="str"` | raise `ConfigTypeError` |
| `get_json()` on malformed JSON (invariant violation — only reachable if a row was written via raw SQL bypassing `set()`) | raise `ConfigTypeError` |
| `set()` with invalid `value_type` | raise `ValueError` before DB call (validated by Pydantic at HTTP boundary too) |
| `set_secret()` Fernet encrypt failure (cryptography internals) | 500 + log + rollback |
| `get_secret()` Fernet decrypt failure (`InvalidToken`) | 500 + log with key name (never plaintext); signals `APP_SECRET_KEY` rotated or row tampered |
| DB pool exhausted or connection drop | SQLAlchemy `pool_pre_ping` handles transient; route returns 503 on persistent |
| Redis publish fails | **log WARNING, don't raise** — DB write already succeeded; 5-min TTL recovers |
| Redis subscribe channel drops | reconnect loop with exponential backoff (1s → 30s cap); each reconnect logged |

### 6c. Cache coherence hazards

- **Race (write-then-concurrent-read)**: worker A writes + publishes; worker B is mid-`get()` and SELECTed the old value before commit was visible. B caches stale until next invalidation OR 5-min TTL. **Acceptable** — config writes are admin actions, not hot paths.
- **Cache stampede on cold start**: each worker independently queries DB on first access. Tables tiny, queries fast; no `singleflight` lock needed.
- **Orphan cache after row delete** (if publish was lost): 5-min TTL catches it.

### 6d. Migration / startup

- `alembic upgrade head` failure → container healthcheck fails → compose doesn't mark backend ready → deploy fails loud (no silent partial upgrade).
- `ConfigService` SUBSCRIBE fails at startup: log CRITICAL; retry in background with backoff. Routes keep serving from DB-only path (slower, degraded coherence) until Redis recovers. Degraded > offline.
- First-ever deploy: empty tables → every `get()` returns its `default` → app runs on bootstrap values → no seed data needed in migration.

## 7. Testing strategy

Target ≥85% overall coverage, **100% on `cf_access.py` + `crypto.py`** (security-critical).

### 7a. Unit (pure logic)

| File | Cases |
|---|---|
| `test_cf_access.py` | Valid RS256 (self-signed test keypair + stub JWKS) → claims extracted. Expired `exp`. Wrong `iss`. Wrong `aud`. Bad signature (flipped byte). Missing header. JWKS fetch 503. Dev-mode bypass when `APP_ENV=dev` (no header → synthetic identity). Prod + dev-bypass active → 500 fail-safe. |
| `test_crypto.py` | `derive_fernet_key(APP_SECRET_KEY)` deterministic across calls. `encrypt` + `decrypt` roundtrip for `b"hello"`, empty string, 1-MB blob. `decrypt` with wrong key raises `InvalidToken`. HKDF changes when salt/info change. |

### 7b. ConfigService integration (real Postgres, fake Redis)

| Test class | Cases |
|---|---|
| `TestConfigCRUD` | `set → get` roundtrip for str/int/bool/json. `delete` removes row. `list(ns)` filters correctly. `list()` returns all. |
| `TestTypedAccessors` | `get_int` on int-typed row → int. `get_int` on str-typed row → `ConfigTypeError`. Same for bool, json. `get()` always returns TEXT. |
| `TestDefaults` | `get("missing", "key", default=42)` → 42. `get("missing", "key")` → None. |
| `TestUpsert` | Two `set(ns, k, v1)` then `set(ns, k, v2)` — second wins; one row in DB. |
| `TestSecretRoundtrip` | `set_secret` → `get_secret` → plaintext. DB row has only ciphertext (verified via raw SQL). `list_secrets` returns metadata (no plaintext field). |
| `TestCacheCoherence` | `set(ns, k, v1)`; `get(ns, k)` → v1 (cached). Direct SQL UPDATE bypassing service; `get(ns, k)` still returns v1 (stale cache). Simulate pub/sub invalidation message → next `get()` returns new value. |
| `TestInvalidationOnFailure` | Monkeypatch Redis to raise on publish. `set()` still succeeds (DB-visible). Log captured at WARNING. Service doesn't raise. |

Backing: existing `DATABASE_URL` from pytest env (Phase 0 tests already use this for `/health`). `fakeredis[asyncio]` for Redis.

### 7c. Admin API end-to-end

`test_admin_api.py` uses FastAPI `TestClient` with `app.dependency_overrides[require_admin_jwt] = lambda: AdminIdentity(email="test@example.com")`.

| Endpoint | Cases |
|---|---|
| `GET /api/admin/config` | Empty → `[]`. 3 inserts → 3 entries. `?namespace=foo` filter. |
| `GET /api/admin/config/{ns}/{key}` | Exists → entry. Missing → 404. |
| `POST /api/admin/config` | Valid → 201 + entry. Invalid `value_type` → 422. Duplicate → 409. Invalid namespace pattern (uppercase) → 422. |
| `PUT /api/admin/config/{ns}/{key}` | Upsert — create if missing, update if exists. |
| `DELETE /api/admin/config/{ns}/{key}` | 204 on existing, 404 on missing. |
| Same matrix for `/api/admin/secrets` | Plus: GET single response has decrypted `value` field. Plus: list response has no `value` field. |
| Any admin endpoint WITHOUT dep override | 401 (confirms route is gated). |

### 7d. Auth contract

`test_admin_auth.py` — real `require_admin_jwt`, no dep override:

- No header + `APP_ENV=prod` → 401
- Malformed JWT (not 3 parts) → 401
- JWT signed with test keypair + JWKS fixture → 200
- Expired → 401
- No header + `APP_ENV=dev` → 200 with `email=dev@localhost`
- `APP_ENV=prod` + test harness trying to force dev-bypass → 500

### 7e. Migration

`test_migration.py`:

- `alembic upgrade head` from empty DB → both tables exist with expected columns + PKs + indexes.
- `alembic downgrade base` → tables gone.
- `alembic upgrade head` again → idempotent no-op (all migrations applied).

Uses the same test DB as other integration tests (pytest session fixture creates it once, drops on teardown). Alembic state is reset between tests via `downgrade base → upgrade head` in a function-scoped fixture so tests don't leak schema state.

## 8. Scope boundaries

### 8a. In scope (Phase 2)

- Two tables + one reversible Alembic migration
- ConfigService (cache + Redis pub/sub + Fernet secrets)
- CF Access JWT verification dep with dev-mode bypass
- Admin REST API (full CRUD on both tables; read-back plaintext for secrets)
- 7 test files; ≥85% coverage (100% on auth + crypto)
- Update CLAUDE.md "Configuration Storage" section — wording pivots from "Phase 2+" to "active"
- Update `backend/.env.example` with two new keys (`CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUDIENCE`)
- Update Phase 1's Playwright smoke test suite to include one admin-endpoint check (confirms auth gate stays green post-deploy)

### 8b. Out of scope (explicit deferrals)

- **Admin UI** → Phase 3 (part of the React shell)
- **Audit log** → later (when a second user role appears)
- **Role-based access** (admin vs viewer) → Phase 3+ if needed
- **Config versioning / change history** → nice-to-have, not now
- **Webhooks on change** → pub/sub covers backend; no external consumers yet
- **Secret rotation workflow** (rotate `APP_SECRET_KEY` safely) → its own mini-project; documented as permanent-for-now
- **Bulk import** (YAML → DB) → curl loop handles it ad-hoc
- **GraphQL / gRPC** → REST only
- **Rate limiting on `/api/admin/*`** → nginx already rate-limits `/api/*` at 10r/s with burst 20 (Phase 1 `dashboard.conf`); sufficient

### 8c. Dependencies / versions

New Python packages via `uv add`:

- `cryptography`
- `redis[hiredis]`
- `pyjwt[crypto]`
- `httpx` (explicit, in case not already pulled transitively)

`uv.lock` updated. No hand-picked versions.

### 8d. Rollout

- **CI path**: PR → `ci.yml` (backend tests include new suites) → merge → `deploy.yml` (rsync + build + compose up + Playwright extended smoke) → green.
- **Backend container start**: the entrypoint runs `alembic upgrade head` before `uvicorn`. A `scripts/entrypoint.sh` wrapper replaces the direct CMD; falls through to `/app/.venv/bin/uvicorn` on success; exits non-zero on migration failure (compose healthcheck fails → deploy stops).
- **Rollback**: `alembic downgrade -1` (drops both tables) + redeploy prior image tag. `app_config` / `app_secrets` data loss is acceptable on rollback because they're ops-written values, not user data.

## 9. Success criteria

Phase 2 ships when **all six** are demonstrable:

1. `alembic upgrade head` creates both tables from an empty DB with zero errors.
2. `backend/ pytest --cov` ≥ 85% overall; 100% on `core/cf_access.py` + `core/crypto.py`.
3. From the NUC, curl against prod with the CI service-token headers:
   - `POST /api/admin/config` with body `{"namespace":"test","key":"phase2","value":"ok","value_type":"str"}` → 201.
   - `GET /api/admin/config/test/phase2` → 200 with same values.
4. From the NUC, curl against prod with NO service-token AND no Google session cookie → 401 (CF Access 302 redirect proves auth still works).
5. `docker compose up --scale backend=2` + a config write on worker-1 → worker-2 reflects the change on the next `get()` within ≤5 seconds.
6. `CLAUDE.md`, `CHANGELOG.md`, `TASKS.md` updated; Phase 2 tagged `v0.2.0`; both CI + Deploy workflows green.

## 10. Open items / risks to track

- **`pyjwt` vs `python-jose`**: both handle RS256. `pyjwt` is more actively maintained and has stricter default validation. Pinning `pyjwt[crypto]` here.
- **Audience tag retrieval**: `CF_ACCESS_AUDIENCE` must be copied from the CF dashboard (Access → Apps → Dashboard → "Application Audience (AUD) Tag") after Phase 1's app was created. User action required once at Phase 2 start; captured in the plan's pre-flight step.
- **Redis password in pub/sub**: `REDIS_URL` already embeds the password (`redis://:$REDIS_PW@redis:6379/0`). `redis.asyncio.Redis.from_url(settings.redis_url)` uses it automatically.
- **First invocation pattern**: consumers (backend services added in Phases 4+) will call `await config.get(...)` inside async handlers. For sync contexts (e.g. a CLI tool), they'd need an async wrapper — out of scope here.

---

## Architect review — applied

*(This section gets populated after running `ARCHITECT-REVIEW` skill on this spec; findings ranked CRITICAL / HIGH / MEDIUM / LOW with concrete "change X to Y" recommendations. CRITICAL + HIGH must be applied before user approval.)*

---

*End of design spec. Next step per phase workflow: ARCHITECT-REVIEW → user approval → writing-plans.*
