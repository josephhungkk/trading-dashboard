"""add filings and filing_feed_cursors

Revision ID: 0059
Revises: 0058a
Create Date: 2026-05-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0059"
down_revision = "0058a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "filings",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "instrument_id",
            sa.BigInteger(),
            sa.ForeignKey("instruments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("canonical_id", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("form_type", sa.Text(), nullable=False),
        sa.Column("filing_date", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("period_of_report", sa.Date(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False, unique=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("llm_summary", sa.Text(), nullable=True),
        sa.Column("llm_summary_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("captured_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("source IN ('sec_edgar', 'hkex_rns')", name="filings_source_check"),
        sa.CheckConstraint(
            "instrument_id IS NOT NULL OR canonical_id IS NOT NULL",
            name="filings_instrument_or_canonical_check",
        ),
    )
    op.create_index("ix_filings_canonical_id", "filings", ["canonical_id"])
    op.create_index("ix_filings_instrument_id", "filings", ["instrument_id"])
    op.create_index("ix_filings_filing_date", "filings", ["filing_date"])

    op.create_table(
        "filing_feed_cursors",
        sa.Column("source", sa.Text(), primary_key=True),
        sa.Column("last_cursor", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "source IN ('sec_edgar', 'hkex_rns')",
            name="filing_feed_cursors_source_check",
        ),
    )


def downgrade() -> None:
    op.drop_table("filing_feed_cursors")
    op.drop_index("ix_filings_filing_date")
    op.drop_index("ix_filings_instrument_id")
    op.drop_index("ix_filings_canonical_id")
    op.drop_table("filings")
