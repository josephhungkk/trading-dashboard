"""Phase 8b T-0.4 -- broker_features table + initial seed (spec sec 7 MED-2)."""

from __future__ import annotations

from alembic import op

revision = "0012_broker_features"
down_revision = "0011a_phase8a_schwab_flip"
branch_labels = None
depends_on = None


BROKER_FEATURE_ROWS = [
    ("ibkr", "modify", True, None, ""),
    ("futu", "modify", False, None, "Phase 6 deferred -- empirical pending"),
    ("schwab", "modify", True, None, ""),
    ("ibkr", "bracket", True, None, ""),
    ("futu", "bracket", False, None, "Phase 6 deferred -- empirical pending"),
    ("schwab", "bracket", False, None, "Phase 8b -- pending implementation"),
    ("ibkr", "oco", False, None, "Phase 8b"),
    ("futu", "oco", False, None, "Phase 8b"),
    ("schwab", "oco", False, None, "Phase 8b"),
    ("ibkr", "gtd_max_days", True, 90, "TWS API limit"),
    ("futu", "gtd_max_days", True, 30, "Futu HK trading-day cap"),
    ("schwab", "gtd_max_days", True, 60, "retail account limit per Schwab docs"),
    (
        "nyse",
        "session_cutoff_minutes",
        True,
        10,
        "MOC cutoff: 15:50 ET = 10 min before 16:00 close",
    ),
    ("hkex", "session_cutoff_minutes", True, 0, "no MOC support -- Phase 8b out of scope"),
]


def _sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    if value is True:
        return "TRUE"
    if value is False:
        return "FALSE"
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


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

    values_sql = ",\n".join(
        f"        ({', '.join(_sql_literal(value) for value in row)})"
        for row in BROKER_FEATURE_ROWS
    )
    op.execute(
        f"""
        INSERT INTO broker_features
            (broker_id, feature, is_supported, int_value, notes)
        VALUES
{values_sql}
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS broker_features")
