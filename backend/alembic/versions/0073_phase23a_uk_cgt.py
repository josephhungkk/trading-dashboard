"""Phase 23a — UK CGT foundation schema

Revision ID: 0073
Down Revision: 0072
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto;"))

    # ── 1. Enrich fills ───────────────────────────────────────────────────
    op.execute(text("""
        ALTER TABLE fills
          ADD COLUMN IF NOT EXISTS instrument_id BIGINT REFERENCES instruments(id),
          ADD COLUMN IF NOT EXISTS side           VARCHAR(4) CHECK (side IN ('buy','sell')),
          ADD COLUMN IF NOT EXISTS bot_id         UUID REFERENCES bots(id)
    """))
    # Backfill side from orders; instrument_id left NULL for historical fills
    # (orders table does not carry instrument_id — set on new fills by consumer)
    op.execute(text("""
        UPDATE fills f
        SET side  = LOWER(o.side::text),
            bot_id = bo.bot_id
        FROM orders o
        LEFT JOIN bot_orders bo ON bo.order_id = o.id
        WHERE f.order_id = o.id
          AND f.side IS NULL
    """))

    # ── 2. hmrc_fx_rates ──────────────────────────────────────────────────
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS hmrc_fx_rates (
          currency      VARCHAR(8)     NOT NULL,
          period_month  DATE           NOT NULL,
          rate_gbp      NUMERIC(20,8)  NOT NULL CHECK (rate_gbp > 0),
          source        VARCHAR(32)    NOT NULL DEFAULT 'hmrc_monthly',
          PRIMARY KEY (currency, period_month)
        )
    """))

    # ── 3. broker_statements ──────────────────────────────────────────────
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS broker_statements (
          id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          broker_id      VARCHAR(32)    NOT NULL,
          account_id     UUID REFERENCES broker_accounts(id),
          statement_type VARCHAR(32)    NOT NULL,
          period_start   DATE           NOT NULL,
          period_end     DATE           NOT NULL,
          raw_content    BYTEA          NOT NULL,
          raw_format     VARCHAR(8)     NOT NULL DEFAULT 'gz_xml',
          raw_sha256     VARCHAR(64)    NOT NULL,
          fetched_at     TIMESTAMPTZ    NOT NULL DEFAULT now(),
          imported_at    TIMESTAMPTZ,
          UNIQUE (broker_id, account_id, statement_type, period_start, period_end, raw_sha256)
        )
    """))

    # ── 4. cgt_class_links ────────────────────────────────────────────────
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS cgt_class_links (
          instrument_id   BIGINT REFERENCES instruments(id) NOT NULL,
          cgt_class_key   VARCHAR(64) NOT NULL,
          PRIMARY KEY (instrument_id)
        )
    """))
    op.execute(text("""
        CREATE INDEX IF NOT EXISTS cgt_class_links_key_idx
          ON cgt_class_links (cgt_class_key)
    """))

    # ── 5. cgt_loss_carry_forward ─────────────────────────────────────────
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS cgt_loss_carry_forward (
          id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          account_id   UUID REFERENCES broker_accounts(id) NOT NULL,
          tax_year     SMALLINT       NOT NULL,
          source_year  SMALLINT       NOT NULL,
          amount_gbp   NUMERIC(20,8)  NOT NULL,
          entered_at   TIMESTAMPTZ    NOT NULL DEFAULT now(),
          notes        TEXT,
          UNIQUE (account_id, tax_year, source_year)
        )
    """))

    # ── 6. tax_events ─────────────────────────────────────────────────────
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS tax_events (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          fill_id             UUID REFERENCES fills(id),
          leg_index           SMALLINT       NOT NULL DEFAULT 0,
          broker_statement_id UUID REFERENCES broker_statements(id),
          external_event_id   VARCHAR(128),
          source              VARCHAR(16)    NOT NULL CHECK (source IN
            ('fill_live','broker_statement','manual','corp_action')),
          account_id          UUID REFERENCES broker_accounts(id) NOT NULL,
          instrument_id       BIGINT REFERENCES instruments(id) NOT NULL,
          cgt_track           VARCHAR(12)    NOT NULL CHECK (cgt_track IN
            ('pool','derivative','exempt')),
          event_type          VARCHAR(32)    NOT NULL,
          side                VARCHAR(4)     NOT NULL CHECK (side IN ('buy','sell')),
          is_short_open       BOOLEAN        NOT NULL DEFAULT FALSE,
          is_short_close      BOOLEAN        NOT NULL DEFAULT FALSE,
          qty                 NUMERIC(28,12) NOT NULL,
          price_gbp           NUMERIC(28,12) NOT NULL,
          commission_native   NUMERIC(28,12) NOT NULL DEFAULT 0,
          commission_currency VARCHAR(8)     NOT NULL DEFAULT 'GBP',
          commission_gbp      NUMERIC(28,12) NOT NULL DEFAULT 0,
          fx_rate             NUMERIC(20,8)  NOT NULL,
          fx_source           VARCHAR(32)    NOT NULL,
          original_currency   VARCHAR(8)     NOT NULL,
          cgt_class_key       VARCHAR(64),
          bb_remaining_qty    NUMERIC(28,12) NOT NULL DEFAULT 0,
          uk_trade_date       DATE GENERATED ALWAYS AS
            ((executed_at AT TIME ZONE 'Europe/London')::date) STORED,
          tax_year            SMALLINT GENERATED ALWAYS AS (
            CASE
              WHEN EXTRACT(MONTH FROM (executed_at AT TIME ZONE 'Europe/London')) > 4
                OR (EXTRACT(MONTH FROM (executed_at AT TIME ZONE 'Europe/London')) = 4
                    AND EXTRACT(DAY FROM (executed_at AT TIME ZONE 'Europe/London')) >= 6)
              THEN EXTRACT(YEAR FROM (executed_at AT TIME ZONE 'Europe/London'))::SMALLINT
              ELSE (EXTRACT(YEAR FROM (executed_at AT TIME ZONE 'Europe/London')) - 1)::SMALLINT
            END
          ) STORED,
          executed_at         TIMESTAMPTZ    NOT NULL,
          bot_id              UUID REFERENCES bots(id) ON DELETE SET NULL,
          transfer_group_id   UUID,
          notes               TEXT,
          CONSTRAINT chk_short_flags CHECK (NOT (is_short_open AND is_short_close)),
          CONSTRAINT chk_fill_leg    UNIQUE (fill_id, event_type, leg_index)
        )
    """))
    op.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS tax_events_external_idx
          ON tax_events (account_id, external_event_id)
          WHERE external_event_id IS NOT NULL
    """))
    op.execute(text("""
        CREATE INDEX IF NOT EXISTS tax_events_pool_active_idx
          ON tax_events (account_id, cgt_class_key, uk_trade_date)
          WHERE cgt_track = 'pool'
    """))
    op.execute(text("""
        CREATE INDEX IF NOT EXISTS tax_events_account_instrument_idx
          ON tax_events (account_id, instrument_id, executed_at)
    """))

    # ── 7. s104_pool ──────────────────────────────────────────────────────
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS s104_pool (
          account_id        UUID REFERENCES broker_accounts(id) NOT NULL,
          instrument_id     BIGINT REFERENCES instruments(id) NOT NULL,
          qty               NUMERIC(28,12) NOT NULL DEFAULT 0 CHECK (qty >= 0),
          total_cost_gbp    NUMERIC(28,12) NOT NULL DEFAULT 0,
          pool_avg_cost_gbp NUMERIC(28,12) GENERATED ALWAYS AS (
            CASE WHEN qty = 0 THEN 0 ELSE total_cost_gbp / qty END
          ) STORED,
          last_updated_at   TIMESTAMPTZ    NOT NULL,
          PRIMARY KEY (account_id, instrument_id)
        )
    """))

    # ── 8. short_obligations ──────────────────────────────────────────────
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS short_obligations (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          account_id          UUID REFERENCES broker_accounts(id) NOT NULL,
          instrument_id       BIGINT REFERENCES instruments(id) NOT NULL,
          open_tax_event_id   UUID REFERENCES tax_events(id) NOT NULL,
          close_tax_event_id  UUID REFERENCES tax_events(id),
          open_qty            NUMERIC(28,12) NOT NULL,
          open_proceeds_gbp   NUMERIC(28,12) NOT NULL,
          close_qty           NUMERIC(28,12),
          close_cost_gbp      NUMERIC(28,12),
          gain_gbp            NUMERIC(28,12),
          status              VARCHAR(8)     NOT NULL CHECK (status IN ('open','closed')),
          opened_at           TIMESTAMPTZ    NOT NULL,
          closed_at           TIMESTAMPTZ
        )
    """))

    # ── 9. derivative_positions ───────────────────────────────────────────
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS derivative_positions (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          account_id          UUID REFERENCES broker_accounts(id) NOT NULL,
          instrument_id       BIGINT REFERENCES instruments(id) NOT NULL,
          open_tax_event_id   UUID REFERENCES tax_events(id) NOT NULL,
          close_tax_event_id  UUID REFERENCES tax_events(id),
          side                VARCHAR(8)     NOT NULL CHECK (side IN ('long','short')),
          qty                 NUMERIC(28,12) NOT NULL,
          total_proceeds_gbp  NUMERIC(28,12) NOT NULL DEFAULT 0,
          total_cost_gbp      NUMERIC(28,12) NOT NULL DEFAULT 0,
          gain_gbp            NUMERIC(28,12),
          status              VARCHAR(8)     NOT NULL CHECK (status IN ('open','closed')),
          opened_at           TIMESTAMPTZ    NOT NULL,
          closed_at           TIMESTAMPTZ
        )
    """))

    # ── 10. s104_pool_events ──────────────────────────────────────────────
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS s104_pool_events (
          id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          account_id       UUID REFERENCES broker_accounts(id) NOT NULL,
          instrument_id    BIGINT REFERENCES instruments(id) NOT NULL,
          tax_event_id     UUID REFERENCES tax_events(id),
          event_type       VARCHAR(32) NOT NULL,
          match_type       VARCHAR(16),
          qty_delta        NUMERIC(28,12) NOT NULL,
          cost_delta_gbp   NUMERIC(28,12) NOT NULL,
          pool_qty_after   NUMERIC(28,12) NOT NULL,
          pool_cost_after  NUMERIC(28,12) NOT NULL,
          matched_event_id UUID REFERENCES tax_events(id),
          gain_gbp         NUMERIC(28,12),
          executed_at      TIMESTAMPTZ NOT NULL
        )
    """))
    op.execute(text("""
        CREATE INDEX IF NOT EXISTS s104_pool_events_account_instrument_idx
          ON s104_pool_events (account_id, instrument_id, executed_at)
    """))

    # ── 11. cgt_disposals ────────────────────────────────────────────────
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS cgt_disposals (
          id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          account_id            UUID REFERENCES broker_accounts(id) NOT NULL,
          instrument_id         BIGINT REFERENCES instruments(id) NOT NULL,
          disposal_tax_event_id UUID REFERENCES tax_events(id) NOT NULL,
          match_seq             SMALLINT       NOT NULL,
          cgt_track             VARCHAR(12)    NOT NULL CHECK (cgt_track IN ('pool','derivative')),
          tax_year              SMALLINT       NOT NULL,
          disposal_date         DATE           NOT NULL,
          proceeds_gbp          NUMERIC(28,12) NOT NULL,
          allowable_cost_gbp    NUMERIC(28,12) NOT NULL,
          gain_gbp              NUMERIC(28,12) NOT NULL,
          match_type            VARCHAR(16)    NOT NULL,
          pool_event_id         UUID REFERENCES s104_pool_events(id),
          short_obligation_id   UUID REFERENCES short_obligations(id),
          derivative_id         UUID REFERENCES derivative_positions(id),
          UNIQUE (disposal_tax_event_id, match_seq)
        )
    """))
    op.execute(text("""
        CREATE INDEX IF NOT EXISTS cgt_disposals_account_year_idx
          ON cgt_disposals (account_id, tax_year)
    """))

    # ── 12. income_events ────────────────────────────────────────────────
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS income_events (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          account_id          UUID REFERENCES broker_accounts(id) NOT NULL,
          instrument_id       BIGINT REFERENCES instruments(id),
          broker_statement_id UUID REFERENCES broker_statements(id),
          external_event_id   VARCHAR(128),
          event_type          VARCHAR(32)    NOT NULL,
          income_subtype      VARCHAR(16)    NOT NULL,
          gross_gbp           NUMERIC(28,12) NOT NULL,
          withholding_tax_gbp NUMERIC(28,12) NOT NULL DEFAULT 0,
          net_gbp             NUMERIC(28,12) NOT NULL,
          fx_rate             NUMERIC(20,8)  NOT NULL,
          fx_source           VARCHAR(32)    NOT NULL,
          original_currency   VARCHAR(8)     NOT NULL,
          tax_year            SMALLINT       NOT NULL,
          ex_date             DATE,
          pay_date            DATE           NOT NULL,
          tax_event_id        UUID REFERENCES tax_events(id),
          notes               TEXT,
          CONSTRAINT chk_scrip_fx CHECK (
            event_type NOT IN ('dividend_scrip','dividend_drip') OR tax_event_id IS NOT NULL
          )
        )
    """))
    op.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS income_events_external_idx
          ON income_events (account_id, external_event_id)
          WHERE external_event_id IS NOT NULL
    """))
    op.execute(text("""
        CREATE INDEX IF NOT EXISTS income_events_account_year_idx
          ON income_events (account_id, tax_year)
    """))


def downgrade() -> None:
    tables = [
        "income_events", "cgt_disposals", "s104_pool_events",
        "derivative_positions", "short_obligations", "s104_pool",
        "tax_events", "cgt_loss_carry_forward", "cgt_class_links",
        "broker_statements", "hmrc_fx_rates",
    ]
    for t in tables:
        op.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
    op.execute(text("""
        ALTER TABLE fills
          DROP COLUMN IF EXISTS instrument_id,
          DROP COLUMN IF EXISTS side,
          DROP COLUMN IF EXISTS bot_id
    """))
