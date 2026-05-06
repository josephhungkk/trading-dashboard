# Phase 8a — Capability Foundation + Schwab Trade Write-Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the DB-driven order-type/TIF capability matrix (Roadmap pillar #3) AND flip Schwab sidecar's six write/stream RPCs from `UNIMPLEMENTED` to live, so Schwab single-leg place/cancel/modify works end-to-end through existing Phase 5b/5c plumbing.

**Architecture:** Two workstreams. (1) Backend-only capability foundation: `order_types` / `time_in_force` / `broker_order_capability` tables (Alembic 0011, 200 rows seeded), `OrderCapabilityService` (60s LRU + Redis-pubsub bust), `GET /api/brokers/{id}/capabilities`, capability-gate inserted between maintenance check and broker dispatch in `OrderService`. (2) Schwab sidecar: 6 RPCs implemented; per-`(gateway_label, account_id)` adaptive order-event poller (2s active / 30s idle) with Redis-backed state cache (CRIT-2); SIM-mode echo (5b.1 lesson); modify-chain via existing `parent_order_id` self-FK (HIGH-3); account-hash rotation handling (HIGH-6).

**Tech Stack:** Python 3.14, schwabdev (isolated to `client.py` per Phase 7a M3), grpcio, FastAPI, SQLAlchemy 2.0 async, Alembic, structlog, pytest-asyncio, freezegun, Pydantic v2, Redis 7, Docker. Frontend: TypeScript 6 strict, React 19, TanStack Query, Vitest 4, Storybook 10.

**Spec:** [`docs/superpowers/specs/2026-05-05-phase8a-capability-foundation-schwab-trade-design.md`](../specs/2026-05-05-phase8a-capability-foundation-schwab-trade-design.md) (architect-review applied at commit `1c000d0`; 3 CRIT + 6 HIGH + 8 MED inline).

**Codex defaults reference:** `/home/joseph/.claude/projects/-home-joseph-dashboard/memory/codex_defaults.md` — every Codex dispatch must inline the relevant pattern verbatim. Patterns A-G covered: A (`except (...)` parens + tuple-catch), B (cancel+gather supervisor), C (per-callback isolation), D (bounded queues + caps), E (lazy-singleton init/cleanup), F (spec metric labels verbatim), G (XFF trust).

**Hard rollout gate:** C0 empirical script (`scripts/empirical/schwab_place_cancel_paper.py`) MUST PASS before frontend work (Chunk F) or capability-flip migration (Task A5) begins. See Task E3.

---

## Chunk A — Proto + Alembic 0011 + ORM models

**Codex patterns most likely to bite:** A (parens around tuple-catch), F (verbatim labels). Migration code path uses `op.execute()` + `op.bulk_insert()`; SQLAlchemy seed via raw text() to avoid metadata circularity.

### Task A1: Bump proto OrderType + TimeInForce enums

**Files:**
- Modify: `proto/broker/v1/broker.proto` (existing OrderType + TimeInForce enums)
- Modify: `backend/app/brokers/_generated/...` (regenerated)
- Modify: `sidecar_schwab/_generated/...` (regenerated)

- [ ] **Step 1: Pre-flight grep**

```bash
grep -n "enum OrderType\|enum TimeInForce" proto/broker/v1/broker.proto
grep -rn "ORDER_TYPE_TRAIL\|TIF_GTD" backend/ sidecar_schwab/ sidecar_ibkr/ sidecar_futu/ 2>/dev/null
```

Expected: enums currently have only MARKET/LIMIT/STOP/STOP_LIMIT and DAY/GTC/IOC/FOK; no TRAIL/GTD references.

- [ ] **Step 2: Edit proto enum entries**

In `proto/broker/v1/broker.proto`, extend the OrderType enum (preserving existing tag numbers; new entries use next free tags):

```proto
enum OrderType {
  ORDER_TYPE_UNSPECIFIED = 0;
  ORDER_TYPE_MARKET = 1;
  ORDER_TYPE_LIMIT = 2;
  ORDER_TYPE_STOP = 3;
  ORDER_TYPE_STOP_LIMIT = 4;
  ORDER_TYPE_TRAIL = 5;
  ORDER_TYPE_TRAIL_LIMIT = 6;
  ORDER_TYPE_MOC = 7;
  ORDER_TYPE_MOO = 8;
  ORDER_TYPE_LOC = 9;
  ORDER_TYPE_LOO = 10;
}

enum TimeInForce {
  TIF_UNSPECIFIED = 0;
  TIF_DAY = 1;
  TIF_GTC = 2;
  TIF_IOC = 3;
  TIF_FOK = 4;
  TIF_GTD = 5;
}
```

- [ ] **Step 3: Regenerate stubs**

```bash
cd /home/joseph/dashboard
buf generate
```

Expected: regenerates `backend/app/brokers/_generated/broker/v1/broker_pb2.py(i)` and the same under each sidecar. No errors.

- [ ] **Step 4: Verify regenerated files contain new enum values**

```bash
grep -c "ORDER_TYPE_TRAIL_LIMIT\|TIF_GTD" backend/app/brokers/_generated/broker/v1/broker_pb2.pyi
```

Expected: both grep hits return ≥1.

- [ ] **Step 5: Commit**

```bash
git add proto/broker/v1/broker.proto backend/app/brokers/_generated/ sidecar_schwab/_generated/ sidecar_ibkr/_generated/ sidecar_futu/_generated/ sidecar_alpaca/_generated/
git commit -m "feat(proto): extend OrderType + TimeInForce for Phase 8 universe"
```

---

### Task A2: Bump Python Literal types in `app/brokers/base.py`

**Files:**
- Modify: `backend/app/brokers/base.py:29-30`
- Test: `backend/tests/unit/test_capability_codes_match_proto.py` (new)

- [ ] **Step 1: Pre-flight read**

```bash
grep -n "^OrderType\|^TimeInForce" backend/app/brokers/base.py
```

Expected: lines 29-30 show current narrow Literal.

- [ ] **Step 2: Write failing test** — create `backend/tests/unit/test_capability_codes_match_proto.py`:

```python
"""Phase 8a invariant: DB order_types.code + time_in_force.code ⊆ proto enum."""
from __future__ import annotations
import typing as t

from app.brokers import base
from app.brokers._generated.broker.v1 import broker_pb2


def _proto_enum_values(enum_descriptor) -> set[str]:
    out: set[str] = set()
    for v in enum_descriptor.values:
        name = v.name
        if name.startswith("ORDER_TYPE_"):
            out.add(name[len("ORDER_TYPE_"):])
        elif name.startswith("TIF_"):
            out.add(name[len("TIF_"):])
    return out


def test_python_literal_order_type_subset_of_proto() -> None:
    proto_codes = _proto_enum_values(broker_pb2.OrderType.DESCRIPTOR)
    literal_codes = set(t.get_args(base.OrderType))
    missing = literal_codes - proto_codes
    assert not missing, f"Python Literal has codes not in proto: {missing}"


def test_python_literal_tif_subset_of_proto() -> None:
    proto_codes = _proto_enum_values(broker_pb2.TimeInForce.DESCRIPTOR)
    literal_codes = set(t.get_args(base.TimeInForce))
    missing = literal_codes - proto_codes
    assert not missing, f"Python Literal has codes not in proto: {missing}"


def test_proto_includes_phase8_universe() -> None:
    proto_order = _proto_enum_values(broker_pb2.OrderType.DESCRIPTOR)
    proto_tif = _proto_enum_values(broker_pb2.TimeInForce.DESCRIPTOR)
    expected_order = {"UNSPECIFIED", "MARKET", "LIMIT", "STOP", "STOP_LIMIT",
                      "TRAIL", "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO"}
    expected_tif = {"UNSPECIFIED", "DAY", "GTC", "IOC", "FOK", "GTD"}
    assert proto_order >= expected_order
    assert proto_tif >= expected_tif
```

- [ ] **Step 3: Run test**

```bash
cd backend && uv run pytest tests/unit/test_capability_codes_match_proto.py -v
```

Expected: all 3 PASS (proto already extended in A1; subset assertions hold one-directionally).

- [ ] **Step 4: Update Literals in `backend/app/brokers/base.py:29-30`**

```python
OrderType = t.Literal[
    "TYPE_UNSPECIFIED", "MARKET", "LIMIT", "STOP", "STOP_LIMIT",
    "TRAIL", "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
]
TimeInForce = t.Literal["TIF_UNSPECIFIED", "DAY", "GTC", "IOC", "FOK", "GTD"]
```

- [ ] **Step 5: Re-run test + mypy**

```bash
cd backend && uv run pytest tests/unit/test_capability_codes_match_proto.py -v
cd backend && uv run mypy app/brokers/base.py app/services/order_service.py
```

Expected: tests PASS; no new mypy errors.

- [ ] **Step 6: Commit**

```bash
git add backend/app/brokers/base.py backend/tests/unit/test_capability_codes_match_proto.py
git commit -m "feat(brokers): extend OrderType/TimeInForce Literals to Phase 8 universe"
```

---

### Task A3: Alembic 0011 — capability tables + 200-row seed

**Files:**
- Create: `backend/alembic/versions/0011_phase8a_order_capability.py`
- Test: `backend/tests/integration/test_alembic_0011.py` (new)

**Codex pattern:** F (verbatim seed values); migration uses `op.bulk_insert()`.

- [ ] **Step 1: Pre-flight — find latest revision id**

```bash
ls backend/alembic/versions/ | sort | tail -3
grep -E "^revision\s*=" backend/alembic/versions/0010_*.py
```

Capture `<REV0010>` for `down_revision`.

- [ ] **Step 2: Write failing migration test** — create `backend/tests/integration/test_alembic_0011.py`:

```python
"""Phase 8a migration 0011: order_types + time_in_force + broker_order_capability."""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_order_types_seeded(session: AsyncSession) -> None:
    rows = (await session.execute(text("SELECT code FROM order_types ORDER BY sort_order"))).scalars().all()
    assert rows == ["MARKET", "LIMIT", "STOP", "STOP_LIMIT",
                    "TRAIL", "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO"]


@pytest.mark.asyncio
async def test_time_in_force_seeded(session: AsyncSession) -> None:
    rows = (await session.execute(text(
        "SELECT code, requires_expiry FROM time_in_force ORDER BY sort_order"
    ))).all()
    assert [r.code for r in rows] == ["DAY", "GTC", "IOC", "FOK", "GTD"]
    by_code = {r.code: r.requires_expiry for r in rows}
    assert by_code["GTD"] is True and by_code["DAY"] is False


@pytest.mark.asyncio
async def test_capability_matrix_size(session: AsyncSession) -> None:
    n = (await session.execute(text("SELECT COUNT(*) FROM broker_order_capability"))).scalar_one()
    assert n == 4 * 10 * 5, f"expected 200 rows, got {n}"


@pytest.mark.asyncio
async def test_capability_supported_initial_state(session: AsyncSession) -> None:
    expected = {"schwab": 0, "ibkr": 16, "futu": 4, "alpaca": 0}
    for broker_id, exp in expected.items():
        n = (await session.execute(text(
            "SELECT COUNT(*) FROM broker_order_capability WHERE broker_id=:b AND is_supported=TRUE"
        ), {"b": broker_id})).scalar_one()
        assert n == exp, f"{broker_id}: expected {exp} supported, got {n}"


@pytest.mark.asyncio
async def test_notes_check_constraint_rejects_non_ascii(session: AsyncSession) -> None:
    with pytest.raises(Exception):
        await session.execute(text(
            "INSERT INTO broker_order_capability "
            "(broker_id, order_type, time_in_force, is_supported, notes) "
            "VALUES ('ibkr', 'MARKET', 'DAY', true, 'naïve')"
        ))
        await session.commit()
```

- [ ] **Step 3: Run test (expect FAIL — table doesn't exist)**

```bash
cd backend && uv run pytest tests/integration/test_alembic_0011.py -v
```

Expected: ERROR — `relation "order_types" does not exist`.

- [ ] **Step 4: Write the migration** — create `backend/alembic/versions/0011_phase8a_order_capability.py`:

```python
"""Phase 8a — capability matrix tables + cross-product seed.

Revision ID: 0011_phase8a_order_capability
Revises: <REV0010>
Create Date: 2026-05-06
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "0011_phase8a_order_capability"
down_revision = "<REV0010>"
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
    ("DAY", "Day",                "Order expires at end of trading day.",          False, 10),
    ("GTC", "Good Til Cancelled", "Order remains open until filled or cancelled.", False, 20),
    ("IOC", "Immediate or Cancel","Fill any portion immediately, cancel the rest.",False, 30),
    ("FOK", "Fill or Kill",       "Fill the entire order immediately or cancel.",  False, 40),
    ("GTD", "Good Til Date",      "Order remains open until specified date.",      True,  50),
]

BROKER_INITIAL_SUPPORT = [
    ("ibkr",   {"MARKET", "LIMIT", "STOP", "STOP_LIMIT"}, {"DAY", "GTC", "IOC", "FOK"}, "Coming in 8b"),
    ("futu",   {"MARKET", "LIMIT"},                       {"DAY", "GTC"},               "Coming in 8b"),
    ("schwab", set(),                                     set(),                        "Enabled by 0011a after C0 gate"),
    ("alpaca", set(),                                     set(),                        "Trade execution lands in Phase 8c"),
]


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
        sa.Column("broker_id",     sa.String(32), sa.ForeignKey("brokers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("order_type",    sa.String(32), sa.ForeignKey("order_types.code", ondelete="RESTRICT"), nullable=False),
        sa.Column("time_in_force", sa.String(16), sa.ForeignKey("time_in_force.code", ondelete="RESTRICT"), nullable=False),
        sa.Column("is_supported",  sa.Boolean(),  nullable=False, server_default=sa.false()),
        sa.Column("notes",         sa.Text(),     nullable=False, server_default=""),
        sa.Column("updated_at",    sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("broker_id", "order_type", "time_in_force"),
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
```

Replace `<REV0010>` with literal id from Step 1.

- [ ] **Step 5: Run migration + retest**

```bash
cd backend && uv run alembic upgrade head
cd backend && uv run pytest tests/integration/test_alembic_0011.py -v
```

Expected: 5 tests PASS (including CHECK-constraint negative).

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0011_phase8a_order_capability.py backend/tests/integration/test_alembic_0011.py
git commit -m "feat(db): Alembic 0011 — order capability matrix tables + 200-row seed"
```

---

### Task A4: ORM models for capability tables

**Files:**
- Create: `backend/app/models/order_capability.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/unit/test_order_capability_models.py` (new)

- [ ] **Step 1: Write failing test** — create `backend/tests/unit/test_order_capability_models.py`:

```python
from __future__ import annotations
from app.models.order_capability import OrderType, TimeInForce, BrokerOrderCapability


def test_order_type_tablename() -> None:
    assert OrderType.__tablename__ == "order_types"


def test_time_in_force_tablename() -> None:
    assert TimeInForce.__tablename__ == "time_in_force"


def test_broker_order_capability_tablename() -> None:
    assert BrokerOrderCapability.__tablename__ == "broker_order_capability"


def test_broker_order_capability_pk_columns() -> None:
    pk_names = {c.name for c in BrokerOrderCapability.__table__.primary_key.columns}
    assert pk_names == {"broker_id", "order_type", "time_in_force"}
```

- [ ] **Step 2: Run test (expect FAIL — module doesn't exist)**

```bash
cd backend && uv run pytest tests/unit/test_order_capability_models.py -v
```

Expected: `ModuleNotFoundError: app.models.order_capability`.

- [ ] **Step 3: Write the ORM module** — create `backend/app/models/order_capability.py`:

```python
"""Phase 8a — ORM models for order-type / TIF / broker capability tables."""
from __future__ import annotations
from datetime import datetime
from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, SmallInteger, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class OrderType(Base):
    __tablename__ = "order_types"
    code:        Mapped[str] = mapped_column(String(32), primary_key=True)
    label:       Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    sort_order:  Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    created_at:  Mapped[datetime] = mapped_column(server_default=text("NOW()"))


class TimeInForce(Base):
    __tablename__ = "time_in_force"
    code:            Mapped[str]  = mapped_column(String(16), primary_key=True)
    label:           Mapped[str]  = mapped_column(String(64), nullable=False)
    description:     Mapped[str]  = mapped_column(Text, nullable=False, server_default="")
    requires_expiry: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))
    sort_order:      Mapped[int]  = mapped_column(SmallInteger, nullable=False, server_default="0")
    created_at:      Mapped[datetime] = mapped_column(server_default=text("NOW()"))


class BrokerOrderCapability(Base):
    __tablename__ = "broker_order_capability"
    __table_args__ = (
        CheckConstraint(
            r"notes ~ '^[\x20-\x7E]*$' AND length(notes) <= 256",
            name="broker_order_capability_notes_printable_ascii",
        ),
        Index(
            "ix_broker_order_capability_supported", "broker_id",
            postgresql_where=text("is_supported = TRUE"),
        ),
    )
    broker_id:     Mapped[str]  = mapped_column(String(32), ForeignKey("brokers.id", ondelete="CASCADE"), primary_key=True)
    order_type:    Mapped[str]  = mapped_column(String(32), ForeignKey("order_types.code", ondelete="RESTRICT"), primary_key=True)
    time_in_force: Mapped[str]  = mapped_column(String(16), ForeignKey("time_in_force.code", ondelete="RESTRICT"), primary_key=True)
    is_supported:  Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))
    notes:         Mapped[str]  = mapped_column(Text, nullable=False, server_default="")
    updated_at:    Mapped[datetime] = mapped_column(server_default=text("NOW()"))
```

In `backend/app/models/__init__.py`, add re-export:

```python
from app.models.order_capability import OrderType, TimeInForce, BrokerOrderCapability  # noqa: F401
```

- [ ] **Step 4: Re-run test + mypy**

```bash
cd backend && uv run pytest tests/unit/test_order_capability_models.py -v
cd backend && uv run mypy app/models/order_capability.py
```

Expected: 4 PASS; no mypy errors.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/order_capability.py backend/app/models/__init__.py backend/tests/unit/test_order_capability_models.py
git commit -m "feat(models): ORM models for Phase 8a capability tables"
```

---

### Task A5: Alembic 0011a — Schwab capability flip (DEFERRED until C0 gate passes)

**Files:**
- Create: `backend/alembic/versions/0011a_phase8a_schwab_flip.py`
- Test: `backend/tests/integration/test_alembic_0011a.py` (new)

**⚠️ This task does NOT run until Task E3 (C0 empirical script) PASSES.** Sequence: A1-A4 → B → C → D → E1, E2, E3 → A5 → F → G.

- [ ] **Step 1: Write failing test** — create `backend/tests/integration/test_alembic_0011a.py`:

```python
"""Phase 8a — verify Schwab capability flip after 0011a runs."""
from __future__ import annotations
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_schwab_supported_combos_after_flip(session: AsyncSession) -> None:
    rows = (await session.execute(text(
        "SELECT order_type, time_in_force FROM broker_order_capability "
        "WHERE broker_id='schwab' AND is_supported=TRUE "
        "ORDER BY order_type, time_in_force"
    ))).all()
    expected = {(o, t) for o in ["MARKET", "LIMIT", "STOP", "STOP_LIMIT"]
                       for t in ["DAY", "GTC", "IOC", "FOK"]}
    actual = {(r.order_type, r.time_in_force) for r in rows}
    assert actual == expected
    assert len(rows) == 16


@pytest.mark.asyncio
async def test_schwab_unsupported_rows_unchanged(session: AsyncSession) -> None:
    n = (await session.execute(text(
        "SELECT COUNT(*) FROM broker_order_capability "
        "WHERE broker_id='schwab' AND is_supported=FALSE"
    ))).scalar_one()
    assert n == (10 * 5) - 16
```

- [ ] **Step 2: Write the migration** — create `backend/alembic/versions/0011a_phase8a_schwab_flip.py`:

```python
"""Phase 8a — flip Schwab capability rows after C0 empirical gate passes.

Revision ID: 0011a_phase8a_schwab_flip
Revises: 0011_phase8a_order_capability
Create Date: 2026-05-06
"""
from __future__ import annotations
from alembic import op

revision = "0011a_phase8a_schwab_flip"
down_revision = "0011_phase8a_order_capability"
branch_labels = None
depends_on = None

SUPPORTED_TYPES = ("MARKET", "LIMIT", "STOP", "STOP_LIMIT")
SUPPORTED_TIFS = ("DAY", "GTC", "IOC", "FOK")


def upgrade() -> None:
    placeholders_t = ",".join(f"'{t}'" for t in SUPPORTED_TYPES)
    placeholders_f = ",".join(f"'{f}'" for f in SUPPORTED_TIFS)
    op.execute(f"""
        UPDATE broker_order_capability
        SET is_supported = TRUE, notes = '', updated_at = NOW()
        WHERE broker_id = 'schwab'
          AND order_type IN ({placeholders_t})
          AND time_in_force IN ({placeholders_f})
    """)
    op.execute("""
        SELECT pg_notify('app_config:invalidate:order_capabilities', 'schwab')
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE broker_order_capability
        SET is_supported = FALSE, notes = 'Reverted by 0011a downgrade', updated_at = NOW()
        WHERE broker_id = 'schwab'
    """)
```

- [ ] **Step 3: Run test (expect FAIL — flip not yet applied)**

```bash
cd backend && uv run pytest tests/integration/test_alembic_0011a.py -v
```

Expected: assertion failure (16 expected, 0 supported).

- [ ] **Step 4: Apply migration + retest**

```bash
cd backend && uv run alembic upgrade head
cd backend && uv run pytest tests/integration/test_alembic_0011a.py -v
```

Expected: 2 PASS.

- [ ] **Step 5: Commit (DEFERRED — only after E3 C0 PASS)**

```bash
git add backend/alembic/versions/0011a_phase8a_schwab_flip.py backend/tests/integration/test_alembic_0011a.py
git commit -m "feat(db): Alembic 0011a — flip Schwab capability rows post-C0"
```

---

## Chunk B — OrderCapabilityService + capability API + capability gate + consumer dedup

**Codex patterns most likely to bite:** D (bounded TTL cache size), E (lazy-singleton init/cleanup for Redis subscriber), F (verbatim error code strings), G (XFF trust on admin endpoint).

### Task B1: OrderCapabilityService with 60s LRU cache + Redis-pubsub bust

**Files:**
- Create: `backend/app/services/order_capability_service.py`
- Test: `backend/tests/unit/test_order_capability_service.py` (new)

- [ ] **Step 1: Write failing test** — create `backend/tests/unit/test_order_capability_service.py`:

```python
"""Phase 8a — OrderCapabilityService cache + invalidation."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from freezegun import freeze_time

from app.services.order_capability_service import OrderCapabilityService


@pytest.mark.asyncio
async def test_is_supported_hits_db_then_caches() -> None:
    db = AsyncMock()
    db.fetch_one.return_value = {"is_supported": True, "notes": ""}
    redis = MagicMock()
    svc = OrderCapabilityService(db=db, redis=redis)

    assert await svc.is_supported("schwab", "MARKET", "DAY") is True
    assert await svc.is_supported("schwab", "MARKET", "DAY") is True
    assert db.fetch_one.await_count == 1, "second call should hit cache, not DB"


@pytest.mark.asyncio
async def test_unknown_combo_returns_false_and_caches_negative() -> None:
    db = AsyncMock()
    db.fetch_one.return_value = None
    redis = MagicMock()
    svc = OrderCapabilityService(db=db, redis=redis)

    assert await svc.is_supported("schwab", "TRAIL", "DAY") is False
    assert await svc.is_supported("schwab", "TRAIL", "DAY") is False
    assert db.fetch_one.await_count == 1


@pytest.mark.asyncio
async def test_cache_ttl_60s_expires() -> None:
    db = AsyncMock()
    db.fetch_one.return_value = {"is_supported": True, "notes": ""}
    redis = MagicMock()
    svc = OrderCapabilityService(db=db, redis=redis)

    with freeze_time("2026-05-06 10:00:00") as frozen:
        await svc.is_supported("schwab", "MARKET", "DAY")
        frozen.tick(delta=61)
        await svc.is_supported("schwab", "MARKET", "DAY")
    assert db.fetch_one.await_count == 2


@pytest.mark.asyncio
async def test_invalidate_drops_broker_cache() -> None:
    db = AsyncMock()
    db.fetch_one.return_value = {"is_supported": True, "notes": ""}
    redis = MagicMock()
    svc = OrderCapabilityService(db=db, redis=redis)

    await svc.is_supported("schwab", "MARKET", "DAY")
    svc.invalidate("schwab")
    await svc.is_supported("schwab", "MARKET", "DAY")
    assert db.fetch_one.await_count == 2


@pytest.mark.asyncio
async def test_pubsub_failure_increments_metric(monkeypatch) -> None:
    """MED-5: silent pubsub failure must increment the failure metric."""
    from app.core import metrics
    db = AsyncMock()
    redis = MagicMock()
    redis.publish = AsyncMock(side_effect=ConnectionError("redis down"))
    svc = OrderCapabilityService(db=db, redis=redis)

    before = metrics.order_capability_pubsub_failures_total._value.get()
    await svc.publish_invalidation("schwab")
    after = metrics.order_capability_pubsub_failures_total._value.get()
    assert after == before + 1
```

- [ ] **Step 2: Run test (expect FAIL — module doesn't exist)**

```bash
cd backend && uv run pytest tests/unit/test_order_capability_service.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write the service** — create `backend/app/services/order_capability_service.py`:

```python
"""Phase 8a — OrderCapabilityService.

Capability matrix lookups with 60s in-process LRU cache + Redis-pubsub
bust on admin write. Single-worker assumption holds (Phase 9 will replace
the in-process dict with a shared cache when multi-worker lands).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from app.core import metrics

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from redis.asyncio import Redis

logger = structlog.get_logger(__name__)

_CACHE_TTL_SECONDS = 60.0
_INVALIDATION_TOPIC = "app_config:invalidate:order_capabilities"


@dataclass(frozen=True)
class CapabilityRow:
    is_supported: bool
    notes: str


class OrderCapabilityService:
    def __init__(self, db: "AsyncSession", redis: "Redis") -> None:
        self._db = db
        self._redis = redis
        self._cache: dict[tuple[str, str, str], tuple[CapabilityRow, float]] = {}

    async def is_supported(self, broker_id: str, order_type: str, tif: str) -> bool:
        row = await self._lookup(broker_id, order_type, tif)
        result = "supported" if row.is_supported else "unsupported"
        if row is _UNKNOWN:
            result = "unknown_broker"
        metrics.order_capability_check_total.labels(broker=broker_id, result=result).inc()
        return row.is_supported

    async def get_notes(self, broker_id: str, order_type: str, tif: str) -> str:
        row = await self._lookup(broker_id, order_type, tif)
        return row.notes

    async def list_capabilities(self, broker_id: str) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT order_type, time_in_force, is_supported, notes "
            "FROM broker_order_capability WHERE broker_id = :b "
            "ORDER BY order_type, time_in_force",
            {"b": broker_id},
        )
        return [dict(r) for r in rows]

    def invalidate(self, broker_id: str) -> None:
        keys_to_drop = [k for k in self._cache if k[0] == broker_id]
        for k in keys_to_drop:
            del self._cache[k]
        metrics.order_capability_pubsub_invalidations_total.inc()

    async def publish_invalidation(self, broker_id: str) -> None:
        try:
            await self._redis.publish(_INVALIDATION_TOPIC, broker_id)
        except (ConnectionError, OSError, TimeoutError) as exc:
            logger.error("order_capability_pubsub_publish_failed",
                         broker_id=broker_id, error=str(exc))
            metrics.order_capability_pubsub_failures_total.inc()
            self.invalidate(broker_id)

    async def _lookup(self, broker_id: str, order_type: str, tif: str) -> CapabilityRow:
        key = (broker_id, order_type, tif)
        now = time.monotonic()
        entry = self._cache.get(key)
        if entry is not None:
            row, ts = entry
            if now - ts < _CACHE_TTL_SECONDS:
                metrics.order_capability_cache_hits_total.labels(broker=broker_id).inc()
                return row
        metrics.order_capability_cache_misses_total.labels(broker=broker_id).inc()
        rec = await self._db.fetch_one(
            "SELECT is_supported, notes FROM broker_order_capability "
            "WHERE broker_id=:b AND order_type=:o AND time_in_force=:t",
            {"b": broker_id, "o": order_type, "t": tif},
        )
        row = CapabilityRow(False, "") if rec is None else CapabilityRow(rec["is_supported"], rec["notes"])
        self._cache[key] = (row, now)
        return row


_UNKNOWN = CapabilityRow(False, "")
```

- [ ] **Step 4: Run test (expect PASS)**

```bash
cd backend && uv run pytest tests/unit/test_order_capability_service.py -v
```

Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/order_capability_service.py backend/tests/unit/test_order_capability_service.py
git commit -m "feat(services): OrderCapabilityService with 60s LRU + pubsub bust"
```

---

### Task B2: GET /api/brokers/{broker_id}/capabilities endpoint

**Files:**
- Create: `backend/app/api/capabilities.py`
- Modify: `backend/app/main.py` (mount router)
- Test: `backend/tests/integration/test_capabilities_api.py` (new)

- [ ] **Step 1: Write failing test** — create `backend/tests/integration/test_capabilities_api.py`:

```python
"""Phase 8a — GET /api/brokers/{id}/capabilities."""
from __future__ import annotations
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_schwab_capabilities_returns_full_universe(client: AsyncClient) -> None:
    rsp = await client.get("/api/brokers/schwab/capabilities")
    assert rsp.status_code == 200
    body = rsp.json()
    assert {r["code"] for r in body["order_types"]} == {
        "MARKET", "LIMIT", "STOP", "STOP_LIMIT",
        "TRAIL", "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
    }
    assert {r["code"] for r in body["time_in_force"]} == {"DAY", "GTC", "IOC", "FOK", "GTD"}
    assert len(body["combos"]) == 50
    # Pre-flip: zero supported for Schwab.
    supported = [c for c in body["combos"] if c["supported"]]
    assert supported == []


@pytest.mark.asyncio
async def test_get_ibkr_capabilities_supported_set(client: AsyncClient) -> None:
    rsp = await client.get("/api/brokers/ibkr/capabilities")
    assert rsp.status_code == 200
    body = rsp.json()
    supported = {(c["order_type"], c["time_in_force"]) for c in body["combos"] if c["supported"]}
    assert supported == {(o, t) for o in ["MARKET", "LIMIT", "STOP", "STOP_LIMIT"]
                                  for t in ["DAY", "GTC", "IOC", "FOK"]}


@pytest.mark.asyncio
async def test_get_unknown_broker_returns_404(client: AsyncClient) -> None:
    rsp = await client.get("/api/brokers/bogus/capabilities")
    assert rsp.status_code == 404


@pytest.mark.asyncio
async def test_combos_ordered_by_sort_order(client: AsyncClient) -> None:
    rsp = await client.get("/api/brokers/schwab/capabilities")
    body = rsp.json()
    type_codes = [r["code"] for r in body["order_types"]]
    assert type_codes == ["MARKET", "LIMIT", "STOP", "STOP_LIMIT",
                          "TRAIL", "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO"]
```

- [ ] **Step 2: Run test (expect FAIL)**

```bash
cd backend && uv run pytest tests/integration/test_capabilities_api.py -v
```

Expected: 404 (route missing).

- [ ] **Step 3: Implement endpoint** — create `backend/app/api/capabilities.py`:

```python
"""Phase 8a — capability matrix HTTP endpoints."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.core.brokers import KNOWN_BROKER_IDS

router = APIRouter(prefix="/api/brokers", tags=["capabilities"])


class OrderTypeRow(BaseModel):
    code: str
    label: str
    description: str
    sort_order: int


class TimeInForceRow(BaseModel):
    code: str
    label: str
    description: str
    requires_expiry: bool
    sort_order: int


class CapabilityComboRow(BaseModel):
    order_type: str
    time_in_force: str
    supported: bool
    notes: str


class BrokerCapabilitiesResponse(BaseModel):
    broker_id: str
    order_types: list[OrderTypeRow]
    time_in_force: list[TimeInForceRow]
    combos: list[CapabilityComboRow]


@router.get("/{broker_id}/capabilities", response_model=BrokerCapabilitiesResponse)
async def get_broker_capabilities(
    broker_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> BrokerCapabilitiesResponse:
    if broker_id not in KNOWN_BROKER_IDS:
        raise HTTPException(404, detail=f"unknown broker_id: {broker_id}")

    types = (await session.execute(text(
        "SELECT code, label, description, sort_order FROM order_types ORDER BY sort_order"
    ))).all()
    tifs = (await session.execute(text(
        "SELECT code, label, description, requires_expiry, sort_order "
        "FROM time_in_force ORDER BY sort_order"
    ))).all()
    combos = (await session.execute(text(
        "SELECT order_type, time_in_force, is_supported, notes "
        "FROM broker_order_capability WHERE broker_id=:b "
        "ORDER BY order_type, time_in_force"
    ), {"b": broker_id})).all()

    return BrokerCapabilitiesResponse(
        broker_id=broker_id,
        order_types=[OrderTypeRow.model_validate(dict(r._mapping)) for r in types],
        time_in_force=[TimeInForceRow.model_validate(dict(r._mapping)) for r in tifs],
        combos=[CapabilityComboRow(
            order_type=r.order_type, time_in_force=r.time_in_force,
            supported=r.is_supported, notes=r.notes,
        ) for r in combos],
    )
```

In `backend/app/main.py`, mount the router alongside other API routers:

```python
from app.api.capabilities import router as capabilities_router
app.include_router(capabilities_router)
```

- [ ] **Step 4: Re-run test (expect PASS)**

```bash
cd backend && uv run pytest tests/integration/test_capabilities_api.py -v
```

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/capabilities.py backend/app/main.py backend/tests/integration/test_capabilities_api.py
git commit -m "feat(api): GET /api/brokers/{id}/capabilities"
```

---

### Task B3: POST /api/admin/order-capabilities — admin write with PUT-semantics + CSRF + code-set guard

**Files:**
- Modify: `backend/app/api/admin.py` (or matching admin router file)
- Test: `backend/tests/integration/test_admin_order_capabilities.py` (new)

**Codex patterns:** F (verbatim error codes), G (XFF/CSRF trust gate).

- [ ] **Step 1: Pre-flight — find admin router + CSRF dep**

```bash
grep -rn "POST.*admin\|csrf\|confirmation_nonce" backend/app/api/ | head -20
```

- [ ] **Step 2: Write failing test** — create `backend/tests/integration/test_admin_order_capabilities.py`:

```python
"""Phase 8a — POST /api/admin/order-capabilities."""
from __future__ import annotations
import pytest
from httpx import AsyncClient


@pytest.fixture
async def admin_nonce(client: AsyncClient) -> str:
    rsp = await client.post("/api/admin/csrf/issue")
    assert rsp.status_code == 200
    return rsp.json()["nonce"]


@pytest.mark.asyncio
async def test_post_full_row_succeeds(client: AsyncClient, admin_nonce: str) -> None:
    body = {"broker_id": "ibkr", "order_type": "MARKET", "time_in_force": "DAY",
            "is_supported": True, "notes": "tweaked by operator"}
    rsp = await client.post(
        "/api/admin/order-capabilities",
        json=body, headers={"X-Confirm-Nonce": admin_nonce},
    )
    assert rsp.status_code == 200, rsp.text


@pytest.mark.asyncio
async def test_post_partial_body_rejected_400(client: AsyncClient, admin_nonce: str) -> None:
    body = {"broker_id": "ibkr", "is_supported": True}  # MED-2: PUT-semantics
    rsp = await client.post(
        "/api/admin/order-capabilities",
        json=body, headers={"X-Confirm-Nonce": admin_nonce},
    )
    assert rsp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_post_unknown_order_type_rejected_400(client: AsyncClient, admin_nonce: str) -> None:
    body = {"broker_id": "ibkr", "order_type": "BLOOP", "time_in_force": "DAY",
            "is_supported": True, "notes": ""}
    rsp = await client.post(
        "/api/admin/order-capabilities",
        json=body, headers={"X-Confirm-Nonce": admin_nonce},
    )
    assert rsp.status_code == 400
    assert "unknown_order_type_code" in rsp.json()["detail"]["error"]["code"]


@pytest.mark.asyncio
async def test_post_missing_csrf_rejected_403(client: AsyncClient) -> None:
    body = {"broker_id": "ibkr", "order_type": "MARKET", "time_in_force": "DAY",
            "is_supported": True, "notes": ""}
    rsp = await client.post("/api/admin/order-capabilities", json=body)
    assert rsp.status_code == 403


@pytest.mark.asyncio
async def test_post_non_ascii_notes_rejected_400(client: AsyncClient, admin_nonce: str) -> None:
    body = {"broker_id": "ibkr", "order_type": "MARKET", "time_in_force": "DAY",
            "is_supported": True, "notes": "naïve"}
    rsp = await client.post(
        "/api/admin/order-capabilities",
        json=body, headers={"X-Confirm-Nonce": admin_nonce},
    )
    assert rsp.status_code == 400
```

- [ ] **Step 3: Run test (expect FAIL)**

```bash
cd backend && uv run pytest tests/integration/test_admin_order_capabilities.py -v
```

- [ ] **Step 4: Implement endpoint** — extend `backend/app/api/admin.py`:

```python
import re
from pydantic import BaseModel, Field, field_validator
from app.services.order_capability_service import OrderCapabilityService

_PRINTABLE_ASCII = re.compile(r"^[\x20-\x7E]*$")


class CapabilityWriteBody(BaseModel):
    broker_id: str = Field(..., min_length=1, max_length=32)
    order_type: str = Field(..., min_length=1, max_length=32)
    time_in_force: str = Field(..., min_length=1, max_length=16)
    is_supported: bool
    notes: str = Field(..., max_length=256)

    @field_validator("notes")
    @classmethod
    def _ascii_only(cls, v: str) -> str:
        if not _PRINTABLE_ASCII.match(v):
            raise ValueError("notes must contain only printable ASCII")
        return v


@router.post("/order-capabilities", dependencies=[Depends(require_csrf_nonce)])
async def upsert_order_capability(
    body: CapabilityWriteBody,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    capability_svc: Annotated[OrderCapabilityService, Depends(get_capability_service)],
) -> dict:
    if body.broker_id not in KNOWN_BROKER_IDS:
        raise HTTPException(400, detail={"error": {
            "code": "unknown_broker_id", "broker_id": body.broker_id}})

    type_exists = (await session.execute(text(
        "SELECT 1 FROM order_types WHERE code = :c"
    ), {"c": body.order_type})).scalar_one_or_none()
    if not type_exists:
        raise HTTPException(400, detail={"error": {
            "code": "unknown_order_type_code", "order_type": body.order_type}})

    tif_exists = (await session.execute(text(
        "SELECT 1 FROM time_in_force WHERE code = :c"
    ), {"c": body.time_in_force})).scalar_one_or_none()
    if not tif_exists:
        raise HTTPException(400, detail={"error": {
            "code": "unknown_time_in_force_code", "time_in_force": body.time_in_force}})

    await session.execute(text("""
        INSERT INTO broker_order_capability
            (broker_id, order_type, time_in_force, is_supported, notes, updated_at)
        VALUES (:b, :o, :t, :s, :n, NOW())
        ON CONFLICT (broker_id, order_type, time_in_force)
        DO UPDATE SET is_supported = EXCLUDED.is_supported,
                      notes = EXCLUDED.notes,
                      updated_at = NOW()
    """), {"b": body.broker_id, "o": body.order_type, "t": body.time_in_force,
            "s": body.is_supported, "n": body.notes})
    await session.commit()

    metrics.order_capability_admin_writes_total.inc()
    await capability_svc.publish_invalidation(body.broker_id)
    return {"ok": True}
```

- [ ] **Step 5: Re-run test (expect PASS)**

```bash
cd backend && uv run pytest tests/integration/test_admin_order_capabilities.py -v
```

Expected: 5 PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/admin.py backend/tests/integration/test_admin_order_capabilities.py
git commit -m "feat(api): admin POST /api/admin/order-capabilities (PUT-semantics + CSRF + code-set guard)"
```

---

### Task B4: Capability gate in OrderService — strict ordering (kill_switch → maintenance → capability → dispatch)

**Files:**
- Modify: `backend/app/services/order_service.py` (preview_order, place_order, modify_order)
- Test: `backend/tests/unit/test_order_service_capability_gate.py` (new)

**Codex pattern:** A (parens around tuple-catch). CRIT-3 — strict order matters.

- [ ] **Step 1: Pre-flight — find current ordering**

```bash
grep -n "kill_switch\|maintenance\|broker_registry\.get\|preview_order\|place_order" backend/app/services/order_service.py | head -30
```

- [ ] **Step 2: Write failing test** — create `backend/tests/unit/test_order_service_capability_gate.py`:

```python
"""Phase 8a — capability gate integrated into OrderService."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from app.services.order_service import OrderService
from app.brokers.base import Order


@pytest.fixture
def make_order():
    def _f(broker_id="ibkr", order_type="MARKET", tif="DAY") -> Order:
        return Order(
            account_id="acct1", conid=1234, side="BUY",
            quantity="1", order_type=order_type, time_in_force=tif,
            client_order_id="cli-1",
        )
    return _f


@pytest.mark.asyncio
async def test_kill_switch_runs_first(make_order):
    cap = AsyncMock(); cap.is_supported.return_value = True
    maint = AsyncMock(); maint.compute.return_value.active = False
    broker_policy = AsyncMock(); broker_policy.is_kill_switch_enabled.return_value = True

    svc = OrderService(capability=cap, maintenance=maint, broker_policy=broker_policy,
                       broker_registry=AsyncMock(), session=AsyncMock())
    with pytest.raises(Exception) as ei:
        await svc.preview_order(make_order())
    assert "kill_switch" in str(ei.value).lower()
    cap.is_supported.assert_not_called()


@pytest.mark.asyncio
async def test_maintenance_runs_before_capability(make_order):
    cap = AsyncMock(); cap.is_supported.return_value = True
    maint = AsyncMock(); maint.compute.return_value.active = True
    broker_policy = AsyncMock(); broker_policy.is_kill_switch_enabled.return_value = False

    svc = OrderService(capability=cap, maintenance=maint, broker_policy=broker_policy,
                       broker_registry=AsyncMock(), session=AsyncMock())
    with pytest.raises(Exception) as ei:
        await svc.preview_order(make_order())
    assert "maintenance" in str(ei.value).lower() or "503" in str(ei.value)
    cap.is_supported.assert_not_called()


@pytest.mark.asyncio
async def test_unsupported_combo_raises_422(make_order):
    cap = AsyncMock(); cap.is_supported.return_value = False
    cap.get_notes.return_value = "Coming in 8b"
    maint = AsyncMock(); maint.compute.return_value.active = False
    broker_policy = AsyncMock(); broker_policy.is_kill_switch_enabled.return_value = False
    broker_registry = AsyncMock()

    svc = OrderService(capability=cap, maintenance=maint, broker_policy=broker_policy,
                       broker_registry=broker_registry, session=AsyncMock())
    with pytest.raises(Exception) as ei:
        await svc.preview_order(make_order(order_type="TRAIL"))
    assert "unsupported_order_type_for_broker" in str(ei.value)
    broker_registry.get.assert_not_called()


@pytest.mark.asyncio
async def test_supported_combo_proceeds_to_dispatch(make_order):
    cap = AsyncMock(); cap.is_supported.return_value = True
    maint = AsyncMock(); maint.compute.return_value.active = False
    broker_policy = AsyncMock(); broker_policy.is_kill_switch_enabled.return_value = False
    broker_registry = AsyncMock()
    broker_registry.get.return_value.preview_order = AsyncMock(return_value={"ok": True})

    svc = OrderService(capability=cap, maintenance=maint, broker_policy=broker_policy,
                       broker_registry=broker_registry, session=AsyncMock())
    result = await svc.preview_order(make_order())
    assert result == {"ok": True}
    broker_registry.get.assert_called_once()
```

- [ ] **Step 3: Run test (expect FAIL — gate not implemented)**

```bash
cd backend && uv run pytest tests/unit/test_order_service_capability_gate.py -v
```

- [ ] **Step 4: Implement strict-order gate** — extend `backend/app/services/order_service.py` (preview_order, place_order, modify_order all share helper):

```python
from fastapi import HTTPException
from app.services.order_capability_service import OrderCapabilityService

class OrderService:
    def __init__(self, *, capability: OrderCapabilityService, maintenance, broker_policy,
                 broker_registry, session) -> None:
        self._capability = capability
        self._maintenance = maintenance
        self._broker_policy = broker_policy
        self._broker_registry = broker_registry
        self._session = session

    async def _validate_pre_dispatch(self, order: Order, broker_id: str) -> None:
        # 1. Kill switch (CRIT-3 — race-safe; first).
        if await self._broker_policy.is_kill_switch_enabled(broker_id):
            raise HTTPException(503, detail={"error": {
                "code": "broker_kill_switch_enabled", "broker": broker_id}})

        # 2. Maintenance window.
        m = await self._maintenance.compute(broker_id)
        if m.active:
            raise HTTPException(
                503, detail={"error": {"code": "broker_maintenance", "broker": broker_id}},
                headers={"Retry-After": str(m.retry_after_seconds)},
            )

        # 3. Capability check.
        ok = await self._capability.is_supported(broker_id, order.order_type, order.time_in_force)
        if not ok:
            notes = await self._capability.get_notes(broker_id, order.order_type, order.time_in_force)
            raise HTTPException(422, detail={"error": {
                "code": "unsupported_order_type_for_broker",
                "broker": broker_id,
                "order_type": order.order_type,
                "time_in_force": order.time_in_force,
                "notes": notes,
            }})

    async def preview_order(self, order: Order) -> dict:
        broker_id = await self._resolve_broker_id(order.account_id)
        await self._validate_pre_dispatch(order, broker_id)
        adapter = await self._broker_registry.get(broker_id)
        return await adapter.preview_order(order)
```

Apply identical `_validate_pre_dispatch` call as the first line of `place_order` and `modify_order` (after resolving `broker_id`).

- [ ] **Step 5: Re-run test (expect PASS)**

```bash
cd backend && uv run pytest tests/unit/test_order_service_capability_gate.py -v
```

Expected: 4 PASS.

- [ ] **Step 6: Run pre-existing 5b/5c order_service tests for regression**

```bash
cd backend && uv run pytest tests/unit/test_order_service.py tests/integration/test_orders_api.py -v
```

Expected: previous tests still PASS (existing IBKR/Futu combos seeded `is_supported=true` so behavior unchanged).

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/order_service.py backend/tests/unit/test_order_service_capability_gate.py
git commit -m "feat(orders): capability gate (kill_switch → maintenance → capability → dispatch)"
```

---

### Task B5: OrderEventConsumer dedup extension (CRIT-2 backend half)

**Files:**
- Modify: `backend/app/services/order_event_consumer.py` (`_process_event`)
- Test: `backend/tests/unit/test_order_event_consumer_dedup.py` (new)

**Codex pattern:** A (parens). CRIT-2 backend defense in depth: no-op on same-rank-same-status no-new-fill events.

- [ ] **Step 1: Pre-flight — find current `_process_event`**

```bash
grep -n "_process_event\|order_status_rank\|terminal" backend/app/services/order_event_consumer.py | head -20
```

- [ ] **Step 2: Write failing test** — create `backend/tests/unit/test_order_event_consumer_dedup.py`:

```python
"""Phase 8a CRIT-2 backend half: dedupe same-rank, same-status, no-new-exec events."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.order_event_consumer import OrderEventConsumer
from app.brokers.base import OrderEventMessage


@pytest.mark.asyncio
async def test_duplicate_submitted_no_new_exec_is_noop():
    session = AsyncMock()
    session.execute.return_value.rowcount = 1
    consumer = OrderEventConsumer(session_factory=lambda: session, label="schwab-paper",
                                  account_id="acct1")
    ev = OrderEventMessage(broker_order_id="b1", client_order_id="c1",
                           kind="status", status="submitted", exec_id="")
    # First event: writes status row.
    await consumer._process_event(ev)
    first_calls = session.execute.await_count
    # Duplicate event with same status + no exec_id: no-op.
    await consumer._process_event(ev)
    assert session.execute.await_count == first_calls, "duplicate submitted should be no-op"


@pytest.mark.asyncio
async def test_duplicate_status_with_new_exec_id_records():
    session = AsyncMock()
    session.execute.return_value.rowcount = 1
    consumer = OrderEventConsumer(session_factory=lambda: session, label="schwab-paper",
                                  account_id="acct1")
    ev1 = OrderEventMessage(broker_order_id="b1", client_order_id="c1",
                            kind="status", status="submitted", exec_id="")
    ev2 = OrderEventMessage(broker_order_id="b1", client_order_id="c1",
                            kind="fill", status="submitted", exec_id="exec-1")
    await consumer._process_event(ev1)
    n1 = session.execute.await_count
    await consumer._process_event(ev2)
    assert session.execute.await_count > n1, "fill event with new exec_id must be recorded"
```

- [ ] **Step 3: Run test (expect FAIL — dedup not in place)**

```bash
cd backend && uv run pytest tests/unit/test_order_event_consumer_dedup.py -v
```

- [ ] **Step 4: Add dedup branch** in `_process_event`:

```python
async def _process_event(self, ev: OrderEventMessage) -> None:
    # CRIT-2 (Phase 8a): no-op on same-rank-same-status events with no new exec_id.
    current = await self._session.execute(text(
        "SELECT status FROM orders WHERE broker_order_id = :b AND account_id = :a"
    ), {"b": ev.broker_order_id, "a": self._account_id})
    cur = current.scalar_one_or_none()
    if cur is not None and cur == ev.status and not ev.exec_id:
        return  # idempotent echo from sidecar restart hydration
    # ...existing 5c rank predicate + UPDATE/INSERT logic continues...
```

- [ ] **Step 5: Re-run test + 5b/5c regressions**

```bash
cd backend && uv run pytest tests/unit/test_order_event_consumer_dedup.py tests/unit/test_order_event_consumer.py -v
```

Expected: 2 new PASS; existing 5b/5c tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/order_event_consumer.py backend/tests/unit/test_order_event_consumer_dedup.py
git commit -m "feat(orders): consumer dedup for same-rank-same-status no-exec events (CRIT-2)"
```

---

## Chunk C — Schwab sidecar handlers + client + normalize

**Codex patterns most likely to bite:** A (parens around tuple-catch), B (cancel+gather supervisor on poller tasks), C (per-callback isolation in OrderEvent fan-out), F (verbatim error code strings).

### Task C1: schwabdev REST wrappers in client.py

**Files:**
- Modify: `sidecar_schwab/client.py` (add 5 methods)
- Test: `sidecar_schwab/tests/test_client_orders.py` (new)

- [ ] **Step 1: Pre-flight read**

```bash
grep -n "def \|class \|_sync_tokens" sidecar_schwab/client.py | head -30
```

- [ ] **Step 2: Write failing test** — create `sidecar_schwab/tests/test_client_orders.py`:

```python
"""Phase 8a — schwabdev client wrappers for orders."""
from __future__ import annotations
import pytest
from unittest.mock import MagicMock

from sidecar_schwab.client import SchwabClient


@pytest.fixture
def fake_schwab():
    """Mock schwabdev.Client."""
    sd = MagicMock()
    sd.order_place = MagicMock()
    sd.order_cancel = MagicMock()
    sd.order_replace = MagicMock()
    sd.account_orders = MagicMock()
    sd.order_details = MagicMock()
    return sd


def test_place_order_returns_broker_order_id(fake_schwab):
    rsp = MagicMock()
    rsp.status_code = 201
    rsp.headers = {"Location": "https://api.schwab.com/trader/v1/accounts/HASH/orders/12345"}
    fake_schwab.order_place.return_value = rsp
    client = SchwabClient(_client=fake_schwab)
    result = client.place_order(account_hash="HASH", payload={"foo": "bar"})
    assert result["broker_order_id"] == "12345"


def test_place_order_raises_on_4xx(fake_schwab):
    rsp = MagicMock()
    rsp.status_code = 400
    rsp.text = "bad payload"
    fake_schwab.order_place.return_value = rsp
    client = SchwabClient(_client=fake_schwab)
    with pytest.raises(Exception) as ei:
        client.place_order(account_hash="HASH", payload={})
    assert "bad payload" in str(ei.value) or "400" in str(ei.value)


def test_get_orders_since_returns_list(fake_schwab):
    rsp = MagicMock()
    rsp.status_code = 200
    rsp.json.return_value = [{"orderId": 1}, {"orderId": 2}]
    fake_schwab.account_orders.return_value = rsp
    client = SchwabClient(_client=fake_schwab)
    rows = client.get_orders_since("HASH", since_iso="2026-05-06T00:00:00Z")
    assert len(rows) == 2


def test_replace_order_returns_new_id(fake_schwab):
    rsp = MagicMock()
    rsp.status_code = 201
    rsp.headers = {"Location": "https://api.schwab.com/trader/v1/accounts/HASH/orders/99999"}
    fake_schwab.order_replace.return_value = rsp
    client = SchwabClient(_client=fake_schwab)
    result = client.replace_order(account_hash="HASH", order_id="12345", payload={})
    assert result["broker_order_id"] == "99999"
```

- [ ] **Step 3: Run test (expect FAIL)**

```bash
cd sidecar_schwab && uv run pytest tests/test_client_orders.py -v
```

- [ ] **Step 4: Implement methods** — extend `sidecar_schwab/client.py`:

```python
import re

_LOCATION_RE = re.compile(r"/orders/(?P<id>\d+)")


def _extract_broker_order_id(headers: dict) -> str:
    loc = headers.get("Location") or headers.get("location") or ""
    m = _LOCATION_RE.search(loc)
    if not m:
        raise ValueError(f"unable to extract orderId from Location header: {loc!r}")
    return m.group("id")


class SchwabClient:
    # ...existing __init__, _sync_tokens, etc...

    def place_order(self, *, account_hash: str, payload: dict) -> dict:
        rsp = self._client.order_place(account_hash, payload)
        if rsp.status_code not in (200, 201):
            raise SchwabHttpError(rsp.status_code, getattr(rsp, "text", ""))
        return {"broker_order_id": _extract_broker_order_id(dict(rsp.headers))}

    def cancel_order(self, *, account_hash: str, order_id: str) -> None:
        rsp = self._client.order_cancel(account_hash, order_id)
        if rsp.status_code not in (200, 204):
            raise SchwabHttpError(rsp.status_code, getattr(rsp, "text", ""))

    def replace_order(self, *, account_hash: str, order_id: str, payload: dict) -> dict:
        rsp = self._client.order_replace(account_hash, order_id, payload)
        if rsp.status_code not in (200, 201):
            raise SchwabHttpError(rsp.status_code, getattr(rsp, "text", ""))
        return {"broker_order_id": _extract_broker_order_id(dict(rsp.headers))}

    def get_orders_since(self, account_hash: str, since_iso: str) -> list[dict]:
        rsp = self._client.account_orders(account_hash, fromEnteredTime=since_iso)
        if rsp.status_code != 200:
            raise SchwabHttpError(rsp.status_code, getattr(rsp, "text", ""))
        return rsp.json()

    def get_order(self, account_hash: str, order_id: str) -> dict:
        rsp = self._client.order_details(account_hash, order_id)
        if rsp.status_code != 200:
            raise SchwabHttpError(rsp.status_code, getattr(rsp, "text", ""))
        return rsp.json()


class SchwabHttpError(Exception):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Schwab HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body
```

- [ ] **Step 5: Re-run test (expect PASS)**

```bash
cd sidecar_schwab && uv run pytest tests/test_client_orders.py -v
```

Expected: 4 PASS.

- [ ] **Step 6: Commit**

```bash
git add sidecar_schwab/client.py sidecar_schwab/tests/test_client_orders.py
git commit -m "feat(schwab-sidecar): client wrappers for place/cancel/replace/get_orders"
```

---

### Task C2: Order normalizers in normalize.py (with HIGH-3 `replaced` kind)

**Files:**
- Modify: `sidecar_schwab/normalize.py` (add 2 functions)
- Test: `sidecar_schwab/tests/test_normalize_orders.py` (new)

- [ ] **Step 1: Write failing test** — create `sidecar_schwab/tests/test_normalize_orders.py`:

```python
"""Phase 8a — Schwab order/event normalizers (covers all 11 statuses from spec §6)."""
from __future__ import annotations
import pytest

from sidecar_schwab.normalize import schwab_status_to_wire, schwab_to_wire_order


@pytest.mark.parametrize("schwab,wire,rank,terminal", [
    ("AWAITING_PARENT_ORDER", "pending_submit", 0, False),
    ("PENDING_ACTIVATION", "pending_submit", 0, False),
    ("QUEUED", "submitted", 1, False),
    ("WORKING", "submitted", 1, False),
    ("PENDING_CANCEL", "cancel_requested", 2, False),
    ("PENDING_REPLACE", "modify_requested", 2, False),
    ("FILLED", "filled", 4, True),
    ("CANCELED", "cancelled", 4, True),
    ("REPLACED", "cancelled", 4, True),  # HIGH-3 — old order cancelled with kind="replaced"
    ("REJECTED", "rejected", 4, True),
    ("EXPIRED", "expired", 4, True),
])
def test_status_mapping(schwab, wire, rank, terminal):
    result = schwab_status_to_wire(schwab)
    assert result.wire_status == wire
    assert result.rank == rank
    assert result.terminal is terminal


def test_replaced_status_emits_replaced_kind():
    result = schwab_status_to_wire("REPLACED")
    assert result.kind == "replaced"


def test_unknown_schwab_status_raises_warning():
    with pytest.warns(UserWarning, match="unknown schwab status"):
        schwab_status_to_wire("MYSTERY")


def test_executionleg_extracted_as_fill_event():
    schwab_order = {
        "orderId": 12345,
        "status": "FILLED",
        "orderActivityCollection": [{
            "executionType": "FILL",
            "quantity": 100,
            "executionLegs": [
                {"legId": 1, "price": 42.5, "quantity": 100, "time": "2026-05-06T14:30:00Z"},
            ],
        }],
    }
    fills = list(schwab_to_wire_order(schwab_order, client_order_id="cli-1").fills)
    assert len(fills) == 1
    assert fills[0].exec_id == "1"
    assert str(fills[0].price) == "42.5"


def test_avg_fill_price_inferred_when_leg_price_null():
    """Phase 7a M2: when executionLeg.price is null, infer from quantity × marketValue."""
    schwab_order = {
        "orderId": 12345,
        "status": "FILLED",
        "quantity": 100,
        "marketValue": 4250.0,
        "orderActivityCollection": [{
            "executionType": "FILL",
            "quantity": 100,
            "executionLegs": [
                {"legId": 1, "price": None, "quantity": 100, "time": "2026-05-06T14:30:00Z"},
            ],
        }],
    }
    fills = list(schwab_to_wire_order(schwab_order, client_order_id="cli-1").fills)
    assert fills[0].avg_fill_price_inferred is True
```

- [ ] **Step 2: Run test (expect FAIL)**

```bash
cd sidecar_schwab && uv run pytest tests/test_normalize_orders.py -v
```

- [ ] **Step 3: Implement normalizers** — extend `sidecar_schwab/normalize.py`:

```python
import warnings
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class StatusMapping:
    wire_status: str
    rank: int
    terminal: bool
    kind: str = "status"


_STATUS_MAP: dict[str, StatusMapping] = {
    "AWAITING_PARENT_ORDER": StatusMapping("pending_submit", 0, False),
    "PENDING_ACTIVATION":    StatusMapping("pending_submit", 0, False),
    "QUEUED":                StatusMapping("submitted", 1, False),
    "WORKING":               StatusMapping("submitted", 1, False),
    "PENDING_CANCEL":        StatusMapping("cancel_requested", 2, False),
    "PENDING_REPLACE":       StatusMapping("modify_requested", 2, False),
    "FILLED":                StatusMapping("filled", 4, True),
    "CANCELED":              StatusMapping("cancelled", 4, True),
    "REPLACED":              StatusMapping("cancelled", 4, True, kind="replaced"),  # HIGH-3
    "REJECTED":              StatusMapping("rejected", 4, True),
    "EXPIRED":               StatusMapping("expired", 4, True),
}


def schwab_status_to_wire(schwab_status: str) -> StatusMapping:
    m = _STATUS_MAP.get(schwab_status)
    if m is None:
        warnings.warn(f"unknown schwab status: {schwab_status}", UserWarning)
        return StatusMapping("submitted", 1, False)
    return m


@dataclass
class FillEvent:
    exec_id: str
    price: Decimal
    quantity: Decimal
    time_iso: str
    avg_fill_price_inferred: bool = False


@dataclass
class NormalizedOrder:
    broker_order_id: str
    client_order_id: str
    status_mapping: StatusMapping
    entered_time_iso: str = ""
    fills: list[FillEvent] = field(default_factory=list)


def schwab_to_wire_order(schwab_order: dict, *, client_order_id: str) -> NormalizedOrder:
    fills: list[FillEvent] = []
    total_qty = schwab_order.get("quantity")
    market_value = schwab_order.get("marketValue")
    for activity in schwab_order.get("orderActivityCollection", []) or []:
        if activity.get("executionType") != "FILL":
            continue
        for leg in activity.get("executionLegs", []) or []:
            inferred = False
            price = leg.get("price")
            if price is None and total_qty and market_value:
                price = Decimal(str(market_value)) / Decimal(str(total_qty))
                inferred = True
            elif price is None:
                continue
            fills.append(FillEvent(
                exec_id=str(leg["legId"]),
                price=Decimal(str(price)),
                quantity=Decimal(str(leg["quantity"])),
                time_iso=leg["time"],
                avg_fill_price_inferred=inferred,
            ))
    return NormalizedOrder(
        broker_order_id=str(schwab_order["orderId"]),
        client_order_id=client_order_id,
        status_mapping=schwab_status_to_wire(schwab_order["status"]),
        entered_time_iso=schwab_order.get("enteredTime", ""),
        fills=fills,
    )
```

- [ ] **Step 4: Re-run test (expect PASS)**

```bash
cd sidecar_schwab && uv run pytest tests/test_normalize_orders.py -v
```

Expected: 14 tests PASS (11 parametrized + 4 standalone, minus duplicate parametrize entries).

- [ ] **Step 5: Commit**

```bash
git add sidecar_schwab/normalize.py sidecar_schwab/tests/test_normalize_orders.py
git commit -m "feat(schwab-sidecar): order/status normalizers (11 statuses + replaced kind + inferred fill)"
```

---

### Task C3: Flip PlaceOrder from UNIMPLEMENTED to live

**Files:**
- Modify: `sidecar_schwab/handlers.py` (PlaceOrder method, was line ~241)
- Test: `sidecar_schwab/tests/test_handlers_place_order.py` (new)

**Codex pattern:** A (`except (SchwabHttpError, OSError) as exc:` parens), F (verbatim grpc status codes).

- [ ] **Step 1: Write failing test** — create `sidecar_schwab/tests/test_handlers_place_order.py`:

```python
"""Phase 8a — PlaceOrder live path (replay cache, SIM, token refresh, REST)."""
from __future__ import annotations
import grpc
import pytest
from unittest.mock import AsyncMock, MagicMock

from sidecar_schwab.handlers import SchwabHandlers
from sidecar_schwab._generated.broker.v1 import broker_pb2


@pytest.fixture
def make_handlers():
    def _f(client=None, simulator=None, poller=None, replay=None):
        return SchwabHandlers(
            client=client or MagicMock(),
            simulator=simulator or MagicMock(),
            poller=poller or MagicMock(),
            replay_cache=replay or {},
            account_resolver=lambda gw, aid: "ACCT_HASH",
        )
    return _f


@pytest.mark.asyncio
async def test_place_order_live_returns_broker_order_id(make_handlers):
    client = MagicMock()
    client.ensure_fresh_token = AsyncMock()
    client.place_order = MagicMock(return_value={"broker_order_id": "12345"})
    h = make_handlers(client=client)
    req = broker_pb2.PlaceOrderRequest(
        gateway_label="schwab-paper", account_id="acct1",
        client_order_id="cli-1",
        order=broker_pb2.Order(order_type=broker_pb2.ORDER_TYPE_MARKET,
                               time_in_force=broker_pb2.TIF_DAY,
                               quantity="1", side=broker_pb2.SIDE_BUY, conid=1234),
    )
    rsp = await h.PlaceOrder(req, MagicMock())
    assert rsp.broker_order_id == "12345"
    client.ensure_fresh_token.assert_awaited_once()


@pytest.mark.asyncio
async def test_place_order_sim_routes_to_simulator(make_handlers):
    sim = MagicMock()
    sim.register = MagicMock(return_value="SIM-uuid7-xyz")
    h = make_handlers(simulator=sim)
    req = broker_pb2.PlaceOrderRequest(
        gateway_label="schwab-paper", account_id="acct1",
        client_order_id="SIM-test-1",
        order=broker_pb2.Order(order_type=broker_pb2.ORDER_TYPE_MARKET,
                               time_in_force=broker_pb2.TIF_DAY,
                               quantity="1", side=broker_pb2.SIDE_BUY, conid=1234),
    )
    rsp = await h.PlaceOrder(req, MagicMock())
    assert rsp.broker_order_id == "SIM-uuid7-xyz"
    sim.register.assert_called_once()


@pytest.mark.asyncio
async def test_place_order_replay_cache_returns_cached_result(make_handlers):
    client = MagicMock()
    client.ensure_fresh_token = AsyncMock()
    client.place_order = MagicMock(return_value={"broker_order_id": "55555"})
    cache: dict = {}
    h = make_handlers(client=client, replay=cache)
    req = broker_pb2.PlaceOrderRequest(
        gateway_label="schwab-paper", account_id="acct1",
        client_order_id="cli-2",
        order=broker_pb2.Order(order_type=broker_pb2.ORDER_TYPE_MARKET,
                               time_in_force=broker_pb2.TIF_DAY,
                               quantity="1", side=broker_pb2.SIDE_BUY, conid=1234),
    )
    rsp1 = await h.PlaceOrder(req, MagicMock())
    rsp2 = await h.PlaceOrder(req, MagicMock())
    assert rsp1.broker_order_id == rsp2.broker_order_id == "55555"
    client.place_order.assert_called_once()  # 2nd call hit cache


@pytest.mark.asyncio
async def test_place_order_429_aborts_resource_exhausted(make_handlers):
    from sidecar_schwab.client import SchwabHttpError
    client = MagicMock()
    client.ensure_fresh_token = AsyncMock()
    client.place_order = MagicMock(side_effect=SchwabHttpError(429, "rate limit"))
    h = make_handlers(client=client)
    ctx = MagicMock()
    ctx.abort = MagicMock(side_effect=Exception("aborted"))
    req = broker_pb2.PlaceOrderRequest(
        gateway_label="schwab-paper", account_id="acct1", client_order_id="cli-3",
        order=broker_pb2.Order(order_type=broker_pb2.ORDER_TYPE_MARKET,
                               time_in_force=broker_pb2.TIF_DAY,
                               quantity="1", side=broker_pb2.SIDE_BUY, conid=1234),
    )
    with pytest.raises(Exception, match="aborted"):
        await h.PlaceOrder(req, ctx)
    ctx.abort.assert_called_once()
    code, _ = ctx.abort.call_args[0]
    assert code == grpc.StatusCode.RESOURCE_EXHAUSTED
```

- [ ] **Step 2: Run test (expect FAIL — handler still returns UNIMPLEMENTED)**

```bash
cd sidecar_schwab && uv run pytest tests/test_handlers_place_order.py -v
```

- [ ] **Step 3: Implement PlaceOrder** — replace UNIMPLEMENTED stub in `sidecar_schwab/handlers.py:241+`:

```python
async def PlaceOrder(self, request, context):
    gw, aid = request.gateway_label, request.account_id
    coid = request.client_order_id

    # 1. SIM route.
    if coid.startswith("SIM-"):
        broker_order_id = self._simulator.register(
            gateway_label=gw, account_id=aid, client_order_id=coid, order=request.order)
        return broker_pb2.PlaceOrderResult(broker_order_id=broker_order_id)

    account_hash = self._account_resolver(gw, aid)

    # 2. Replay cache.
    cache_key = (account_hash, coid)
    if cache_key in self._replay_cache:
        return self._replay_cache[cache_key]

    # 3. Token pre-warm (HIGH-4).
    await self._client.ensure_fresh_token()

    # 4. Live REST.
    payload = self._normalize.to_schwab_order_payload(request.order)
    t0 = time.monotonic()
    try:
        result = self._client.place_order(account_hash=account_hash, payload=payload)
    except SchwabHttpError as exc:
        metrics.schwab_place_order_duration_ms.observe((time.monotonic() - t0) * 1000)
        await self._abort_for_http(context, exc)
    except (OSError, TimeoutError) as exc:
        metrics.schwab_place_order_duration_ms.observe((time.monotonic() - t0) * 1000)
        await context.abort(grpc.StatusCode.UNAVAILABLE, f"schwab transport: {exc}")

    metrics.schwab_place_order_duration_ms.observe((time.monotonic() - t0) * 1000)

    rsp = broker_pb2.PlaceOrderResult(broker_order_id=result["broker_order_id"])
    self._replay_cache[cache_key] = rsp
    self._poller.activate_fast(gateway_label=gw, account_id=aid)
    return rsp


async def _abort_for_http(self, context, exc: SchwabHttpError):
    if exc.status == 401:
        await context.abort(grpc.StatusCode.UNAUTHENTICATED, exc.body[:200])
    elif exc.status == 403:
        await context.abort(grpc.StatusCode.PERMISSION_DENIED, exc.body[:200])
    elif exc.status == 429:
        await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, exc.body[:200])
    elif 400 <= exc.status < 500:
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, exc.body[:200])
    else:
        await context.abort(grpc.StatusCode.UNAVAILABLE, exc.body[:200])
```

Add `__init__` constructor parameters: `client`, `simulator`, `poller`, `replay_cache: dict`, `account_resolver`, `normalize` modules. Wire in main.py.

- [ ] **Step 4: Re-run test (expect PASS)**

```bash
cd sidecar_schwab && uv run pytest tests/test_handlers_place_order.py -v
```

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add sidecar_schwab/handlers.py sidecar_schwab/tests/test_handlers_place_order.py
git commit -m "feat(schwab-sidecar): PlaceOrder live (SIM + replay cache + REST + error map)"
```

---

### Task C4: Flip CancelOrder + ModifyOrder (with HIGH-3 parent_order_id link)

**Files:**
- Modify: `sidecar_schwab/handlers.py` (CancelOrder, ModifyOrder stubs)
- Test: `sidecar_schwab/tests/test_handlers_cancel_modify.py` (new)

- [ ] **Step 1: Write failing test** — create `sidecar_schwab/tests/test_handlers_cancel_modify.py`:

```python
"""Phase 8a — CancelOrder + ModifyOrder live paths."""
from __future__ import annotations
import grpc
import pytest
from unittest.mock import AsyncMock, MagicMock

from sidecar_schwab.handlers import SchwabHandlers
from sidecar_schwab._generated.broker.v1 import broker_pb2


@pytest.fixture
def handlers_with_client():
    client = MagicMock()
    client.ensure_fresh_token = AsyncMock()
    sim = MagicMock()
    h = SchwabHandlers(
        client=client, simulator=sim, poller=MagicMock(), replay_cache={},
        account_resolver=lambda gw, aid: "ACCT_HASH",
    )
    return h, client, sim


@pytest.mark.asyncio
async def test_cancel_order_live_calls_rest(handlers_with_client):
    h, client, _ = handlers_with_client
    client.cancel_order = MagicMock(return_value=None)
    req = broker_pb2.CancelOrderRequest(
        gateway_label="schwab-paper", account_id="acct1",
        broker_order_id="12345", client_order_id="cli-1",
    )
    rsp = await h.CancelOrder(req, MagicMock())
    assert rsp.cancel_requested is True
    client.cancel_order.assert_called_once_with(account_hash="ACCT_HASH", order_id="12345")


@pytest.mark.asyncio
async def test_cancel_order_sim_emits_synthetic_cancelled(handlers_with_client):
    h, _, sim = handlers_with_client
    req = broker_pb2.CancelOrderRequest(
        gateway_label="schwab-paper", account_id="acct1",
        broker_order_id="SIM-uuid7-abc", client_order_id="SIM-test-1",
    )
    rsp = await h.CancelOrder(req, MagicMock())
    sim.cancel.assert_called_once()
    assert rsp.cancel_requested is True


@pytest.mark.asyncio
async def test_modify_order_returns_new_broker_order_id_with_parent_link(handlers_with_client):
    h, client, _ = handlers_with_client
    client.get_order = MagicMock(return_value={
        "orderId": 12345, "status": "WORKING",
        "orderType": "LIMIT", "duration": "DAY",
        "quantity": 100, "price": 50.0,
        "orderLegCollection": [{"instrument": {"symbol": "AAPL"}, "instruction": "BUY", "quantity": 100}],
    })
    client.replace_order = MagicMock(return_value={"broker_order_id": "99999"})
    req = broker_pb2.ModifyOrderRequest(
        gateway_label="schwab-paper", account_id="acct1",
        broker_order_id="12345", client_order_id="cli-2",
        new_quantity="200", new_price="48.5", nonce="nonce-1",
    )
    rsp = await h.ModifyOrder(req, MagicMock())
    assert rsp.new_broker_order_id == "99999"
    assert rsp.parent_broker_order_id == "12345"  # HIGH-3
```

- [ ] **Step 2: Run test (expect FAIL)**

```bash
cd sidecar_schwab && uv run pytest tests/test_handlers_cancel_modify.py -v
```

- [ ] **Step 3: Implement CancelOrder + ModifyOrder** — extend `sidecar_schwab/handlers.py`:

```python
async def CancelOrder(self, request, context):
    gw, aid = request.gateway_label, request.account_id
    if request.broker_order_id.startswith("SIM-"):
        self._simulator.cancel(client_order_id=request.client_order_id)
        return broker_pb2.CancelOrderResult(cancel_requested=True)
    account_hash = self._account_resolver(gw, aid)
    await self._client.ensure_fresh_token()
    t0 = time.monotonic()
    try:
        self._client.cancel_order(account_hash=account_hash, order_id=request.broker_order_id)
    except SchwabHttpError as exc:
        metrics.schwab_cancel_order_duration_ms.observe((time.monotonic() - t0) * 1000)
        await self._abort_for_http(context, exc)
    except (OSError, TimeoutError) as exc:
        metrics.schwab_cancel_order_duration_ms.observe((time.monotonic() - t0) * 1000)
        await context.abort(grpc.StatusCode.UNAVAILABLE, f"schwab transport: {exc}")
    metrics.schwab_cancel_order_duration_ms.observe((time.monotonic() - t0) * 1000)
    return broker_pb2.CancelOrderResult(cancel_requested=True)


async def ModifyOrder(self, request, context):
    gw, aid = request.gateway_label, request.account_id
    if request.broker_order_id.startswith("SIM-"):
        new_id = self._simulator.modify(
            client_order_id=request.client_order_id,
            new_quantity=request.new_quantity, new_price=request.new_price)
        return broker_pb2.ModifyOrderResult(
            new_broker_order_id=new_id,
            parent_broker_order_id=request.broker_order_id,
        )
    account_hash = self._account_resolver(gw, aid)
    await self._client.ensure_fresh_token()
    t0 = time.monotonic()
    try:
        current = self._client.get_order(account_hash, request.broker_order_id)
        replacement = self._normalize.merge_modify(
            current=current,
            new_quantity=request.new_quantity, new_price=request.new_price,
            new_tif=request.new_time_in_force,
        )
        result = self._client.replace_order(
            account_hash=account_hash,
            order_id=request.broker_order_id,
            payload=replacement,
        )
    except SchwabHttpError as exc:
        metrics.schwab_modify_order_duration_ms.observe((time.monotonic() - t0) * 1000)
        await self._abort_for_http(context, exc)
    except (OSError, TimeoutError) as exc:
        metrics.schwab_modify_order_duration_ms.observe((time.monotonic() - t0) * 1000)
        await context.abort(grpc.StatusCode.UNAVAILABLE, f"schwab transport: {exc}")
    metrics.schwab_modify_order_duration_ms.observe((time.monotonic() - t0) * 1000)

    return broker_pb2.ModifyOrderResult(
        new_broker_order_id=result["broker_order_id"],
        parent_broker_order_id=request.broker_order_id,  # HIGH-3 link
    )
```

Note: proto must include `parent_broker_order_id` field on `ModifyOrderResult`. Add to `proto/broker/v1/broker.proto` if missing; covered by Task A1 if you add it then.

- [ ] **Step 4: Re-run test (expect PASS)**

```bash
cd sidecar_schwab && uv run pytest tests/test_handlers_cancel_modify.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add sidecar_schwab/handlers.py sidecar_schwab/tests/test_handlers_cancel_modify.py
git commit -m "feat(schwab-sidecar): CancelOrder + ModifyOrder live (with parent_order_id link, HIGH-3)"
```

---

### Task C5: Flip OrderEvent + SearchContracts + GetOrders

**Files:**
- Modify: `sidecar_schwab/handlers.py` (OrderEvent, SearchContracts, GetOrders)
- Test: `sidecar_schwab/tests/test_handlers_stream_search.py` (new)

**Codex pattern:** B (cancel+gather supervisor), C (per-callback isolation in fan-out).

- [ ] **Step 1: Write failing test** — create `sidecar_schwab/tests/test_handlers_stream_search.py`:

```python
"""Phase 8a — OrderEvent server-streaming + SearchContracts + GetOrders."""
from __future__ import annotations
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from sidecar_schwab.handlers import SchwabHandlers
from sidecar_schwab._generated.broker.v1 import broker_pb2


@pytest.mark.asyncio
async def test_order_event_subscribes_and_yields_events():
    fan_out = MagicMock()
    queue: asyncio.Queue = asyncio.Queue()
    fan_out.subscribe = MagicMock(return_value=queue)
    fan_out.unsubscribe = MagicMock()

    poller = MagicMock()
    poller.fan_out_for = MagicMock(return_value=fan_out)

    h = SchwabHandlers(
        client=MagicMock(), simulator=MagicMock(), poller=poller, replay_cache={},
        account_resolver=lambda gw, aid: "ACCT_HASH",
    )
    req = broker_pb2.OrderEventRequest(gateway_label="schwab-paper", account_id="acct1")
    ctx = MagicMock(); ctx.is_active = MagicMock(return_value=True)

    ev = broker_pb2.OrderEventMessage(broker_order_id="12345", client_order_id="cli-1",
                                       kind="status", status="submitted")
    await queue.put(ev)
    await queue.put(None)  # sentinel

    yielded = []
    async for msg in h.OrderEvent(req, ctx):
        yielded.append(msg)
    assert yielded[0].broker_order_id == "12345"


@pytest.mark.asyncio
async def test_search_contracts_caches_5min():
    client = MagicMock()
    client.search_instruments = MagicMock(return_value=[
        {"symbol": "AAPL", "description": "Apple Inc.", "cusip": "037833100"},
    ])
    client.ensure_fresh_token = AsyncMock()
    h = SchwabHandlers(
        client=client, simulator=MagicMock(), poller=MagicMock(), replay_cache={},
        account_resolver=lambda gw, aid: "ACCT_HASH",
    )
    req = broker_pb2.SearchContractsRequest(query="AAPL", limit=10)
    rsp1 = await h.SearchContracts(req, MagicMock())
    rsp2 = await h.SearchContracts(req, MagicMock())
    assert len(rsp1.matches) == 1
    client.search_instruments.assert_called_once()  # cached on 2nd call


@pytest.mark.asyncio
async def test_get_orders_with_from_to_filter():
    client = MagicMock()
    client.ensure_fresh_token = AsyncMock()
    client.get_orders_since = MagicMock(return_value=[
        {"orderId": 12345, "status": "FILLED", "enteredTime": "2026-05-06T14:30:00Z"},
    ])
    h = SchwabHandlers(
        client=client, simulator=MagicMock(), poller=MagicMock(), replay_cache={},
        account_resolver=lambda gw, aid: "ACCT_HASH",
    )
    req = broker_pb2.GetOrdersRequest(
        gateway_label="schwab-paper", account_id="acct1",
        from_ts="2026-05-06T00:00:00Z", to_ts="2026-05-06T23:59:59Z",
    )
    rsp = await h.GetOrders(req, MagicMock())
    assert len(rsp.orders) == 1
    client.get_orders_since.assert_called_once()
```

- [ ] **Step 2: Run test (expect FAIL)**

```bash
cd sidecar_schwab && uv run pytest tests/test_handlers_stream_search.py -v
```

- [ ] **Step 3: Implement** — extend `sidecar_schwab/handlers.py`:

```python
import time

_SEARCH_CACHE_TTL_S = 300
_SEARCH_CACHE_MAX_ENTRIES = 1000


class SchwabHandlers:
    def __init__(self, *, client, simulator, poller, replay_cache, account_resolver,
                 normalize=None) -> None:
        self._client = client
        self._simulator = simulator
        self._poller = poller
        self._replay_cache = replay_cache
        self._account_resolver = account_resolver
        self._normalize = normalize
        self._search_cache: dict[tuple[str, int], tuple[list, float]] = {}

    async def OrderEvent(self, request, context):
        gw, aid = request.gateway_label, request.account_id
        fan_out = self._poller.fan_out_for(gateway_label=gw, account_id=aid)
        queue = fan_out.subscribe()
        try:
            while context.is_active():
                ev = await queue.get()
                if ev is None:
                    break
                yield ev
        finally:
            fan_out.unsubscribe(queue)

    async def SearchContracts(self, request, context):
        key = (request.query.upper(), request.limit or 10)
        now = time.monotonic()
        cached = self._search_cache.get(key)
        if cached is not None and now - cached[1] < _SEARCH_CACHE_TTL_S:
            return broker_pb2.SearchContractsResponse(matches=cached[0])

        await self._client.ensure_fresh_token()
        try:
            results = self._client.search_instruments(query=request.query, limit=request.limit or 10)
        except SchwabHttpError as exc:
            await self._abort_for_http(context, exc)
        except (OSError, TimeoutError) as exc:
            await context.abort(grpc.StatusCode.UNAVAILABLE, f"schwab transport: {exc}")

        matches = [broker_pb2.ContractMatch(
            symbol=r["symbol"], description=r.get("description", ""),
            cusip=r.get("cusip", ""),
        ) for r in results]
        if len(self._search_cache) >= _SEARCH_CACHE_MAX_ENTRIES:
            oldest = min(self._search_cache, key=lambda k: self._search_cache[k][1])
            del self._search_cache[oldest]
        self._search_cache[key] = (matches, now)
        return broker_pb2.SearchContractsResponse(matches=matches)

    async def GetOrders(self, request, context):
        account_hash = self._account_resolver(request.gateway_label, request.account_id)
        await self._client.ensure_fresh_token()
        try:
            schwab_orders = self._client.get_orders_since(
                account_hash, since_iso=request.from_ts or "1970-01-01T00:00:00Z")
        except SchwabHttpError as exc:
            await self._abort_for_http(context, exc)
        except (OSError, TimeoutError) as exc:
            await context.abort(grpc.StatusCode.UNAVAILABLE, f"schwab transport: {exc}")

        if request.to_ts:
            schwab_orders = [o for o in schwab_orders
                             if o.get("enteredTime", "") <= request.to_ts]
        wire_orders = [self._normalize.schwab_to_proto_order(o) for o in schwab_orders]
        return broker_pb2.GetOrdersResponse(orders=wire_orders)
```

- [ ] **Step 4: Re-run test (expect PASS)**

```bash
cd sidecar_schwab && uv run pytest tests/test_handlers_stream_search.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add sidecar_schwab/handlers.py sidecar_schwab/tests/test_handlers_stream_search.py
git commit -m "feat(schwab-sidecar): OrderEvent stream + SearchContracts (5m cache) + GetOrders (from/to filter)"
```

---

## Chunk D — Order poller + state cache + simulator + lifespan wiring

**Codex patterns most likely to bite:** B (cancel+gather supervisor over poller tasks), C (per-callback isolation in fan-out), D (bounded queues — fan-out subscribers cap), E (lazy-singleton init/cleanup).

### Task D1: Redis-backed order_state_cache (CRIT-2 sidecar half)

**Files:**
- Create: `sidecar_schwab/order_state_cache.py`
- Test: `sidecar_schwab/tests/test_order_state_cache.py` (new)

- [ ] **Step 1: Write failing test** — create `sidecar_schwab/tests/test_order_state_cache.py`:

```python
"""Phase 8a CRIT-2: Redis-backed order state cache (write-through, restart-safe)."""
from __future__ import annotations
import json
import pytest
from unittest.mock import AsyncMock

from sidecar_schwab.order_state_cache import OrderStateCache, OrderState


@pytest.mark.asyncio
async def test_put_writes_to_redis_and_in_memory():
    redis = AsyncMock()
    cache = OrderStateCache(redis=redis, gateway_label="schwab-paper", account_id="acct1")
    state = OrderState(client_order_id="cli-1", broker_order_id="12345",
                       schwab_status="WORKING", entered_time_iso="2026-05-06T14:30Z")
    await cache.put(state)
    redis.hset.assert_awaited_once()
    redis.expire.assert_awaited_once_with("schwab:order_state:schwab-paper:acct1", 7 * 24 * 3600)
    got = await cache.get("cli-1")
    assert got.schwab_status == "WORKING"


@pytest.mark.asyncio
async def test_get_falls_through_to_redis_on_miss():
    redis = AsyncMock()
    redis.hget.return_value = json.dumps({
        "client_order_id": "cli-1", "broker_order_id": "12345",
        "schwab_status": "FILLED", "entered_time_iso": "2026-05-06T14:30Z",
    }).encode()
    cache = OrderStateCache(redis=redis, gateway_label="schwab-paper", account_id="acct1")
    got = await cache.get("cli-1")
    assert got.schwab_status == "FILLED"


@pytest.mark.asyncio
async def test_hydrate_from_redis_loads_all_keys():
    redis = AsyncMock()
    redis.hgetall.return_value = {
        b"cli-1": json.dumps({"client_order_id": "cli-1", "broker_order_id": "12345",
                              "schwab_status": "WORKING", "entered_time_iso": ""}).encode(),
        b"cli-2": json.dumps({"client_order_id": "cli-2", "broker_order_id": "67890",
                              "schwab_status": "QUEUED", "entered_time_iso": ""}).encode(),
    }
    cache = OrderStateCache(redis=redis, gateway_label="schwab-paper", account_id="acct1")
    await cache.hydrate()
    s1 = await cache.get("cli-1")
    s2 = await cache.get("cli-2")
    redis.hgetall.assert_awaited_once()
    assert s1.schwab_status == "WORKING"
    assert s2.schwab_status == "QUEUED"
```

- [ ] **Step 2: Run test (expect FAIL)**

```bash
cd sidecar_schwab && uv run pytest tests/test_order_state_cache.py -v
```

- [ ] **Step 3: Implement** — create `sidecar_schwab/order_state_cache.py`:

```python
"""Phase 8a CRIT-2: Redis-backed sidecar order state cache.

Survives sidecar restart; first poll after restart hydrates instead of
re-emitting `submitted` for every in-flight order.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis

_TTL_SECONDS = 7 * 24 * 3600


@dataclass
class OrderState:
    client_order_id: str
    broker_order_id: str
    schwab_status: str
    entered_time_iso: str = ""
    last_exec_id: str = ""


class OrderStateCache:
    def __init__(self, *, redis: "Redis", gateway_label: str, account_id: str) -> None:
        self._redis = redis
        self._key = f"schwab:order_state:{gateway_label}:{account_id}"
        self._mem: dict[str, OrderState] = {}

    async def hydrate(self) -> None:
        raw = await self._redis.hgetall(self._key)
        for field, value in raw.items():
            field_str = field.decode() if isinstance(field, bytes) else field
            data = json.loads(value)
            self._mem[field_str] = OrderState(**data)

    async def get(self, client_order_id: str) -> OrderState | None:
        if client_order_id in self._mem:
            return self._mem[client_order_id]
        raw = await self._redis.hget(self._key, client_order_id)
        if raw is None:
            return None
        data = json.loads(raw)
        state = OrderState(**data)
        self._mem[client_order_id] = state
        return state

    async def put(self, state: OrderState) -> None:
        self._mem[state.client_order_id] = state
        await self._redis.hset(self._key, state.client_order_id, json.dumps(asdict(state)))
        await self._redis.expire(self._key, _TTL_SECONDS)

    async def invalidate_all(self) -> None:
        self._mem.clear()
        await self._redis.delete(self._key)

    def known_client_order_ids(self) -> set[str]:
        return set(self._mem.keys())
```

- [ ] **Step 4: Re-run test (expect PASS)**

```bash
cd sidecar_schwab && uv run pytest tests/test_order_state_cache.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add sidecar_schwab/order_state_cache.py sidecar_schwab/tests/test_order_state_cache.py
git commit -m "feat(schwab-sidecar): Redis-backed order state cache (CRIT-2)"
```

---

### Task D2: Adaptive OrderPoller with cadence + 429 backoff + hash rotation

**Files:**
- Create: `sidecar_schwab/order_poller.py`
- Test: `sidecar_schwab/tests/test_order_poller.py` (new)

**Codex patterns:** B (cancel+gather), F (verbatim metric labels), A (parens).

- [ ] **Step 1: Write failing test** — create `sidecar_schwab/tests/test_order_poller.py`:

```python
"""Phase 8a — adaptive order poller (2s active / 30s idle), 429 backoff, hash rotation."""
from __future__ import annotations
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from sidecar_schwab.order_poller import OrderPoller
from sidecar_schwab.order_state_cache import OrderState


@pytest.fixture
def fake_state_cache():
    cache = MagicMock()
    cache.hydrate = AsyncMock()
    cache.get = AsyncMock(return_value=None)
    cache.put = AsyncMock()
    cache.invalidate_all = AsyncMock()
    cache.known_client_order_ids = MagicMock(return_value=set())
    return cache


@pytest.mark.asyncio
async def test_diff_emits_submitted_for_new_client_order_id(fake_state_cache):
    client = MagicMock()
    client.get_orders_since = MagicMock(return_value=[
        {"orderId": 12345, "clientOrderId": "cli-1", "status": "QUEUED",
         "enteredTime": "2026-05-06T14:30:00Z"},
    ])
    poller = OrderPoller(
        client=client, state_cache=fake_state_cache,
        gateway_label="schwab-paper", account_id="acct1",
        account_hash_resolver=lambda: "ACCT_HASH",
    )
    events = await poller._poll_once()
    assert any(e.status == "submitted" for e in events)


@pytest.mark.parametrize("attempt,expected_delay", [
    (0, 2.0),
    (1, 4.0),
    (2, 8.0),
    (3, 16.0),
    (4, 30.0),
    (5, 30.0),
])
def test_backoff_doubles_with_cap(attempt, expected_delay):
    from sidecar_schwab.order_poller import compute_backoff
    assert compute_backoff(attempt) == expected_delay


@pytest.mark.asyncio
async def test_cadence_switches_on_in_flight(fake_state_cache):
    poller = OrderPoller(
        client=MagicMock(), state_cache=fake_state_cache,
        gateway_label="schwab-paper", account_id="acct1",
        account_hash_resolver=lambda: "ACCT_HASH",
    )
    poller.activate_fast()
    assert poller.current_tick_seconds() == 2.0
    poller._mark_no_in_flight()
    assert poller.current_tick_seconds() == 30.0


@pytest.mark.asyncio
async def test_hash_rotation_invalidates_state(fake_state_cache):
    poller = OrderPoller(
        client=MagicMock(), state_cache=fake_state_cache,
        gateway_label="schwab-paper", account_id="acct1",
        account_hash_resolver=lambda: "ACCT_HASH_NEW",
    )
    await poller.handle_account_hash_rotation()
    fake_state_cache.invalidate_all.assert_awaited_once()
```

- [ ] **Step 2: Run test (expect FAIL)**

```bash
cd sidecar_schwab && uv run pytest tests/test_order_poller.py -v
```

- [ ] **Step 3: Implement** — create `sidecar_schwab/order_poller.py`:

```python
"""Phase 8a — adaptive order poller per (gateway_label, account_id) — CRIT-1 supervisor key."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

import structlog

from sidecar_schwab import metrics
from sidecar_schwab.client import SchwabHttpError
from sidecar_schwab.normalize import schwab_status_to_wire, schwab_to_wire_order
from sidecar_schwab.order_state_cache import OrderState, OrderStateCache

logger = structlog.get_logger(__name__)

_FAST_TICK_S = 2.0
_IDLE_TICK_S = 30.0
_MAX_BACKOFF_S = 30.0
_OVERLAP_S = 5
_MAX_QUEUE_SIZE = 1000  # bounded queue per CRIT-D pattern


def compute_backoff(attempt: int) -> float:
    return min(2.0 * (2 ** attempt), _MAX_BACKOFF_S)


@dataclass
class WireEvent:
    broker_order_id: str
    client_order_id: str
    kind: str
    status: str
    exec_id: str = ""


class _FanOut:
    """Per-callback isolated fan-out (Codex pattern C). Bounded queues (D)."""
    def __init__(self) -> None:
        self._subs: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)
        self._subs.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subs.remove(q)
        except ValueError:
            pass

    async def publish(self, ev: WireEvent) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                # Drop the slow consumer per Codex pattern D bounded-queue rule.
                self.unsubscribe(q)
                metrics.schwab_fanout_subscriber_dropped_total.inc()


class OrderPoller:
    def __init__(self, *, client, state_cache: OrderStateCache,
                 gateway_label: str, account_id: str,
                 account_hash_resolver: Callable[[], str]) -> None:
        self._client = client
        self._state = state_cache
        self._gw = gateway_label
        self._aid = account_id
        self._hash_resolver = account_hash_resolver
        self._tick = _IDLE_TICK_S
        self._backoff_attempt = 0
        self._fan_out = _FanOut()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._last_poll_iso = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self._in_flight: set[str] = set()

    def fan_out(self) -> _FanOut:
        return self._fan_out

    def activate_fast(self) -> None:
        if self._tick != _FAST_TICK_S:
            metrics.schwab_order_poller_cadence_changed_total.labels(
                gateway_label=self._gw, account_id=self._aid,
                **{"from": str(self._tick), "to": str(_FAST_TICK_S)}).inc()
            self._tick = _FAST_TICK_S

    def _mark_no_in_flight(self) -> None:
        if not self._in_flight and self._tick != _IDLE_TICK_S:
            metrics.schwab_order_poller_cadence_changed_total.labels(
                gateway_label=self._gw, account_id=self._aid,
                **{"from": str(self._tick), "to": str(_IDLE_TICK_S)}).inc()
            self._tick = _IDLE_TICK_S

    def current_tick_seconds(self) -> float:
        return self._tick

    async def start(self) -> None:
        await self._state.hydrate()
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name=f"poller:{self._gw}:{self._aid}")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)  # Codex B
            self._task = None

    async def handle_account_hash_rotation(self) -> None:
        await self._state.invalidate_all()
        self._in_flight.clear()
        self._last_poll_iso = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                events = await self._poll_once()
                for ev in events:
                    await self._fan_out.publish(ev)
                self._backoff_attempt = 0
                self._mark_no_in_flight()
            except SchwabHttpError as exc:
                if exc.status == 429:
                    self._backoff_attempt += 1
                    metrics.schwab_order_poller_iterations_total.labels(
                        gateway_label=self._gw, account_id=self._aid,
                        cadence=f"backoff_{self._backoff_attempt}").inc()
                    await asyncio.sleep(compute_backoff(self._backoff_attempt))
                    continue
                logger.error("schwab_poller_http_error", status=exc.status, body=exc.body[:200])
            except (OSError, TimeoutError) as exc:
                logger.error("schwab_poller_transport_error", error=str(exc))
            metrics.schwab_order_poller_iterations_total.labels(
                gateway_label=self._gw, account_id=self._aid,
                cadence=f"{self._tick:g}s").inc()
            await asyncio.sleep(self._tick)

    async def _poll_once(self) -> list[WireEvent]:
        account_hash = self._hash_resolver()
        since = self._last_poll_iso  # 5s overlap baked into next-call window math
        rows = self._client.get_orders_since(account_hash, since_iso=since)
        self._last_poll_iso = datetime.now(timezone.utc).isoformat()

        events: list[WireEvent] = []
        for raw in rows:
            coid = raw.get("clientOrderId") or ""
            if not coid:
                continue
            normalized = schwab_to_wire_order(raw, client_order_id=coid)
            prev = await self._state.get(coid)
            new_state = OrderState(
                client_order_id=coid,
                broker_order_id=normalized.broker_order_id,
                schwab_status=raw["status"],
                entered_time_iso=normalized.entered_time_iso,
                last_exec_id=normalized.fills[-1].exec_id if normalized.fills else "",
            )
            if prev is None:
                events.append(WireEvent(
                    broker_order_id=normalized.broker_order_id,
                    client_order_id=coid, kind="status", status="submitted"))
            elif prev.schwab_status != new_state.schwab_status:
                mapping = schwab_status_to_wire(new_state.schwab_status)
                events.append(WireEvent(
                    broker_order_id=normalized.broker_order_id,
                    client_order_id=coid, kind=mapping.kind, status=mapping.wire_status))
            for fill in normalized.fills:
                if fill.exec_id and fill.exec_id != prev.last_exec_id if prev else True:
                    events.append(WireEvent(
                        broker_order_id=normalized.broker_order_id,
                        client_order_id=coid, kind="fill",
                        status="submitted", exec_id=fill.exec_id))
            await self._state.put(new_state)
            mapping = schwab_status_to_wire(new_state.schwab_status)
            if mapping.terminal:
                self._in_flight.discard(coid)
            else:
                self._in_flight.add(coid)
                self.activate_fast()
            metrics.schwab_order_event_emitted_total.labels(kind=mapping.kind).inc()
        return events
```

- [ ] **Step 4: Re-run test (expect PASS)**

```bash
cd sidecar_schwab && uv run pytest tests/test_order_poller.py -v
```

Expected: 9 PASS (1 standard + 6 parametrized + 2 cadence/rotation).

- [ ] **Step 5: Commit**

```bash
git add sidecar_schwab/order_poller.py sidecar_schwab/tests/test_order_poller.py
git commit -m "feat(schwab-sidecar): adaptive OrderPoller (2s/30s, 429 backoff, hash rotation)"
```

---

### Task D3: SimRegistry — SIM-prefix synthetic event emitter

**Files:**
- Create: `sidecar_schwab/simulator.py`
- Test: `sidecar_schwab/tests/test_simulator.py` (new)

- [ ] **Step 1: Write failing test** — create `sidecar_schwab/tests/test_simulator.py`:

```python
"""Phase 8a — SIM mode echo (5b.1 lesson)."""
from __future__ import annotations
import asyncio
import pytest
from freezegun import freeze_time
from unittest.mock import MagicMock

from sidecar_schwab.simulator import SimRegistry


@pytest.mark.asyncio
async def test_register_returns_sim_id_and_emits_submitted_after_50ms():
    fan_out = MagicMock()
    fan_out.publish = MagicMock()
    sim = SimRegistry(fan_out=fan_out)
    sim_id = sim.register(gateway_label="schwab-paper", account_id="acct1",
                          client_order_id="SIM-test-1", order=MagicMock())
    assert sim_id.startswith("SIM-")
    await asyncio.sleep(0.07)
    fan_out.publish.assert_called_once()


@pytest.mark.asyncio
async def test_cancel_emits_cancelled_after_50ms():
    fan_out = MagicMock()
    fan_out.publish = MagicMock()
    sim = SimRegistry(fan_out=fan_out)
    sim.register(gateway_label="schwab-paper", account_id="acct1",
                 client_order_id="SIM-test-1", order=MagicMock())
    await asyncio.sleep(0.07)
    sim.cancel(client_order_id="SIM-test-1")
    await asyncio.sleep(0.07)
    assert fan_out.publish.call_count == 2  # submitted + cancelled


@pytest.mark.asyncio
async def test_modify_emits_modified_then_submitted():
    fan_out = MagicMock()
    fan_out.publish = MagicMock()
    sim = SimRegistry(fan_out=fan_out)
    sim.register(gateway_label="schwab-paper", account_id="acct1",
                 client_order_id="SIM-test-1", order=MagicMock())
    await asyncio.sleep(0.07)
    new_id = sim.modify(client_order_id="SIM-test-1", new_quantity="200", new_price="50")
    await asyncio.sleep(0.07)
    assert new_id.startswith("SIM-")
    assert fan_out.publish.call_count == 3  # submitted + modified + submitted (replacement)


@pytest.mark.asyncio
async def test_gc_drops_entries_after_1h():
    fan_out = MagicMock()
    sim = SimRegistry(fan_out=fan_out)
    with freeze_time("2026-05-06 10:00:00") as frozen:
        sim.register(gateway_label="schwab-paper", account_id="acct1",
                     client_order_id="SIM-old", order=MagicMock())
        frozen.tick(delta=3601)
        sim.gc()
    assert "SIM-old" not in sim._entries
```

- [ ] **Step 2: Run test (expect FAIL)**

```bash
cd sidecar_schwab && uv run pytest tests/test_simulator.py -v
```

- [ ] **Step 3: Implement** — create `sidecar_schwab/simulator.py`:

```python
"""Phase 8a — SIM mode echo for Schwab sidecar (mirrors IBKR 5b.1 pattern)."""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass

_SIM_TTL_SECONDS = 3600
_SYNTHETIC_DELAY_S = 0.05


def _sim_id() -> str:
    return f"SIM-{uuid.uuid4()}"


@dataclass
class _SimEntry:
    client_order_id: str
    broker_order_id: str
    created_at: float


class SimRegistry:
    def __init__(self, *, fan_out) -> None:
        self._fan_out = fan_out
        self._entries: dict[str, _SimEntry] = {}

    def register(self, *, gateway_label: str, account_id: str,
                 client_order_id: str, order) -> str:
        broker_order_id = _sim_id()
        self._entries[client_order_id] = _SimEntry(
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
            created_at=time.monotonic(),
        )
        asyncio.get_event_loop().call_later(
            _SYNTHETIC_DELAY_S,
            lambda: self._emit_status(client_order_id, broker_order_id, "submitted"),
        )
        return broker_order_id

    def cancel(self, *, client_order_id: str) -> None:
        entry = self._entries.get(client_order_id)
        if entry is None:
            return
        asyncio.get_event_loop().call_later(
            _SYNTHETIC_DELAY_S,
            lambda: self._emit_status(client_order_id, entry.broker_order_id, "cancelled"),
        )

    def modify(self, *, client_order_id: str, new_quantity: str, new_price: str) -> str:
        entry = self._entries.get(client_order_id)
        if entry is None:
            return ""
        new_broker_order_id = _sim_id()
        # Emit modified for old, submitted for replacement.
        asyncio.get_event_loop().call_later(
            _SYNTHETIC_DELAY_S,
            lambda: self._emit_status(client_order_id, entry.broker_order_id, "modified"),
        )
        asyncio.get_event_loop().call_later(
            _SYNTHETIC_DELAY_S,
            lambda: self._emit_status(client_order_id, new_broker_order_id, "submitted"),
        )
        self._entries[client_order_id] = _SimEntry(
            client_order_id=client_order_id,
            broker_order_id=new_broker_order_id,
            created_at=time.monotonic(),
        )
        return new_broker_order_id

    def _emit_status(self, client_order_id: str, broker_order_id: str, status: str) -> None:
        from sidecar_schwab.order_poller import WireEvent
        ev = WireEvent(broker_order_id=broker_order_id, client_order_id=client_order_id,
                       kind="status", status=status)
        self._fan_out.publish(ev)

    def gc(self) -> None:
        cutoff = time.monotonic() - _SIM_TTL_SECONDS
        stale = [k for k, e in self._entries.items() if e.created_at < cutoff]
        for k in stale:
            del self._entries[k]
```

- [ ] **Step 4: Re-run test (expect PASS)**

```bash
cd sidecar_schwab && uv run pytest tests/test_simulator.py -v
```

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add sidecar_schwab/simulator.py sidecar_schwab/tests/test_simulator.py
git commit -m "feat(schwab-sidecar): SIM-mode echo (place/cancel/modify synthetic events)"
```

---

### Task D4: Wire poller + state cache + simulator into sidecar lifespan

**Files:**
- Modify: `sidecar_schwab/main.py` (lifespan + per-account semaphore)
- Test: `sidecar_schwab/tests/test_lifespan_wiring.py` (new)

**Codex pattern:** E (lazy-singleton init/cleanup), B (cancel+gather supervisor over per-account pollers).

- [ ] **Step 1: Write failing test** — create `sidecar_schwab/tests/test_lifespan_wiring.py`:

```python
"""Phase 8a — sidecar lifespan starts pollers per (gateway_label, account_id) and tears them down."""
from __future__ import annotations
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from sidecar_schwab.main import PollerSupervisor


@pytest.mark.asyncio
async def test_supervisor_starts_one_poller_per_account():
    cfg_accounts = [
        {"gateway_label": "schwab-paper", "account_id": "acct1", "account_hash": "H1"},
        {"gateway_label": "schwab-paper", "account_id": "acct2", "account_hash": "H2"},
    ]
    sup = PollerSupervisor(client=MagicMock(), redis=AsyncMock(), accounts=cfg_accounts)
    await sup.start()
    assert len(sup._pollers) == 2
    await sup.stop()
    assert len(sup._pollers) == 0


@pytest.mark.asyncio
async def test_supervisor_cancel_gather_cleans_pending(monkeypatch):
    sup = PollerSupervisor(client=MagicMock(), redis=AsyncMock(), accounts=[])
    sup._pollers = {("gw", "aid"): MagicMock()}
    sup._pollers[("gw", "aid")].stop = AsyncMock()
    await sup.stop()
    sup._pollers[("gw", "aid")].stop.assert_awaited()
```

- [ ] **Step 2: Run test (expect FAIL)**

```bash
cd sidecar_schwab && uv run pytest tests/test_lifespan_wiring.py -v
```

- [ ] **Step 3: Implement supervisor** — extend `sidecar_schwab/main.py`:

```python
import asyncio

from sidecar_schwab.order_poller import OrderPoller
from sidecar_schwab.order_state_cache import OrderStateCache
from sidecar_schwab.simulator import SimRegistry


class PollerSupervisor:
    def __init__(self, *, client, redis, accounts: list[dict]) -> None:
        self._client = client
        self._redis = redis
        self._accounts = accounts
        self._pollers: dict[tuple[str, str], OrderPoller] = {}
        self._sims: dict[tuple[str, str], SimRegistry] = {}
        # Per-account semaphore for rate-limit defense (max 4 concurrent).
        self._semaphores: dict[tuple[str, str], asyncio.Semaphore] = {}

    async def start(self) -> None:
        for acct in self._accounts:
            gw = acct["gateway_label"]
            aid = acct["account_id"]
            account_hash = acct["account_hash"]
            key = (gw, aid)
            cache = OrderStateCache(redis=self._redis, gateway_label=gw, account_id=aid)
            poller = OrderPoller(
                client=self._client, state_cache=cache,
                gateway_label=gw, account_id=aid,
                account_hash_resolver=lambda h=account_hash: h,
            )
            sim = SimRegistry(fan_out=poller.fan_out())
            self._pollers[key] = poller
            self._sims[key] = sim
            self._semaphores[key] = asyncio.Semaphore(4)
            await poller.start()

    async def stop(self) -> None:
        # Codex B: cancel + gather all in parallel.
        tasks = [p.stop() for p in self._pollers.values()]
        await asyncio.gather(*tasks, return_exceptions=True)
        self._pollers.clear()
        self._sims.clear()
        self._semaphores.clear()

    def get_poller(self, gw: str, aid: str) -> OrderPoller:
        return self._pollers[(gw, aid)]

    def get_simulator(self, gw: str, aid: str) -> SimRegistry:
        return self._sims[(gw, aid)]
```

In the existing `lifespan` (or equivalent FastAPI/grpc lifespan):

```python
@asynccontextmanager
async def lifespan(app):
    redis = await create_redis_pool()
    client = SchwabClient(...)
    accounts = await load_configured_accounts()  # populated at Configure time
    supervisor = PollerSupervisor(client=client, redis=redis, accounts=accounts)
    await supervisor.start()
    app.state.supervisor = supervisor
    try:
        yield
    finally:
        await supervisor.stop()
        await redis.close()
```

- [ ] **Step 4: Re-run test (expect PASS)**

```bash
cd sidecar_schwab && uv run pytest tests/test_lifespan_wiring.py -v
```

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add sidecar_schwab/main.py sidecar_schwab/tests/test_lifespan_wiring.py
git commit -m "feat(schwab-sidecar): PollerSupervisor lifespan wiring + per-account semaphore"
```

---

## Chunk E — E2E mock + C0 empirical script + nightly/weekly real-Schwab CI

**Codex patterns most likely to bite:** A (parens), B (cancel+gather on test fixtures), F (verbatim metric labels in assertions).

### Task E1: FakeSchwabServicer extension for E2E mocking

**Files:**
- Modify: `backend/tests/fixtures/fake_schwab.py` (or create if absent)
- Test: `backend/tests/integration/test_fake_schwab_servicer.py` (new — sanity smoke for the mock)

- [ ] **Step 1: Pre-flight — confirm fixture path**

```bash
find backend/tests -name "fake_schwab*" -o -name "fake_*.py" | head
```

- [ ] **Step 2: Write smoke test** — create `backend/tests/integration/test_fake_schwab_servicer.py`:

```python
"""Sanity smoke for the extended FakeSchwabServicer."""
from __future__ import annotations
import pytest
from backend.tests.fixtures.fake_schwab import FakeSchwabServicer


@pytest.mark.asyncio
async def test_place_order_returns_assigned_id():
    s = FakeSchwabServicer()
    rsp = await s.PlaceOrder({"client_order_id": "cli-1", "account_id": "acct1"}, None)
    assert rsp["broker_order_id"].startswith("FAKE-")


@pytest.mark.asyncio
async def test_cancel_order_emits_cancelled_event():
    s = FakeSchwabServicer()
    rsp = await s.PlaceOrder({"client_order_id": "cli-1", "account_id": "acct1"}, None)
    bid = rsp["broker_order_id"]
    await s.CancelOrder({"broker_order_id": bid, "account_id": "acct1"}, None)
    events = []
    async for e in s.OrderEvent({"account_id": "acct1"}, None):
        events.append(e)
        if len(events) >= 2:
            break
    assert any(e["status"] == "cancelled" for e in events)


@pytest.mark.asyncio
async def test_modify_order_emits_replaced_with_parent_link():
    s = FakeSchwabServicer()
    rsp1 = await s.PlaceOrder({"client_order_id": "cli-1", "account_id": "acct1"}, None)
    bid = rsp1["broker_order_id"]
    rsp2 = await s.ModifyOrder({"broker_order_id": bid, "account_id": "acct1",
                                "new_quantity": "200"}, None)
    assert rsp2["new_broker_order_id"] != bid
    assert rsp2["parent_broker_order_id"] == bid
```

- [ ] **Step 3: Run test (expect FAIL)**

```bash
cd backend && uv run pytest tests/integration/test_fake_schwab_servicer.py -v
```

- [ ] **Step 4: Implement FakeSchwabServicer** — create/extend `backend/tests/fixtures/fake_schwab.py`:

```python
"""Phase 8a — Fake Schwab gRPC servicer for backend E2E tests (no real Schwab REST)."""
from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict


class FakeSchwabServicer:
    def __init__(self) -> None:
        self._orders: dict[str, dict] = {}
        self._events: dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)

    async def PlaceOrder(self, request, _ctx):
        bid = f"FAKE-{uuid.uuid4()}"
        coid = request["client_order_id"] if isinstance(request, dict) else request.client_order_id
        aid = request["account_id"] if isinstance(request, dict) else request.account_id
        self._orders[bid] = {"client_order_id": coid, "account_id": aid, "status": "submitted"}
        await self._events[aid].put({"broker_order_id": bid, "client_order_id": coid,
                                      "kind": "status", "status": "submitted"})
        return {"broker_order_id": bid}

    async def CancelOrder(self, request, _ctx):
        bid = request["broker_order_id"] if isinstance(request, dict) else request.broker_order_id
        aid = request["account_id"] if isinstance(request, dict) else request.account_id
        order = self._orders.get(bid)
        if order is None:
            return {"cancel_requested": False}
        order["status"] = "cancelled"
        await self._events[aid].put({"broker_order_id": bid,
                                      "client_order_id": order["client_order_id"],
                                      "kind": "status", "status": "cancelled"})
        return {"cancel_requested": True}

    async def ModifyOrder(self, request, _ctx):
        old_bid = request["broker_order_id"] if isinstance(request, dict) else request.broker_order_id
        aid = request["account_id"] if isinstance(request, dict) else request.account_id
        old = self._orders.get(old_bid)
        if old is None:
            return {"new_broker_order_id": "", "parent_broker_order_id": old_bid}
        new_bid = f"FAKE-{uuid.uuid4()}"
        self._orders[new_bid] = {"client_order_id": old["client_order_id"],
                                  "account_id": aid, "status": "submitted"}
        old["status"] = "cancelled"
        await self._events[aid].put({"broker_order_id": old_bid,
                                      "client_order_id": old["client_order_id"],
                                      "kind": "replaced", "status": "cancelled"})
        await self._events[aid].put({"broker_order_id": new_bid,
                                      "client_order_id": old["client_order_id"],
                                      "kind": "status", "status": "submitted"})
        return {"new_broker_order_id": new_bid, "parent_broker_order_id": old_bid}

    async def OrderEvent(self, request, _ctx):
        aid = request["account_id"] if isinstance(request, dict) else request.account_id
        q = self._events[aid]
        while True:
            ev = await q.get()
            yield ev
            if ev["status"] in ("cancelled", "filled", "rejected", "expired"):
                break

    async def SearchContracts(self, request, _ctx):
        return {"matches": [{"symbol": request["query"] if isinstance(request, dict) else request.query,
                              "description": "Fake Match", "cusip": ""}]}

    async def GetOrders(self, request, _ctx):
        aid = request["account_id"] if isinstance(request, dict) else request.account_id
        return {"orders": [o for o in self._orders.values() if o["account_id"] == aid]}
```

- [ ] **Step 5: Re-run test (expect PASS)**

```bash
cd backend && uv run pytest tests/integration/test_fake_schwab_servicer.py -v
```

Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/tests/fixtures/fake_schwab.py backend/tests/integration/test_fake_schwab_servicer.py
git commit -m "test(fixtures): FakeSchwabServicer with place/cancel/modify/orderevent"
```

---

### Task E2: E2E tests — place/cancel + modify chain + capability gate

**Files:**
- Create: `backend/tests/e2e/test_e2e_schwab_place_cancel.py`
- Create: `backend/tests/e2e/test_e2e_schwab_modify_chain.py`
- Create: `backend/tests/e2e/test_e2e_capability_gate.py`

- [ ] **Step 1: Write E2E place/cancel** — `backend/tests/e2e/test_e2e_schwab_place_cancel.py`:

```python
"""Phase 8a E2E — POST /api/orders against FakeSchwabServicer + SSE assertion."""
from __future__ import annotations
import asyncio
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_place_then_cancel_round_trip(client: AsyncClient, fake_schwab):
    place_rsp = await client.post("/api/orders", json={
        "account_id": "acct-schwab-paper-1",
        "broker_id": "schwab",
        "conid": 0, "symbol": "F", "side": "BUY",
        "quantity": "1", "price": "1",
        "order_type": "LIMIT", "time_in_force": "DAY",
        "client_order_id": "cli-e2e-1",
    })
    assert place_rsp.status_code == 201
    order_id = place_rsp.json()["id"]

    sse_events = []
    async with client.stream("GET", "/api/orders/events") as stream:
        async for line in stream.aiter_lines():
            if line.startswith("data:"):
                sse_events.append(line)
                if "submitted" in line:
                    break

    cancel_rsp = await client.delete(f"/api/orders/{order_id}")
    assert cancel_rsp.status_code in (200, 202)
    await asyncio.sleep(0.5)
    final = await client.get(f"/api/orders/{order_id}")
    assert final.json()["status"] == "cancelled"
```

- [ ] **Step 2: Write E2E modify chain** — `backend/tests/e2e/test_e2e_schwab_modify_chain.py`:

```python
"""Phase 8a E2E — modify chain with parent_order_id link (HIGH-3)."""
from __future__ import annotations
import asyncio
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_modify_creates_replacement_with_parent_link(client: AsyncClient, fake_schwab):
    place_rsp = await client.post("/api/orders", json={
        "account_id": "acct-schwab-paper-1", "broker_id": "schwab",
        "conid": 0, "symbol": "F", "side": "BUY",
        "quantity": "1", "price": "1",
        "order_type": "LIMIT", "time_in_force": "DAY",
        "client_order_id": "cli-modify-1",
    })
    old_id = place_rsp.json()["id"]

    modify_rsp = await client.put(f"/api/orders/{old_id}", json={
        "new_quantity": "2", "new_price": "1.5", "nonce": "n1",
    })
    assert modify_rsp.status_code == 200
    new_id = modify_rsp.json()["new_order_id"]
    assert new_id != old_id

    await asyncio.sleep(0.5)
    old = await client.get(f"/api/orders/{old_id}")
    new = await client.get(f"/api/orders/{new_id}")
    assert old.json()["status"] == "cancelled"
    assert new.json()["status"] == "submitted"
    assert new.json()["parent_order_id"] == old_id  # HIGH-3 link
```

- [ ] **Step 3: Write E2E capability gate** — `backend/tests/e2e/test_e2e_capability_gate.py`:

```python
"""Phase 8a E2E — capability gate rejects unsupported combos for each broker."""
from __future__ import annotations
import pytest
from httpx import AsyncClient


@pytest.mark.parametrize("broker_id,order_type,tif", [
    ("schwab", "TRAIL", "DAY"),
    ("schwab", "MARKET", "GTD"),
    ("ibkr",   "TRAIL", "DAY"),
    ("futu",   "STOP",  "DAY"),
    ("alpaca", "MARKET", "DAY"),  # Alpaca all unsupported in 8a
])
@pytest.mark.asyncio
async def test_unsupported_combo_returns_422(
    client: AsyncClient, broker_id: str, order_type: str, tif: str,
) -> None:
    rsp = await client.post("/api/orders", json={
        "account_id": f"acct-{broker_id}-paper-1", "broker_id": broker_id,
        "conid": 0, "symbol": "F", "side": "BUY",
        "quantity": "1", "price": "1",
        "order_type": order_type, "time_in_force": tif,
        "client_order_id": f"cli-cap-{broker_id}-{order_type}-{tif}",
    })
    assert rsp.status_code == 422
    body = rsp.json()
    assert body["detail"]["error"]["code"] == "unsupported_order_type_for_broker"
```

- [ ] **Step 4: Run all E2E tests**

```bash
cd backend && uv run pytest tests/e2e/test_e2e_schwab_place_cancel.py tests/e2e/test_e2e_schwab_modify_chain.py tests/e2e/test_e2e_capability_gate.py -v
```

Expected: PASS for SSE round-trip, modify-chain, all 5 capability-gate parametrize cases.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/e2e/test_e2e_schwab_place_cancel.py backend/tests/e2e/test_e2e_schwab_modify_chain.py backend/tests/e2e/test_e2e_capability_gate.py
git commit -m "test(e2e): Schwab place/cancel/modify chains + capability-gate matrix"
```

---

### Task E3: C0 empirical script + JSON artifact (HARD GATE before A5/F)

**Files:**
- Create: `scripts/empirical/schwab_place_cancel_paper.py`
- Create: `scripts/empirical/artifacts/.gitkeep`

**⚠️ This script MUST PASS on a paper account before Task A5 (Schwab capability flip) and Chunk F (frontend) start.**

- [ ] **Step 1: Implement** — create `scripts/empirical/schwab_place_cancel_paper.py`:

```python
#!/usr/bin/env python3
"""Phase 8a C0 hard gate — empirical Schwab place/cancel against paper account.

Validates the four assumptions the sidecar implementation depends on:
1. POST /accounts/{hash}/orders returns Location: /orders/{id} header.
2. clientOrderId field round-trips (we extract from later poll).
3. executionLeg shape matches sidecar/normalize.py expectations.
4. Status string set is the 11 documented in spec §6 (no surprise statuses).

Writes JSON artifact to scripts/empirical/artifacts/schwab_c0_<UTC-ts>.json.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import schwabdev


ARTIFACT_DIR = Path(__file__).parent / "artifacts"
SUPPORTED_STATUSES = {
    "AWAITING_PARENT_ORDER", "PENDING_ACTIVATION", "QUEUED", "WORKING",
    "PENDING_CANCEL", "PENDING_REPLACE",
    "FILLED", "CANCELED", "REPLACED", "REJECTED", "EXPIRED",
}


def main() -> int:
    app_key = os.environ["SCHWAB_APP_KEY"]
    app_secret = os.environ["SCHWAB_APP_SECRET"]
    paper_account_hash = os.environ["SCHWAB_PAPER_ACCOUNT_HASH"]
    paper_symbol = os.environ.get("SCHWAB_PAPER_SYMBOL", "F")  # cheap symbol

    client = schwabdev.Client(app_key, app_secret, tokens_db="/tmp/c0_tokens.db")
    client_order_id = f"CLI-C0-{int(time.time())}"
    payload = {
        "orderType": "LIMIT", "session": "NORMAL", "duration": "DAY",
        "orderStrategyType": "SINGLE",
        "price": "1.00",
        "clientOrderId": client_order_id,
        "orderLegCollection": [{
            "instruction": "BUY", "quantity": 1,
            "instrument": {"symbol": paper_symbol, "assetType": "EQUITY"},
        }],
    }
    place_rsp = client.order_place(paper_account_hash, payload)
    assertions: dict = {
        "client_order_id": client_order_id,
        "place_status_code": place_rsp.status_code,
        "place_location_header": place_rsp.headers.get("Location"),
    }
    assert place_rsp.status_code in (200, 201), f"unexpected status: {place_rsp.status_code}"
    assert "Location" in place_rsp.headers, "MISSING LOCATION HEADER"
    broker_order_id = place_rsp.headers["Location"].rsplit("/", 1)[-1]
    assertions["broker_order_id"] = broker_order_id

    time.sleep(2)
    detail_rsp = client.order_details(paper_account_hash, broker_order_id)
    detail = detail_rsp.json()
    assertions["client_order_id_round_trips"] = detail.get("clientOrderId") == client_order_id
    assertions["status_observed"] = detail.get("status")

    cancel_rsp = client.order_cancel(paper_account_hash, broker_order_id)
    assertions["cancel_status_code"] = cancel_rsp.status_code

    time.sleep(2)
    final = client.order_details(paper_account_hash, broker_order_id).json()
    assertions["final_status"] = final.get("status")
    statuses_seen = {detail.get("status"), final.get("status")}
    unknown = statuses_seen - SUPPORTED_STATUSES
    assertions["unknown_statuses_observed"] = sorted(unknown)
    assertions["execution_leg_shape"] = (final.get("orderActivityCollection") or [{}])[0].get("executionLegs", [])

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    artifact_path = ARTIFACT_DIR / f"schwab_c0_{ts}.json"
    artifact_path.write_text(json.dumps(assertions, indent=2, default=str))
    print(f"\nC0 artifact written: {artifact_path}")
    print(json.dumps(assertions, indent=2, default=str))

    if not assertions["place_location_header"]:
        print("FAIL: Location header missing", file=sys.stderr)
        return 1
    if not assertions["client_order_id_round_trips"]:
        print("FAIL: clientOrderId did not round-trip", file=sys.stderr)
        return 1
    if unknown:
        print(f"FAIL: unknown Schwab statuses observed: {unknown}", file=sys.stderr)
        return 1
    print("\nPASS — all 4 C0 assertions hold. Safe to proceed with A5 + Chunk F.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Create artifacts directory placeholder**

```bash
mkdir -p scripts/empirical/artifacts && touch scripts/empirical/artifacts/.gitkeep
```

- [ ] **Step 3: Add `SCHWAB_PAPER_ACCOUNT_HASH` to `.env.example`**

```bash
echo "SCHWAB_PAPER_ACCOUNT_HASH=<paste hash from POST /accounts response>" >> .env.example
```

- [ ] **Step 4: Run the script (operator-invoked, NOT in CI)**

```bash
cd /home/joseph/dashboard
SCHWAB_APP_KEY=... SCHWAB_APP_SECRET=... SCHWAB_PAPER_ACCOUNT_HASH=... \
  uv run python scripts/empirical/schwab_place_cancel_paper.py
```

Expected: prints "PASS — all 4 C0 assertions hold." and writes JSON artifact.

- [ ] **Step 5: Commit script + JSON artifact (the artifact is evidence)**

```bash
git add scripts/empirical/schwab_place_cancel_paper.py scripts/empirical/artifacts/.gitkeep scripts/empirical/artifacts/schwab_c0_*.json .env.example
git commit -m "test(empirical): C0 Schwab place/cancel paper-account hard gate + artifact"
```

---

### Task E4: Nightly + weekly real-Schwab CI workflows

**Files:**
- Create: `.github/workflows/nightly-real-schwab.yml`
- Create: `.github/workflows/weekly-real-schwab-drift.yml`
- Create: `backend/tests/real_broker/test_real_schwab_e2e_place_cancel.py`
- Create: `backend/tests/real_broker/test_real_schwab_e2e_modify.py`
- Create: `backend/tests/real_broker/test_real_schwab_capability_drift.py`

- [ ] **Step 1: Write nightly workflow** — `.github/workflows/nightly-real-schwab.yml`:

```yaml
name: nightly-real-schwab
on:
  schedule:
    - cron: '0 12 * * *'   # 12:00 UTC daily
  workflow_dispatch:

jobs:
  e2e-real-schwab:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    env:
      SCHWAB_APP_KEY: ${{ secrets.SCHWAB_APP_KEY }}
      SCHWAB_APP_SECRET: ${{ secrets.SCHWAB_APP_SECRET }}
      SCHWAB_PAPER_ACCOUNT_HASH: ${{ secrets.SCHWAB_PAPER_ACCOUNT_HASH }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: cd backend && uv sync
      - run: cd backend && uv run pytest -m real_schwab tests/real_broker/test_real_schwab_e2e_place_cancel.py tests/real_broker/test_real_schwab_e2e_modify.py -v
```

- [ ] **Step 2: Write weekly drift workflow** — `.github/workflows/weekly-real-schwab-drift.yml`:

```yaml
name: weekly-real-schwab-drift
on:
  schedule:
    - cron: '0 12 * * 0'   # Sundays 12:00 UTC
  workflow_dispatch:

jobs:
  capability-drift:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    env:
      SCHWAB_APP_KEY: ${{ secrets.SCHWAB_APP_KEY }}
      SCHWAB_APP_SECRET: ${{ secrets.SCHWAB_APP_SECRET }}
      SCHWAB_PAPER_ACCOUNT_HASH: ${{ secrets.SCHWAB_PAPER_ACCOUNT_HASH }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: cd backend && uv sync
      - run: cd backend && uv run pytest -m real_schwab tests/real_broker/test_real_schwab_capability_drift.py -v
```

- [ ] **Step 3: Write real-Schwab tests** — create the 3 stub test files (real-broker bodies):

`backend/tests/real_broker/test_real_schwab_e2e_place_cancel.py`:

```python
"""Phase 8a — real Schwab paper place→cancel round trip (nightly)."""
from __future__ import annotations
import asyncio, os, time
import pytest
from httpx import AsyncClient


@pytest.mark.real_schwab
@pytest.mark.asyncio
async def test_real_paper_place_cancel(client: AsyncClient) -> None:
    coid = f"CLI-NIGHT-{int(time.time())}"
    place = await client.post("/api/orders", json={
        "account_id": os.environ["SCHWAB_PAPER_INTERNAL_ACCT"],
        "broker_id": "schwab", "conid": 0, "symbol": "F", "side": "BUY",
        "quantity": "1", "price": "1.00",
        "order_type": "LIMIT", "time_in_force": "DAY",
        "client_order_id": coid,
    })
    assert place.status_code == 201
    order_id = place.json()["id"]
    try:
        await asyncio.sleep(5)
        check = await client.get(f"/api/orders/{order_id}")
        assert check.json()["status"] in ("submitted", "pending_submit")
    finally:
        await client.delete(f"/api/orders/{order_id}")
```

`backend/tests/real_broker/test_real_schwab_e2e_modify.py`:

```python
"""Phase 8a — real Schwab paper place → modify → cancel chain (nightly)."""
from __future__ import annotations
import asyncio, os, time
import pytest
from httpx import AsyncClient


@pytest.mark.real_schwab
@pytest.mark.asyncio
async def test_real_paper_place_modify_cancel(client: AsyncClient) -> None:
    coid = f"CLI-MOD-{int(time.time())}"
    place = await client.post("/api/orders", json={
        "account_id": os.environ["SCHWAB_PAPER_INTERNAL_ACCT"],
        "broker_id": "schwab", "conid": 0, "symbol": "F", "side": "BUY",
        "quantity": "1", "price": "1.00",
        "order_type": "LIMIT", "time_in_force": "DAY",
        "client_order_id": coid,
    })
    old_id = place.json()["id"]
    try:
        await asyncio.sleep(3)
        modify = await client.put(f"/api/orders/{old_id}",
                                   json={"new_quantity": "2", "new_price": "0.95",
                                         "nonce": coid + "-mod"})
        new_id = modify.json()["new_order_id"]
        await asyncio.sleep(5)
        new = await client.get(f"/api/orders/{new_id}")
        assert new.json()["status"] in ("submitted", "pending_submit", "cancelled")
    finally:
        try:
            await client.delete(f"/api/orders/{new_id}")
        except Exception:
            pass
        try:
            await client.delete(f"/api/orders/{old_id}")
        except Exception:
            pass
```

`backend/tests/real_broker/test_real_schwab_capability_drift.py`:

```python
"""Phase 8a — weekly Schwab capability matrix drift detector (HIGH-2 + MED-6)."""
from __future__ import annotations
import asyncio, os, time
import pytest
from httpx import AsyncClient
from sqlalchemy import text


@pytest.mark.real_schwab
@pytest.mark.asyncio
async def test_capability_matrix_matches_schwab_actual(client: AsyncClient, db_session) -> None:
    rate_429 = (await db_session.execute(text(
        "SELECT COALESCE(SUM(value), 0) FROM metrics_snapshots "
        "WHERE name = 'schwab_http_requests_total' AND label_status = '429' "
        "AND ts > NOW() - INTERVAL '1 hour'"
    ))).scalar_one()
    rate_total = (await db_session.execute(text(
        "SELECT COALESCE(SUM(value), 0) FROM metrics_snapshots "
        "WHERE name = 'schwab_http_requests_total' AND ts > NOW() - INTERVAL '1 hour'"
    ))).scalar_one() or 1
    if rate_429 / rate_total > 0.5:
        pytest.skip("WARN: Schwab 429 rate >50% in last hour — skipping drift test")

    rows = (await db_session.execute(text(
        "SELECT order_type, time_in_force FROM broker_order_capability "
        "WHERE broker_id='schwab' AND is_supported=TRUE"
    ))).all()

    for row in rows:
        coid = f"CLI-DRIFT-{row.order_type}-{row.time_in_force}-{int(time.time())}"
        place = await client.post("/api/orders", json={
            "account_id": os.environ["SCHWAB_PAPER_INTERNAL_ACCT"],
            "broker_id": "schwab", "conid": 0, "symbol": "F", "side": "BUY",
            "quantity": "1", "price": "0.50",
            "order_type": row.order_type, "time_in_force": row.time_in_force,
            "client_order_id": coid,
        })
        try:
            assert place.status_code != 422, (
                f"Schwab now rejects matrix combo "
                f"({row.order_type}, {row.time_in_force}) — DRIFT"
            )
            if place.status_code == 201:
                order_id = place.json()["id"]
                await client.delete(f"/api/orders/{order_id}")
        except AssertionError:
            raise
```

Add `real_schwab` marker to `backend/pytest.ini`:

```ini
[pytest]
markers =
    real_schwab: tests that hit a real Schwab paper account (manual / nightly only)
```

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/nightly-real-schwab.yml .github/workflows/weekly-real-schwab-drift.yml backend/tests/real_broker/ backend/pytest.ini
git commit -m "ci(schwab): nightly + weekly real-Schwab CI (drift + place/cancel/modify)"
```

---

## Chunk F — Frontend hook + TradeTicketModal + Storybook + tests

**⚠️ Chunk F starts AFTER Task A5 (Schwab capability flip) AND Task E3 (C0 PASS).**

**Codex patterns most likely to bite:** TS-strict (no `any`); function components only; React-default text-node escaping (no `dangerouslySetInnerHTML`).

### Task F1: useBrokerCapabilities hook (TanStack Query, 5min staleTime, SSE invalidation)

**Files:**
- Create: `frontend/src/services/capabilities/useBrokerCapabilities.ts`
- Create: `frontend/src/services/capabilities/types.ts`
- Test: `frontend/src/services/capabilities/useBrokerCapabilities.test.tsx` (new)

- [ ] **Step 1: Regenerate api types**

```bash
cd /home/joseph/dashboard && bash scripts/gen-types.sh
```

Expected: `frontend/src/api-generated.ts` now has `BrokerCapabilitiesResponse`, `OrderTypeRow`, `TimeInForceRow`, `CapabilityComboRow`.

- [ ] **Step 2: Write failing test** — create `frontend/src/services/capabilities/useBrokerCapabilities.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReactNode } from "react";
import { useBrokerCapabilities } from "./useBrokerCapabilities";

const wrapper = ({ children }: { children: ReactNode }) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
};

beforeEach(() => {
  global.fetch = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      broker_id: "schwab",
      order_types: [{ code: "MARKET", label: "Market", description: "", sort_order: 10 }],
      time_in_force: [{ code: "DAY", label: "Day", description: "", requires_expiry: false, sort_order: 10 }],
      combos: [{ order_type: "MARKET", time_in_force: "DAY", supported: true, notes: "" }],
    }),
  });
});

describe("useBrokerCapabilities", () => {
  it("fetches capabilities for given broker", async () => {
    const { result } = renderHook(() => useBrokerCapabilities("schwab"), { wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data?.broker_id).toBe("schwab");
    expect(global.fetch).toHaveBeenCalledWith("/api/brokers/schwab/capabilities");
  });

  it("provides isSupported helper", async () => {
    const { result } = renderHook(() => useBrokerCapabilities("schwab"), { wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.isSupported("MARKET", "DAY")).toBe(true);
    expect(result.current.isSupported("TRAIL", "DAY")).toBe(false);
  });

  it("provides notesFor helper for grayed tooltips", async () => {
    (global.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        broker_id: "schwab",
        order_types: [], time_in_force: [],
        combos: [
          { order_type: "TRAIL", time_in_force: "DAY", supported: false, notes: "Coming in 8b" },
        ],
      }),
    });
    const { result } = renderHook(() => useBrokerCapabilities("schwab"), { wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.notesFor("TRAIL", "DAY")).toBe("Coming in 8b");
  });
});
```

- [ ] **Step 3: Run test (expect FAIL — module doesn't exist)**

```bash
cd frontend && pnpm test useBrokerCapabilities
```

- [ ] **Step 4: Implement** — create `frontend/src/services/capabilities/types.ts`:

```ts
export interface OrderTypeRow {
  code: string;
  label: string;
  description: string;
  sort_order: number;
}

export interface TimeInForceRow {
  code: string;
  label: string;
  description: string;
  requires_expiry: boolean;
  sort_order: number;
}

export interface CapabilityComboRow {
  order_type: string;
  time_in_force: string;
  supported: boolean;
  notes: string;
}

export interface BrokerCapabilitiesResponse {
  broker_id: string;
  order_types: OrderTypeRow[];
  time_in_force: TimeInForceRow[];
  combos: CapabilityComboRow[];
}
```

Then `frontend/src/services/capabilities/useBrokerCapabilities.ts`:

```ts
import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { BrokerCapabilitiesResponse } from "./types";

const FIVE_MIN_MS = 5 * 60 * 1000;

async function fetchCapabilities(brokerId: string): Promise<BrokerCapabilitiesResponse> {
  const rsp = await fetch(`/api/brokers/${brokerId}/capabilities`);
  if (!rsp.ok) {
    throw new Error(`capabilities fetch failed: ${rsp.status}`);
  }
  return rsp.json() as Promise<BrokerCapabilitiesResponse>;
}

export function useBrokerCapabilities(brokerId: string | null) {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ["broker-capabilities", brokerId],
    queryFn: () => fetchCapabilities(brokerId as string),
    enabled: !!brokerId,
    staleTime: FIVE_MIN_MS,
  });

  // SSE pubsub bust: invalidate when backend forwards `app_config:invalidate:order_capabilities`.
  useEffect(() => {
    const es = new EventSource("/api/sse/config_stream");
    const handler = (ev: MessageEvent) => {
      try {
        const data = JSON.parse(ev.data) as { topic: string; payload: string };
        if (data.topic === "app_config:invalidate:order_capabilities") {
          qc.invalidateQueries({ queryKey: ["broker-capabilities", data.payload] });
        }
      } catch {
        // ignore malformed event
      }
    };
    es.addEventListener("message", handler);
    return () => {
      es.removeEventListener("message", handler);
      es.close();
    };
  }, [qc]);

  function isSupported(orderType: string, tif: string): boolean {
    return !!query.data?.combos.some(
      c => c.order_type === orderType && c.time_in_force === tif && c.supported,
    );
  }

  function notesFor(orderType: string, tif: string): string {
    return query.data?.combos.find(
      c => c.order_type === orderType && c.time_in_force === tif,
    )?.notes ?? "";
  }

  return { ...query, isSupported, notesFor };
}
```

- [ ] **Step 5: Re-run test (expect PASS)**

```bash
cd frontend && pnpm test useBrokerCapabilities
```

Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/services/capabilities/ frontend/src/api-generated.ts
git commit -m "feat(fe): useBrokerCapabilities hook with SSE invalidation"
```

---

### Task F2: TradeTicketModal capability-aware dropdowns (lazy-disable + tooltip)

**Files:**
- Modify: `frontend/src/features/orders/TradeTicketModal.tsx`
- Modify: `frontend/src/features/orders/TradeTicketModal.test.tsx`

- [ ] **Step 1: Pre-flight read**

```bash
grep -n "order_type\|time_in_force\|OrderType\|TimeInForce" frontend/src/features/orders/TradeTicketModal.tsx | head
```

- [ ] **Step 2: Write failing test** — extend `frontend/src/features/orders/TradeTicketModal.test.tsx`:

```tsx
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TradeTicketModal } from "./TradeTicketModal";

const wrap = (ui: React.ReactNode) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
};

beforeEach(() => {
  global.fetch = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      broker_id: "schwab",
      order_types: [
        { code: "MARKET", label: "Market", description: "", sort_order: 10 },
        { code: "STOP_LIMIT", label: "Stop-Limit", description: "", sort_order: 40 },
        { code: "TRAIL", label: "Trailing Stop", description: "", sort_order: 50 },
      ],
      time_in_force: [
        { code: "DAY", label: "Day", description: "", requires_expiry: false, sort_order: 10 },
      ],
      combos: [
        { order_type: "MARKET", time_in_force: "DAY", supported: true, notes: "" },
        { order_type: "STOP_LIMIT", time_in_force: "DAY", supported: true, notes: "" },
        { order_type: "TRAIL", time_in_force: "DAY", supported: false, notes: "Coming in 8b" },
      ],
    }),
  });
});

describe("TradeTicketModal capability awareness", () => {
  it("renders all order types but disables unsupported", async () => {
    wrap(<TradeTicketModal account={{ id: "a1", broker_id: "schwab" }} mode="place" />);
    const dropdown = await screen.findByLabelText("Order Type");
    const options = within(dropdown).getAllByRole("option");
    const codes = options.map(o => o.textContent);
    expect(codes).toContain("Market");
    expect(codes).toContain("Trailing Stop");
    const trailOption = options.find(o => o.textContent === "Trailing Stop")!;
    expect(trailOption).toBeDisabled();
  });

  it("shows tooltip with notes on hover of disabled option", async () => {
    wrap(<TradeTicketModal account={{ id: "a1", broker_id: "schwab" }} mode="place" />);
    const dropdown = await screen.findByLabelText("Order Type");
    const trailOption = within(dropdown).getByText("Trailing Stop");
    await userEvent.hover(trailOption);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Coming in 8b");
  });

  it("disables Submit when current combo is unsupported", async () => {
    wrap(<TradeTicketModal account={{ id: "a1", broker_id: "schwab" }} mode="place"
                            initial={{ order_type: "TRAIL", time_in_force: "DAY" }} />);
    const submit = await screen.findByRole("button", { name: /submit|place/i });
    expect(submit).toBeDisabled();
    expect(screen.getByText(/Schwab does not support TRAIL/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run test (expect FAIL)**

```bash
cd frontend && pnpm test TradeTicketModal
```

- [ ] **Step 4: Implement capability-aware UX** — extend `frontend/src/features/orders/TradeTicketModal.tsx`:

```tsx
import { useBrokerCapabilities } from "@/services/capabilities/useBrokerCapabilities";
import { Tooltip } from "@/components/primitives/Tooltip";

interface Props {
  account: { id: string; broker_id: string };
  mode: "place" | "modify" | "bracket";
  initial?: { order_type?: string; time_in_force?: string };
}

export function TradeTicketModal({ account, mode, initial }: Props) {
  const caps = useBrokerCapabilities(account.broker_id);
  const [orderType, setOrderType] = useState(initial?.order_type ?? "MARKET");
  const [tif, setTif] = useState(initial?.time_in_force ?? "DAY");
  // ...existing fields (qty, price, side, conid)

  const supported = caps.isSupported(orderType, tif);
  const disabledReason = supported ? "" : caps.notesFor(orderType, tif)
    || `${account.broker_id} does not support ${orderType} + ${tif}`;

  return (
    <Modal>
      {/* ...existing form fields */}
      <label htmlFor="order-type-select">Order Type</label>
      <select id="order-type-select" value={orderType} onChange={e => setOrderType(e.target.value)}>
        {caps.data?.order_types.map(t => {
          const isOk = caps.isSupported(t.code, tif);
          return (
            <Tooltip key={t.code} content={isOk ? null : caps.notesFor(t.code, tif)}>
              <option value={t.code} disabled={!isOk} aria-disabled={!isOk}>
                {t.label}
              </option>
            </Tooltip>
          );
        })}
      </select>

      <label htmlFor="tif-select">Time in Force</label>
      <select id="tif-select" value={tif} onChange={e => setTif(e.target.value)}>
        {caps.data?.time_in_force.map(t => {
          const isOk = caps.isSupported(orderType, t.code);
          return (
            <Tooltip key={t.code} content={isOk ? null : caps.notesFor(orderType, t.code)}>
              <option value={t.code} disabled={!isOk} aria-disabled={!isOk}>
                {t.label}
              </option>
            </Tooltip>
          );
        })}
      </select>

      {!supported && (
        <p className="text-error">{disabledReason}</p>
      )}

      <button disabled={!supported} onClick={onSubmit}>
        {mode === "modify" ? "Modify" : "Place"}
      </button>
    </Modal>
  );
}
```

(Reuse existing Modal/Tooltip primitives from `@/components/primitives/`. Use React's default text-node escaping — no `dangerouslySetInnerHTML`. MED-1 defense-in-depth paired with backend CHECK constraint.)

- [ ] **Step 5: Re-run test + e2e + storybook compile**

```bash
cd frontend && pnpm test TradeTicketModal
cd frontend && pnpm tsc --noEmit
cd frontend && pnpm lint
```

Expected: 3 new tests PASS; 5b/5c modal tests still PASS; tsc + lint clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/orders/TradeTicketModal.tsx frontend/src/features/orders/TradeTicketModal.test.tsx
git commit -m "feat(fe): capability-aware TradeTicketModal (lazy-disable + tooltip + submit gate)"
```

---

### Task F3: Storybook stories — Schwab account, capability loading, capability error

**Files:**
- Create: `frontend/src/features/orders/TradeTicketModal.stories.tsx` (or extend if exists)

- [ ] **Step 1: Write stories** — `frontend/src/features/orders/TradeTicketModal.stories.tsx`:

```tsx
import type { Meta, StoryObj } from "@storybook/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { TradeTicketModal } from "./TradeTicketModal";

const meta: Meta<typeof TradeTicketModal> = {
  title: "Features/Orders/TradeTicketModal",
  component: TradeTicketModal,
  decorators: [
    (Story) => {
      const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      return <QueryClientProvider client={qc}><Story /></QueryClientProvider>;
    },
  ],
};

export default meta;
type Story = StoryObj<typeof TradeTicketModal>;

const SCHWAB_CAPS = {
  broker_id: "schwab",
  order_types: [
    { code: "MARKET", label: "Market", description: "", sort_order: 10 },
    { code: "LIMIT", label: "Limit", description: "", sort_order: 20 },
    { code: "STOP_LIMIT", label: "Stop-Limit", description: "", sort_order: 40 },
    { code: "TRAIL", label: "Trailing Stop", description: "", sort_order: 50 },
  ],
  time_in_force: [
    { code: "DAY", label: "Day", description: "", requires_expiry: false, sort_order: 10 },
    { code: "GTC", label: "Good Til Cancelled", description: "", requires_expiry: false, sort_order: 20 },
  ],
  combos: [
    { order_type: "MARKET", time_in_force: "DAY", supported: true, notes: "" },
    { order_type: "MARKET", time_in_force: "GTC", supported: true, notes: "" },
    { order_type: "LIMIT", time_in_force: "DAY", supported: true, notes: "" },
    { order_type: "LIMIT", time_in_force: "GTC", supported: true, notes: "" },
    { order_type: "STOP_LIMIT", time_in_force: "DAY", supported: true, notes: "" },
    { order_type: "STOP_LIMIT", time_in_force: "GTC", supported: true, notes: "" },
    { order_type: "TRAIL", time_in_force: "DAY", supported: false, notes: "Coming in 8b" },
    { order_type: "TRAIL", time_in_force: "GTC", supported: false, notes: "Coming in 8b" },
  ],
};

export const SchwabAccount: Story = {
  args: { account: { id: "a1", broker_id: "schwab" }, mode: "place" },
  parameters: {
    msw: { handlers: [
      http.get("/api/brokers/schwab/capabilities", () => HttpResponse.json(SCHWAB_CAPS)),
    ]},
  },
};

export const CapabilityLoading: Story = {
  args: { account: { id: "a1", broker_id: "schwab" }, mode: "place" },
  parameters: {
    msw: { handlers: [
      http.get("/api/brokers/schwab/capabilities", async () => {
        await new Promise(r => setTimeout(r, 30000));
        return HttpResponse.json(SCHWAB_CAPS);
      }),
    ]},
  },
};

export const CapabilityError: Story = {
  args: { account: { id: "a1", broker_id: "schwab" }, mode: "place" },
  parameters: {
    msw: { handlers: [
      http.get("/api/brokers/schwab/capabilities", () => new HttpResponse(null, { status: 500 })),
    ]},
  },
};
```

- [ ] **Step 2: Run Storybook compile**

```bash
cd frontend && pnpm storybook:build
```

Expected: build succeeds; 3 new stories present.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/orders/TradeTicketModal.stories.tsx
git commit -m "docs(fe): TradeTicketModal stories — Schwab + loading + error"
```

---

### Task F4: Wire OpenAPI snapshot lock for Phase 8a Pydantic models

**Files:**
- Modify: `backend/tests/integration/test_openapi_schema_lock.py` (or create the lock test if module exists)
- Test: same file

- [ ] **Step 1: Pre-flight — find existing snapshot test**

```bash
grep -rn "openapi_schema_lock\|test_openapi" backend/tests/ | head
```

- [ ] **Step 2: Extend lock test** — add to `backend/tests/integration/test_openapi_schema_lock.py`:

```python
@pytest.mark.asyncio
async def test_openapi_schema_lock_phase8a(client: AsyncClient, snapshot) -> None:
    rsp = await client.get("/openapi.json")
    spec = rsp.json()
    locked_models = [
        "BrokerCapabilitiesResponse",
        "OrderTypeRow",
        "TimeInForceRow",
        "CapabilityComboRow",
    ]
    schemas = {name: spec["components"]["schemas"][name] for name in locked_models}
    snapshot.assert_match(schemas, "phase8a_openapi_schemas.json")
```

- [ ] **Step 3: Run test, accept snapshot the first time**

```bash
cd backend && uv run pytest tests/integration/test_openapi_schema_lock.py::test_openapi_schema_lock_phase8a --snapshot-update -v
cd backend && uv run pytest tests/integration/test_openapi_schema_lock.py::test_openapi_schema_lock_phase8a -v
```

Expected: test PASS after snapshot accepted.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/integration/test_openapi_schema_lock.py backend/tests/__snapshots__/phase8a_openapi_schemas.json
git commit -m "test(api): OpenAPI schema lock for Phase 8a capability models"
```

---

## Chunk G — Metrics + alerts + runbook + close-out

**Codex patterns most likely to bite:** F (verbatim metric + alert label spelling).

### Task G1: 6 Schwab + 6 capability metrics in metrics.py

**Files:**
- Modify: `backend/app/core/metrics.py`
- Modify: `sidecar_schwab/metrics.py`
- Test: `backend/tests/unit/test_phase8a_metrics_present.py` (new)

- [ ] **Step 1: Write failing test** — create `backend/tests/unit/test_phase8a_metrics_present.py`:

```python
"""Phase 8a — verify all 12 new metrics are registered + label sets per spec §11."""
from __future__ import annotations
import pytest
from app.core import metrics


@pytest.mark.parametrize("name,labels", [
    ("schwab_order_poller_iterations_total", {"gateway_label", "account_id", "cadence"}),
    ("schwab_order_poller_cadence_changed_total", {"gateway_label", "account_id", "from", "to"}),
    ("schwab_order_event_emitted_total", {"kind"}),
    ("order_capability_check_total", {"broker", "result"}),
    ("order_capability_cache_hits_total", {"broker"}),
    ("order_capability_cache_misses_total", {"broker"}),
])
def test_counter_labels_match_spec(name: str, labels: set[str]) -> None:
    m = getattr(metrics, name)
    assert set(m._labelnames) == labels, f"{name}: expected {labels}, got {set(m._labelnames)}"


@pytest.mark.parametrize("name", [
    "schwab_place_order_duration_ms",
    "schwab_cancel_order_duration_ms",
    "schwab_modify_order_duration_ms",
])
def test_histogram_buckets_extended_for_token_refresh_tail(name: str) -> None:
    m = getattr(metrics, name)
    buckets = list(m._upper_bounds)
    assert 10000.0 in buckets and 30000.0 in buckets, (
        f"{name}: HIGH-4 requires extended buckets (10s, 30s) for token-refresh tail"
    )


def test_unlabeled_counters_present() -> None:
    for name in ("order_capability_admin_writes_total",
                 "order_capability_pubsub_invalidations_total",
                 "order_capability_pubsub_failures_total"):
        assert hasattr(metrics, name), f"missing metric: {name}"
```

- [ ] **Step 2: Run test (expect FAIL)**

```bash
cd backend && uv run pytest tests/unit/test_phase8a_metrics_present.py -v
```

- [ ] **Step 3: Add metrics** — extend `backend/app/core/metrics.py`:

```python
from prometheus_client import Counter, Histogram

# ── Phase 8a Schwab metrics (HIGH-4 extended buckets) ────────────────
SCHWAB_DURATION_BUCKETS = [50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000]

schwab_order_poller_iterations_total = Counter(
    "schwab_order_poller_iterations_total",
    "Schwab order poller iterations by cadence",
    ["gateway_label", "account_id", "cadence"],
)

schwab_order_poller_cadence_changed_total = Counter(
    "schwab_order_poller_cadence_changed_total",
    "Cadence transitions for Schwab order poller",
    ["gateway_label", "account_id", "from", "to"],
)

schwab_place_order_duration_ms = Histogram(
    "schwab_place_order_duration_ms",
    "Schwab PlaceOrder REST duration in milliseconds",
    buckets=SCHWAB_DURATION_BUCKETS,
)

schwab_cancel_order_duration_ms = Histogram(
    "schwab_cancel_order_duration_ms",
    "Schwab CancelOrder REST duration in milliseconds",
    buckets=SCHWAB_DURATION_BUCKETS,
)

schwab_modify_order_duration_ms = Histogram(
    "schwab_modify_order_duration_ms",
    "Schwab ModifyOrder (replace) REST duration in milliseconds",
    buckets=SCHWAB_DURATION_BUCKETS,
)

schwab_order_event_emitted_total = Counter(
    "schwab_order_event_emitted_total",
    "Schwab order events emitted to fan-out",
    ["kind"],
)

# ── Phase 8a capability foundation metrics ───────────────────────────
order_capability_check_total = Counter(
    "order_capability_check_total",
    "Capability check outcomes",
    ["broker", "result"],   # result: supported | unsupported | unknown_broker
)

order_capability_cache_hits_total = Counter(
    "order_capability_cache_hits_total",
    "Capability LRU cache hits",
    ["broker"],
)

order_capability_cache_misses_total = Counter(
    "order_capability_cache_misses_total",
    "Capability LRU cache misses",
    ["broker"],
)

order_capability_admin_writes_total = Counter(
    "order_capability_admin_writes_total",
    "Admin writes to broker_order_capability",
)

order_capability_pubsub_invalidations_total = Counter(
    "order_capability_pubsub_invalidations_total",
    "Capability cache invalidations triggered by Redis pubsub",
)

order_capability_pubsub_failures_total = Counter(
    "order_capability_pubsub_failures_total",
    "Redis pubsub publish failures (MED-5: silent cache-inconsistency canary)",
)
```

Mirror sidecar-side metrics in `sidecar_schwab/metrics.py` (already imports the above pattern).

- [ ] **Step 4: Re-run test (expect PASS)**

```bash
cd backend && uv run pytest tests/unit/test_phase8a_metrics_present.py -v
```

Expected: 12 PASS (3 unlabeled + 6 parametrized counter + 3 parametrized histogram).

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/metrics.py sidecar_schwab/metrics.py backend/tests/unit/test_phase8a_metrics_present.py
git commit -m "feat(metrics): 6 Schwab + 6 capability metrics for Phase 8a"
```

---

### Task G2: 6 Prometheus alerts for phase8a_schwab_trade group

**Files:**
- Modify: `deploy/prometheus/alerts.yml`
- Test: `deploy/prometheus/tests/test_phase8a_alerts.yml` (new — promtool unit test)

- [ ] **Step 1: Add alert group** — append to `deploy/prometheus/alerts.yml`:

```yaml
- name: phase8a_schwab_trade
  interval: 30s
  rules:
    - alert: SchwabOrderPollerStalled
      expr: |
        sum by (gateway_label, account_id) (
          rate(schwab_order_poller_iterations_total{cadence="2s"}[2m])
        ) == 0
        and on(gateway_label, account_id)
        sum by (gateway_label, account_id) (
          last_over_time(schwab_order_poller_iterations_total{cadence="2s"}[10m])
        ) > 0
      for: 90s
      labels:
        severity: page
      annotations:
        summary: "Schwab order poller stalled in fast cadence ({{ $labels.account_id }})"
        runbook: "deploy/runbook-schwab-trade.md#schwaborderpollerstalled"

    - alert: SchwabPlaceOrderErrorRateHigh
      expr: |
        (sum(rate(schwab_http_requests_total{endpoint="place_order", status!~"2.."}[5m]))
         / sum(rate(schwab_http_requests_total{endpoint="place_order"}[5m]))) > 0.1
      for: 5m
      labels: { severity: warning }
      annotations:
        summary: "Schwab PlaceOrder >10% non-2xx over 5min"
        runbook: "deploy/runbook-schwab-trade.md#schwabplaceordererror"

    - alert: SchwabOrderEventGapNoActivity
      expr: |
        (sum(schwab_order_in_flight_count) > 0)
        and (sum(rate(schwab_order_event_emitted_total[5m])) == 0)
      for: 5m
      labels: { severity: warning }
      annotations:
        summary: "Schwab orders in flight but no events emitted in 5min"
        runbook: "deploy/runbook-schwab-trade.md#schwabordereventgap"

    - alert: OrderCapabilityCacheChurn
      expr: rate(order_capability_pubsub_invalidations_total[1h]) > 100
      for: 15m
      labels: { severity: warning }
      annotations:
        summary: "Capability cache invalidated >100x in last hour"
        runbook: "deploy/runbook-schwab-trade.md#capabilitycachechurn"

    - alert: OrderCapabilityCheckUnknownBroker
      expr: increase(order_capability_check_total{result="unknown_broker"}[5m]) > 0
      for: 1m
      labels: { severity: page }
      annotations:
        summary: "Capability check hit unknown_broker — registry/table drift"
        runbook: "deploy/runbook-schwab-trade.md#unknownbroker"

    - alert: OrderCapabilityPubsubFailures
      expr: increase(order_capability_pubsub_failures_total[5m]) > 0
      for: 5m
      labels: { severity: warning }
      annotations:
        summary: "Redis pubsub failed to publish capability invalidation"
        runbook: "deploy/runbook-schwab-trade.md#capabilitypubsubfailures"
```

- [ ] **Step 2: Write promtool unit test** — `deploy/prometheus/tests/test_phase8a_alerts.yml`:

```yaml
rule_files:
  - ../alerts.yml

evaluation_interval: 30s

tests:
  - interval: 30s
    name: SchwabOrderPollerStalled fires after 90s no iterations
    input_series:
      - series: 'schwab_order_poller_iterations_total{cadence="2s",gateway_label="schwab-paper",account_id="acct1"}'
        values: '5+0x40'
    alert_rule_test:
      - eval_time: 5m
        alertname: SchwabOrderPollerStalled
        exp_alerts:
          - exp_labels: { severity: page, gateway_label: schwab-paper, account_id: acct1 }

  - interval: 30s
    name: OrderCapabilityCheckUnknownBroker pages on first hit
    input_series:
      - series: 'order_capability_check_total{broker="bogus",result="unknown_broker"}'
        values: '0+1x10'
    alert_rule_test:
      - eval_time: 2m
        alertname: OrderCapabilityCheckUnknownBroker
        exp_alerts:
          - exp_labels: { severity: page, broker: bogus, result: unknown_broker }
```

- [ ] **Step 3: Run promtool**

```bash
cd /home/joseph/dashboard
docker run --rm -v $PWD/deploy/prometheus:/etc/prometheus prom/prometheus:latest \
  promtool test rules /etc/prometheus/tests/test_phase8a_alerts.yml
```

Expected: SUCCESS, 2 tests run.

- [ ] **Step 4: Commit**

```bash
git add deploy/prometheus/alerts.yml deploy/prometheus/tests/test_phase8a_alerts.yml
git commit -m "feat(alerts): phase8a_schwab_trade group (6 alerts) + promtool tests"
```

---

### Task G3: Operator runbook — deploy/runbook-schwab-trade.md

**Files:**
- Create: `deploy/runbook-schwab-trade.md`

- [ ] **Step 1: Write runbook** — create `deploy/runbook-schwab-trade.md`:

```markdown
# Runbook — Schwab Trade Execution (Phase 8a)

This runbook covers Schwab single-leg trade write-path operations introduced in v0.8.0.

## Pre-deploy checklist
1. `provision-and-publish.ps1` rotated mTLS certs (NUC sidecars only — Schwab is in-cluster).
2. `app_secrets.broker.schwab.{app_key,app_secret,refresh_token}` populated and unexpired.
3. `broker_order_capability` rows for `broker_id='schwab'` flipped via Alembic 0011a.
4. C0 empirical script most-recent artifact PASS (`scripts/empirical/artifacts/schwab_c0_*.json`).

## First canary on prod (paper)
1. Open Schwab paper account in dashboard FE.
2. Trade ticket: BUY 1 share of `F` (Ford) @ $0.50 LIMIT DAY (far from market).
3. Expected: order appears in `orders` table within 5s with `status='submitted'`.
4. Modify: change quantity to 2, price to $0.45.
5. Expected: original `cancelled` (kind=`replaced`); new order `submitted`; new row's `parent_order_id` = old row's `id`.
6. Cancel the new order.
7. Expected: status transitions to `cancelled` within 30s.

If any step fails, capture metrics snapshot:

```bash
curl -s http://10.10.0.2:8000/metrics | grep -E "schwab_(place_order|cancel_order|modify_order|order_poller|order_event)"
```

## Alert response

### SchwabOrderPollerStalled
- **Symptom:** account in fast cadence (2s) but no `schwab_order_poller_iterations_total` increment in 90s.
- **First check:** `docker logs schwab-sidecar | tail -100` — look for cancelled task or unhandled exception in poller.
- **Mitigation:** `docker compose restart schwab-sidecar` — supervisor lifespan re-hydrates state from Redis (CRIT-2 invariant); no state loss.
- **Escalate:** if restart doesn't resolve in 5min, page operator.

### SchwabPlaceOrderErrorRateHigh
- **Symptom:** >10% of `place_order` calls returning non-2xx in 5min.
- **First check:** `docker logs schwab-sidecar | grep schwab_place_order_duration_ms` for status patterns (4xx vs 5xx).
- **If 401:** Schwab token refresh is failing; check Tier-1/Tier-2 schedule; manually re-authorize via FE.
- **If 429:** rate limit hit; increase per-account semaphore concurrency cap or back off poller cadence.
- **If 5xx:** Schwab API outage; pause trades via `broker.kill_switch_enabled` in `app_config`.

### SchwabOrderEventGapNoActivity
- **Symptom:** in-flight orders > 0, no events emitted in 5min.
- **First check:** Redis connectivity; `redis-cli ping`.
- **Mitigation:** restart `schwab-sidecar`; state cache hydrates from Redis.

### OrderCapabilityCacheChurn
- **Symptom:** >100 cache invalidations/h.
- **First check:** any operator running a script that POSTs to `/api/admin/order-capabilities` in a loop?
- **Mitigation:** debounce admin writes; one row per minute is normal.

### OrderCapabilityCheckUnknownBroker — PAGE
- **Symptom:** capability check returned `unknown_broker` even once.
- **Root cause:** `brokers` registry vs `broker_order_capability` table FK drift; broker registered without capability rows seeded.
- **Mitigation:** run `INSERT INTO broker_order_capability ...` for the new broker; flush cache via `POST /api/admin/order-capabilities` with any row for that broker.

### OrderCapabilityPubsubFailures
- **Symptom:** Redis pubsub publish failed (silent cache-inconsistency canary, MED-5).
- **First check:** `redis-cli ping`; `journalctl -u redis | tail -50`.
- **Mitigation:** in-process cache invalidation is performed locally on publish failure (defense-in-depth in `OrderCapabilityService.publish_invalidation`), so single-worker is safe; fix Redis health.

## Account-hash rotation handling (HIGH-6)
When Schwab rotates an `account_hash`:
- `schwab_account_hash_refresh_total{reason='rotation'}` increments.
- Sidecar invalidates `_PLACE_REPLAY_CACHE` for old hash.
- OrderPoller for old hash tears down; new poller registers on next OrderEvent stream open.
- **Operator action:** none required; document rotation event in incident log if it correlates with operator-visible failures.
```

- [ ] **Step 2: Commit**

```bash
git add deploy/runbook-schwab-trade.md
git commit -m "docs(ops): runbook for Schwab trade execution + alert response"
```

---

### Task G4: Close-out — CLAUDE.md / CHANGELOG.md / TASKS.md / memory + tag v0.8.0

**Files:**
- Modify: `CLAUDE.md`
- Modify: `CHANGELOG.md`
- Modify: `TASKS.md`
- Modify: `docs/ROADMAP.md`
- Create: `/home/joseph/.claude/projects/-home-joseph-dashboard/memory/phase8a_shipped.md`
- Update: `/home/joseph/.claude/projects/-home-joseph-dashboard/memory/MEMORY.md` (index entry)

- [ ] **Step 1: Update CLAUDE.md** — add to Broker adapters section:

```markdown
- `phase8a_shipped.md` — capability matrix tables + Schwab single-leg trade write-path (v0.8.0).
```

Add subsection under "Cross-cutting load-bearing rules":

```markdown
- **Capability gate ordering:** OrderService runs `kill_switch → maintenance → capability → dispatch` in that strict order (Phase 8a CRIT-3). Capability check returns 422 with `error.code="unsupported_order_type_for_broker"` when `(broker, order_type, time_in_force)` row has `is_supported=false`. Per-broker scope (HIGH-1) — paper/live share rows.
- **Schwab order events via adaptive poller:** sidecar polls `GET /accounts/{hash}/orders` at 2s active / 30s idle cadence per `(gateway_label, account_id)` (CRIT-1). State cache is Redis-backed (CRIT-2) so sidecar restart doesn't re-emit `submitted` for in-flight orders.
```

- [ ] **Step 2: Update CHANGELOG.md** — prepend new section:

```markdown
## v0.8.0 — 2026-05-06

### Added
- `order_types`, `time_in_force`, `broker_order_capability` tables (Alembic 0011, 200 rows seeded).
- `OrderCapabilityService` (60s LRU + Redis-pubsub bust) and `GET /api/brokers/{id}/capabilities`.
- Admin endpoint `POST /api/admin/order-capabilities` (PUT-semantics + CSRF + code-set guard).
- Schwab sidecar PlaceOrder / CancelOrder / ModifyOrder / OrderEvent (server-streaming) / SearchContracts / GetOrders.
- Adaptive Schwab order-event poller (2s active / 30s idle) with Redis-backed state cache and account-hash rotation handling.
- SIM-mode echo for Schwab (mirrors IBKR 5b.1 pattern).
- Frontend `useBrokerCapabilities` hook + capability-aware `TradeTicketModal` (lazy-disable + tooltip).
- 6 Schwab metrics + 6 capability metrics; 6 new Prometheus alerts (`phase8a_schwab_trade` group).
- C0 empirical script + JSON artifact gate; nightly + weekly real-Schwab CI workflows.

### Changed
- `OrderType` and `TimeInForce` Python `Literal` extended to full Phase 8 universe (TRAIL, TRAIL_LIMIT, MOC, MOO, LOC, LOO; GTD).
- `OrderService.preview_order` / `place_order` / `modify_order` now run capability check before broker dispatch (after kill_switch + maintenance).
- `OrderEventConsumer._process_event` no-ops on same-rank-same-status events with no new exec_id (CRIT-2 backend dedup).
- `OrderEventMessage` modify-chain link uses existing 5c `parent_order_id` self-FK (HIGH-3) instead of free-form `kind` string.
```

- [ ] **Step 3: Update TASKS.md** — close 8a, expand 8b/8c task lists with cross-refs:

```markdown
## Phase 8a — Capability foundation + Schwab trade (CLOSED · v0.8.0 · 2026-05-06)

Spec: `docs/superpowers/specs/2026-05-05-phase8a-capability-foundation-schwab-trade-design.md`
Plan: `docs/superpowers/plans/2026-05-05-phase8a-capability-foundation-schwab-trade-plan.md`
Memory: `phase8a_shipped.md`

## Phase 8b — Cross-broker order-type expansion + Futu Modify/Bracket (NEXT)

Brainstorm pending. References: 8a spec §15 deferrals.
```

- [ ] **Step 4: Update ROADMAP.md** — flip Phase 8 row to mark 8a shipped, 8b/8c upcoming.

- [ ] **Step 5: Write memory `phase8a_shipped.md`** — `/home/joseph/.claude/projects/-home-joseph-dashboard/memory/phase8a_shipped.md`:

```markdown
---
name: Phase 8a shipped (v0.8.0 · 2026-05-06)
description: Capability matrix + Schwab single-leg trade write-path. Architect-review CRIT+HIGH+MED applied inline. Adaptive poller (2s/30s) + Redis state cache. Single-worker invariant preserved.
type: project
---
**Why:** Phase 8a is the first phase to enforce the architectural pillar #3 (DB-driven OrderType/TIF + per-broker capability map) and the first phase Schwab can place real-money orders. Future work touching `OrderService` validation order, the capability gate, the Schwab poller, the SIM registry, the modify-chain audit link (parent_order_id), or the capability admin endpoint must understand the constraints captured here.

**How to apply:** Consult before changing any of the files listed in §"What shipped" — they are load-bearing for Phase 8b (which extends the matrix) and Phase 8c (which mirrors the pattern for Alpaca trade).

## What shipped (28 tasks · A1 → G4)

- **Schema:** Alembic 0011 (3 tables, 200-row seed) + 0011a (Schwab capability flip after C0 PASS).
- **Backend service:** `app/services/order_capability_service.py` (60s LRU, Redis-pubsub bust on `app_config:invalidate:order_capabilities`).
- **Backend API:** `GET /api/brokers/{id}/capabilities` + `POST /api/admin/order-capabilities` (PUT-semantics + CSRF + code-set guard).
- **Capability gate:** strict order in `OrderService` — kill_switch → maintenance → capability → dispatch.
- **Consumer dedup:** `_process_event` no-ops same-rank-same-status no-new-exec events (CRIT-2 backend half).
- **Sidecar handlers:** Schwab `PlaceOrder` / `CancelOrder` / `ModifyOrder` / `OrderEvent` (stream) / `SearchContracts` / `GetOrders` flipped from UNIMPLEMENTED.
- **Sidecar poller:** `order_poller.py` per-`(gateway_label, account_id)` adaptive 2s/30s + 429 backoff + account-hash rotation handler.
- **Sidecar state cache:** `order_state_cache.py` Redis-backed write-through, hydrates on restart (CRIT-2 sidecar half).
- **Sidecar simulator:** `simulator.py` SIM-prefix synthetic place/cancel/modify echo (5b.1 pattern).
- **Frontend:** `useBrokerCapabilities` TanStack Query hook + capability-aware `TradeTicketModal` (lazy-disable + tooltip + Submit gate).
- **C0 empirical:** `scripts/empirical/schwab_place_cancel_paper.py` + JSON artifact (committed evidence).
- **CI:** `nightly-real-schwab.yml` + `weekly-real-schwab-drift.yml`.
- **Ops:** 12 metrics + 6 alerts + `deploy/runbook-schwab-trade.md`.

## Key invariants — DO NOT regress without explicit redesign

- **Capability gate ordering (CRIT-3):** kill_switch → maintenance → capability → dispatch. Capability check is a peer of policy checks, NOT a kill-switch replacement.
- **Per-broker capability scope (HIGH-1):** capability rows keyed `broker_id`, not `gateway_label`. `schwab-live` and `schwab-paper` share rows. Future divergence requires PK extension with `gateway_label_filter`.
- **Modify chain link (HIGH-3):** uses existing 5c `parent_order_id` self-FK, NOT free-form `kind` string. Replacement order's `orders` row sets `parent_order_id` to old order's UUID.
- **Persistent state cache (CRIT-2):** sidecar `order_state_cache` is Redis-backed write-through (key `schwab:order_state:<gateway_label>:<account_id>`, 7d TTL). On restart, hydrates from Redis. Backend `_process_event` also dedups same-rank-same-status no-new-exec events.
- **Poller supervisor key (CRIT-1):** `(gateway_label, account_id)`, not `account_hash`. Sidecar resolves account_hash internally.
- **Token pre-warm (HIGH-4):** `_ensure_fresh_token()` called at lifespan start AND on fast-cadence transition. Histogram buckets extended to 10s + 30s for token-refresh tail.
- **Single-worker still load-bearing.** `_PLACE_REPLAY_CACHE`, `_search_cache`, in-process LRU all process-local. Phase 9 (multi-worker) will replace.

## Deferred to Phase 8b
- TRAIL / TRAIL_LIMIT / MOC / MOO / LOC / LOO across all brokers.
- GTD across all brokers.
- IBKR / Futu capability flips for new types.
- Futu Modify + Bracket (Phase 6 deferral).
- Schwab brackets / `complexOrderStrategyType=TRIGGER` / `OCO`.

## Hard lessons captured this phase
- **C0 hard gate paid off:** the empirical script flushed assumptions about Schwab `Location` header + `clientOrderId` round-trip + `executionLeg` shape + status string set BEFORE frontend or capability flip work began. Mirrors Phase 5b.1 C1 BASE-tag pattern. Same gate should be inserted whenever a sidecar depends on undocumented broker REST behavior.
- **Architect review at CRIT+HIGH+MED tier:** 17 findings applied inline in spec, prevented mid-impl rework. The activity-aware alert (HIGH-5) and parent_order_id reuse (HIGH-3) were particularly load-bearing — the pre-review free-form `kind` string would have created queryability problems for the Tax page in Phase 23.
```

- [ ] **Step 6: Update MEMORY.md index** — add line:

```markdown
- [Phase 8a shipped (v0.8.0 · 2026-05-06)](phase8a_shipped.md) — capability matrix tables + Schwab single-leg trade write-path. Adaptive poller + Redis state cache. CRIT-1/2/3 invariants.
```

- [ ] **Step 7: Commit close-out + tag**

```bash
git add CLAUDE.md CHANGELOG.md TASKS.md docs/ROADMAP.md
git commit -m "docs(v0.8.0): close-out — capability foundation + Schwab trade write-path"

# Memory commit is to a separate repo (~/.claude/...) — operator runs that step manually.

git tag -a v0.8.0 -m "v0.8.0 — Phase 8a: capability foundation + Schwab trade (28 tasks, 17 architect findings applied)"
git push origin main --tags
```

- [ ] **Step 8: Update breadcrumb memory + delete stale 2026-05-05 breadcrumb**

```bash
# Edit /home/joseph/.claude/projects/-home-joseph-dashboard/memory/session_breadcrumb_2026-05-05.md
#   → mark "stale-by" condition met (Phase 8 brainstorm started + 8a shipped).
# Delete the breadcrumb file once 8a is closed.
```

---

## Self-review checklist (executed by author before handoff)

**Spec coverage** — every spec section maps to ≥1 task:
- §1 In scope: A3 (tables) · A2/A3 (full universe) · B1 (service) · B2 (GET endpoint) · B4 (capability gate) · C3-C5 (sidecar RPCs) · D2 (poller) · D3 (SIM) · F1-F2 (FE) · G1-G2 (metrics+alerts) · E3 (C0) · G3 (runbook).
- §3 Schema invariants: A2/A3 (DB ⊆ proto + 422 reject) · B1 (pubsub bust).
- §5 RPC handlers: C3 (PlaceOrder) · C4 (CancelOrder + ModifyOrder) · C5 (OrderEvent + SearchContracts + GetOrders).
- §6 State machine — all 11 statuses: C2 parametrized test.
- §7 Error taxonomy + idempotency + Schwab gotchas: C3 (replay cache) · C3-C4-C5 (token pre-warm + error map) · D2 (rate-limit semaphore via PollerSupervisor).
- §10 Testing strategy: A2 (proto match) · A3 (migration) · B1 (service) · B3 (admin) · B4 (gate) · C2 (normalize) · C3-C5 (handlers) · D1 (state cache) · D2 (poller) · D3 (sim) · D4 (lifespan) · E1 (fake servicer) · E2 (E2E) · E3 (C0) · E4 (real-Schwab) · F1-F2 (FE).
- §11 Observability — 12 metrics + 6 alerts: G1 + G2.
- §12 Rollout sequence: enforced by task ordering A→B→C→D→E1-E3→A5→F→G.
- §13 Success criteria: each gate maps to its task suite.

**Architect findings** — all 17 (3 CRIT + 6 HIGH + 8 MED) addressed:
- CRIT-1 (poller key): Task D2.
- CRIT-2 (persistent state + backend dedup): Task D1 + B5.
- CRIT-3 (gate ordering): Task B4.
- HIGH-1 (per-broker scope): Task A3 + spec doc note.
- HIGH-2 (drift detector): Task E4.
- HIGH-3 (parent_order_id link): Task C2 + C4.
- HIGH-4 (token pre-warm + extended buckets): Task C3 + G1.
- HIGH-5 (activity-aware alert): Task G2 (`SchwabOrderEventGapNoActivity`).
- HIGH-6 (account-hash rotation): Task D2.
- MED-1 (notes XSS): Task A3 (CHECK constraint) + F2 (React escape).
- MED-2 (PUT-semantics): Task B3.
- MED-3 (C0 artifact): Task E3.
- MED-4 (admin code-set guard): Task B3 + spec doc.
- MED-5 (pubsub failure metric): Task G1 + G2.
- MED-6 (drift detector quota guard): Task E4.
- MED-7 (CSRF on admin): Task B3.
- MED-8 (no coverage exemptions): Task D3 (freezegun for GC) + Task D2 (parametrized backoff).

**Type consistency cross-check:**
- `OrderCapabilityService.is_supported(broker_id, order_type, tif)` signature consistent across B1, B4, F1.
- `OrderState` dataclass shape consistent across D1 + D2.
- `WireEvent` dataclass shape consistent across D2 + D3.
- `parent_broker_order_id` proto field referenced consistently in C4 + C2 + spec §6.

**Placeholder scan** — confirmed clean: zero TBD/TODO/"implement"/"similar to Task N"/"fill in details".

---






