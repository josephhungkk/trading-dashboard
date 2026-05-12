# app_config / app_secrets Inventory

**Generated:** 2026-05-12 (Phase 11a CI-debt sweep — recovery from empty-prod-DB finding)

**Purpose:** authoritative list of every `(namespace, key)` row the application
expects to find in `app_config` (plaintext settings) and `app_secrets`
(Fernet-encrypted credentials), derived by grep over `app/services/`
and `app/api/`. Use this when re-seeding prod after a wipe.

Schema reference: see `backend/alembic/versions/0002_app_config_secrets.py`
(both tables have composite PK `(namespace, key)`).

Seed via the admin API per memory `feedback_schwab_app_key_seed_order.md`:
```
PUT /api/admin/config        { "namespace": "...", "key": "...", "value": "...", "value_type": "str|int|bool|json" }
PUT /api/admin/secrets       { "namespace": "...", "key": "...", "value": "..." }
```

---

## app_secrets (Fernet-encrypted)

### `ai` namespace
| Key | Purpose | Source |
|---|---|---|
| `litellm_master_key` | LiteLLM master key for proxy auth | `app/main.py:207` — `redis.get("ai:litellm_master_key")` (cached after first `reveal_secret`) |

### `ai_provider` namespace (per-provider, dynamic keys)
| Key pattern | Purpose | Source |
|---|---|---|
| `<provider>.api_key` | API key for cloud LLM provider | `app/main.py:228`, `litellm_auth_callback.py:41`, `provider_keys.py:77` — providers seen: `anthropic`, `openai`, `xai`, `gemini`, `xai-grok`, `gemini-pro`, `anthropic-sonnet`, `openai-gpt4o` |

### `broker` namespace — IBKR (per-gateway-label)
Labels (from `SIDECAR_PORTS`): **`isa-live`, `isa-paper`, `normal-live`, `normal-paper`**

| Key pattern | Purpose | Source |
|---|---|---|
| `mtls.client_cert_pem` | mTLS client cert (one per fleet, NOT per-label) | `broker_registry_factory.py:295` |
| `mtls.client_key_pem` | mTLS client private key | `broker_registry_factory.py:296` |
| `mtls.ca_bundle_pem` | mTLS CA bundle | `broker_registry_factory.py:297` |
| `<label>.unlock_pwd_md5` | IBC unlock password (MD5 hash for IBKR Gateway) | `broker_registry_factory.py:113` |
| `<label>.rsa_priv_pem` | Per-label RSA private key for IBC secondary auth | `broker_registry_factory.py:116` |

### `broker` namespace — Futu (label = `futu`)
| Key | Purpose | Source |
|---|---|---|
| `futu.rsa_priv_pem` | 1024-bit RSA key for OpenD pairing (memory:futu_1024_rsa_key.md says it MUST be 1024-bit) | `broker_registry_factory.py:116` (`{label}.rsa_priv_pem`) |

### `broker` namespace — Schwab
| Key | Purpose | Source |
|---|---|---|
| `schwab.app_key` | Schwab developer app key | `broker_registry_factory.py:166`, `schwab_oauth.py:68/132`, `sidecar_schwab_oauth.py:90` |
| `schwab.app_secret` | Schwab developer app secret | `broker_registry_factory.py:167` (and same call sites as above) |
| `schwab.refresh_token` | Schwab OAuth refresh token (rotates every 90d) | `broker_registry_factory.py:168`, `schwab_oauth.py:72`, `tier2_refresher.py:303` |
| `schwab.access_token` | Schwab OAuth access token (30-min TTL) | `tier2_refresher.py:229` |

### `broker` namespace — Alpaca (per-mode label)
Labels: **`alpaca-live`, `alpaca-paper`**

| Key pattern | Purpose | Source |
|---|---|---|
| `<label>.api_key` | Alpaca API key | `sidecar_alpaca.py:259` (with `legacy_key` fallback) |
| `<label>.api_secret` | Alpaca API secret | `sidecar_alpaca.py:262` (with `legacy_secret` fallback) |

---

## app_config (plaintext)

### `broker` namespace
| Key | Type | Purpose | Source |
|---|---|---|---|
| `kill_switch` | bool | Global kill switch (legacy name) | `risk_service.py` — `cfg.get_bool` |
| `kill_switch_enabled` | bool | Global kill switch (current name) | `orders_service.py` — `cfg.get_bool` |
| `oco.enabled` | bool | OCO endpoint feature flag | `orders_service.py` |
| `nuc_wg_host` | str | NUC WireGuard IP override (default `10.10.0.2`) | `engine_factory.py:127` |
| `quote_source_priority` | json | Per-asset-class quote source ordering | `engine_factory.py:107` |
| `ibkr_gateway_quote_assignment` | json | Quote source → IBKR label routing | `engine_factory.py:108` |
| `ibkr_gateway_quote_fallback` | json | IBKR quote fallback list | `engine_factory.py:109` |
| `<gateway_label>.trade_enabled` | bool | Per-label trade enable flag (used by E2E chain tests + admin UI) | `orders_service.py` |

### `broker` namespace — Schwab
| Key | Type | Purpose | Source |
|---|---|---|---|
| `schwab.refresh_token_issued_at` | str (ISO) | When refresh token was minted | `schwab_oauth.py:199/222` |
| `schwab.access_token_issued_at` | str (ISO) | When access token was minted | `schwab_oauth.py` |
| `schwab.callback_url` | str | OAuth callback URL | `schwab_oauth.py` |
| `schwab.tier2_refresh_enabled` | bool | Tier-2 (Playwright auto-refresh) flag | `schwab_oauth.py` |
| `schwab.tier2_consecutive_failures` | int | Tier-2 failure counter | `schwab_oauth.py` |

### `broker` namespace — Futu (per-label)
| Key pattern | Type | Purpose | Source |
|---|---|---|---|
| `<label>.opend_host` | str | Futu OpenD host (default `127.0.0.1`) | `broker_registry_factory.py:120` |
| `<label>.opend_port` | str | Futu OpenD port (default `11111`) | `broker_registry_factory.py:127` |
| `<label>.connection_id` | str | Futu connection identifier (optional) | `broker_registry_factory.py:135` |

### `ai_router` namespace
| Key | Type | Purpose | Source |
|---|---|---|---|
| `capability_map` | json | Per-model capability override (LLM routing) | `main.py:217` |

### `charts` namespace
| Key | Type | Purpose | Source |
|---|---|---|---|
| `chart_layout_schema_version` | int | klinecharts schema version | Used by `chart_layouts` migration logic |

---

## What's likely missing for Phase 11a unskipping

For the 9 real-broker E2E tests to pass through the DB-driven approach, the following NEEDS to exist in `app_secrets` / `app_config`:

### Schwab (3 tests + `CI_USE_REAL_SCHWAB=1` smoke + capability-drift)
- ✅ `app_secrets[broker/schwab.app_key]`
- ✅ `app_secrets[broker/schwab.app_secret]`
- ✅ `app_secrets[broker/schwab.refresh_token]` (one-time interactive OAuth seed needed via `scripts/mint_schwab_access_token.py`)
- `app_config[broker/schwab.paper_account_hash]` — NEW (not yet referenced in code; needed by tests that target paper specifically)

### IBKR (3 tests)
- ✅ `app_secrets[broker/mtls.client_cert_pem]` + `mtls.client_key_pem` + `mtls.ca_bundle_pem` (`deploy/nuc/provision-and-publish.ps1` is the canonical seeder)
- ✅ `app_secrets[broker/isa-paper.unlock_pwd_md5]` + `isa-paper.rsa_priv_pem` (per-label IBC creds)
- NEW for tests: `app_config[testing/ibkr_paper_account]` (the DU***** account number)
- NEW for tests: `app_secrets[testing/cf_access_client_id]` + `cf_access_client_secret` (CF Access service token for hitting prod ingress)

### Alpaca (2 tests)
- ✅ `app_secrets[broker/alpaca-paper.api_key]`
- ✅ `app_secrets[broker/alpaca-paper.api_secret]`

### Futu (1 test)
- ✅ `app_secrets[broker/futu.rsa_priv_pem]`
- ✅ `app_config[broker/futu.opend_host]` + `futu.opend_port`

### CI flags (kept in `.env.test`, NOT in DB)
These are test-runner toggles, not app config:
- `CI_USE_REAL_REDIS=1` + `CI_REDIS_URL`
- `CI_USE_REAL_SCHWAB=1`
- `E2E_BACKEND_URL` + `E2E_JWT`

---

## Re-seed procedure (after fixing the VPS 502)

1. **Fix VPS 502 first** — per memory `feedback_post_deploy_broker_recovery.md`:
   ```
   ssh -p 2222 trader@88.208.197.219
   cd trading-dashboard
   # Check what's broken
   docker compose ps
   docker compose logs backend --tail 50
   # Standard recovery
   docker compose restart backend
   docker compose restart nginx     # per memory:nginx_backend_recreate_502.md
   ```

2. **Verify admin endpoint** is up:
   ```
   curl -H "CF-Access-Client-Id: $TOKEN_ID" -H "CF-Access-Client-Secret: $TOKEN_SECRET" \
     https://dashboard.kiusinghung.com/api/admin/config
   ```

3. **Mint mTLS** (IBKR) via `deploy/nuc/provision-and-publish.ps1` — that script writes 3 secrets directly via the admin API.

4. **Seed Schwab** in order (per memory `feedback_schwab_app_key_seed_order.md`):
   ```
   PUT /api/admin/secrets { ns: broker, key: schwab.app_key, value: ... }
   PUT /api/admin/secrets { ns: broker, key: schwab.app_secret, value: ... }
   # Then trigger OAuth flow → seeds schwab.refresh_token + schwab.access_token
   ```

5. **Seed Alpaca**:
   ```
   PUT /api/admin/secrets { ns: broker, key: alpaca-paper.api_key, value: ... }
   PUT /api/admin/secrets { ns: broker, key: alpaca-paper.api_secret, value: ... }
   ```

6. **Seed Futu**:
   ```
   PUT /api/admin/secrets { ns: broker, key: futu.rsa_priv_pem, value: <1024-bit PEM> }
   PUT /api/admin/config  { ns: broker, key: futu.opend_host, value: 10.10.0.2 }
   PUT /api/admin/config  { ns: broker, key: futu.opend_port, value: 11111 }
   ```

7. **Run `./scripts/db/copy-prod-creds-to-test-pg.sh`** to mirror into test_postgres.

8. **Run real-broker tests** — should now pass through the DB-read fixture path.

---

## Notes

- **Restoration sources**: the IBC unlock passwords + RSA keys were originally generated by `deploy/nuc/provision-and-publish.ps1`. mTLS was rotated via `deploy/nuc/renew-sidecar-mtls.ps1`. If the prod DB has been wiped, you need to re-run these provisioners.
- **APP_SECRET_KEY rotation invalidates all `app_secrets` ciphertext.** The `feedback_pytest_prod_db_wipe.md` memory and the `MissingBrokerSecrets` path in `broker_registry_factory.py:309` both flag this.
- **`account_kill_switches`, `risk_limits`** are also operator-tunable but currently empty — re-seed at your own pace (defaults work without them).
