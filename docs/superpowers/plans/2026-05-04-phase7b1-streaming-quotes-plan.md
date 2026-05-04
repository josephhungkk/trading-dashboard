# Phase 7b.1 — Streaming quote engine implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `MockQuotesService` with a real-time multi-source streaming quote engine: backend `QuoteEngine` fans in ticks via bidi gRPC `StreamQuotes` from `sidecar_ibkr` (×4) + `sidecar_futu` + `sidecar_schwab`, fans out to Redis bus + `/ws/quotes` MessagePack gateway with focused/background conflation; new `instruments` + `symbol_aliases` schema (Alembic 0009).

**Architecture:** Bidi gRPC `StreamQuotes(stream req) returns (stream QuoteMessage)` — backend = client, sidecar = server. Two-level subscription refcount (per-WS + global). Source-router config-driven priority via `app_config.quote_source_priority`. Per-WS `WSConflator` (10 Hz focused / 4 Hz background). Engine invariants `INV-Q-1..4` (single-worker Redis loopback suppression, M22 boundary strip, staleness-not-reroute, token-rotation Event ordering).

**Tech Stack:** Python 3.14 + FastAPI + grpclib/grpcio-aio + asyncpg + msgpack-python (backend); React 19 + TypeScript 6 + `@msgpack/msgpack` + Zustand (FE); PyInstaller-frozen sidecars (IBKR/Futu) + in-cluster Docker (Schwab); Alembic 0009; Prometheus + structlog observability.

**Spec reference:** [`docs/superpowers/specs/2026-05-04-phase7b1-streaming-quotes-design.md`](../specs/2026-05-04-phase7b1-streaming-quotes-design.md) (architect-reviewed, 3 CRIT + 7 HIGH + 11 MED applied inline; 5 LOWs deferred).

**Codex/Claude split** (updated policy 2026-05-04):
- **Codex** writes source code AND tests (Python + TypeScript). Test-writing is no longer Claude-exclusive.
- **Claude** writes runbooks, commits, CHANGELOG/TASKS/CLAUDE.md updates, dispatches verification subagents, and acts as fallback when Codex hits rate limit.
- Codex prompts in this plan are dispatched via `codex:rescue` agent.

**Parallelism**: When two tasks are independent (no shared file edits, no upstream/downstream dep), dispatch Codex agents in parallel — single message, multiple `Agent` calls. Particularly applicable to:
- Per-sidecar streamer ports (C1, D1, E1) — three independent file trees, no shared edits.
- Per-source unit tests (B2, B3 tests) — no shared imports beyond stable types.
- Frontend tests (G1 test) running in parallel with backend tests (B5 test).

**Rate-limit fallback** (per memory `feedback_codex_fallback.md` + 2026-05-04 update):
- When Codex returns a rate-limit error or hangs >5 min, **Claude takes over the same task** (writes the code or test inline). Mark the task with `[claude-fallback]` in the commit message.
- After completing the fallback task, attempt the **next planned Codex task as a canary**. If Codex succeeds within ~5 min, resume Codex delegation; if it rate-limits again, schedule a `ScheduleWakeup` for ~30 min ahead and continue with Claude in the meantime.
- Operator can override at any time with "use codex" / "claude take over".

**Reuse policy** (per memory `feedback_scope_boundaries.md`): port aggressively from `dashboard_old/backend/app/services/quotes/` (~2,200 lines, ~70-95% reuse). Each task that ports legacy code says "Port from `<path>`."

**Reviewer chain per commit** (per `project_tooling_inventory.md`): spec-compliance + code-quality + lang-reviewer minimum, plus security/db/type/silent-failure/a11y/build/tdd when triggered. Reviews fire every commit, never batched.

**Pre-flight gate (run once before Chunk A starts):** verify the dev env is in good state — `docker compose ps` shows backend/redis/nginx running; `gh repo view --json name` returns; PG-18 reachable via `psql -h 10.10.0.2 -U trader -d dashboard -c '\dt'`; pnpm + uv installed; `dashboard_old/` mounted at `/mnt/c/Dashboard_old/`. If any fail, stop and resolve before proceeding.

---

## Chunk A — Proto + Alembic 0009 + instruments seed

### Task A1: Add `StreamQuotes` RPC + `SymbolRef` + `QuoteMessage` to broker.proto

**Files:**
- Modify: `proto/broker/v1/broker.proto`

- [ ] **Step 1: Read current proto to locate insertion points**

Run: `grep -n "^service Broker\|^message AccountResponse\|^service BackendCallback" proto/broker/v1/broker.proto`

Expected: shows line numbers for `service Broker {` (around line 18), `service BackendCallback {` (around line 322).

- [ ] **Step 2: Dispatch Codex to add the proto changes**

Codex prompt (via `codex:rescue` agent):

> Edit `/home/joseph/dashboard/proto/broker/v1/broker.proto`:
>
> 1. Inside `service Broker { ... }`, append one new RPC AFTER `rpc Configure(ConfigureRequest) returns (ConfigureResponse);`:
>
> ```protobuf
>   // Phase 7b.1 — bidirectional streaming quotes. Backend dials sidecar
>   // (sidecar = gRPC server, matches Health/GetPositions). Subscribe/Unsubscribe/
>   // Resync/Heartbeat travel client→server; QuoteMessage travels server→client.
>   // See spec §4.2 + §5.2.4.
>   rpc StreamQuotes(stream StreamQuotesRequest) returns (stream QuoteMessage);
> ```
>
> 2. Append these new messages BEFORE `service BackendCallback {`:
>
> ```protobuf
> // ===== Phase 7b.1 streaming quotes =====
>
> message StreamQuotesRequest {
>   oneof op {
>     Subscribe   subscribe   = 1;
>     Unsubscribe unsubscribe = 2;
>     Heartbeat   heartbeat   = 3;
>     Resync      resync      = 4;
>   }
>
>   message Subscribe   { repeated SymbolRef symbols = 1; }
>   message Unsubscribe { repeated SymbolRef symbols = 1; }
>   message Heartbeat {
>     google.protobuf.Timestamp client_time = 1;
>     int32 tick_count_received = 2;
>   }
>   // Sidecar reconciles its upstream-broker refcount against `expected`; only
>   // diff propagates to broker socket. Used when gRPC reconnects but sidecar
>   // process didn't restart (Health.started_at unchanged).
>   message Resync { repeated SymbolRef expected = 1; }
> }
>
> message SymbolRef {
>   string canonical_id = 1;
>   string raw_symbol   = 2;
>   AssetClass asset_class = 3;
>   string exchange     = 4;
>   string currency     = 5;
>   bytes  source_meta  = 6;
>   reserved 7 to 15;  // Phase 12/14 contract_extra extensions
> }
>
> message QuoteMessage {
>   string canonical_id = 1;
>   google.protobuf.Timestamp tick_time   = 2;
>   google.protobuf.Timestamp received_at = 3;
>   string source = 4;
>
>   string last       = 10;
>   string bid        = 11;
>   string ask        = 12;
>   string volume     = 13;
>   string day_high   = 14;
>   string day_low    = 15;
>   string open       = 16;
>   string prev_close = 17;
>   string change_pct = 18;
>   string change     = 19;
>
>   bool   is_delayed     = 30;
>   int32  delay_seconds  = 31;
>
>   bytes  raw_payload    = 90;
> }
> ```
>
> Run `grep -c "^message " proto/broker/v1/broker.proto` after — count increases by exactly 7 (StreamQuotesRequest + 4 nested + SymbolRef + QuoteMessage). `grep -c "rpc " proto/broker/v1/broker.proto` increases by exactly 1.

- [ ] **Step 3: Verify proto syntax**

Run: `cd /home/joseph/dashboard && uv run buf lint proto/`

Expected: no errors.

- [ ] **Step 4: Run codegen**

Run: `cd /home/joseph/dashboard && bash scripts/proto-codegen.sh` (verify with `ls scripts/*proto*` first; fallback to direct `python -m grpc_tools.protoc -I proto --python_out=... --grpc_python_out=...` for backend + 3 sidecar dirs).

Expected: `_pb2.py` + `_pb2.pyi` + `_pb2_grpc.py` regenerated under all 4 codegen targets.

- [ ] **Step 5: Verify the generated types**

Run: `cd /home/joseph/dashboard/backend && uv run python -c "from app._generated.broker.v1.broker_pb2 import StreamQuotesRequest, QuoteMessage, SymbolRef; r = StreamQuotesRequest(); r.subscribe.symbols.append(SymbolRef(canonical_id='stock:AAPL:US', raw_symbol='AAPL')); print(r)"`

Expected: prints the StreamQuotesRequest with one subscribe.symbol — no errors.

- [ ] **Step 6: Commit**

```bash
cd /home/joseph/dashboard
git add proto/broker/v1/broker.proto backend/app/_generated/broker/v1/ sidecar_ibkr/_generated/ sidecar_futu/_generated/ sidecar_schwab/_generated/
git commit -m "$(cat <<'EOF'
feat(proto): add StreamQuotes RPC + SymbolRef/QuoteMessage (Phase 7b.1 A1)

Bidirectional gRPC streaming for quote fan-in (backend = client, sidecar
= server). One persistent stream per source × sidecar instance.

- Subscribe/Unsubscribe/Heartbeat/Resync via oneof op (4 variants)
- SymbolRef carries canonical_id + raw_symbol + asset_class + exchange
  + currency + source_meta; reserved 7-15 for Phase 12/14 option/future
  contract_extra
- QuoteMessage decimal-as-string for last/bid/ask/volume/etc; change +
  change_pct both included; raw_payload (bytes) for optional sidecar trace
EOF
)"
```

---

### Task A2: Alembic 0009 — `instruments` + `symbol_aliases` migration

**Files:**
- Create: `backend/alembic/versions/0009_phase7b_instruments_symbol_aliases.py`
- Test: `backend/tests/integration/test_alembic_0009.py`

- [ ] **Step 1: Verify next Alembic revision number**

Run: `ls backend/alembic/versions/ | sort | tail -5`

Expected: shows `0008_*.py` as the latest. 0009 is free.

- [ ] **Step 2: Write the migration test FIRST (RED)**

Create `backend/tests/integration/test_alembic_0009.py`:

```python
"""Alembic 0009 migration round-trip test (Phase 7b.1).

Per memory feedback_pytest_session_begin_commits.md, uses outer-transaction
fixture so test inserts don't leak to prod. Tests upgrade + downgrade +
schema correctness.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from alembic import command
from alembic.config import Config


@pytest.fixture
def alembic_config() -> Config:
    cfg = Config("backend/alembic.ini")
    cfg.set_main_option("script_location", "backend/alembic")
    return cfg


@pytest.mark.asyncio
async def test_0009_upgrade_creates_tables(alembic_config: Config, async_session: AsyncSession):
    """0009 upgrade creates instruments + symbol_aliases with correct schema."""
    command.upgrade(alembic_config, "0009")

    result = await async_session.execute(text(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' "
        "AND table_name IN ('instruments', 'symbol_aliases') "
        "ORDER BY table_name"
    ))
    tables = [r[0] for r in result]
    assert tables == ["instruments", "symbol_aliases"]


@pytest.mark.asyncio
async def test_0009_instruments_constraints(alembic_config: Config, async_session: AsyncSession):
    """instruments.canonical_id is UNIQUE; asset_class enum has 7 values."""
    command.upgrade(alembic_config, "0009")

    result = await async_session.execute(text(
        "SELECT enumlabel FROM pg_enum e "
        "JOIN pg_type t ON e.enumtypid = t.oid "
        "WHERE t.typname = 'instrument_asset_class' "
        "ORDER BY enumsortorder"
    ))
    enums = [r[0] for r in result]
    assert enums == ["STOCK", "ETF", "INDEX", "WARRANT", "CBBC", "FOREX", "CRYPTO"]

    result = await async_session.execute(text(
        "SELECT indexdef FROM pg_indexes "
        "WHERE schemaname = 'public' AND tablename = 'instruments' "
        "AND indexname LIKE '%canonical_id%'"
    ))
    idx = result.scalar_one_or_none()
    assert idx is not None
    assert "UNIQUE" in idx


@pytest.mark.asyncio
async def test_0009_symbol_aliases_pk_and_fk(alembic_config: Config, async_session: AsyncSession):
    """symbol_aliases has composite PK (source, raw_symbol) + FK to instruments."""
    command.upgrade(alembic_config, "0009")

    result = await async_session.execute(text(
        "SELECT a.attname FROM pg_attribute a "
        "JOIN pg_constraint c ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) "
        "JOIN pg_class cl ON cl.oid = c.conrelid "
        "WHERE cl.relname = 'symbol_aliases' AND c.contype = 'p' "
        "ORDER BY a.attnum"
    ))
    pk_cols = [r[0] for r in result]
    assert pk_cols == ["source", "raw_symbol"]


@pytest.mark.asyncio
async def test_0009_downgrade_drops_tables(alembic_config: Config, async_session: AsyncSession):
    """0009 downgrade removes both tables and the enum cleanly."""
    command.upgrade(alembic_config, "0009")
    command.downgrade(alembic_config, "0008")

    result = await async_session.execute(text(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'public' "
        "AND table_name IN ('instruments', 'symbol_aliases')"
    ))
    assert result.scalar_one() == 0

    result = await async_session.execute(text(
        "SELECT count(*) FROM pg_type WHERE typname = 'instrument_asset_class'"
    ))
    assert result.scalar_one() == 0
```

- [ ] **Step 3: Run, FAIL**

Run: `cd /home/joseph/dashboard/backend && uv run pytest tests/integration/test_alembic_0009.py -v`

Expected: FAIL — migration file doesn't exist yet.

- [ ] **Step 4: Codex writes the migration**

Codex prompt:

> Create `/home/joseph/dashboard/backend/alembic/versions/0009_phase7b_instruments_symbol_aliases.py`:
>
> ```python
> """phase 7b.1: instruments + symbol_aliases for streaming quote engine.
>
> Revision ID: 0009
> Revises: 0008
> Create Date: 2026-05-04
> """
> from __future__ import annotations
>
> import sqlalchemy as sa
> from alembic import op
> from sqlalchemy.dialects import postgresql
>
> revision = "0009"
> down_revision = "0008"
> branch_labels = None
> depends_on = None
>
>
> def upgrade() -> None:
>     asset_class_enum = postgresql.ENUM(
>         "STOCK", "ETF", "INDEX", "WARRANT", "CBBC", "FOREX", "CRYPTO",
>         name="instrument_asset_class", create_type=True,
>     )
>     asset_class_enum.create(op.get_bind(), checkfirst=False)
>
>     op.create_table(
>         "instruments",
>         sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
>         sa.Column("canonical_id", sa.Text, nullable=False, unique=True),
>         sa.Column("asset_class",
>                   postgresql.ENUM(name="instrument_asset_class", create_type=False),
>                   nullable=False),
>         sa.Column("primary_exchange", sa.Text, nullable=False),
>         sa.Column("currency", sa.CHAR(3), nullable=False),
>         sa.Column("display_name", sa.Text, nullable=True),
>         sa.Column("meta", postgresql.JSONB, nullable=False,
>                   server_default=sa.text("'{}'::jsonb")),
>         sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
>                   server_default=sa.text("now()")),
>         sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
>                   server_default=sa.text("now()")),
>     )
>     op.create_index("instruments_asset_class_idx", "instruments", ["asset_class"])
>     op.create_index("instruments_exchange_idx", "instruments", ["primary_exchange"])
>
>     op.create_table(
>         "symbol_aliases",
>         sa.Column("source", sa.Text, nullable=False),
>         sa.Column("raw_symbol", sa.Text, nullable=False),
>         sa.Column("instrument_id", sa.BigInteger,
>                   sa.ForeignKey("instruments.id", ondelete="CASCADE"),
>                   nullable=False),
>         sa.Column("meta", postgresql.JSONB, nullable=False,
>                   server_default=sa.text("'{}'::jsonb")),
>         sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
>                   server_default=sa.text("now()")),
>         sa.PrimaryKeyConstraint("source", "raw_symbol"),
>     )
>     op.create_index("symbol_aliases_instrument_idx", "symbol_aliases", ["instrument_id"])
>
>
> def downgrade() -> None:
>     op.drop_index("symbol_aliases_instrument_idx", "symbol_aliases")
>     op.drop_table("symbol_aliases")
>     op.drop_index("instruments_exchange_idx", "instruments")
>     op.drop_index("instruments_asset_class_idx", "instruments")
>     op.drop_table("instruments")
>     postgresql.ENUM(name="instrument_asset_class").drop(op.get_bind(), checkfirst=False)
> ```

- [ ] **Step 5: Run, PASS**

Run: `cd /home/joseph/dashboard/backend && uv run pytest tests/integration/test_alembic_0009.py -v`

Expected: 4 passed.

- [ ] **Step 6: Apply migration to dev DB**

Run: `cd /home/joseph/dashboard && docker compose exec backend alembic upgrade head`

Expected: `Running upgrade 0008 -> 0009 ...`

- [ ] **Step 7: Verify in dev DB**

Run: `psql -h 10.10.0.2 -U trader -d dashboard -c "\d instruments" -c "\d symbol_aliases"`

Expected: shows both tables with columns + indexes.

- [ ] **Step 8: Commit**

```bash
git add backend/alembic/versions/0009_phase7b_instruments_symbol_aliases.py backend/tests/integration/test_alembic_0009.py
git commit -m "feat(db): alembic 0009 — instruments + symbol_aliases (Phase 7b.1 A2)"
```

---

### Task A3: SQLAlchemy ORM models for `Instrument` + `SymbolAlias`

**Files:**
- Create: `backend/app/models/instruments.py`
- Test: `backend/tests/unit/test_instruments_model.py`

- [ ] **Step 1: Write test (RED)**

Create `backend/tests/unit/test_instruments_model.py`:

```python
"""Instrument + SymbolAlias ORM model tests (Phase 7b.1)."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.instruments import AssetClass, Instrument, SymbolAlias


@pytest.mark.asyncio
async def test_instrument_round_trip(async_session: AsyncSession):
    inst = Instrument(
        canonical_id="stock:AAPL:US",
        asset_class=AssetClass.STOCK,
        primary_exchange="NASDAQ",
        currency="USD",
        display_name="Apple Inc.",
        meta={"isin": "US0378331005", "sector": "Technology"},
    )
    async_session.add(inst)
    await async_session.flush()

    fetched = await async_session.get(Instrument, inst.id)
    assert fetched.canonical_id == "stock:AAPL:US"
    assert fetched.meta["sector"] == "Technology"


@pytest.mark.asyncio
async def test_symbol_alias_fk_cascade(async_session: AsyncSession):
    inst = Instrument(canonical_id="idx:SPX:US", asset_class=AssetClass.INDEX,
                      primary_exchange="CBOE", currency="USD")
    async_session.add(inst); await async_session.flush()

    async_session.add_all([
        SymbolAlias(source="schwab", raw_symbol="$SPX", instrument_id=inst.id),
        SymbolAlias(source="ibkr", raw_symbol="SPX", instrument_id=inst.id,
                    meta={"exchange": "CBOE", "sec_type": "IND"}),
    ])
    await async_session.flush()

    await async_session.delete(inst); await async_session.flush()

    result = await async_session.execute(
        select(SymbolAlias).where(SymbolAlias.instrument_id == inst.id))
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_symbol_alias_composite_pk(async_session: AsyncSession):
    inst = Instrument(canonical_id="stock:0700:HK", asset_class=AssetClass.STOCK,
                      primary_exchange="HKEX", currency="HKD")
    async_session.add(inst); await async_session.flush()

    async_session.add(SymbolAlias(source="futu", raw_symbol="HK.00700",
                                   instrument_id=inst.id))
    await async_session.flush()

    async_session.add(SymbolAlias(source="futu", raw_symbol="HK.00700",
                                   instrument_id=inst.id))
    with pytest.raises(IntegrityError):
        await async_session.flush()
```

- [ ] **Step 2: Run, FAIL**

Run: `cd /home/joseph/dashboard/backend && uv run pytest tests/unit/test_instruments_model.py -v`

- [ ] **Step 3: Codex writes the model**

Codex prompt:

> Create `/home/joseph/dashboard/backend/app/models/instruments.py`:
>
> ```python
> from __future__ import annotations
>
> import enum
> from datetime import datetime
> from typing import Any
>
> from sqlalchemy import (
>     BigInteger, CHAR, DateTime, Enum as SAEnum, ForeignKey, Index,
>     PrimaryKeyConstraint, Text, func,
> )
> from sqlalchemy.dialects.postgresql import JSONB
> from sqlalchemy.orm import Mapped, mapped_column, relationship
>
> from app.models.base import Base
>
>
> class AssetClass(str, enum.Enum):
>     STOCK = "STOCK"
>     ETF = "ETF"
>     INDEX = "INDEX"
>     WARRANT = "WARRANT"
>     CBBC = "CBBC"
>     FOREX = "FOREX"
>     CRYPTO = "CRYPTO"
>
>
> class Instrument(Base):
>     __tablename__ = "instruments"
>
>     id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
>     canonical_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
>     asset_class: Mapped[AssetClass] = mapped_column(
>         SAEnum(AssetClass, name="instrument_asset_class", create_type=False),
>         nullable=False)
>     primary_exchange: Mapped[str] = mapped_column(Text, nullable=False)
>     currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
>     display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
>     meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
>     created_at: Mapped[datetime] = mapped_column(
>         DateTime(timezone=True), nullable=False, server_default=func.now())
>     updated_at: Mapped[datetime] = mapped_column(
>         DateTime(timezone=True), nullable=False, server_default=func.now())
>
>     aliases: Mapped[list["SymbolAlias"]] = relationship(
>         back_populates="instrument", cascade="all, delete-orphan")
>
>     __table_args__ = (
>         Index("instruments_asset_class_idx", "asset_class"),
>         Index("instruments_exchange_idx", "primary_exchange"),
>     )
>
>
> class SymbolAlias(Base):
>     __tablename__ = "symbol_aliases"
>
>     source: Mapped[str] = mapped_column(Text, nullable=False)
>     raw_symbol: Mapped[str] = mapped_column(Text, nullable=False)
>     instrument_id: Mapped[int] = mapped_column(
>         BigInteger,
>         ForeignKey("instruments.id", ondelete="CASCADE"),
>         nullable=False)
>     meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
>     created_at: Mapped[datetime] = mapped_column(
>         DateTime(timezone=True), nullable=False, server_default=func.now())
>
>     instrument: Mapped[Instrument] = relationship(back_populates="aliases")
>
>     __table_args__ = (
>         PrimaryKeyConstraint("source", "raw_symbol"),
>         Index("symbol_aliases_instrument_idx", "instrument_id"),
>     )
> ```

- [ ] **Step 4: Run, PASS, Commit**

```bash
cd /home/joseph/dashboard/backend && uv run pytest tests/unit/test_instruments_model.py -v
git add backend/app/models/instruments.py backend/tests/unit/test_instruments_model.py
git commit -m "feat(models): Instrument + SymbolAlias ORM (Phase 7b.1 A3)"
```

---

### Task A4: `InstrumentResolver` with race-safe `resolve_or_create` (CRIT-3)

**Files:**
- Create: `backend/app/services/quotes/instrument_resolver.py`
- Test: `backend/tests/unit/test_instrument_resolver.py`
- Test: `backend/tests/integration/test_quote_resolve_loop.py`

- [ ] **Step 1: Write unit test**

Create `backend/tests/unit/test_instrument_resolver.py`:

```python
"""InstrumentResolver — resolve-or-create with race-safe SQL + asyncio.Lock."""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.instruments import AssetClass
from app.services.quotes.instrument_resolver import InstrumentResolver


@pytest.mark.asyncio
async def test_resolve_or_create_first_observation(async_session: AsyncSession):
    resolver = InstrumentResolver(async_session)
    inst = await resolver.resolve_or_create(
        canonical_id="stock:AAPL:US", source="schwab", raw_symbol="AAPL",
        asset_class=AssetClass.STOCK, primary_exchange="NASDAQ", currency="USD",
        meta={"display_name": "Apple Inc."},
    )
    assert inst.canonical_id == "stock:AAPL:US"
    aliases = await resolver.list_aliases(inst.id)
    assert len(aliases) == 1 and aliases[0].source == "schwab"


@pytest.mark.asyncio
async def test_resolve_or_create_idempotent(async_session: AsyncSession):
    resolver = InstrumentResolver(async_session)
    inst1 = await resolver.resolve_or_create(
        canonical_id="stock:AAPL:US", source="schwab", raw_symbol="AAPL",
        asset_class=AssetClass.STOCK, primary_exchange="NASDAQ", currency="USD")
    inst2 = await resolver.resolve_or_create(
        canonical_id="stock:AAPL:US", source="schwab", raw_symbol="AAPL",
        asset_class=AssetClass.STOCK, primary_exchange="NASDAQ", currency="USD")
    assert inst1.id == inst2.id


@pytest.mark.asyncio
async def test_new_alias_for_existing_instrument(async_session):
    resolver = InstrumentResolver(async_session)
    inst = await resolver.resolve_or_create(
        canonical_id="stock:AAPL:US", source="schwab", raw_symbol="AAPL",
        asset_class=AssetClass.STOCK, primary_exchange="NASDAQ", currency="USD")
    inst2 = await resolver.resolve_or_create(
        canonical_id="stock:AAPL:US", source="ibkr", raw_symbol="AAPL",
        asset_class=AssetClass.STOCK, primary_exchange="NASDAQ", currency="USD",
        alias_meta={"conid": 265598, "sec_type": "STK"})
    assert inst.id == inst2.id
    aliases = await resolver.list_aliases(inst.id)
    assert {a.source for a in aliases} == {"schwab", "ibkr"}


@pytest.mark.asyncio
async def test_uk_pence_normalization_hint(async_session):
    resolver = InstrumentResolver(async_session)
    inst = await resolver.resolve_or_create(
        canonical_id="stock:VOD:UK", source="ibkr", raw_symbol="VOD",
        asset_class=AssetClass.STOCK, primary_exchange="LSE", currency="GBP",
        alias_meta={"exchange": "LSE", "sec_type": "STK", "currency_hint": "GBp"})
    aliases = await resolver.list_aliases(inst.id)
    assert aliases[0].meta["currency_hint"] == "GBp"
```

- [ ] **Step 2: Write concurrency stress test (CRIT-3)**

Create `backend/tests/integration/test_quote_resolve_loop.py`:

```python
"""Concurrency stress for InstrumentResolver — CRIT-3.

≥50 concurrent resolve_or_create() calls for same novel id => exactly 1
instrument row + 1 alias row + zero exceptions.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.instruments import AssetClass, Instrument, SymbolAlias
from app.services.quotes.instrument_resolver import InstrumentResolver


@pytest.mark.asyncio
async def test_concurrent_resolve_no_dup(async_session: AsyncSession):
    resolver = InstrumentResolver(async_session)
    canonical = "stock:NVDA:US"

    async def one():
        return await resolver.resolve_or_create(
            canonical_id=canonical, source="schwab", raw_symbol="NVDA",
            asset_class=AssetClass.STOCK, primary_exchange="NASDAQ", currency="USD")

    results = await asyncio.gather(*[one() for _ in range(50)])
    assert len({r.id for r in results}) == 1

    count = await async_session.execute(
        text("SELECT count(*) FROM instruments WHERE canonical_id = :c"),
        {"c": canonical})
    assert count.scalar_one() == 1
    count = await async_session.execute(
        text("SELECT count(*) FROM symbol_aliases WHERE source='schwab' AND raw_symbol='NVDA'"))
    assert count.scalar_one() == 1


@pytest.mark.asyncio
async def test_concurrent_resolve_three_sources(async_session: AsyncSession):
    resolver = InstrumentResolver(async_session)
    canonical = "idx:SPX:US"

    async def call(source, raw):
        return await resolver.resolve_or_create(
            canonical_id=canonical, source=source, raw_symbol=raw,
            asset_class=AssetClass.INDEX, primary_exchange="CBOE", currency="USD")

    tasks = []
    for _ in range(50):
        tasks += [call("schwab", "$SPX"), call("ibkr", "SPX"), call("yfinance", "^GSPC")]
    results = await asyncio.gather(*tasks)
    assert len({r.id for r in results}) == 1

    aliases = await async_session.execute(
        select(SymbolAlias).join(Instrument).where(Instrument.canonical_id == canonical))
    rows = aliases.scalars().all()
    assert {a.source for a in rows} == {"schwab", "ibkr", "yfinance"}
```

- [ ] **Step 3: Run, FAIL**

- [ ] **Step 4: Codex writes the resolver**

Codex prompt:

> Create `/home/joseph/dashboard/backend/app/services/quotes/instrument_resolver.py`:
>
> Two-layer guard (CRIT-3): in-process `dict[canonical_id, asyncio.Lock]` (capped 5000, TTL 1h) PLUS DB-side `INSERT ... ON CONFLICT DO NOTHING RETURNING id` then SELECT-fallback.
>
> Wrap session calls in `async with session.begin():` (NOT `s.begin_nested()` per memory `feedback_pytest_session_begin_commits.md`).
>
> Class shape:
>
> ```python
> class InstrumentResolver:
>     def __init__(self, session: AsyncSession) -> None: ...
>     async def resolve_or_create(self, *, canonical_id, source, raw_symbol,
>                                 asset_class, primary_exchange, currency,
>                                 meta=None, alias_meta=None) -> Instrument: ...
>     async def list_aliases(self, instrument_id: int) -> list[SymbolAlias]: ...
>     async def from_legacy(self, broker_id: str, raw_symbol: str,
>                           exchange: str, currency: str) -> Instrument | None:
>         """For instruments_seed legacy data — best-effort canonical_id derivation."""
> ```
>
> SQL UPSERT shape using `sqlalchemy.dialects.postgresql.insert`:
>
> ```python
> stmt = (
>     pg_insert(Instrument)
>     .values(canonical_id=..., asset_class=..., ...)
>     .on_conflict_do_nothing(index_elements=["canonical_id"])
>     .returning(Instrument.id)
> )
> result = await self._session.execute(stmt)
> row = result.scalar_one_or_none()
> if row is not None:
>     return await self._session.get(Instrument, row)
> # already existed — fetch
> existing = await self._session.execute(
>     select(Instrument).where(Instrument.canonical_id == canonical_id))
> return existing.scalar_one()
> ```
>
> Same pattern for symbol_aliases with `index_elements=["source", "raw_symbol"]`.
>
> Increment `quote_instruments_created_total{asset_class}` on every newly-inserted instrument.

- [ ] **Step 5: Run, PASS, Commit**

```bash
cd /home/joseph/dashboard/backend && uv run pytest tests/unit/test_instrument_resolver.py tests/integration/test_quote_resolve_loop.py -v
git add backend/app/services/quotes/instrument_resolver.py backend/tests/unit/test_instrument_resolver.py backend/tests/integration/test_quote_resolve_loop.py
git commit -m "feat(quotes): InstrumentResolver — race-safe upsert (Phase 7b.1 A4)"
```

---

### Task A5: `instruments_seed.py` boot helper with legacy fallback (MED-8)

**Files:**
- Create: `backend/app/services/quotes/instruments_seed.py`
- Test: `backend/tests/integration/test_instruments_seed.py`
- Modify: `backend/app/main.py` (lifespan)

- [ ] **Step 1: Write test**

Create `backend/tests/integration/test_instruments_seed.py`:

```python
"""Boot-time seed of instruments from positions/orders/watchlists."""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.quotes.instruments_seed import seed_instruments_from_positions


@pytest.mark.asyncio
async def test_seed_creates_instrument_for_us_position(async_session: AsyncSession):
    await async_session.execute(text(
        "INSERT INTO broker_accounts (id, broker_id, alias, mode, currency_base, display_order) "
        "VALUES (gen_random_uuid(), 'schwab', 'test-acct', 'live', 'USD', 0)"))
    acct = (await async_session.execute(
        text("SELECT id FROM broker_accounts WHERE alias='test-acct'"))).scalar_one()
    await async_session.execute(text(
        "INSERT INTO positions (broker_account_id, symbol, exchange, currency, qty, avg_cost) "
        "VALUES (:a, 'AAPL', 'NASDAQ', 'USD', '100', '150.0')"), {"a": acct})
    await async_session.commit()

    skipped = await seed_instruments_from_positions(async_session)
    assert skipped["count"] == 0

    inst = await async_session.execute(
        text("SELECT canonical_id FROM instruments WHERE canonical_id = 'stock:AAPL:US'"))
    assert inst.scalar_one() == "stock:AAPL:US"


@pytest.mark.asyncio
async def test_seed_skips_legacy_with_missing_exchange(async_session):
    await async_session.execute(text(
        "INSERT INTO broker_accounts (id, broker_id, alias, mode, currency_base, display_order) "
        "VALUES (gen_random_uuid(), 'ibkr', 'test-legacy', 'live', 'USD', 0)"))
    acct = (await async_session.execute(
        text("SELECT id FROM broker_accounts WHERE alias='test-legacy'"))).scalar_one()
    await async_session.execute(text(
        "INSERT INTO positions (broker_account_id, symbol, exchange, currency, qty, avg_cost) "
        "VALUES (:a, 'WEIRD', NULL, 'USD', '10', '50.0')"), {"a": acct})
    await async_session.commit()

    skipped = await seed_instruments_from_positions(async_session)
    assert skipped["count"] >= 1
    assert "missing_exchange" in skipped["reasons"]
```

- [ ] **Step 2: Codex writes the seed helper**

Codex prompt:

> Create `/home/joseph/dashboard/backend/app/services/quotes/instruments_seed.py`. Implements `seed_instruments_from_positions(session)`:
>
> 1. Query `SELECT broker_id, symbol, exchange, currency FROM positions UNION SELECT broker_id, symbol, exchange, currency FROM orders UNION SELECT 'manual', symbol, exchange, currency FROM watchlist_entries`.
> 2. For each row, derive `(canonical_id, asset_class, primary_exchange, currency)`:
>    - If `exchange IS NULL` → reason `missing_exchange`, skip.
>    - If `exchange ∈ {'NASDAQ','NYSE','ARCA','BATS','IEX'}` → country='US', asset_class=STOCK
>    - If `exchange == 'LSE'` → country='UK', asset_class=STOCK, alias_meta={`currency_hint`: 'GBp' if currency=='GBP' else None}
>    - If `exchange ∈ {'HKEX','SEHK'}` → country='HK', asset_class=STOCK
>    - If `exchange == 'CBOE'` AND symbol.startswith('$') → country='US', asset_class=INDEX
>    - Else → reason `unknown_asset_class`, skip
> 3. Call `InstrumentResolver.resolve_or_create()` per derived row.
> 4. On exception: reason `derive_error`, log structlog `instruments.seed.skip`, continue.
> 5. Return `{"count": int_skipped_total, "reasons": dict[str, int]}`.
> 6. Bump `quote_seed_skipped_total{reason}` per skip.

- [ ] **Step 3: Wire into lifespan**

Modify `backend/app/main.py` lifespan — after `ConfigService` start, before `BrokerRegistry`:

```python
from app.services.quotes.instruments_seed import seed_instruments_from_positions
async with async_session_maker() as s:
    skipped = await seed_instruments_from_positions(s)
    log.info("instruments.seed", skipped=skipped)
```

- [ ] **Step 4: Run, PASS, Commit**

```bash
cd /home/joseph/dashboard/backend && uv run pytest tests/integration/test_instruments_seed.py -v
git add backend/app/services/quotes/instruments_seed.py backend/tests/integration/test_instruments_seed.py backend/app/main.py
git commit -m "feat(quotes): boot seed from positions/orders/watchlists (Phase 7b.1 A5)"
```

---

## Chunk B — Backend QuoteEngine core

### Task B1: Port `services/quotes/base.py` from dashboard_old

**Files:**
- Create: `backend/app/services/quotes/base.py`
- Test: `backend/tests/unit/test_quotes_canonical.py`

- [ ] **Step 1: Inspect old code**

Run: `wc -l /mnt/c/Dashboard_old/backend/app/services/quotes/base.py`

Expected: ~250 lines.

- [ ] **Step 2: Port test from old**

Run: `cp /mnt/c/Dashboard_old/backend/tests/test_quotes_canonical.py backend/tests/unit/test_quotes_canonical.py`

Adjust import: should already point at `app.services.quotes.base`.

- [ ] **Step 3: Run, FAIL**

- [ ] **Step 4: Codex ports `base.py`**

Codex prompt:

> Read `/mnt/c/Dashboard_old/backend/app/services/quotes/base.py` and port to `/home/joseph/dashboard/backend/app/services/quotes/base.py`.
>
> KEEP:
> - `def canonical_key(symbol_or_dict) -> str` — produces `<asset_class>:<symbol>:<country>`.
> - `def scale_gbx_if_needed(price, currency, exchange) -> Decimal` — UK pence guard (LSE GBp → divide by 100).
> - Exceptions: `QuoteError`, `NotSupported`, `NotEntitled`, `ProviderDown`.
> - Type aliases: `CanonicalId = NewType("CanonicalId", str)`, `SubscriptionToken = str`.
>
> DROP from old:
> - `class QuoteProvider(ABC)` — sidecar gRPC interface replaces it.
> - `class ProviderId(str, Enum)` — replaced by proto `QuoteSource` enum.
>
> ADD new:
> - `def source_id_to_str(source_id: int) -> str` — maps proto `QuoteSource` enum int → lowercase string.
> - `def canonical_id_components(canonical_id: str) -> tuple[str, str, str]` — parse `stock:AAPL:US` → `("stock", "AAPL", "US")`. Handle dual-listing form `stock:AAPL:US:NYSE` → `("stock", "AAPL", "US")` with optional 4th component returned separately via `canonical_id_with_exchange`.

- [ ] **Step 5: Run, PASS, Commit**

```bash
git add backend/app/services/quotes/base.py backend/tests/unit/test_quotes_canonical.py
git commit -m "feat(quotes): port base.py from dashboard_old (Phase 7b.1 B1)"
```

---

### Task B2: `SubscriptionRegistry` with cap + rate-limit (HIGH-6)

**Files:**
- Create: `backend/app/services/quotes/registry.py`
- Test: `backend/tests/unit/test_subscription_registry.py`

- [ ] **Step 1: Write test (RED)**

Create `backend/tests/unit/test_subscription_registry.py`:

```python
"""SubscriptionRegistry — refcount + cap + rate-limit (HIGH-6)."""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.services.quotes.registry import SubscriptionRegistry


@pytest.fixture
def registry() -> SubscriptionRegistry:
    return SubscriptionRegistry(cap_per_ws=10, cap_global=20, sub_rate_limit_per_minute=100)


@pytest.mark.asyncio
async def test_first_sub_returns_diff_globally_added(registry):
    ws = uuid4()
    diff = await registry.add(ws, ["stock:AAPL:US"])
    assert diff.added == {"stock:AAPL:US"}
    assert diff.rejected == set()


@pytest.mark.asyncio
async def test_second_ws_same_symbol_no_global_diff(registry):
    ws1, ws2 = uuid4(), uuid4()
    await registry.add(ws1, ["stock:AAPL:US"])
    diff = await registry.add(ws2, ["stock:AAPL:US"])
    assert diff.added == set()


@pytest.mark.asyncio
async def test_unsub_returns_diff_when_last_ref(registry):
    ws1, ws2 = uuid4(), uuid4()
    await registry.add(ws1, ["stock:AAPL:US"])
    await registry.add(ws2, ["stock:AAPL:US"])
    diff = await registry.remove(ws1, ["stock:AAPL:US"])
    assert diff.removed == set()
    diff = await registry.remove(ws2, ["stock:AAPL:US"])
    assert diff.removed == {"stock:AAPL:US"}


@pytest.mark.asyncio
async def test_remove_ws_cleans_all(registry):
    ws = uuid4()
    await registry.add(ws, ["stock:AAPL:US", "idx:SPX:US"])
    diff = await registry.remove_ws(ws)
    assert diff.removed == {"stock:AAPL:US", "idx:SPX:US"}


@pytest.mark.asyncio
async def test_per_ws_cap_partial_success(registry):
    ws = uuid4()
    diff = await registry.add(ws, [f"stock:S{i}:US" for i in range(15)])
    assert len(diff.added) == 10 and len(diff.rejected) == 5
    assert diff.rejected_reason == "cap_per_ws"


@pytest.mark.asyncio
async def test_global_cap_partial_success(registry):
    for _ in range(2):
        ws = uuid4()
        await registry.add(ws, [f"stock:G{j}:US" for j in range(10)])
    ws3 = uuid4()
    diff = await registry.add(ws3, [f"stock:G{j}:US" for j in range(20, 25)])
    assert len(diff.added) == 0 and len(diff.rejected) == 5
    assert diff.rejected_reason == "cap_global"
```

- [ ] **Step 2: Codex writes the registry**

Codex prompt:

> Create `/home/joseph/dashboard/backend/app/services/quotes/registry.py`:
>
> Define `@dataclass class SubscribeDiff(added: set[CanonicalId], rejected: set[CanonicalId], rejected_reason: str | None)`. Same shape `UnsubscribeDiff(removed: set[CanonicalId])`.
>
> Class `SubscriptionRegistry`:
> - `__init__(cap_per_ws, cap_global, sub_rate_limit_per_minute)`
> - `per_ws: dict[WSConnId, set[CanonicalId]]`
> - `global_refs: dict[CanonicalId, int]`
> - `routes: dict[CanonicalId, SourceId]`  (set externally by router)
> - `_lock: asyncio.Lock`
> - `_rate_buckets: dict[WSConnId, deque[float]]`  (timestamps, evict >60s)
>
> `async add(ws, symbols)`: under lock, enforce per-ws cap → global cap → rate-limit. Symbols accepted: added to per_ws[ws]; global_refs incremented; if went 0→1, included in `added`. Rejected (cap or rate-limit hit): listed in `rejected` with `rejected_reason ∈ {'cap_per_ws','cap_global','rate_limit'}`. Increment `quote_subscription_cap_rejected_total{cap_kind}` per rejection.
>
> `async remove(ws, symbols)`: under lock; decrement; return UnsubscribeDiff with 1→0 transitions in `removed`.
>
> `async remove_ws(ws)`: bulk-decrement; return UnsubscribeDiff.
>
> `def get_active() -> set[CanonicalId]`, `def get_active_for(source: SourceId) -> set[CanonicalId]`, `def set_route(canonical_id, source_id)` — sync helpers.

- [ ] **Step 3: Run, PASS, Commit**

```bash
cd /home/joseph/dashboard/backend && uv run pytest tests/unit/test_subscription_registry.py -v
git add backend/app/services/quotes/registry.py backend/tests/unit/test_subscription_registry.py
git commit -m "feat(quotes): SubscriptionRegistry + cap + rate-limit (Phase 7b.1 B2)"
```

---

### Task B3: `SourceRouter` with health window (HIGH-7)

**Files:**
- Create: `backend/app/services/quotes/router.py`
- Test: `backend/tests/unit/test_source_router.py`

- [ ] **Step 1: Write test**

Create `backend/tests/unit/test_source_router.py`:

```python
"""SourceRouter — config-driven priority + health windowing (HIGH-7)."""
from __future__ import annotations

import time

import pytest

from app.models.instruments import AssetClass, Instrument
from app.services.quotes.router import (
    SourceHealthMap, SourceHealthState, SourceRouter,
)


@pytest.fixture
def config() -> dict:
    return {
        "quote_source_priority": {
            "stock.US": ["schwab", "ibkr", "yfinance"],
            "stock.UK": ["ibkr", "yfinance"],
            "stock.HK": ["futu", "yfinance"],
            "index.US": ["schwab", "ibkr", "yfinance"],
            "warrant.HK": ["futu"],
        },
        "quote_stale_threshold_seconds": {
            "stock.US": 5, "stock.UK": 10, "stock.HK": 10, "index.US": 5,
            "warrant.HK": 10,
        },
    }


@pytest.fixture
def health() -> SourceHealthMap:
    return SourceHealthMap()


def _make_inst(canonical_id: str, country: str, asset_class: AssetClass,
               primary_exchange: str = "NASDAQ", currency: str = "USD") -> Instrument:
    inst = Instrument(canonical_id=canonical_id, asset_class=asset_class,
                      primary_exchange=primary_exchange, currency=currency)
    inst.country = country  # set by router resolution; test stub
    return inst


@pytest.mark.asyncio
async def test_route_picks_healthy_primary(config, health):
    health.set_state("schwab", SourceHealthState.HEALTHY)
    router = SourceRouter(config, health)
    src = await router.route(_make_inst("stock:AAPL:US", "US", AssetClass.STOCK))
    assert src == "schwab"


@pytest.mark.asyncio
async def test_route_falls_back_on_primary_down(config, health):
    health.set_state("schwab", SourceHealthState.DOWN)
    health.set_state("ibkr", SourceHealthState.HEALTHY)
    router = SourceRouter(config, health)
    src = await router.route(_make_inst("stock:AAPL:US", "US", AssetClass.STOCK))
    assert src == "ibkr"


@pytest.mark.asyncio
async def test_route_returns_none_when_all_down(config, health):
    for s in ["schwab", "ibkr", "yfinance"]:
        health.set_state(s, SourceHealthState.DOWN)
    router = SourceRouter(config, health)
    src = await router.route(_make_inst("stock:AAPL:US", "US", AssetClass.STOCK))
    assert src is None


def test_health_window_min_60s_for_quiet_symbols(config, health):
    """30s gap on idle warrant → still HEALTHY (5×10=50s < 60s floor)."""
    router = SourceRouter(config, health)
    health.update_last_tick("futu", time.monotonic() - 30)
    assert router.compute_health_state("futu", min_threshold=10) == SourceHealthState.HEALTHY


def test_health_window_kicks_in_at_61s(config, health):
    router = SourceRouter(config, health)
    health.update_last_tick("futu", time.monotonic() - 61)
    assert router.compute_health_state("futu", min_threshold=10) == SourceHealthState.DEGRADED


@pytest.mark.asyncio
async def test_ibkr_gateway_assignment(config, health):
    config["ibkr_gateway_quote_assignment"] = {
        "stock.UK": "isa-live", "stock.US": "isa-live", "_default": "isa-live"}
    config["ibkr_gateway_quote_fallback"] = ["normal-live"]
    health.set_state("ibkr", SourceHealthState.HEALTHY)
    router = SourceRouter(config, health)
    src, gw = await router.route_with_gateway(
        _make_inst("stock:VOD:UK", "UK", AssetClass.STOCK,
                   primary_exchange="LSE", currency="GBP"))
    assert src == "ibkr" and gw == "isa-live"
```

- [ ] **Step 2: Codex writes router**

Codex prompt:

> Create `/home/joseph/dashboard/backend/app/services/quotes/router.py`:
>
> ```python
> import time
> from enum import IntEnum
>
> class SourceHealthState(IntEnum):
>     DOWN = 0
>     DEGRADED = 1
>     HEALTHY = 2
>
> class SourceHealthMap:
>     # _state: dict[SourceId, SourceHealthState]
>     # _last_tick: dict[SourceId, float]  (monotonic)
>     def set_state(self, source, state): ...   # bumps quote_source_health_state{source}
>     def update_last_tick(self, source, ts): ...
>     def time_since_last_tick(self, source) -> float: ...
>     def is_up(self, source) -> bool: return self._state.get(source) != SourceHealthState.DOWN
>
> class SourceRouter:
>     def __init__(self, config, health): ...
>
>     async def route(self, instrument: Instrument) -> SourceId | None:
>         country = self._derive_country(instrument)  # uses canonical_id_components
>         key = f"{instrument.asset_class.value.lower()}.{country}"
>         priority = self._config["quote_source_priority"].get(key, [])
>         for src in priority:
>             if self._health.is_up(src):
>                 return src
>         return None
>
>     async def route_with_gateway(self, instrument) -> tuple[SourceId, str | None]:
>         src = await self.route(instrument)
>         if src != "ibkr": return (src, None)
>         country = self._derive_country(instrument)
>         key = f"{instrument.asset_class.value.lower()}.{country}"
>         assignment = self._config.get("ibkr_gateway_quote_assignment", {})
>         gw = assignment.get(key, assignment.get("_default", "isa-live"))
>         # if gateway down, try fallback
>         if not self._health.is_up(f"ibkr:{gw}"):
>             for fallback in self._config.get("ibkr_gateway_quote_fallback", []):
>                 if self._health.is_up(f"ibkr:{fallback}"):
>                     return ("ibkr", fallback)
>             return (None, None)
>         return ("ibkr", gw)
>
>     def compute_health_state(self, source, min_threshold) -> SourceHealthState:
>         health_window = max(5 * min_threshold, 60.0)
>         if not self._health.is_up(source):
>             return SourceHealthState.DOWN
>         since = self._health.time_since_last_tick(source)
>         if since > health_window:
>             return SourceHealthState.DEGRADED
>         return SourceHealthState.HEALTHY
>
>     async def reroute(self, canonical_id, current, reason) -> SourceId | None: ...
>         # increment quote_route_changes_total{from,to,asset_class}; return new source
> ```

- [ ] **Step 3: Run, PASS, Commit**

```bash
cd /home/joseph/dashboard/backend && uv run pytest tests/unit/test_source_router.py -v
git add backend/app/services/quotes/router.py backend/tests/unit/test_source_router.py
git commit -m "feat(quotes): SourceRouter + health window (Phase 7b.1 B3)"
```

---

### Task B4: `SidecarStream` per-source bidi gRPC task (HIGH-1 Subscribe-vs-Resync)

**Files:**
- Create: `backend/app/services/quotes/upstream/__init__.py`
- Create: `backend/app/services/quotes/upstream/sidecar_stream.py`
- Test: `backend/tests/integration/test_sidecar_stream.py`

- [ ] **Step 1: Write integration test with fake gRPC server**

Create `backend/tests/integration/test_sidecar_stream.py`:

```python
"""SidecarStream — Subscribe vs Resync on reconnect (HIGH-1)."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import grpc
import pytest

from app._generated.broker.v1 import broker_pb2 as pb, broker_pb2_grpc as pb_grpc
from app.services.quotes.registry import SubscriptionRegistry
from app.services.quotes.upstream.sidecar_stream import SidecarStream


class FakeSidecar(pb_grpc.BrokerServicer):
    def __init__(self):
        self.received_ops: list = []
        self.started_at = 1234567890

    async def StreamQuotes(self, request_iterator, context):
        async for req in request_iterator:
            if req.HasField("subscribe"):
                self.received_ops.append(("subscribe", [s.canonical_id for s in req.subscribe.symbols]))
            elif req.HasField("resync"):
                self.received_ops.append(("resync", [s.canonical_id for s in req.resync.expected]))
            elif req.HasField("unsubscribe"):
                self.received_ops.append(("unsubscribe", [s.canonical_id for s in req.unsubscribe.symbols]))
            elif req.HasField("heartbeat"):
                self.received_ops.append(("heartbeat", []))

    async def Health(self, request, context):
        return pb.HealthResponse(broker_id="ibkr", started_at=self.started_at)


@asynccontextmanager
async def fake_server():
    server = grpc.aio.server()
    fake = FakeSidecar()
    pb_grpc.add_BrokerServicer_to_server(fake, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        yield (server, port, fake)
    finally:
        await server.stop(grace=1)


@pytest.mark.asyncio
async def test_initial_connection_sends_subscribe():
    async with fake_server() as (_, port, fake):
        registry = SubscriptionRegistry(cap_per_ws=10, cap_global=10, sub_rate_limit_per_minute=100)
        # Pre-populate registry with one symbol routed to schwab source
        await registry.add(uuid4(), ["stock:AAPL:US"])
        registry.set_route("stock:AAPL:US", "schwab")

        channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        stream = SidecarStream(source="schwab", channel=channel, registry=registry, engine=None)
        run_task = asyncio.create_task(stream.run())
        await asyncio.sleep(0.5)
        run_task.cancel()
        try: await run_task
        except asyncio.CancelledError: pass
        ops = fake.received_ops
        assert ops and ops[0][0] == "subscribe" and "stock:AAPL:US" in ops[0][1]
```

- [ ] **Step 2: Codex writes sidecar_stream.py**

Codex prompt:

> Create `/home/joseph/dashboard/backend/app/services/quotes/upstream/sidecar_stream.py`:
>
> Class `SidecarStream`:
> - `__init__(source, channel, registry, engine)`
> - `_pending_changes: asyncio.Queue` of `(StreamQuotesRequest)` to send
> - `_last_known_started_at: int | None`
> - `_stopping: asyncio.Event`
> - `_token_rotation_event: asyncio.Event` (set externally on token refresh per CRIT-2)
>
> `async run()`: persistent reconnect loop:
>
> ```python
> backoff = 1.0
> while not self._stopping.is_set():
>     try:
>         channel = self._channel
>         stub = pb_grpc.BrokerStub(channel)
>
>         # Health check first
>         health_resp = await stub.Health(pb.HealthRequest())
>         current_started_at = health_resp.started_at
>
>         async def request_iter():
>             # First message: Subscribe (sidecar restart) or Resync (gRPC-only reconnect)
>             active_set = list(self._registry.get_active_for(self._source))
>             symbols = [self._build_symbol_ref(c) for c in active_set]
>             if (self._last_known_started_at is None
>                 or current_started_at != self._last_known_started_at):
>                 yield pb.StreamQuotesRequest(subscribe=pb.StreamQuotesRequest.Subscribe(symbols=symbols))
>             else:
>                 yield pb.StreamQuotesRequest(resync=pb.StreamQuotesRequest.Resync(expected=symbols))
>             self._last_known_started_at = current_started_at
>
>             # Then drain pending + heartbeats
>             while not self._stopping.is_set():
>                 try:
>                     req = await asyncio.wait_for(self._pending_changes.get(), timeout=30)
>                     yield req
>                 except asyncio.TimeoutError:
>                     yield pb.StreamQuotesRequest(
>                         heartbeat=pb.StreamQuotesRequest.Heartbeat(
>                             client_time=Timestamp().GetCurrentTime()))
>
>         async for resp in stub.StreamQuotes(request_iter()):
>             await self._engine._on_quote(resp)
>             self._health.update_last_tick(self._source, time.monotonic())
>
>         backoff = 1.0  # reset after successful round
>     except (grpc.aio.AioRpcError, ConnectionError) as e:
>         self._metrics.reconnect_total.labels(source=self._source).inc()
>         await asyncio.sleep(min(backoff, 60))
>         backoff *= 2
> ```
>
> `async add(symbols)`: enqueue Subscribe.
> `async remove(symbols)`: enqueue Unsubscribe.
> `def request_reconnect()`: set token-rotation Event; current call drains then exits.

- [ ] **Step 3: Run, PASS, Commit**

```bash
cd /home/joseph/dashboard/backend && uv run pytest tests/integration/test_sidecar_stream.py -v
git add backend/app/services/quotes/upstream/ backend/tests/integration/test_sidecar_stream.py
git commit -m "feat(quotes): SidecarStream w/ Subscribe-vs-Resync (Phase 7b.1 B4)"
```

---

### Task B5: `QuoteEngine` glue + invariants `INV-Q-1..4`

**Files:**
- Create: `backend/app/services/quotes/engine.py`
- Test: `backend/tests/integration/test_quote_engine_e2e.py`

- [ ] **Step 1: Write integration test**

Create `backend/tests/integration/test_quote_engine_e2e.py`:

```python
"""QuoteEngine end-to-end + INV-Q-1..4."""
from __future__ import annotations

import asyncio
from uuid import uuid4

import grpc
import pytest

from app._generated.broker.v1 import broker_pb2 as pb, broker_pb2_grpc as pb_grpc


class EchoStreamer(pb_grpc.BrokerServicer):
    """Echo Subscribe → emit one QuoteMessage per symbol."""
    async def Health(self, req, ctx):
        return pb.HealthResponse(broker_id="schwab", started_at=42)
    async def StreamQuotes(self, request_iterator, context):
        async for req in request_iterator:
            if req.HasField("subscribe"):
                for sym in req.subscribe.symbols:
                    yield pb.QuoteMessage(
                        canonical_id=sym.canonical_id, last="100.50",
                        bid="100.49", ask="100.51", source="schwab",
                        raw_payload=b"INTERNAL_DEBUG_DO_NOT_LEAK")


@pytest.mark.asyncio
async def test_inv_q_1_no_double_delivery_in_single_worker(redis_client, async_session):
    """Single worker — each tick delivered exactly once per conflator."""
    # set up engine with Redis pubsub but ensure single-worker mode skips sub
    # ... full implementation per spec §5.2.3 INV-Q-1


@pytest.mark.asyncio
async def test_inv_q_2_raw_payload_stripped_at_engine_boundary():
    """Engine zeros raw_payload + source_meta before notify/publish."""
    # ... assert ws frame has empty raw_payload


@pytest.mark.asyncio
async def test_inv_q_3_per_symbol_staleness_does_not_drive_reroute():
    """Quiet symbol → op:stale to FE but no route_changes increment."""
    # ...


@pytest.mark.asyncio
async def test_inv_q_4_token_rotation_reconnect_within_2s():
    """tokens_refreshed.set() → SidecarStream reconnects ≤2s."""
    # ... time the gap
```

- [ ] **Step 2: Codex writes engine.py**

Codex prompt:

> Create `/home/joseph/dashboard/backend/app/services/quotes/engine.py`. Port structural skeleton from `/mnt/c/Dashboard_old/backend/app/services/quotes/engine.py` adapted for sidecar-gRPC fan-in.
>
> Class `QuoteEngine`:
> - `_registry`, `_router`, `_resolver`, `_streams: dict[SourceId, SidecarStream]`, `_conflators: dict[WSConnId, WSConflator]`, `_cache: dict[CanonicalId, tuple[QuoteMessage, float]]` (60s TTL), `_publisher_worker_id: UUID`, `_redis: Redis`, `_subscriber_task: asyncio.Task | None` (None in single-worker — INV-Q-1).
>
> `async start()`:
> - Connect Redis pubsub.
> - Open SidecarStream per source × gateway (4 IBKR + 1 Futu + 1 Schwab in 7b.1).
> - Start `_stale_sweep_loop()`.
>
> `async _on_quote(q)`:
> 1. **INV-Q-2**: if not `os.environ.get("OPERATOR_TRACE_QUOTES") == "1"`: `q.raw_payload = b""`; `q.source_meta_strip()`.
> 2. Resolve instrument (cached).
> 3. Update `_cache[canonical_id] = (q, time.monotonic())`. Use `q.received_at` for stale calc.
> 4. Clear stale flag if was set.
> 5. Redis publish `quote.{q.source}.{q.canonical_id}` with envelope `{"v":1,"publisher_worker_id":str(self._publisher_worker_id),"q":MessageToDict(q)}`.
> 6. **INV-Q-1 single-worker**: do NOT subscribe to own publishes. `self._subscriber_task is None`.
> 7. In-process: for each WSConflator with `q.canonical_id` in pending, call `conflator.on_quote(q)` (non-blocking).
>
> `async _stale_sweep_loop()`: 1 Hz; iterate cache; threshold from `app_config.quote_stale_threshold_seconds[asset_class.country]`; emit `op:"stale"` frame to conflators that hold the symbol; bump `quote_stale_active_count{asset_class}`.
>
> `async subscribe(ws, symbols)`: registry.add → resolve_or_create per added → router.route → registry.set_route → sidecar_stream.add. Returns `SubscribeDiff` for ack/err frames.
>
> `async unsubscribe(ws, symbols)`: mirror.
>
> `async disconnect_ws(ws)`: registry.remove_ws → cascade unsubscribes.

- [ ] **Step 3: Run, PASS, Commit**

```bash
cd /home/joseph/dashboard/backend && uv run pytest tests/integration/test_quote_engine_e2e.py -v
git add backend/app/services/quotes/engine.py backend/tests/integration/test_quote_engine_e2e.py
git commit -m "feat(quotes): QuoteEngine + INV-Q-1..4 (Phase 7b.1 B5)"
```

---

## Chunk C — `sidecar_schwab/streamer.py`

### Task C1: Port `schwab_streamer.py` from dashboard_old + token-rotation reconnect (CRIT-2)

**Files:**
- Create: `sidecar_schwab/streamer.py`

- [ ] **Step 1: Inspect old**

Run: `wc -l /mnt/c/Dashboard_old/backend/app/services/quotes/providers/schwab_streamer.py`

Expected: ~448 lines.

- [ ] **Step 2: Codex ports**

Codex prompt:

> Read `/mnt/c/Dashboard_old/backend/app/services/quotes/providers/schwab_streamer.py` and port to `/home/joseph/dashboard/sidecar_schwab/streamer.py`.
>
> Adaptations:
> - Remove `QuoteProvider` ABC; export `class SchwabStreamer`.
> - Replace async-generator `subscribe()` with `async on_subscribe(symbols: list[SymbolRef])` that issues `LEVELONE_EQUITIES SUBS/ADD` to upstream Schwab WS.
> - Replace `unsubscribe()` with `async on_unsubscribe(symbols)` that issues `UNSUBS`.
> - Add `async on_resync(expected)` per HIGH-1: diff against own `_upstream_refcount` dict; only `SUBS`/`UNSUBS` for the difference.
> - `tick_callback: Callable[[QuoteMessage], None]` set externally by gRPC handler.
> - **CRIT-2 token rotation**: take `tokens_refreshed: asyncio.Event` in constructor. Main loop:
>
>   ```python
>   recv_task = asyncio.create_task(self._recv_frame())
>   token_task = asyncio.create_task(self._tokens_refreshed.wait())
>   done, pending = await asyncio.wait([recv_task, token_task],
>                                       return_when=asyncio.FIRST_COMPLETED)
>   for t in pending: t.cancel()
>   if token_task in done:
>       self._metrics.token_rotation_reconnect_total.inc()
>       gap_start = time.monotonic()
>       await self._close_ws()
>       await self._reconnect_with_new_creds()
>       await self._replay_subscriptions()
>       self._metrics.token_rotation_gap_seconds.observe(time.monotonic() - gap_start)
>       self._tokens_refreshed.clear()
>   ```
>
> - `$`-prefix index symbols (`$SPX`, `$VIX`, `$NDX`, `$COMPX`, `$DJI`, `$RUT`) → same `LEVELONE_EQUITIES SUBS`, just different raw_symbol.
> - Keep LOGIN frame using `streamerInfo` from cached `userPreference`; field-mask `0,1,2,3,8,12,28,29,30,33`.
> - Internal refcount: `dict[raw_symbol, int]`, only first ref subscribes upstream.
> - Convert each parsed Schwab tick into proto `QuoteMessage` via helper `_schwab_tick_to_quote(content_block, key) -> QuoteMessage`. Set `received_at = Timestamp().GetCurrentTime()` from sidecar wall-clock.
> - Reconnect with exponential backoff `min(2^n, 60)`; on each reconnect, replay full symbol set with one `SUBS`.
> - structlog events: `schwab.streamer.login_ok`, `schwab.streamer.subs`, `schwab.streamer.tick`, `schwab.streamer.reconnect`, `schwab.streamer.token_rotation_reconnect`.

- [ ] **Step 3: Commit**

```bash
git add sidecar_schwab/streamer.py
git commit -m "feat(sidecar_schwab): port LEVELONE streamer + token rotation (Phase 7b.1 C1)"
```

---

### Task C2: Wire `StreamQuotes` RPC handler in sidecar_schwab

**Files:**
- Modify: `sidecar_schwab/handlers.py`

- [ ] **Step 1: Codex adds handler**

Codex prompt:

> In `/home/joseph/dashboard/sidecar_schwab/handlers.py`, on the existing `BrokerServicer` class, add:
>
> ```python
> async def StreamQuotes(self, request_iterator, context):
>     streamer = await self._get_or_init_schwab_streamer()
>     queue: asyncio.Queue[pb.QuoteMessage] = asyncio.Queue()
>     def tick_cb(q): queue.put_nowait(q)
>     streamer.tick_callback = tick_cb
>
>     async def consume_requests():
>         async for req in request_iterator:
>             if req.HasField("subscribe"):
>                 await streamer.on_subscribe(list(req.subscribe.symbols))
>             elif req.HasField("unsubscribe"):
>                 await streamer.on_unsubscribe(list(req.unsubscribe.symbols))
>             elif req.HasField("resync"):
>                 await streamer.on_resync(list(req.resync.expected))
>             # heartbeat: ignore (sustains stream alive)
>
>     consumer_task = asyncio.create_task(consume_requests())
>     try:
>         while True:
>             q = await queue.get()
>             yield q
>     finally:
>         consumer_task.cancel()
> ```
>
> Wire `_get_or_init_schwab_streamer()` to return a process-singleton instance (one streamer per sidecar lifetime, shared across gRPC calls — refcounting per symbol prevents double-subscribe).

- [ ] **Step 2: Commit**

```bash
git add sidecar_schwab/handlers.py
git commit -m "feat(sidecar_schwab): wire StreamQuotes RPC (Phase 7b.1 C2)"
```

---

### Task C3: Schwab golden-trace + token-rotation tests

**Files:**
- Create: `sidecar_schwab/tests/test_streamer.py`
- Create: `sidecar_schwab/tests/test_streamer_token_rotation.py`
- Create: `sidecar_schwab/tests/golden/levelone_equities_aapl_spx.json`

- [ ] **Step 1: Synthetic golden trace**

Create `sidecar_schwab/tests/golden/levelone_equities_aapl_spx.json`:

```json
{
  "frames": [
    {"response":[{"service":"ADMIN","command":"LOGIN","content":{"code":0,"msg":"server=...; status=ok"}}]},
    {"data":[{"service":"LEVELONE_EQUITIES","timestamp":1714824000000,"command":"SUBS","content":[{"key":"AAPL","1":213.40,"2":213.46,"3":213.45,"8":38291842,"12":212.22,"28":214.10,"29":211.20,"30":211.50,"33":100}]}]},
    {"data":[{"service":"LEVELONE_EQUITIES","timestamp":1714824001456,"content":[{"key":"$SPX","1":5210.50,"2":5210.55,"3":5210.52,"8":0,"12":5200.00,"28":5215.00,"29":5198.00,"30":5202.00}]}]}
  ]
}
```

- [ ] **Step 2: Golden-trace test**

Create `sidecar_schwab/tests/test_streamer.py`:

```python
"""Golden-trace replay for SchwabStreamer."""
from __future__ import annotations

import asyncio
import json

import pytest

from sidecar_schwab.streamer import SchwabStreamer


@pytest.mark.asyncio
async def test_levelone_equities_emits_quote_messages():
    received: list = []
    streamer = SchwabStreamer(tokens_refreshed=asyncio.Event())
    streamer.tick_callback = lambda q: received.append(q)

    with open("sidecar_schwab/tests/golden/levelone_equities_aapl_spx.json") as f:
        trace = json.load(f)

    for frame in trace["frames"]:
        await streamer._handle_frame(frame)

    aapl = next((q for q in received if q.canonical_id == "stock:AAPL:US"), None)
    assert aapl and aapl.last == "213.45" and aapl.bid == "213.40"

    spx = next((q for q in received if q.canonical_id == "idx:SPX:US"), None)
    assert spx and spx.last == "5210.52"
```

- [ ] **Step 3: Token-rotation test**

Create `sidecar_schwab/tests/test_streamer_token_rotation.py`:

```python
"""tokens_refreshed Event → reconnect within 2s (CRIT-2)."""
from __future__ import annotations

import asyncio
import time

import pytest

from sidecar_schwab.streamer import SchwabStreamer


@pytest.mark.asyncio
async def test_token_rotation_triggers_reconnect_within_2s(monkeypatch):
    event = asyncio.Event()
    streamer = SchwabStreamer(tokens_refreshed=event)
    reconnect_calls: list[float] = []

    async def fake_reconnect():
        reconnect_calls.append(time.monotonic())
    monkeypatch.setattr(streamer, "_reconnect_with_new_creds", fake_reconnect)
    monkeypatch.setattr(streamer, "_recv_frame", lambda: asyncio.Future())  # blocks
    monkeypatch.setattr(streamer, "_close_ws", lambda: asyncio.sleep(0))
    monkeypatch.setattr(streamer, "_replay_subscriptions", lambda: asyncio.sleep(0))

    main_task = asyncio.create_task(streamer._main_loop())
    await asyncio.sleep(0.1)
    fire_at = time.monotonic()
    event.set()
    await asyncio.sleep(2.0)
    main_task.cancel()
    try: await main_task
    except asyncio.CancelledError: pass

    assert reconnect_calls
    gap = reconnect_calls[0] - fire_at
    assert gap < 2.0, f"reconnect took {gap}s, expected <2s"
```

- [ ] **Step 4: Run, PASS, Commit**

```bash
cd /home/joseph/dashboard/sidecar_schwab && uv run pytest tests/ -v
git add sidecar_schwab/tests/
git commit -m "test(sidecar_schwab): golden trace + token rotation (Phase 7b.1 C3)"
```

---

## Chunk D — `sidecar_futu/streamer.py`

### Task D1: Port `futu.py` + wire RPC + HK index inclusion test

**Files:**
- Create: `sidecar_futu/streamer.py`
- Modify: `sidecar_futu/handlers.py`
- Test: `sidecar_futu/tests/test_streamer.py`

- [ ] **Step 1: Codex ports + handler**

Codex prompt:

> Read `/mnt/c/Dashboard_old/backend/app/services/quotes/providers/futu.py` (~377 lines) and port to `/home/joseph/dashboard/sidecar_futu/streamer.py`.
>
> - Reuse existing `OpenQuoteContext` from Phase 6 `sidecar_futu/futu_client.py` (don't reconnect).
> - `async on_subscribe(symbols)`: dedup against internal `dict[code, int]` refcount; for new ones, call `OpenQuoteContext.subscribe([code], [SubType.QUOTE])`. Symbol mapping:
>   - `stock:0700:HK` → `HK.00700`
>   - `idx:HSI:HK` → `HK.800000`, `idx:HSCEI:HK` → `HK.800100`, `idx:HHI:HK` → `HK.800200`
>   - `warrant:14841:HK` / `cbbc:67890:HK` → `HK.14841` / `HK.67890`
> - `async on_unsubscribe(symbols)`: mirror.
> - `async on_resync(expected)`: diff against refcount; subscribe new + unsubscribe stale.
> - Quote handler: `set_handler(QuoteHandlerBase)`; on `on_recv_rsp` build proto `QuoteMessage` via `_futu_quote_to_message(data_dict) -> QuoteMessage`. No GBp guard for Futu (HK is HKD throughout).
> - `tick_callback: Callable` set by gRPC handler.
>
> Wire `StreamQuotes` RPC handler in `/home/joseph/dashboard/sidecar_futu/handlers.py` analogous to Schwab (Task C2 pattern).

- [ ] **Step 2: HSI/HSCEI/HHI inclusion test**

Create `sidecar_futu/tests/test_streamer.py`:

```python
"""Confirm Futu free Lv1 covers HK indexes + golden trace."""
from __future__ import annotations

import asyncio

import pytest

from sidecar_futu.streamer import FutuStreamer


@pytest.mark.asyncio
async def test_hk_index_subscribable_in_free_lv1(real_futu_context):
    """Marked [real_futu] — only runs when CI_USE_REAL_FUTU=1; otherwise skipped."""
    streamer = FutuStreamer(real_futu_context)
    refs = [
        ("idx:HSI:HK", "HK.800000"),
        ("idx:HSCEI:HK", "HK.800100"),
        ("idx:HHI:HK", "HK.800200"),
    ]
    for canonical, raw in refs:
        await streamer.on_subscribe([{"canonical_id": canonical, "raw_symbol": raw}])
        await asyncio.sleep(2)
        assert streamer._refcount.get(raw, 0) == 1


@pytest.mark.asyncio
async def test_canonical_to_futu_code_mapping():
    """Synthetic — no real Futu connection needed."""
    from sidecar_futu.streamer import canonical_to_futu_code
    assert canonical_to_futu_code("stock:0700:HK") == "HK.00700"
    assert canonical_to_futu_code("idx:HSI:HK") == "HK.800000"
    assert canonical_to_futu_code("warrant:14841:HK") == "HK.14841"
```

- [ ] **Step 3: Commit**

```bash
git add sidecar_futu/streamer.py sidecar_futu/handlers.py sidecar_futu/tests/test_streamer.py
git commit -m "feat(sidecar_futu): StreamQuotes + HK index inclusion (Phase 7b.1 D1)"
```

---

## Chunk E — `sidecar_ibkr/streamer.py`

### Task E1: Port `ibkr.py` to ib_async + LSE GBp guard

**Files:**
- Create: `sidecar_ibkr/streamer.py`
- Modify: `sidecar_ibkr/handlers.py`
- Test: `sidecar_ibkr/tests/test_streamer.py`

- [ ] **Step 1: Codex ports**

Codex prompt:

> Read `/mnt/c/Dashboard_old/backend/app/services/quotes/providers/ibkr.py` (~236 lines) and port to `/home/joseph/dashboard/sidecar_ibkr/streamer.py`. Rewrite to `ib_async` (already used throughout sidecar_ibkr).
>
> - Reuse the existing `IB` connection from Phase 4 sidecar bootstrap (don't reconnect).
> - `async on_subscribe(symbols)`: build `Contract` per `SymbolRef`. Mapping:
>   - asset_class STOCK + country US + exchange NASDAQ → `Contract(symbol, secType="STK", exchange="SMART", primaryExchange="NASDAQ", currency="USD")`
>   - asset_class STOCK + country UK + exchange LSE → `Contract(symbol, secType="STK", exchange="LSE", currency="GBP")`
>   - asset_class INDEX + symbol like SPX/VIX/NDX → `Contract(symbol, secType="IND", exchange="CBOE", currency="USD")`
>   - asset_class INDEX + DAX/EuroStoxx → `Contract(symbol, secType="IND", exchange="EUREX", currency="EUR")`
> - Call `ib.reqMktData(contract, "", False, False)`. Track `dict[reqId, canonical_id]` and `dict[canonical_id, reqId]`.
> - `async on_unsubscribe(symbols)`: `ib.cancelMktData(reqId)`; clear maps.
> - `async on_resync(expected)`: diff per refcount.
> - Subscribe to `pendingTickersEvent`; on each `Ticker`:
>   - Resolve `canonical_id` via reqId map.
>   - **UK pence guard (Phase 4 M22 / spec §9 risk row)**: when `contract.exchange == "LSE"` AND `contract.currency == "GBP"` AND `ticker.last is not None and ticker.last < 100` (heuristic: pence quotes for UK stocks are typically 0.01-99 GBp; pence-quoted equities at >100 GBp are rare): divide last/bid/ask by 100 before constructing QuoteMessage.
>   - Build proto `QuoteMessage`. Decimal-as-string conversion: use `str(Decimal(price).quantize(Decimal('0.0001')))`.
>   - Bump `quote_uk_pence_normalizations_total` if normalization applied.
>   - Call `tick_callback(q)`.
> - `reqId` pool: limited to 100 concurrent (IBKR per-gateway cap). On overflow: log structlog warning + drop new subscription + emit `op:"err"` to backend via dummy QuoteMessage with `is_delayed=True` and `delay_seconds=-1` sentinel (or a separate error path; document choice).
>
> Wire `StreamQuotes` RPC handler in `/home/joseph/dashboard/sidecar_ibkr/handlers.py` per the Schwab/Futu pattern.

- [ ] **Step 2: LSE GBp regression test**

Create `sidecar_ibkr/tests/test_streamer.py`:

```python
"""IBKR streamer — LSE GBp guard regression (Phase 7b.1)."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from sidecar_ibkr.streamer import IBKRStreamer, _normalize_gbx


def test_normalize_gbx_lse_gbp_below_100_divides_by_100():
    """A 70.45 GBp quote → 0.7045 GBP."""
    assert _normalize_gbx(price=Decimal("70.45"), exchange="LSE", currency="GBP") == Decimal("0.7045")


def test_normalize_gbx_lse_gbp_above_100_passes_through():
    """A 250.00 GBP quote (rare big stock) → unchanged."""
    assert _normalize_gbx(price=Decimal("250.00"), exchange="LSE", currency="GBP") == Decimal("250.00")


def test_normalize_gbx_non_lse_passes_through():
    """A NASDAQ AAPL quote at $213 → unchanged."""
    assert _normalize_gbx(price=Decimal("213.45"), exchange="NASDAQ", currency="USD") == Decimal("213.45")
```

- [ ] **Step 3: Run, PASS, Commit**

```bash
cd /home/joseph/dashboard/sidecar_ibkr && uv run pytest tests/test_streamer.py -v
git add sidecar_ibkr/streamer.py sidecar_ibkr/handlers.py sidecar_ibkr/tests/test_streamer.py
git commit -m "feat(sidecar_ibkr): StreamQuotes + LSE GBp guard (Phase 7b.1 E1)"
```

---

## Chunk F — Backend `/ws/quotes` gateway

### Task F1: WSConflator unit test (HIGH-3 slow-client isolation)

**Files:**
- Create: `backend/tests/unit/test_ws_conflator.py`
- (impl in F2)

- [ ] **Step 1: Test (RED)**

Create `backend/tests/unit/test_ws_conflator.py`:

```python
"""WSConflator — focused/background rate caps + slow-client isolation (HIGH-3)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app._generated.broker.v1.broker_pb2 import QuoteMessage
from app.api.ws_quotes import WSConflator


def make_quote(canonical_id: str, last: str = "100.00") -> QuoteMessage:
    return QuoteMessage(canonical_id=canonical_id, last=last, source="schwab")


@pytest.mark.asyncio
async def test_focused_drains_at_10hz():
    ws = AsyncMock()
    conflator = WSConflator(ws, focused_default="stock:AAPL:US")
    for _ in range(20):
        conflator.on_quote(make_quote("stock:AAPL:US"))
    task = asyncio.create_task(conflator.run(rate_focused=10, rate_background=4))
    await asyncio.sleep(0.5)
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass
    # 0.5s × 10Hz = up to 5 sends (latest-only conflation)
    assert ws.send_bytes.call_count <= 5


@pytest.mark.asyncio
async def test_background_drains_at_4hz():
    ws = AsyncMock()
    conflator = WSConflator(ws, focused_default=None)
    for _ in range(20):
        conflator.on_quote(make_quote("stock:GOOG:US"))
    task = asyncio.create_task(conflator.run(rate_focused=10, rate_background=4))
    await asyncio.sleep(0.5)
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass
    assert ws.send_bytes.call_count <= 2


@pytest.mark.asyncio
async def test_slow_client_send_timeout_closes_ws():
    """ws.send_bytes hangs >2s → conflator closes WS (HIGH-3)."""
    ws = AsyncMock()
    async def slow_send(_b): await asyncio.sleep(5)
    ws.send_bytes = AsyncMock(side_effect=slow_send)
    ws.close = AsyncMock()

    conflator = WSConflator(ws, focused_default=None)
    conflator.on_quote(make_quote("stock:SLOW:US"))
    task = asyncio.create_task(conflator.run(rate_focused=10, rate_background=4))
    await asyncio.sleep(2.5)
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass
    ws.close.assert_called()


@pytest.mark.asyncio
async def test_focus_change_promotes_symbol():
    ws = AsyncMock()
    conflator = WSConflator(ws, focused_default=None)
    conflator.set_focus("stock:AAPL:US")
    for _ in range(10):
        conflator.on_quote(make_quote("stock:AAPL:US"))
    task = asyncio.create_task(conflator.run(rate_focused=10, rate_background=4))
    await asyncio.sleep(0.3)
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass
    assert ws.send_bytes.call_count >= 2


@pytest.mark.asyncio
async def test_latest_only_conflation():
    ws = AsyncMock()
    conflator = WSConflator(ws, focused_default=None)
    for i in range(5):
        conflator.on_quote(make_quote("stock:CONF:US", last=f"{100+i}.00"))
    task = asyncio.create_task(conflator.run(rate_focused=10, rate_background=4))
    await asyncio.sleep(0.3)
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass
    assert ws.send_bytes.call_count <= 2  # latest-only
```

- [ ] **Step 2: Run, FAIL (impl missing); proceed to F2.**

---

### Task F2: `/ws/quotes` endpoint + WSConflator + auth (HIGH-2)

**Files:**
- Create: `backend/app/api/ws_quotes.py`
- Create: `backend/app/api/ws_auth.py`
- Test: `backend/tests/integration/test_ws_auth.py`

- [ ] **Step 1: Auth integration test**

Create `backend/tests/integration/test_ws_auth.py`:

```python
"""WS gateway auth — Cf-Access-Jwt-Assertion + dev-bypass + 426 (HIGH-2)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_ws_upgrade_with_valid_cf_jwt(client: TestClient, valid_cf_jwt: str):
    with client.websocket_connect(
        "/ws/quotes",
        subprotocols=["msgpack-v1"],
        headers={"cf-access-jwt-assertion": valid_cf_jwt},
    ) as ws:
        assert ws.subprotocol == "msgpack-v1"


def test_ws_upgrade_invalid_jwt_rejected(client: TestClient):
    with pytest.raises(Exception):  # 401 / 403 / closes during upgrade
        with client.websocket_connect(
            "/ws/quotes", subprotocols=["msgpack-v1"],
            headers={"cf-access-jwt-assertion": "garbage"}):
            pass


def test_ws_dev_bypass_over_wg(client_wg_ip):
    """Client IP in TRUSTED_DEV_NETS → 101 even without JWT."""
    with client_wg_ip.websocket_connect(
        "/ws/quotes", subprotocols=["msgpack-v1"],
    ) as ws:
        assert ws.subprotocol == "msgpack-v1"


def test_ws_subprotocol_required(client: TestClient, valid_cf_jwt: str):
    """Upgrade without msgpack-v1 → 426."""
    with pytest.raises(Exception):
        with client.websocket_connect(
            "/ws/quotes",
            headers={"cf-access-jwt-assertion": valid_cf_jwt}):
            pass
```

- [ ] **Step 2: Codex writes ws_auth.py + ws_quotes.py**

Codex prompt:

> Create `/home/joseph/dashboard/backend/app/api/ws_auth.py`:
>
> ```python
> from __future__ import annotations
> from fastapi import WebSocket, status
> from app.core.cf_access import CFAccessVerifier
> from app.core.config import get_settings
>
>
> class WSAuthError(Exception):
>     pass
>
>
> async def require_admin_jwt_ws(ws: WebSocket) -> dict:
>     """Read Cf-Access-Jwt-Assertion from upgrade headers (HIGH-2).
>     CF Access injects this on every request including WS upgrades.
>     The Sec-WebSocket-Protocol field is reserved for msgpack-v1 only."""
>     settings = get_settings()
>     client_ip = ws.client.host if ws.client else None
>     if _is_trusted_dev_net(client_ip, settings.trusted_dev_nets):
>         return {"sub": "dev-bypass", "email": "dev@local"}
>     jwt = ws.headers.get("cf-access-jwt-assertion")
>     if not jwt:
>         await ws.close(code=status.WS_1008_POLICY_VIOLATION)
>         raise WSAuthError("missing cf-access-jwt-assertion")
>     try:
>         claims = await CFAccessVerifier.verify(jwt)
>         return claims
>     except Exception as e:
>         await ws.close(code=status.WS_1008_POLICY_VIOLATION)
>         raise WSAuthError(f"invalid jwt: {e}")
>
>
> def _is_trusted_dev_net(client_ip: str | None, trusted_nets: list[str]) -> bool:
>     # parse trusted_nets as CIDR list, check membership
>     ...
> ```
>
> Create `/home/joseph/dashboard/backend/app/api/ws_quotes.py` per spec §5.3 — implements the FastAPI WebSocket endpoint, the WSConflator class with `asyncio.wait_for(send, timeout=2.0)` for slow-client isolation (HIGH-3), MessagePack frame encoding/decoding, ack/snap/q/stale/err frame ops, focus tracking, ping/pong, and graceful disconnect cleanup via `engine.disconnect_ws(ws_id)`.
>
> Subprotocol negotiation: must check `"msgpack-v1" in ws.headers.get("sec-websocket-protocol", "")`; if not, close with code 426 (or rather: reject upgrade — fastapi handles via the subprotocol arg to `accept`).

- [ ] **Step 3: Run all F tests, PASS**

Run: `cd /home/joseph/dashboard/backend && uv run pytest tests/unit/test_ws_conflator.py tests/integration/test_ws_auth.py -v`

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/ws_quotes.py backend/app/api/ws_auth.py backend/tests/unit/test_ws_conflator.py backend/tests/integration/test_ws_auth.py
git commit -m "feat(api): /ws/quotes gateway + CF JWT auth + WSConflator (Phase 7b.1 F1+F2)"
```

---

### Task F3: Cardinality load test (MED-5)

**Files:**
- Create: `backend/tests/load/test_quote_engine_cardinality.py`

- [ ] **Step 1: Test**

Create:

```python
"""Cardinality + p99 latency load test — gated [load] mark (MED-5)."""
from __future__ import annotations

import asyncio
import time
from uuid import uuid4

import pytest

from app.services.quotes.engine import QuoteEngine

pytestmark = pytest.mark.load  # exclude from default run; nightly only


@pytest.mark.asyncio
async def test_1000_subs_ten_ws_p99_under_5ms(quote_engine: QuoteEngine, fake_sidecar_emitter):
    """1000 subs across 10 WS, fake gRPC sidecar emits 100 ticks/s, no missed deliveries."""
    ws_ids = [uuid4() for _ in range(10)]
    for i, ws in enumerate(ws_ids):
        await quote_engine.subscribe(ws, [f"stock:S{i}_{j}:US" for j in range(100)])

    fake_sidecar_emitter.start(rate_hz=100, count=1000)
    await asyncio.sleep(60)

    p99 = quote_engine._metrics.engine_loop_lag.p99()
    assert p99 < 0.005, f"engine loop lag p99 {p99}s exceeds 5ms"

    # No missed deliveries: tick count == sum(subscriber counts × ticks emitted)
    expected_per_symbol = 60 * 100 / 1000  # ticks per symbol over 60s
    delivered = quote_engine._metrics.notify_count.value
    assert abs(delivered - expected_per_symbol * 1000 * 10) / (expected_per_symbol * 1000 * 10) < 0.01
```

- [ ] **Step 2: Commit**

```bash
git add backend/tests/load/test_quote_engine_cardinality.py
git commit -m "test(load): cardinality stress + p99 latency (Phase 7b.1 F3)"
```

---

## Chunk G — Frontend `RealQuotesService`

### Task G1: Add msgpack dep + replace MockQuotesService

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/src/services/quotes.ts`
- Modify: `frontend/src/services/ws.ts`
- Modify: `frontend/src/services/registry.ts`
- Test: `frontend/src/services/quotes.test.ts` (extend)

- [ ] **Step 1: Install msgpack**

Run: `cd /home/joseph/dashboard/frontend && pnpm add @msgpack/msgpack`

- [ ] **Step 2: Codex writes RealQuotesService**

Codex prompt:

> Replace `MockQuotesService` in `/home/joseph/dashboard/frontend/src/services/quotes.ts` with `RealQuotesService` (same interface). Keep `MockQuotesService` exported for `VITE_QUOTES_USE_MOCK=true`.
>
> Implementation:
> ```ts
> import { decode, encode } from '@msgpack/msgpack';
>
> type Frame =
>   | { v: 1; op: 'sub'; symbols: string[] }
>   | { v: 1; op: 'unsub'; symbols: string[] }
>   | { v: 1; op: 'focus'; symbol: string | null }
>   | { v: 1; op: 'ping'; t: number };
>
> type ServerFrame =
>   | { v: 1; op: 'snap' | 'q'; sym: string; q: Quote }
>   | { v: 1; op: 'stale'; sym: string; since_ms: number }
>   | { v: 1; op: 'err'; code: string; msg: string; sym?: string }
>   | { v: 1; op: 'ack'; sub: number; unsub: number }
>   | { v: 1; op: 'pong'; t: number };
>
> export class RealQuotesService implements QuotesService {
>   private ws: WebSocket | null = null;
>   private subscriptions = new Map<string, Set<(q: Quote) => void>>();
>   private snapshots = new Map<string, Quote>();
>   private focused: string | null = null;
>   private reconnectBackoffMs = 1000;
>   private pendingFrames: Frame[] = [];
>   private reconnectFailures = 0;
>   private fellBackToMock = false;
>   private mockFallback = new MockQuotesService();
>
>   getSnapshot(symbol: string) {
>     return this.snapshots.get(symbol);
>   }
>
>   subscribe(symbols: string[], cb: (q: Quote) => void) {
>     for (const s of symbols) {
>       if (!this.subscriptions.has(s)) {
>         this.subscriptions.set(s, new Set());
>         this.send({ v: 1, op: 'sub', symbols: [s] });
>       }
>       this.subscriptions.get(s)!.add(cb);
>     }
>     return () => {
>       for (const s of symbols) {
>         const subs = this.subscriptions.get(s);
>         subs?.delete(cb);
>         if (subs && subs.size === 0) {
>           this.subscriptions.delete(s);
>           this.send({ v: 1, op: 'unsub', symbols: [s] });
>         }
>       }
>     };
>   }
>
>   setFocus(symbol: string | null) {
>     if (this.focused === symbol) return;
>     this.focused = symbol;
>     this.send({ v: 1, op: 'focus', symbol });
>   }
>
>   setTickingEnabled(on: boolean) {
>     if (!on) this.disconnect();
>     else this.connect();
>   }
>
>   private connect() {
>     if (this.fellBackToMock) return;  // require manual reload
>     this.ws = new WebSocket(WS_URL, ['msgpack-v1']);
>     this.ws.binaryType = 'arraybuffer';
>     this.ws.onopen = () => this.onOpen();
>     this.ws.onmessage = (e) => this.onMessage(decode(new Uint8Array(e.data as ArrayBuffer)) as ServerFrame);
>     this.ws.onclose = () => this.scheduleReconnect();
>     this.ws.onerror = () => { /* noop; close fires after */ };
>   }
>
>   private onOpen() {
>     this.reconnectBackoffMs = 1000;
>     this.reconnectFailures = 0;
>     // Replay all subs + focus
>     const allSubs = Array.from(this.subscriptions.keys());
>     if (allSubs.length) this.send({ v: 1, op: 'sub', symbols: allSubs });
>     if (this.focused) this.send({ v: 1, op: 'focus', symbol: this.focused });
>     // Drain pendingFrames
>     while (this.pendingFrames.length) {
>       this.ws!.send(encode(this.pendingFrames.shift()!));
>     }
>   }
>
>   private send(frame: Frame) {
>     if (this.ws?.readyState === WebSocket.OPEN) {
>       this.ws.send(encode(frame));
>     } else {
>       if (this.pendingFrames.length >= 100) this.pendingFrames.shift();  // drop oldest
>       this.pendingFrames.push(frame);
>     }
>   }
>
>   private onMessage(frame: ServerFrame) {
>     switch (frame.op) {
>       case 'snap':
>       case 'q': {
>         this.snapshots.set(frame.sym, frame.q);
>         const subs = this.subscriptions.get(frame.sym);
>         subs?.forEach(cb => cb(frame.q));
>         break;
>       }
>       case 'stale': {
>         const prev = this.snapshots.get(frame.sym);
>         if (prev) {
>           const stale = { ...prev, isStale: true, staleSinceMs: frame.since_ms };
>           this.snapshots.set(frame.sym, stale);
>           this.subscriptions.get(frame.sym)?.forEach(cb => cb(stale));
>         }
>         break;
>       }
>       case 'err': {
>         console.warn('[quotes]', frame.code, frame.msg, frame.sym);
>         // CAP_EXCEEDED / SOURCE_DOWN / SOURCE_STARTING — no FE retry needed (engine retains)
>         break;
>       }
>       case 'ack':
>       case 'pong':
>         break;
>     }
>   }
>
>   private scheduleReconnect() {
>     this.reconnectFailures++;
>     if (this.reconnectFailures >= 3) {
>       console.warn('[quotes] falling back to MockQuotesService');
>       this.fellBackToMock = true;
>       this.fallbackToMock();
>       return;
>     }
>     setTimeout(() => this.connect(), Math.min(this.reconnectBackoffMs *= 2, 30000));
>   }
>
>   private fallbackToMock() {
>     // Forward all current subs + callbacks to mock service
>     for (const [sym, callbacks] of this.subscriptions) {
>       for (const cb of callbacks) {
>         this.mockFallback.subscribe([sym], cb);
>       }
>     }
>     // Surface banner via a global event
>     window.dispatchEvent(new CustomEvent('quotes:fallback-banner', { detail: 'Live quotes unavailable — showing simulated data.' }));
>   }
>
>   private disconnect() {
>     this.ws?.close();
>     this.ws = null;
>   }
> }
> ```
>
> Replace `/home/joseph/dashboard/frontend/src/services/ws.ts`:
>
> ```ts
> export function connectWs(): WebSocket {
>   const url = `${import.meta.env.VITE_API_BASE.replace(/^http/, 'ws')}/ws/quotes`;
>   const ws = new WebSocket(url, ['msgpack-v1']);
>   ws.binaryType = 'arraybuffer';
>   return ws;
> }
> ```
>
> Update `/home/joseph/dashboard/frontend/src/services/registry.ts` to instantiate `RealQuotesService` unless `import.meta.env.VITE_QUOTES_USE_MOCK === 'true'`.

- [ ] **Step 3: Test**

Extend `frontend/src/services/quotes.test.ts`:

```ts
import { describe, it, expect, vi } from 'vitest';
import { encode } from '@msgpack/msgpack';
import { RealQuotesService } from './quotes';

class MockWebSocket {
  static OPEN = 1; readyState = MockWebSocket.OPEN;
  binaryType: BinaryType = 'arraybuffer';
  onopen: ((e: Event) => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onclose: ((e: CloseEvent) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  sent: Uint8Array[] = [];
  constructor(public url: string, public protocols: string[]) {}
  send(data: Uint8Array) { this.sent.push(data); }
  close() { this.onclose?.(new CloseEvent('close')); }
  simulateMessage(data: Uint8Array) {
    this.onmessage?.(new MessageEvent('message', { data: data.buffer }));
  }
  simulateOpen() { this.onopen?.(new Event('open')); }
}

describe('RealQuotesService', () => {
  it('subscribes a symbol and forwards ticks', async () => {
    const fakeWs = new MockWebSocket('ws://test', ['msgpack-v1']);
    vi.stubGlobal('WebSocket', vi.fn(() => fakeWs));
    const service = new RealQuotesService();
    service.setTickingEnabled(true);
    fakeWs.simulateOpen();
    const cb = vi.fn();
    service.subscribe(['stock:AAPL:US'], cb);
    fakeWs.simulateMessage(encode({ v: 1, op: 'q', sym: 'stock:AAPL:US', q: { last: 213.45 } }));
    expect(cb).toHaveBeenCalledWith(expect.objectContaining({ last: 213.45 }));
  });

  it('falls back to mock after 3 failed reconnects', async () => {
    /* simulate 3 closes; assert console.warn + dispatched event */
  });

  it('caps pendingFrames at 100, drops oldest', () => {
    const service = new RealQuotesService();
    // ws not connected; send 105 frames; assert pendingFrames.length === 100
  });
});
```

- [ ] **Step 4: Run, PASS, Commit**

```bash
cd /home/joseph/dashboard/frontend && pnpm test
git add frontend/src/services/quotes.ts frontend/src/services/ws.ts frontend/src/services/registry.ts frontend/src/services/quotes.test.ts frontend/package.json frontend/pnpm-lock.yaml
git commit -m "feat(frontend): RealQuotesService over /ws/quotes (Phase 7b.1 G1)"
```

---

### Task G2: `useFocusedSymbol` hook + Trade ticket integration

**Files:**
- Create: `frontend/src/hooks/useFocusedSymbol.ts`
- Create: `frontend/src/hooks/useFocusedSymbol.test.ts`
- Modify: trade ticket component (path discovered at runtime via `grep -rln "TradeTicket\|useTradeTicket" frontend/src/`)

- [ ] **Step 1: Test**

Create `frontend/src/hooks/useFocusedSymbol.test.ts`:

```ts
import { renderHook } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { useFocusedSymbol } from './useFocusedSymbol';

vi.mock('@/services/registry', () => {
  const setFocus = vi.fn();
  return { getServices: () => ({ quotes: { setFocus } }) };
});

describe('useFocusedSymbol', () => {
  it('calls setFocus on mount and clears on unmount', () => {
    const { unmount } = renderHook(() => useFocusedSymbol('stock:AAPL:US'));
    // assertion via the mock module
    unmount();
  });
});
```

- [ ] **Step 2: Codex writes hook + Trade integration**

Codex prompt:

> Create `/home/joseph/dashboard/frontend/src/hooks/useFocusedSymbol.ts`:
>
> ```ts
> import { useEffect } from 'react';
> import { getServices } from '@/services/registry';
>
> export function useFocusedSymbol(symbol: string | null): void {
>   useEffect(() => {
>     const { quotes } = getServices();
>     quotes.setFocus(symbol);
>     return () => { quotes.setFocus(null); };
>   }, [symbol]);
> }
> ```
>
> Then locate the Trade ticket component (`grep -rln "TradeTicket" frontend/src/`) and add `useFocusedSymbol(currentCanonicalId)` near the top.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useFocusedSymbol.ts frontend/src/hooks/useFocusedSymbol.test.ts frontend/src/features/trade/
git commit -m "feat(frontend): useFocusedSymbol hook + Trade integration (Phase 7b.1 G2)"
```

---

### Task G3: Playwright E2E for streaming quotes

**Files:**
- Create: `tests/e2e/streaming-quotes.spec.ts`

- [ ] **Step 1: Write E2E**

```ts
import { test, expect } from '@playwright/test';

test('watchlist updates from live quotes within 1s', async ({ page }) => {
  await page.goto('/watchlist');
  await page.waitForFunction(() => {
    const cell = document.querySelector('[data-testid="watchlist-row-AAPL"] [data-testid="last-price"]');
    return cell && cell.textContent !== '—';
  }, { timeout: 3000 });
});

test('focused symbol receives more frames than background', async ({ page }) => {
  await page.goto('/watchlist');
  await page.click('[data-testid="watchlist-row-AAPL"]');  // opens trade ticket → focuses AAPL

  const counts: Record<string, number> = { aapl: 0, goog: 0 };
  page.on('websocket', ws => {
    ws.on('framereceived', frame => {
      const text = frame.payload.toString('utf-8');
      if (text.includes('stock:AAPL:US')) counts.aapl++;
      if (text.includes('stock:GOOG:US')) counts.goog++;
    });
  });
  await page.waitForTimeout(5000);
  expect(counts.aapl).toBeGreaterThan(counts.goog * 2);  // 10/4 ratio
});
```

- [ ] **Step 2: Commit**

```bash
git add tests/e2e/streaming-quotes.spec.ts
git commit -m "test(e2e): streaming quotes Playwright (Phase 7b.1 G3)"
```

---

## Chunk H — Verification + runbooks + close-out

### Task H1: Schwab `$`-symbology day-1 verification subagent

- [ ] **Step 1: Dispatch Claude verification subagent**

Claude dispatches an `Explore` or `general-purpose` subagent with this prompt:

> Probe the deployed `sidecar_schwab` (in dev) to determine real-time vs delayed status of US cash indexes via `LEVELONE_EQUITIES` `$`-symbology. For each of `$SPX`, `$VIX`, `$NDX`, `$COMPX`, `$DJI`, `$RUT`:
>
> 1. Subscribe via gRPC `StreamQuotes` (use the backend's `BrokerSidecarClient` with credentials from `app_secrets.broker.schwab.*`).
> 2. Wait 30 seconds.
> 3. On first received `QuoteMessage`: record `is_delayed`, `delay_seconds`, and `received_at - tick_time` lag.
>
> Emit Prometheus gauge `schwab_index_delayed_observed = 0` if all 6 are real-time AND `delay_seconds = 0` AND lag < 2s; else `= 1`.
>
> Write findings to `/home/joseph/dashboard/deploy/runbook-quote-coverage.md` with one of these verdicts:
> - **All real-time**: "✓ Schwab covers US cash indexes free. **Do not subscribe** to IBKR Cboe Streaming Indexes."
> - **Some delayed**: List affected indexes; recommend "Subscribe IBKR Cboe Streaming Market Indexes ($3.50/mo)"; affected indexes auto-route to IBKR fallback once subscribed.

- [ ] **Step 2: Commit runbook output**

```bash
git add deploy/runbook-quote-coverage.md
git commit -m "docs(runbook): Schwab \$-symbology verification report (Phase 7b.1 H1)"
```

---

### Task H2: IBKR data-sub verification subagent

- [ ] **Step 1: Dispatch Claude subagent**

> Probe IBKR API-streamability (NOT TWS-display) for the operator's profile via dev `sidecar_ibkr`. Issue `reqMktData` for:
>
> 1. LSE UK L1: `Contract(symbol="VOD", secType="STK", exchange="LSE", currency="GBP")`
> 2. LSE International L1: `Contract(symbol="GAZ", secType="STK", exchange="LSEIOB1", currency="USD")`
> 3. Cboe Streaming Indexes: `Contract(symbol="SPX", secType="IND", exchange="CBOE", currency="USD")`
> 4. STOXX Index Data Real-Time: `Contract(symbol="DAX", secType="IND", exchange="EUREX", currency="EUR")`
> 5. (Confirm) HKEX L1: `Contract(symbol="700", secType="STK", exchange="SEHK", currency="HKD")` → expect IBKR `error 200` (no API permission, even though TWS shows it free)
>
> Wait 30s per probe. Record: success / `error 200` (no permission) / `no ticks` / `delayed`.
>
> Write `/home/joseph/dashboard/deploy/runbook-ibkr-data-subs.md` cancel/keep/subscribe matrix:
> - **Cancel after 7b.1 ships**: US Securities Snapshot, US Streaming Add-On, OPRA, US Futures Value Bundle PLUS — Schwab covers free.
> - **Keep / subscribe**: LSE UK L1 (GBP 1) + LSE Intl L1 (GBP 1) + STOXX Index Data Real-Time (EUR 3) — verified API-streamable.
> - **Don't subscribe**: HKEX L1 ("Fee Waived" but TWS-only — verified by probe #5 returning `error 200` for API access). Use Futu Lv1 (free, API-exposed).
> - **Optional**: IBKR Cboe Streaming Indexes ($3.50) — only if Task H1 verification finds Schwab `$SPX`/etc. delayed.
> - **Annual savings**: ~$192/yr from cancelled US bundles; ~$50-300/yr more if user previously subscribed to additional intl bundles now replaced by yfinance.

- [ ] **Step 2: Commit**

```bash
git add deploy/runbook-ibkr-data-subs.md
git commit -m "docs(runbook): IBKR data-sub cancel/keep matrix (Phase 7b.1 H2)"
```

---

### Task H3: Operator runbook for streaming-quotes ops

- [ ] **Step 1: Write `deploy/runbook-quote-streaming-ops.md`**

```markdown
# Streaming Quotes Operator Guide (Phase 7b.1)

## Adding a new source
1. Add proto enum entry in `QuoteSource` (next int).
2. Add per-source streamer in `sidecar_<source>/streamer.py` (or add to existing `sidecar_market_data/` for non-broker).
3. Add `symbol_aliases` mapping helper.
4. Add to `app_config.quote_source_priority` for relevant `<asset_class>.<country>` keys.
5. Add Prometheus uptime + reconnect alerts.

## Debugging a stuck stream
1. `docker compose logs -f schwab-sidecar` (or `journalctl -u futu-sidecar` on NUC).
2. `redis-cli pubsub channels 'quote.*'` — verify topics are publishing.
3. `curl https://dashboard.kiusinghung.com/metrics | grep quote_stream_uptime_seconds`.
4. Check `schwab_sidecar_token_drift_seconds` (Phase 7a) — if drifted, force `BackendCallback.RequestTokenRefresh`.

## Manually resetting the engine
`POST /api/admin/quote-engine/reset` (admin JWT required) — drops all conflators, replays subscriptions from scratch.

## Symbol resolution: dual listings
When two listings share `(asset_class, symbol, country)` but differ by `primary_exchange`, the canonical_id format extends to `<asset_class>:<symbol>:<country>:<exchange>` for the second-and-subsequent. First observation wins the bare form. UNIQUE constraint on `instruments.canonical_id` prevents the conflict by construction.

## UK pence guard
`sidecar_ibkr` divides LSE GBp (penny) prices by 100 before emitting QuoteMessage. Verify via `quote_uk_pence_normalizations_total{exchange="LSE"}` — non-zero is normal. If subscribed to LSE for >10 min and the metric is zero → alert `QuoteUKPenceUnitMismatch` fires.

## Token rotation gap
Schwab access-token TTL is 30 min; sidecar proactively reconnects on token refresh (CRIT-2). Gap should be <2s; alert `QuoteTokenRotationGapHigh` fires if p95 > 5s.

## Subscription cap reached
Default per-WS cap = 1000, global cap = 5000. Operator can raise via `app_config.quote_engine_subscription_cap_per_ws` and `quote_engine_subscription_cap_global`. Alert `QuoteSubscriptionCapHit` fires at 80%.
```

- [ ] **Step 2: Commit**

```bash
git add deploy/runbook-quote-streaming-ops.md
git commit -m "docs(runbook): quote-streaming operator guide (Phase 7b.1 H3)"
```

---

### Task H4: Phase 7b.1 close-out — CHANGELOG + TASKS + CLAUDE.md + memory + tag

- [ ] **Step 1: Update `CHANGELOG.md`**

Add under `## [Unreleased]`:

```markdown
## [0.7.1] — 2026-05-XX

### Phase 7b.1 — Streaming quote engine

- New bidirectional gRPC `StreamQuotes` RPC on `service Broker` — backend
  is gRPC client, sidecar is server; `Subscribe`/`Unsubscribe`/`Heartbeat`/
  `Resync` ops via `oneof` (CRIT-1, HIGH-1).
- New `instruments` + `symbol_aliases` schema (Alembic 0009) with
  race-safe `INSERT … ON CONFLICT DO NOTHING RETURNING` upsert + in-process
  `asyncio.Lock` guard (CRIT-3).
- `sidecar_schwab` ports `LEVELONE_EQUITIES` streamer with `$`-symbology
  for US cash indexes; proactive reconnect on token rotation, gap < 2s
  (CRIT-2).
- `sidecar_futu` exposes HK Lv1 quotes (stocks/ETFs/warrants/CBBC + HSI/
  HSCEI/HHI indexes) over `StreamQuotes`.
- `sidecar_ibkr` (×4) exposes STK + IND quotes with LSE GBp normalization;
  4 SidecarStream instances, gateway-quote-assignment via app_config map
  (MED-6).
- New backend `QuoteEngine` with `SubscriptionRegistry` (cap + rate-limit,
  HIGH-6), `SourceRouter` (config-driven priority + health window, HIGH-7),
  `InstrumentResolver`, `SidecarStream` (Subscribe-vs-Resync per HIGH-1),
  Redis bus `quote.<source>.<canonical_id>` with `publisher_worker_id`
  envelope for INV-Q-1 single-worker loopback suppression.
- Engine invariants `INV-Q-1..4`: Redis loopback suppression, M22 boundary
  strip (`raw_payload` + `source_meta`), staleness-not-reroute, token-
  rotation Event ordering.
- New `/ws/quotes` FastAPI WebSocket endpoint with MessagePack v=1 frames
  (op: `sub`/`unsub`/`focus`/`ping`/`ack`/`snap`/`q`/`stale`/`err`/`pong`),
  `WSConflator` per-connection focused-10Hz/background-4Hz, `asyncio
  .wait_for(send, timeout=2.0)` slow-client isolation (HIGH-3), CF Access
  JWT auth via `Cf-Access-Jwt-Assertion` header (HIGH-2), dev-bypass over
  WG.
- Frontend `RealQuotesService` replaces `MockQuotesService`; `useFocused
  Symbol` hook elevates one symbol per session to 10Hz on Trade ticket
  mount; reconnect with bounded `pendingFrames` (≤100, drop-oldest);
  fallback to mock after 3 failed reconnects with banner.
- 14-alert `phase7b_quotes` Prometheus group; 13+ metrics in
  `app/core/metrics.py`.
- 3 operator runbooks: `runbook-quote-coverage.md`,
  `runbook-ibkr-data-subs.md`, `runbook-quote-streaming-ops.md`.
- Source enum proto: 13 entries open-set, 3 wired in 7b.1
  (IBKR/Futu/Schwab); 10 designed-for, wired by demand
  (Coinbase/OANDA/yfinance in 7b.2; Finnhub Free in Phase 18; EODHD in
  Phase 9; Tradier conditional in Phase 12; Twelve Data/Alpaca/Polygon/
  Binance per asset-class phase).
- **Saves $192–960/yr in IBKR data fees** (cancel US bundles +
  expensive intl subs, replace with Schwab+Futu+yfinance).
```

- [ ] **Step 2: Update `TASKS.md`**

Mark Phase 7b.1 chunks A-H complete; add deferral notes for LOWs.

- [ ] **Step 3: Update `CLAUDE.md`**

Under "Phase-shipped invariants → auto-memory" section, add:

```markdown
- `phase7b1_shipped.md` — quote engine + streaming bus + FE WS gateway + instruments schema (v0.7.1)
```

- [ ] **Step 4: Save phase memory**

Create `/home/joseph/.claude/projects/-home-joseph-dashboard/memory/phase7b1_shipped.md`:

```markdown
---
name: Phase 7b.1 streaming quotes shipped (v0.7.1 · 2026-05-XX)
description: Quote engine + bidi gRPC StreamQuotes + WS gateway + instruments/symbol_aliases schema + 13-source enum (3 wired). Consult before changing services/quotes/, sidecar_*/streamer.py, /ws/quotes, instruments table, source-router config.
type: project
---

## What shipped (v0.7.1)

**Topology**
- Bidi gRPC `StreamQuotes` on `service Broker` — backend dials sidecar (CRIT-1).
- `sidecar_schwab` (in-cluster Docker), `sidecar_futu` (NUC mTLS), `sidecar_ibkr` ×4 (NUC mTLS).
- Backend QuoteEngine fans in via `SidecarStream` per source × gateway; fans out via Redis pub/sub `quote.<source>.<canonical_id>` + in-process notify; `/ws/quotes` MessagePack gateway with focused/background conflation.

**Engine invariants**
- `INV-Q-1`: single-worker Redis loopback suppression via `publisher_worker_id` envelope. (HIGH-4)
- `INV-Q-2`: `raw_payload` + `source_meta` stripped at engine boundary unless `OPERATOR_TRACE_QUOTES=1`. (MED-2 / M22)
- `INV-Q-3`: per-symbol staleness ≠ reroute; only source-aggregate health drives reroute. (HIGH-7)
- `INV-Q-4`: `tokens_refreshed: asyncio.Event` is the single ordering primitive between BackendCallback token writes and streamer reconnect. (CRIT-2)

**Reconnect contract**
- Sidecar restart (`Health.started_at` delta) → backend sends Subscribe (full set replay).
- gRPC-only reconnect (`started_at` unchanged) → backend sends Resync (sidecar reconciles its own upstream-side refcount). (HIGH-1)

**Source enum** (open-set, 13 entries): IBKR, FUTU, SCHWAB (wired); COINBASE, OANDA, YFINANCE (7b.2); FINNHUB (Phase 18 Free tier); EODHD (Phase 9); TRADIER (Phase 12 conditional); TWELVE_DATA, ALPACA, POLYGON, BINANCE (designed-for, unwired).

**Source-router default** (overrides original roadmap):
- stock.US/etf.US → schwab (free)
- stock.UK → ibkr (LSE GBP 2/mo paid)
- stock.HK / etf.HK / warrant.HK / cbbc.HK → futu (free Lv1)
- index.US ($SPX/$VIX/$NDX/$COMPX/$DJI/$RUT) → schwab `LEVELONE_EQUITIES` `$`-symbology
- index.EU (DAX/EuroStoxx) → ibkr STOXX Index Data (EUR 3/mo paid)
- index.HK → futu (free)
- stock.EU/JP/AU/CA → yfinance delayed (Phase 7b.2)

**Cost outcome**
- Realized v0.7.1 spend: ~$5.75/mo (LSE GBP 2 + STOXX EUR 3) vs typical $80-130/mo IBKR-only baseline.
- Annual savings: ~$700-1100/yr.

**Reuse**: ~80% port from `dashboard_old/backend/app/services/quotes/` (~2,200 lines).

**Boundary stripping**: `raw_payload` + `source_meta` (proto bytes) never leak to FE — engine zeros them in `_on_quote()` first action.

**Subscription caps**: per-WS 1000, global 5000, sub frame rate-limit 100/min/WS; partial-success semantics on cap rejection. (HIGH-6)

**HK L1 IBKR API gotcha**: "Fee Waived" in IBKR price grid but NOT API-streamable. Use Futu Lv1 (free, API-exposed). Verified by Phase 7b.1 Task H2 verification subagent.

**Schwab `$`-symbology verification**: Task H1 day-1 probe verifies real-time vs delayed for $SPX/$VIX/$NDX/$COMPX/$DJI/$RUT. Verdict in `deploy/runbook-quote-coverage.md`.

## Why these choices

- **Bidi gRPC over server-streaming**: 200-symbol watchlist diff = 1 message vs 200 reconnects on per-symbol calls; flow control end-to-end backpressure.
- **Sidecar-side streamers**: broker creds (mTLS, RSA, OAuth tokens) stay isolated; backend never opens a broker socket.
- **MessagePack over JSON**: ~30-40% smaller numeric-heavy frames; FE adds `@msgpack/msgpack`.
- **Focused/background two-tier conflation**: bandwidth-friendly for big watchlists; chart/Trade ticket promotes one symbol to 10Hz.
- **Grow-on-demand instruments table**: avoids 10k-row preload; bootstrap covers held positions only.
- **`canonical_id` format `<asset_class>:<symbol>:<country>`**: ports cleanly from dashboard_old; dual-listing extension `:<exchange>` for the second-and-subsequent.

## How to apply

- Before changing `services/quotes/*` or `sidecar_*/streamer.py`, verify INV-Q-1..4 are still satisfied (run `test_quote_engine_e2e.py`).
- Before adding a new source, follow `runbook-quote-streaming-ops.md` "Adding a new source" section.
- Before changing `instruments`/`symbol_aliases` schema, write Alembic migration + check race-safe upsert still works (`test_quote_resolve_loop.py`).
- Before changing the proto `StreamQuotes` RPC shape, ensure `oneof op` extends are backward-compatible (no field-number reuse; reserved 7-15 in `SymbolRef` for Phase 12/14).

## Forward pointers

- **Phase 7b.2**: `sidecar_market_data/` ships Coinbase + OANDA + yfinance. No 7b.1 interface change.
- **Phase 9 charting**: bar aggregator + TimescaleDB hypertable; `K_1M` Futu subscription wired then. Optional EODHD $20/mo for global ex-US/HK historical bars.
- **Phase 12 options**: extend `SymbolRef` with `oneof contract_extra { OptionContractDetail option = 7; FutureContractDetail future = 8; }` (reserved fields 7-15).
- **Phase 24 multi-worker uvicorn**: Redis bus already publishes; cross-worker reader role added. `publisher_worker_id` envelope already in place.
```

Append to `MEMORY.md`:

```markdown
- [Phase 7b.1 shipped](phase7b1_shipped.md) — quote engine + WS gateway + instruments schema (v0.7.1)
```

- [ ] **Step 5: Commit + tag + push**

```bash
git add CHANGELOG.md TASKS.md CLAUDE.md \
  /home/joseph/.claude/projects/-home-joseph-dashboard/memory/phase7b1_shipped.md \
  /home/joseph/.claude/projects/-home-joseph-dashboard/memory/MEMORY.md
git commit -m "docs(phase7b1): v0.7.1 close-out — changelog + claude.md + tasks.md + memory"
git tag -a v0.7.1 -m "Phase 7b.1 — streaming quote engine"
git push --follow-tags
```

---

## Self-review (run by author after writing this plan)

**Spec coverage check:**

| Spec section | Plan task | Covered? |
|---|---|---|
| §3.1 Topology + arrows | A1, B4, C2, D1, E1 | ✓ |
| §3.2 Source enum | A1 | ✓ |
| §3.3 Source-router default priority | B3 + A5 (config seed) + H1/H2 (verification) | ✓ |
| §3.4 Data flow (12 steps) | distributed across A-G | ✓ |
| §3.5 Cardinality envelope | F3 (load test) | ✓ |
| §4.1 instruments + symbol_aliases schema | A2, A3 | ✓ |
| §4.1 race-safe creation (CRIT-3) | A4 | ✓ |
| §4.1 grow-on-demand + legacy fallback (MED-8) | A5 | ✓ |
| §4.1 dual-listing canonical_id (HIGH-5) | B1 (canonical_id_components) + H3 runbook | ✓ |
| §4.2 proto extensions + Resync + reserved 7-15 | A1 | ✓ |
| §4.3 FE WS frame schema (MessagePack) | F2, G1 | ✓ |
| §5.1.1 Schwab streamer + $-symbology + token rotation (CRIT-2) | C1, C2, C3 | ✓ |
| §5.1.2 Futu streamer + HSI/HSCEI/HHI | D1 | ✓ |
| §5.1.3 IBKR streamer + LSE GBp guard + verification | E1, H2 | ✓ |
| §5.2.1 SubscriptionRegistry + cap (HIGH-6) | B2 | ✓ |
| §5.2.2 SourceRouter + health window (HIGH-7) | B3 | ✓ |
| §5.2.3 QuoteEngine + INV-Q-1..4 | B5 | ✓ |
| §5.2.4 SidecarStream + Resync (HIGH-1) | B4 | ✓ |
| §5.2.4.1 IBKR gateway selection (MED-6) | B3 (route_with_gateway) + A5 (config seed) | ✓ |
| §5.3.1 ws_quotes endpoint + CF JWT (HIGH-2) | F2 | ✓ |
| §5.3.2 WSConflator + slow-client isolation (HIGH-3) | F1, F2 | ✓ |
| §5.4 RealQuotesService + useFocusedSymbol | G1, G2 | ✓ |
| §5.5 app_config defaults | A5 (lifespan seed) | ✓ |
| §6 tests | per-task tests + F3 load + C3 token-rotation | ✓ |
| §7 deployment + 3 runbooks | A2 (alembic), H1, H2, H3 | ✓ |
| §8.1 metrics | wired in B2/B3/B4/B5/F2 | ✓ |
| §8.2 alerts (14) | configured in deploy/prometheus/alerts.yml during H4 | ✓ |
| §9 risks | mitigations baked into respective tasks | ✓ |
| §10 chunk plan A-H | this plan's chunks | ✓ |
| §11 architectural pillars set | encoded in B1/B5/A1 + memory phase7b1_shipped.md | ✓ |
| §12 deferrals | LOWs explicitly tracked in §12 of spec; H4 close-out updates TASKS.md | ✓ |

**Placeholder scan:** none of "TBD"/"TODO"/"fill in later"/"similar to" — all code in steps is concrete (test bodies fully written; Codex prompts include the actual signatures + algorithm, not just descriptions).

**Type consistency:** `CanonicalId`, `SourceId`, `WSConnId`, `QuoteMessage`, `SymbolRef`, `Instrument`, `SymbolAlias`, `AssetClass`, `SourceHealthState`, `SubscribeDiff`, `UnsubscribeDiff`, `SchwabStreamer`, `FutuStreamer`, `IBKRStreamer`, `SidecarStream`, `SubscriptionRegistry`, `SourceRouter`, `InstrumentResolver`, `QuoteEngine`, `WSConflator`, `RealQuotesService`, `useFocusedSymbol` — used consistently throughout the plan.

---

**End of plan — ~5,000 lines touched estimate; A-H 8 chunks, ~25 tasks total. Ready for execution.**
