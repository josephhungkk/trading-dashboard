# app_config / app_secrets Inventory

**Generated:** 2026-05-12 (Phase 11a CI-debt sweep — recovery from empty-prod-DB finding)
**Updated:** 2026-05-14 (fix Alpaca key schema to dotted form; remove IBKR per-label rows — never read by app; add futu.unlock_pwd_md5; note prod-wipe root-cause fix in 0048)

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
| `litellm_master_key` | LiteLLM master key for proxy auth | `app/main.py` — cached in Redis after first reveal |

### `ai_provider` namespace (per-provider, dynamic keys)
| Key pattern | Purpose | Source |
|---|---|---|
| `<provider>.api_key` | API key for cloud LLM provider | `litellm_auth_callback.py`, `provider_keys.py` — providers: `anthropic`, `openai`, `xai`, `gemini` |

### `broker` namespace — IBKR
IBKR labels (`isa-live`, `isa-paper`, `normal-live`, `normal-paper`) connect via mTLS at the gRPC transport level.
`BrokerConfigurer.configure()` is **not called** for IBKR labels (they are not in `targets`).
Only the three fleet-wide mTLS secrets are needed — no per-label rows.

| Key | Purpose | Source |
|---|---|---|
| `mtls.client_cert_pem` | mTLS client cert (fleet-wide, seeded by `provision-and-publish.ps1`) | `broker_registry_factory.py` |
| `mtls.client_key_pem` | mTLS client private key | `broker_registry_factory.py` |
| `mtls.ca_bundle_pem` | mTLS CA bundle | `broker_registry_factory.py` |

### `broker` namespace — Futu (label = `futu`)
| Key | Purpose | Source |
|---|---|---|
| `futu.rsa_priv_pem` | 1024-bit RSA key for OpenD pairing — MUST be 1024-bit (memory: futu_1024_rsa_key.md) | `broker_registry_factory.py` (`{label}.rsa_priv_pem`) |
| `futu.unlock_pwd_md5` | OpenD unlock password MD5 hash | `broker_registry_factory.py` (`{label}.unlock_pwd_md5`) |

### `broker` namespace — Schwab
| Key | Purpose | Source |
|---|---|---|
| `schwab.app_key` | Schwab developer app key | `broker_registry_factory.py`, `schwab_oauth.py` |
| `schwab.app_secret` | Schwab developer app secret | `broker_registry_factory.py` |
| `schwab.refresh_token` | Schwab OAuth refresh token (rotates every 90d) | `broker_registry_factory.py`, `schwab_oauth.py` |
| `schwab.access_token` | Schwab OAuth access token (30-min TTL, written by OAuth flow) | `tier2_refresher.py` |

### `broker` namespace — Alpaca (dotted schema)
Legacy key form (single-account): `alpaca.<mode>.api_key` e.g. `alpaca.paper.api_key`
Labeled form (multi-account): `alpaca.<account_label>.<mode>.api_key`

| Key | Purpose | Source |
|---|---|---|
| `alpaca.paper.api_key` | Alpaca paper API key | `broker_registry_factory.py` |
| `alpaca.paper.api_secret` | Alpaca paper API secret | `broker_registry_factory.py` |
| `alpaca.live.api_key` | Alpaca live API key | `broker_registry_factory.py` |
| `alpaca.live.api_secret` | Alpaca live API secret | `broker_registry_factory.py` |

**Note:** Key schema is dotted (`alpaca.paper.api_key`), NOT hyphenated (`alpaca-paper.api_key`). Hyphenated form silently fails the Configure call.

### `testing` namespace (real-broker test gates)
| Key | Purpose | Source |
|---|---|---|
| `cf_access_client_id` | CF Access service token ID for IBKR E2E test (hits prod ingress) | `real_broker/conftest.py` |
| `cf_access_client_secret` | CF Access service token secret | `real_broker/conftest.py` |

---

## app_config (plaintext)

### `broker` namespace — global
| Key | Type | Purpose | Source |
|---|---|---|---|
| `kill_switch_enabled` | bool | Global kill switch | `orders_service.py`, `risk_service.py` |
| `kill_switch` | bool | Legacy alias for kill switch | `risk_service.py` |
| `oco.enabled` | bool | OCO endpoint feature flag | `orders_service.py` |
| `nuc_wg_host` | str | NUC WireGuard IP override (default `10.10.0.2`) | `quotes/engine_factory.py` |
| `quote_source_priority` | json | Per-asset-class quote source ordering | `quotes/engine_factory.py` |
| `ibkr_gateway_quote_assignment` | json | Quote source → IBKR label routing | `quotes/engine_factory.py` |
| `ibkr_gateway_quote_fallback` | json | IBKR quote fallback list | `quotes/engine_factory.py` |
| `<gateway_label>.trade_enabled` | bool | Per-label trade enable flag | `orders_service.py` |

### `broker` namespace — Schwab (written by OAuth flow, not seeded manually)
| Key | Type | Purpose | Source |
|---|---|---|---|
| `schwab.refresh_token_issued_at` | str (ISO) | When refresh token was minted | `schwab_oauth.py` |
| `schwab.access_token_issued_at` | str (ISO) | When access token was minted | `schwab_oauth.py` |
| `schwab.callback_url` | str | OAuth callback URL | `schwab_oauth.py` |
| `schwab.tier2_refresh_enabled` | bool | Tier-2 (Playwright auto-refresh) flag | `schwab_oauth.py` |
| `schwab.tier2_consecutive_failures` | int | Tier-2 failure counter | `schwab_oauth.py` |

### `broker` namespace — Futu
| Key | Type | Purpose | Source |
|---|---|---|---|
| `futu.opend_host` | str | Futu OpenD host (default `127.0.0.1`) | `broker_registry_factory.py` |
| `futu.opend_port` | str | Futu OpenD port (default `11111`) | `broker_registry_factory.py` |
| `futu.connection_id` | str | Futu connection identifier (optional) | `broker_registry_factory.py` |

### `ai_router` namespace
| Key | Type | Purpose | Source |
|---|---|---|---|
| `capability_map` | json | Per-capability model routing map | `app/main.py` — seeded by Phase 11b migration |

### `alert_capabilities` namespace
| Key | Type | Purpose | Source |
|---|---|---|---|
| `capability_map` | json | Alert engine capability config | Phase 11b migration — seeded automatically |

### `charts` namespace
| Key | Type | Purpose | Source |
|---|---|---|---|
| `chart_layout_schema_version` | int | klinecharts schema version | `chart_layouts` migration logic |

### `testing` namespace (real-broker test gates)
| Key | Type | Purpose | Source |
|---|---|---|---|
| `ibkr_test_enabled` | bool | Opt-in to run real IBKR paper E2E test | `real_broker/conftest.py` |
| `ibkr_paper_account` | str | IBKR paper account UUID | `real_broker/conftest.py` |
| `futu_test_enabled` | bool | Opt-in to run real Futu paper E2E test (default false — OpenD thread-hang risk) | `real_broker/conftest.py` |
| `schwab_account_hash` | str | Schwab live account hash for E2E tests (unfillable prices used) | `real_broker/conftest.py` |

---

## Re-seed procedure (after a prod DB wipe)

1. **Run `deploy/nuc/provision-and-publish.ps1`** — seeds 3 mTLS secrets directly via admin API.

2. **Verify admin endpoint** is up:
   ```
   curl -H "CF-Access-Client-Id: $TOKEN_ID" -H "CF-Access-Client-Secret: $TOKEN_SECRET" \
     https://dashboard.kiusinghung.com/api/admin/config
   ```

3. **Run placeholder seeder** to create REPLACE_ME rows for all broker secrets:
   ```
   docker compose exec -T -w /app backend bash -c \
     'PYTHONPATH=/app uv run python /tmp/seed-secrets.py'
   # (copy scripts/db/seed-prod-app-secrets-placeholders.py to /tmp/seed-secrets.py first)
   ```

4. **Seed Schwab** in order (per memory `feedback_schwab_app_key_seed_order.md`):
   ```
   PUT /api/admin/secrets { ns: broker, key: schwab.app_key, value: ... }
   PUT /api/admin/secrets { ns: broker, key: schwab.app_secret, value: ... }
   # Then trigger OAuth flow → seeds schwab.refresh_token + schwab.access_token
   ```

5. **Seed Alpaca** (dotted schema — NOT hyphenated):
   ```
   PUT /api/admin/secrets { ns: broker, key: alpaca.paper.api_key, value: ... }
   PUT /api/admin/secrets { ns: broker, key: alpaca.paper.api_secret, value: ... }
   PUT /api/admin/secrets { ns: broker, key: alpaca.live.api_key, value: ... }
   PUT /api/admin/secrets { ns: broker, key: alpaca.live.api_secret, value: ... }
   ```

6. **Seed Futu** (1024-bit RSA only — see memory futu_1024_rsa_key.md):
   ```
   PUT /api/admin/secrets { ns: broker, key: futu.rsa_priv_pem, value: <1024-bit PEM> }
   PUT /api/admin/secrets { ns: broker, key: futu.unlock_pwd_md5, value: <MD5 hash of unlock pwd> }
   PUT /api/admin/config  { ns: broker, key: futu.opend_host, value: 10.10.0.2 }
   PUT /api/admin/config  { ns: broker, key: futu.opend_port, value: 11111 }
   ```

7. **Seed CF Access creds** (for IBKR real-broker tests):
   ```
   PUT /api/admin/secrets { ns: testing, key: cf_access_client_id, value: ... }
   PUT /api/admin/secrets { ns: testing, key: cf_access_client_secret, value: ... }
   ```

8. **Seed app_config defaults**:
   ```
   POST /api/admin/config { ns: broker, key: kill_switch_enabled, value: false, value_type: bool }
   POST /api/admin/config { ns: testing, key: ibkr_test_enabled, value: true, value_type: bool }
   POST /api/admin/config { ns: testing, key: futu_test_enabled, value: false, value_type: bool }
   ```

9. **Run `./scripts/db/copy-prod-creds-to-test-pg.sh`** to mirror into test_postgres.

---

## Notes

- **IBKR per-label rows removed (2026-05-14):** `isa-paper.*`, `isa-live.*`, `normal-paper.*`, `normal-live.*` (`rsa_priv_pem` + `unlock_pwd_md5`) were seeded as placeholders but are never read. IBKR labels are not in `BrokerConfigurer.targets` — they connect via mTLS transport only.
- **APP_SECRET_KEY rotation invalidates all `app_secrets` ciphertext.** If a wipe followed a key rotation, all secrets must be re-entered even if the rows survive.
- **`account_kill_switches`, `risk_limits`** are operator-tunable but defaults work without them.
- **`alert_capabilities/capability_map`** and **`ai_router/capability_map`** are seeded automatically by alembic migrations — do not seed manually.
