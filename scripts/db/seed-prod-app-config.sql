-- Seed prod app_config with safe defaults + fix one typo'd app_secrets row.
--
-- Idempotent: every INSERT uses ON CONFLICT (namespace, key) DO NOTHING so
-- re-running won't overwrite values you've set via the admin API. Drop into
-- a one-shot psql session against prod (NUC PG 10.10.0.2):
--
--   . .env  # loads DATABASE_URL with prod creds
--   uv --project backend run python - <<'PY'
--   import asyncio, os
--   from sqlalchemy.ext.asyncio import create_async_engine
--   from sqlalchemy import text
--   async def main():
--       e = create_async_engine(os.environ['DATABASE_URL'])
--       with open('scripts/db/seed-prod-app-config.sql') as f:
--           sql = f.read()
--       async with e.begin() as c:
--           # Split on top-level ';' so each statement runs independently.
--           for stmt in sql.split(';'):
--               s = stmt.strip()
--               if not s or s.startswith('--'):
--                   continue
--               await c.execute(text(s))
--       print('seeded')
--   asyncio.run(main())
--   PY
--
-- Or just run from psql:
--   psql "$DATABASE_URL_sync" -f scripts/db/seed-prod-app-config.sql
--
-- After running, override specific values via the admin API:
--   PUT /api/admin/config { "namespace": "broker", "key": "...", "value": "...", "value_type": "..." }
--
-- This script does NOT touch app_secrets (they're Fernet-encrypted and must
-- go through the admin API or ConfigService.set_secret). One exception: the
-- Alpaca key rename is a metadata-only fix that's safe in raw SQL.

-- ─── app_secrets schema migration ───────────────────────────────────────────
-- Alpaca credentials use a dotted hierarchy (`alpaca.<mode>.api_*`) NOT
-- hyphenated (`alpaca-<mode>.api_*`). The hyphenated form matches the
-- gateway_label spelling but `broker_registry_factory._configure_alpaca`
-- reads the dotted form, so the hyphenated rows silently fail Configure.
-- This DO block performs both renames idempotently in one pass:
--   1. `alpaca-papaer.api_secret` (historical typo) -> dotted form
--   2. `alpaca-{mode}.api_{key,secret}` -> `alpaca.{mode}.api_{key,secret}`
DO $$
BEGIN
    -- Step 1: collapse the legacy 'papaer' typo into the canonical form
    -- (still hyphenated at this point; step 2 then re-canonicalizes).
    IF EXISTS (SELECT 1 FROM app_secrets WHERE namespace='broker' AND key='alpaca-papaer.api_secret')
    AND NOT EXISTS (SELECT 1 FROM app_secrets WHERE namespace='broker' AND key='alpaca-paper.api_secret') THEN
        UPDATE app_secrets SET key='alpaca-paper.api_secret', updated_at=now()
         WHERE namespace='broker' AND key='alpaca-papaer.api_secret';
        RAISE NOTICE 'renamed alpaca-papaer.api_secret -> alpaca-paper.api_secret';
    END IF;
    -- Step 2: rename each hyphenated key to its dotted counterpart, but
    -- only if the dotted form doesn't already exist.
    FOR r IN
        SELECT old_key, new_key FROM (VALUES
            ('alpaca-paper.api_key',    'alpaca.paper.api_key'),
            ('alpaca-paper.api_secret', 'alpaca.paper.api_secret'),
            ('alpaca-live.api_key',     'alpaca.live.api_key'),
            ('alpaca-live.api_secret',  'alpaca.live.api_secret')
        ) AS t(old_key, new_key)
    LOOP
        IF EXISTS (SELECT 1 FROM app_secrets WHERE namespace='broker' AND key=r.old_key)
        AND NOT EXISTS (SELECT 1 FROM app_secrets WHERE namespace='broker' AND key=r.new_key) THEN
            UPDATE app_secrets SET key=r.new_key, updated_at=now()
             WHERE namespace='broker' AND key=r.old_key;
            RAISE NOTICE 'renamed % -> %', r.old_key, r.new_key;
        END IF;
    END LOOP;
END$$;

-- ─── broker namespace: per-label trade_enabled flags ────────────────────────
-- All 7 broker labels with trade_enabled=false. Operators toggle via the
-- admin UI; the chain tests already set isa-paper=true (skip if present).
INSERT INTO app_config (namespace, key, value, value_type) VALUES
    ('broker', 'isa-live.trade_enabled',      'false', 'bool'),
    ('broker', 'normal-paper.trade_enabled',  'false', 'bool'),
    ('broker', 'normal-live.trade_enabled',   'false', 'bool'),
    ('broker', 'alpaca-paper.trade_enabled',  'false', 'bool'),
    ('broker', 'alpaca-live.trade_enabled',   'false', 'bool'),
    ('broker', 'futu.trade_enabled',          'false', 'bool'),
    ('broker', 'schwab.trade_enabled',        'false', 'bool')
ON CONFLICT (namespace, key) DO NOTHING;

-- ─── broker namespace: Futu OpenD config (secrets exist, config doesn't) ────
INSERT INTO app_config (namespace, key, value, value_type) VALUES
    ('broker', 'futu.opend_host', '10.10.0.2', 'str'),
    ('broker', 'futu.opend_port', '11111',     'str')
ON CONFLICT (namespace, key) DO NOTHING;

-- ─── broker namespace: Schwab tier2 tracking + callback ─────────────────────
-- callback_url left empty for the operator to set per environment (e.g.
-- https://dashboard.kiusinghung.com/api/oauth/schwab/callback). tier2_*
-- counters start at zero / disabled.
INSERT INTO app_config (namespace, key, value, value_type) VALUES
    ('broker', 'schwab.callback_url',                '', 'str'),
    ('broker', 'schwab.tier2_refresh_enabled',  'false', 'bool'),
    ('broker', 'schwab.tier2_consecutive_failures', '0', 'int')
ON CONFLICT (namespace, key) DO NOTHING;

-- ─── ai_router namespace: capability_map ────────────────────────────────────
-- Empty JSON object — the LLM router has code-side capability defaults; the
-- override map only needs to exist if operator overrides are wanted.
INSERT INTO app_config (namespace, key, value_json, value_type) VALUES
    ('ai_router', 'capability_map', '{}'::jsonb, 'json')
ON CONFLICT (namespace, key) DO NOTHING;

-- ─── charts namespace ───────────────────────────────────────────────────────
INSERT INTO app_config (namespace, key, value, value_type) VALUES
    ('charts', 'chart_layout_schema_version', '1', 'int')
ON CONFLICT (namespace, key) DO NOTHING;

-- ─── broker namespace: OCO + connection_id placeholders ─────────────────────
-- Futu connection_id is optional (empty string means no override).
INSERT INTO app_config (namespace, key, value, value_type) VALUES
    ('broker', 'futu.connection_id', '', 'str')
ON CONFLICT (namespace, key) DO NOTHING;
