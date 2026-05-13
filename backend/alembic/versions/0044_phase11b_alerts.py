"""Phase 11b chunk A: alerts + alert_fires hypertable + alert_fire_context + bars_1m NOTIFY trigger.

Capability registry uses app_config (no separate table — HIGH-7 single-source).

Revision ID: 0044_phase11b_alerts
Down Revision: 0043_phase11a_risk_attempt_kind_preview
Create Date: 2026-05-13
"""

from __future__ import annotations

from alembic import op

revision = "0044_phase11b_alerts"
down_revision = "0043_phase11a_risk_attempt_kind_preview"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE alerts (
          id              BIGSERIAL PRIMARY KEY,
          jwt_subject     TEXT NOT NULL,
          user_label      TEXT NOT NULL,
          original_nl     TEXT NOT NULL,
          predicate_json  JSONB NOT NULL,
          requires_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
          parse_status    TEXT NOT NULL
            CHECK (parse_status IN ('ok','uncertain','manual','failed')),
          parse_metadata  JSONB,
          delivery_channels JSONB NOT NULL DEFAULT '["in_app"]'::jsonb,
          tick_subscribed BOOLEAN NOT NULL DEFAULT FALSE,
          status          TEXT NOT NULL
            CHECK (status IN ('pending','active','dormant','disabled','deleted')),
          dormancy_reason TEXT,
          consecutive_eval_errors INT NOT NULL DEFAULT 0,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          confirmed_at    TIMESTAMPTZ,
          deleted_at      TIMESTAMPTZ
        );
        """
    )
    op.execute(
        """
        CREATE INDEX idx_alerts_active_by_subject ON alerts (jwt_subject)
          WHERE status = 'active';
        """
    )
    op.execute(
        """
        CREATE INDEX idx_alerts_status ON alerts (status);
        """
    )
    op.execute(
        """
        CREATE INDEX idx_alerts_predicate_gin ON alerts
          USING GIN (predicate_json jsonb_path_ops)
          WHERE status IN ('active', 'dormant');
        """
    )
    op.execute(
        """
        CREATE INDEX idx_alerts_requires_capabilities_gin ON alerts
          USING GIN (requires_capabilities)
          WHERE status IN ('active', 'dormant');
        """
    )
    op.execute(
        """
        CREATE TABLE alert_fires (
          id            BIGSERIAL,
          alert_id      BIGINT NOT NULL,
          jwt_subject   TEXT NOT NULL,
          fired_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          verdict       TEXT NOT NULL,
          fire_context_id BIGINT,
          delivery_outcomes JSONB NOT NULL DEFAULT '{}'::jsonb,
          PRIMARY KEY (id, fired_at)
        );
        """
    )
    op.execute(
        """
        SELECT create_hypertable('alert_fires', 'fired_at',
                                 chunk_time_interval => INTERVAL '7 days');
        """
    )
    op.execute(
        """
        ALTER TABLE alert_fires SET (
          timescaledb.compress,
          timescaledb.compress_orderby = 'fired_at DESC'
        );
        """
    )
    op.execute(
        """
        SELECT add_compression_policy('alert_fires', INTERVAL '90 days');
        """
    )
    op.execute(
        """
        SELECT add_retention_policy('alert_fires', INTERVAL '1 year');
        """
    )
    op.execute(
        """
        CREATE INDEX idx_alert_fires_subject_fired
          ON alert_fires (jwt_subject, fired_at DESC);
        """
    )
    op.execute(
        """
        CREATE TABLE alert_fire_context (
          id              BIGSERIAL PRIMARY KEY,
          alert_id        BIGINT NOT NULL,
          fired_at        TIMESTAMPTZ NOT NULL,
          evaluated_values JSONB NOT NULL,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        """
        CREATE INDEX idx_alert_fire_context_alert
          ON alert_fire_context (alert_id, fired_at DESC);
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION notify_bars_1m_insert()
        RETURNS TRIGGER AS $$
        BEGIN
          PERFORM pg_notify(
            'bars_1m_insert',
            json_build_object(
              'inst_id', NEW.instrument_id,
              'ts', extract(epoch from NEW.bucket_start)
            )::text
          );
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_bars_1m_notify AFTER INSERT ON bars_1m
          FOR EACH ROW EXECUTE FUNCTION notify_bars_1m_insert();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_bars_1m_notify ON bars_1m;")
    op.execute("DROP FUNCTION IF EXISTS notify_bars_1m_insert;")
    op.execute("DROP TABLE IF EXISTS alert_fire_context;")
    op.execute("DROP TABLE IF EXISTS alert_fires CASCADE;")
    op.execute("DROP TABLE IF EXISTS alerts CASCADE;")
