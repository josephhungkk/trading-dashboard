"""Phase 8b T-0.4 -- broker_features table + initial seed (spec sec 7 MED-2)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_broker_features"
down_revision = "0011a_phase8a_schwab_flip"
branch_labels = None
depends_on = None


BROKER_FEATURE_ROWS = [
    {"b": "ibkr", "f": "modify", "s": True, "i": None, "n": ""},
    {"b": "futu", "f": "modify", "s": False, "i": None, "n": "Phase 6 deferred -- empirical pending"},
    {"b": "schwab", "f": "modify", "s": True, "i": None, "n": ""},
    {"b": "ibkr", "f": "bracket", "s": True, "i": None, "n": ""},
    {"b": "futu", "f": "bracket", "s": False, "i": None, "n": "Phase 6 deferred -- empirical pending"},
    {"b": "schwab", "f": "bracket", "s": False, "i": None, "n": "Phase 8b -- pending implementation"},
    {"b": "ibkr", "f": "oco", "s": False, "i": None, "n": "Phase 8b"},
    {"b": "futu", "f": "oco", "s": False, "i": None, "n": "Phase 8b"},
    {"b": "schwab", "f": "oco", "s": False, "i": None, "n": "Phase 8b"},
    {"b": "ibkr", "f": "gtd_max_days", "s": True, "i": 90, "n": "TWS API limit"},
    {"b": "futu", "f": "gtd_max_days", "s": True, "i": 30, "n": "Futu HK trading-day cap"},
    {"b": "schwab", "f": "gtd_max_days", "s": True, "i": 60, "n": "retail account limit per Schwab docs"},
    {
        "b": "nyse",
        "f": "session_cutoff_minutes",
        "s": True,
        "i": 10,
        "n": "MOC cutoff: 15:50 ET = 10 min before 16:00 close",
    },
    {
        "b": "hkex",
        "f": "session_cutoff_minutes",
        "s": True,
        "i": 0,
        "n": "no MOC support -- Phase 8b out of scope",
    },
]


def upgrade() -> None:
    op.execute(
        r"""
        CREATE TABLE broker_features (
          broker_id        VARCHAR NOT NULL CHECK (
            broker_id IN ('ibkr','futu','schwab','alpaca','nyse','hkex')
          ),
          feature          VARCHAR NOT NULL CHECK (
            feature IN ('modify','bracket','oco','gtd_max_days','session_cutoff_minutes')
          ),
          is_supported     BOOLEAN NOT NULL DEFAULT FALSE,
          int_value        INTEGER,
          notes            VARCHAR(256) NOT NULL DEFAULT '' CHECK (notes ~ '^[\x20-\x7E]*$'),
          updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          PRIMARY KEY (broker_id, feature)
        )
        """
    )

    bind = op.get_bind()
    for row in BROKER_FEATURE_ROWS:
        bind.execute(
            sa.text(
                """
                INSERT INTO broker_features
                    (broker_id, feature, is_supported, int_value, notes, updated_at)
                VALUES (:b, :f, :s, :i, :n, NOW())
                ON CONFLICT (broker_id, feature) DO NOTHING
                """
            ),
            row,
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS broker_features")
