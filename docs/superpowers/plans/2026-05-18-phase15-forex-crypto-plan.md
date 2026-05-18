# Phase 15 — Forex + Crypto Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add IBKR IDEALPRO FX (MKT/LMT/RFQ) and IBKR Paxos crypto (open-set instrument registry, Coinbase L1+L2 data) as two new asset classes, shipping as v0.15.0 (15a Forex) then v0.15.1 (15b Crypto).

**Architecture:** Phase 15a adds a `forex_rfq_quotes` table and a three-state RFQ service (`request → accepting → accepted`) that holds separate DB sessions around the broker RPC, wires a `ForexCalendar` into the risk gate, and provides a `/forex` workspace page. Phase 15b adds `CryptoDetails` meta, a `CoinbaseWsAdapter` that writes L2 order book deltas to Redis streams and snapshots to a TimescaleDB hypertable, `_check_crypto_exposure` in the risk gate using a NLV Redis key written by `BalanceSnapshotWriter`, and a `/crypto` workspace page.

**Tech Stack:** Python 3.14 · FastAPI · SQLAlchemy 2.0 async · Alembic · Pydantic v2 · asyncpg · Redis · TimescaleDB · gRPC/protobuf · React 19 · TypeScript strict · Tailwind v4 · shadcn/ui · Zustand · klinecharts · Vitest · pytest-asyncio

---

## Subagent Routing

Per CLAUDE.md Phase 15 spec §9, coding is dispatched to Codex (`codex exec -m gpt-5.5`) or local Qwen (`curl http://192.168.50.30:11435/v1/completions`). Anthropic Claude agents are for review only (haiku = spec-compliance + lang-reviewer; sonnet = code-quality + security + database; opus = ARCHITECT-REVIEW once at phase close).

---

## Phase 15a — IDEALPRO FX (v0.15.0)

### Task 1: Alembic 0051 — `forex_rfq_quotes` table + `ForexDetails` type + EvaluationContext field

**Route:** Qwen

**Files:**
- Create: `backend/alembic/versions/0051_phase15a_forex.py`
- Modify: `backend/app/services/options/types.py` (add `ForexDetails`, extend `InstrumentMeta`)
- Modify: `backend/app/services/risk_service.py` (add `account_nlv_base` field to `EvaluationContext`)
- Test: `backend/tests/test_alembic_0051.py`

- [ ] **Step 1: Write failing migration test**

```python
# backend/tests/test_alembic_0051.py
import pytest
from sqlalchemy import text

@pytest.mark.asyncio
async def test_forex_rfq_quotes_schema(db_session):
    await db_session.execute(text("""
        INSERT INTO forex_rfq_quotes (
            account_id, instrument_id, bid, ask, ttl_seconds,
            broker_quote_id, side, notional, notional_currency,
            status, expires_at
        ) VALUES (
            gen_random_uuid(), 1, 1.0800, 1.0802, 30,
            'test-bqid-001', 'BUY', 10000, 'base',
            'pending', now() + interval '30 seconds'
        )
    """))
    result = await db_session.execute(text(
        "SELECT request_id, status FROM forex_rfq_quotes WHERE broker_quote_id='test-bqid-001'"
    ))
    row = result.one()
    assert row.status == 'pending'
    assert row.request_id is not None

@pytest.mark.asyncio
async def test_forex_rfq_quotes_broker_quote_id_unique(db_session):
    """Partial unique index prevents duplicate pending quotes."""
    await db_session.execute(text("""
        INSERT INTO forex_rfq_quotes (account_id, instrument_id, bid, ask,
            ttl_seconds, broker_quote_id, side, notional, notional_currency,
            status, expires_at)
        VALUES (gen_random_uuid(), 1, 1.08, 1.082, 30, 'dupe-bqid', 'BUY',
                10000, 'base', 'pending', now() + interval '30 seconds')
    """))
    with pytest.raises(Exception):  # unique violation
        await db_session.execute(text("""
            INSERT INTO forex_rfq_quotes (account_id, instrument_id, bid, ask,
                ttl_seconds, broker_quote_id, side, notional, notional_currency,
                status, expires_at)
            VALUES (gen_random_uuid(), 1, 1.08, 1.082, 30, 'dupe-bqid', 'SELL',
                    10000, 'base', 'pending', now() + interval '30 seconds')
        """))
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/joseph/dashboard && docker compose exec backend pytest backend/tests/test_alembic_0051.py -v
```
Expected: FAIL — table `forex_rfq_quotes` does not exist

- [ ] **Step 3: Write the migration** (Qwen task — dispatch with prompt below)

Dispatch to Qwen with prompt:
```
Write Alembic migration backend/alembic/versions/0051_phase15a_forex.py.

revision = "0051_phase15a_forex"
down_revision = "0050_phase14_futures"

The migration must:

1. Widen instrument_asset_class PG enum with IF NOT EXISTS (autocommit_block like 0050):
   ALTER TYPE instrument_asset_class ADD VALUE IF NOT EXISTS 'FOREX'

2. Create table forex_rfq_quotes:
   CREATE TABLE IF NOT EXISTS forex_rfq_quotes (
       id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
       request_id        UUID NOT NULL DEFAULT gen_random_uuid(),
       account_id        UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE RESTRICT,
       instrument_id     BIGINT NOT NULL REFERENCES instruments(id) ON DELETE RESTRICT,
       bid               NUMERIC(20,8) NOT NULL,
       ask               NUMERIC(20,8) NOT NULL,
       ttl_seconds       INT NOT NULL,
       broker_quote_id   TEXT,
       side              TEXT CHECK (side IN ('BUY', 'SELL')),
       notional          NUMERIC(20,8),
       notional_currency TEXT,
       status            TEXT NOT NULL CHECK (status IN ('pending','accepting','accepted','expired','rejected')),
       reject_reason     TEXT,
       order_id          UUID REFERENCES orders(id) ON DELETE SET NULL,
       created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
       expires_at        TIMESTAMPTZ NOT NULL
   );

3. Create indexes:
   CREATE UNIQUE INDEX forex_rfq_quotes_broker_quote_id_idx
       ON forex_rfq_quotes (broker_quote_id) WHERE broker_quote_id IS NOT NULL;
   CREATE INDEX forex_rfq_quotes_account_status_idx
       ON forex_rfq_quotes (account_id, status, expires_at);

4. Seed the forex_max_notional_per_trade risk limit row (INSERT ... ON CONFLICT DO NOTHING):
   INSERT INTO risk_limits (scope_type, scope_id, limit_kind, limit_value, is_active)
   VALUES ('global', NULL, 'forex_max_notional_per_trade', 100000, true)
   ON CONFLICT DO NOTHING;

Use op.execute() for all DDL (no autogenerate). Follow the pattern from
backend/alembic/versions/0050_phase14_futures.py exactly.
Include a downgrade() that drops the table and removes the risk_limits seed row.
```

- [ ] **Step 4: Add `ForexDetails` to `options/types.py`** (Qwen task)

Dispatch to Qwen with prompt:
```
Modify backend/app/services/options/types.py.

Current InstrumentMeta (line 54):
InstrumentMeta = Annotated[
    NonOptionDetails | OptionDetails | FutureDetails,
    Field(discriminator="asset_class"),
]

Add after FutureDetails class (line 51):

from decimal import Decimal
from typing import Literal

class ForexDetails(BaseModel):
    """IDEALPRO spot FX pair details — Phase 15a."""
    asset_class: Literal["FOREX"] = "FOREX"
    base_currency: str
    quote_currency: str
    pip_size: Decimal
    contract_size: Decimal | None = None  # None for spot (notional-based)
    trading_hours: str  # human-readable e.g. "Sun 17:00 – Fri 17:00 ET"

Update InstrumentMeta union to include ForexDetails:
InstrumentMeta = Annotated[
    NonOptionDetails | OptionDetails | FutureDetails | ForexDetails,
    Field(discriminator="asset_class"),
]

Also update the parse_instrument_meta return type annotation to include ForexDetails.
Preserve all existing code exactly — only add new code.
```

- [ ] **Step 5: Add `account_nlv_base` to `EvaluationContext` in `risk_service.py`** (Qwen task)

Dispatch to Qwen with prompt:
```
Modify backend/app/services/risk_service.py.

In the EvaluationContext dataclass (around line 81), add ONE new optional field
after the existing `position_effect` field (line ~107):

    account_nlv_base: Decimal | None = None
    # Populated by orders_service before calling RiskService.evaluate() for
    # FOREX/CRYPTO assets. Source: Redis key account:nlv:{account_id}:{base_ccy}
    # (15s TTL). None → concentration check is skipped (log INFO; not an error).

Import Decimal is already present. Do not change any other code.
```

- [ ] **Step 6: Run migration**

```bash
cd /home/joseph/dashboard && docker compose exec backend alembic upgrade head
```
Expected: Applied `0051_phase15a_forex`

- [ ] **Step 7: Run test to verify it passes**

```bash
cd /home/joseph/dashboard && docker compose exec backend pytest backend/tests/test_alembic_0051.py -v
```
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/alembic/versions/0051_phase15a_forex.py \
        backend/app/services/options/types.py \
        backend/app/services/risk_service.py \
        backend/tests/test_alembic_0051.py
git commit -m "feat(forex): alembic 0051 + ForexDetails meta + account_nlv_base field"
```

---

### Task 2: ForexCalendar + CryptoCalendar + ForexInstrumentResolver

**Route:** Qwen

**Files:**
- Modify: `backend/app/services/market_calendar.py` (add `ForexCalendar` and `CryptoCalendar` — file is 210 lines, well under 800 limit)
- Create: `backend/app/services/forex/__init__.py`
- Create: `backend/app/services/forex/instrument_resolver.py`
- Test: `backend/tests/services/test_forex_calendar.py`
- Test: `backend/tests/services/test_forex_instrument_resolver.py`

- [ ] **Step 1: Write failing calendar tests**

```python
# backend/tests/services/test_forex_calendar.py
from datetime import datetime, timezone
import pytest
from app.services.market_calendar import is_forex_session_open, next_forex_session_open

def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)

def test_forex_open_monday_noon_et():
    # Monday 17:00 UTC = Monday 12:00 ET (UTC-5 winter) — should be open
    assert is_forex_session_open(_dt("2026-01-05T17:00:00+00:00")) is True

def test_forex_closed_saturday():
    assert is_forex_session_open(_dt("2026-01-03T12:00:00+00:00")) is False

def test_forex_closed_friday_close():
    # Friday 22:00 ET = Saturday 03:00 UTC — closed
    assert is_forex_session_open(_dt("2026-01-02T22:00:00-05:00")) is False

def test_forex_closed_during_daily_gap():
    # Weekday 17:05 ET = in the 17:00–17:15 gap
    assert is_forex_session_open(_dt("2026-01-05T22:05:00+00:00")) is False

def test_next_forex_session_open_from_gap():
    # During 17:05 ET gap → next open is 17:15 ET same day
    result = next_forex_session_open(_dt("2026-01-05T22:05:00+00:00"))
    assert result.hour == 22 and result.minute == 15  # 17:15 ET = 22:15 UTC in winter

def test_next_forex_session_open_from_saturday():
    # Saturday → next open is Sunday 17:00 ET = 22:00 UTC
    result = next_forex_session_open(_dt("2026-01-03T12:00:00+00:00"))
    assert result.weekday() == 6  # Sunday
```

```python
# backend/tests/services/test_forex_instrument_resolver.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.forex.instrument_resolver import ForexInstrumentResolver

@pytest.mark.asyncio
async def test_resolver_returns_none_on_miss(mock_db, mock_redis):
    mock_redis.get.return_value = None
    mock_db.execute.return_value.scalar_one_or_none.return_value = None
    resolver = ForexInstrumentResolver(mock_db, mock_redis)
    result = await resolver.resolve("EUR", "USD")
    assert result is None

@pytest.mark.asyncio
async def test_resolver_returns_cached(mock_db, mock_redis):
    import json
    cached = json.dumps({"id": 42, "canonical_id": "EUR.USD"})
    mock_redis.get.return_value = cached.encode()
    resolver = ForexInstrumentResolver(mock_db, mock_redis)
    result = await resolver.resolve("EUR", "USD")
    assert result["id"] == 42
    mock_db.execute.assert_not_called()

@pytest.mark.asyncio
async def test_resolver_invalidate_cache(mock_redis):
    resolver = ForexInstrumentResolver(None, mock_redis)
    await resolver.invalidate("EUR", "USD")
    mock_redis.delete.assert_called_once_with("forex:instrument:EURUSD")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/joseph/dashboard && docker compose exec backend pytest backend/tests/services/test_forex_calendar.py backend/tests/services/test_forex_instrument_resolver.py -v
```
Expected: FAIL — `is_forex_session_open` not defined

- [ ] **Step 3: Implement ForexCalendar in market_calendar.py** (Qwen task)

Dispatch to Qwen with prompt:
```
Append to backend/app/services/market_calendar.py (current file is 210 lines, do not alter existing code).

Add these two functions after the existing code:

The ET timezone is ZoneInfo("America/New_York").

def is_forex_session_open(now: datetime | None = None) -> bool:
    """IDEALPRO FX is 24/5: Sun 17:00 ET – Fri 17:00 ET, with a 17:00–17:15 ET daily gap."""
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    if now is None:
        now = datetime.now(UTC)
    now_et = now.astimezone(et)
    weekday = now_et.weekday()  # 0=Mon … 6=Sun
    t = now_et.time()
    close_time = time(17, 0)
    gap_end = time(17, 15)
    # Saturday (5): always closed
    if weekday == 5:
        return False
    # Sunday (6): only open from 17:00 onwards
    if weekday == 6:
        return t >= close_time
    # Friday (4): closes at 17:00
    if weekday == 4:
        return t < close_time
    # Mon–Thu: open except 17:00–17:15 daily gap
    if close_time <= t < gap_end:
        return False
    return True

def next_forex_session_open(now: datetime | None = None) -> datetime:
    """Return the next datetime when IDEALPRO FX opens (17:15 ET same day, or Sunday 17:00 ET)."""
    from zoneinfo import ZoneInfo
    from datetime import timedelta
    et = ZoneInfo("America/New_York")
    if now is None:
        now = datetime.now(UTC)
    now_et = now.astimezone(et)
    weekday = now_et.weekday()
    t = now_et.time()
    close_time = time(17, 0)
    gap_end = time(17, 15)
    # In daily gap (Mon–Thu) → same day 17:15 ET
    if weekday in (0, 1, 2, 3) and close_time <= t < gap_end:
        same_day = now_et.replace(hour=17, minute=15, second=0, microsecond=0)
        return same_day.astimezone(UTC)
    # Otherwise → next Sunday 17:00 ET
    days_until_sunday = (6 - weekday) % 7
    if days_until_sunday == 0 and t >= close_time:
        days_until_sunday = 7  # already past Sunday open, skip to next
    target = now_et + timedelta(days=days_until_sunday)
    target = target.replace(hour=17, minute=0, second=0, microsecond=0)
    return target.astimezone(UTC)

def is_crypto_session_open(now: datetime | None = None, maintenance_windows: list[dict] | None = None) -> bool:
    """Paxos crypto is 24/7 minus operator-configured blackout windows.
    maintenance_windows: list of {start_utc: "HH:MM", duration_minutes: int, days: ["mon",...]}
    """
    if now is None:
        now = datetime.now(UTC)
    if not maintenance_windows:
        return True
    day_abbr = now.strftime("%a").lower()  # "mon","tue","wed","thu","fri","sat","sun"
    for window in maintenance_windows:
        if day_abbr not in window.get("days", []):
            continue
        h, m = (int(x) for x in window["start_utc"].split(":"))
        from datetime import timedelta
        window_start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        window_end = window_start + timedelta(minutes=window["duration_minutes"])
        if window_start <= now < window_end:
            return False
    return True

def next_crypto_session_open(now: datetime | None = None, maintenance_windows: list[dict] | None = None) -> datetime:
    """Return soonest datetime when crypto session opens (skips any active blackout)."""
    from datetime import timedelta
    if now is None:
        now = datetime.now(UTC)
    if not maintenance_windows:
        return now
    # Find active window end if in one
    day_abbr = now.strftime("%a").lower()
    for window in maintenance_windows:
        if day_abbr not in window.get("days", []):
            continue
        h, m = (int(x) for x in window["start_utc"].split(":"))
        window_start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        window_end = window_start + timedelta(minutes=window["duration_minutes"])
        if window_start <= now < window_end:
            return window_end
    return now
```

- [ ] **Step 4: Create forex package + ForexInstrumentResolver** (Qwen task)

Dispatch to Qwen with prompt:
```
Create two files:

FILE 1: backend/app/services/forex/__init__.py
Empty file.

FILE 2: backend/app/services/forex/instrument_resolver.py

"""Phase 15a — read-only FX instrument resolver with Redis cache."""
from __future__ import annotations

import json
from typing import Any

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)
_CACHE_TTL = 3600  # 60 minutes

class ForexInstrumentResolver:
    """Read-only: resolves (base_currency, quote_currency) → instruments row dict.

    Does NOT write. Use _ensure_forex_instrument() in rfq_service.py for upsert.
    Returns None if the instrument row does not exist yet.
    """

    def __init__(self, db: AsyncSession, redis: Any) -> None:
        self._db = db
        self._redis = redis

    def _cache_key(self, base: str, quote: str) -> str:
        return f"forex:instrument:{base}{quote}"

    async def resolve(self, base: str, quote: str) -> dict[str, Any] | None:
        key = self._cache_key(base, quote)
        cached = await self._redis.get(key)
        if cached is not None:
            return json.loads(cached)
        result = await self._db.execute(
            text(
                "SELECT id, canonical_id, conid, asset_class, meta "
                "FROM instruments WHERE asset_class = 'FOREX' "
                "AND meta->>'base_currency' = :base AND meta->>'quote_currency' = :quote "
                "LIMIT 1"
            ),
            {"base": base, "quote": quote},
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        data = dict(row)
        await self._redis.set(key, json.dumps(data, default=str), ex=_CACHE_TTL)
        return data

    async def invalidate(self, base: str, quote: str) -> None:
        await self._redis.delete(self._cache_key(base, quote))
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/joseph/dashboard && docker compose exec backend pytest backend/tests/services/test_forex_calendar.py backend/tests/services/test_forex_instrument_resolver.py -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/market_calendar.py \
        backend/app/services/forex/__init__.py \
        backend/app/services/forex/instrument_resolver.py \
        backend/tests/services/test_forex_calendar.py \
        backend/tests/services/test_forex_instrument_resolver.py
git commit -m "feat(forex): ForexCalendar, CryptoCalendar, ForexInstrumentResolver"
```

---

### Task 3: `_check_forex_exposure` in risk_service.py

**Route:** Qwen

**Files:**
- Modify: `backend/app/services/risk_service.py`
- Test: `backend/tests/services/test_forex_risk_check.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/services/test_forex_risk_check.py
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from app.services.risk_service import EvaluationContext, RiskService

def _make_ctx(**kwargs):
    defaults = dict(
        account_id="00000000-0000-0000-0000-000000000001",
        broker_id="ibkr",
        instrument_id=1,
        side="BUY",
        qty=Decimal("10000"),
        price=Decimal("1.0800"),
        order_type="MARKET",
        time_in_force="IOC",
        request_id="req-001",
        currency_base="USD",
        asset_class="FOREX",
        notional=Decimal("10800"),
    )
    defaults.update(kwargs)
    return EvaluationContext(**defaults)

@pytest.mark.asyncio
async def test_check_forex_blocks_when_session_closed(mock_risk_service):
    with patch("app.services.risk_service.is_forex_session_open", return_value=False):
        ctx = _make_ctx()
        blocker, warning = await mock_risk_service._check_forex_exposure(ctx)
    assert blocker is not None
    assert blocker.code == "session_closed"
    assert warning is None

@pytest.mark.asyncio
async def test_check_forex_blocks_notional_cap(mock_risk_service):
    with patch("app.services.risk_service.is_forex_session_open", return_value=True):
        mock_risk_service._resolve_limit.return_value = MagicMock(limit_value=Decimal("5000"))
        ctx = _make_ctx(notional=Decimal("10000"))
        blocker, warning = await mock_risk_service._check_forex_exposure(ctx)
    assert blocker is not None
    assert blocker.code == "forex_notional_exceeded"

@pytest.mark.asyncio
async def test_check_forex_passes_when_open_no_cap(mock_risk_service):
    with patch("app.services.risk_service.is_forex_session_open", return_value=True):
        mock_risk_service._resolve_limit.return_value = None
        ctx = _make_ctx()
        blocker, warning = await mock_risk_service._check_forex_exposure(ctx)
    assert blocker is None
```

- [ ] **Step 2: Run to verify fail**

```bash
cd /home/joseph/dashboard && docker compose exec backend pytest backend/tests/services/test_forex_risk_check.py -v
```
Expected: FAIL — `_check_forex_exposure` not defined

- [ ] **Step 3: Add `_check_forex_exposure` to risk_service.py + wire into evaluate()** (Qwen task)

Dispatch to Qwen with prompt:
```
Modify backend/app/services/risk_service.py.

Import at top (after existing imports):
from app.services.market_calendar import is_forex_session_open

Add after _check_futures_exposure (line ~856), before evaluate():

    async def _check_forex_exposure(self, ctx: "EvaluationContext") -> "CheckResult":
        """Phase 15a: IDEALPRO FX risk checks. Fail-OPEN on infrastructure errors."""
        import structlog
        log = structlog.get_logger(__name__)
        blockers: list[Any] = []
        warnings: list[Any] = []
        try:
            if not is_forex_session_open():
                from app.services.market_calendar import next_forex_session_open
                retry_at = next_forex_session_open().isoformat()
                blockers.append(GateBlockerEntry(
                    check="forex_session",
                    code="session_closed",
                    message=f"IDEALPRO FX session is closed. Next open: {retry_at}",
                ))
                return blockers[0], None
            # Notional cap check
            notional = getattr(ctx, "notional", None) or (ctx.qty * (ctx.price or Decimal("1")))
            limit_row = await self._resolve_limit(ctx.account_id, ctx.broker_id, "forex_max_notional_per_trade")
            if limit_row is not None and notional > limit_row.limit_value:
                blockers.append(GateBlockerEntry(
                    check="forex_notional",
                    code="forex_notional_exceeded",
                    message=f"Notional {notional} exceeds per-trade cap {limit_row.limit_value}.",
                ))
                return blockers[0], None
        except Exception:
            metrics.forex_risk_check_failures_total.inc()
            log.exception("forex_risk_check_infrastructure_error", account_id=str(ctx.account_id))
            return None, None  # fail-OPEN
        if blockers:
            return blockers[0], None
        if warnings:
            return None, warnings[0]
        return None, None

In the evaluate() method, add after the "FUTURE" block (around line 889+):
        # Phase 15a: FX checks
        if ctx.asset_class == "FOREX":
            fx_blocker, fx_warning = (await self._check_forex_exposure(ctx)) or (None, None)
            if fx_blocker is not None:
                return GateVerdict(
                    final_verdict="block",
                    blockers=[fx_blocker],
                    warnings=[],
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                )
            if fx_warning is not None:
                pre_warnings = [fx_warning]
```

Also add Prometheus metric registration (in the metrics module or inline):
```python
# In app/core/metrics.py, add:
forex_risk_check_failures_total = Counter(
    "forex_risk_check_failures_total",
    "FX risk gate infrastructure errors (fail-open)",
)
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd /home/joseph/dashboard && docker compose exec backend pytest backend/tests/services/test_forex_risk_check.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/risk_service.py backend/app/core/metrics.py \
        backend/tests/services/test_forex_risk_check.py
git commit -m "feat(forex): _check_forex_exposure + FOREX branch in risk_service.evaluate()"
```

---

### Task 4: Proto additions + sidecar SearchContracts FOREX branch

**Route:** Codex

**Files:**
- Modify: `proto/broker/v1/broker.proto` (add 4 FX RPCs + messages)
- Modify: `sidecar_ibkr/handlers.py` (add FOREX branch in SearchContracts)
- Regenerate: compiled proto stubs

- [ ] **Step 1: Add proto messages + RPCs** (Codex task)

Dispatch to Codex with:
```bash
codex exec -m gpt-5.5 "
Add to proto/broker/v1/broker.proto after the existing last rpc entry.

Messages to add:
message FxQuoteRequest {
  string account_id = 1;
  string base_currency = 2;
  string quote_currency = 3;
  string notional = 4;           // decimal string
  string notional_currency = 5;  // 'base' or 'quote'
}

message FxQuoteResponse {
  string broker_quote_id = 1;
  string bid = 2;
  string ask = 3;
  int32  ttl_seconds = 4;
  string expires_at = 5;         // ISO8601 UTC
}

message FxAcceptRequest {
  string account_id = 1;
  string broker_quote_id = 2;
  string side = 3;               // 'BUY' or 'SELL'
  string qty = 4;                // decimal string
}

message FxAcceptResponse {
  string order_id = 1;
  string fill_price = 2;
  string status = 3;
}

message FxCancelRequest {
  string account_id = 1;
  string broker_quote_id = 2;
}

message FxMidRate {
  string base_currency = 1;
  string quote_currency = 2;
  string mid = 3;
  string timestamp = 4;          // ISO8601 UTC
}

RPCs to add:
  rpc RequestFxQuote(FxQuoteRequest) returns (FxQuoteResponse);
  rpc AcceptFxQuote(FxAcceptRequest) returns (FxAcceptResponse);
  rpc CancelFxQuote(FxCancelRequest) returns (google.protobuf.Empty);
  rpc StreamFxRates(google.protobuf.Empty) returns (stream FxMidRate);

Then in sidecar_ibkr/handlers.py SearchContracts handler (around line 1507),
change:
    ib_contract = ib_async.Contract(
        symbol=request.query,
        secType=request.asset_class or 'STK',
    )

to:
    # Phase 15: FOREX→CASH/IDEALPRO, CRYPTO→CRYPTO/PAXOS, default STK
    asset_class = request.asset_class or 'STK'
    if asset_class == 'FOREX':
        ib_contract = ib_async.Forex(symbol=request.query, exchange='IDEALPRO')
    elif asset_class == 'CRYPTO':
        ib_contract = ib_async.Crypto(symbol=request.query, exchange='PAXOS')
    else:
        ib_contract = ib_async.Contract(symbol=request.query, secType=asset_class)
"
```

- [ ] **Step 2: Regenerate proto stubs**

```bash
cd /home/joseph/dashboard && bash scripts/gen-proto.sh
```
or
```bash
cd /home/joseph/dashboard/proto && buf generate
```

- [ ] **Step 3: Verify sidecar still builds**

```bash
cd /home/joseph/dashboard && docker compose exec backend python -c "from sidecar_ibkr import handlers; print('OK')"
```
Expected: OK

- [ ] **Step 4: Commit**

```bash
git add proto/broker/v1/broker.proto sidecar_ibkr/handlers.py sidecar_ibkr/_generated/
git commit -m "feat(forex): proto FX RPCs + SearchContracts FOREX/CRYPTO secType map"
```

---

### Task 5: `rfq_service.py` + `app/api/forex.py`

**Route:** Codex

**Files:**
- Create: `backend/app/services/forex/rfq_service.py`
- Create: `backend/app/api/forex.py`
- Modify: `backend/app/main.py` (register forex router)
- Test: `backend/tests/services/test_rfq_service.py`
- Test: `backend/tests/api/test_forex_api.py`

- [ ] **Step 1: Write failing tests for rfq_service** (Codex task)

Dispatch to Codex with:
```bash
codex exec -m gpt-5.5 "
Write backend/tests/services/test_rfq_service.py with these test cases:

1. test_request_quote_creates_pending_row:
   - Mock: sidecar RPC returns broker_quote_id='bq-001', bid='1.0800', ask='1.0802', ttl_seconds=30
   - Mock: _ensure_forex_instrument returns instrument row with id=1
   - Mock: ForexInstrumentResolver.resolve returns {'id': 1, 'canonical_id': 'EUR.USD', 'conid': '12345'}
   - Mock: redis.set for CSRF nonce
   - Call: rfq_service.request_quote(account_id=UUID(...), pair=('EUR','USD'), notional=Decimal('10000'), notional_currency='base')
   - Assert: forex_rfq_quotes row with status='pending', broker_quote_id='bq-001'
   - Assert: redis.set called with 'forex:rfq:nonce:bq-001', ex=30

2. test_request_quote_409_on_duplicate_broker_quote_id:
   - Pre-insert a pending row with broker_quote_id='dupe-001'
   - Mock sidecar to return broker_quote_id='dupe-001'
   - Assert: raises HTTPException with status_code=409

3. test_accept_quote_three_state_transition:
   - Pre-insert a pending row (status='pending', expires_at=now()+60s)
   - Mock: risk gate returns allow
   - Mock: sidecar AcceptFxQuote returns fill_price='1.0801', order_id='ord-001'
   - Call: rfq_service.accept_quote(account_id=..., broker_quote_id='bq-001', side='BUY', qty=Decimal('10000'))
   - Assert: orders row created with status='pending_submit', order_type='MARKET', tif='IOC'
   - Assert: forex_rfq_quotes row status='accepted', order_id set

4. test_accept_quote_expired_raises_409:
   - Pre-insert a pending row with expires_at=now()-10s (already expired)
   - Call: rfq_service.accept_quote(...)
   - Assert: raises HTTPException with status_code=409

5. test_cancel_quote_sets_rejected:
   - Pre-insert pending row
   - Mock: sidecar CancelFxQuote returns empty
   - Call: rfq_service.cancel_quote(account_id=..., broker_quote_id='bq-001')
   - Assert: status='rejected'

Use pytest-asyncio with AsyncMock for async functions.
"
```

- [ ] **Step 2: Implement rfq_service.py** (Codex task)

Dispatch to Codex with:
```bash
codex exec -m gpt-5.5 "
Create backend/app/services/forex/rfq_service.py.

The service implements request_quote, accept_quote, cancel_quote per Phase 15 spec §4.2.

Key implementation details:
- _ensure_forex_instrument(db, pair): if ForexInstrumentResolver.resolve() returns None,
  call sidecar RequestFxQuote with notional='1' to probe, then upsert instruments row
  with ForexDetails meta, then invalidate resolver cache. Return instrument row.
- request_quote: call _ensure_forex_instrument, then ForexInstrumentResolver.resolve,
  then sidecar RequestFxQuote, then INSERT into forex_rfq_quotes with
  ON CONFLICT (broker_quote_id) DO NOTHING RETURNING id. If no row returned → HTTP 409.
  Then redis.set('forex:rfq:nonce:{broker_quote_id}', nonce_value, ex=ttl_seconds).
- accept_quote: three-state with separate DB sessions:
  Session 1: SELECT FOR UPDATE WHERE status='pending' AND expires_at > now().
  If not found → QuoteExpiredError → HTTP 409.
  Re-evaluate risk gate using EvaluationContext with request_id from quote row,
  account_nlv_base from redis.get('account:nlv:{account_id}:{currency_base}').
  On BLOCK: UPDATE status='rejected', reject_reason=blocker.message. Return 422.
  UPDATE status='accepting'. Explicit await db.commit().
  Session 2: AcceptFxQuote sidecar RPC (outside TX). On success:
    INSERT orders row: account_id, broker_id, instrument_id, conid (from instrument.conid),
    symbol (from instrument.canonical_id), side, qty, order_type='MARKET', tif='IOC',
    price=fill_price, notional=qty*fill_price, status='pending_submit', filled_qty=0,
    client_order_id=f'rfq-{broker_quote_id}'.
    UPDATE forex_rfq_quotes SET status='accepted', order_id=new_order.id.
    Both in one TX (session.begin()).
  On RPC failure: UPDATE status='rejected', reject_reason=str(exc).
- cancel_quote: guard status IN ('pending','accepting'). UPDATE status='rejected'. Sidecar CancelFxQuote.

Import from app.services.forex.instrument_resolver import ForexInstrumentResolver.
Import from app.services.market_calendar import is_forex_session_open, next_forex_session_open.
Use structlog for logging. Fail-OPEN on infrastructure errors in risk gate re-evaluation.
Use DECIMAL_10_PATTERN from app.schemas.orders for qty validation.
"
```

- [ ] **Step 3: Create app/api/forex.py** (Codex task)

Dispatch to Codex with:
```bash
codex exec -m gpt-5.5 "
Create backend/app/api/forex.py with these endpoints:

POST /api/forex/quote
  - JWT auth required
  - Rate limit: 10/min per account (use in-process deque limiter matching position_sizing pattern)
  - Body: {pair: str (e.g. 'EURUSD'), notional: str, notional_currency: 'base'|'quote', account_id: str}
  - Calls rfq_service.request_quote
  - Returns FxQuoteResponse (broker_quote_id, bid, ask, ttl_seconds, expires_at)

POST /api/forex/quote/{broker_quote_id}/accept
  - JWT auth required
  - Header: X-Csrf-Nonce (validated via GETDEL from redis 'forex:rfq:nonce:{broker_quote_id}')
  - Rate limit: 10/min per account
  - Body: {account_id: str, side: 'BUY'|'SELL', qty: str}
  - Calls rfq_service.accept_quote
  - Returns {order_id: str, fill_price: str, status: str}

DELETE /api/forex/quote/{broker_quote_id}
  - JWT auth required
  - Rate limit: 20/min per account
  - Calls rfq_service.cancel_quote

GET /api/forex/quotes
  - JWT auth required
  - Query param: account_id
  - Returns list of quotes with effective status (CASE WHEN status='pending' AND expires_at < now() THEN 'expired' ELSE status END)
  - Cursor pagination via created_at

GET /api/forex/pairs
  - JWT auth required
  - Returns list of FX pairs from app_config key 'forex/enabled_pairs'
  - Default: ['EURUSD','USDJPY','GBPUSD','AUDUSD','USDCAD','USDCHF','NZDUSD']

Include APIRouter(prefix='/api/forex', tags=['forex']).
Follow the pattern from backend/app/api/futures.py for JWT auth and error handling.
"
```

- [ ] **Step 4: Register forex router in main.py**

In `backend/app/main.py`, add:
```python
from app.api.forex import router as forex_router
app.include_router(forex_router)
```

- [ ] **Step 5: Run tests**

```bash
cd /home/joseph/dashboard && docker compose exec backend pytest backend/tests/services/test_rfq_service.py backend/tests/api/test_forex_api.py -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/forex/rfq_service.py \
        backend/app/api/forex.py \
        backend/app/main.py \
        backend/tests/services/test_rfq_service.py \
        backend/tests/api/test_forex_api.py
git commit -m "feat(forex): rfq_service three-state machine + /api/forex endpoints"
```

---

### Task 6: APScheduler TTL sweep + Prometheus metrics + lifespan hook

**Route:** Qwen

**Files:**
- Modify: `backend/app/services/forex/rfq_service.py` (add TTL sweep function)
- Modify: `backend/app/main.py` (register APScheduler job + lifespan hook)
- Modify: `backend/app/core/metrics.py` (register 7 forex Prometheus metrics)
- Test: `backend/tests/services/test_rfq_sweep.py`

- [ ] **Step 1: Write failing sweep test**

```python
# backend/tests/services/test_rfq_sweep.py
import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import text

@pytest.mark.asyncio
async def test_sweep_expires_pending_quotes(db_session):
    """Sweep marks pending quotes past expires_at as expired."""
    await db_session.execute(text("""
        INSERT INTO forex_rfq_quotes (account_id, instrument_id, bid, ask,
            ttl_seconds, broker_quote_id, side, notional, notional_currency,
            status, expires_at)
        VALUES (gen_random_uuid(), 1, 1.08, 1.082, 5, 'sweep-test-001', 'BUY',
                10000, 'base', 'pending', now() - interval '10 seconds')
    """))
    await db_session.commit()
    from app.services.forex.rfq_service import sweep_expired_quotes
    await sweep_expired_quotes(db_session)
    result = await db_session.execute(text(
        "SELECT status FROM forex_rfq_quotes WHERE broker_quote_id='sweep-test-001'"
    ))
    assert result.scalar_one() == 'expired'
```

- [ ] **Step 2: Implement sweep + metrics + lifespan wiring** (Qwen task)

Dispatch to Qwen with prompt:
```
Add to backend/app/services/forex/rfq_service.py:

async def sweep_expired_quotes(db: AsyncSession) -> int:
    """APScheduler job: mark pending quotes past expires_at as expired.
    Returns count of rows updated.
    """
    result = await db.execute(
        text(
            "UPDATE forex_rfq_quotes SET status='expired' "
            "WHERE status='pending' AND expires_at < now()"
        )
    )
    await db.commit()
    count = result.rowcount
    if count:
        log.info("forex_rfq_expired_swept", count=count)
    return count

Add to backend/app/core/metrics.py the 7 Phase 15a Prometheus metrics:

from prometheus_client import Counter, Histogram

forex_rfq_requests_total = Counter(
    "forex_rfq_requests_total", "FX RFQ requests", ["pair"]
)
forex_rfq_accepts_total = Counter(
    "forex_rfq_accepts_total", "FX RFQ accepts", ["pair", "outcome"]
)
forex_rfq_expired_total = Counter(
    "forex_rfq_expired_total", "FX RFQ TTL expirations", ["pair"]
)
forex_quote_stream_updates_total = Counter(
    "forex_quote_stream_updates_total", "FX mid-rate stream updates", ["pair"]
)
forex_risk_blocks_total = Counter(
    "forex_risk_blocks_total", "FX risk gate blocks", ["reason"]
)
forex_risk_check_failures_total = Counter(
    "forex_risk_check_failures_total", "FX risk gate infrastructure errors (fail-open)"
)
forex_rfq_latency_seconds = Histogram(
    "forex_rfq_latency_seconds", "FX RFQ stage latency", ["stage"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0]
)

In backend/app/main.py, in the lifespan async context manager, add the APScheduler job:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from app.services.forex.rfq_service import sweep_expired_quotes
    scheduler.add_job(sweep_expired_quotes, 'interval', seconds=5,
                      args=[async_session_factory()],
                      id='forex_rfq_sweep', replace_existing=True)

(Follow the existing APScheduler pattern used in futures settlement jobs.)
```

- [ ] **Step 3: Run sweep test**

```bash
cd /home/joseph/dashboard && docker compose exec backend pytest backend/tests/services/test_rfq_sweep.py -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/forex/rfq_service.py \
        backend/app/core/metrics.py \
        backend/app/main.py \
        backend/tests/services/test_rfq_sweep.py
git commit -m "feat(forex): APScheduler TTL sweep + 7 Prometheus metrics wired"
```

---

### Task 7: FX Frontend — types, api, FractionalQtyInput, FxTicketSection, modal

**Route:** Codex

**Files:**
- Create: `frontend/src/services/forex/types.ts`
- Create: `frontend/src/services/forex/api.ts`
- Create: `frontend/src/components/primitives/FractionalQtyInput.tsx`
- Create: `frontend/src/lib/decimal.ts`
- Create: `frontend/src/features/forex/FxTicketSection.tsx`
- Create: `frontend/src/features/forex/FxQuoteDisplay.tsx`
- Modify: `frontend/src/features/orders/TradeTicketModal.tsx` (add FX mode toggle)
- Test: `frontend/src/components/primitives/FractionalQtyInput.test.tsx`
- Test: `frontend/src/features/forex/FxTicketSection.test.tsx`

- [ ] **Step 1: Write failing FractionalQtyInput test** (Codex task)

Dispatch to Codex with:
```bash
codex exec -m gpt-5.5 "
Write frontend/src/components/primitives/FractionalQtyInput.test.tsx:

import { render, fireEvent, screen } from '@testing-library/react';
import { FractionalQtyInput } from './FractionalQtyInput';

test('accepts valid decimal input within step precision', () => {
  const onChange = vi.fn();
  render(<FractionalQtyInput value='' onChange={onChange} step='0.01' decimals={2} />);
  fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '1.23' } });
  expect(onChange).toHaveBeenCalledWith('1.23');
});

test('shows error when precision exceeds decimals on blur', () => {
  render(<FractionalQtyInput value='1.234' onChange={vi.fn()} step='0.01' decimals={2} />);
  fireEvent.blur(screen.getByRole('spinbutton'));
  expect(screen.getByText(/precision/i)).toBeInTheDocument();
});

test('clears error when valid value entered after blur', () => {
  render(<FractionalQtyInput value='1.23' onChange={vi.fn()} step='0.01' decimals={2} />);
  fireEvent.blur(screen.getByRole('spinbutton'));
  expect(screen.queryByText(/precision/i)).not.toBeInTheDocument();
});

Also write frontend/src/lib/decimal.ts with:
export function countDecimals(value: string): number {
  const [, frac] = value.split('.');
  return frac ? frac.length : 0;
}
export function exceedsPrecision(value: string, decimals: number): boolean {
  return countDecimals(value) > decimals;
}
"
```

- [ ] **Step 2: Implement FractionalQtyInput + FxTicketSection + API layer** (Codex task)

Dispatch to Codex with:
```bash
codex exec -m gpt-5.5 "
Create these files for Phase 15a FX frontend:

FILE: frontend/src/lib/decimal.ts
export function countDecimals(value: string): number {
  const [, frac] = value.split('.');
  return frac ? frac.length : 0;
}
export function exceedsPrecision(value: string, decimals: number): boolean {
  return countDecimals(value) > decimals;
}

FILE: frontend/src/components/primitives/FractionalQtyInput.tsx
'use client';
import * as React from 'react';
import { exceedsPrecision } from '@/lib/decimal';

interface Props {
  value: string;
  onChange: (v: string) => void;
  step?: string;
  min?: string;
  max?: string;
  decimals?: number;
  placeholder?: string;
  disabled?: boolean;
}

export function FractionalQtyInput({ value, onChange, step, min, max, decimals = 8, placeholder, disabled }: Props) {
  const [error, setError] = React.useState<string | null>(null);
  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setError(null);
    onChange(e.target.value);
  };
  const handleBlur = () => {
    if (value && exceedsPrecision(value, decimals)) {
      setError(\`Max \${decimals} decimal places\`);
    } else {
      setError(null);
    }
  };
  return (
    <div>
      <input
        type='number'
        role='spinbutton'
        value={value}
        onChange={handleChange}
        onBlur={handleBlur}
        step={step}
        min={min}
        max={max}
        placeholder={placeholder}
        disabled={disabled}
        className='w-full rounded border px-2 py-1 text-sm'
      />
      {error && <p className='mt-1 text-xs text-red-500'>{error} (precision exceeded)</p>}
    </div>
  );
}

FILE: frontend/src/services/forex/types.ts
export interface FxPair {
  canonical_id: string;
  base_currency: string;
  quote_currency: string;
  pip_size: string;
}

export interface FxQuote {
  id: string;
  broker_quote_id: string;
  bid: string;
  ask: string;
  ttl_seconds: number;
  expires_at: string;
  status: 'pending' | 'accepting' | 'accepted' | 'expired' | 'rejected';
  side: 'BUY' | 'SELL' | null;
  notional: string | null;
  notional_currency: string | null;
  request_id: string;
}

export interface FxQuoteRequest {
  pair: string;
  notional: string;
  notional_currency: 'base' | 'quote';
  account_id: string;
}

export interface FxAcceptRequest {
  account_id: string;
  side: 'BUY' | 'SELL';
  qty: string;
}

export interface FxPosition {
  instrument_id: number;
  canonical_id: string;
  base_currency: string;
  quote_currency: string;
  qty: string;
  avg_cost: string;
  market_value: string;
  unrealised_pnl: string;
}

FILE: frontend/src/services/forex/api.ts
import { mintCsrfNonce } from '@/services/admin/api';
import type { FxPair, FxQuote, FxQuoteRequest, FxAcceptRequest } from './types';

const BASE = '/api/forex';

export async function listPairs(): Promise<FxPair[]> {
  const res = await fetch(\`\${BASE}/pairs\`, { credentials: 'include' });
  if (!res.ok) throw new Error('Failed to fetch FX pairs');
  return res.json();
}

export async function requestQuote(req: FxQuoteRequest): Promise<FxQuote> {
  const res = await fetch(\`\${BASE}/quote\`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function acceptQuote(brokerQuoteId: string, req: FxAcceptRequest): Promise<{ order_id: string; fill_price: string; status: string }> {
  const nonce = await mintCsrfNonce();
  const res = await fetch(\`\${BASE}/quote/\${brokerQuoteId}/accept\`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-Csrf-Nonce': nonce },
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function cancelQuote(brokerQuoteId: string, accountId: string): Promise<void> {
  await fetch(\`\${BASE}/quote/\${brokerQuoteId}?account_id=\${accountId}\`, {
    method: 'DELETE',
    credentials: 'include',
  });
}

export async function listQuotes(accountId: string): Promise<FxQuote[]> {
  const res = await fetch(\`\${BASE}/quotes?account_id=\${accountId}\`, { credentials: 'include' });
  if (!res.ok) throw new Error('Failed to fetch quotes');
  return res.json();
}

FILE: frontend/src/features/forex/FxQuoteDisplay.tsx
Shows bid/ask spread, countdown timer, amber badge when TTL < 5s, red expired state.
Use useEffect with setInterval(1s) to tick TTL down from (expires_at - Date.now()) / 1000.
Props: { quote: FxQuote; onAccept: (side: 'BUY'|'SELL', qty: string) => void; onCancel: () => void }
Show two confirm buttons: 'Buy at {ask}' and 'Sell at {bid}'.
When ttl <= 0: show red text 'Quote expired — refresh' and disable buttons.
When ttl < 5: show amber badge 'Expiring'.

FILE: frontend/src/features/forex/FxTicketSection.tsx
Props: { accountId: string; pair: FxPair }
State: notional string, notionalCurrency 'base'|'quote', activeQuote FxQuote|null, loading bool, error string|null
'Get Quote' button calls requestQuote, sets activeQuote.
Renders FxQuoteDisplay when activeQuote !== null.
On accept: calls acceptQuote, shows success toast (use existing toast pattern from TradeTicketModal).
On cancel: calls cancelQuote, clears activeQuote.

Then modify frontend/src/features/orders/TradeTicketModal.tsx to:
- Import FxTicketSection
- Detect asset_class === 'FOREX' on the instrument → set tradeMode = 'fx'
- When tradeMode === 'fx': render FxTicketSection instead of the standard order form
  (between the instrument header and the risk gate warnings section)
"
```

- [ ] **Step 3: Run tests**

```bash
cd /home/joseph/dashboard/frontend && pnpm test -- --run FractionalQtyInput FxTicketSection
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/decimal.ts \
        frontend/src/components/primitives/FractionalQtyInput.tsx \
        frontend/src/services/forex/ \
        frontend/src/features/forex/ \
        frontend/src/features/orders/TradeTicketModal.tsx
git commit -m "feat(forex): FractionalQtyInput + FxTicketSection + TradeTicketModal FX mode"
```

---

### Task 8: ForexPage + route + integration tests

**Route:** Codex

**Files:**
- Create: `frontend/src/features/forex/ForexPage.tsx`
- Create: `frontend/src/routes/forex.tsx`
- Test: `backend/tests/integration/test_forex_rfq_flow.py`

- [ ] **Step 1: Write integration tests for RFQ flow** (Codex task)

Dispatch to Codex with:
```bash
codex exec -m gpt-5.5 "
Write backend/tests/integration/test_forex_rfq_flow.py with these integration tests:

1. test_request_to_accept_full_flow: full RFQ lifecycle through the API
   - POST /api/forex/quote → get broker_quote_id
   - POST /api/forex/quote/{broker_quote_id}/accept with correct CSRF nonce
   - Assert: orders row created with status='pending_submit'
   - Assert: forex_rfq_quotes status='accepted'

2. test_accept_expired_quote_returns_409:
   - POST /api/forex/quote, immediately expire the quote via SQL UPDATE
   - POST accept → expect 409

3. test_session_gap_blocks_order:
   - Patch is_forex_session_open to return False
   - POST /api/forex/quote → should succeed (no session check on request, only on accept)
   - POST accept → expect 422 with code='session_closed'

4. test_ttl_sweep_expires_pending:
   - Insert a pending quote with expires_at = now() - 10s
   - Call sweep_expired_quotes
   - Assert: status='expired'

5. test_ensure_forex_instrument_upserts_on_first_request:
   - Clear instruments table of FOREX rows
   - POST /api/forex/quote for EURUSD
   - Assert: instruments row exists with asset_class='FOREX' and meta.base_currency='EUR'

Use httpx AsyncClient with the FastAPI app. Use the existing async test client fixture pattern from backend/tests/conftest.py.
"
```

- [ ] **Step 2: Create ForexPage.tsx** (Codex task)

Dispatch to Codex with:
```bash
codex exec -m gpt-5.5 "
Create frontend/src/features/forex/ForexPage.tsx — a four-panel responsive layout:

1. Left: PairBrowser — searchable list from listPairs(). Shows live mid-rate from WS
   quote feed channel 'quote.ibkr.{canonical_id}'. Click selects the pair.
2. Top-right: RateChart — klinecharts component wired to forex quote source.
   Timeframe selector: 1m, 5m, 1h, 1d.
3. Bottom-left: RFQ panel — FxTicketSection for the selected pair.
4. Bottom-right: Positions table — fetched from GET /api/portfolio/rollup filtered to FOREX.
   Show unrealised P&L per pair.

On mobile (below md breakpoint): tab bar with 'Pairs', 'Chart', 'Quote', 'Positions'.
On desktop: CSS grid 2×2.
Selected pair state: useState in ForexPage, passed down to children.
Default selected pair: 'EURUSD' if available.

Follow the pattern from frontend/src/features/futures/FuturesPage.tsx for tab/grid layout.

Also create frontend/src/routes/forex.tsx:
import { createFileRoute } from '@tanstack/react-router';
import { ForexPage } from '@/features/forex/ForexPage';
export const Route = createFileRoute('/forex')({ component: ForexPage });
"
```

- [ ] **Step 3: Run integration tests**

```bash
cd /home/joseph/dashboard && docker compose exec backend pytest backend/tests/integration/test_forex_rfq_flow.py -v
```
Expected: PASS

- [ ] **Step 4: Run FE type check**

```bash
cd /home/joseph/dashboard/frontend && pnpm tsr generate && pnpm typecheck
```
Expected: no errors

- [ ] **Step 5: Commit + tag v0.15.0**

```bash
git add frontend/src/features/forex/ForexPage.tsx \
        frontend/src/routes/forex.tsx \
        backend/tests/integration/test_forex_rfq_flow.py
git commit -m "feat(forex): ForexPage 4-panel workspace + /forex route + integration tests"
git tag v0.15.0
```

---

## Phase 15b — Paxos Crypto + Coinbase WS (v0.15.1)

### Task 9: Alembic 0052 — `crypto_order_book_snapshots` hypertable + CryptoDetails

**Route:** Qwen

**Files:**
- Create: `backend/alembic/versions/0052_phase15b_crypto.py`
- Modify: `backend/app/services/options/types.py` (add `CryptoDetails`, extend `InstrumentMeta`)
- Test: `backend/tests/test_alembic_0052.py`

- [ ] **Step 1: Write failing migration test**

```python
# backend/tests/test_alembic_0052.py
import pytest
from sqlalchemy import text

@pytest.mark.asyncio
async def test_crypto_order_book_snapshots_schema(db_session):
    """Table exists and accepts inserts."""
    await db_session.execute(text("""
        INSERT INTO crypto_order_book_snapshots
            (instrument_id, source, level, side, price, qty, captured_at)
        VALUES (1, 'coinbase', 1, 'bid', 50000.00, 0.5, now())
    """))

@pytest.mark.asyncio
async def test_crypto_asset_class_enum(db_session):
    """CRYPTO is a valid instrument_asset_class value."""
    result = await db_session.execute(text(
        "SELECT unnest(enum_range(NULL::instrument_asset_class))"
    ))
    values = [r[0] for r in result]
    assert 'CRYPTO' in values
```

- [ ] **Step 2: Run to verify fail**

```bash
cd /home/joseph/dashboard && docker compose exec backend pytest backend/tests/test_alembic_0052.py -v
```
Expected: FAIL — table does not exist

- [ ] **Step 3: Write migration** (Qwen task)

Dispatch to Qwen with prompt:
```
Write Alembic migration backend/alembic/versions/0052_phase15b_crypto.py.

revision = "0052_phase15b_crypto"
down_revision = "0051_phase15a_forex"

The migration must:

1. Widen instrument_asset_class PG enum with IF NOT EXISTS (autocommit_block like 0050/0051):
   ALTER TYPE instrument_asset_class ADD VALUE IF NOT EXISTS 'CRYPTO'

2. Create hypertable crypto_order_book_snapshots:
   CREATE TABLE IF NOT EXISTS crypto_order_book_snapshots (
       instrument_id BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
       source        TEXT NOT NULL DEFAULT 'coinbase',
       level         INT NOT NULL,
       side          TEXT NOT NULL CHECK (side IN ('bid','ask')),
       price         NUMERIC(20,8) NOT NULL,
       qty           NUMERIC(20,8) NOT NULL,
       captured_at   TIMESTAMPTZ NOT NULL
   );
   SELECT create_hypertable('crypto_order_book_snapshots', 'captured_at');
   SELECT add_retention_policy('crypto_order_book_snapshots', INTERVAL '7 days');

3. Create CAGG (must use autocommit_block because CREATE MATERIALIZED VIEW cannot
   run in a transaction):
   CREATE MATERIALIZED VIEW crypto_order_book_1h
       WITH (timescaledb.continuous, timescaledb.materialized_only=false) AS
       SELECT time_bucket('1 hour', captured_at) AS bucket,
              instrument_id, source, side, level,
              first(price, captured_at) AS price_open,
              last(price, captured_at)  AS price_close,
              avg(qty)                  AS qty_avg
       FROM   crypto_order_book_snapshots
       WHERE  level <= 3
       GROUP BY bucket, instrument_id, source, side, level;
   SELECT add_continuous_aggregate_policy(
       'crypto_order_book_1h',
       start_offset      => INTERVAL '7 days',
       end_offset        => INTERVAL '1 hour',
       schedule_interval => INTERVAL '1 hour'
   );

Follow the autocommit_block pattern from backend/alembic/versions/0040_phase10b2_balance_snapshots_caggs.py
for both the enum widening and the CAGG creation.
Include downgrade() that drops the CAGG view and the table (CASCADE).
```

- [ ] **Step 4: Add CryptoDetails to options/types.py** (Qwen task)

Dispatch to Qwen with prompt:
```
Modify backend/app/services/options/types.py.

Add after ForexDetails (added in Task 1):

class CryptoDetails(BaseModel):
    """Paxos crypto asset details — Phase 15b."""
    asset_class: Literal["CRYPTO"] = "CRYPTO"
    base_asset: str        # e.g. "BTC"
    quote_asset: str       # e.g. "USD"
    min_qty: Decimal       # e.g. 0.00001
    qty_step: Decimal      # e.g. 0.00001
    min_notional: Decimal | None  # e.g. 1.00 USD; None if not specified

Update InstrumentMeta union to include CryptoDetails:
InstrumentMeta = Annotated[
    NonOptionDetails | OptionDetails | FutureDetails | ForexDetails | CryptoDetails,
    Field(discriminator="asset_class"),
]

Also update parse_instrument_meta return type to include CryptoDetails.
```

- [ ] **Step 5: Run migration + tests**

```bash
cd /home/joseph/dashboard && docker compose exec backend alembic upgrade head && \
  docker compose exec backend pytest backend/tests/test_alembic_0052.py -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0052_phase15b_crypto.py \
        backend/app/services/options/types.py \
        backend/tests/test_alembic_0052.py
git commit -m "feat(crypto): alembic 0052 + CryptoDetails meta + crypto_order_book_snapshots hypertable"
```

---

### Task 10: ListCryptoAssets proto + crypto_service + crypto API + sidecar PAXOS branch + NLV Redis key

**Route:** Codex

**Files:**
- Modify: `proto/broker/v1/broker.proto` (add ListCryptoAssets RPC)
- Create: `backend/app/services/crypto/__init__.py`
- Create: `backend/app/services/crypto/crypto_service.py`
- Create: `backend/app/api/crypto.py`
- Modify: `backend/app/services/balance_snapshot_writer.py` (add Redis NLV SET)
- Modify: `sidecar_ibkr/handlers.py` (already done via proto regen; verify CRYPTO path)
- Test: `backend/tests/services/test_crypto_service.py`
- Test: `backend/tests/api/test_crypto_api.py`

- [ ] **Step 1: Add ListCryptoAssets proto + implement crypto_service** (Codex task)

Dispatch to Codex with:
```bash
codex exec -m gpt-5.5 "
1. Add to proto/broker/v1/broker.proto:
   message CryptoAsset {
     string symbol = 1;
     string base_asset = 2;
     string quote_asset = 3;
     string min_qty = 4;
     string qty_step = 5;
     string min_notional = 6;   // empty string if none
     bool   available_24h = 7;
   }
   message ListCryptoAssetsRequest { string account_id = 1; }
   message ListCryptoAssetsResponse { repeated CryptoAsset assets = 1; }
   rpc ListCryptoAssets(ListCryptoAssetsRequest) returns (ListCryptoAssetsResponse);

2. Regenerate proto stubs.

3. Create backend/app/services/crypto/__init__.py (empty).

4. Create backend/app/services/crypto/crypto_service.py:

class CryptoService:
    def __init__(self, db, redis, sidecar_client):
        self._db = db
        self._redis = redis
        self._sidecar = sidecar_client
    
    async def list_assets(self, account_id: str) -> list[dict]:
        cache_key = f'crypto:assets:{account_id}'
        cached = await self._redis.get(cache_key)
        if cached:
            return json.loads(cached)
        resp = await self._sidecar.list_crypto_assets(account_id=account_id)
        assets = []
        for asset in resp.assets:
            # Upsert instruments row with CryptoDetails meta
            meta = {
                'asset_class': 'CRYPTO',
                'base_asset': asset.base_asset,
                'quote_asset': asset.quote_asset,
                'min_qty': asset.min_qty,
                'qty_step': asset.qty_step,
                'min_notional': asset.min_notional or None,
            }
            canonical_id = f'{asset.base_asset}.{asset.quote_asset}'
            await self._db.execute(text('''
                INSERT INTO instruments (canonical_id, asset_class, meta)
                VALUES (:cid, 'CRYPTO', :meta::jsonb)
                ON CONFLICT (canonical_id) DO UPDATE
                SET meta = EXCLUDED.meta, asset_class = EXCLUDED.asset_class
            '''), {'cid': canonical_id, 'meta': json.dumps(meta)})
            await self._db.commit()
            data = {'canonical_id': canonical_id, **meta}
            assets.append(data)
        await self._redis.set(cache_key, json.dumps(assets), ex=300)
        return assets

    async def resolve_crypto_instrument(self, symbol: str, broker_id: str) -> dict | None:
        # Try instruments table first
        result = await self._db.execute(text(
            'SELECT id, canonical_id, conid, meta FROM instruments '
            'WHERE canonical_id = :symbol AND asset_class = :ac LIMIT 1'
        ), {'symbol': symbol, 'ac': 'CRYPTO'})
        row = result.mappings().one_or_none()
        if row:
            return dict(row)
        # Fallback: list_assets populates the instruments table
        await self.list_assets(broker_id)
        result = await self._db.execute(text(
            'SELECT id, canonical_id, conid, meta FROM instruments '
            'WHERE canonical_id = :symbol AND asset_class = :ac LIMIT 1'
        ), {'symbol': symbol, 'ac': 'CRYPTO'})
        row = result.mappings().one_or_none()
        return dict(row) if row else None

5. Modify backend/app/services/balance_snapshot_writer.py:
   In the record() method, after 'metrics.portfolio_rollup_snapshot_writes_total.inc()'
   succeeds (inside the try block, after the inner savepoint commits), add:
   # Phase 15b: write NLV to Redis for crypto concentration check (15s TTL)
   try:
       await self._redis.set(
           f'account:nlv:{account_id}:{currency}',
           str(nlv),
           ex=15,
       )
   except Exception:
       log.warning('forex_nlv_redis_write_failed', account_id=str(account_id))
"
```

- [ ] **Step 2: Create app/api/crypto.py** (Codex task)

Dispatch to Codex with:
```bash
codex exec -m gpt-5.5 "
Create backend/app/api/crypto.py with these endpoints:

GET /api/crypto/assets
  - JWT auth
  - Query param: account_id (required)
  - Calls CryptoService.list_assets(account_id)
  - 5-min cache header

GET /api/crypto/positions
  - JWT auth
  - Query param: account_id (required)
  - Returns positions filtered to asset_class=CRYPTO from positions table
  - Include unrealised_pnl computed from market_value - cost_basis

GET /api/crypto/trades
  - JWT auth
  - Query params: account_id, before (cursor), limit (default 50)
  - Returns order fills for CRYPTO positions
  - Cursor pagination via filled_at TIMESTAMPTZ

GET /api/crypto/book/{canonical_id}
  - JWT auth
  - Returns top-20 order book snapshot from Redis hash crypto:book:snap:{canonical_id}
  - If hash not found: return empty bids/asks with captured_at=null

APIRouter(prefix='/api/crypto', tags=['crypto']).
Register in app/main.py.
Follow backend/app/api/futures.py pattern for JWT auth.
"
```

- [ ] **Step 3: Write tests** (Codex task)

Dispatch to Codex with:
```bash
codex exec -m gpt-5.5 "
Write backend/tests/services/test_crypto_service.py:

1. test_list_assets_populates_instruments: mock sidecar ListCryptoAssets returns 2 assets,
   verify instruments rows upserted and Redis cache set.

2. test_resolve_crypto_instrument_db_hit: instruments row exists → returned without sidecar call.

3. test_resolve_crypto_instrument_fallback: no instruments row → list_assets called → row found.

4. test_balance_snapshot_writer_sets_nlv_redis:
   - Call BalanceSnapshotWriter.record() with valid params
   - Assert: redis.set called with key 'account:nlv:{account_id}:{currency}' ex=15

Write backend/tests/api/test_crypto_api.py:

1. test_list_assets_endpoint: GET /api/crypto/assets?account_id=... returns list.
2. test_book_endpoint_empty_on_miss: GET /api/crypto/book/BTC.USD with no Redis data → 200 empty.
3. test_positions_endpoint: GET /api/crypto/positions?account_id=... returns list.
"
```

- [ ] **Step 4: Run tests**

```bash
cd /home/joseph/dashboard && docker compose exec backend pytest \
  backend/tests/services/test_crypto_service.py \
  backend/tests/api/test_crypto_api.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add proto/broker/v1/broker.proto sidecar_ibkr/_generated/ \
        backend/app/services/crypto/ \
        backend/app/api/crypto.py \
        backend/app/main.py \
        backend/app/services/balance_snapshot_writer.py \
        backend/tests/services/test_crypto_service.py \
        backend/tests/api/test_crypto_api.py
git commit -m "feat(crypto): ListCryptoAssets proto + CryptoService + /api/crypto endpoints + NLV Redis key"
```

---

### Task 11: CoinbaseWsAdapter + OrderBook (book_manager)

**Route:** Qwen

**Files:**
- Create: `backend/app/services/crypto/book_manager.py`
- Create: `backend/app/services/crypto/coinbase_ws.py`
- Test: `backend/tests/services/test_book_manager.py`
- Test: `backend/tests/services/test_coinbase_ws.py`

- [ ] **Step 1: Write failing book_manager tests**

```python
# backend/tests/services/test_book_manager.py
from decimal import Decimal
import pytest
from app.services.crypto.book_manager import OrderBook, MAX_BOOK_DEPTH

def test_apply_delta_adds_bid():
    book = OrderBook(bids={}, asks={})
    book.apply_delta("bid", Decimal("50000"), Decimal("0.5"), 1)
    assert book.bids[Decimal("50000")] == Decimal("0.5")

def test_apply_delta_removes_on_zero_qty():
    book = OrderBook(bids={Decimal("50000"): Decimal("0.5")}, asks={})
    book.apply_delta("bid", Decimal("50000"), Decimal("0"), 2)
    assert Decimal("50000") not in book.bids

def test_apply_delta_bounds_bids_to_max_depth():
    book = OrderBook(bids={}, asks={})
    for i in range(MAX_BOOK_DEPTH + 5):
        book.apply_delta("bid", Decimal(str(50000 + i)), Decimal("0.1"), i)
    assert len(book.bids) == MAX_BOOK_DEPTH

def test_apply_delta_keeps_top_bids():
    """Bids: keep highest prices."""
    book = OrderBook(bids={}, asks={})
    for i in range(MAX_BOOK_DEPTH + 5):
        book.apply_delta("bid", Decimal(str(i)), Decimal("0.1"), i)
    # Lowest prices should have been evicted
    assert min(book.bids.keys()) >= Decimal("5")

def test_snapshot_returns_depth_levels():
    book = OrderBook(bids={Decimal("50001"): Decimal("1"), Decimal("50000"): Decimal("2")}, asks={})
    snap = book.snapshot(depth=1)
    assert len(snap["bids"]) == 1
    assert snap["bids"][0][0] == Decimal("50001")  # highest bid first

def test_last_seq_updated():
    book = OrderBook(bids={}, asks={})
    book.apply_delta("ask", Decimal("50001"), Decimal("0.3"), 42)
    assert book.last_seq == 42
```

- [ ] **Step 2: Run to verify fail**

```bash
cd /home/joseph/dashboard && docker compose exec backend pytest backend/tests/services/test_book_manager.py -v
```
Expected: FAIL — `book_manager` not found

- [ ] **Step 3: Implement book_manager.py** (Qwen task)

Dispatch to Qwen with prompt:
```
Create backend/app/services/crypto/book_manager.py exactly as specified in the Phase 15 spec §7.2:

from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal

MAX_BOOK_DEPTH = 100

@dataclass
class OrderBook:
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    last_seq: int = 0

    def apply_delta(self, side: str, price: Decimal, qty: Decimal, seq: int) -> None:
        book = self.bids if side == "bid" else self.asks
        if qty == Decimal("0"):
            book.pop(price, None)
        else:
            book[price] = qty
        # Evict levels beyond MAX_BOOK_DEPTH after update
        if side == "bid" and len(book) > MAX_BOOK_DEPTH:
            # Keep highest MAX_BOOK_DEPTH bid prices
            to_evict = sorted(book)[:len(book) - MAX_BOOK_DEPTH]
            for p in to_evict:
                del book[p]
        elif side == "ask" and len(book) > MAX_BOOK_DEPTH:
            # Keep lowest MAX_BOOK_DEPTH ask prices
            to_evict = sorted(book, reverse=True)[:len(book) - MAX_BOOK_DEPTH]
            for p in to_evict:
                del book[p]
        self.last_seq = seq

    def snapshot(self, depth: int = 20) -> dict:
        bids = sorted(self.bids.items(), reverse=True)[:depth]
        asks = sorted(self.asks.items())[:depth]
        return {"bids": list(bids), "asks": list(asks)}
```

- [ ] **Step 4: Implement coinbase_ws.py** (Qwen task)

Dispatch to Qwen with prompt:
```
Create backend/app/services/crypto/coinbase_ws.py.

Class CoinbaseWsAdapter:
- __init__(self, redis, config_cache): reads subscribed_pairs from app_config[coinbase/subscribed_pairs]
- async start(): connects to wss://advanced-trade-ws.coinbase.com/ using websockets library
  Subscribes to channels: [{type: 'subscribe', product_ids: [...], channel: 'ticker'},
                           {type: 'subscribe', product_ids: [...], channel: 'level2'}]
- Message handling:
  - 'ticker' type: publish quote.coinbase.{canonical_id} to Redis pub/sub
    (same shape as existing quote adapters: {price, bid, ask, volume, timestamp, canonical_id})
  - 'l2_data' type (level2 updates): for each event in msg['events']:
    - Verify sequence field. IMPORTANT: field name must be confirmed at impl time.
      Use msg.get('sequence') or msg.get('sequence_num') with None-guard:
      seq = msg.get('sequence') or msg.get('sequence_num')
      if seq is None:
          log.warning('coinbase_book_missing_sequence', product_id=product_id)
          seq_int = None  # skip gap detection
      else:
          seq_int = int(seq)
    - If seq_int is not None and book.last_seq > 0:
      if seq_int != book.last_seq + 1:  # gap detected
          metrics.coinbase_book_sequence_gap_total.labels(canonical_id=canonical_id).inc()
          log.warning('coinbase_book_sequence_gap', expected=book.last_seq+1, received=seq_int)
          del self._books[product_id]  # drop book, resubscribe
          await self._resubscribe(product_id)
          return
    - For each update in event['updates']:
        book.apply_delta(side, Decimal(price), Decimal(qty), seq_int or book.last_seq + 1)
    - XADD crypto:book:{canonical_id} MAXLEN ~ 1000 {side, price, qty, seq}
    - Every 5s: HSET crypto:book:snap:{canonical_id} bids/asks from book.snapshot(100)
  - 'snapshot' type: rebuild book from scratch using snapshot prices/sizes
- Reconnect: bounded backoff [1, 2, 5, 15, 30] seconds between reconnect attempts
- One OrderBook instance per product_id, stored in self._books dict
- canonical_id conversion: 'BTC-USD' → 'BTC.USD' (replace hyphen with dot)
- Lifespan: expose start() / stop() methods for main.py integration

Prometheus metrics to increment (from app.core.metrics):
  coinbase_ws_messages_total (labels: channel, outcome)
  coinbase_ws_reconnects_total
  coinbase_book_publish_total (labels: canonical_id)
  coinbase_book_sequence_gap_total (labels: canonical_id)
  coinbase_book_lag_seconds (histogram: receipt → Redis XADD)

Use structlog for logging. Use websockets>=12 library. Never use the Redis stream
for book reconstruction — only for downstream consumers.
```

- [ ] **Step 5: Write Coinbase WS tests**

```python
# backend/tests/services/test_coinbase_ws.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from decimal import Decimal
from app.services.crypto.book_manager import OrderBook
from app.services.crypto.coinbase_ws import CoinbaseWsAdapter

@pytest.mark.asyncio
async def test_ticker_message_publishes_quote():
    redis = AsyncMock()
    adapter = CoinbaseWsAdapter(redis=redis, config_cache=None)
    adapter._subscribed_pairs = ['BTC-USD']
    ticker_msg = {
        'type': 'ticker',
        'events': [{'type': 'ticker', 'tickers': [{'product_id': 'BTC-USD', 'price': '50000', 'best_bid': '49999', 'best_ask': '50001', 'volume_24_h': '1000'}]}]
    }
    await adapter._handle_message(ticker_msg)
    redis.publish.assert_called_once()
    call_args = redis.publish.call_args[0]
    assert 'quote.coinbase.BTC.USD' in call_args[0]

@pytest.mark.asyncio
async def test_sequence_gap_triggers_resubscribe():
    redis = AsyncMock()
    adapter = CoinbaseWsAdapter(redis=redis, config_cache=None)
    book = OrderBook(bids={}, asks={})
    book.last_seq = 10  # expect 11 next
    adapter._books = {'BTC-USD': book}
    adapter._resubscribe = AsyncMock()
    gap_msg = {
        'type': 'l2_data',
        'product_id': 'BTC-USD',
        'sequence': '15',  # gap: expected 11
        'events': [{'type': 'update', 'updates': [{'side': 'bid', 'price_level': '50000', 'new_quantity': '0.1'}]}]
    }
    await adapter._handle_message(gap_msg)
    adapter._resubscribe.assert_called_once_with('BTC-USD')
    assert 'BTC-USD' not in adapter._books

@pytest.mark.asyncio
async def test_none_sequence_skips_gap_detection():
    redis = AsyncMock()
    adapter = CoinbaseWsAdapter(redis=redis, config_cache=None)
    book = OrderBook(bids={}, asks={})
    book.last_seq = 10
    adapter._books = {'BTC-USD': book}
    adapter._resubscribe = AsyncMock()
    # Message with no sequence field
    msg = {
        'type': 'l2_data',
        'product_id': 'BTC-USD',
        'events': [{'type': 'update', 'updates': [{'side': 'bid', 'price_level': '50000', 'new_quantity': '0.1'}]}]
    }
    await adapter._handle_message(msg)
    adapter._resubscribe.assert_not_called()  # no gap detected
```

- [ ] **Step 6: Run tests**

```bash
cd /home/joseph/dashboard && docker compose exec backend pytest \
  backend/tests/services/test_book_manager.py \
  backend/tests/services/test_coinbase_ws.py -v
```
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/crypto/book_manager.py \
        backend/app/services/crypto/coinbase_ws.py \
        backend/tests/services/test_book_manager.py \
        backend/tests/services/test_coinbase_ws.py
git commit -m "feat(crypto): CoinbaseWsAdapter + OrderBook book_manager (L2 + L1 feed)"
```

---

### Task 12: `_check_crypto_exposure` + CryptoCalendar integration

**Route:** Qwen

**Files:**
- Modify: `backend/app/services/risk_service.py` (add `_check_crypto_exposure` + CRYPTO branch in `evaluate()`)
- Modify: `backend/app/core/metrics.py` (add 6 crypto Prometheus metrics)
- Test: `backend/tests/services/test_crypto_risk_check.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/services/test_crypto_risk_check.py
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from app.services.risk_service import EvaluationContext, RiskService

def _make_ctx(**kwargs):
    defaults = dict(
        account_id="00000000-0000-0000-0000-000000000001",
        broker_id="ibkr",
        instrument_id=1,
        side="BUY",
        qty=Decimal("0.1"),
        price=Decimal("50000"),
        order_type="MARKET",
        time_in_force="DAY",
        request_id="req-001",
        currency_base="USD",
        asset_class="CRYPTO",
        account_nlv_base=Decimal("10000"),
    )
    defaults.update(kwargs)
    return EvaluationContext(**defaults)

@pytest.mark.asyncio
async def test_crypto_blocks_when_session_closed(mock_risk_service):
    with patch("app.services.risk_service.is_crypto_session_open", return_value=False):
        ctx = _make_ctx()
        blocker, warning = await mock_risk_service._check_crypto_exposure(ctx)
    assert blocker is not None
    assert blocker.code == "session_closed"

@pytest.mark.asyncio
async def test_crypto_blocks_on_invalid_qty_precision(mock_risk_service):
    with patch("app.services.risk_service.is_crypto_session_open", return_value=True):
        # qty_step=0.001, qty=0.0001 (too precise)
        ctx = _make_ctx(qty=Decimal("0.0001"))
        # Inject meta with qty_step=0.001
        mock_risk_service._db.execute.return_value.scalar_one_or_none.return_value = \
            MagicMock(meta={'asset_class':'CRYPTO','base_asset':'BTC','quote_asset':'USD',
                            'min_qty':'0.001','qty_step':'0.001','min_notional':None})
        blocker, warning = await mock_risk_service._check_crypto_exposure(ctx)
    assert blocker is not None
    assert blocker.code == "invalid_qty_precision"

@pytest.mark.asyncio
async def test_crypto_concentration_warn_on_large_position(mock_risk_service):
    with patch("app.services.risk_service.is_crypto_session_open", return_value=True):
        # qty=0.1, price=50000 → notional=5000; NLV=10000 → 50% > 20%
        ctx = _make_ctx(qty=Decimal("0.1"), price=Decimal("50000"), account_nlv_base=Decimal("10000"))
        blocker, warning = await mock_risk_service._check_crypto_exposure(ctx)
    assert blocker is None
    assert warning is not None
    assert warning.code == "concentration_warning"

@pytest.mark.asyncio
async def test_crypto_skips_concentration_when_nlv_none(mock_risk_service):
    with patch("app.services.risk_service.is_crypto_session_open", return_value=True):
        ctx = _make_ctx(qty=Decimal("0.1"), price=Decimal("50000"), account_nlv_base=None)
        blocker, warning = await mock_risk_service._check_crypto_exposure(ctx)
    assert blocker is None
    assert warning is None
```

- [ ] **Step 2: Implement `_check_crypto_exposure`** (Qwen task)

Dispatch to Qwen with prompt:
```
Modify backend/app/services/risk_service.py.

Import at top:
from app.services.market_calendar import is_crypto_session_open, next_crypto_session_open

Add _check_crypto_exposure method after _check_forex_exposure:

    async def _check_crypto_exposure(self, ctx: "EvaluationContext") -> "CheckResult":
        """Phase 15b: Paxos crypto risk checks. Fail-OPEN on infrastructure errors."""
        log = structlog.get_logger(__name__)
        blockers: list[Any] = []
        warnings: list[Any] = []
        try:
            # Session check
            if not is_crypto_session_open():
                retry_at = next_crypto_session_open().isoformat()
                return GateBlockerEntry(
                    check="crypto_session",
                    code="session_closed",
                    message=f"Crypto session closed (maintenance). Next open: {retry_at}",
                ), None

            # Load CryptoDetails meta if instrument_id known
            meta = None
            if ctx.instrument_id is not None:
                result = await self._db.execute(
                    text("SELECT meta FROM instruments WHERE id = :id LIMIT 1"),
                    {"id": ctx.instrument_id}
                )
                row = result.scalar_one_or_none()
                if row:
                    meta_data = json.loads(row) if isinstance(row, str) else row
                    if meta_data.get("asset_class") == "CRYPTO":
                        meta = meta_data

            # Qty precision check
            if meta and meta.get("qty_step"):
                qty_step = Decimal(str(meta["qty_step"]))
                if qty_step > 0:
                    remainder = ctx.qty % qty_step
                    if remainder != 0:
                        blockers.append(GateBlockerEntry(
                            check="crypto_qty_precision",
                            code="invalid_qty_precision",
                            message=f"Qty {ctx.qty} not a multiple of step {qty_step}.",
                        ))
                        return blockers[0], None

            # Min notional check
            if meta and meta.get("min_notional") and ctx.price:
                notional = ctx.qty * ctx.price
                min_notional = Decimal(str(meta["min_notional"]))
                if notional < min_notional:
                    blockers.append(GateBlockerEntry(
                        check="crypto_min_notional",
                        code="below_min_notional",
                        message=f"Notional {notional} below minimum {min_notional}.",
                    ))
                    return blockers[0], None

            # Concentration check (skip if NLV unavailable)
            if ctx.account_nlv_base is not None and ctx.price:
                notional = ctx.qty * ctx.price
                pct = notional / ctx.account_nlv_base * 100 if ctx.account_nlv_base > 0 else 0
                if pct > 20:
                    warnings.append(GateWarningEntry(
                        check="crypto_concentration",
                        code="concentration_warning",
                        message=f"Crypto position is {pct:.1f}% of account NLV (>20%).",
                    ))
            else:
                if ctx.account_nlv_base is None:
                    log.info("crypto_concentration_check_skipped_no_nlv", account_id=str(ctx.account_id))

            # Wide spread advisory for low-liquidity hours (00:00-04:00 UTC)
            now_utc = datetime.now(UTC)
            if 0 <= now_utc.hour < 4:
                warnings.append(GateWarningEntry(
                    check="crypto_spread",
                    code="wide_spread_advisory",
                    message="Low-liquidity hours (00:00–04:00 UTC). Spreads may be wide.",
                ))

        except Exception:
            metrics.crypto_risk_check_failures_total.inc()
            log.exception("crypto_risk_check_infrastructure_error", account_id=str(ctx.account_id))
            return None, None  # fail-OPEN

        if blockers:
            return blockers[0], None
        if warnings:
            return None, warnings[0]
        return None, None

In evaluate() method, add after the FOREX block:
        # Phase 15b: crypto checks
        if ctx.asset_class == "CRYPTO":
            crypto_blocker, crypto_warning = (await self._check_crypto_exposure(ctx)) or (None, None)
            if crypto_blocker is not None:
                return GateVerdict(
                    final_verdict="block",
                    blockers=[crypto_blocker],
                    warnings=[],
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                )
            if crypto_warning is not None:
                pre_warnings = [crypto_warning]

Add 6 Phase 15b Prometheus metrics to backend/app/core/metrics.py:
crypto_assets_list_total, crypto_order_attempts_total, crypto_risk_blocks_total,
crypto_risk_check_failures_total, crypto_position_stream_updates_total, crypto_instrument_resolve_total
(all Counters with appropriate labels per spec §6.5).
```

- [ ] **Step 3: Run tests**

```bash
cd /home/joseph/dashboard && docker compose exec backend pytest backend/tests/services/test_crypto_risk_check.py -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/risk_service.py backend/app/core/metrics.py \
        backend/tests/services/test_crypto_risk_check.py
git commit -m "feat(crypto): _check_crypto_exposure + CRYPTO branch in risk_service.evaluate()"
```

---

### Task 13: WS Gateway — `crypto_book:` subscription type

**Route:** Codex

**Files:**
- Modify: `backend/app/api/ws_gateway.py` (or wherever WS subscriptions are dispatched)
- Modify: `backend/app/main.py` (wire CoinbaseWsAdapter into lifespan)
- Test: `backend/tests/api/test_ws_crypto_book.py`

- [ ] **Step 1: Identify WS gateway subscription dispatch** (read the existing code)

```bash
grep -n "subscription\|subscribe\|channel.*book\|XREAD\|stream" /home/joseph/dashboard/backend/app/api/ws_gateway.py 2>/dev/null | head -20
```

- [ ] **Step 2: Add `crypto_book:` subscription type** (Codex task)

Dispatch to Codex with:
```bash
codex exec -m gpt-5.5 "
Extend the WS gateway (backend/app/api/ws_gateway.py or wherever WS subscriptions are handled)
to support the subscription type 'crypto_book:{canonical_id}'.

On subscribe:
1. Send initial snapshot: read Redis hash crypto:book:snap:{canonical_id} (HGETALL).
   If key not found, send empty snapshot: {type: 'book_snapshot', bids: [], asks: [], canonical_id: canonical_id}.
   If found: parse and send {type: 'book_snapshot', bids: [...], asks: [...], canonical_id: canonical_id}.

2. Consume Redis stream crypto:book:{canonical_id} (XREAD, blocking, block=500ms).
   Push each entry as {type: 'book_delta', side, price, qty, seq, canonical_id}.
   Conflation: max 2 updates/s per subscriber (500ms min interval between sends).
   Use the same conflation pattern used for existing quote subscriptions.

On unsubscribe: stop the stream consumer loop.

Also wire CoinbaseWsAdapter into backend/app/main.py lifespan:
  - Import CoinbaseWsAdapter
  - Create instance: coinbase_adapter = CoinbaseWsAdapter(redis=redis_conn, config_cache=config_cache)
  - In lifespan startup: asyncio.create_task(coinbase_adapter.start())
  - In lifespan shutdown: await coinbase_adapter.stop()

Follow the existing lifespan pattern for quote adapters.
"
```

- [ ] **Step 3: Write WS test**

```python
# backend/tests/api/test_ws_crypto_book.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import json

@pytest.mark.asyncio
async def test_crypto_book_subscribe_sends_initial_snapshot(ws_test_client):
    """On subscribe, WS sends initial snapshot from Redis hash."""
    with patch('app.api.ws_gateway.redis') as mock_redis:
        mock_redis.hgetall.return_value = {
            b'bids': json.dumps([[50000, 0.5], [49999, 1.0]]).encode(),
            b'asks': json.dumps([[50001, 0.3]]).encode(),
        }
        await ws_test_client.send_json({'action': 'subscribe', 'channel': 'crypto_book:BTC.USD'})
        msg = await ws_test_client.receive_json()
    assert msg['type'] == 'book_snapshot'
    assert msg['canonical_id'] == 'BTC.USD'
    assert len(msg['bids']) == 2

@pytest.mark.asyncio
async def test_crypto_book_empty_snapshot_on_miss(ws_test_client):
    with patch('app.api.ws_gateway.redis') as mock_redis:
        mock_redis.hgetall.return_value = {}
        await ws_test_client.send_json({'action': 'subscribe', 'channel': 'crypto_book:ETH.USD'})
        msg = await ws_test_client.receive_json()
    assert msg['type'] == 'book_snapshot'
    assert msg['bids'] == []
    assert msg['asks'] == []
```

- [ ] **Step 4: Run tests**

```bash
cd /home/joseph/dashboard && docker compose exec backend pytest backend/tests/api/test_ws_crypto_book.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/ws_gateway.py \
        backend/app/main.py \
        backend/tests/api/test_ws_crypto_book.py
git commit -m "feat(crypto): WS gateway crypto_book subscription + CoinbaseWsAdapter lifespan"
```

---

### Task 14: Crypto Frontend — types, api, OrderBookDisplay, CryptoDetailsSection, modal, CryptoPage

**Route:** Codex

**Files:**
- Create: `frontend/src/services/crypto/types.ts`
- Create: `frontend/src/services/crypto/api.ts`
- Create: `frontend/src/features/crypto/OrderBookDisplay.tsx`
- Create: `frontend/src/features/crypto/CryptoDetailsSection.tsx`
- Modify: `frontend/src/features/orders/TradeTicketModal.tsx` (add CRYPTO mode)
- Create: `frontend/src/features/crypto/CryptoPage.tsx`
- Create: `frontend/src/routes/crypto.tsx`
- Test: `frontend/src/features/crypto/OrderBookDisplay.test.tsx`
- Test: `frontend/src/features/crypto/CryptoDetailsSection.test.tsx`

- [ ] **Step 1: Write failing OrderBookDisplay test**

```tsx
// frontend/src/features/crypto/OrderBookDisplay.test.tsx
import { render, screen } from '@testing-library/react';
import { OrderBookDisplay } from './OrderBookDisplay';

const mockSnapshot = {
  bids: [{ price: '50000', qty: '0.5', side: 'bid' }, { price: '49999', qty: '1.0', side: 'bid' }],
  asks: [{ price: '50001', qty: '0.3', side: 'ask' }],
  captured_at: new Date().toISOString(),
  seq: 42,
};

test('renders bid and ask levels', () => {
  render(<OrderBookDisplay snapshot={mockSnapshot} isStale={false} />);
  expect(screen.getByText('50000')).toBeInTheDocument();
  expect(screen.getByText('50001')).toBeInTheDocument();
});

test('shows stale badge when isStale', () => {
  render(<OrderBookDisplay snapshot={mockSnapshot} isStale={true} />);
  expect(screen.getByText(/stale/i)).toBeInTheDocument();
});

test('renders spread between best bid and ask', () => {
  render(<OrderBookDisplay snapshot={mockSnapshot} isStale={false} />);
  // Spread = 50001 - 50000 = 1
  expect(screen.getByText(/1\.00/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Implement all crypto frontend files** (Codex task)

Dispatch to Codex with:
```bash
codex exec -m gpt-5.5 "
Create these Phase 15b crypto frontend files:

FILE: frontend/src/services/crypto/types.ts
export interface CryptoAsset {
  canonical_id: string;
  base_asset: string;
  quote_asset: string;
  min_qty: string;
  qty_step: string;
  min_notional: string | null;
}

export interface CryptoPosition {
  instrument_id: number;
  canonical_id: string;
  base_asset: string;
  quote_asset: string;
  qty: string;
  avg_cost: string;
  market_value: string;
  unrealised_pnl: string;
}

export interface CryptoTrade {
  id: string;
  canonical_id: string;
  side: 'BUY' | 'SELL';
  qty: string;
  price: string;
  filled_at: string;
  client_order_id: string;
}

export interface OrderBookLevel {
  price: string;
  qty: string;
  side: 'bid' | 'ask';
}

export interface OrderBookSnapshot {
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
  captured_at: string | null;
  seq: number;
}

FILE: frontend/src/services/crypto/api.ts
import type { CryptoAsset, CryptoPosition, CryptoTrade, OrderBookSnapshot } from './types';

const BASE = '/api/crypto';

export async function listAssets(accountId: string): Promise<CryptoAsset[]> {
  const res = await fetch(\`\${BASE}/assets?account_id=\${accountId}\`, { credentials: 'include' });
  if (!res.ok) throw new Error('Failed to fetch crypto assets');
  return res.json();
}

export async function listPositions(accountId: string): Promise<CryptoPosition[]> {
  const res = await fetch(\`\${BASE}/positions?account_id=\${accountId}\`, { credentials: 'include' });
  if (!res.ok) throw new Error('Failed to fetch crypto positions');
  return res.json();
}

export async function listTrades(accountId: string, before?: string, limit = 50): Promise<CryptoTrade[]> {
  const params = new URLSearchParams({ account_id: accountId, limit: String(limit) });
  if (before) params.set('before', before);
  const res = await fetch(\`\${BASE}/trades?\${params}\`, { credentials: 'include' });
  if (!res.ok) throw new Error('Failed to fetch crypto trades');
  return res.json();
}

export async function getBookSnapshot(canonicalId: string): Promise<OrderBookSnapshot> {
  const res = await fetch(\`\${BASE}/book/\${encodeURIComponent(canonicalId)}\`, { credentials: 'include' });
  if (!res.ok) throw new Error('Failed to fetch order book');
  return res.json();
}

export function subscribeOrderBook(
  canonicalId: string,
  onSnapshot: (s: OrderBookSnapshot) => void,
  onDelta: (d: Partial<OrderBookSnapshot>) => void
): () => void {
  const wsUrl = \`\${location.protocol === 'https:' ? 'wss' : 'ws'}://\${location.host}/ws/gateway\`;
  const ws = new WebSocket(wsUrl);
  ws.onopen = () => ws.send(JSON.stringify({ action: 'subscribe', channel: \`crypto_book:\${canonicalId}\` }));
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === 'book_snapshot') onSnapshot(msg);
    else if (msg.type === 'book_delta') onDelta(msg);
  };
  return () => ws.close();
}

FILE: frontend/src/features/crypto/OrderBookDisplay.tsx
Props: { snapshot: OrderBookSnapshot; isStale: boolean }
- Shows top-10 bids (desc price) and top-10 asks (asc price) in a table
- Spread = best_ask - best_bid shown between them as 'Spread: {spread}'
- Size bars: each row has a div width proportional to qty/max_qty (use useRef + manual DOM for performance)
- Amber badge 'Stale' shown when isStale=true (last update > 5s → passed from parent)
- Use useRef for size-bar DOM updates to avoid React reconcile at 2/s

FILE: frontend/src/features/crypto/CryptoDetailsSection.tsx
Props: { asset: CryptoAsset; lastPrice: string | null }
Shows: base_asset/quote_asset pair name, min_qty, qty_step, 24h price (lastPrice from Coinbase feed if available)
Mirrors FutureDetailsSection pattern from frontend/src/features/futures/FutureDetailsSection.tsx

Modify frontend/src/features/orders/TradeTicketModal.tsx to:
- Import CryptoDetailsSection and FractionalQtyInput
- Detect asset_class === 'CRYPTO' → inject CryptoDetailsSection above the quantity input
- Replace quantity input with FractionalQtyInput when asset_class === 'CRYPTO'
  (step=CryptoDetails.qty_step, decimals=8)
- Standard MKT/LMT flow (no RFQ for crypto)

FILE: frontend/src/features/crypto/CryptoPage.tsx
Four-panel layout (grid 2×2 on desktop, tabs on mobile):
1. Asset browser (left): list from listAssets(); live last price from WS quote bus;
   24h change % from Coinbase ticker; click selects asset.
2. Order book (top-right): OrderBookDisplay for selected asset, updated via subscribeOrderBook().
   Track last update time to show stale badge if > 5s.
3. Positions + P&L (bottom-left): listPositions() with unrealised_pnl column; 
   'Trades' tab with listTrades().
4. Trade panel (bottom-right): FractionalQtyInput for qty; 'Review' button opens TradeTicketModal
   in CRYPTO mode for the selected asset.

FILE: frontend/src/routes/crypto.tsx
import { createFileRoute } from '@tanstack/react-router';
import { CryptoPage } from '@/features/crypto/CryptoPage';
export const Route = createFileRoute('/crypto')({ component: CryptoPage });
"
```

- [ ] **Step 3: Run FE tests**

```bash
cd /home/joseph/dashboard/frontend && pnpm test -- --run OrderBookDisplay CryptoDetailsSection
```
Expected: PASS

- [ ] **Step 4: Run type check**

```bash
cd /home/joseph/dashboard/frontend && pnpm tsr generate && pnpm typecheck
```
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/services/crypto/ \
        frontend/src/features/crypto/ \
        frontend/src/routes/crypto.tsx \
        frontend/src/features/orders/TradeTicketModal.tsx
git commit -m "feat(crypto): CryptoPage + OrderBookDisplay + CryptoDetailsSection + modal crypto mode"
```

---

### Task 15: Integration tests + close-out

**Route:** Codex

**Files:**
- Create: `backend/tests/integration/test_crypto_full_flow.py`
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/TASKS.md`
- Modify: `CLAUDE.md` (Phase 15 cross-cutting rules block)

- [ ] **Step 1: Write crypto integration tests** (Codex task)

Dispatch to Codex with:
```bash
codex exec -m gpt-5.5 "
Write backend/tests/integration/test_crypto_full_flow.py:

1. test_list_assets_populates_instruments_and_cache:
   - Mock sidecar ListCryptoAssets returns BTC.USD and ETH.USD
   - GET /api/crypto/assets?account_id=...
   - Assert 2 instruments rows with asset_class=CRYPTO
   - Assert Redis cache key set with 5min TTL

2. test_place_crypto_order_routes_through_existing_place_order:
   - Use existing POST /api/orders/place with instrument of asset_class=CRYPTO
   - Assert: goes through risk gate CRYPTO branch (session open check)
   - Assert: orders row created (not rejected by calibration error)

3. test_crypto_risk_gate_blocks_when_session_closed:
   - Patch is_crypto_session_open to return False
   - POST /api/orders/preview with CRYPTO instrument
   - Assert: 422 with code='session_closed'

4. test_coinbase_book_ws_sends_snapshot:
   - Pre-populate Redis hash crypto:book:snap:BTC.USD
   - Connect WS and subscribe to crypto_book:BTC.USD
   - Assert: first message is type='book_snapshot'

5. test_balance_snapshot_writer_writes_nlv_redis:
   - Call BalanceSnapshotWriter.record() inside a test session
   - Assert: redis.set called with key matching 'account:nlv:{account_id}:{currency}'
   - Assert: TTL is 15 seconds

Use AsyncClient from httpx + the FastAPI app test fixture from conftest.py.
"
```

- [ ] **Step 2: Run all new tests as final verification**

```bash
cd /home/joseph/dashboard && docker compose exec backend pytest \
  backend/tests/test_alembic_0051.py \
  backend/tests/test_alembic_0052.py \
  backend/tests/services/test_forex_calendar.py \
  backend/tests/services/test_forex_instrument_resolver.py \
  backend/tests/services/test_forex_risk_check.py \
  backend/tests/services/test_rfq_service.py \
  backend/tests/services/test_rfq_sweep.py \
  backend/tests/services/test_book_manager.py \
  backend/tests/services/test_coinbase_ws.py \
  backend/tests/services/test_crypto_service.py \
  backend/tests/services/test_crypto_risk_check.py \
  backend/tests/api/test_forex_api.py \
  backend/tests/api/test_crypto_api.py \
  backend/tests/api/test_ws_crypto_book.py \
  backend/tests/integration/test_forex_rfq_flow.py \
  backend/tests/integration/test_crypto_full_flow.py \
  -v 2>&1 | tee /tmp/phase15_test_results.txt
```

- [ ] **Step 3: Run FE full test suite**

```bash
cd /home/joseph/dashboard/frontend && pnpm test -- --run 2>&1 | tee /tmp/phase15_fe_test_results.txt
```

- [ ] **Step 4: Update CHANGELOG.md**

Add Phase 15 entry:
```markdown
## [v0.15.1] — 2026-05-18

### Phase 15b — IBKR Paxos Crypto + Coinbase WS

- CryptoDetails discriminated-union arm + alembic 0052 + crypto_order_book_snapshots hypertable (7d retention, 1h CAGG)
- CoinbaseWsAdapter: L1 ticker + L2 incremental order book with sequence-gap recovery + bounded depth (N=100)
- OrderBook dataclass (book_manager.py) with apply_delta + snapshot
- CryptoService: ListCryptoAssets + instrument upsert + 5-min cache
- /api/crypto: assets, positions, trades, book endpoints
- _check_crypto_exposure: session gate, qty precision, min notional, concentration WARN (NLV-denominated), wide-spread advisory
- WS gateway crypto_book: subscription type (initial snapshot + XREAD stream deltas, 2/s conflation)
- BalanceSnapshotWriter extended: writes account:nlv:{id}:{ccy} Redis key (15s TTL) for concentration check
- CryptoPage 4-panel workspace + /crypto route
- OrderBookDisplay, CryptoDetailsSection, TradeTicketModal crypto mode
- 6 Prometheus metrics + CryptoCalendar 24/7 configurable maintenance windows

## [v0.15.0] — 2026-05-18

### Phase 15a — IBKR IDEALPRO FX + RFQ

- ForexDetails discriminated-union arm + alembic 0051 + forex_rfq_quotes table (with request_id, order_id FK, accepting status)
- RFQ three-state machine: pending → accepting → accepted | rejected (separate DB sessions around sidecar RPC)
- ForexInstrumentResolver (read-only) + _ensure_forex_instrument upsert helper
- ForexCalendar (24/5, 17:00–17:15 ET daily gap) + CryptoCalendar (24/7 + configurable blackouts)
- _check_forex_exposure: session gate, notional cap (limit_kind row), consolidation WARN
- /api/forex: quote, accept (CSRF single-use nonce), cancel, quotes list, pairs
- APScheduler TTL sweep (5s)
- SearchContracts sidecar: FOREX→CASH/IDEALPRO + CRYPTO→CRYPTO/PAXOS secType map
- FxTicketSection + FxQuoteDisplay (TTL countdown, amber badge, expired state)
- FractionalQtyInput primitive (src/components/primitives/)
- ForexPage 4-panel workspace + /forex route
- 7 Prometheus metrics
```

- [ ] **Step 5: Update TASKS.md to close Phase 15**

Mark Phase 15 rows as done in TASKS.md.

- [ ] **Step 6: Commit close-out**

```bash
git add docs/CHANGELOG.md docs/TASKS.md
git commit -m "docs: close out Phase 15 — Forex + Crypto v0.15.0+v0.15.1"
git tag v0.15.1
git push && git push --tags
```

---

## Self-Review Against Spec

### Spec Coverage Check

| Spec Section | Covered by Task |
|---|---|
| §2.1 `forex_rfq_quotes` + `ForexDetails` + `EvaluationContext.account_nlv_base` | Task 1 |
| §2.2 `crypto_order_book_snapshots` hypertable + CAGG + `CryptoDetails` | Task 9 |
| §3.1 `ForexCalendar` | Task 2 |
| §3.2 `CryptoCalendar` | Task 2 |
| §3.3 Risk gate integration + `BalanceSnapshotWriter` Redis NLV write | Task 10 (NLV) + Task 3 (FX gate) + Task 12 (crypto gate) |
| §4.1 Proto: 4 FX RPCs + FxMidRate | Task 4 |
| §4.2 `rfq_service.py` (3-state, ON CONFLICT, NOT-NULL columns) | Task 5 |
| §4.3 `app/api/forex.py` (5 endpoints + CSRF) | Task 5 |
| §4.4 `_check_forex_exposure` | Task 3 |
| §4.5 7 Prometheus metrics (15a) | Task 6 |
| §5.1 `services/forex/types.ts` + `api.ts` | Task 7 |
| §5.2 `FxTicketSection` + `FxQuoteDisplay` + modal FX mode | Task 7 |
| §5.3 `ForexPage` 4-panel + `/forex` route | Task 8 |
| §6.1 `ListCryptoAssets` proto RPC | Task 10 |
| §6.2 `crypto_service.py` + sidecar PAXOS branch | Task 10 |
| §6.3 `app/api/crypto.py` (4 endpoints) | Task 10 |
| §6.4 `_check_crypto_exposure` | Task 12 |
| §6.5 6 Prometheus metrics (15b) | Task 12 |
| §7.1 `coinbase_ws.py` (L1+L2, seq gap, bounded depth) | Task 11 |
| §7.2 `book_manager.py` (`OrderBook`, `apply_delta`, `snapshot`) | Task 11 |
| §7.3 WS gateway `crypto_book:` subscription | Task 13 |
| §7.4 4 Prometheus metrics (Coinbase) | Task 11 |
| §8.1 `FractionalQtyInput` in primitives/ + `lib/decimal.ts` | Task 7 |
| §8.2 `services/crypto/types.ts` + `api.ts` | Task 14 |
| §8.3 `OrderBookDisplay.tsx` | Task 14 |
| §8.4 TradeTicketModal crypto mode + `CryptoDetailsSection` | Task 14 |
| §8.5 `CryptoPage` 4-panel + `/crypto` route | Task 14 |
| APScheduler TTL sweep 5s | Task 6 |
| `SearchContracts` FOREX→CASH/IDEALPRO + CRYPTO→CRYPTO/PAXOS | Task 4 |
| `_resolve_contract` PlaceOrder hot-path verification | Task 4 (included in Codex dispatch) |
| Integration tests (RFQ flow, Coinbase WS mock, risk gate) | Tasks 8, 15 |
| CHANGELOG + TASKS close-out | Task 15 |

All spec sections covered. No gaps.

### Type Consistency Check

- `FxQuote.broker_quote_id: string` → used in `acceptQuote(brokerQuoteId: string, ...)` ✓
- `OrderBookLevel.{price, qty, side}` → `OrderBookDisplay` Props use `OrderBookSnapshot.{bids, asks}` ✓
- `FractionalQtyInput` imported from `@/components/primitives/FractionalQtyInput` in both `FxTicketSection` and `CryptoPage` ✓
- `EvaluationContext.account_nlv_base: Decimal | None` → used in `_check_crypto_exposure` ✓
- `forex_rfq_quotes.request_id` → populated at `request_quote`, reused at `accept_quote` ✓
- `orders` NOT-NULL columns in `accept_quote`: `conid`, `symbol`, `order_type='MARKET'`, `tif='IOC'`, `notional=qty×fill_price` ✓
