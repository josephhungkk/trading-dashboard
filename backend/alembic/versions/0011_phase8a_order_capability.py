"""Phase 8a - capability matrix tables + cross-product seed.

Revision ID: 0011_phase8a_order_capability
Revises: 0010
Create Date: 2026-05-06
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0011_phase8a_order_capability"
down_revision = "0010"
branch_labels = None
depends_on = None


ORDER_TYPES = [
    ("MARKET",      "Market",              "Buy or sell at next available price.",                    10),
    ("LIMIT",       "Limit",               "Trade only at limit price or better.",                    20),
    ("STOP",        "Stop",                "Triggers a market order when stop price is reached.",     30),
    ("STOP_LIMIT",  "Stop-Limit",          "Triggers a limit order when stop price is reached.",      40),
    ("TRAIL",       "Trailing Stop",       "Stop following the market by a fixed amount or percent.", 50),
    ("TRAIL_LIMIT", "Trailing Stop-Limit", "Trailing stop that triggers a limit order.",              60),
    ("MOC",         "Market on Close",     "Market order executed at the closing auction.",           70),
    ("MOO",         "Market on Open",      "Market order executed at the opening auction.",           80),
    ("LOC",         "Limit on Close",      "Limit order executed at the closing auction.",            90),
    ("LOO",         "Limit on Open",       "Limit order executed at the opening auction.",           100),
]

TIME_IN_FORCE = [
    ("DAY", "Day",                 "Order expires at end of trading day.",          False, 10),
    ("GTC", "Good Til Cancelled",  "Order remains open until filled or cancelled.", False, 20),
    ("IOC", "Immediate or Cancel", "Fill any portion immediately, cancel the rest.",False, 30),
    ("FOK", "Fill or Kill",        "Fill the entire order immediately or cancel.",  False, 40),
    ("GTD", "Good Til Date",       "Order remains open until specified date.",      True,  50),
]

# (broker_id, supported_types, supported_tifs, default_unsupported_notes)
BROKER_INITIAL_SUPPORT = [
    ("ibkr",   {"MARKET", "LIMIT", "STOP", "STOP_LIMIT"}, {"DAY", "GTC", "IOC", "FOK"}, "Coming in 8b"),
    ("futu",   {"MARKET", "LIMIT"},                       {"DAY", "GTC"},               "Coming in 8b"),
    ("schwab", set(),                                     set(),                        "Enabled by 0011a after C0 gate"),
    ("alpaca", set(),                                     set(),                        "Trade execution lands in Phase 8c"),
]

_VALID_BROKERS = "('ibkr', 'futu', 'schwab', 'alpaca')"


def upgrade() -> None:
    op.create_table(
        "order_types",
        sa.Column("code",        sa.String(32), primary_key=True),
        sa.Column("label",       sa.String(64), nullable=False),
        sa.Column("description", sa.Text(),     nullable=False, server_default=""),
        sa.Column("sort_order",  sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("created_at",  sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "time_in_force",
        sa.Column("code",            sa.String(16), primary_key=True),
        sa.Column("label",           sa.String(64), nullable=False),
        sa.Column("description",     sa.Text(),     nullable=False, server_default=""),
        sa.Column("requires_expiry", sa.Boolean(),  nullable=False, server_default=sa.false()),
        sa.Column("sort_order",      sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("created_at",      sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "broker_order_capability",
        # No FK to a brokers table (schema uses broker_id_enum on broker_accounts, no standalone brokers table).
        # Enforce allowed values via CHECK constraint instead.
        sa.Column("broker_id",     sa.String(32), nullable=False),
        sa.Column("order_type",    sa.String(32), sa.ForeignKey("order_types.code", ondelete="RESTRICT"), nullable=False),
        sa.Column("time_in_force", sa.String(16), sa.ForeignKey("time_in_force.code", ondelete="RESTRICT"), nullable=False),
        sa.Column("is_supported",  sa.Boolean(),  nullable=False, server_default=sa.false()),
        sa.Column("notes",         sa.Text(),     nullable=False, server_default=""),
        sa.Column("updated_at",    sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("broker_id", "order_type", "time_in_force"),
        sa.CheckConstraint(
            f"broker_id IN {_VALID_BROKERS}",
            name="broker_order_capability_broker_id_valid",
        ),
        sa.CheckConstraint(
            r"notes ~ '^[\x20-\x7E]*$' AND length(notes) <= 256",
            name="broker_order_capability_notes_printable_ascii",
        ),
    )

    op.create_index(
        "ix_broker_order_capability_supported",
        "broker_order_capability", ["broker_id"],
        postgresql_where=sa.text("is_supported = TRUE"),
    )

    op.bulk_insert(
        sa.table("order_types",
                 sa.column("code", sa.String), sa.column("label", sa.String),
                 sa.column("description", sa.Text), sa.column("sort_order", sa.SmallInteger)),
        [{"code": c, "label": l, "description": d, "sort_order": s} for (c, l, d, s) in ORDER_TYPES],
    )
    op.bulk_insert(
        sa.table("time_in_force",
                 sa.column("code", sa.String), sa.column("label", sa.String),
                 sa.column("description", sa.Text), sa.column("requires_expiry", sa.Boolean),
                 sa.column("sort_order", sa.SmallInteger)),
        [{"code": c, "label": l, "description": d, "requires_expiry": r, "sort_order": s}
         for (c, l, d, r, s) in TIME_IN_FORCE],
    )

    rows = []
    type_codes = [t[0] for t in ORDER_TYPES]
    tif_codes = [t[0] for t in TIME_IN_FORCE]
    for (broker_id, supported_types, supported_tifs, default_notes) in BROKER_INITIAL_SUPPORT:
        for ot in type_codes:
            for tif in tif_codes:
                supported = (ot in supported_types) and (tif in supported_tifs)
                rows.append({
                    "broker_id": broker_id, "order_type": ot, "time_in_force": tif,
                    "is_supported": supported,
                    "notes": "" if supported else default_notes,
                })
    op.bulk_insert(
        sa.table("broker_order_capability",
                 sa.column("broker_id", sa.String), sa.column("order_type", sa.String),
                 sa.column("time_in_force", sa.String), sa.column("is_supported", sa.Boolean),
                 sa.column("notes", sa.Text)),
        rows,
    )


def downgrade() -> None:
    op.drop_index("ix_broker_order_capability_supported", table_name="broker_order_capability")
    op.drop_table("broker_order_capability")
    op.drop_table("time_in_force")
    op.drop_table("order_types")
