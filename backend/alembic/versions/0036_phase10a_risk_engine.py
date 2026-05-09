"""phase10a risk engine — risk_limits, account_kill_switches, risk_decisions

Revision ID: 0036_phase10a_risk_engine
Revises: 0035_phase9_5_nlv_at_index
Create Date: 2026-05-09

Spec: docs/superpowers/specs/2026-05-08-phase10a-risk-engine-design.md §3
"""

from __future__ import annotations

from alembic import op

revision = "0036_phase10a_risk_engine"
down_revision = "0035_phase9_5_nlv_at_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── ENUMs ───────────────────────────────────────────────────────────────
    op.execute("CREATE TYPE risk_scope_type AS ENUM ('global', 'broker', 'account')")
    op.execute(
        "CREATE TYPE risk_limit_kind AS ENUM ("
        "'max_daily_loss_currency_base', "
        "'max_position_concentration_pct', "
        "'pdt_warn_remaining', "
        "'min_buying_power_buffer_pct')"
    )
    op.execute("CREATE TYPE risk_verdict AS ENUM ('allow', 'warn', 'block')")

    # ── risk_limits ─────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE risk_limits (
          id            BIGSERIAL PRIMARY KEY,
          scope_type    risk_scope_type NOT NULL,
          scope_id      TEXT,
          limit_kind    risk_limit_kind NOT NULL,
          limit_value   NUMERIC(20, 8) NOT NULL,
          warn_at_pct   NUMERIC(5, 2),
          is_active     BOOLEAN NOT NULL DEFAULT TRUE,
          notes         TEXT NOT NULL DEFAULT '',
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by    TEXT NOT NULL,
          CHECK ( (scope_type = 'global') = (scope_id IS NULL) ),
          CHECK ( warn_at_pct IS NULL OR (warn_at_pct >= 0 AND warn_at_pct <= 100) ),
          CHECK ( length(notes) <= 1000 )
        )
        """
    )
    # [C1] Two partial unique indexes — Postgres treats NULLs as distinct in
    # plain UNIQUE, which would let two `(global, NULL, max_daily_loss)` rows
    # coexist and make _resolve_limit non-deterministic.
    op.execute(
        "CREATE UNIQUE INDEX uq_risk_limits_global_kind ON risk_limits (limit_kind) "
        "WHERE scope_type = 'global' AND scope_id IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_risk_limits_scoped ON risk_limits "
        "(scope_type, scope_id, limit_kind) WHERE scope_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX idx_risk_limits_lookup ON risk_limits "
        "(scope_type, scope_id, limit_kind) WHERE is_active"
    )

    # ── risk_limits_history + UPDATE trigger [M3] ───────────────────────────
    op.execute(
        """
        CREATE TABLE risk_limits_history (
          history_id    BIGSERIAL PRIMARY KEY,
          limit_id      BIGINT NOT NULL,
          scope_type    risk_scope_type NOT NULL,
          scope_id      TEXT,
          limit_kind    risk_limit_kind NOT NULL,
          limit_value   NUMERIC(20, 8) NOT NULL,
          warn_at_pct   NUMERIC(5, 2),
          is_active     BOOLEAN NOT NULL,
          notes         TEXT NOT NULL,
          changed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          changed_by    TEXT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_risk_limits_history() RETURNS TRIGGER AS $$
        BEGIN
          INSERT INTO risk_limits_history
            (limit_id, scope_type, scope_id, limit_kind, limit_value, warn_at_pct,
             is_active, notes, changed_at, changed_by)
          VALUES
            (OLD.id, OLD.scope_type, OLD.scope_id, OLD.limit_kind, OLD.limit_value,
             OLD.warn_at_pct, OLD.is_active, OLD.notes, now(), NEW.updated_by);
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_risk_limits_history
          BEFORE UPDATE ON risk_limits
          FOR EACH ROW WHEN (OLD.* IS DISTINCT FROM NEW.*)
          EXECUTE FUNCTION fn_risk_limits_history()
        """
    )

    # ── account_kill_switches + history + symmetric trigger [M3] ────────────
    op.execute(
        """
        CREATE TABLE account_kill_switches (
          account_id    UUID PRIMARY KEY REFERENCES broker_accounts(id) ON DELETE CASCADE,
          is_enabled    BOOLEAN NOT NULL DEFAULT FALSE,
          reason        TEXT NOT NULL DEFAULT '',
          enabled_at    TIMESTAMPTZ,
          enabled_by    TEXT,
          updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK ( length(reason) <= 1000 ),
          CHECK ( (is_enabled IS FALSE) OR (enabled_at IS NOT NULL AND enabled_by IS NOT NULL) )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE account_kill_switches_history (
          history_id    BIGSERIAL PRIMARY KEY,
          account_id    UUID NOT NULL,
          is_enabled    BOOLEAN NOT NULL,
          reason        TEXT NOT NULL,
          changed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          changed_by    TEXT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_account_kill_switches_history() RETURNS TRIGGER AS $$
        BEGIN
          INSERT INTO account_kill_switches_history
            (account_id, is_enabled, reason, changed_at, changed_by)
          VALUES
            (OLD.account_id, OLD.is_enabled, OLD.reason, now(),
             COALESCE(NEW.enabled_by, OLD.enabled_by, 'system'));
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_account_kill_switches_history
          BEFORE UPDATE ON account_kill_switches
          FOR EACH ROW WHEN (OLD.* IS DISTINCT FROM NEW.*)
          EXECUTE FUNCTION fn_account_kill_switches_history()
        """
    )

    # ── risk_decisions + minimal pg_notify trigger [M4] ─────────────────────
    op.execute(
        """
        CREATE TABLE risk_decisions (
          id              BIGSERIAL PRIMARY KEY,
          account_id      UUID NOT NULL REFERENCES broker_accounts(id),
          instrument_id   BIGINT REFERENCES instruments(id),
          side            TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
          qty             NUMERIC(20, 8) NOT NULL,
          price           NUMERIC(20, 8),
          order_type      TEXT NOT NULL,
          time_in_force   TEXT NOT NULL,
          verdict         risk_verdict NOT NULL,
          blockers        JSONB NOT NULL DEFAULT '[]'::jsonb,
          warnings        JSONB NOT NULL DEFAULT '[]'::jsonb,
          evaluated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          latency_ms      INT NOT NULL CHECK (latency_ms >= 0),
          attempt_kind    TEXT NOT NULL CHECK (attempt_kind IN ('place_order', 'modify_order')),
          request_id      TEXT NOT NULL,
          order_id        UUID REFERENCES orders(id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_risk_decisions_account_time ON risk_decisions "
        "(account_id, evaluated_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_risk_decisions_blocked ON risk_decisions "
        "(evaluated_at DESC) WHERE verdict = 'block'"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_risk_decisions_notify() RETURNS TRIGGER AS $$
        BEGIN
          IF NEW.verdict = 'block' THEN
            PERFORM pg_notify('risk_decision', json_build_object(
              'id', NEW.id, 'verdict', NEW.verdict, 'account_id', NEW.account_id::text
            )::text);
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_risk_decisions_notify
          AFTER INSERT ON risk_decisions
          FOR EACH ROW EXECUTE FUNCTION fn_risk_decisions_notify()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_risk_decisions_notify ON risk_decisions")
    op.execute("DROP FUNCTION IF EXISTS fn_risk_decisions_notify()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_account_kill_switches_history "
        "ON account_kill_switches"
    )
    op.execute("DROP FUNCTION IF EXISTS fn_account_kill_switches_history()")
    op.execute("DROP TRIGGER IF EXISTS trg_risk_limits_history ON risk_limits")
    op.execute("DROP FUNCTION IF EXISTS fn_risk_limits_history()")
    op.execute("DROP TABLE IF EXISTS risk_decisions")
    op.execute("DROP TABLE IF EXISTS account_kill_switches_history")
    op.execute("DROP TABLE IF EXISTS account_kill_switches")
    op.execute("DROP TABLE IF EXISTS risk_limits_history")
    op.execute("DROP TABLE IF EXISTS risk_limits")
    op.execute("DROP TYPE IF EXISTS risk_verdict")
    op.execute("DROP TYPE IF EXISTS risk_limit_kind")
    op.execute("DROP TYPE IF EXISTS risk_scope_type")
