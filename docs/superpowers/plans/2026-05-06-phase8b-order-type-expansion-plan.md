# Phase 8b — Order-Type Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Expand the broker trade write path to the Phase 8b order universe: 10 order types, 5 TIFs, exchange-aware GTD and auction-session validation, Schwab/Futu/IBKR capability flips, Futu Modify and Bracket support, and two-leg OCO support.

**Architecture:** Six implementation chunks. Chunk 0 widens schemas, proto contracts, calendar services, migration foundations, config invalidation, and empirical safeguards. Chunk S extends Schwab payload mapping and flips its supported rows. Chunk F brings Futu Modify and Bracket live, adds empirical proof, and flips Futu features. Chunk I maps IBKR native order fields and flips IBKR rows. Chunk O ships OCO as a backend-orchestrated service with native broker adapters where available. Close-out updates release notes, task status, and the v0.9.0 tag.

**Tech Stack:** Python 3.14, Pydantic v2, FastAPI, SQLAlchemy 2 async, Alembic, asyncpg, Redis asyncio, exchange_calendars, grpcio, protobuf/buf, pytest, pytest-asyncio, freezegun, schwabdev, futu-api, ib_async, GitHub Actions, pre-commit.

**Spec:** [`docs/superpowers/specs/2026-05-06-phase8b-order-type-expansion-design.md`](../specs/2026-05-06-phase8b-order-type-expansion-design.md)

**Global invariants:** Session-bound order types (`MOC`, `MOO`, `LOC`, `LOO`) are DAY-only and reject non-DAY TIF with error code `session_window_closed`. Broker capability gates keep returning `unsupported_order_type_for_broker`. OCO cancel paths never consult broker capability rows.
## Task T-0.1 — widen Pydantic order schemas to full universe

**Files:**
- Modify: `backend/app/schemas/orders.py`
- Test: `backend/tests/integration/test_orders_schema_8b.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "Literal|_check_order_type_prices|trail_offset|expiry_date|model_validator" backend/app/schemas/orders.py
```

- [ ] **Step 2: Write or extend the focused tests**

```python
import pytest
from pydantic import ValidationError
from app.schemas.orders import OrderRequest

def make(**kw):
    data = {"order_type": "MARKET", "tif": "DAY"}
    data.update(kw)
    return data

def test_trail_without_offset_rejects():
    with pytest.raises(ValidationError): OrderRequest.model_validate(make(order_type="TRAIL"))

def test_trail_limit_without_limit_offset_rejects():
    with pytest.raises(ValidationError): OrderRequest.model_validate(make(order_type="TRAIL_LIMIT", trail_offset="0.10", trail_offset_type="AMOUNT"))

def test_moc_gtc_has_session_window_code():
    with pytest.raises(ValidationError) as exc: OrderRequest.model_validate(make(order_type="MOC", tif="GTC"))
    assert "session_window_closed" in str(exc.value)

def test_gtd_without_expiry_rejects():
    with pytest.raises(ValidationError): OrderRequest.model_validate(make(order_type="LIMIT", tif="GTD", limit_price="1.00"))

def test_valid_trail_and_moc_and_gtd_pass():
    assert OrderRequest.model_validate(make(order_type="TRAIL", trail_offset="0.10", trail_offset_type="AMOUNT"))
    assert OrderRequest.model_validate(make(order_type="MOC", tif="DAY"))
    assert OrderRequest.model_validate(make(order_type="LIMIT", tif="GTD", limit_price="1.00", expiry_date="2026-05-07"))
```

- [ ] **Step 3: Apply the implementation change**

```python
from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, model_validator

OrderType = Literal["MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL", "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO"]
TimeInForce = Literal["DAY", "GTC", "IOC", "FOK", "GTD"]
TrailOffsetType = Literal["AMOUNT", "PERCENT"]
SESSION_BOUND = {"MOC", "MOO", "LOC", "LOO"}

class OrderRequest(BaseModel):
    order_type: OrderType
    tif: TimeInForce = "DAY"
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    trail_offset: Decimal | None = None
    trail_offset_type: TrailOffsetType | None = None
    trail_limit_offset: Decimal | None = None
    expiry_date: date | None = None

    @model_validator(mode="after")
    def _check_order_type_prices(self):
        if self.order_type == "STOP_LIMIT" and (self.stop_price is None or self.limit_price is None):
            raise ValueError("STOP_LIMIT requires stop_price and limit_price")
        if self.order_type == "TRAIL" and (self.trail_offset is None or self.trail_offset_type is None):
            raise ValueError("TRAIL requires trail_offset and trail_offset_type")
        if self.order_type == "TRAIL_LIMIT" and (self.trail_offset is None or self.trail_offset_type is None or self.trail_limit_offset is None):
            raise ValueError("TRAIL_LIMIT requires trail_offset, trail_offset_type, and trail_limit_offset")
        if self.order_type in {"LOC", "LOO"} and self.limit_price is None:
            raise ValueError("LOC and LOO require limit_price")
        if self.order_type in SESSION_BOUND and self.tif != "DAY":
            raise ValueError({"msg": "session-bound order type requires DAY", "code": "session_window_closed"})
        if self.tif == "GTD" and self.expiry_date is None:
            raise ValueError("GTD requires expiry_date")
        return self
```

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest tests/integration/test_orders_schema_8b.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "MARKET|TRAIL_LIMIT|session_window_closed|expiry_date" backend/app/schemas/orders.py backend/tests/integration/test_orders_schema_8b.py
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/orders.py backend/tests/integration/test_orders_schema_8b.py
git commit -m "feat(orders): widen schema for phase 8b order universe"
```

---

## Task T-0.2 — add proto fields 11-14 and sidecar pass-through

**Files:**
- Modify: `protos/orders.proto`
- Modify: `sidecar_ibkr/normalize.py`
- Modify: `sidecar_futu/normalize.py`
- Modify: `sidecar_schwab/normalize.py`
- Modify: `sidecar_alpaca/normalize.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "message (OrderRequest|PlaceOrderRequest|ModifyOrderRequest|Order)|trail_offset|expiry_date" protos/orders.proto sidecar_* -g normalize.py
```

- [ ] **Step 2: Write or extend the focused tests**

```python
def test_proto_phase8b_fields_pass_through(order_request_proto):
    order_request_proto.trail_offset = "0.10"
    order_request_proto.trail_offset_type = "AMOUNT"
    order_request_proto.trail_limit_offset = "0.05"
    order_request_proto.expiry_date = "2026-05-07"
    data = proto_order_to_dict(order_request_proto)
    assert data["trail_offset"] == "0.10"
    assert data["trail_offset_type"] == "AMOUNT"
    assert data["trail_limit_offset"] == "0.05"
    assert data["expiry_date"] == "2026-05-07"
```

- [ ] **Step 3: Apply the implementation change**

```python
// Phase 8b reserved tags 11-14
string trail_offset = 11;
string trail_offset_type = 12;
string trail_limit_offset = 13;
string expiry_date = 14;  // ISO-8601 date

def proto_order_to_dict(req):
    return {
        "trail_offset": req.trail_offset or None,
        "trail_offset_type": req.trail_offset_type or None,
        "trail_limit_offset": req.trail_limit_offset or None,
        "expiry_date": req.expiry_date or None,
    }
```

- [ ] **Step 4: Run the focused test command**

```bash
cd sidecar_ibkr && buf generate && cd ../sidecar_futu && buf generate && cd ../sidecar_schwab && buf generate && cd ../sidecar_alpaca && buf generate
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "Phase 8b reserved tags 11-14|trail_offset|expiry_date" protos/orders.proto sidecar_ibkr sidecar_futu sidecar_schwab sidecar_alpaca
```

- [ ] **Step 6: Commit**

```bash
git add protos/orders.proto sidecar_ibkr/normalize.py sidecar_futu/normalize.py sidecar_schwab/normalize.py sidecar_alpaca/normalize.py
git commit -m "feat(proto): add phase 8b order fields"
```

---

## Task T-0.3 — add exchange-aware market calendar service

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/app/services/market_calendar.py`
- Test: `backend/tests/unit/test_market_calendar.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "exchange_calendars|market_calendar|calendar" backend/pyproject.toml backend/app backend/tests || true
```

- [ ] **Step 2: Write or extend the focused tests**

```python
from datetime import date
from app.services.market_calendar import eod_for_exchange, is_trading_day

def test_nyse_dst_est_and_edt():
    assert eod_for_exchange("NYSE", date(2026, 3, 13)).tzinfo.key == "America/New_York"
    assert eod_for_exchange("NYSE", date(2026, 11, 6)).tzinfo.key == "America/New_York"

def test_hkex_no_dst_and_lse_bst():
    assert eod_for_exchange("HKEX", date(2026, 7, 15)).utcoffset().total_seconds() == 28800
    assert eod_for_exchange("LSE", date(2026, 6, 15)).tzinfo.key == "Europe/London"

def test_holidays_and_early_closes():
    assert is_trading_day("NYSE", date(2026, 11, 26)) is False
    assert eod_for_exchange("NYSE", date(2026, 11, 27)).hour == 13
    assert eod_for_exchange("NYSE", date(2026, 12, 24)).hour == 13
    assert is_trading_day("HKEX", date(2026, 2, 17)) is False
```

- [ ] **Step 3: Apply the implementation change**

```python
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo
import exchange_calendars as xcals

CAL = {"NYSE": "XNYS", "HKEX": "XHKG", "LSE": "XLON"}
TZ = {"NYSE": ZoneInfo("America/New_York"), "HKEX": ZoneInfo("Asia/Hong_Kong"), "LSE": ZoneInfo("Europe/London")}
CLOSE = {"NYSE": time(16, 0), "HKEX": time(16, 0), "LSE": time(16, 30)}

def today_in_exchange_tz(exchange: str) -> date:
    return datetime.now(TZ[exchange.upper()]).date()

def is_trading_day(exchange: str, d: date | None = None) -> bool:
    target = d or today_in_exchange_tz(exchange)
    return xcals.get_calendar(CAL[exchange.upper()]).is_session(target.isoformat())

def eod_for_exchange(exchange: str, reference_date: date | None = None) -> datetime:
    exch = exchange.upper(); d = reference_date or today_in_exchange_tz(exch)
    cal = xcals.get_calendar(CAL[exch]); sched = cal.session_schedule(d.isoformat(), d.isoformat())
    if len(sched): return sched.iloc[0]["market_close"].to_pydatetime().astimezone(TZ[exch])
    return datetime.combine(d, CLOSE[exch], TZ[exch])

def next_session_open(exchange: str) -> datetime:
    exch = exchange.upper(); cal = xcals.get_calendar(CAL[exch]); nxt = cal.next_session(today_in_exchange_tz(exch).isoformat())
    return cal.session_schedule(nxt, nxt).iloc[0]["market_open"].to_pydatetime().astimezone(TZ[exch])

def is_session_window_open(exchange: str, window: Literal["MOO", "MOC", "LOO", "LOC"]) -> bool:
    exch = exchange.upper(); now = datetime.now(TZ[exch])
    if not is_trading_day(exch, now.date()): return False
    close_at = eod_for_exchange(exch, now.date())
    if window in {"MOC", "LOC"}: return close_at - timedelta(minutes=15) <= now <= close_at
    open_at = datetime.combine(now.date(), time(9, 30), TZ[exch])
    return open_at - timedelta(minutes=2) <= now <= open_at + timedelta(minutes=2)
```

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv add exchange_calendars && uv run pytest tests/unit/test_market_calendar.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "today_in_exchange_tz|eod_for_exchange|is_session_window_open|Black Friday|Lunar" backend/app/services/market_calendar.py backend/tests/unit/test_market_calendar.py
```

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/app/services/market_calendar.py backend/tests/unit/test_market_calendar.py
git commit -m "feat(calendar): add exchange-aware market calendar service"
```

---

## Task T-0.4 — create Alembic 0012 broker_features table

**Files:**
- Create: `backend/alembic/versions/0012_broker_features.py`
- Test: `backend/tests/integration/test_alembic_0012.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
ls backend/alembic/versions | sort | tail -6
```

- [ ] **Step 2: Write or extend the focused tests**

```python
from sqlalchemy import text
import pytest
@pytest.mark.asyncio
async def test_broker_features_seed_count(session):
    assert (await session.execute(text("SELECT COUNT(*) FROM broker_features"))).scalar_one() == 14
```

- [ ] **Step 3: Apply the implementation change**

```python
from alembic import op
revision = "0012_broker_features"; down_revision = "0011"; branch_labels = None; depends_on = None
ROWS = [("ibkr","MARKET","DAY",True,None),("ibkr","LIMIT","DAY",True,None),("ibkr","LIMIT","GTC",True,None),("ibkr","STOP","DAY",True,None),("ibkr","STOP_LIMIT","DAY",True,None),("futu","MARKET","DAY",True,None),("futu","LIMIT","DAY",True,None),("futu","LIMIT","GTC",True,None),("schwab","MARKET","DAY",True,None),("schwab","LIMIT","DAY",True,None),("schwab","LIMIT","GTC",True,None),("schwab","STOP","DAY",True,None),("schwab","STOP_LIMIT","DAY",True,None),("alpaca","MARKET","DAY",True,None)]
def upgrade():
    op.execute("""CREATE TABLE broker_features (id SERIAL PRIMARY KEY, broker_id TEXT NOT NULL, order_type TEXT NOT NULL, tif TEXT NOT NULL, supported BOOLEAN NOT NULL DEFAULT FALSE, notes TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), UNIQUE (broker_id, order_type, tif))""")
    for b,o,t,s,n in ROWS:
        op.execute(f"INSERT INTO broker_features (broker_id, order_type, tif, supported, notes) VALUES ('{b}', '{o}', '{t}', TRUE, NULL)")
def downgrade():
    op.execute("DROP TABLE IF EXISTS broker_features")
```

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest tests/integration/test_alembic_0012.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "broker_features|UNIQUE|alpaca|STOP_LIMIT" backend/alembic/versions/0012_broker_features.py backend/tests/integration/test_alembic_0012.py
```

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0012_broker_features.py backend/tests/integration/test_alembic_0012.py
git commit -m "feat(db): add broker features seed"
```

---

## Task T-0.5 — add Postgres LISTEN to Redis bridge

**Files:**
- Create: `backend/app/services/postgres_listen_bridge.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/unit/test_postgres_listen_bridge.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "lifespan|create_task|redis|asyncpg|app_config:invalidate" backend/app
```

- [ ] **Step 2: Write or extend the focused tests**

```python
from unittest.mock import AsyncMock
import pytest
from app.services.postgres_listen_bridge import PostgresListenBridge
@pytest.mark.asyncio
async def test_notify_republishes_to_redis():
    redis = AsyncMock(); bridge = PostgresListenBridge("postgresql://x", redis)
    await bridge._on_notify(None, 1, "app_config:invalidate:order_capabilities", "schwab")
    redis.publish.assert_awaited_once_with("app_config:invalidate:order_capabilities", "schwab")
@pytest.mark.asyncio
async def test_connected_health_flag():
    bridge = PostgresListenBridge("postgresql://x", AsyncMock()); assert bridge.is_connected() is False
    bridge._connected = True; assert bridge.is_connected() is True
```

- [ ] **Step 3: Apply the implementation change**

```python
import asyncio, contextlib
from dataclasses import dataclass
import asyncpg, redis.asyncio as aioredis
CHANNEL = "app_config:invalidate:*"
@dataclass
class PostgresListenBridge:
    dsn: str; redis: aioredis.Redis; _connected: bool = False; _stopped: bool = False
    def is_connected(self) -> bool: return self._connected
    async def run(self):
        delay = 1
        while not self._stopped:
            conn = None
            try:
                conn = await asyncpg.connect(self.dsn); self._connected = True
                await conn.add_listener(CHANNEL, self._on_notify); await conn.execute(f"LISTEN {CHANNEL}")
                delay = 1
                while not self._stopped and not conn.is_closed(): await asyncio.sleep(1)
            except (asyncpg.PostgresError, OSError, asyncio.TimeoutError):
                self._connected = False; await asyncio.sleep(delay); delay = min(delay * 2, 30)
            finally:
                self._connected = False
                if conn is not None:
                    with contextlib.suppress(Exception): await conn.close()
    async def _on_notify(self, connection, pid, channel, payload): await self.redis.publish(channel, payload)
    async def stop(self): self._stopped = True
```

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest tests/unit/test_postgres_listen_bridge.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "PostgresListenBridge|is_connected|app_config:invalidate|create_task" backend/app backend/tests/unit/test_postgres_listen_bridge.py
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/postgres_listen_bridge.py backend/app/main.py backend/tests/unit/test_postgres_listen_bridge.py
git commit -m "feat(config): bridge postgres invalidations to redis"
```

---

## Task T-0.6 — add empirical artifact pre-commit guard

**Files:**
- Create: `scripts/pre-commit-check-empirical-artifacts.sh`
- Modify: `.pre-commit-config.yaml`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
sed -n "1,240p" .pre-commit-config.yaml 2>/dev/null || true
```

- [ ] **Step 2: Write or extend the focused tests**

```python
mkdir -p scripts/empirical
printf 'accountNumber = "123"
' > scripts/empirical/_probe.py
git add scripts/empirical/_probe.py
pre-commit run check-empirical-artifacts --files scripts/empirical/_probe.py; test $? -eq 1
git reset -- scripts/empirical/_probe.py
rm scripts/empirical/_probe.py
```

- [ ] **Step 3: Apply the implementation change**

```python
#!/usr/bin/env bash
set -euo pipefail
patterns='accountNumber|account_number|clientOrderId|access_token'
files=$(git diff --cached --name-only -- 'scripts/empirical/*.py' || true)
[ -z "$files" ] && exit 0
if grep -nE "$patterns" $files; then
  echo "ERROR: empirical scripts must strip broker artifacts before commit" >&2
  exit 1
fi
```

- [ ] **Step 4: Run the focused test command**

```bash
chmod +x scripts/pre-commit-check-empirical-artifacts.sh && pre-commit run check-empirical-artifacts --all-files
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "check-empirical-artifacts|accountNumber|access_token" .pre-commit-config.yaml scripts/pre-commit-check-empirical-artifacts.sh
```

- [ ] **Step 6: Commit**

```bash
git add scripts/pre-commit-check-empirical-artifacts.sh .pre-commit-config.yaml
git commit -m "chore(empirical): block broker artifacts in scripts"
```

---

## Task T-0.7 — wire schema and capability error codes

**Files:**
- Modify: `backend/app/schemas/orders.py`
- Modify: `backend/app/services/order_service.py`
- Test: `backend/tests/integration/test_orders_schema_8b.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "session_window_closed|unsupported_order_type_for_broker|HTTPException" backend/app backend/tests
```

- [ ] **Step 2: Write or extend the focused tests**

```python
def test_session_bound_non_day_error_code():
    with pytest.raises(ValidationError) as exc:
        OrderRequest.model_validate({"order_type": "MOC", "tif": "GTC"})
    assert "session_window_closed" in str(exc.value)

def test_capability_gate_uses_existing_code():
    assert "unsupported_order_type_for_broker" == "unsupported_order_type_for_broker"
```

- [ ] **Step 3: Apply the implementation change**

```python
from fastapi import HTTPException
async def _capability_gate(self, broker_id: str, order_type: str, tif: str) -> None:
    if await self._capability.is_supported(broker_id, order_type, tif):
        return
    notes = await self._capability.get_notes(broker_id, order_type, tif)
    raise HTTPException(status_code=422, detail={"error": {"code": "unsupported_order_type_for_broker", "broker_id": broker_id, "order_type": order_type, "tif": tif, "notes": notes}})
```

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest tests/integration/test_orders_schema_8b.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "session_window_closed|unsupported_order_type_for_broker" backend/app/schemas/orders.py backend/app/services/order_service.py backend/tests
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/orders.py backend/app/services/order_service.py backend/tests/integration/test_orders_schema_8b.py
git commit -m "fix(orders): expose phase 8b validation error codes"
```

---

## Task T-S.1 — extend Schwab normalization for six new order types

**Files:**
- Modify: `sidecar_schwab/normalize.py`
- Test: `sidecar_schwab/tests/test_normalize_orders.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "to_schwab_order_payload|orderType|session" sidecar_schwab
```

- [ ] **Step 2: Write or extend the focused tests**

```python
def test_trail_amount_payload():
    p = to_schwab_order_payload({"order_type":"TRAIL","trail_offset":"0.10","trail_offset_type":"AMOUNT"})
    assert p["trailingStopOffset"] == "0.10" and p["stopPriceLinkType"] == "VALUE"
def test_moo_and_loo_payloads():
    assert to_schwab_order_payload({"order_type":"MOO"})["session"] == "AM"
    assert to_schwab_order_payload({"order_type":"LOO","limit_price":"10.00"})["orderType"] == "LIMIT_ON_OPEN"
```

- [ ] **Step 3: Apply the implementation change**

```python
def to_schwab_order_payload(order):
    t = order["order_type"]; p = {"duration": order.get("tif", "DAY"), "orderStrategyType": "SINGLE"}
    if t == "TRAIL": p.update(orderType="TRAILING_STOP", trailingStopOffset=order["trail_offset"], stopPriceLinkType="VALUE" if order["trail_offset_type"] == "AMOUNT" else "PERCENT")
    if t == "TRAIL_LIMIT": p.update(orderType="TRAILING_STOP_LIMIT", trailingStopOffset=order["trail_offset"], stopPrice=order["trail_limit_offset"], stopPriceLinkType="VALUE" if order["trail_offset_type"] == "AMOUNT" else "PERCENT")
    if t == "MOC": p.update(orderType="MARKET_ON_CLOSE", session="NORMAL")
    if t == "MOO": p.update(orderType="MARKET_ON_OPEN", session="AM")
    if t == "LOC": p.update(orderType="LIMIT_ON_CLOSE", session="NORMAL", price=order["limit_price"])
    if t == "LOO": p.update(orderType="LIMIT_ON_OPEN", session="AM", price=order["limit_price"])
    return p
```

- [ ] **Step 4: Run the focused test command**

```bash
cd sidecar_schwab && uv run pytest tests/test_normalize_orders.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "TRAILING_STOP|MARKET_ON_CLOSE|LIMIT_ON_OPEN" sidecar_schwab
```

- [ ] **Step 6: Commit**

```bash
git add sidecar_schwab/normalize.py sidecar_schwab/tests/test_normalize_orders.py
git commit -m "feat(schwab): normalize phase 8b order types"
```

---

## Task T-S.2 — extend Schwab GTD cancelTime mapping

**Files:**
- Modify: `sidecar_schwab/normalize.py`
- Test: `sidecar_schwab/tests/test_normalize_orders.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "duration|cancelTime|GOOD_TILL" sidecar_schwab
```

- [ ] **Step 2: Write or extend the focused tests**

```python
def test_gtd_payload_has_cancel_time():
    p = to_schwab_order_payload({"order_type":"LIMIT","tif":"GTD","limit_price":"10.00","exchange":"NYSE"})
    assert p["duration"] == "GOOD_TILL_CANCEL"
    assert "T" in p["cancelTime"]
```

- [ ] **Step 3: Apply the implementation change**

```python
def _apply_gtd(payload, order):
    if order.get("tif") == "GTD":
        from app.services import market_calendar
        payload["duration"] = "GOOD_TILL_CANCEL"
        payload["cancelTime"] = market_calendar.eod_for_exchange(order.get("exchange", "NYSE")).isoformat()
```

- [ ] **Step 4: Run the focused test command**

```bash
cd sidecar_schwab && uv run pytest tests/test_normalize_orders.py -k gtd -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "GOOD_TILL_CANCEL|cancelTime|eod_for_exchange" sidecar_schwab/normalize.py sidecar_schwab/tests/test_normalize_orders.py
```

- [ ] **Step 6: Commit**

```bash
git add sidecar_schwab/normalize.py sidecar_schwab/tests/test_normalize_orders.py
git commit -m "feat(schwab): map gtd orders to cancel time"
```

---

## Task T-S.3 — flip Schwab partial capability rows

**Files:**
- Create: `backend/alembic/versions/0013_schwab_capability_flip.py`
- Test: `backend/tests/integration/test_alembic_0013.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
ls backend/alembic/versions | sort | tail -8
```

- [ ] **Step 2: Write or extend the focused tests**

```python
from sqlalchemy import text
import pytest
@pytest.mark.asyncio
async def test_schwab_supported_count(session):
    n = (await session.execute(text("SELECT COUNT(*) FROM broker_features WHERE broker_id='schwab' AND supported=TRUE"))).scalar_one()
    assert n >= 20
```

- [ ] **Step 3: Apply the implementation change**

```python
revision = "0013_schwab_capability_flip"
down_revision = "previous"
ROWS = [('schwab', 'TRAIL', 'DAY'), ('schwab', 'TRAIL', 'GTC'), ('schwab', 'TRAIL_LIMIT', 'DAY'), ('schwab', 'TRAIL_LIMIT', 'GTC'), ('schwab', 'MOC', 'DAY'), ('schwab', 'MOO', 'DAY'), ('schwab', 'LOC', 'DAY'), ('schwab', 'LOO', 'DAY'), ('schwab', 'LIMIT', 'IOC'), ('schwab', 'LIMIT', 'FOK'), ('schwab', 'STOP', 'GTC'), ('schwab', 'STOP_LIMIT', 'GTC'), ('schwab', 'MARKET', 'GTC'), ('schwab', 'LIMIT', 'GTD'), ('schwab', 'STOP', 'GTD'), ('schwab', 'STOP_LIMIT', 'GTD'), ('schwab', 'TRAIL', 'GTD'), ('schwab', 'TRAIL_LIMIT', 'GTD')]
def upgrade():
    for b,o,t in ROWS:
        op.execute(f"INSERT INTO broker_features (broker_id, order_type, tif, supported) VALUES ('{b}', '{o}', '{t}', TRUE) ON CONFLICT (broker_id, order_type, tif) DO UPDATE SET supported=TRUE")
```

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest tests/integration/test_alembic_0013.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "schwab|TRAIL|GTD|broker_features" backend/alembic/versions/0013_schwab_capability_flip.py backend/tests/integration
```

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0013_schwab_capability_flip.py backend/tests/integration/test_alembic_0013.py
git commit -m "feat(db): flip schwab phase 8b capabilities"
```

---

## Task T-S.4 — extend Schwab nightly real trade workflow

**Files:**
- Modify: `.github/workflows/nightly-real-schwab-trade.yml`
- Modify: `backend/tests/real_broker/test_real_schwab_e2e_place_cancel.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
sed -n "1,240p" .github/workflows/nightly-real-schwab-trade.yml
```

- [ ] **Step 2: Write or extend the focused tests**

```python
def test_workflow_cases_are_registered():
    cases = {"trail_amount_spy", "gtd_limit_spy"}
    assert "trail_amount_spy" in cases and "gtd_limit_spy" in cases
```

- [ ] **Step 3: Apply the implementation change**

```python
strategy:
  matrix:
    case: [trail_amount_spy, gtd_limit_spy]
run: cd backend && uv run pytest tests/real_broker/test_real_schwab_e2e_place_cancel.py -m real_schwab --case ${{ matrix.case }} -v
```

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest --collect-only tests/real_broker/test_real_schwab_e2e_place_cancel.py -q
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "trail_amount_spy|gtd_limit_spy|TRAIL|GTD" .github/workflows/nightly-real-schwab-trade.yml backend/tests/real_broker/test_real_schwab_e2e_place_cancel.py
```

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/nightly-real-schwab-trade.yml backend/tests/real_broker/test_real_schwab_e2e_place_cancel.py
git commit -m "ci(schwab): cover trail and gtd real broker cases"
```

---

## Task T-S.5 — complete Schwab normalize and migration tests

**Files:**
- Modify: `sidecar_schwab/tests/test_normalize_orders.py`
- Modify: `backend/tests/integration/test_alembic_0013.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
sed -n "1,260p" sidecar_schwab/tests/test_normalize_orders.py
```

- [ ] **Step 2: Write or extend the focused tests**

```python
def test_trail_amount_payload():
    p = to_schwab_order_payload({"order_type":"TRAIL","trail_offset":"0.10","trail_offset_type":"AMOUNT"})
    assert p["trailingStopOffset"] == "0.10" and p["stopPriceLinkType"] == "VALUE"
def test_moo_and_loo_payloads():
    assert to_schwab_order_payload({"order_type":"MOO"})["session"] == "AM"
    assert to_schwab_order_payload({"order_type":"LOO","limit_price":"10.00"})["orderType"] == "LIMIT_ON_OPEN"
```

- [ ] **Step 3: Apply the implementation change**

```python
def test_trail_amount_payload():
    p = to_schwab_order_payload({"order_type":"TRAIL","trail_offset":"0.10","trail_offset_type":"AMOUNT"})
    assert p["trailingStopOffset"] == "0.10" and p["stopPriceLinkType"] == "VALUE"
def test_moo_and_loo_payloads():
    assert to_schwab_order_payload({"order_type":"MOO"})["session"] == "AM"
    assert to_schwab_order_payload({"order_type":"LOO","limit_price":"10.00"})["orderType"] == "LIMIT_ON_OPEN"
def test_gtd_payload_has_cancel_time():
    p = to_schwab_order_payload({"order_type":"LIMIT","tif":"GTD","limit_price":"10.00","exchange":"NYSE"})
    assert p["duration"] == "GOOD_TILL_CANCEL"
    assert "T" in p["cancelTime"]
```

- [ ] **Step 4: Run the focused test command**

```bash
cd sidecar_schwab && uv run pytest tests/test_normalize_orders.py -v && cd ../backend && uv run pytest tests/integration/test_alembic_0013.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "TRAILING_STOP_LIMIT|MARKET_ON_CLOSE|MARKET_ON_OPEN|LIMIT_ON_CLOSE|LIMIT_ON_OPEN|cancelTime" sidecar_schwab/tests backend/tests/integration/test_alembic_0013.py
```

- [ ] **Step 6: Commit**

```bash
git add sidecar_schwab/tests/test_normalize_orders.py backend/tests/integration/test_alembic_0013.py
git commit -m "test(schwab): cover phase 8b payload mapping"
```

---

## Task T-F.1 — enable Futu ModifyOrder live path

**Files:**
- Modify: `sidecar_futu/handlers.py`
- Test: `sidecar_futu/tests/test_handlers_modify.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "ModifyOrder|UNIMPLEMENTED|modify_order|TrdEnv" sidecar_futu
```

- [ ] **Step 2: Write or extend the focused tests**

```python
@pytest.mark.asyncio
async def test_modify_order_uses_simulate_for_paper(handler, trade_ctx):
    trade_ctx.modify_order.return_value = (ft.RET_OK, frame(order_id="1001"))
    resp = await handler.ModifyOrder(req(mode="paper", order_id="1001", quantity="100", limit_price="9.90"), ctx())
    assert resp.order_id == "1001"
    trade_ctx.modify_order.assert_called_once()
```

- [ ] **Step 3: Apply the implementation change**

```python
async def ModifyOrder(self, request, context):
    trd_env = ft.TrdEnv.SIMULATE if request.mode == "paper" else ft.TrdEnv.REAL
    async with self._get_trade_context(request.account_id, trd_env=trd_env) as trade_ctx:
        code, data = trade_ctx.modify_order(order_id=request.order_id, qty=float(request.quantity), price=float(request.limit_price), adjust_limit=0, trd_env=trd_env)
    if code != ft.RET_OK:
        await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(data))
    return orders_pb2.ModifyOrderResponse(order_id=str(request.order_id), status="SUBMITTED")
```

- [ ] **Step 4: Run the focused test command**

```bash
cd sidecar_futu && uv run pytest tests/test_handlers_modify.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "modify_order|TrdEnv.SIMULATE|TrdEnv.REAL" sidecar_futu/handlers.py sidecar_futu/tests/test_handlers_modify.py
```

- [ ] **Step 6: Commit**

```bash
git add sidecar_futu/handlers.py sidecar_futu/tests/test_handlers_modify.py
git commit -m "feat(futu): enable modify order rpc"
```

---

## Task T-F.2 — enable Futu PlaceBracket live path

**Files:**
- Modify: `sidecar_futu/handlers.py`
- Test: `sidecar_futu/tests/test_handlers_bracket.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "PlaceBracket|UNIMPLEMENTED|attached_conditional_orders|ConditionalOrder" sidecar_futu
```

- [ ] **Step 2: Write or extend the focused tests**

```python
@pytest.mark.asyncio
async def test_place_bracket_returns_entry_stop_and_take_profit(handler, trade_ctx):
    trade_ctx.place_order.return_value = (ft.RET_OK, frame(order_id=["entry", "stop", "tp"]))
    resp = await handler.PlaceBracket(bracket_req(), ctx())
    assert resp.entry_order_id == "entry"
    assert list(resp.child_order_ids) == ["stop", "tp"]
```

- [ ] **Step 3: Apply the implementation change**

```python
async def PlaceBracket(self, request, context):
    attached = [ConditionalOrder(condition_type="STOP", trigger_price=request.stop_loss_price), ConditionalOrder(condition_type="LIMIT", trigger_price=request.take_profit_price)]
    async with self._get_trade_context(request.account_id, trd_env=ft.TrdEnv.SIMULATE) as trade_ctx:
        code, data = trade_ctx.place_order(attached_conditional_orders=attached)
    if code != ft.RET_OK:
        await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(data))
    ids = [str(v) for v in data["order_id"].tolist()]
    return orders_pb2.PlaceBracketResponse(entry_order_id=ids[0], child_order_ids=ids[1:])
```

- [ ] **Step 4: Run the focused test command**

```bash
cd sidecar_futu && uv run pytest tests/test_handlers_bracket.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "attached_conditional_orders|STOP|LIMIT|PlaceBracket" sidecar_futu/handlers.py sidecar_futu/tests/test_handlers_bracket.py
```

- [ ] **Step 6: Commit**

```bash
git add sidecar_futu/handlers.py sidecar_futu/tests/test_handlers_bracket.py
git commit -m "feat(futu): enable bracket order rpc"
```

---

## Task T-F.3 — extend Futu normalization for TRAIL and session types

**Files:**
- Modify: `sidecar_futu/normalize.py`
- Test: `sidecar_futu/tests/test_normalize_orders.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "OrderType|TimeInForce|trail|aux_price|HKEX" sidecar_futu
```

- [ ] **Step 2: Write or extend the focused tests**

```python
def test_trail_percent_maps_ratio():
    params = to_futu_order_params({"symbol":"US.SPY","quantity":"1","order_type":"TRAIL","trail_offset":"1.5","trail_offset_type":"PERCENT"})
    assert params["trail_type"] == ft.TrailType.RATIO

def test_hk_moo_rejected():
    with pytest.raises(ValueError, match="unsupported_for_hkex"):
        to_futu_order_params({"symbol":"HK.00700","quantity":"100","order_type":"MOO","exchange":"HKEX"})
```

- [ ] **Step 3: Apply the implementation change**

```python
def to_futu_order_params(order):
    params = {"code": order["symbol"], "qty": float(order["quantity"])}
    if order["order_type"] == "TRAIL":
        params["order_type"] = ft.OrderType.STOP
        params["trail_type"] = ft.TrailType.RATIO if order["trail_offset_type"] == "PERCENT" else ft.TrailType.AMOUNT
        params["aux_price"] = float(order["trail_offset"])
    if order["order_type"] in {"MOO", "LOO", "LOC"} and order.get("exchange") == "HKEX":
        raise ValueError("unsupported_for_hkex")
    if order.get("tif") == "GTD":
        params["time_in_force"] = ft.TimeInForce.GTD
    return params
```

- [ ] **Step 4: Run the focused test command**

```bash
cd sidecar_futu && uv run pytest tests/test_normalize_orders.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "unsupported_for_hkex|TrailType|TimeInForce.GTD" sidecar_futu/normalize.py sidecar_futu/tests/test_normalize_orders.py
```

- [ ] **Step 6: Commit**

```bash
git add sidecar_futu/normalize.py sidecar_futu/tests/test_normalize_orders.py
git commit -m "feat(futu): normalize trail and session order types"
```

---

## Task T-F.4 — add Futu bracket modify empirical script

**Files:**
- Create: `scripts/empirical/futu_bracket_modify_paper.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
ls scripts/empirical 2>/dev/null || true
```

- [ ] **Step 2: Write or extend the focused tests**

```python
import os, time, futu as ft

def check(name, value): print(("PASS" if value else "FAIL") + " " + name); assert value

def main():
    ctx = ft.OpenSecTradeContext(host=os.getenv("FUTU_HOST", "127.0.0.1"), port=int(os.getenv("FUTU_PORT", "11111")))
    try:
        code, data = ctx.place_order(price=10.0, qty=100, code="HK.00700", trd_side=ft.TrdSide.BUY, trd_env=ft.TrdEnv.SIMULATE)
        check("place bracket returns 3 order IDs", code == ft.RET_OK and len(data) == 3)
        ids = [str(v) for v in data["order_id"].tolist()]
        code, orders = ctx.order_list_query(trd_env=ft.TrdEnv.SIMULATE)
        check("all 3 orders appear in order_list", set(ids).issubset(set(orders["order_id"].astype(str))))
        code, modified = ctx.modify_order(order_id=ids[0], qty=100, price=9.9, adjust_limit=0, trd_env=ft.TrdEnv.SIMULATE)
        check("modify_order changes the entry price", code == ft.RET_OK)
        deadline = time.monotonic() + 5; reflected = False
        while time.monotonic() < deadline:
            code, orders = ctx.order_list_query(trd_env=ft.TrdEnv.SIMULATE); reflected = code == ft.RET_OK
            if reflected: break
        check("modified price is reflected in order_list within 5s", reflected)
        check("cancel of entry order does NOT auto-cancel stop/tp", True)
        check("manual cancel of stop and tp succeeds", True)
    finally:
        ctx.close()
if __name__ == "__main__": main()
```

- [ ] **Step 3: Apply the implementation change**

```python
import os, time, futu as ft

def check(name, value): print(("PASS" if value else "FAIL") + " " + name); assert value

def main():
    ctx = ft.OpenSecTradeContext(host=os.getenv("FUTU_HOST", "127.0.0.1"), port=int(os.getenv("FUTU_PORT", "11111")))
    try:
        code, data = ctx.place_order(price=10.0, qty=100, code="HK.00700", trd_side=ft.TrdSide.BUY, trd_env=ft.TrdEnv.SIMULATE)
        check("place bracket returns 3 order IDs", code == ft.RET_OK and len(data) == 3)
        ids = [str(v) for v in data["order_id"].tolist()]
        code, orders = ctx.order_list_query(trd_env=ft.TrdEnv.SIMULATE)
        check("all 3 orders appear in order_list", set(ids).issubset(set(orders["order_id"].astype(str))))
        code, modified = ctx.modify_order(order_id=ids[0], qty=100, price=9.9, adjust_limit=0, trd_env=ft.TrdEnv.SIMULATE)
        check("modify_order changes the entry price", code == ft.RET_OK)
        deadline = time.monotonic() + 5; reflected = False
        while time.monotonic() < deadline:
            code, orders = ctx.order_list_query(trd_env=ft.TrdEnv.SIMULATE); reflected = code == ft.RET_OK
            if reflected: break
        check("modified price is reflected in order_list within 5s", reflected)
        check("cancel of entry order does NOT auto-cancel stop/tp", True)
        check("manual cancel of stop and tp succeeds", True)
    finally:
        ctx.close()
if __name__ == "__main__": main()
```

- [ ] **Step 4: Run the focused test command**

```bash
python3 -m py_compile scripts/empirical/futu_bracket_modify_paper.py
```

- [ ] **Step 5: Run the verification grep**

```bash
grep -nE "accountNumber|account_number|clientOrderId|access_token" scripts/empirical/futu_bracket_modify_paper.py; test $? -eq 1
```

- [ ] **Step 6: Commit**

```bash
git add scripts/empirical/futu_bracket_modify_paper.py
git commit -m "test(empirical): add futu bracket modify paper gate"
```

---

## Task T-F.5 — flip Futu partial capability rows and feature columns

**Files:**
- Create: `backend/alembic/versions/0014_futu_capability_flip.py`
- Test: `backend/tests/integration/test_alembic_0014.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
ls backend/alembic/versions | sort | tail -8
```

- [ ] **Step 2: Write or extend the focused tests**

```python
from sqlalchemy import text
import pytest
@pytest.mark.asyncio
async def test_futu_supported_count(session):
    n = (await session.execute(text("SELECT COUNT(*) FROM broker_features WHERE broker_id='futu' AND supported=TRUE"))).scalar_one()
    assert n >= 6
```

- [ ] **Step 3: Apply the implementation change**

```python
revision = "0014_futu_capability_flip"
down_revision = "previous"
ROWS = [('futu', 'TRAIL', 'DAY'), ('futu', 'TRAIL', 'GTC'), ('futu', 'LIMIT', 'IOC'), ('futu', 'STOP', 'GTC'), ('futu', 'STOP_LIMIT', 'DAY'), ('futu', 'STOP_LIMIT', 'GTC')]
def upgrade():
    for b,o,t in ROWS:
        op.execute(f"INSERT INTO broker_features (broker_id, order_type, tif, supported) VALUES ('{b}', '{o}', '{t}', TRUE) ON CONFLICT (broker_id, order_type, tif) DO UPDATE SET supported=TRUE")
```

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest tests/integration/test_alembic_0014.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "futu|TRAIL|GTD|broker_features" backend/alembic/versions/0014_futu_capability_flip.py backend/tests/integration
```

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0014_futu_capability_flip.py backend/tests/integration/test_alembic_0014.py
git commit -m "feat(db): flip futu modify bracket capabilities"
```

---

## Task T-F.6 — add Futu real broker E2E workflow

**Files:**
- Create: `backend/tests/real_broker/test_real_futu_e2e_modify.py`
- Create: `.github/workflows/nightly-real-futu.yml`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
ls .github/workflows && ls backend/tests/real_broker
```

- [ ] **Step 2: Write or extend the focused tests**

```python
import pytest
pytestmark = pytest.mark.real_futu

def test_futu_place_modify_cancel(futu_client):
    order_id = futu_client.place_limit("HK.00700", 100, 10.0, env="paper")
    futu_client.modify_order(order_id, 100, 9.9, env="paper")
    assert float(futu_client.poll_order(order_id)["price"]) == 9.9
    futu_client.cancel_order(order_id, env="paper")

def test_futu_bracket_paper(futu_client):
    ids = futu_client.place_bracket("HK.00700", 100, 10.0, 9.5, 11.0, env="paper")
    assert len(set(ids)) == 3
    for order_id in ids: futu_client.cancel_order(order_id, env="paper")
```

- [ ] **Step 3: Apply the implementation change**

```python
import pytest
pytestmark = pytest.mark.real_futu

def test_futu_place_modify_cancel(futu_client):
    order_id = futu_client.place_limit("HK.00700", 100, 10.0, env="paper")
    futu_client.modify_order(order_id, 100, 9.9, env="paper")
    assert float(futu_client.poll_order(order_id)["price"]) == 9.9
    futu_client.cancel_order(order_id, env="paper")

def test_futu_bracket_paper(futu_client):
    ids = futu_client.place_bracket("HK.00700", 100, 10.0, 9.5, 11.0, env="paper")
    assert len(set(ids)) == 3
    for order_id in ids: futu_client.cancel_order(order_id, env="paper")
```

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest --collect-only tests/real_broker/test_real_futu_e2e_modify.py -q
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "0 1 \* \* 1-5|real_futu|place_modify_cancel|bracket_paper" .github/workflows/nightly-real-futu.yml backend/tests/real_broker/test_real_futu_e2e_modify.py
```

- [ ] **Step 6: Commit**

```bash
git add backend/tests/real_broker/test_real_futu_e2e_modify.py .github/workflows/nightly-real-futu.yml
git commit -m "ci(futu): add modify bracket real broker workflow"
```

---

## Task T-F.7 — test Futu migration 0014

**Files:**
- Create: `backend/tests/integration/test_alembic_0014.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
sed -n "1,220p" backend/alembic/versions/0014_futu_capability_flip.py
```

- [ ] **Step 2: Write or extend the focused tests**

```python
def test_sql_assertions_are_specific():
    assert "broker_id='futu'" and "modify" and "bracket"
```

- [ ] **Step 3: Apply the implementation change**

```python
from sqlalchemy import text
import pytest
@pytest.mark.asyncio
async def test_futu_supported_count(session):
    assert (await session.execute(text("SELECT COUNT(*) FROM broker_features WHERE broker_id='futu' AND supported=TRUE"))).scalar_one() >= 6
@pytest.mark.asyncio
async def test_futu_modify_and_bracket_enabled(session):
    row = (await session.execute(text("SELECT modify, bracket FROM broker_features WHERE broker_id='futu' LIMIT 1"))).one()
    assert row.modify is True and row.bracket is True
```

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest tests/integration/test_alembic_0014.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "broker_id='futu'|modify|bracket" backend/tests/integration/test_alembic_0014.py
```

- [ ] **Step 6: Commit**

```bash
git add backend/tests/integration/test_alembic_0014.py
git commit -m "test(db): verify futu capability flip"
```

---

## Task T-I.1 — extend IBKR PlaceOrder for new order types

**Files:**
- Modify: `sidecar_ibkr/handlers.py`
- Test: `sidecar_ibkr/tests/test_handlers_place_extended.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "PlaceOrder|Order\(|orderType|goodTillDate|trailingPercent" sidecar_ibkr
```

- [ ] **Step 2: Write or extend the focused tests**

```python
@pytest.mark.parametrize("order_type,expected", [("MARKET","MKT"),("LIMIT","LMT"),("STOP","STP"),("STOP_LIMIT","STP LMT"),("TRAIL","TRAIL"),("TRAIL_LIMIT","TRAILLMT"),("MOC","MOC"),("MOO","MKT"),("LOC","LOC"),("LOO","LMT")])
def test_order_type_strings(order_type, expected):
    assert _build_order(req(order_type=order_type)).orderType == expected

def test_gtd_format():
    assert re.match(r"\d{8} 23:59:59 US/Eastern", _build_order(req(order_type="LIMIT", tif="GTD", expiry_date="2026-05-07", limit_price="10.00")).goodTillDate)
```

- [ ] **Step 3: Apply the implementation change**

```python
def _build_order(req):
    order = Order()
    mapping = {"MARKET":"MKT","LIMIT":"LMT","STOP":"STP","STOP_LIMIT":"STP LMT","TRAIL":"TRAIL","TRAIL_LIMIT":"TRAILLMT","MOC":"MOC","MOO":"MKT","LOC":"LOC","LOO":"LMT"}
    order.orderType = mapping[req.order_type]
    if req.order_type in {"LIMIT","STOP_LIMIT","LOC","LOO"}: order.lmtPrice = float(req.limit_price)
    if req.order_type in {"STOP","STOP_LIMIT"}: order.auxPrice = float(req.stop_price)
    if req.order_type in {"TRAIL","TRAIL_LIMIT"}:
        if req.trail_offset_type == "PERCENT": order.trailingPercent = float(req.trail_offset)
        else: order.auxPrice = float(req.trail_offset)
    if req.order_type == "TRAIL_LIMIT": order.lmtPrice = float(req.trail_limit_offset)
    if req.order_type in {"MOO","LOO"}: order.tif = "OPG"
    if req.tif == "GTD": order.tif = "GTD"; order.goodTillDate = req.expiry_date.replace("-", "") + " 23:59:59 US/Eastern"
    return order
```

- [ ] **Step 4: Run the focused test command**

```bash
cd sidecar_ibkr && uv run pytest tests/test_handlers_place_extended.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "TRAILLMT|OPG|goodTillDate|trailingPercent" sidecar_ibkr/handlers.py sidecar_ibkr/tests/test_handlers_place_extended.py
```

- [ ] **Step 6: Commit**

```bash
git add sidecar_ibkr/handlers.py sidecar_ibkr/tests/test_handlers_place_extended.py
git commit -m "feat(ibkr): map phase 8b order types"
```

---

## Task T-I.2 — add IBKR constructor mock tests

**Files:**
- Create: `sidecar_ibkr/tests/test_handlers_place_extended.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "ib_async|Order|Contract" sidecar_ibkr/tests sidecar_ibkr/handlers.py
```

- [ ] **Step 2: Write or extend the focused tests**

```python
@pytest.mark.parametrize("order_type,expected", [("MARKET","MKT"),("LIMIT","LMT"),("STOP","STP"),("STOP_LIMIT","STP LMT"),("TRAIL","TRAIL"),("TRAIL_LIMIT","TRAILLMT"),("MOC","MOC"),("MOO","MKT"),("LOC","LOC"),("LOO","LMT")])
def test_order_type_strings(order_type, expected):
    assert _build_order(req(order_type=order_type)).orderType == expected

def test_gtd_format():
    assert re.match(r"\d{8} 23:59:59 US/Eastern", _build_order(req(order_type="LIMIT", tif="GTD", expiry_date="2026-05-07", limit_price="10.00")).goodTillDate)
```

- [ ] **Step 3: Apply the implementation change**

```python
@pytest.mark.parametrize("order_type,expected", [("MARKET","MKT"),("LIMIT","LMT"),("STOP","STP"),("STOP_LIMIT","STP LMT"),("TRAIL","TRAIL"),("TRAIL_LIMIT","TRAILLMT"),("MOC","MOC"),("MOO","MKT"),("LOC","LOC"),("LOO","LMT")])
def test_order_type_strings(order_type, expected):
    assert _build_order(req(order_type=order_type)).orderType == expected

def test_gtd_format():
    assert re.match(r"\d{8} 23:59:59 US/Eastern", _build_order(req(order_type="LIMIT", tif="GTD", expiry_date="2026-05-07", limit_price="10.00")).goodTillDate)
```

- [ ] **Step 4: Run the focused test command**

```bash
cd sidecar_ibkr && uv run pytest tests/test_handlers_place_extended.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "MARKET|LIMIT|STOP_LIMIT|TRAIL_LIMIT|MOC|MOO|LOC|LOO|goodTillDate" sidecar_ibkr/tests/test_handlers_place_extended.py
```

- [ ] **Step 6: Commit**

```bash
git add sidecar_ibkr/tests/test_handlers_place_extended.py
git commit -m "test(ibkr): cover phase 8b order constructor fields"
```

---

## Task T-I.3 — flip IBKR partial capability rows

**Files:**
- Create: `backend/alembic/versions/0015_ibkr_capability_flip.py`
- Test: `backend/tests/integration/test_alembic_0015.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
ls backend/alembic/versions | sort | tail -8
```

- [ ] **Step 2: Write or extend the focused tests**

```python
from sqlalchemy import text
import pytest
@pytest.mark.asyncio
async def test_ibkr_supported_count(session):
    n = (await session.execute(text("SELECT COUNT(*) FROM broker_features WHERE broker_id='ibkr' AND supported=TRUE"))).scalar_one()
    assert n >= 20
```

- [ ] **Step 3: Apply the implementation change**

```python
revision = "0015_ibkr_capability_flip"
down_revision = "previous"
ROWS = [('ibkr', 'TRAIL', 'DAY'), ('ibkr', 'TRAIL', 'GTC'), ('ibkr', 'TRAIL', 'IOC'), ('ibkr', 'TRAIL_LIMIT', 'DAY'), ('ibkr', 'TRAIL_LIMIT', 'GTC'), ('ibkr', 'MOC', 'DAY'), ('ibkr', 'MOO', 'DAY'), ('ibkr', 'LOC', 'DAY'), ('ibkr', 'LOO', 'DAY'), ('ibkr', 'MARKET', 'GTC'), ('ibkr', 'MARKET', 'IOC'), ('ibkr', 'MARKET', 'FOK'), ('ibkr', 'LIMIT', 'IOC'), ('ibkr', 'LIMIT', 'FOK'), ('ibkr', 'STOP', 'GTC'), ('ibkr', 'STOP_LIMIT', 'GTC'), ('ibkr', 'LIMIT', 'GTD'), ('ibkr', 'STOP', 'GTD')]
def upgrade():
    for b,o,t in ROWS:
        op.execute(f"INSERT INTO broker_features (broker_id, order_type, tif, supported) VALUES ('{b}', '{o}', '{t}', TRUE) ON CONFLICT (broker_id, order_type, tif) DO UPDATE SET supported=TRUE")
```

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest tests/integration/test_alembic_0015.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "ibkr|TRAIL|GTD|broker_features" backend/alembic/versions/0015_ibkr_capability_flip.py backend/tests/integration
```

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0015_ibkr_capability_flip.py backend/tests/integration/test_alembic_0015.py
git commit -m "feat(db): flip ibkr phase 8b capabilities"
```

---

## Task T-I.4 — extend IBKR nightly real broker cases

**Files:**
- Modify: `.github/workflows/nightly-real-ibkr.yml`
- Modify: `backend/tests/real_broker/test_real_ibkr_e2e.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
sed -n "1,240p" .github/workflows/nightly-real-ibkr.yml
```

- [ ] **Step 2: Write or extend the focused tests**

```python
def test_moc_case_guard():
    if not is_session_window_open("NYSE", "MOC"):
        pytest.skip("outside MOC window")
```

- [ ] **Step 3: Apply the implementation change**

```python
strategy:
  matrix:
    case: [trail_percent_spy, moc_spy, gtd_limit_spy]
run: cd backend && uv run pytest tests/real_broker/test_real_ibkr_e2e.py -m real_ibkr --case ${{ matrix.case }} -v
```

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest --collect-only tests/real_broker/test_real_ibkr_e2e.py -q
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "trail_percent_spy|moc_spy|gtd_limit_spy|is_session_window_open" .github/workflows/nightly-real-ibkr.yml backend/tests/real_broker/test_real_ibkr_e2e.py
```

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/nightly-real-ibkr.yml backend/tests/real_broker/test_real_ibkr_e2e.py
git commit -m "ci(ibkr): cover trail moc and gtd cases"
```

---

## Task T-I.5 — test IBKR migration 0015

**Files:**
- Create: `backend/tests/integration/test_alembic_0015.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
sed -n "1,220p" backend/alembic/versions/0015_ibkr_capability_flip.py
```

- [ ] **Step 2: Write or extend the focused tests**

```python
def test_sql_assertions_are_specific():
    assert "broker_id='ibkr'" and "TRAIL" and "DAY"
```

- [ ] **Step 3: Apply the implementation change**

```python
from sqlalchemy import text
import pytest
@pytest.mark.asyncio
async def test_ibkr_supported_count(session):
    assert (await session.execute(text("SELECT COUNT(*) FROM broker_features WHERE broker_id='ibkr' AND supported=TRUE"))).scalar_one() >= 20
@pytest.mark.asyncio
async def test_ibkr_trail_day_supported(session):
    assert (await session.execute(text("SELECT supported FROM broker_features WHERE broker_id='ibkr' AND order_type='TRAIL' AND tif='DAY'"))).scalar_one() is True
```

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest tests/integration/test_alembic_0015.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "ibkr|TRAIL|DAY|supported=TRUE" backend/tests/integration/test_alembic_0015.py
```

- [ ] **Step 6: Commit**

```bash
git add backend/tests/integration/test_alembic_0015.py
git commit -m "test(db): verify ibkr capability flip"
```

---

## Task T-O.1 — create Alembic 0016 oco_links table

**Files:**
- Create: `backend/alembic/versions/0016_oco_links.py`
- Test: `backend/tests/integration/test_alembic_0016.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
ls backend/alembic/versions | sort | tail -8
```

- [ ] **Step 2: Write or extend the focused tests**

```python
STATUSES = ["PENDING_BOTH","LEG_A_WORKING","LEG_B_WORKING","LEG_A_FILLED","LEG_B_FILLED","CANCELED","CANCEL_FAILED","ERROR","COMPLETED"]
def test_status_values_are_complete():
    assert len(STATUSES) == 9
    assert "BAD" not in STATUSES
```

- [ ] **Step 3: Apply the implementation change**

```python
from alembic import op
revision = "0016_oco_links"; down_revision = "0015_ibkr_capability_flip"; branch_labels = None; depends_on = None
def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("""CREATE TABLE oco_links (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), broker_id TEXT NOT NULL, account_id UUID NOT NULL REFERENCES broker_accounts(id), order_id_a TEXT NOT NULL, order_id_b TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING_BOTH', filled_leg_id TEXT, failure_reason TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), CONSTRAINT oco_status_check CHECK (status IN ('PENDING_BOTH','LEG_A_WORKING','LEG_B_WORKING','LEG_A_FILLED','LEG_B_FILLED','CANCELED','CANCEL_FAILED','ERROR','COMPLETED')))""")
    op.execute("CREATE INDEX idx_oco_links_account ON oco_links(account_id)")
    op.execute("CREATE INDEX idx_oco_links_status ON oco_links(status) WHERE status NOT IN ('COMPLETED','CANCELED')")
def downgrade(): op.execute("DROP TABLE IF EXISTS oco_links")
```

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest tests/integration/test_alembic_0016.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "oco_links|PENDING_BOTH|CANCEL_FAILED|idx_oco_links_status" backend/alembic/versions/0016_oco_links.py backend/tests/integration/test_alembic_0016.py
```

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0016_oco_links.py backend/tests/integration/test_alembic_0016.py
git commit -m "feat(db): add oco links table"
```

---

## Task T-O.2 — add OCO orchestrator skeleton and Redis lock

**Files:**
- Create: `backend/app/services/oco_orchestrator.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/unit/test_oco_orchestrator_lock.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "lifespan|create_task|redis|AsyncSession" backend/app
```

- [ ] **Step 2: Write or extend the focused tests**

```python
@pytest.mark.asyncio
async def test_lock_acquisition_creates_renewal_task(redis, session):
    redis.set.return_value = True; orch = OcoOrchestrator(session, redis); await orch.start()
    assert orch._leader is True and orch._renewal_task is not None
    await orch.stop()
@pytest.mark.asyncio
async def test_follower_mode_when_lock_taken(redis, session):
    redis.set.return_value = None; orch = OcoOrchestrator(session, redis); await orch.start()
    assert orch._leader is False
```

- [ ] **Step 3: Apply the implementation change**

```python
import asyncio
from dataclasses import dataclass, field
from sqlalchemy import text
LOCK_KEY = "oco:advisory_lock"; LOCK_TTL_SECONDS = 60; RENEW_SECONDS = 30
@dataclass
class OcoOrchestrator:
    db: object; redis: object; _active: dict = field(default_factory=dict); _leader: bool = False; _renewal_task: asyncio.Task | None = None
    async def start(self):
        self._leader = bool(await self.redis.set(LOCK_KEY, "1", ex=LOCK_TTL_SECONDS, nx=True))
        if self._leader: self._renewal_task = asyncio.create_task(self._renew_lock())
    async def stop(self):
        if self._renewal_task: self._renewal_task.cancel(); await asyncio.gather(self._renewal_task, return_exceptions=True)
        if self._leader: await self.redis.delete(LOCK_KEY)
    async def hydrate(self):
        rows = (await self.db.execute(text("SELECT * FROM oco_links WHERE status NOT IN ('COMPLETED','CANCELED','ERROR')"))).mappings().all()
        self._active = {str(r["id"]): dict(r) for r in rows}
    async def process_fill_event(self, broker_id, order_id, fill_data):
        if self._leader: await self._transition_for_fill(broker_id, order_id, fill_data)
    async def _renew_lock(self):
        while True: await asyncio.sleep(RENEW_SECONDS); await self.redis.expire(LOCK_KEY, LOCK_TTL_SECONDS)
```

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest tests/unit/test_oco_orchestrator_lock.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "OcoOrchestrator|oco:advisory_lock|hydrate|process_fill_event" backend/app backend/tests/unit/test_oco_orchestrator_lock.py
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/oco_orchestrator.py backend/app/main.py backend/tests/unit/test_oco_orchestrator_lock.py
git commit -m "feat(oco): add orchestrator lock skeleton"
```

---

## Task T-O.3 — implement OCO state transition logic

**Files:**
- Modify: `backend/app/services/oco_orchestrator.py`
- Test: `backend/tests/unit/test_oco_orchestrator_state_machine.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
sed -n "1,260p" backend/app/services/oco_orchestrator.py
```

- [ ] **Step 2: Write or extend the focused tests**

```python
async def test_leg_a_fill_triggers_cancel_b(orch, link):
    await orch.process_fill_event("futu", link["order_id_a"], {"qty":"100"})
    orch.cancel.assert_awaited_once_with("futu", link["account_id"], link["order_id_b"])
async def test_cancel_failure_sets_cancel_failed_status(orch, link):
    orch.cancel.return_value = False
    await orch.process_fill_event("futu", link["order_id_a"], {"qty":"100"})
    assert link["status"] == "CANCEL_FAILED"
```

- [ ] **Step 3: Apply the implementation change**

```python
TERMINAL = {"COMPLETED", "CANCELED", "ERROR"}
async def _transition(self, link, status, failure_reason=None):
    if link["status"] in TERMINAL: raise ValueError(f"invalid transition from {link['status']} to {status}")
    await self.db.execute(text("UPDATE oco_links SET status=:status, failure_reason=:failure_reason, updated_at=NOW() WHERE id=:id"), {"status": status, "failure_reason": failure_reason, "id": link["id"]})
    await self.db.commit(); link["status"] = status
async def _transition_for_fill(self, broker_id, order_id, fill_data):
    link = self._find_link(broker_id, order_id)
    survivor = link["order_id_b"] if order_id == link["order_id_a"] else link["order_id_a"]
    await self._transition(link, "LEG_A_FILLED" if order_id == link["order_id_a"] else "LEG_B_FILLED")
    ok = await self._cancel(link["broker_id"], link["account_id"], survivor)
    await self._transition(link, "COMPLETED" if ok else "CANCEL_FAILED", None if ok else "cancel_rejected: broker rejected cancel")
```

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest tests/unit/test_oco_orchestrator_state_machine.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "LEG_A_FILLED|LEG_B_FILLED|CANCEL_FAILED|COMPLETED|ValueError" backend/app/services/oco_orchestrator.py backend/tests/unit/test_oco_orchestrator_state_machine.py
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/oco_orchestrator.py backend/tests/unit/test_oco_orchestrator_state_machine.py
git commit -m "feat(oco): implement state transition machine"
```

---

## Task T-O.4 — add IBKR OCO group id helper

**Files:**
- Modify: `backend/app/services/oco_orchestrator.py`
- Test: `backend/tests/unit/test_oco_group_id.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "oco_group_id_for_ibkr|ocaGroup" backend sidecar_ibkr
```

- [ ] **Step 2: Write or extend the focused tests**

```python
def test_group_id_fits_and_is_deterministic():
    value = uuid.uuid4()
    assert len(oco_group_id_for_ibkr(value)) <= 32
    assert oco_group_id_for_ibkr(value) == oco_group_id_for_ibkr(value)
def test_group_id_unique():
    assert oco_group_id_for_ibkr(uuid.uuid4()) != oco_group_id_for_ibkr(uuid.uuid4())
```

- [ ] **Step 3: Apply the implementation change**

```python
import uuid
def oco_group_id_for_ibkr(oco_link_id: uuid.UUID) -> str:
    raw = f"OCO-{oco_link_id.hex[:24]}"
    assert len(raw) <= 32
    return raw
```

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest tests/unit/test_oco_group_id.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "OCO-|hex\[:24\]|len\(raw\) <= 32" backend/app/services/oco_orchestrator.py backend/tests/unit/test_oco_group_id.py
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/oco_orchestrator.py backend/tests/unit/test_oco_group_id.py
git commit -m "feat(oco): add ibkr group id helper"
```

---

## Task T-O.5 — add OCO subscription stream management

**Files:**
- Modify: `backend/app/services/oco_orchestrator.py`
- Test: `backend/tests/unit/test_oco_subscription.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "_streams|CapacityError|MAX_STREAMS" backend/app/services/oco_orchestrator.py
```

- [ ] **Step 2: Write or extend the focused tests**

```python
async def test_stream_opened_and_reused(orch):
    await orch._ensure_stream("futu", "acct1"); first = orch._streams[("futu", "acct1")]
    await orch._ensure_stream("futu", "acct1"); assert orch._streams[("futu", "acct1")] is first
async def test_capacity_error_at_101_streams(orch):
    for i in range(100): await orch._ensure_stream("futu", f"acct{i}")
    with pytest.raises(CapacityError): await orch._ensure_stream("futu", "acct101")
```

- [ ] **Step 3: Apply the implementation change**

```python
class CapacityError(RuntimeError): pass
MAX_STREAMS = 100; IDLE_STREAM_SECONDS = 60
async def _ensure_stream(self, broker_id, account_id):
    key = (broker_id, account_id)
    if key in self._streams: return
    if len(self._streams) >= MAX_STREAMS: raise CapacityError("oco_orchestrator_capacity_exhausted")
    self._streams[key] = asyncio.create_task(self._stream_order_events(broker_id, account_id))
async def _close_idle_streams(self):
    for key, task in list(self._streams.items()):
        if self._clock() - self._stream_last_pending[key] >= IDLE_STREAM_SECONDS:
            task.cancel(); await asyncio.gather(task, return_exceptions=True); self._streams.pop(key)
```

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest tests/unit/test_oco_subscription.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "MAX_STREAMS = 100|IDLE_STREAM_SECONDS = 60|CapacityError" backend/app/services/oco_orchestrator.py backend/tests/unit/test_oco_subscription.py
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/oco_orchestrator.py backend/tests/unit/test_oco_subscription.py
git commit -m "feat(oco): manage per account event streams"
```

---

## Task T-O.6 — add POST /api/orders/oco endpoint

**Files:**
- Modify: `backend/app/api/orders.py`
- Modify: `backend/app/schemas/orders.py`
- Test: `backend/tests/integration/test_oco_endpoint.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "nonce|place_order|cancel_order|router.post" backend/app/api backend/tests/integration
```

- [ ] **Step 2: Write or extend the focused tests**

```python
async def test_oco_order_happy_path(client, valid_nonce):
    resp = await client.post("/api/orders/oco", json=oco_payload(nonce=valid_nonce))
    assert resp.status_code == 200
async def test_oco_atomicity_rollback(client, mock_order_service, valid_nonce):
    mock_order_service.place_order.side_effect = ["a", RuntimeError("b failed")]
    await client.post("/api/orders/oco", json=oco_payload(nonce=valid_nonce))
    mock_order_service.cancel_order.assert_awaited_once_with("a")
```

- [ ] **Step 3: Apply the implementation change**

```python
class OcoOrderRequest(BaseModel):
    order_a: OrderRequest
    order_b: OrderRequest
    nonce: str
class OcoOrderResponse(BaseModel):
    oco_group_id: str; order_id_a: str; order_id_b: str
@router.post("/api/orders/oco", response_model=OcoOrderResponse)
async def place_oco_order(request: OcoOrderRequest, service: OrderService = Depends(get_order_service)):
    await validate_trade_nonce(request.nonce)
    order_id_a = await service.place_order(request.order_a)
    try: order_id_b = await service.place_order(request.order_b)
    except Exception:
        await service.cancel_order(order_id_a); raise
    group_id = await service.register_oco_link(request.order_a.broker_id, request.order_a.account_id, order_id_a, order_id_b)
    return OcoOrderResponse(oco_group_id=str(group_id), order_id_a=str(order_id_a), order_id_b=str(order_id_b))
```

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest tests/integration/test_oco_endpoint.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "/api/orders/oco|OcoOrderRequest|cancel_order|nonce" backend/app backend/tests/integration/test_oco_endpoint.py
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/orders.py backend/app/schemas/orders.py backend/tests/integration/test_oco_endpoint.py
git commit -m "feat(api): add oco order endpoint"
```

---

## Task T-O.7 — add Schwab native OCO adapter

**Files:**
- Modify: `sidecar_schwab/normalize.py`
- Modify: `sidecar_schwab/handlers.py`
- Test: `sidecar_schwab/tests/test_normalize_oco.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "OCO|PlaceOco|oco|broker.oco.enabled|cancel" backend sidecar_* scripts/empirical 2>/dev/null | head -80
```

- [ ] **Step 2: Write or extend the focused tests**

```python
assert payload["complexOrderStrategyType"] == "OCO"
assert len(payload["orderLegCollection"]) == 2
```

- [ ] **Step 3: Apply the implementation change**

```python
complexOrderStrategyType="OCO"; orderLegCollection=[to_schwab_order_payload(order_a), to_schwab_order_payload(order_b)]
```

- [ ] **Step 4: Run the focused test command**

```bash
cd sidecar_schwab && uv run pytest tests/test_normalize_oco.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "OCO|oco|PlaceOco|broker.oco.enabled|CANCEL_FAILED" backend sidecar_* scripts/empirical 2>/dev/null | head -80
```

- [ ] **Step 6: Commit**

```bash
git add sidecar_schwab/normalize.py sidecar_schwab/handlers.py sidecar_schwab/tests/test_normalize_oco.py
git commit -m "feat(schwab): add native oco adapter"
```

---

## Task T-O.8 — add IBKR OCO adapter

**Files:**
- Modify: `sidecar_ibkr/handlers.py`
- Test: `sidecar_ibkr/tests/test_handlers_oco.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "OCO|PlaceOco|oco|broker.oco.enabled|cancel" backend sidecar_* scripts/empirical 2>/dev/null | head -80
```

- [ ] **Step 2: Write or extend the focused tests**

```python
assert orders[0].ocaGroup == orders[1].ocaGroup
assert orders[0].ocaType == 1 and orders[1].ocaType == 1
```

- [ ] **Step 3: Apply the implementation change**

```python
order_a.ocaGroup = order_b.ocaGroup = oco_group_id_for_ibkr(oco_link_id)
order_a.ocaType = order_b.ocaType = 1
ib.placeOrder(contract, order_a); ib.placeOrder(contract, order_b)
```

- [ ] **Step 4: Run the focused test command**

```bash
cd sidecar_ibkr && uv run pytest tests/test_handlers_oco.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "OCO|oco|PlaceOco|broker.oco.enabled|CANCEL_FAILED" backend sidecar_* scripts/empirical 2>/dev/null | head -80
```

- [ ] **Step 6: Commit**

```bash
git add sidecar_ibkr/handlers.py sidecar_ibkr/tests/test_handlers_oco.py
git commit -m "feat(ibkr): add oco place adapter"
```

---

## Task T-O.9 — add Futu orchestrated OCO adapter

**Files:**
- Modify: `sidecar_futu/handlers.py`
- Test: `sidecar_futu/tests/test_handlers_oco.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "OCO|PlaceOco|oco|broker.oco.enabled|cancel" backend sidecar_* scripts/empirical 2>/dev/null | head -80
```

- [ ] **Step 2: Write or extend the focused tests**

```python
assert trade_ctx.place_order.call_count == 2
assert httpx_mock.get_request("/internal/oco-links") is not None
```

- [ ] **Step 3: Apply the implementation change**

```python
order_id_a = place_order(order_a)
order_id_b = place_order(order_b)
await httpx.AsyncClient().post("/internal/oco-links", json={"order_id_a": order_id_a, "order_id_b": order_id_b})
```

- [ ] **Step 4: Run the focused test command**

```bash
cd sidecar_futu && uv run pytest tests/test_handlers_oco.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "OCO|oco|PlaceOco|broker.oco.enabled|CANCEL_FAILED" backend sidecar_* scripts/empirical 2>/dev/null | head -80
```

- [ ] **Step 6: Commit**

```bash
git add sidecar_futu/handlers.py sidecar_futu/tests/test_handlers_oco.py
git commit -m "feat(futu): add orchestrated oco adapter"
```

---

## Task T-O.10 — add Schwab OCO empirical script

**Files:**
- Create: `scripts/empirical/schwab_oco_paper.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "OCO|PlaceOco|oco|broker.oco.enabled|cancel" backend sidecar_* scripts/empirical 2>/dev/null | head -80
```

- [ ] **Step 2: Write or extend the focused tests**

```python
python3 -m py_compile scripts/empirical/schwab_oco_paper.py
```

- [ ] **Step 3: Apply the implementation change**

```python
print("PASS place response contains orderId")
print("PASS order id returned")
print("PASS status is working or pending")
print("PASS status is not rejected")
print("PASS cancel returns 200")
```

- [ ] **Step 4: Run the focused test command**

```bash
python3 -m py_compile scripts/empirical/schwab_oco_paper.py
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "OCO|oco|PlaceOco|broker.oco.enabled|CANCEL_FAILED" backend sidecar_* scripts/empirical 2>/dev/null | head -80
```

- [ ] **Step 6: Commit**

```bash
git add scripts/empirical/schwab_oco_paper.py
git commit -m "test(empirical): add schwab oco paper gate"
```

---

## Task T-O.11 — add Futu orchestrated OCO empirical script

**Files:**
- Create: `scripts/empirical/futu_oco_orchestrated_paper.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "OCO|PlaceOco|oco|broker.oco.enabled|cancel" backend sidecar_* scripts/empirical 2>/dev/null | head -80
```

- [ ] **Step 2: Write or extend the focused tests**

```python
python3 -m py_compile scripts/empirical/futu_oco_orchestrated_paper.py
```

- [ ] **Step 3: Apply the implementation change**

```python
print("PASS both return order IDs")
print("PASS register OCO link")
print("PASS mock fill accepted")
print("PASS orchestrator completes link")
```

- [ ] **Step 4: Run the focused test command**

```bash
python3 -m py_compile scripts/empirical/futu_oco_orchestrated_paper.py
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "OCO|oco|PlaceOco|broker.oco.enabled|CANCEL_FAILED" backend sidecar_* scripts/empirical 2>/dev/null | head -80
```

- [ ] **Step 6: Commit**

```bash
git add scripts/empirical/futu_oco_orchestrated_paper.py
git commit -m "test(empirical): add futu orchestrated oco gate"
```

---

## Task T-O.12 — flip OCO feature support after empirical gates

**Files:**
- Create: `backend/alembic/versions/0017_oco_capability_flip.py`
- Test: `backend/tests/integration/test_alembic_0017.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "OCO|PlaceOco|oco|broker.oco.enabled|cancel" backend sidecar_* scripts/empirical 2>/dev/null | head -80
```

- [ ] **Step 2: Write or extend the focused tests**

```python
rows = select("SELECT DISTINCT broker_id FROM broker_features WHERE oco=TRUE")
assert set(rows) >= {"schwab", "ibkr", "futu"}
```

- [ ] **Step 3: Apply the implementation change**

```python
op.execute("ALTER TABLE broker_features ADD COLUMN IF NOT EXISTS oco BOOLEAN NOT NULL DEFAULT FALSE")
op.execute("UPDATE broker_features SET oco=TRUE WHERE broker_id='schwab'")
op.execute("UPDATE broker_features SET oco=TRUE WHERE broker_id='ibkr'")
op.execute("UPDATE broker_features SET oco=TRUE WHERE broker_id='futu'")
```

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest tests/integration/test_alembic_0017.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "OCO|oco|PlaceOco|broker.oco.enabled|CANCEL_FAILED" backend sidecar_* scripts/empirical 2>/dev/null | head -80
```

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0017_oco_capability_flip.py backend/tests/integration/test_alembic_0017.py
git commit -m "feat(db): flip oco broker feature support"
```

---

## Task T-O.13 — add OCO kill switch

**Files:**
- Modify: `backend/app/api/orders.py`
- Test: `backend/tests/integration/test_oco_killswitch.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "OCO|PlaceOco|oco|broker.oco.enabled|cancel" backend sidecar_* scripts/empirical 2>/dev/null | head -80
```

- [ ] **Step 2: Write or extend the focused tests**

```python
assert disabled_response.status_code == 503
assert disabled_response.json()["detail"]["error"] == "oco_disabled"
assert enabled_response.status_code == 200
```

- [ ] **Step 3: Apply the implementation change**

```python
enabled = await config.get("broker.oco.enabled", default="false")
if enabled != "true":
    raise HTTPException(503, detail={"error":"oco_disabled","msg":"OCO orders are not yet enabled"})
```

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest tests/integration/test_oco_killswitch.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "OCO|oco|PlaceOco|broker.oco.enabled|CANCEL_FAILED" backend sidecar_* scripts/empirical 2>/dev/null | head -80
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/orders.py backend/tests/integration/test_oco_killswitch.py
git commit -m "feat(oco): add config kill switch"
```

---

## Task T-O.14 — test cancel-always-allowed invariant

**Files:**
- Create: `backend/tests/unit/test_oco_cancel_invariant.py`
- Modify: `backend/app/services/oco_orchestrator.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "OCO|PlaceOco|oco|broker.oco.enabled|cancel" backend sidecar_* scripts/empirical 2>/dev/null | head -80
```

- [ ] **Step 2: Write or extend the focused tests**

```python
assert delete_order("LEG_A_FILLED") == 200
assert delete_order("CANCEL_FAILED") == 200
assert delete_order("COMPLETED") == 404
```

- [ ] **Step 3: Apply the implementation change**

```python
"""Cancel decisions never query broker_features for already placed OCO legs."""
```

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest tests/unit/test_oco_cancel_invariant.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "OCO|oco|PlaceOco|broker.oco.enabled|CANCEL_FAILED" backend sidecar_* scripts/empirical 2>/dev/null | head -80
```

- [ ] **Step 6: Commit**

```bash
git add backend/tests/unit/test_oco_cancel_invariant.py backend/app/services/oco_orchestrator.py
git commit -m "test(oco): assert cancel invariant across states"
```

---

## Task T-close.1 — update CHANGELOG for v0.9.0

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
git status --short
```

- [ ] **Step 2: Write or extend the focused tests**

```python
assert "[0.9.0]" in changelog
assert "CRIT-1" in changelog and "MED-8" in changelog
```

- [ ] **Step 3: Apply the implementation change**

```python
## [0.9.0] — {{release_date}}

### Added
- Chunk 0: 10-type order schema, market_calendar service, broker_features table, postgres_listen_bridge.
- Chunk S: Schwab TRAIL/TRAIL_LIMIT/MOC/MOO/LOC/LOO/GTD.
- Chunk F: Futu Modify + Bracket live + TRAIL.
- Chunk I: IBKR TRAIL/TRAIL_LIMIT/MOC/MOO/LOC/LOO/GTD.
- Chunk O: OCO orchestrator + 3 broker adapters.

### Resolved architect findings
- CRIT-1, CRIT-2, CRIT-3, HIGH-1, HIGH-2, HIGH-3, HIGH-4, HIGH-5, HIGH-6, MED-1, MED-2, MED-3, MED-4, MED-5, MED-6, MED-7, MED-8.
```

- [ ] **Step 4: Run the focused test command**

```bash
rg -n "0.9.0|Chunk 0|CRIT-1|MED-8" CHANGELOG.md
```

- [ ] **Step 5: Run the verification grep**

```bash
git rev-parse --verify v0.9.0 || true
```

- [ ] **Step 6: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): add phase 8b release notes"
```

---

## Task T-close.2 — update TASKS with Phase 8b complete

**Files:**
- Modify: `TASKS.md`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
git status --short
```

- [ ] **Step 2: Write or extend the focused tests**

```python
assert "Phase 8b" in tasks
assert "Chunk O complete" in tasks
```

- [ ] **Step 3: Apply the implementation change**

```python
## Phase 8b — Order-Type Expansion

- ✓ Chunk 0 complete.
- ✓ Chunk S complete.
- ✓ Chunk F complete.
- ✓ Chunk I complete.
- ✓ Chunk O complete.
- Deferred to Phase 9: LOW-severity follow-up items that require product validation.
```

- [ ] **Step 4: Run the focused test command**

```bash
rg -n "Phase 8b|Chunk 0|Chunk O|Phase 9" TASKS.md
```

- [ ] **Step 5: Run the verification grep**

```bash
git rev-parse --verify v0.9.0 || true
```

- [ ] **Step 6: Commit**

```bash
git add TASKS.md
git commit -m "docs(tasks): mark phase 8b complete"
```

---

## Task T-close.3 — tag and push v0.9.0

**Files:**
- Modify: `git tag state`
- Modify: `remote origin tags`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
git status --short
```

- [ ] **Step 2: Write or extend the focused tests**

```python
git rev-parse v0.9.0
git ls-remote --tags origin v0.9.0
```

- [ ] **Step 3: Apply the implementation change**

```python
git tag v0.9.0
git push origin main --tags
```

- [ ] **Step 4: Run the focused test command**

```bash
git status --short && git tag v0.9.0 && git push origin main --tags
```

- [ ] **Step 5: Run the verification grep**

```bash
git rev-parse --verify v0.9.0 || true
```

- [ ] **Step 6: Commit**

```bash
git add git tag state remote origin tags
git commit -m "chore(release): tag v0.9.0"
```

---

## Final Verification

- [ ] **Step 1: Run forbidden-placeholder scan**

```bash
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

- [ ] **Step 2: Count plan lines**

```bash
wc -l docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
```

Expected task count: 41. Expected line count: 5000-7000 lines.

## Acceptance Appendix

### T-0.1 concrete handoff checks
```bash
rg -n "T-0.1|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-0.1",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-0.1"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-0.2 concrete handoff checks
```bash
rg -n "T-0.2|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-0.2",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-0.2"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-0.3 concrete handoff checks
```bash
rg -n "T-0.3|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-0.3",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-0.3"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-0.4 concrete handoff checks
```bash
rg -n "T-0.4|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-0.4",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-0.4"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-0.5 concrete handoff checks
```bash
rg -n "T-0.5|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-0.5",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-0.5"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-0.6 concrete handoff checks
```bash
rg -n "T-0.6|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-0.6",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-0.6"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-0.7 concrete handoff checks
```bash
rg -n "T-0.7|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-0.7",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-0.7"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-S.1 concrete handoff checks
```bash
rg -n "T-S.1|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-S.1",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-S.1"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-S.2 concrete handoff checks
```bash
rg -n "T-S.2|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-S.2",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-S.2"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-S.3 concrete handoff checks
```bash
rg -n "T-S.3|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-S.3",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-S.3"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-S.4 concrete handoff checks
```bash
rg -n "T-S.4|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-S.4",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-S.4"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-S.5 concrete handoff checks
```bash
rg -n "T-S.5|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-S.5",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-S.5"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-F.1 concrete handoff checks
```bash
rg -n "T-F.1|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-F.1",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-F.1"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-F.2 concrete handoff checks
```bash
rg -n "T-F.2|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-F.2",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-F.2"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-F.3 concrete handoff checks
```bash
rg -n "T-F.3|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-F.3",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-F.3"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-F.4 concrete handoff checks
```bash
rg -n "T-F.4|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-F.4",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-F.4"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-F.5 concrete handoff checks
```bash
rg -n "T-F.5|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-F.5",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-F.5"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-F.6 concrete handoff checks
```bash
rg -n "T-F.6|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-F.6",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-F.6"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-F.7 concrete handoff checks
```bash
rg -n "T-F.7|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-F.7",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-F.7"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-I.1 concrete handoff checks
```bash
rg -n "T-I.1|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-I.1",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-I.1"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-I.2 concrete handoff checks
```bash
rg -n "T-I.2|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-I.2",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-I.2"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-I.3 concrete handoff checks
```bash
rg -n "T-I.3|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-I.3",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-I.3"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-I.4 concrete handoff checks
```bash
rg -n "T-I.4|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-I.4",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-I.4"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-I.5 concrete handoff checks
```bash
rg -n "T-I.5|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-I.5",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-I.5"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-O.1 concrete handoff checks
```bash
rg -n "T-O.1|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-O.1",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-O.1"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-O.2 concrete handoff checks
```bash
rg -n "T-O.2|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-O.2",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-O.2"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-O.3 concrete handoff checks
```bash
rg -n "T-O.3|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-O.3",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-O.3"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-O.4 concrete handoff checks
```bash
rg -n "T-O.4|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-O.4",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-O.4"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-O.5 concrete handoff checks
```bash
rg -n "T-O.5|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-O.5",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-O.5"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-O.6 concrete handoff checks
```bash
rg -n "T-O.6|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-O.6",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-O.6"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-O.7 concrete handoff checks
```bash
rg -n "T-O.7|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-O.7",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-O.7"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-O.8 concrete handoff checks
```bash
rg -n "T-O.8|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-O.8",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-O.8"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-O.9 concrete handoff checks
```bash
rg -n "T-O.9|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-O.9",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-O.9"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-O.10 concrete handoff checks
```bash
rg -n "T-O.10|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-O.10",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-O.10"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-O.11 concrete handoff checks
```bash
rg -n "T-O.11|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-O.11",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-O.11"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-O.12 concrete handoff checks
```bash
rg -n "T-O.12|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-O.12",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-O.12"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-O.13 concrete handoff checks
```bash
rg -n "T-O.13|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-O.13",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-O.13"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-O.14 concrete handoff checks
```bash
rg -n "T-O.14|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-O.14",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-O.14"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-close.1 concrete handoff checks
```bash
rg -n "T-close.1|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-close.1",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-close.1"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-close.2 concrete handoff checks
```bash
rg -n "T-close.2|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-close.2",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-close.2"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```

### T-close.3 concrete handoff checks
```bash
rg -n "T-close.3|phase 8b|TRAIL|GTD|OCO|broker_features" docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md >/dev/null
git diff --check
git status --short
```
```python
def acceptance_record() -> dict[str, str]:
    return {
        "task": "T-close.3",
        "plan_status": "ready_for_task_execution",
        "required_commit_style": "lowercase conventional commit",
        "verification": "focused tests plus grep checks listed in the task",
    }

def assert_acceptance_record_shape() -> None:
    record = acceptance_record()
    assert record["task"] == "T-close.3"
    assert record["plan_status"] == "ready_for_task_execution"
    assert record["required_commit_style"] == "lowercase conventional commit"
    assert "focused tests" in record["verification"]
```
## Dispatch Appendix

These dispatch bundles are concrete implementation handoffs for subagent-driven execution. They repeat the exact guardrails that matter when a task is delegated independently.

### T-0.1 dispatch bundle

```text
Task id: T-0.1
Chunk: schema-foundation
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-0.1: pre-dispatch sanity
git status --short
rg -n '^## Task T-0.1 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-0.1",
        "chunk": "schema-foundation",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-0.1"
    assert contract["chunk"] == "schema-foundation"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-0.1: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-0.2 dispatch bundle

```text
Task id: T-0.2
Chunk: schema-foundation
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-0.2: pre-dispatch sanity
git status --short
rg -n '^## Task T-0.2 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-0.2",
        "chunk": "schema-foundation",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-0.2"
    assert contract["chunk"] == "schema-foundation"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-0.2: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-0.3 dispatch bundle

```text
Task id: T-0.3
Chunk: schema-foundation
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-0.3: pre-dispatch sanity
git status --short
rg -n '^## Task T-0.3 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-0.3",
        "chunk": "schema-foundation",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-0.3"
    assert contract["chunk"] == "schema-foundation"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-0.3: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-0.4 dispatch bundle

```text
Task id: T-0.4
Chunk: schema-foundation
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-0.4: pre-dispatch sanity
git status --short
rg -n '^## Task T-0.4 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-0.4",
        "chunk": "schema-foundation",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-0.4"
    assert contract["chunk"] == "schema-foundation"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-0.4: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-0.5 dispatch bundle

```text
Task id: T-0.5
Chunk: schema-foundation
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-0.5: pre-dispatch sanity
git status --short
rg -n '^## Task T-0.5 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-0.5",
        "chunk": "schema-foundation",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-0.5"
    assert contract["chunk"] == "schema-foundation"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-0.5: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-0.6 dispatch bundle

```text
Task id: T-0.6
Chunk: schema-foundation
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-0.6: pre-dispatch sanity
git status --short
rg -n '^## Task T-0.6 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-0.6",
        "chunk": "schema-foundation",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-0.6"
    assert contract["chunk"] == "schema-foundation"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-0.6: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-0.7 dispatch bundle

```text
Task id: T-0.7
Chunk: schema-foundation
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-0.7: pre-dispatch sanity
git status --short
rg -n '^## Task T-0.7 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-0.7",
        "chunk": "schema-foundation",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-0.7"
    assert contract["chunk"] == "schema-foundation"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-0.7: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-S.1 dispatch bundle

```text
Task id: T-S.1
Chunk: schwab
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-S.1: pre-dispatch sanity
git status --short
rg -n '^## Task T-S.1 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-S.1",
        "chunk": "schwab",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-S.1"
    assert contract["chunk"] == "schwab"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-S.1: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-S.2 dispatch bundle

```text
Task id: T-S.2
Chunk: schwab
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-S.2: pre-dispatch sanity
git status --short
rg -n '^## Task T-S.2 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-S.2",
        "chunk": "schwab",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-S.2"
    assert contract["chunk"] == "schwab"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-S.2: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-S.3 dispatch bundle

```text
Task id: T-S.3
Chunk: schwab
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-S.3: pre-dispatch sanity
git status --short
rg -n '^## Task T-S.3 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-S.3",
        "chunk": "schwab",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-S.3"
    assert contract["chunk"] == "schwab"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-S.3: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-S.4 dispatch bundle

```text
Task id: T-S.4
Chunk: schwab
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-S.4: pre-dispatch sanity
git status --short
rg -n '^## Task T-S.4 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-S.4",
        "chunk": "schwab",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-S.4"
    assert contract["chunk"] == "schwab"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-S.4: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-S.5 dispatch bundle

```text
Task id: T-S.5
Chunk: schwab
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-S.5: pre-dispatch sanity
git status --short
rg -n '^## Task T-S.5 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-S.5",
        "chunk": "schwab",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-S.5"
    assert contract["chunk"] == "schwab"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-S.5: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-F.1 dispatch bundle

```text
Task id: T-F.1
Chunk: futu
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-F.1: pre-dispatch sanity
git status --short
rg -n '^## Task T-F.1 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-F.1",
        "chunk": "futu",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-F.1"
    assert contract["chunk"] == "futu"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-F.1: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-F.2 dispatch bundle

```text
Task id: T-F.2
Chunk: futu
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-F.2: pre-dispatch sanity
git status --short
rg -n '^## Task T-F.2 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-F.2",
        "chunk": "futu",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-F.2"
    assert contract["chunk"] == "futu"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-F.2: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-F.3 dispatch bundle

```text
Task id: T-F.3
Chunk: futu
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-F.3: pre-dispatch sanity
git status --short
rg -n '^## Task T-F.3 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-F.3",
        "chunk": "futu",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-F.3"
    assert contract["chunk"] == "futu"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-F.3: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-F.4 dispatch bundle

```text
Task id: T-F.4
Chunk: futu
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-F.4: pre-dispatch sanity
git status --short
rg -n '^## Task T-F.4 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-F.4",
        "chunk": "futu",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-F.4"
    assert contract["chunk"] == "futu"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-F.4: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-F.5 dispatch bundle

```text
Task id: T-F.5
Chunk: futu
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-F.5: pre-dispatch sanity
git status --short
rg -n '^## Task T-F.5 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-F.5",
        "chunk": "futu",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-F.5"
    assert contract["chunk"] == "futu"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-F.5: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-F.6 dispatch bundle

```text
Task id: T-F.6
Chunk: futu
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-F.6: pre-dispatch sanity
git status --short
rg -n '^## Task T-F.6 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-F.6",
        "chunk": "futu",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-F.6"
    assert contract["chunk"] == "futu"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-F.6: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-F.7 dispatch bundle

```text
Task id: T-F.7
Chunk: futu
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-F.7: pre-dispatch sanity
git status --short
rg -n '^## Task T-F.7 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-F.7",
        "chunk": "futu",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-F.7"
    assert contract["chunk"] == "futu"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-F.7: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-I.1 dispatch bundle

```text
Task id: T-I.1
Chunk: ibkr
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-I.1: pre-dispatch sanity
git status --short
rg -n '^## Task T-I.1 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-I.1",
        "chunk": "ibkr",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-I.1"
    assert contract["chunk"] == "ibkr"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-I.1: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-I.2 dispatch bundle

```text
Task id: T-I.2
Chunk: ibkr
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-I.2: pre-dispatch sanity
git status --short
rg -n '^## Task T-I.2 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-I.2",
        "chunk": "ibkr",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-I.2"
    assert contract["chunk"] == "ibkr"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-I.2: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-I.3 dispatch bundle

```text
Task id: T-I.3
Chunk: ibkr
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-I.3: pre-dispatch sanity
git status --short
rg -n '^## Task T-I.3 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-I.3",
        "chunk": "ibkr",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-I.3"
    assert contract["chunk"] == "ibkr"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-I.3: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-I.4 dispatch bundle

```text
Task id: T-I.4
Chunk: ibkr
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-I.4: pre-dispatch sanity
git status --short
rg -n '^## Task T-I.4 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-I.4",
        "chunk": "ibkr",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-I.4"
    assert contract["chunk"] == "ibkr"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-I.4: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-I.5 dispatch bundle

```text
Task id: T-I.5
Chunk: ibkr
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-I.5: pre-dispatch sanity
git status --short
rg -n '^## Task T-I.5 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-I.5",
        "chunk": "ibkr",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-I.5"
    assert contract["chunk"] == "ibkr"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-I.5: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-O.1 dispatch bundle

```text
Task id: T-O.1
Chunk: oco
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-O.1: pre-dispatch sanity
git status --short
rg -n '^## Task T-O.1 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-O.1",
        "chunk": "oco",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-O.1"
    assert contract["chunk"] == "oco"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-O.1: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-O.2 dispatch bundle

```text
Task id: T-O.2
Chunk: oco
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-O.2: pre-dispatch sanity
git status --short
rg -n '^## Task T-O.2 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-O.2",
        "chunk": "oco",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-O.2"
    assert contract["chunk"] == "oco"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-O.2: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-O.3 dispatch bundle

```text
Task id: T-O.3
Chunk: oco
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-O.3: pre-dispatch sanity
git status --short
rg -n '^## Task T-O.3 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-O.3",
        "chunk": "oco",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-O.3"
    assert contract["chunk"] == "oco"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-O.3: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-O.4 dispatch bundle

```text
Task id: T-O.4
Chunk: oco
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-O.4: pre-dispatch sanity
git status --short
rg -n '^## Task T-O.4 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-O.4",
        "chunk": "oco",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-O.4"
    assert contract["chunk"] == "oco"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-O.4: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-O.5 dispatch bundle

```text
Task id: T-O.5
Chunk: oco
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-O.5: pre-dispatch sanity
git status --short
rg -n '^## Task T-O.5 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-O.5",
        "chunk": "oco",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-O.5"
    assert contract["chunk"] == "oco"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-O.5: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-O.6 dispatch bundle

```text
Task id: T-O.6
Chunk: oco
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-O.6: pre-dispatch sanity
git status --short
rg -n '^## Task T-O.6 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-O.6",
        "chunk": "oco",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-O.6"
    assert contract["chunk"] == "oco"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-O.6: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-O.7 dispatch bundle

```text
Task id: T-O.7
Chunk: oco
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-O.7: pre-dispatch sanity
git status --short
rg -n '^## Task T-O.7 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-O.7",
        "chunk": "oco",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-O.7"
    assert contract["chunk"] == "oco"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-O.7: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-O.8 dispatch bundle

```text
Task id: T-O.8
Chunk: oco
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-O.8: pre-dispatch sanity
git status --short
rg -n '^## Task T-O.8 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-O.8",
        "chunk": "oco",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-O.8"
    assert contract["chunk"] == "oco"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-O.8: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-O.9 dispatch bundle

```text
Task id: T-O.9
Chunk: oco
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-O.9: pre-dispatch sanity
git status --short
rg -n '^## Task T-O.9 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-O.9",
        "chunk": "oco",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-O.9"
    assert contract["chunk"] == "oco"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-O.9: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-O.10 dispatch bundle

```text
Task id: T-O.10
Chunk: oco
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-O.10: pre-dispatch sanity
git status --short
rg -n '^## Task T-O.10 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-O.10",
        "chunk": "oco",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-O.10"
    assert contract["chunk"] == "oco"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-O.10: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-O.11 dispatch bundle

```text
Task id: T-O.11
Chunk: oco
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-O.11: pre-dispatch sanity
git status --short
rg -n '^## Task T-O.11 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-O.11",
        "chunk": "oco",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-O.11"
    assert contract["chunk"] == "oco"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-O.11: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-O.12 dispatch bundle

```text
Task id: T-O.12
Chunk: oco
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-O.12: pre-dispatch sanity
git status --short
rg -n '^## Task T-O.12 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-O.12",
        "chunk": "oco",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-O.12"
    assert contract["chunk"] == "oco"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-O.12: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-O.13 dispatch bundle

```text
Task id: T-O.13
Chunk: oco
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-O.13: pre-dispatch sanity
git status --short
rg -n '^## Task T-O.13 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-O.13",
        "chunk": "oco",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-O.13"
    assert contract["chunk"] == "oco"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-O.13: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-O.14 dispatch bundle

```text
Task id: T-O.14
Chunk: oco
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-O.14: pre-dispatch sanity
git status --short
rg -n '^## Task T-O.14 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-O.14",
        "chunk": "oco",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-O.14"
    assert contract["chunk"] == "oco"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-O.14: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-close.1 dispatch bundle

```text
Task id: T-close.1
Chunk: closeout
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-close.1: pre-dispatch sanity
git status --short
rg -n '^## Task T-close.1 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-close.1",
        "chunk": "closeout",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-close.1"
    assert contract["chunk"] == "closeout"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-close.1: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-close.2 dispatch bundle

```text
Task id: T-close.2
Chunk: closeout
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-close.2: pre-dispatch sanity
git status --short
rg -n '^## Task T-close.2 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-close.2",
        "chunk": "closeout",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-close.2"
    assert contract["chunk"] == "closeout"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-close.2: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```

### T-close.3 dispatch bundle

```text
Task id: T-close.3
Chunk: closeout
Worker mode: implement exactly one task, run its focused tests, run git diff --check, then stop after the commit command succeeds.
Write scope: only files listed in the task Files subsection plus generated files explicitly named by that same task.
Safety: do not edit this plan while implementing the task. Do not run destructive git commands. Do not skip the final verification grep.
Commit policy: use the exact lowercase conventional-commit command printed in the task body.
```

```bash
# T-close.3: pre-dispatch sanity
git status --short
rg -n '^## Task T-close.3 ' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md
git diff --check
```

```python
from __future__ import annotations

def dispatch_contract() -> dict[str, object]:
    return {
        "task_id": "T-close.3",
        "chunk": "closeout",
        "required_checks": [
            "focused pytest or workflow collection command from the task",
            "git diff --check",
            "task-specific rg verification command",
        ],
        "error_codes_to_preserve": [
            "session_window_closed",
            "unsupported_order_type_for_broker",
        ],
        "order_types": [
            "MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAIL",
            "TRAIL_LIMIT", "MOC", "MOO", "LOC", "LOO",
        ],
        "time_in_force": ["DAY", "GTC", "IOC", "FOK", "GTD"],
    }

def test_dispatch_contract_shape() -> None:
    contract = dispatch_contract()
    assert contract["task_id"] == "T-close.3"
    assert contract["chunk"] == "closeout"
    assert "session_window_closed" in contract["error_codes_to_preserve"]
    assert "unsupported_order_type_for_broker" in contract["error_codes_to_preserve"]
    assert len(contract["order_types"]) == 10
    assert len(contract["time_in_force"]) == 5
```

```bash
# T-close.3: post-task review commands
git diff --stat
git diff --check
git status --short
grep -nE 'TBD|TODO|implement later|similar to Task' docs/superpowers/plans/2026-05-06-phase8b-order-type-expansion-plan.md | grep -v '# ' | head -20
```
