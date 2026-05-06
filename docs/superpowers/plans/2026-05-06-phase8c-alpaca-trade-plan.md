# Phase 8c — Alpaca Trade (US Equity + Crypto) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Add Alpaca trade write path for US equity + crypto (4-tuple capability matrix extension), with native equity bracket/OCO + orchestrator-fallback crypto OCO, notional/cash_amount fractional ordering, exchange-aware crypto session bypass, and dual-stream (equity TradingStream + crypto stream) order event fan-in. Targets v0.10.0.

**Architecture:** Five implementation chunks. Chunk 0 widens schemas (4-tuple PK migration ATOMIC with service signature), proto field 15 cash_amount, market_calendar crypto bypass, broker_features per-asset-class widening, ETF bucket adapter, symbol normalization. Chunk S brings Alpaca equity trade live (PlaceOrder/CancelOrder/ModifyOrder/OrderEvent dual-stream). Chunk C adds crypto trade with notional XOR validator. Chunk B splits bracket into equity (native) + crypto (likely UNSUPPORTED, micro-empirical). Chunk OCO splits OCO into equity (native order_class=oco) + crypto (orchestrator-fallback or UNSUPPORTED). Close-out updates release notes, task status, and v0.10.0 tag.

**Tech Stack:** Python 3.14, Pydantic v2, FastAPI, SQLAlchemy 2 async, Alembic, asyncpg, Redis asyncio, exchange_calendars, alpaca-py, grpcio, protobuf/buf, pytest, pytest-asyncio, freezegun, GitHub Actions, pre-commit.

**Spec:** docs/superpowers/specs/2026-05-06-phase8c-alpaca-trade-design.md

**Global invariants:**
- 4-tuple capability gate: (broker_id, asset_class, order_type, time_in_force)
- notional (response, USD value of qty x price) is DISTINCT from cash_amount (request, fractional buy size)
- Crypto orders BYPASS market_calendar (crypto trades 24/7)
- Crypto symbols use BTC/USD slash notation end-to-end (canonical_crypto_symbol() normalizes)
- Quantity precision: NUMERIC(20, 10) for qty (was 8); money stays NUMERIC(20, 8)
- ETF mapped to STOCK bucket via adapter before capability lookup
- TradingStream cap = 5 per account; gRPC RESOURCE_EXHAUSTED if exceeded
- 5 LOW findings deferred — see plan footer

---

<!-- CHUNK 0 — Foundation (est. 4-5 days) -->

## Task T-0.1 — Alembic 0018: 4-tuple PK widening (atomic single-transaction migration)

**Files:**
- Create: `backend/alembic/versions/0018_phase8c_capability_4tuple.py`
- Test: `backend/tests/integration/test_alembic_0018.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

Verify current broker_order_capability PK (expect 3-tuple: broker_id, order_type, tif) and confirm no FK constraints reference it. Check broker_features PK (expect 2-tuple: broker_id, feature).

```bash
rg -n "broker_order_capability|broker_features" backend/alembic/versions/ backend/app/models/
psql "$DATABASE_URL" -c "\d+ broker_order_capability" | grep "Foreign-key"
psql "$DATABASE_URL" -c "SELECT kcu.column_name FROM information_schema.key_column_usage kcu JOIN information_schema.table_constraints tc ON kcu.constraint_name=tc.constraint_name WHERE tc.constraint_type='PRIMARY KEY' AND tc.table_name='broker_order_capability'"
```

- [ ] **Step 2: Write the focused tests**

File: `backend/tests/integration/test_alembic_0018.py`

```python
import pytest
from sqlalchemy import text

@pytest.mark.asyncio
async def test_capability_has_asset_class_column(async_session):
    r = await async_session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='broker_order_capability' AND column_name='asset_class'"
    ))
    assert r.scalar() == "asset_class"

@pytest.mark.asyncio
async def test_capability_pk_is_4tuple(async_session):
    r = await async_session.execute(text(
        "SELECT count(*) FROM information_schema.key_column_usage "
        "WHERE constraint_name='broker_order_capability_pkey'"
    ))
    assert r.scalar() == 4

@pytest.mark.asyncio
async def test_broker_features_pk_is_3tuple(async_session):
    r = await async_session.execute(text(
        "SELECT count(*) FROM information_schema.key_column_usage "
        "WHERE constraint_name='broker_features_pkey'"
    ))
    assert r.scalar() == 3

@pytest.mark.asyncio
async def test_alpaca_crypto_50_rows_seeded(async_session):
    r = await async_session.execute(text(
        "SELECT count(*) FROM broker_order_capability "
        "WHERE broker_id='alpaca' AND asset_class='CRYPTO'"
    ))
    assert r.scalar() == 50  # 10 order_types x 5 TIFs

@pytest.mark.asyncio
async def test_notional_orders_alpaca_stock_true(async_session):
    r = await async_session.execute(text(
        "SELECT is_supported FROM broker_features "
        "WHERE broker_id='alpaca' AND asset_class='STOCK' AND feature='notional_orders'"
    ))
    assert r.scalar() is True

@pytest.mark.asyncio
async def test_notional_orders_alpaca_crypto_true(async_session):
    r = await async_session.execute(text(
        "SELECT is_supported FROM broker_features "
        "WHERE broker_id='alpaca' AND asset_class='CRYPTO' AND feature='notional_orders'"
    ))
    assert r.scalar() is True

@pytest.mark.asyncio
async def test_orders_qty_is_10dp(async_session):
    r = await async_session.execute(text(
        "SELECT numeric_scale FROM information_schema.columns "
        "WHERE table_name='orders' AND column_name='qty'"
    ))
    assert r.scalar() == 10

@pytest.mark.asyncio
async def test_order_events_fill_qty_is_10dp(async_session):
    r = await async_session.execute(text(
        "SELECT numeric_scale FROM information_schema.columns "
        "WHERE table_name='order_events' AND column_name='fill_qty'"
    ))
    assert r.scalar() == 10

@pytest.mark.asyncio
async def test_idempotent_reseed_no_count_change(async_session):
    r1 = await async_session.execute(text(
        "SELECT count(*) FROM broker_order_capability WHERE asset_class='CRYPTO'"
    ))
    before = r1.scalar()
    from alembic.config import Config
    from alembic import command
    command.upgrade(Config("alembic.ini"), "0018")
    r2 = await async_session.execute(text(
        "SELECT count(*) FROM broker_order_capability WHERE asset_class='CRYPTO'"
    ))
    assert r2.scalar() == before
```

- [ ] **Step 3: Apply the implementation**

Create `backend/alembic/versions/0018_phase8c_capability_4tuple.py`. Revision `"0018"`, down_revision `"0017"`.

Migration comment: Phase 8c 4-tuple PK widening. ATOMIC single transaction. LOCK TABLE acquired first (CRIT-2). Includes: asset_class column on broker_order_capability and broker_features, PK reshaping, 10dp qty columns, Alpaca CRYPTO seeding (50 rows), broker_features notional_orders and bracket rows. ON CONFLICT DO NOTHING on all INSERTs (MED-11 idempotency). Downgrade drops asset_class; data NOT recoverable.

```python
_ORDER_TYPES = ["MARKET","LIMIT","STOP","STOP_LIMIT","TRAIL","TRAIL_LIMIT","MOC","MOO","LOC","LOO"]
_TIFS = ["DAY","GTC","IOC","FOK","GTD"]
_SESSION_BOUND = frozenset(["MOC","MOO","LOC","LOO"])

def upgrade() -> None:
    op.execute("LOCK TABLE broker_order_capability IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE broker_features IN ACCESS EXCLUSIVE MODE")

    # broker_order_capability: add asset_class, backfill STOCK, SET NOT NULL, add check, drop old PK, add 4-tuple PK
    op.add_column("broker_order_capability", sa.Column("asset_class", sa.String, nullable=True))
    op.execute("UPDATE broker_order_capability SET asset_class='STOCK' WHERE asset_class IS NULL")
    op.alter_column("broker_order_capability", "asset_class", nullable=False)
    op.execute("ALTER TABLE broker_order_capability ADD CONSTRAINT boc_asset_class_check "
               "CHECK (asset_class IN ('STOCK','CRYPTO','OPTION','FUTURE','FOREX','BOND'))")
    op.execute("ALTER TABLE broker_order_capability DROP CONSTRAINT broker_order_capability_pkey")
    op.execute("ALTER TABLE broker_order_capability ADD PRIMARY KEY (broker_id, asset_class, order_type, tif)")

    # broker_features: add asset_class DEFAULT STOCK, add check, drop old PK, add 3-tuple PK
    op.add_column("broker_features", sa.Column("asset_class", sa.String, nullable=False, server_default="STOCK"))
    op.execute("ALTER TABLE broker_features ADD CONSTRAINT bf_asset_class_check "
               "CHECK (asset_class IN ('STOCK','CRYPTO','OPTION','FUTURE','FOREX','BOND'))")
    op.execute("ALTER TABLE broker_features DROP CONSTRAINT broker_features_pkey")
    op.execute("ALTER TABLE broker_features ADD PRIMARY KEY (broker_id, asset_class, feature)")

    # 10dp qty columns (HIGH-5, bundled in ATOMIC 0018)
    for tbl, col in [("orders","qty"),("orders","filled_qty"),("order_events","fill_qty")]:
        op.execute(f"ALTER TABLE {tbl} ALTER COLUMN {col} TYPE NUMERIC(20, 10)")

    conn = op.get_bind()
    # seed 50 Alpaca CRYPTO rows (10 types x 5 TIFs)
    for ot in _ORDER_TYPES:
        for tif in _TIFS:
            supported = ot in ("MARKET","LIMIT") and tif in ("GTC","IOC","FOK","DAY") and ot not in _SESSION_BOUND
            notes = "Crypto: 24/7 — session-bound types not applicable" if ot in _SESSION_BOUND else None
            conn.execute(sa.text(
                "INSERT INTO broker_order_capability (broker_id,asset_class,order_type,tif,is_supported,notes) "
                "VALUES (:b,'CRYPTO',:ot,:tif,:s,:n) ON CONFLICT (broker_id,asset_class,order_type,tif) DO NOTHING"
            ), {"b":"alpaca","ot":ot,"tif":tif,"s":supported,"n":notes})
    # seed placeholder CRYPTO rows for schwab/ibkr/futu (all FALSE)
    for broker in ("schwab","ibkr","futu"):
        for ot in _ORDER_TYPES:
            for tif in _TIFS:
                conn.execute(sa.text(
                    "INSERT INTO broker_order_capability (broker_id,asset_class,order_type,tif,is_supported,notes) "
                    "VALUES (:b,'CRYPTO',:ot,:tif,FALSE,:n) ON CONFLICT (broker_id,asset_class,order_type,tif) DO NOTHING"
                ), {"b":broker,"ot":ot,"tif":tif,"n":"Crypto not supported — placeholder for Phase 15+"})
    # seed broker_features notional_orders
    for b,ac,v in [("alpaca","STOCK",True),("alpaca","CRYPTO",True),
                   ("schwab","STOCK",False),("ibkr","STOCK",False),("futu","STOCK",False)]:
        conn.execute(sa.text(
            "INSERT INTO broker_features (broker_id,asset_class,feature,is_supported) "
            "VALUES (:b,:ac,'notional_orders',:v) ON CONFLICT (broker_id,asset_class,feature) DO NOTHING"
        ), {"b":b,"ac":ac,"v":v})
    # seed bracket rows for alpaca
    for ac, val in [("STOCK",True), ("CRYPTO",False)]:
        conn.execute(sa.text(
            "INSERT INTO broker_features (broker_id,asset_class,feature,is_supported) "
            "VALUES ('alpaca',:ac,'bracket',:v) ON CONFLICT (broker_id,asset_class,feature) DO NOTHING"
        ), {"ac":ac,"v":val})

def downgrade() -> None:
    # WARNING: data NOT recoverable after downgrade
    op.execute("ALTER TABLE broker_order_capability DROP CONSTRAINT broker_order_capability_pkey")
    op.execute("ALTER TABLE broker_order_capability ADD PRIMARY KEY (broker_id, order_type, tif)")
    op.drop_column("broker_order_capability", "asset_class")
    op.execute("ALTER TABLE broker_features DROP CONSTRAINT broker_features_pkey")
    op.execute("ALTER TABLE broker_features ADD PRIMARY KEY (broker_id, feature)")
    op.drop_column("broker_features", "asset_class")
    for tbl, col in [("orders","qty"),("orders","filled_qty"),("order_events","fill_qty")]:
        op.execute(f"ALTER TABLE {tbl} ALTER COLUMN {col} TYPE NUMERIC(20, 8)")
```

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run alembic upgrade 0018 && uv run pytest tests/integration/test_alembic_0018.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "LOCK TABLE|ON CONFLICT DO NOTHING|4tuple|0018" backend/alembic/versions/0018_phase8c_capability_4tuple.py | head -20
psql "$DATABASE_URL" -c "SELECT count(*) FROM broker_order_capability WHERE asset_class='CRYPTO'"
# Expect 200 (4 brokers x 50 rows)
```

- [ ] **Step 6: Conventional commit**

```bash
git add backend/alembic/versions/0018_phase8c_capability_4tuple.py backend/tests/integration/test_alembic_0018.py
git commit -m "feat(db): Alembic 0018 — 4-tuple PK widening + broker_features asset_class + 10dp qty"
```

---

## Task T-0.2 â OrderCapabilityService 4-tuple signature + deprecation shim + ETF bucket

**Files:**
- Modify: `backend/app/services/order_capability_service.py`
- Test: `backend/tests/unit/test_order_capability_service_4tuple.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "def is_supported|LRUCache|maxsize" backend/app/services/order_capability_service.py
grep -rn "is_supported(" backend/app/ | grep -v "_deprecated|def is_supported" | wc -l
# Record count â after T-0.3 it must reach 0
```

- [ ] **Step 2: Write the focused tests**

File: `backend/tests/unit/test_order_capability_service_4tuple.py`

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

@pytest.fixture
def svc():
    from app.services.order_capability_service import OrderCapabilityService
    return OrderCapabilityService(db=AsyncMock())

@pytest.mark.asyncio
async def test_4tuple_passes_asset_class(svc):
    svc._fetch_row = AsyncMock(return_value=True)
    await svc.is_supported("alpaca", "CRYPTO", "MARKET", "DAY")
    svc._fetch_row.assert_called_once_with("alpaca", "CRYPTO", "MARKET", "DAY")

@pytest.mark.asyncio
async def test_deprecated_shim_defaults_stock_and_warns(svc):
    svc._fetch_row = AsyncMock(return_value=True)
    with patch("app.services.order_capability_service.structlog") as mock_sl:
        logger = MagicMock()
        mock_sl.get_logger.return_value = logger
        with patch("app.services.order_capability_service.metrics"):
            await svc.is_supported_3tuple_deprecated("alpaca", "MARKET", "DAY")
    svc._fetch_row.assert_called_once_with("alpaca", "STOCK", "MARKET", "DAY")
    logger.warning.assert_called_once()
    assert "order_capability_legacy_3tuple_called" in str(logger.warning.call_args)

@pytest.mark.asyncio
async def test_deprecated_shim_increments_counter(svc):
    svc._fetch_row = AsyncMock(return_value=False)
    with patch("app.services.order_capability_service.metrics") as m:
        with patch("app.services.order_capability_service.structlog"):
            await svc.is_supported_3tuple_deprecated("schwab", "LIMIT", "GTC")
    m.counter.assert_called_with("order_capability_legacy_3tuple_calls_total")
    m.counter.return_value.inc.assert_called_once()

@pytest.mark.asyncio
async def test_etf_collapses_to_stock(svc):
    svc._fetch_row = AsyncMock(return_value=True)
    await svc.is_supported("alpaca", "ETF", "MARKET", "DAY")
    svc._fetch_row.assert_called_once_with("alpaca", "STOCK", "MARKET", "DAY")

def test_cache_maxsize_is_2048(svc):
    assert svc._cache.maxsize == 2048

@pytest.mark.asyncio
async def test_eviction_counter_fires_on_full_cache(svc):
    from cachetools import LRUCache
    svc._cache = LRUCache(maxsize=1)
    svc._fetch_row = AsyncMock(return_value=True)
    with patch("app.services.order_capability_service.metrics") as m:
        await svc.is_supported("alpaca", "STOCK", "MARKET", "DAY")
        await svc.is_supported("alpaca", "STOCK", "LIMIT", "GTC")
    m.counter.assert_any_call("order_capability_cache_evictions_total")
```

- [ ] **Step 3: Apply the implementation**

In `backend/app/services/order_capability_service.py`:

```python
import structlog
from cachetools import LRUCache
from sqlalchemy import text
from app.core import metrics

_ASSET_CLASS_BUCKET: dict[str, str] = {
    "STOCK": "STOCK",
    "ETF": "STOCK",    # ETF collapses to STOCK capability bucket (MED-7)
    "CRYPTO": "CRYPTO",
    # Phase 12+: OPTION, FUTURE, FOREX, BOND
}

def _capability_bucket(asset_class: str) -> str:
    return _ASSET_CLASS_BUCKET.get(asset_class, asset_class)

class OrderCapabilityService:
    def __init__(self, db) -> None:
        self._db = db
        self._cache: LRUCache = LRUCache(maxsize=2048)

    async def is_supported(
        self, broker: str, asset_class: str, order_type: str, tif: str
    ) -> bool:
        bucket = _capability_bucket(asset_class)
        key = (broker, bucket, order_type, tif)
        if key in self._cache:
            return self._cache[key]
        result = await self._fetch_row(broker, bucket, order_type, tif)
        if len(self._cache) >= self._cache.maxsize:
            metrics.counter("order_capability_cache_evictions_total").inc()
        self._cache[key] = result
        return result

    async def is_supported_3tuple_deprecated(
        self, broker: str, order_type: str, tif: str
    ) -> bool:
        """Deprecated: use is_supported(broker, asset_class, order_type, tif).
        Defaults asset_class=STOCK. Emits structlog warning + counter.
        SLO: counter must reach 0 within 24h of Alembic 0018 deploy.
        """
        structlog.get_logger().warning(
            "order_capability_legacy_3tuple_called",
            broker=broker, order_type=order_type, tif=tif,
        )
        metrics.counter("order_capability_legacy_3tuple_calls_total").inc()
        return await self.is_supported(broker, "STOCK", order_type, tif)

    async def _fetch_row(
        self, broker: str, asset_class: str, order_type: str, tif: str
    ) -> bool:
        row = await self._db.execute(
            text("SELECT is_supported FROM broker_order_capability "
                 "WHERE broker_id=:b AND asset_class=:ac AND order_type=:ot AND tif=:tif"),
            {"b": broker, "ac": asset_class, "ot": order_type, "tif": tif},
        )
        val = row.scalar()
        return bool(val) if val is not None else False
```

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest tests/unit/test_order_capability_service_4tuple.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "def is_supported|_ASSET_CLASS_BUCKET|LRUCache.*2048|order_capability_legacy" backend/app/services/order_capability_service.py
```

- [ ] **Step 6: Conventional commit**

```bash
git add backend/app/services/order_capability_service.py backend/tests/unit/test_order_capability_service_4tuple.py
git commit -m "feat(capability): widen is_supported to 4-tuple + ETF bucket + deprecation shim + cache 2048"
```

---

<!-- CHUNK 0 (continued) -->

## Task T-0.3 -- Migrate all is_supported callers to 4-tuple signature

**Files:**
- Modify: `backend/app/services/orders_service.py`
- Modify: `backend/app/routers/orders.py`
- Test: `backend/tests/integration/test_orders_service_4tuple_callers.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
grep -rn "is_supported(" backend/app/ | grep -v "_deprecated|def is_supported"
# Record count -- must reach 0
```

- [ ] **Step 2: Write the focused tests**

File: `backend/tests/integration/test_orders_service_4tuple_callers.py`

```python
import pytest, subprocess
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_4tuple_passes_asset_class(orders_service_fixture):
    with patch.object(
        orders_service_fixture.capability_svc, "is_supported",
        new_callable=AsyncMock, return_value=True
    ) as mock_sup:
        await orders_service_fixture.validate_capability(
            broker="alpaca", asset_class="CRYPTO", order_type="MARKET", tif="DAY"
        )
    mock_sup.assert_called_once_with("alpaca", "CRYPTO", "MARKET", "DAY")

def test_no_legacy_3tuple_calls_remain():
    result = subprocess.run(
        ["grep", "-rn", "is_supported(", "backend/app/"],
        capture_output=True, text=True
    )
    legacy = [l for l in result.stdout.splitlines()
              if "_deprecated" not in l and "def is_supported" not in l
              and l.count(",") < 3]
    assert legacy == [], "Legacy calls:\n" + "\n".join(legacy)
```

- [ ] **Step 3: Apply the implementation**

In backend/app/services/orders_service.py, update all 3-tuple calls
from capability_service.is_supported(broker_id, order.order_type, order.tif)
to the 4-tuple form:

    await self.capability_svc.is_supported(
        broker_id, contract.asset_class, order.order_type, order.tif
    )

For admin router endpoints: add asset_class query param.
After updates, audit grep must return 0:

    grep -rn is_supported( backend/app/ | grep -v deprecated | wc -l

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest tests/integration/test_orders_service_4tuple_callers.py tests/unit/test_order_capability_service_4tuple.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
grep -rn "is_supported(" backend/app/ | grep -v "_deprecated|def is_supported" | wc -l
# Must print 0
```

- [ ] **Step 6: Conventional commit**

```bash
git add backend/app/services/orders_service.py backend/app/routers/orders.py backend/tests/integration/test_orders_service_4tuple_callers.py
git commit -m "refactor(capability): migrate all is_supported callers to 4-tuple signature"
```

---

## Task T-0.4 -- market_calendar.crypto_eod() + CRYPTO asset_class bypass

**Files:**
- Modify: `backend/app/services/market_calendar.py`
- Modify: `backend/app/services/orders_service.py`
- Test: `backend/tests/unit/test_orders_service_crypto_bypasses_market_calendar.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "assert_market_open|eod_for_exchange|market_calendar|asset_class" backend/app/services/orders_service.py | head -20
rg -n "def assert_market_open|def eod_for_exchange|def crypto_eod" backend/app/services/market_calendar.py
```

- [ ] **Step 2: Write the focused tests**

File: `backend/tests/unit/test_orders_service_crypto_bypasses_market_calendar.py`

```python
import pytest
from unittest.mock import AsyncMock, patch
from datetime import date

@pytest.mark.asyncio
async def test_crypto_never_calls_assert_market_open(orders_service_fixture):
    # CRIT-3: CRYPTO must bypass all market_calendar calls
    with patch("app.services.orders_service.market_calendar") as mock_cal:
        mock_cal.assert_market_open.side_effect = RuntimeError("must not be called")
        await orders_service_fixture.validate_market_session(
            asset_class="CRYPTO", order_type="MARKET", exchange="CRYPTO", tif="DAY"
        )
    mock_cal.assert_market_open.assert_not_called()

@pytest.mark.asyncio
async def test_stock_calls_assert_market_open(orders_service_fixture):
    with patch("app.services.orders_service.market_calendar") as mock_cal:
        mock_cal.assert_market_open.return_value = None
        await orders_service_fixture.validate_market_session(
            asset_class="STOCK", order_type="MARKET", exchange="NYSE", tif="DAY"
        )
    mock_cal.assert_market_open.assert_called_once()

def test_crypto_eod_is_23_59_59_utc():
    from app.services.market_calendar import crypto_eod
    from datetime import datetime, timezone
    assert crypto_eod(date(2026, 5, 10)) == datetime(2026, 5, 10, 23, 59, 59, tzinfo=timezone.utc)

def test_crypto_gtd_uses_crypto_eod(orders_service_fixture):
    from app.services.market_calendar import crypto_eod
    with patch("app.services.orders_service.crypto_eod", wraps=crypto_eod) as mock_eod:
        orders_service_fixture.resolve_gtd_expiry(
            asset_class="CRYPTO", tif="GTD", expiry_date=date(2026, 5, 10), exchange="CRYPTO"
        )
    mock_eod.assert_called_once_with(date(2026, 5, 10))

def test_stock_gtd_does_not_use_crypto_eod(orders_service_fixture):
    with patch("app.services.orders_service.crypto_eod") as mock_c:
        with patch("app.services.orders_service.market_calendar") as mock_cal:
            mock_cal.eod_for_exchange.return_value = None
            orders_service_fixture.resolve_gtd_expiry(
                asset_class="STOCK", tif="GTD", expiry_date=date(2026, 5, 10), exchange="NYSE"
            )
    mock_c.assert_not_called()
```

- [ ] **Step 3: Apply the implementation**

In backend/app/services/market_calendar.py, add:

    from datetime import date, datetime, time, timezone

    def crypto_eod(expiry_date: date) -> datetime:
        """UTC end-of-day for a crypto GTD order (23:59:59 UTC)."""
        return datetime.combine(expiry_date, time(23, 59, 59), tzinfo=timezone.utc)

In backend/app/services/orders_service.py, import crypto_eod and apply CRYPTO bypass:

    from app.services.market_calendar import crypto_eod

    # At every assert_market_open call site:
    if asset_class != "CRYPTO":
        market_calendar.assert_market_open(exchange, order_type)

    # GTD expiry resolution:
    def resolve_gtd_expiry(self, asset_class, tif, expiry_date, exchange):
        if asset_class == "CRYPTO" and tif == "GTD":
            return crypto_eod(expiry_date)
        return market_calendar.eod_for_exchange(exchange, expiry_date)

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest tests/unit/test_orders_service_crypto_bypasses_market_calendar.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "def crypto_eod|asset_class.*CRYPTO|!= .CRYPTO." backend/app/services/market_calendar.py backend/app/services/orders_service.py
```

- [ ] **Step 6: Conventional commit**

```bash
git add backend/app/services/market_calendar.py backend/app/services/orders_service.py backend/tests/unit/test_orders_service_crypto_bypasses_market_calendar.py
git commit -m "feat(calendar): crypto_eod() helper + CRYPTO bypasses market_calendar (CRIT-3)"
```

---

## Task T-0.5 -- canonical_crypto_symbol() normalization helper

**Files:**
- Modify: `sidecar_alpaca/normalize.py`
- Test: `backend/app/brokers/symbol_normalize.py (create)`
- Test: `backend/tests/unit/test_symbol_normalize.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "symbol_normalize|canonical_crypto|BTCUSD" backend/app/brokers/ sidecar_alpaca/ 2>/dev/null || echo none
ls backend/app/brokers/
```

- [ ] **Step 2: Write the focused tests**

File: `backend/tests/unit/test_symbol_normalize.py`

```python
from app.brokers.symbol_normalize import canonical_crypto_symbol

def test_btcusd(): assert canonical_crypto_symbol("BTCUSD") == "BTC/USD"
def test_ethusd(): assert canonical_crypto_symbol("ETHUSD") == "ETH/USD"
def test_already(): assert canonical_crypto_symbol("BTC/USD") == "BTC/USD"
def test_shib():   assert canonical_crypto_symbol("SHIBUSD") == "SHIB/USD"
def test_ltc():    assert canonical_crypto_symbol("LTCUSD") == "LTC/USD"
def test_ethbtc(): assert canonical_crypto_symbol("ETHBTC") == "ETH/BTC"
def test_idempotent():
    s = "SOL/USD"
    assert canonical_crypto_symbol(canonical_crypto_symbol(s)) == s
def test_short():  assert canonical_crypto_symbol("BT") == "BT"
```

- [ ] **Step 3: Apply the implementation**

Create backend/app/brokers/symbol_normalize.py:

    def canonical_crypto_symbol(s: str) -> str:
        """Normalize Alpaca WS crypto symbol to canonical BTC/USD slashed form.
        Heuristic: quote currencies are 3 chars (USD, EUR, GBP, BTC, ETH).
        """
        if "/" in s:
            return s
        if len(s) < 4:
            return s
        return f"{s[:-3]}/{s[-3:]}"

In sidecar_alpaca/normalize.py, add import and usage:

    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
    from app.brokers.symbol_normalize import canonical_crypto_symbol

    def normalize_crypto_stream_event(event: dict) -> dict:
        """Normalize crypto stream event symbol from BTCUSD to BTC/USD form."""
        if "S" in event:
            return {**event, "S": canonical_crypto_symbol(event["S"])}
        return event

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest tests/unit/test_symbol_normalize.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "def canonical_crypto_symbol|symbol_normalize|normalize_crypto_stream_event" backend/app/brokers/symbol_normalize.py sidecar_alpaca/normalize.py
```

- [ ] **Step 6: Conventional commit**

```bash
git add backend/app/brokers/symbol_normalize.py backend/tests/unit/test_symbol_normalize.py sidecar_alpaca/normalize.py
git commit -m "feat(symbol): canonical_crypto_symbol() helper + sidecar stream ingress normalization"
```

---

## Task T-0.6 -- proto field 15 cash_amount + all-sidecar pass-through

**Files:**
- Modify: `protos/orders.proto`
- Modify: `sidecar_alpaca/normalize.py`
- Modify: `sidecar_ibkr/normalize.py`
- Modify: `sidecar_futu/normalize.py`
- Modify: `sidecar_schwab/normalize.py`
- Test: `sidecar_alpaca/tests/test_normalize_cash_amount.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "cash_amount|field 15" protos/orders.proto sidecar_*/normalize.py 2>/dev/null || echo none
grep -n "= 14" protos/orders.proto
```

- [ ] **Step 2: Write the focused tests**

File: `sidecar_alpaca/tests/test_normalize_cash_amount.py`

```python
import pytest
from sidecar_alpaca.normalize import proto_order_to_alpaca_payload

def base(**kw):
    return {"symbol":"AAPL","side":"buy","order_type":"MARKET","tif":"DAY",**kw}

def test_cash_amount_maps_to_notional():
    p = proto_order_to_alpaca_payload(base(cash_amount="100.00"))
    assert p["notional"] == "100.00" and "qty" not in p

def test_qty_maps_to_qty():
    p = proto_order_to_alpaca_payload(base(qty="5"))
    assert p["qty"] == "5" and "notional" not in p

def test_both_raises():
    with pytest.raises(ValueError, match="XOR"):
        proto_order_to_alpaca_payload(base(qty="5", cash_amount="100.00"))

def test_neither_raises():
    with pytest.raises(ValueError, match="XOR"):
        proto_order_to_alpaca_payload(base())
```

- [ ] **Step 3: Apply the implementation**

In protos/orders.proto, add after field 14:

    // Phase 8c field 15: request-side fractional cash amount in USD.
    // XOR with qty. Implies side=BUY, order_type=MARKET, tif=DAY.
    // DISTINCT from response field notional (qty x price).
    string cash_amount = 15;

Run buf generate in each sidecar directory. In sidecar_alpaca/normalize.py:

    def proto_order_to_alpaca_payload(req: dict) -> dict:
        qty = req.get("qty") or None
        cash = req.get("cash_amount") or None
        if bool(qty) == bool(cash):
            raise ValueError("XOR: exactly one of qty or cash_amount must be set")
        payload = {
            "symbol": req["symbol"],
            "side": req["side"].lower(),
            "type": _map_order_type(req["order_type"]),
            "time_in_force": req["tif"].lower(),
        }
        if cash:
            payload["notional"] = cash  # Alpaca REST key for notional orders
        else:
            payload["qty"] = qty
        return payload

For sidecar_ibkr/futu/schwab: proto stub exposes cash_amount but normalize functions
do NOT map it. Backend capability gate (notional_orders=FALSE) rejects before sidecar.

- [ ] **Step 4: Run the focused test command**

```bash
cd protos && buf lint
cd ../sidecar_alpaca && buf generate && uv run pytest tests/test_normalize_cash_amount.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "cash_amount|field 15|notional" protos/orders.proto sidecar_alpaca/normalize.py
```

- [ ] **Step 6: Conventional commit**

```bash
git add protos/orders.proto sidecar_alpaca/normalize.py sidecar_ibkr/normalize.py sidecar_futu/normalize.py sidecar_schwab/normalize.py sidecar_alpaca/tests/test_normalize_cash_amount.py
git commit -m "feat(proto): add field 15 cash_amount + alpaca notional adapter mapping"
```

---

## Task T-0.7 -- PlaceOrderRequest / PreviewRequest qty + cash_amount XOR validator (10dp regex)

**Files:**
- Modify: `backend/app/schemas/orders.py`
- Test: `backend/tests/unit/test_orders_schema_cash_amount.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "class PlaceOrderRequest|class PreviewRequest|qty.*str|cash_amount|model_validator" backend/app/schemas/orders.py | head -20
rg -n "QTY_PATTERN|1,8|1,10" backend/app/schemas/orders.py
```

- [ ] **Step 2: Write the focused tests**

File: `backend/tests/unit/test_orders_schema_cash_amount.py`

```python
import pytest
from pydantic import ValidationError
from app.schemas.orders import PlaceOrderRequest

def base(**kw):
    return {"order_type":"MARKET","tif":"DAY","side":"BUY","symbol":"AAPL","broker_id":"alpaca",**kw}

def test_cash_amount_only_valid():
    r = PlaceOrderRequest.model_validate(base(cash_amount="100.00"))
    assert r.cash_amount == "100.00" and r.qty is None

def test_qty_only_valid():
    r = PlaceOrderRequest.model_validate(base(qty="5"))
    assert r.qty == "5" and r.cash_amount is None

def test_both_rejects():
    with pytest.raises(ValidationError, match="exactly one"):
        PlaceOrderRequest.model_validate(base(qty="5", cash_amount="100.00"))

def test_neither_rejects():
    with pytest.raises(ValidationError, match="exactly one"):
        PlaceOrderRequest.model_validate(base())

def test_cash_amount_sell_rejects():
    with pytest.raises(ValidationError, match="BUY"):
        PlaceOrderRequest.model_validate(base(cash_amount="100.00", side="SELL"))

def test_cash_amount_limit_rejects():
    with pytest.raises(ValidationError, match="MARKET"):
        PlaceOrderRequest.model_validate(
            base(cash_amount="100.00", order_type="LIMIT", limit_price="150.00"))

def test_cash_amount_gtc_rejects():
    with pytest.raises(ValidationError, match="DAY"):
        PlaceOrderRequest.model_validate(base(cash_amount="100.00", tif="GTC"))

def test_10dp_qty_accepted():
    r = PlaceOrderRequest.model_validate(base(qty="0.0000000001"))
    assert r.qty == "0.0000000001"

def test_11dp_qty_rejects():
    with pytest.raises(ValidationError):
        PlaceOrderRequest.model_validate(base(qty="0.00000000001"))
```

- [ ] **Step 3: Apply the implementation**

In backend/app/schemas/orders.py, add:

    import re
    from pydantic import BaseModel, model_validator

    QTY_PATTERN = re.compile(r"^\d+(\.\d{1,10})?$")  # 10dp (widened from 8dp for crypto)

    class PlaceOrderRequest(BaseModel):
        qty: str | None = None
        cash_amount: str | None = None
        order_type: str
        tif: str = "DAY"
        side: str
        symbol: str
        broker_id: str

        @model_validator(mode="after")
        def _validate_qty_cash_xor(self) -> "PlaceOrderRequest":
            has_qty = bool(self.qty)
            has_cash = bool(self.cash_amount)
            if has_qty == has_cash:
                raise ValueError("exactly one of qty or cash_amount must be set (XOR)")
            if has_cash:
                if self.side.upper() != "BUY":
                    raise ValueError("cash_amount requires side=BUY")
                if self.order_type.upper() != "MARKET":
                    raise ValueError("cash_amount requires order_type=MARKET")
                if self.tif.upper() != "DAY":
                    raise ValueError("cash_amount requires tif=DAY")
            return self

Apply identical qty/cash_amount fields and XOR validator to PreviewRequest.
For OrderModifyRequest, add mode-switch prevention:
    if (self.qty and self._original_was_cash_based) or\n       (self.cash_amount and not self._original_was_cash_based):
        raise ValueError("Cannot switch between qty and cash_amount on modify")

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest tests/unit/test_orders_schema_cash_amount.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "QTY_PATTERN|cash_amount.*XOR|exactly one|1,10" backend/app/schemas/orders.py
```

- [ ] **Step 6: Conventional commit**

```bash
git add backend/app/schemas/orders.py backend/tests/unit/test_orders_schema_cash_amount.py
git commit -m "feat(schema): qty/cash_amount XOR validator + 10dp regex on PlaceOrderRequest and PreviewRequest"
```

---

## Task T-0.8 -- Capability API grouped-by-asset-class response

**Files:**
- Modify: `backend/app/services/order_capability_service.py`
- Modify: `backend/app/routers/brokers.py`
- Test: `backend/tests/integration/test_capability_api_grouped.py`

- [ ] **Step 1: Pre-flight inspect the existing surface**

```bash
rg -n "/capabilities|list_capabilities|asset_class.*query" backend/app/routers/ backend/app/services/order_capability_service.py | head -20
```

- [ ] **Step 2: Write the focused tests**

File: `backend/tests/integration/test_capability_api_grouped.py`

```python
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_with_asset_class_returns_flat_list(client: AsyncClient):
    resp = await client.get("/api/brokers/alpaca/capabilities?asset_class=STOCK")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert all(r["asset_class"] == "STOCK" for r in data)

@pytest.mark.asyncio
async def test_no_asset_class_alpaca_returns_grouped_dict(client: AsyncClient):
    resp = await client.get("/api/brokers/alpaca/capabilities")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert "STOCK" in data and "CRYPTO" in data

@pytest.mark.asyncio
async def test_single_class_broker_returns_flat_list(client: AsyncClient):
    # schwab has STOCK only -- backward compat flat list
    resp = await client.get("/api/brokers/schwab/capabilities")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
```

- [ ] **Step 3: Apply the implementation**

In backend/app/services/order_capability_service.py, add method:

    async def list_capabilities(
        self, broker_id: str, asset_class: str | None = None
    ) -> list[dict] | dict[str, list[dict]]:
        if asset_class:
            result = await self._db.execute(
                text("SELECT * FROM broker_order_capability "
                     "WHERE broker_id=:b AND asset_class=:ac ORDER BY order_type, tif"),
                {"b": broker_id, "ac": asset_class},
            )
            return [dict(r) for r in result.mappings()]
        result = await self._db.execute(
            text("SELECT * FROM broker_order_capability WHERE broker_id=:b "
                 "ORDER BY asset_class, order_type, tif"),
            {"b": broker_id},
        )
        rows = [dict(r) for r in result.mappings()]
        classes = list({r["asset_class"] for r in rows})
        if len(classes) <= 1:
            return rows  # backward compat flat list for single-asset-class brokers
        grouped: dict[str, list[dict]] = {}
        for r in rows:
            grouped.setdefault(r["asset_class"], []).append(r)
        return grouped

In the router, update endpoint:

    @router.get("/brokers/{broker_id}/capabilities")
    async def get_capabilities(
        broker_id: str,
        asset_class: str | None = None,
        svc: OrderCapabilityService = Depends(get_capability_service),
    ):
        return await svc.list_capabilities(broker_id, asset_class)

- [ ] **Step 4: Run the focused test command**

```bash
cd backend && uv run pytest tests/integration/test_capability_api_grouped.py -v
```

- [ ] **Step 5: Run the verification grep**

```bash
rg -n "list_capabilities|asset_class.*None|grouped|backward compat" backend/app/services/order_capability_service.py backend/app/routers/
```

- [ ] **Step 6: Conventional commit**

```bash
git add backend/app/services/order_capability_service.py backend/app/routers/ backend/tests/integration/test_capability_api_grouped.py
git commit -m "feat(api): GET /capabilities returns dict grouped by asset_class for multi-class brokers"
```

---

<!-- CHUNK S -- Alpaca Equity Trade (est. 4-5 days) -->


## Task T-0.9: 8dp to 10dp quantity migration

Files:
- `backend/alembic/versions/0019_qty_10dp.py`
- `app/services/orders.py`
- `sidecar_alpaca/handlers.py`
- `sidecar_ibkr/handlers.py`
- `sidecar_futu/handlers.py`
- `sidecar_schwab/handlers.py`

- [ ] **Step 1: Pre-flight grep**

```bash
rg -n "NUMERIC\(20,8\)|Numeric\(20, 8\)|filled_qty|fill_qty|qty" backend app sidecar_alpaca sidecar_ibkr sidecar_futu sidecar_schwab
rg -n "_format_decimal_8|1e-8|quantize" app sidecar_alpaca sidecar_ibkr sidecar_futu sidecar_schwab
ls backend/alembic/versions | sort | tail -20
rg -n "orders\.qty|orders\.filled_qty|order_events\.fill_qty" backend/tests app/tests
rg -n "asset_class.*CRYPTO|asset_class.*EQUITY|asset_class.*STOCK" app sidecar_alpaca
```

- [ ] **Step 2: Write tests**

```python
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.services.orders import _format_decimal_10


def test_format_decimal_10_preserves_crypto_precision():
    assert _format_decimal_10(Decimal('0.1234567891')) == '0.1234567891'
    assert _format_decimal_10(Decimal('1')) == '1.0000000000'


def test_format_decimal_10_rejects_more_than_ten_dp():
    with pytest.raises(ValueError, match='10 decimal places'):
        _format_decimal_10(Decimal('0.12345678911'))


@pytest.mark.asyncio
async def test_qty_columns_are_10dp_after_0019(async_session):
    result = await async_session.execute(text("""
        SELECT column_name, numeric_precision, numeric_scale
        FROM information_schema.columns
        WHERE table_name IN ('orders', 'order_events')
          AND column_name IN ('qty', 'filled_qty', 'fill_qty')
        ORDER BY table_name, column_name
    """))
    scales = {row.column_name: row.numeric_scale for row in result}
    assert scales['qty'] == 10
    assert scales['filled_qty'] == 10
    assert scales['fill_qty'] == 10


def test_sidecar_decimal_helpers_export_10dp_names():
    import sidecar_alpaca.handlers as alpaca
    import sidecar_ibkr.handlers as ibkr
    import sidecar_futu.handlers as futu
    import sidecar_schwab.handlers as schwab
    for module in (alpaca, ibkr, futu, schwab):
        assert hasattr(module, '_format_decimal_10')
        assert not hasattr(module, '_format_decimal_8')
```

- [ ] **Step 3: Apply the implementation**

```python
# backend/alembic/versions/0019_qty_10dp.py
from alembic import op
import sqlalchemy as sa

revision = '0019_qty_10dp'
down_revision = '0018_phase8c_capability_4tuple'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('orders', 'qty', existing_type=sa.Numeric(20, 8), type_=sa.Numeric(20, 10), existing_nullable=False)
    op.alter_column('orders', 'filled_qty', existing_type=sa.Numeric(20, 8), type_=sa.Numeric(20, 10), existing_nullable=True)
    op.alter_column('order_events', 'fill_qty', existing_type=sa.Numeric(20, 8), type_=sa.Numeric(20, 10), existing_nullable=True)


def downgrade() -> None:
    op.alter_column('order_events', 'fill_qty', existing_type=sa.Numeric(20, 10), type_=sa.Numeric(20, 8), existing_nullable=True)
    op.alter_column('orders', 'filled_qty', existing_type=sa.Numeric(20, 10), type_=sa.Numeric(20, 8), existing_nullable=True)
    op.alter_column('orders', 'qty', existing_type=sa.Numeric(20, 10), type_=sa.Numeric(20, 8), existing_nullable=False)


# app/services/orders.py and each sidecar adapter
from decimal import Decimal, InvalidOperation

TEN_DP = Decimal('0.0000000001')


def _format_decimal_10(value: Decimal | str) -> str:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('invalid decimal value') from exc
    quantized = decimal_value.quantize(TEN_DP)
    if quantized != decimal_value:
        raise ValueError('value exceeds 10 decimal places')
    return f'{quantized:.10f}'


def _format_order_qty(value: Decimal | str, asset_class: str) -> str:
    # Quantity columns are 10dp for all asset classes; equity values remain a subset.
    return _format_decimal_10(value)
```

- [ ] **Step 4: Run the focused test**

```bash
cd backend && uv run pytest tests/test_order_qty_precision.py -v
cd backend && uv run pytest tests/alembic/test_0019_qty_10dp.py -v
pnpm -s proto:check
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "0019_qty_10dp|Numeric\(20, 10\)|NUMERIC\(20, 10\)" backend/alembic app backend/tests
rg -n "_format_decimal_8" app sidecar_alpaca sidecar_ibkr sidecar_futu sidecar_schwab || true
rg -n "_format_decimal_10|TEN_DP" app sidecar_alpaca sidecar_ibkr sidecar_futu sidecar_schwab
```

- [ ] **Step 6: Commit**

Subject:

```text
fix(orders): widen quantity precision to 10 decimal places
```

Body:

```text
Add Alembic 0019 for qty, filled_qty, and fill_qty precision. Rename adapter decimal formatting to the 10dp helper and cover the migration plus formatter behavior with focused tests.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-0.11: Cache size bump and eviction metric

Files:
- `app/services/order_capability.py`
- `app/metrics.py`
- `backend/tests/test_order_capability.py`

- [ ] **Step 1: Pre-flight grep**

```bash
rg -n "order_capability_cache|LRU|lru|cache_size|eviction" app backend/tests
rg -n "Counter|prometheus|metrics" app/metrics.py app
rg -n "OrderCapabilityService|is_supported" backend/tests/test_order_capability.py app/services/order_capability.py
```

- [ ] **Step 2: Write tests**

```python
from app.services.order_capability import OrderCapabilityService
from app.metrics import ORDER_CAPABILITY_CACHE_EVICTIONS


class FakeRepo:
    def __init__(self):
        self.calls = 0

    async def is_supported(self, broker_id, asset_class, order_type, tif):
        self.calls += 1
        return True


async def test_capability_cache_holds_2048_entries():
    repo = FakeRepo()
    svc = OrderCapabilityService(repo=repo)
    for idx in range(2048):
        assert await svc.is_supported('alpaca', 'EQUITY', f'MARKET_{idx}', 'DAY') is True
    assert svc.order_capability_cache.currsize == 2048


async def test_capability_cache_eviction_increments_metric(monkeypatch):
    seen = []

    class Counter:
        def labels(self, broker_id, asset_class):
            seen.append((broker_id, asset_class))
            return self
        def inc(self):
            seen.append('inc')

    monkeypatch.setattr('app.services.order_capability.ORDER_CAPABILITY_CACHE_EVICTIONS', Counter())
    repo = FakeRepo()
    svc = OrderCapabilityService(repo=repo, cache_size=2)
    await svc.is_supported('alpaca', 'EQUITY', 'MARKET', 'DAY')
    await svc.is_supported('alpaca', 'CRYPTO', 'MARKET', 'GTC')
    await svc.is_supported('ibkr', 'EQUITY', 'LIMIT', 'DAY')
    assert ('alpaca', 'EQUITY') in seen
    assert 'inc' in seen
```

- [ ] **Step 3: Apply the implementation**

```python
# app/metrics.py
from prometheus_client import Counter

ORDER_CAPABILITY_CACHE_EVICTIONS = Counter(
    'order_capability_cache_evictions_total',
    'LRU evictions from the broker order capability cache.',
    ['broker_id', 'asset_class'],
)


# app/services/order_capability.py
from collections import OrderedDict
from dataclasses import dataclass

from app.metrics import ORDER_CAPABILITY_CACHE_EVICTIONS


@dataclass(frozen=True)
class CapabilityKey:
    broker_id: str
    asset_class: str
    order_type: str
    tif: str


class OrderCapabilityService:
    def __init__(self, repo, cache_size: int = 2048):
        self.repo = repo
        self.cache_size = cache_size
        self.order_capability_cache: OrderedDict[CapabilityKey, bool] = OrderedDict()

    async def is_supported(self, broker_id: str, asset_class: str, order_type: str, tif: str) -> bool:
        key = CapabilityKey(broker_id, asset_class, order_type, tif)
        if key in self.order_capability_cache:
            self.order_capability_cache.move_to_end(key)
            return self.order_capability_cache[key]
        value = await self.repo.is_supported(broker_id, asset_class, order_type, tif)
        self.order_capability_cache[key] = value
        self.order_capability_cache.move_to_end(key)
        self._evict_if_needed()
        return value

    def _evict_if_needed(self) -> None:
        while len(self.order_capability_cache) > self.cache_size:
            evicted_key, _ = self.order_capability_cache.popitem(last=False)
            ORDER_CAPABILITY_CACHE_EVICTIONS.labels(
                broker_id=evicted_key.broker_id,
                asset_class=evicted_key.asset_class,
            ).inc()
```

- [ ] **Step 4: Run the focused test**

```bash
cd backend && uv run pytest tests/test_order_capability.py -v
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "2048|order_capability_cache_evictions_total|ORDER_CAPABILITY_CACHE_EVICTIONS|popitem" app/services/order_capability.py app/metrics.py backend/tests/test_order_capability.py
```

- [ ] **Step 6: Commit**

Subject:

```text
feat(capabilities): record order capability cache evictions
```

Body:

```text
Increase the order capability LRU size to 2048 and expose an eviction counter labelled by broker and asset class. Add focused tests for capacity and metric emission.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-S.1: sidecar_alpaca PlaceOrder live path

Files:
- `sidecar_alpaca/handlers.py`
- `sidecar_alpaca/tests/test_place_order.py`

- [ ] **Step 1: Pre-flight grep**

```bash
rg -n "class Alpaca|Configure|PlaceOrder|TradingClient|submit_order" sidecar_alpaca
rg -n "OrderType|TimeInForce|MarketOrderRequest|LimitOrderRequest|StopOrderRequest|TrailingStopOrderRequest" sidecar_alpaca proto
```

- [ ] **Step 2: Write tests**

```python
from decimal import Decimal
from types import SimpleNamespace

import pytest

from sidecar_alpaca.handlers import AlpacaTradeServicer


class FakeTradingClient:
    def __init__(self):
        self.requests = []

    def submit_order(self, order_data):
        self.requests.append(order_data)
        return SimpleNamespace(id='alpaca-order-123', client_order_id=getattr(order_data, 'client_order_id', None))


@pytest.mark.asyncio
async def test_place_limit_order_submits_decimal_qty():
    client = FakeTradingClient()
    svc = AlpacaTradeServicer(trading_client_factory=lambda *_: client)
    await svc.Configure(SimpleNamespace(api_key='k', api_secret='s', mode='paper'), None)
    req = SimpleNamespace(symbol='AAPL', side='BUY', order_type='LIMIT', qty='1.25', limit_price='150.00', tif='DAY', asset_class='EQUITY', client_order_id='cid-1')
    resp = await svc.PlaceOrder(req, None)
    assert resp.external_order_id == 'alpaca-order-123'
    assert client.requests[0].qty == Decimal('1.25')
    assert client.requests[0].limit_price == Decimal('150.00')


@pytest.mark.parametrize('order_type, field, value', [
    ('MARKET', 'qty', Decimal('2')),
    ('STOP', 'stop_price', Decimal('140.00')),
    ('TRAILING_STOP', 'trail_percent', Decimal('1.5')),
])
@pytest.mark.asyncio
async def test_place_order_maps_supported_types(order_type, field, value):
    client = FakeTradingClient()
    svc = AlpacaTradeServicer(trading_client_factory=lambda *_: client)
    await svc.Configure(SimpleNamespace(api_key='k', api_secret='s', mode='live'), None)
    req = SimpleNamespace(symbol='AAPL', side='SELL', order_type=order_type, qty='2', limit_price='', stop_price='140.00', trail_percent='1.5', tif='GTC', asset_class='EQUITY', client_order_id='')
    resp = await svc.PlaceOrder(req, None)
    assert resp.external_order_id == 'alpaca-order-123'
    assert getattr(client.requests[0], field) == value
```

- [ ] **Step 3: Apply the implementation**

```python
from decimal import Decimal
from uuid import uuid4

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest, StopOrderRequest, TrailingStopOrderRequest


TIF_MAP = {'DAY': TimeInForce.DAY, 'GTC': TimeInForce.GTC, 'IOC': TimeInForce.IOC, 'FOK': TimeInForce.FOK}
SIDE_MAP = {'BUY': OrderSide.BUY, 'SELL': OrderSide.SELL}


class AlpacaTradeServicer:
    def __init__(self, trading_client_factory=TradingClient):
        self._trading_client_factory = trading_client_factory
        self._trading_client = None

    async def Configure(self, request, context):
        paper = request.mode.lower() != 'live'
        self._trading_client = self._trading_client_factory(request.api_key, request.api_secret, paper=paper)
        return ConfigureResponse(ok=True)

    async def PlaceOrder(self, request, context):
        order_data = self._build_order_request(request)
        order = self._trading_client.submit_order(order_data=order_data)
        return PlaceOrderResponse(external_order_id=str(order.id), client_order_id=getattr(order, 'client_order_id', ''))

    def _build_order_request(self, request):
        common = dict(symbol=request.symbol, side=SIDE_MAP[request.side], time_in_force=TIF_MAP[request.tif], client_order_id=request.client_order_id or str(uuid4()))
        if request.order_type == 'MARKET':
            return MarketOrderRequest(qty=Decimal(request.qty), **common)
        if request.order_type == 'LIMIT':
            return LimitOrderRequest(qty=Decimal(request.qty), limit_price=Decimal(request.limit_price), **common)
        if request.order_type == 'STOP':
            return StopOrderRequest(qty=Decimal(request.qty), stop_price=Decimal(request.stop_price), **common)
        if request.order_type == 'TRAILING_STOP':
            return TrailingStopOrderRequest(qty=Decimal(request.qty), trail_percent=Decimal(request.trail_percent), **common)
        raise ValueError(f'unsupported order type: {request.order_type}')
```

- [ ] **Step 4: Run the focused test**

```bash
cd sidecar_alpaca && uv run pytest tests/test_place_order.py -v
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "TradingClient|submit_order|MarketOrderRequest|LimitOrderRequest|StopOrderRequest|TrailingStopOrderRequest|external_order_id" sidecar_alpaca/handlers.py sidecar_alpaca/tests/test_place_order.py
```

- [ ] **Step 6: Commit**

Subject:

```text
feat(alpaca): enable live PlaceOrder path
```

Body:

```text
Wire Configure to alpaca-py TradingClient and submit market, limit, stop, and trailing stop orders with Decimal quantities and gRPC response mapping.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-S.2: sidecar_alpaca CancelOrder live path

Files:
- `sidecar_alpaca/handlers.py`
- `sidecar_alpaca/tests/test_cancel_order.py`

- [ ] **Step 1: Pre-flight grep**

```bash
rg -n "CancelOrder|cancel_order_by_id|ALREADY_FILLED|StatusCode" sidecar_alpaca proto
rg -n "PlaceOrderResponse|CancelOrderResponse" proto sidecar_alpaca
```

- [ ] **Step 2: Write tests**

```python
from types import SimpleNamespace

import grpc
import pytest

from sidecar_alpaca.handlers import AlpacaTradeServicer


class FakeTradingClient:
    def __init__(self, exc=None):
        self.exc = exc
        self.cancelled = []

    def cancel_order_by_id(self, order_id):
        self.cancelled.append(order_id)
        if self.exc:
            raise self.exc
        return SimpleNamespace(id=order_id, status='canceled')


@pytest.mark.asyncio
async def test_cancel_order_calls_alpaca_by_id():
    client = FakeTradingClient()
    svc = AlpacaTradeServicer(trading_client_factory=lambda *_: client)
    svc._trading_client = client
    resp = await svc.CancelOrder(SimpleNamespace(external_order_id='abc'), None)
    assert client.cancelled == ['abc']
    assert resp.external_order_id == 'abc'
    assert resp.status == 'CANCELED'


@pytest.mark.asyncio
async def test_cancel_order_422_already_filled_sets_grpc_status():
    class Context:
        code = None
        details = None
        def set_code(self, code):
            self.code = code
        def set_details(self, details):
            self.details = details
    exc = Exception('422 order already filled')
    client = FakeTradingClient(exc=exc)
    svc = AlpacaTradeServicer(trading_client_factory=lambda *_: client)
    svc._trading_client = client
    ctx = Context()
    resp = await svc.CancelOrder(SimpleNamespace(external_order_id='filled'), ctx)
    assert ctx.code == grpc.StatusCode.ALREADY_EXISTS
    assert ctx.details == 'ALREADY_FILLED'
    assert resp.status == 'ALREADY_FILLED'
```

- [ ] **Step 3: Apply the implementation**

```python
import grpc


def _is_already_filled(exc: Exception) -> bool:
    text = str(exc).lower()
    return '422' in text and ('filled' in text or 'too late to cancel' in text)


class AlpacaTradeServicer:
    async def CancelOrder(self, request, context):
        try:
            response = self._trading_client.cancel_order_by_id(request.external_order_id)
        except Exception as exc:
            if _is_already_filled(exc):
                if context is not None:
                    context.set_code(grpc.StatusCode.ALREADY_EXISTS)
                    context.set_details('ALREADY_FILLED')
                return CancelOrderResponse(external_order_id=request.external_order_id, status='ALREADY_FILLED')
            raise
        return CancelOrderResponse(
            external_order_id=str(getattr(response, 'id', request.external_order_id)),
            status='CANCELED',
        )
```

- [ ] **Step 4: Run the focused test**

```bash
cd sidecar_alpaca && uv run pytest tests/test_cancel_order.py -v
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "CancelOrder|cancel_order_by_id|ALREADY_FILLED|StatusCode.ALREADY" sidecar_alpaca/handlers.py sidecar_alpaca/tests/test_cancel_order.py
```

- [ ] **Step 6: Commit**

Subject:

```text
feat(alpaca): enable live CancelOrder path
```

Body:

```text
Cancel Alpaca orders by id, map successful cancellation to the gRPC response, and surface already-filled 422 responses as the explicit ALREADY_FILLED status.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-S.3: sidecar_alpaca ModifyOrder via replace_order_by_id

Files:
- `sidecar_alpaca/handlers.py`
- `sidecar_alpaca/tests/test_modify_order.py`

- [ ] **Step 1: Pre-flight grep**

```bash
rg -n "ModifyOrder|replace_order_by_id|OrderModify|limit_price|stop_price" sidecar_alpaca proto
rg -n "ModifyOrderResponse|external_order_id" sidecar_alpaca proto
```

- [ ] **Step 2: Write tests**

```python
from decimal import Decimal
from types import SimpleNamespace

import pytest

from sidecar_alpaca.handlers import AlpacaTradeServicer


class FakeTradingClient:
    def __init__(self):
        self.calls = []
    def replace_order_by_id(self, order_id, **kwargs):
        self.calls.append((order_id, kwargs))
        return SimpleNamespace(id='replacement-456')


@pytest.mark.asyncio
async def test_modify_order_replaces_delta_fields():
    client = FakeTradingClient()
    svc = AlpacaTradeServicer(trading_client_factory=lambda *_: client)
    svc._trading_client = client
    req = SimpleNamespace(external_order_id='old-123', qty='2.5', limit_price='151.25', stop_price='')
    resp = await svc.ModifyOrder(req, None)
    assert resp.external_order_id == 'replacement-456'
    assert client.calls == [('old-123', {'qty': Decimal('2.5'), 'limit_price': Decimal('151.25')})]


@pytest.mark.asyncio
async def test_modify_order_omits_empty_fields():
    client = FakeTradingClient()
    svc = AlpacaTradeServicer(trading_client_factory=lambda *_: client)
    svc._trading_client = client
    req = SimpleNamespace(external_order_id='old-123', qty='', limit_price='', stop_price='145.00')
    await svc.ModifyOrder(req, None)
    assert client.calls[0][1] == {'stop_price': Decimal('145.00')}
```

- [ ] **Step 3: Apply the implementation**

```python
from decimal import Decimal


def _decimal_or_none(value: str):
    if value is None or value == '':
        return None
    return Decimal(value)


class AlpacaTradeServicer:
    async def ModifyOrder(self, request, context):
        changes = {}
        qty = _decimal_or_none(getattr(request, 'qty', ''))
        limit_price = _decimal_or_none(getattr(request, 'limit_price', ''))
        stop_price = _decimal_or_none(getattr(request, 'stop_price', ''))
        if qty is not None:
            changes['qty'] = qty
        if limit_price is not None:
            changes['limit_price'] = limit_price
        if stop_price is not None:
            changes['stop_price'] = stop_price
        replacement = self._trading_client.replace_order_by_id(request.external_order_id, **changes)
        return ModifyOrderResponse(external_order_id=str(replacement.id))
```

- [ ] **Step 4: Run the focused test**

```bash
cd sidecar_alpaca && uv run pytest tests/test_modify_order.py -v
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "ModifyOrder|replace_order_by_id|ModifyOrderResponse|replacement" sidecar_alpaca/handlers.py sidecar_alpaca/tests/test_modify_order.py
```

- [ ] **Step 6: Commit**

Subject:

```text
feat(alpaca): replace orders for ModifyOrder
```

Body:

```text
Use Alpaca replace_order_by_id with qty, limit_price, and stop_price deltas and return the replacement id issued by Alpaca.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-S.4: sidecar_alpaca OrderEvent dual-subscription

Files:
- `sidecar_alpaca/handlers.py`
- `sidecar_alpaca/streaming.py`
- `sidecar_alpaca/tests/test_order_events.py`

- [ ] **Step 1: Pre-flight grep**

```bash
rg -n "StreamOrderEvents|OrderEvent|TradingStream|CryptoDataStream|asyncio.Queue|fan" sidecar_alpaca
rg -n "OrderEventResponse|order_events" proto sidecar_alpaca
```

- [ ] **Step 2: Write tests**

```python
import asyncio

import pytest

from sidecar_alpaca.streaming import fan_in_order_events


async def equity_source():
    yield {'asset_class': 'EQUITY', 'external_order_id': 'eq-1', 'status': 'filled'}


async def crypto_source():
    yield {'asset_class': 'CRYPTO', 'external_order_id': 'cr-1', 'status': 'canceled'}


@pytest.mark.asyncio
async def test_fan_in_yields_equity_and_crypto_events():
    events = []
    async for event in fan_in_order_events(equity_source, crypto_source):
        events.append(event)
        if len(events) == 2:
            break
    assert {event['asset_class'] for event in events} == {'EQUITY', 'CRYPTO'}


@pytest.mark.asyncio
async def test_fan_in_cancels_tasks_when_consumer_stops():
    cancelled = asyncio.Event()
    async def endless():
        try:
            while True:
                await asyncio.sleep(0.01)
                yield {'asset_class': 'EQUITY'}
        finally:
            cancelled.set()
    agen = fan_in_order_events(endless, crypto_source)
    await agen.__anext__()
    await agen.aclose()
    await asyncio.wait_for(cancelled.wait(), timeout=1)
```

- [ ] **Step 3: Apply the implementation**

```python
import asyncio
from collections.abc import AsyncIterator, Callable


async def _pump(source_factory: Callable[[], AsyncIterator[dict]], queue: asyncio.Queue):
    async for event in source_factory():
        await queue.put(event)


async def fan_in_order_events(equity_source_factory, crypto_source_factory):
    queue: asyncio.Queue[dict] = asyncio.Queue()
    tasks = [
        asyncio.create_task(_pump(equity_source_factory, queue)),
        asyncio.create_task(_pump(crypto_source_factory, queue)),
    ]
    try:
        while True:
            if all(task.done() for task in tasks) and queue.empty():
                return
            event = await queue.get()
            yield event
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class AlpacaTradeServicer:
    async def StreamOrderEvents(self, request, context):
        async for event in fan_in_order_events(self._equity_trade_updates, self._crypto_trade_updates):
            yield OrderEventResponse(
                external_order_id=event['external_order_id'],
                asset_class=event['asset_class'],
                status=event['status'].upper(),
            )
```

- [ ] **Step 4: Run the focused test**

```bash
cd sidecar_alpaca && uv run pytest tests/test_order_events.py -v
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "fan_in_order_events|TradingStream|CryptoDataStream|StreamOrderEvents|OrderEventResponse" sidecar_alpaca/handlers.py sidecar_alpaca/streaming.py sidecar_alpaca/tests/test_order_events.py
```

- [ ] **Step 6: Commit**

Subject:

```text
feat(alpaca): fan in equity and crypto order events
```

Body:

```text
Subscribe to equity and crypto trade update sources as independent tasks and expose a single gRPC order-event stream with cancellation-safe fan-in behavior.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-S.5: TradingStream cap equals 5 enforcement

Files:
- `sidecar_alpaca/handlers.py`
- `sidecar_alpaca/tests/test_stream_cap.py`

- [ ] **Step 1: Pre-flight grep**

```bash
rg -n "Semaphore|RESOURCE_EXHAUSTED|trading_stream_cap_5|StreamOrderEvents" sidecar_alpaca
rg -n "stream.*cap|concurrent" sidecar_alpaca/tests
```

- [ ] **Step 2: Write tests**

```python
import asyncio

import grpc
import pytest

from sidecar_alpaca.handlers import AlpacaTradeServicer


class Context:
    def __init__(self):
        self.code = None
        self.details = None
    def set_code(self, code):
        self.code = code
    def set_details(self, details):
        self.details = details


@pytest.mark.asyncio
async def test_sixth_stream_is_resource_exhausted():
    svc = AlpacaTradeServicer()
    acquired = []
    for _ in range(5):
        assert await svc._try_acquire_stream_slot(Context()) is True
        acquired.append(True)
    ctx = Context()
    assert await svc._try_acquire_stream_slot(ctx) is False
    assert ctx.code == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert ctx.details == 'trading_stream_cap_5'
    for _ in acquired:
        svc._release_stream_slot()


@pytest.mark.asyncio
async def test_stream_slot_released_after_generator_close():
    svc = AlpacaTradeServicer()
    ctx = Context()
    assert await svc._try_acquire_stream_slot(ctx) is True
    svc._release_stream_slot()
    assert await svc._try_acquire_stream_slot(Context()) is True
```

- [ ] **Step 3: Apply the implementation**

```python
import asyncio
import grpc


class AlpacaTradeServicer:
    def __init__(self, *args, **kwargs):
        self._stream_slots = asyncio.Semaphore(5)

    async def _try_acquire_stream_slot(self, context) -> bool:
        if self._stream_slots.locked() and self._stream_slots._value == 0:
            context.set_code(grpc.StatusCode.RESOURCE_EXHAUSTED)
            context.set_details('trading_stream_cap_5')
            return False
        await self._stream_slots.acquire()
        return True

    def _release_stream_slot(self) -> None:
        self._stream_slots.release()

    async def StreamOrderEvents(self, request, context):
        if not await self._try_acquire_stream_slot(context):
            return
        try:
            async for event in self._order_event_source(request):
                yield event
        finally:
            self._release_stream_slot()
```

- [ ] **Step 4: Run the focused test**

```bash
cd sidecar_alpaca && uv run pytest tests/test_stream_cap.py -v
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "Semaphore\(5\)|RESOURCE_EXHAUSTED|trading_stream_cap_5|_try_acquire_stream_slot" sidecar_alpaca/handlers.py sidecar_alpaca/tests/test_stream_cap.py
```

- [ ] **Step 6: Commit**

Subject:

```text
fix(alpaca): cap concurrent trading streams at five
```

Body:

```text
Track active StreamOrderEvents calls with a five-slot semaphore and return RESOURCE_EXHAUSTED with details trading_stream_cap_5 when the cap is reached.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-S.6: clientOrderId round-trip and in-memory dedupe

Files:
- `sidecar_alpaca/handlers.py`
- `sidecar_alpaca/dedup.py`
- `sidecar_alpaca/tests/test_dedup.py`

- [ ] **Step 1: Pre-flight grep**

```bash
rg -n "client_order_id|clientOrderId|PlaceOrderRequest|uuid4|dedupe|TTL" proto sidecar_alpaca
rg -n "string client_order_id|PlaceOrderRequest" proto
```

- [ ] **Step 2: Write tests**

```python
import time

from sidecar_alpaca.dedup import ClientOrderDedupe


def test_dedupe_returns_existing_order_inside_ttl():
    dedupe = ClientOrderDedupe(maxlen=2, ttl_seconds=60, clock=lambda: 100.0)
    dedupe.store('cid-1', 'order-1')
    assert dedupe.get('cid-1') == 'order-1'


def test_dedupe_expires_after_ttl():
    now = 100.0
    dedupe = ClientOrderDedupe(maxlen=2, ttl_seconds=60, clock=lambda: now)
    dedupe.store('cid-1', 'order-1')
    now = 161.0
    assert dedupe.get('cid-1') is None


def test_dedupe_evicts_lru_entry():
    dedupe = ClientOrderDedupe(maxlen=2, ttl_seconds=60, clock=lambda: 100.0)
    dedupe.store('a', 'order-a')
    dedupe.store('b', 'order-b')
    assert dedupe.get('a') == 'order-a'
    dedupe.store('c', 'order-c')
    assert dedupe.get('b') is None
    assert dedupe.get('a') == 'order-a'
    assert dedupe.get('c') == 'order-c'
```

- [ ] **Step 3: Apply the implementation**

```python
from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic
from uuid import uuid4


@dataclass
class DedupeEntry:
    external_order_id: str
    created_at: float


class ClientOrderDedupe:
    def __init__(self, maxlen: int = 10000, ttl_seconds: int = 60, clock=monotonic):
        self.maxlen = maxlen
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._items: OrderedDict[str, DedupeEntry] = OrderedDict()

    def get(self, client_order_id: str) -> str | None:
        entry = self._items.get(client_order_id)
        if entry is None:
            return None
        if self.clock() - entry.created_at > self.ttl_seconds:
            self._items.pop(client_order_id, None)
            return None
        self._items.move_to_end(client_order_id)
        return entry.external_order_id

    def store(self, client_order_id: str, external_order_id: str) -> None:
        self._items[client_order_id] = DedupeEntry(external_order_id, self.clock())
        self._items.move_to_end(client_order_id)
        while len(self._items) > self.maxlen:
            self._items.popitem(last=False)


class AlpacaTradeServicer:
    def __init__(self, *args, **kwargs):
        self._dedupe = ClientOrderDedupe()

    async def PlaceOrder(self, request, context):
        client_order_id = request.client_order_id or str(uuid4())
        existing = self._dedupe.get(client_order_id)
        if existing is not None:
            return PlaceOrderResponse(external_order_id=existing, client_order_id=client_order_id)
        order_data = self._build_order_request(request, client_order_id=client_order_id)
        order = self._trading_client.submit_order(order_data=order_data)
        self._dedupe.store(client_order_id, str(order.id))
        return PlaceOrderResponse(external_order_id=str(order.id), client_order_id=client_order_id)
```

- [ ] **Step 4: Run the focused test**

```bash
cd sidecar_alpaca && uv run pytest tests/test_dedup.py -v
pnpm -s proto:generate && pnpm -s proto:check
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "client_order_id|ClientOrderDedupe|maxlen=10000|ttl_seconds=60|uuid4" proto sidecar_alpaca/handlers.py sidecar_alpaca/dedup.py sidecar_alpaca/tests/test_dedup.py
```

- [ ] **Step 6: Commit**

Subject:

```text
feat(alpaca): add client order id idempotency
```

Body:

```text
Accept client_order_id on PlaceOrder, pass it through to Alpaca, generate one when absent, and keep a bounded 60-second in-memory dedupe map.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-S.7: Empirical script alpaca_equity_place_cancel_paper.py

Files:
- `scripts/empirical/alpaca_equity_place_cancel_paper.py`

- [ ] **Step 1: Pre-flight grep**

```bash
test -d scripts/empirical
rg -n "ALPACA_PAPER_API_KEY|TradingClient|submit_order|cancel_order_by_id" scripts sidecar_alpaca || true
rg -n "alpaca-py|alpaca.trading" pyproject.toml requirements*.txt backend/pyproject.toml
```

- [ ] **Step 2: Write tests**

```python
import os

from scripts.empirical.alpaca_equity_place_cancel_paper import run


class FakeClient:
    def __init__(self):
        self.cancelled = []
    def submit_order(self, order_data):
        assert order_data.symbol == 'AAPL'
        assert str(order_data.qty) == '1'
        assert str(order_data.limit_price).endswith('.00')
        return type('Order', (), {'id': 'paper-order-1'})()
    def cancel_order_by_id(self, order_id):
        self.cancelled.append(order_id)
        return type('Cancel', (), {'id': order_id})()


def test_run_returns_pass_with_fake_client(capsys):
    assert run(FakeClient()) == 0
    assert 'PASS' in capsys.readouterr().out
```

- [ ] **Step 3: Apply the implementation**

```python
#!/usr/bin/env python3
import os
import sys
from decimal import Decimal

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest


def build_client() -> TradingClient:
    key = os.environ['ALPACA_PAPER_API_KEY']
    secret = os.environ['ALPACA_PAPER_API_SECRET']
    return TradingClient(key, secret, paper=True)


def run(client: TradingClient | None = None) -> int:
    client = client or build_client()
    order = client.submit_order(order_data=LimitOrderRequest(
        symbol='AAPL',
        qty=Decimal('1'),
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
        limit_price=Decimal('1.00'),
    ))
    order_id = str(order.id)
    if not order_id:
        print('FAIL: missing order id')
        return 1
    client.cancel_order_by_id(order_id)
    print(f'PASS: placed and canceled AAPL paper order {order_id}')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(run())
    except Exception as exc:
        print(f'FAIL: {exc}')
        raise SystemExit(1)
```

- [ ] **Step 4: Run the focused test**

```bash
uv run python scripts/empirical/alpaca_equity_place_cancel_paper.py
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "AAPL|LimitOrderRequest|cancel_order_by_id|PASS|FAIL" scripts/empirical/alpaca_equity_place_cancel_paper.py
```

- [ ] **Step 6: Commit**

Subject:

```text
test(alpaca): add equity paper place cancel script
```

Body:

```text
Add a standalone Alpaca paper script that places a low-priced AAPL limit order, verifies an order id, cancels the order, and prints PASS or FAIL.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-S.8: Alembic 0020 flip Alpaca equity is_supported true

Files:
- `backend/alembic/versions/0020_alpaca_equity_supported.py`

- [ ] **Step 1: Pre-flight grep**

```bash
ls backend/alembic/versions | sort | tail -20
rg -n "alpaca.*EQUITY|asset_class.*EQUITY|trailing_stop|BRACKET" backend/alembic/versions app
```

- [ ] **Step 2: Write tests**

```python
from sqlalchemy import text


def test_0020_marks_alpaca_equity_trade_types_supported(db_session):
    rows = db_session.execute(text("""
        SELECT order_type, is_supported
        FROM order_capability
        WHERE broker_id = (SELECT id FROM brokers WHERE slug = 'alpaca')
          AND asset_class = 'EQUITY'
          AND order_type IN ('MARKET', 'LIMIT', 'STOP', 'TRAILING_STOP')
    """)).mappings().all()
    assert {row['order_type'] for row in rows} == {'MARKET', 'LIMIT', 'STOP', 'TRAILING_STOP'}
    assert all(row['is_supported'] for row in rows)


def test_0020_does_not_enable_equity_bracket(db_session):
    supported = db_session.execute(text("""
        SELECT is_supported FROM order_capability
        WHERE broker_id = (SELECT id FROM brokers WHERE slug = 'alpaca')
          AND asset_class = 'EQUITY' AND order_type = 'BRACKET'
    """)).scalar_one()
    assert supported is False
```

- [ ] **Step 3: Apply the implementation**

```python
from alembic import op

revision = '0020_alpaca_equity_supported'
down_revision = '0019_qty_10dp'
branch_labels = None
depends_on = None


SUPPORTED_TYPES = ('MARKET', 'LIMIT', 'STOP', 'TRAILING_STOP')


def upgrade() -> None:
    op.execute("""
        UPDATE order_capability
        SET is_supported = TRUE, notes = 'Phase 8c Alpaca equity live write path enabled'
        WHERE broker_id = (SELECT id FROM brokers WHERE slug = 'alpaca')
          AND asset_class = 'EQUITY'
          AND order_type IN ('MARKET', 'LIMIT', 'STOP', 'TRAILING_STOP')
    """)
    op.execute("""
        UPDATE order_capability
        SET is_supported = FALSE, notes = 'Phase 8c bracket flip deferred to 0021-eq'
        WHERE broker_id = (SELECT id FROM brokers WHERE slug = 'alpaca')
          AND asset_class = 'EQUITY'
          AND order_type = 'BRACKET'
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE order_capability
        SET is_supported = FALSE, notes = 'Reverted Phase 8c Alpaca equity support'
        WHERE broker_id = (SELECT id FROM brokers WHERE slug = 'alpaca')
          AND asset_class = 'EQUITY'
          AND order_type IN ('MARKET', 'LIMIT', 'STOP', 'TRAILING_STOP')
    """)
```

- [ ] **Step 4: Run the focused test**

```bash
cd backend && uv run pytest tests/alembic/test_0020_alpaca_equity_supported.py -v
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "0020_alpaca_equity_supported|MARKET|LIMIT|STOP|TRAILING_STOP|BRACKET" backend/alembic/versions/0020_alpaca_equity_supported.py backend/tests
```

- [ ] **Step 6: Commit**

Subject:

```text
feat(capabilities): enable Alpaca equity order types
```

Body:

```text
Flip Alpaca equity market, limit, stop, and trailing stop capability rows to supported while leaving bracket disabled until the bracket-specific migration.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-S.9: Nightly real-broker workflow and E2E test

Files:
- `.github/workflows/nightly-real-alpaca-equity.yml`
- `backend/tests/real_broker/test_real_alpaca_equity_e2e.py`

- [ ] **Step 1: Pre-flight grep**

```bash
ls .github/workflows
rg -n "real_alpaca|ALPACA_PAPER_API_KEY|nightly|real_broker" .github backend/tests
find backend/tests/real_broker -maxdepth 1 -type f -print
```

- [ ] **Step 2: Write tests**

```python
import os

import pytest

pytestmark = pytest.mark.real_broker


@pytest.mark.parametrize('order_type', ['market', 'limit', 'trailing_stop'])
async def test_real_alpaca_equity_place_cancel_and_event(alpaca_client, order_type):
    if not os.getenv('ALPACA_PAPER_API_KEY') or not os.getenv('ALPACA_PAPER_API_SECRET'):
        pytest.skip('Alpaca paper credentials are not configured')
    order = await alpaca_client.place_order(
        symbol='AAPL',
        asset_class='EQUITY',
        side='BUY',
        order_type=order_type,
        qty='1',
        tif='DAY',
        limit_price='1.00' if order_type == 'limit' else None,
        trail_percent='1.0' if order_type == 'trailing_stop' else None,
    )
    assert order.external_order_id
    try:
        event = await alpaca_client.wait_for_order_event(order.external_order_id, timeout=30)
        assert event.external_order_id == order.external_order_id
    finally:
        cancel = await alpaca_client.cancel_order(order.external_order_id)
        assert cancel.status in {'CANCELED', 'ALREADY_FILLED'}
```

- [ ] **Step 3: Apply the implementation**

```yaml
name: nightly-real-alpaca-equity

on:
  schedule:
    - cron: '0 6 * * 1-5'
  workflow_dispatch:

jobs:
  real-alpaca-equity:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    env:
      ALPACA_PAPER_API_KEY: ${{ secrets.ALPACA_PAPER_API_KEY }}
      ALPACA_PAPER_API_SECRET: ${{ secrets.ALPACA_PAPER_API_SECRET }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: Install uv
        uses: astral-sh/setup-uv@v5
      - name: Run Alpaca equity E2E
        run: cd backend && uv run pytest tests/real_broker/test_real_alpaca_equity_e2e.py -v -m real_broker
```

- [ ] **Step 4: Run the focused test**

```bash
cd backend && uv run pytest tests/real_broker/test_real_alpaca_equity_e2e.py -v -m real_broker
actionlint .github/workflows/nightly-real-alpaca-equity.yml
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "nightly-real-alpaca-equity|0 6|ALPACA_PAPER_API_KEY|market|limit|trailing_stop|OrderEvent" .github/workflows/nightly-real-alpaca-equity.yml backend/tests/real_broker/test_real_alpaca_equity_e2e.py
```

- [ ] **Step 6: Commit**

Subject:

```text
test(alpaca): add nightly equity real broker coverage
```

Body:

```text
Add a weekday Alpaca paper workflow and real-broker E2E coverage for market, limit, and trailing stop equity orders with order event assertions.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-C.1: sidecar_alpaca crypto stream subscription

Files:
- `sidecar_alpaca/streaming.py`
- `sidecar_alpaca/tests/test_crypto_stream.py`

- [ ] **Step 1: Pre-flight grep**

```bash
rg -n "CryptoDataStream|trade_updates|crypto_feed|fan_in_order_events|TradingStream" sidecar_alpaca
rg -n "CryptoOrderEventResponse|OrderEventResponse" proto sidecar_alpaca
```

- [ ] **Step 2: Write tests**

```python
import pytest

from sidecar_alpaca.streaming import crypto_order_event_source


class FakeCryptoStream:
    def __init__(self):
        self.handler = None
        self.subscribed = False
    def subscribe_trade_updates(self, handler):
        self.handler = handler
        self.subscribed = True
    async def run(self):
        await self.handler({'order': {'id': 'cr-1', 'symbol': 'BTCUSD'}, 'event': 'fill'})


@pytest.mark.asyncio
async def test_crypto_stream_subscribes_trade_updates():
    stream = FakeCryptoStream()
    events = []
    async for event in crypto_order_event_source(lambda: stream):
        events.append(event)
        break
    assert stream.subscribed is True
    assert events[0]['external_order_id'] == 'cr-1'
    assert events[0]['symbol'] == 'BTC/USD'
```

- [ ] **Step 3: Apply the implementation**

```python
import asyncio

from alpaca.data.live.crypto import CryptoDataStream

from sidecar_alpaca.symbol_util import canonical_crypto_symbol


def _map_crypto_trade_update(payload: dict) -> dict:
    order = payload.get('order', {})
    return {
        'asset_class': 'CRYPTO',
        'external_order_id': str(order.get('id', '')),
        'symbol': canonical_crypto_symbol(str(order.get('symbol', ''))),
        'status': str(payload.get('event', '')).upper(),
    }


async def crypto_order_event_source(stream_factory):
    queue: asyncio.Queue[dict] = asyncio.Queue()
    async def on_trade_update(payload):
        await queue.put(_map_crypto_trade_update(payload))
    stream = stream_factory()
    stream.subscribe_trade_updates(on_trade_update)
    task = asyncio.create_task(stream.run())
    try:
        while True:
            yield await queue.get()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def build_crypto_stream(api_key: str, api_secret: str, crypto_feed: str = 'us'):
    return CryptoDataStream(api_key, api_secret, feed=crypto_feed)
```

- [ ] **Step 4: Run the focused test**

```bash
cd sidecar_alpaca && uv run pytest tests/test_crypto_stream.py -v
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "CryptoDataStream|subscribe_trade_updates|canonical_crypto_symbol|CRYPTO|crypto_order_event_source" sidecar_alpaca/streaming.py sidecar_alpaca/tests/test_crypto_stream.py
```

- [ ] **Step 6: Commit**

Subject:

```text
feat(alpaca): subscribe to crypto order updates
```

Body:

```text
Add a crypto trade update stream source using CryptoDataStream and keep it independent from the equity TradingStream path for fan-in.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-C.2: cash_amount field plumbing for crypto notional ordering

Files:
- `proto/alpaca_sidecar.proto`
- `sidecar_alpaca/handlers.py`
- `sidecar_alpaca/tests/test_crypto_notional.py`

- [ ] **Step 1: Pre-flight grep**

```bash
rg -n "PlaceOrderRequest|cash_amount|notional|qty" proto sidecar_alpaca
rg -n "asset_class.*CRYPTO|OrderSide.BUY|TimeInForce.DAY" sidecar_alpaca
```

- [ ] **Step 2: Write tests**

```python
from decimal import Decimal
from types import SimpleNamespace

import pytest

from sidecar_alpaca.handlers import AlpacaTradeServicer


class FakeClient:
    def __init__(self):
        self.order_data = None
    def submit_order(self, order_data):
        self.order_data = order_data
        return SimpleNamespace(id='crypto-notional-1')


@pytest.mark.asyncio
async def test_crypto_cash_amount_maps_to_notional_not_qty():
    client = FakeClient()
    svc = AlpacaTradeServicer(trading_client_factory=lambda *_: client)
    svc._trading_client = client
    req = SimpleNamespace(symbol='BTC/USD', asset_class='CRYPTO', side='BUY', order_type='MARKET', qty='', cash_amount='10.00', tif='DAY', client_order_id='cid')
    resp = await svc.PlaceOrder(req, None)
    assert resp.external_order_id == 'crypto-notional-1'
    assert client.order_data.notional == Decimal('10.00')
    assert getattr(client.order_data, 'qty', None) is None
```

- [ ] **Step 3: Apply the implementation**

```proto
message PlaceOrderRequest {
  string symbol = 1;
  string side = 2;
  string order_type = 3;
  string qty = 4;
  string tif = 5;
  string limit_price = 6;
  string stop_price = 7;
  string trail_percent = 8;
  string asset_class = 14;
  // Request-side USD notional. XOR with qty. Crypto uses Alpaca notional=.
  string cash_amount = 15;
}
```
```python
from decimal import Decimal


def _qty_or_notional_kwargs(request) -> dict:
    if request.cash_amount and request.asset_class == 'CRYPTO':
        return {'notional': Decimal(request.cash_amount)}
    if request.cash_amount:
        return {'notional': Decimal(request.cash_amount)}
    return {'qty': Decimal(request.qty)}


def _build_market_order_request(request, common):
    return MarketOrderRequest(**_qty_or_notional_kwargs(request), **common)
```

- [ ] **Step 4: Run the focused test**

```bash
pnpm -s proto:generate
cd sidecar_alpaca && uv run pytest tests/test_crypto_notional.py -v
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "cash_amount = 15|notional|_qty_or_notional_kwargs|test_crypto_cash_amount" proto/alpaca_sidecar.proto sidecar_alpaca/handlers.py sidecar_alpaca/tests/test_crypto_notional.py
```

- [ ] **Step 6: Commit**

Subject:

```text
feat(alpaca): support crypto cash amount orders
```

Body:

```text
Add cash_amount to the sidecar proto and map crypto market buys to Alpaca notional orders instead of qty orders.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-C.3: Symbol normalization on ingress

Files:
- `sidecar_alpaca/symbol_util.py`
- `sidecar_alpaca/handlers.py`
- `sidecar_alpaca/tests/test_symbol_util.py`

- [ ] **Step 1: Pre-flight grep**

```bash
rg -n "canonical_crypto_symbol|BTCUSD|BTC/USD|symbol" sidecar_alpaca app backend/tests
rg -n "PlaceOrder|CancelOrder|ModifyOrder" sidecar_alpaca/handlers.py
```

- [ ] **Step 2: Write tests**

```python
import pytest

from sidecar_alpaca.symbol_util import canonical_crypto_symbol


@pytest.mark.parametrize('raw, expected', [
    ('BTCUSD', 'BTC/USD'),
    ('BTC/USD', 'BTC/USD'),
    ('BTCUSDT', 'BTC/USDT'),
    ('ETHUSD', 'ETH/USD'),
    ('SHIBUSD', 'SHIB/USD'),
])
def test_canonical_crypto_symbol(raw, expected):
    assert canonical_crypto_symbol(raw) == expected


def test_canonical_crypto_symbol_rejects_empty():
    with pytest.raises(ValueError):
        canonical_crypto_symbol('')
```

- [ ] **Step 3: Apply the implementation**

```python
QUOTE_CURRENCIES = ('USDT', 'USDC', 'USD', 'EUR', 'GBP', 'BTC', 'ETH')


def canonical_crypto_symbol(symbol: str) -> str:
    raw = symbol.strip().upper()
    if not raw:
        raise ValueError('symbol is required')
    if '/' in raw:
        base, quote = raw.split('/', 1)
        if not base or not quote:
            raise ValueError(f'invalid crypto symbol: {symbol}')
        return f'{base}/{quote}'
    for quote in QUOTE_CURRENCIES:
        if raw.endswith(quote) and len(raw) > len(quote):
            return f'{raw[:-len(quote)]}/{quote}'
    raise ValueError(f'unsupported crypto quote currency: {symbol}')


def normalize_order_symbol(request):
    if getattr(request, 'asset_class', '') == 'CRYPTO':
        request.symbol = canonical_crypto_symbol(request.symbol)
    return request


# handlers.py applies normalize_order_symbol(request) at the start of PlaceOrder, CancelOrder, and ModifyOrder.
```

- [ ] **Step 4: Run the focused test**

```bash
cd sidecar_alpaca && uv run pytest tests/test_symbol_util.py -v
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "canonical_crypto_symbol|QUOTE_CURRENCIES|normalize_order_symbol|BTCUSDT|BTC/USD" sidecar_alpaca/symbol_util.py sidecar_alpaca/handlers.py sidecar_alpaca/tests/test_symbol_util.py
```

- [ ] **Step 6: Commit**

Subject:

```text
feat(alpaca): normalize crypto symbols on ingress
```

Body:

```text
Add canonical crypto symbol normalization and apply it before PlaceOrder, CancelOrder, and ModifyOrder calls into Alpaca.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-C.4: app_config crypto_location setting

Files:
- `app/core/config_schema.py`
- `sidecar_alpaca/handlers.py`
- `sidecar_alpaca/tests/test_crypto_location.py`

- [ ] **Step 1: Pre-flight grep**

```bash
rg -n "app_config|crypto_location|broker.alpaca|Configure|crypto_feed" app sidecar_alpaca backend/tests
rg -n "ConfigSchema|config_schema" app/core
```

- [ ] **Step 2: Write tests**

```python
from types import SimpleNamespace

import pytest

from sidecar_alpaca.handlers import AlpacaTradeServicer


def test_config_schema_has_default_crypto_location():
    from app.core.config_schema import DEFAULT_APP_CONFIG
    assert DEFAULT_APP_CONFIG['broker']['alpaca']['crypto_location'] == 'us'


@pytest.mark.asyncio
async def test_configure_passes_crypto_location_to_stream_factory():
    seen = {}
    def stream_factory(api_key, api_secret, crypto_feed):
        seen['crypto_feed'] = crypto_feed
        return object()
    svc = AlpacaTradeServicer(crypto_stream_factory=stream_factory)
    await svc.Configure(SimpleNamespace(api_key='k', api_secret='s', mode='paper', crypto_location='us'), None)
    assert seen['crypto_feed'] == 'us'
```

- [ ] **Step 3: Apply the implementation**

```python
# app/core/config_schema.py
DEFAULT_APP_CONFIG = {
    'broker': {
        'alpaca': {
            # Phase 8c: account-level crypto routing is deferred to Phase 16 per HIGH-6.
            'crypto_location': 'us',
        },
    },
}


# sidecar_alpaca/handlers.py
class AlpacaTradeServicer:
    def __init__(self, trading_client_factory=None, crypto_stream_factory=None):
        self._crypto_stream_factory = crypto_stream_factory
        self._crypto_stream = None

    async def Configure(self, request, context):
        crypto_location = getattr(request, 'crypto_location', '') or 'us'
        if self._crypto_stream_factory is not None:
            self._crypto_stream = self._crypto_stream_factory(
                request.api_key,
                request.api_secret,
                crypto_feed=crypto_location,
            )
        return ConfigureResponse(ok=True)
```

- [ ] **Step 4: Run the focused test**

```bash
cd backend && uv run pytest tests/test_config_schema.py -v
cd sidecar_alpaca && uv run pytest tests/test_crypto_location.py -v
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "crypto_location|crypto_feed='us'|Phase 16|HIGH-6" app/core/config_schema.py sidecar_alpaca/handlers.py sidecar_alpaca/tests/test_crypto_location.py
```

- [ ] **Step 6: Commit**

Subject:

```text
feat(alpaca): configure crypto location feed
```

Body:

```text
Add broker.alpaca.crypto_location with default us and pass it through Configure to the crypto data stream while documenting per-account routing deferral.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-C.5: Empirical script alpaca_crypto_place_cancel_paper.py

Files:
- `scripts/empirical/alpaca_crypto_place_cancel_paper.py`

- [ ] **Step 1: Pre-flight grep**

```bash
test -d scripts/empirical
rg -n "BTCUSD|BTC/USD|cash_amount|notional|Crypto" scripts sidecar_alpaca || true
rg -n "ALPACA_PAPER_API_KEY|ALPACA_PAPER_API_SECRET" scripts .github
```

- [ ] **Step 2: Write tests**

```python
from scripts.empirical.alpaca_crypto_place_cancel_paper import normalize_input_symbol, run


class FakeClient:
    def __init__(self):
        self.cancelled = []
    def submit_order(self, order_data):
        assert order_data.symbol == 'BTC/USD'
        assert str(order_data.notional) == '1.00'
        return type('Order', (), {'id': 'crypto-paper-1'})()
    def cancel_order_by_id(self, order_id):
        self.cancelled.append(order_id)


def test_normalize_input_symbol():
    assert normalize_input_symbol('BTCUSD') == 'BTC/USD'


def test_crypto_script_passes_with_fake_client(capsys):
    assert run(FakeClient(), cash_amount='1.00') == 0
    assert 'PASS' in capsys.readouterr().out
```

- [ ] **Step 3: Apply the implementation**

```python
#!/usr/bin/env python3
import os
from decimal import Decimal

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest


def normalize_input_symbol(symbol: str) -> str:
    if symbol == 'BTCUSD':
        return 'BTC/USD'
    return symbol


def build_client() -> TradingClient:
    return TradingClient(os.environ['ALPACA_PAPER_API_KEY'], os.environ['ALPACA_PAPER_API_SECRET'], paper=True)


def run(client=None, cash_amount: str = '1.00') -> int:
    client = client or build_client()
    order = client.submit_order(order_data=MarketOrderRequest(
        symbol=normalize_input_symbol('BTCUSD'),
        notional=Decimal(cash_amount),
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    ))
    order_id = str(order.id)
    if not order_id:
        print('FAIL: missing order id')
        return 1
    client.cancel_order_by_id(order_id)
    print(f'PASS: placed and canceled BTC/USD paper notional order {order_id}')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(run())
    except Exception as exc:
        print(f'FAIL: {exc}')
        raise SystemExit(1)
```

- [ ] **Step 4: Run the focused test**

```bash
uv run python scripts/empirical/alpaca_crypto_place_cancel_paper.py
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "BTCUSD|BTC/USD|notional|MarketOrderRequest|PASS|FAIL" scripts/empirical/alpaca_crypto_place_cancel_paper.py
```

- [ ] **Step 6: Commit**

Subject:

```text
test(alpaca): add crypto paper place cancel script
```

Body:

```text
Add a standalone Alpaca paper script that validates BTCUSD normalization and the cash_amount to notional path by placing and canceling a BTC/USD order.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-C.6: Alembic 0020a flip Alpaca crypto is_supported

Files:
- `backend/alembic/versions/0020a_alpaca_crypto_supported.py`

- [ ] **Step 1: Pre-flight grep**

```bash
ls backend/alembic/versions | sort | tail -20
rg -n "CRYPTO|MED-4|alpaca.*crypto|is_supported" backend/alembic/versions docs/superpowers/specs/2026-05-06-phase8c-alpaca-trade-design.md
```

- [ ] **Step 2: Write tests**

```python
from sqlalchemy import text


def test_0020a_marks_alpaca_crypto_supported_after_empirical_pass(db_session):
    rows = db_session.execute(text("""
        SELECT order_type, tif, is_supported
        FROM order_capability
        WHERE broker_id = (SELECT id FROM brokers WHERE slug = 'alpaca')
          AND asset_class = 'CRYPTO'
          AND order_type IN ('MARKET', 'LIMIT')
    """)).mappings().all()
    assert rows
    assert all(row['is_supported'] for row in rows)


def test_0020a_keeps_crypto_bracket_disabled(db_session):
    supported = db_session.execute(text("""
        SELECT is_supported FROM order_capability
        WHERE broker_id = (SELECT id FROM brokers WHERE slug = 'alpaca')
          AND asset_class = 'CRYPTO'
          AND order_type = 'BRACKET'
    """)).scalar_one()
    assert supported is False
```

- [ ] **Step 3: Apply the implementation**

```python
from alembic import op

revision = '0020a_alpaca_crypto_supported'
down_revision = '0020_alpaca_equity_supported'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PASS branch: scripts/empirical/alpaca_crypto_place_cancel_paper.py returned PASS.
    # FAIL branch, if MED-4 fails in a real run: leave these rows FALSE and set
    # notes = 'MED-4 empirical gate failed; Alpaca crypto trade disabled'.
    op.execute("""
        UPDATE order_capability
        SET is_supported = TRUE, notes = 'Phase 8c Alpaca crypto empirical PASS'
        WHERE broker_id = (SELECT id FROM brokers WHERE slug = 'alpaca')
          AND asset_class = 'CRYPTO'
          AND order_type IN ('MARKET', 'LIMIT')
    """)
    op.execute("""
        UPDATE order_capability
        SET is_supported = FALSE, notes = 'Crypto bracket is gated by 0021-cr negative capability'
        WHERE broker_id = (SELECT id FROM brokers WHERE slug = 'alpaca')
          AND asset_class = 'CRYPTO'
          AND order_type = 'BRACKET'
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE order_capability
        SET is_supported = FALSE, notes = 'Reverted Phase 8c Alpaca crypto support'
        WHERE broker_id = (SELECT id FROM brokers WHERE slug = 'alpaca')
          AND asset_class = 'CRYPTO'
          AND order_type IN ('MARKET', 'LIMIT')
    """)
```

- [ ] **Step 4: Run the focused test**

```bash
cd backend && uv run pytest tests/alembic/test_0020a_alpaca_crypto_supported.py -v
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "0020a_alpaca_crypto_supported|MED-4|CRYPTO|MARKET|LIMIT|BRACKET" backend/alembic/versions/0020a_alpaca_crypto_supported.py backend/tests
```

- [ ] **Step 6: Commit**

Subject:

```text
feat(capabilities): enable Alpaca crypto market and limit
```

Body:

```text
Add the PASS-branch migration for Alpaca crypto support and keep bracket disabled with a documented MED-4 fallback comment.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-C.7: Crypto E2E test and workflow

Files:
- `.github/workflows/nightly-real-alpaca-crypto.yml`
- `backend/tests/real_broker/test_real_alpaca_crypto_e2e.py`

- [ ] **Step 1: Pre-flight grep**

```bash
ls .github/workflows
rg -n "nightly-real-alpaca|real_alpaca_crypto|BTC/USD|ALPACA_PAPER" .github backend/tests
```

- [ ] **Step 2: Write tests**

```python
import os

import pytest

pytestmark = pytest.mark.real_broker


async def test_real_alpaca_crypto_notional_market_order(alpaca_client):
    if not os.getenv('ALPACA_PAPER_API_KEY') or not os.getenv('ALPACA_PAPER_API_SECRET'):
        pytest.skip('Alpaca paper credentials are not configured')
    order = await alpaca_client.place_order(
        symbol='BTC/USD',
        asset_class='CRYPTO',
        side='BUY',
        order_type='market',
        cash_amount='1.00',
        tif='DAY',
    )
    assert order.external_order_id
    try:
        event = await alpaca_client.wait_for_order_event(order.external_order_id, timeout=45)
        assert event.asset_class == 'CRYPTO'
        assert event.external_order_id == order.external_order_id
    finally:
        cancel = await alpaca_client.cancel_order(order.external_order_id)
        assert cancel.status in {'CANCELED', 'ALREADY_FILLED'}
```

- [ ] **Step 3: Apply the implementation**

```yaml
name: nightly-real-alpaca-crypto

on:
  schedule:
    - cron: '30 6 * * 1-5'
  workflow_dispatch:

jobs:
  real-alpaca-crypto:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    env:
      ALPACA_PAPER_API_KEY: ${{ secrets.ALPACA_PAPER_API_KEY }}
      ALPACA_PAPER_API_SECRET: ${{ secrets.ALPACA_PAPER_API_SECRET }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - uses: astral-sh/setup-uv@v5
      - name: Run Alpaca crypto E2E
        run: cd backend && uv run pytest tests/real_broker/test_real_alpaca_crypto_e2e.py -v -m real_broker
```

- [ ] **Step 4: Run the focused test**

```bash
cd backend && uv run pytest tests/real_broker/test_real_alpaca_crypto_e2e.py -v -m real_broker
actionlint .github/workflows/nightly-real-alpaca-crypto.yml
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "nightly-real-alpaca-crypto|30 6|BTC/USD|cash_amount|CRYPTO|ALPACA_PAPER_API_SECRET" .github/workflows/nightly-real-alpaca-crypto.yml backend/tests/real_broker/test_real_alpaca_crypto_e2e.py
```

- [ ] **Step 6: Commit**

Subject:

```text
test(alpaca): add nightly crypto real broker coverage
```

Body:

```text
Add weekday Alpaca paper coverage for BTC/USD cash_amount market orders and assert the crypto order event path before canceling or accepting fill completion.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-B-eq.1: sidecar_alpaca PlaceBracket equity

Files:
- `sidecar_alpaca/handlers.py`
- `sidecar_alpaca/tests/test_bracket_equity.py`

- [ ] **Step 1: Pre-flight grep**

```bash
rg -n "PlaceBracket|BracketOrder|order_class|take_profit|stop_loss" sidecar_alpaca proto backend/tests
rg -n "MarketOrderRequest|LimitOrderRequest" sidecar_alpaca/handlers.py
```

- [ ] **Step 2: Write tests**

```python
from decimal import Decimal
from types import SimpleNamespace

import pytest

from sidecar_alpaca.handlers import AlpacaTradeServicer


class FakeClient:
    def __init__(self):
        self.order_data = None
    def submit_order(self, order_data):
        self.order_data = order_data
        return SimpleNamespace(id='bracket-parent', legs=[SimpleNamespace(id='tp'), SimpleNamespace(id='sl')])


@pytest.mark.asyncio
async def test_place_bracket_equity_builds_bracket_order():
    client = FakeClient()
    svc = AlpacaTradeServicer(trading_client_factory=lambda *_: client)
    svc._trading_client = client
    req = SimpleNamespace(symbol='AAPL', side='BUY', order_type='MARKET', qty='1', tif='DAY', take_profit_limit_price='153.00', stop_loss_stop_price='148.00', stop_loss_limit_price='147.50', asset_class='EQUITY')
    resp = await svc.PlaceBracketOrder(req, None)
    assert resp.external_order_id == 'bracket-parent'
    assert resp.leg_order_ids == ['tp', 'sl']
    assert client.order_data.order_class == 'bracket'
```

- [ ] **Step 3: Apply the implementation**

```python
from decimal import Decimal

from alpaca.trading.enums import OrderClass
from alpaca.trading.requests import MarketOrderRequest, TakeProfitRequest, StopLossRequest


class AlpacaTradeServicer:
    async def PlaceBracketOrder(self, request, context):
        if request.asset_class != 'EQUITY':
            raise ValueError('alpaca bracket currently supports EQUITY only')
        order_data = MarketOrderRequest(
            symbol=request.symbol,
            qty=Decimal(request.qty),
            side=SIDE_MAP[request.side],
            time_in_force=TIF_MAP[request.tif],
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=Decimal(request.take_profit_limit_price)),
            stop_loss=StopLossRequest(
                stop_price=Decimal(request.stop_loss_stop_price),
                limit_price=Decimal(request.stop_loss_limit_price) if request.stop_loss_limit_price else None,
            ),
        )
        order = self._trading_client.submit_order(order_data=order_data)
        return BracketOrderResponse(
            external_order_id=str(order.id),
            leg_order_ids=[str(leg.id) for leg in getattr(order, 'legs', [])],
        )
```

- [ ] **Step 4: Run the focused test**

```bash
cd sidecar_alpaca && uv run pytest tests/test_bracket_equity.py -v
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "PlaceBracketOrder|OrderClass.BRACKET|TakeProfitRequest|StopLossRequest|leg_order_ids" sidecar_alpaca/handlers.py sidecar_alpaca/tests/test_bracket_equity.py
```

- [ ] **Step 6: Commit**

Subject:

```text
feat(alpaca): place native equity bracket orders
```

Body:

```text
Map PlaceBracketOrder to Alpaca order_class bracket requests with take-profit and stop-loss legs and return parent plus child order ids.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-B-eq.2: Empirical micro-script for equity bracket

Files:
- `scripts/empirical/alpaca_equity_bracket_paper.py`

- [ ] **Step 1: Pre-flight grep**

```bash
test -d scripts/empirical
rg -n "bracket|take_profit|stop_loss|AAPL" scripts sidecar_alpaca || true
```

- [ ] **Step 2: Write tests**

```python
from scripts.empirical.alpaca_equity_bracket_paper import expected_prices, run


class FakeClient:
    def submit_order(self, order_data):
        legs = [type('Leg', (), {'id': 'child-1'})(), type('Leg', (), {'id': 'child-2'})()]
        return type('Order', (), {'id': 'parent', 'legs': legs})()
    def cancel_order_by_id(self, order_id):
        return None


def test_expected_prices():
    take_profit, stop_loss = expected_prices(100)
    assert take_profit == '102.00'
    assert stop_loss == '99.00'


def test_bracket_script_passes_with_three_orders(capsys):
    assert run(FakeClient(), reference_price=100) == 0
    assert 'PASS' in capsys.readouterr().out
```

- [ ] **Step 3: Apply the implementation**

```python
#!/usr/bin/env python3
import os
from decimal import Decimal, ROUND_HALF_UP

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest


def expected_prices(reference_price: int | float | Decimal):
    base = Decimal(str(reference_price))
    take_profit = (base * Decimal('1.02')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    stop_loss = (base * Decimal('0.99')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return f'{take_profit:.2f}', f'{stop_loss:.2f}'


def build_client():
    return TradingClient(os.environ['ALPACA_PAPER_API_KEY'], os.environ['ALPACA_PAPER_API_SECRET'], paper=True)


def run(client=None, reference_price=100) -> int:
    client = client or build_client()
    tp, sl = expected_prices(reference_price)
    order = client.submit_order(order_data=MarketOrderRequest(
        symbol='AAPL', qty=Decimal('1'), side=OrderSide.BUY, time_in_force=TimeInForce.DAY,
        order_class=OrderClass.BRACKET,
        take_profit=TakeProfitRequest(limit_price=Decimal(tp)),
        stop_loss=StopLossRequest(stop_price=Decimal(sl)),
    ))
    ids = [str(order.id)] + [str(leg.id) for leg in getattr(order, 'legs', [])]
    if len(ids) != 3:
        print(f'FAIL: expected 3 orders, got {len(ids)}')
        return 1
    for order_id in ids:
        client.cancel_order_by_id(order_id)
    print(f'PASS: bracket parent and children canceled: {ids}')
    return 0


if __name__ == '__main__':
    raise SystemExit(run())
```

- [ ] **Step 4: Run the focused test**

```bash
uv run python scripts/empirical/alpaca_equity_bracket_paper.py
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "OrderClass.BRACKET|expected_prices|take_profit|stop_loss|PASS|FAIL" scripts/empirical/alpaca_equity_bracket_paper.py
```

- [ ] **Step 6: Commit**

Subject:

```text
test(alpaca): add equity bracket paper script
```

Body:

```text
Add a standalone script that places an AAPL bracket order in paper, verifies parent plus two children, cancels all orders, and prints PASS or FAIL.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-B-eq.3: Alembic 0021-eq flip equity bracket true

Files:
- `backend/alembic/versions/0021_eq_alpaca_equity_bracket.py`

- [ ] **Step 1: Pre-flight grep**

```bash
ls backend/alembic/versions | sort | tail -20
rg -n "BRACKET|alpaca.*EQUITY|0021" backend/alembic/versions backend/tests
```

- [ ] **Step 2: Write tests**

```python
from sqlalchemy import text


def test_0021_eq_enables_alpaca_equity_bracket(db_session):
    supported = db_session.execute(text("""
        SELECT is_supported FROM order_capability
        WHERE broker_id = (SELECT id FROM brokers WHERE slug='alpaca')
          AND asset_class='EQUITY' AND order_type='BRACKET'
    """)).scalar_one()
    assert supported is True
```

- [ ] **Step 3: Apply the implementation**

```python
from alembic import op

revision = '0021_eq_alpaca_equity_bracket'
down_revision = '0020a_alpaca_crypto_supported'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE order_capability
        SET is_supported = TRUE, notes = 'Phase 8c equity bracket empirical PASS'
        WHERE broker_id = (SELECT id FROM brokers WHERE slug='alpaca')
          AND asset_class = 'EQUITY'
          AND order_type = 'BRACKET'
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE order_capability
        SET is_supported = FALSE, notes = 'Reverted Phase 8c equity bracket support'
        WHERE broker_id = (SELECT id FROM brokers WHERE slug='alpaca')
          AND asset_class = 'EQUITY'
          AND order_type = 'BRACKET'
    """)
```

- [ ] **Step 4: Run the focused test**

```bash
cd backend && uv run pytest tests/alembic/test_0021_eq_alpaca_equity_bracket.py -v
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "0021_eq_alpaca_equity_bracket|BRACKET|asset_class = 'EQUITY'|empirical PASS" backend/alembic/versions/0021_eq_alpaca_equity_bracket.py backend/tests
```

- [ ] **Step 6: Commit**

Subject:

```text
feat(capabilities): enable Alpaca equity bracket orders
```

Body:

```text
Flip the Alpaca equity BRACKET capability row to supported after the paper bracket empirical script passes.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-B-cr.1: Empirical micro-script proving crypto bracket fails

Files:
- `scripts/empirical/alpaca_crypto_bracket_paper.py`

- [ ] **Step 1: Pre-flight grep**

```bash
test -d scripts/empirical
rg -n "crypto.*bracket|BTC/USD|EXPECTED_FAIL|UNEXPECTED_PASS" scripts sidecar_alpaca || true
```

- [ ] **Step 2: Write tests**

```python
from scripts.empirical.alpaca_crypto_bracket_paper import run


class FailingClient:
    def submit_order(self, order_data):
        raise RuntimeError('bracket orders are not supported for crypto')


class PassingClient:
    def submit_order(self, order_data):
        return type('Order', (), {'id': 'unexpected'})()


def test_crypto_bracket_expected_fail(capsys):
    assert run(FailingClient()) == 0
    assert 'EXPECTED_FAIL' in capsys.readouterr().out


def test_crypto_bracket_unexpected_pass(capsys):
    assert run(PassingClient()) == 1
    assert 'UNEXPECTED_PASS' in capsys.readouterr().out
```

- [ ] **Step 3: Apply the implementation**

```python
#!/usr/bin/env python3
import os
from decimal import Decimal

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest


def build_client():
    return TradingClient(os.environ['ALPACA_PAPER_API_KEY'], os.environ['ALPACA_PAPER_API_SECRET'], paper=True)


def run(client=None) -> int:
    client = client or build_client()
    try:
        order = client.submit_order(order_data=MarketOrderRequest(
            symbol='BTC/USD', notional=Decimal('1.00'), side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY, order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=Decimal('200000.00')),
            stop_loss=StopLossRequest(stop_price=Decimal('1.00')),
        ))
    except Exception as exc:
        print(f'EXPECTED_FAIL: {exc}')
        return 0
    print(f'UNEXPECTED_PASS: {order.id}')
    return 1


if __name__ == '__main__':
    raise SystemExit(run())
```

- [ ] **Step 4: Run the focused test**

```bash
uv run python scripts/empirical/alpaca_crypto_bracket_paper.py
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "EXPECTED_FAIL|UNEXPECTED_PASS|OrderClass.BRACKET|BTC/USD" scripts/empirical/alpaca_crypto_bracket_paper.py
```

- [ ] **Step 6: Commit**

Subject:

```text
test(alpaca): document crypto bracket rejection
```

Body:

```text
Add the paper empirical script that attempts a BTC/USD bracket order and treats Alpaca rejection as the expected outcome.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-B-cr.2: Alembic 0021-cr flip crypto bracket false

Files:
- `backend/alembic/versions/0021_cr_alpaca_crypto_bracket.py`

- [ ] **Step 1: Pre-flight grep**

```bash
ls backend/alembic/versions | sort | tail -20
rg -n "CRYPTO|BRACKET|not supported|0021_cr" backend/alembic/versions backend/tests
```

- [ ] **Step 2: Write tests**

```python
from sqlalchemy import text


def test_0021_cr_sets_negative_crypto_bracket_capability(db_session):
    row = db_session.execute(text("""
        SELECT is_supported, notes FROM order_capability
        WHERE broker_id = (SELECT id FROM brokers WHERE slug='alpaca')
          AND asset_class='CRYPTO' AND order_type='BRACKET'
    """)).mappings().one()
    assert row['is_supported'] is False
    assert 'not supported' in row['notes'].lower()
```

- [ ] **Step 3: Apply the implementation**

```python
from alembic import op

revision = '0021_cr_alpaca_crypto_bracket'
down_revision = '0021_eq_alpaca_equity_bracket'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO order_capability (broker_id, asset_class, order_type, tif, is_supported, notes)
        SELECT id, 'CRYPTO', 'BRACKET', 'DAY', FALSE, 'Alpaca crypto bracket not supported per Phase 8c empirical gate'
        FROM brokers WHERE slug='alpaca'
        ON CONFLICT (broker_id, asset_class, order_type, tif)
        DO UPDATE SET is_supported = FALSE, notes = EXCLUDED.notes
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE order_capability
        SET notes = 'Reverted explicit Phase 8c crypto bracket negative capability'
        WHERE broker_id = (SELECT id FROM brokers WHERE slug='alpaca')
          AND asset_class = 'CRYPTO'
          AND order_type = 'BRACKET'
    """)
```

- [ ] **Step 4: Run the focused test**

```bash
cd backend && uv run pytest tests/alembic/test_0021_cr_alpaca_crypto_bracket.py -v
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "0021_cr_alpaca_crypto_bracket|CRYPTO|BRACKET|is_supported = FALSE|not supported" backend/alembic/versions/0021_cr_alpaca_crypto_bracket.py backend/tests
```

- [ ] **Step 6: Commit**

Subject:

```text
feat(capabilities): mark Alpaca crypto bracket unsupported
```

Body:

```text
Insert or update the explicit negative capability row for Alpaca crypto bracket orders so the UI can render not-supported state correctly.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-O.1: dispatch_oco_alpaca_equity in oco_orchestrator.py

Files:
- `app/services/oco_orchestrator.py`
- `backend/tests/test_oco_alpaca_equity.py`

- [ ] **Step 1: Pre-flight grep**

```bash
rg -n "dispatch_oco|OcoOrderRequest|OcoOrderResponse|order_class.*oco|alpaca" app backend/tests
rg -n "oco_orchestrator" app/services backend/tests
```

- [ ] **Step 2: Write tests**

```python
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.oco_orchestrator import dispatch_oco_alpaca_equity


class FakeAlpacaClient:
    def __init__(self):
        self.order_data = None
    async def submit_order(self, order_data):
        self.order_data = order_data
        return SimpleNamespace(id='oco-parent', legs=[SimpleNamespace(id='limit-leg'), SimpleNamespace(id='stop-leg')])


@pytest.mark.asyncio
async def test_dispatch_oco_alpaca_equity_uses_native_oco():
    client = FakeAlpacaClient()
    req = SimpleNamespace(symbol='AAPL', side='SELL', qty='1', limit_price='200.00', stop_price='150.00', stop_limit_price='149.50', tif='GTC')
    resp = await dispatch_oco_alpaca_equity(req, client)
    assert resp.leg_order_ids == ['limit-leg', 'stop-leg']
    assert client.order_data.order_class == 'oco'
```

- [ ] **Step 3: Apply the implementation**

```python
from decimal import Decimal
from dataclasses import dataclass


@dataclass
class OcoOrderResponse:
    external_order_id: str
    leg_order_ids: list[str]


async def dispatch_oco_alpaca_equity(request, alpaca_client) -> OcoOrderResponse:
    order_data = AlpacaOcoOrderRequest(
        symbol=request.symbol,
        qty=Decimal(request.qty),
        side=request.side,
        time_in_force=request.tif,
        order_class='oco',
        limit_price=Decimal(request.limit_price),
        stop_price=Decimal(request.stop_price),
        stop_limit_price=Decimal(request.stop_limit_price) if request.stop_limit_price else None,
    )
    order = await alpaca_client.submit_order(order_data)
    return OcoOrderResponse(
        external_order_id=str(order.id),
        leg_order_ids=[str(leg.id) for leg in getattr(order, 'legs', [])],
    )
```

- [ ] **Step 4: Run the focused test**

```bash
cd backend && uv run pytest tests/test_oco_alpaca_equity.py -v
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "dispatch_oco_alpaca_equity|order_class='oco'|OcoOrderResponse|leg_order_ids" app/services/oco_orchestrator.py backend/tests/test_oco_alpaca_equity.py
```

- [ ] **Step 6: Commit**

Subject:

```text
feat(oco): dispatch Alpaca equity OCO natively
```

Body:

```text
Add an Alpaca equity OCO dispatch branch that uses native order_class oco support and returns both leg order ids to the orchestrator caller.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-O.2: dispatch_oco_alpaca_crypto fallback branch

Files:
- `app/services/oco_orchestrator.py`
- `backend/tests/test_oco_alpaca_crypto.py`

- [ ] **Step 1: Pre-flight grep**

```bash
rg -n "dispatch_oco_alpaca_crypto|alpaca_crypto_oco_not_supported|NotImplementedError|OCO" app backend/tests
rg -n "asset_class.*CRYPTO|order_type.*OCO" app backend/tests
```

- [ ] **Step 2: Write tests**

```python
from types import SimpleNamespace

import pytest

from app.services.oco_orchestrator import dispatch_oco_alpaca_crypto


class FakeAlpacaClient:
    async def submit_order(self, order_data):
        return SimpleNamespace(id='crypto-oco-parent', legs=[SimpleNamespace(id='a'), SimpleNamespace(id='b')])


@pytest.mark.asyncio
async def test_dispatch_oco_alpaca_crypto_pass_branch():
    req = SimpleNamespace(symbol='BTC/USD', side='SELL', qty='0.001', limit_price='200000.00', stop_price='50000.00', stop_limit_price='', tif='GTC')
    resp = await dispatch_oco_alpaca_crypto(req, FakeAlpacaClient(), crypto_oco_supported=True)
    assert resp.leg_order_ids == ['a', 'b']


@pytest.mark.asyncio
async def test_dispatch_oco_alpaca_crypto_fail_branch():
    req = SimpleNamespace(symbol='BTC/USD')
    with pytest.raises(NotImplementedError, match='alpaca_crypto_oco_not_supported'):
        await dispatch_oco_alpaca_crypto(req, FakeAlpacaClient(), crypto_oco_supported=False)
```

- [ ] **Step 3: Apply the implementation**

```python
async def dispatch_oco_alpaca_crypto(request, alpaca_client, crypto_oco_supported: bool = True):
    # PASS branch: keep native parity with equity if the empirical script succeeds.
    # TODO: if scripts/empirical/alpaca_crypto_oco_paper.py prints EXPECTED_FAIL,
    # call this with crypto_oco_supported=False and let the API layer convert to 422.
    if not crypto_oco_supported:
        raise NotImplementedError('alpaca_crypto_oco_not_supported')
    return await _dispatch_oco_alpaca_native(
        request=request,
        alpaca_client=alpaca_client,
        asset_class='CRYPTO',
    )


async def _dispatch_oco_alpaca_native(request, alpaca_client, asset_class: str):
    order_data = AlpacaOcoOrderRequest(
        symbol=request.symbol,
        qty=Decimal(request.qty),
        side=request.side,
        time_in_force=request.tif,
        order_class='oco',
        limit_price=Decimal(request.limit_price),
        stop_price=Decimal(request.stop_price),
        stop_limit_price=Decimal(request.stop_limit_price) if request.stop_limit_price else None,
    )
    order = await alpaca_client.submit_order(order_data)
    return OcoOrderResponse(str(order.id), [str(leg.id) for leg in getattr(order, 'legs', [])])
```

- [ ] **Step 4: Run the focused test**

```bash
cd backend && uv run pytest tests/test_oco_alpaca_crypto.py -v
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "dispatch_oco_alpaca_crypto|alpaca_crypto_oco_not_supported|crypto_oco_supported|TODO" app/services/oco_orchestrator.py backend/tests/test_oco_alpaca_crypto.py
```

- [ ] **Step 6: Commit**

Subject:

```text
feat(oco): add Alpaca crypto OCO branch
```

Body:

```text
Add the Alpaca crypto OCO dispatch hook with a native PASS branch and an explicit NotImplementedError fallback for the empirical FAIL outcome.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-O.5: Alembic 0022 OCO capability flip plus empirical scripts

Files:
- `scripts/empirical/alpaca_equity_oco_paper.py`
- `scripts/empirical/alpaca_crypto_oco_paper.py`
- `backend/alembic/versions/0022_alpaca_oco_capability.py`

- [ ] **Step 1: Pre-flight grep**

```bash
test -d scripts/empirical
ls backend/alembic/versions | sort | tail -20
rg -n "OCO|order_class.*oco|alpaca.*oco|EXPECTED_FAIL|UNEXPECTED_PASS" app backend scripts docs/superpowers/specs/2026-05-06-phase8c-alpaca-trade-design.md
```

- [ ] **Step 2: Write tests**

```python
from scripts.empirical.alpaca_crypto_oco_paper import run as run_crypto
from scripts.empirical.alpaca_equity_oco_paper import run as run_equity


class EquityClient:
    def submit_order(self, order_data):
        return type('Order', (), {'id': 'parent', 'legs': [type('Leg', (), {'id': 'l1'})(), type('Leg', (), {'id': 'l2'})()]})()
    def cancel_order_by_id(self, order_id):
        return None


class CryptoFailClient:
    def submit_order(self, order_data):
        raise RuntimeError('oco is unavailable for crypto')


def test_equity_oco_script_passes(capsys):
    assert run_equity(EquityClient()) == 0
    assert 'PASS' in capsys.readouterr().out


def test_crypto_oco_script_expected_fail(capsys):
    assert run_crypto(CryptoFailClient()) == 0
    assert 'EXPECTED_FAIL' in capsys.readouterr().out
```

- [ ] **Step 3: Apply the implementation**

```python
# scripts/empirical/alpaca_equity_oco_paper.py
#!/usr/bin/env python3
import os
from decimal import Decimal
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest

def build_client():
    return TradingClient(os.environ['ALPACA_PAPER_API_KEY'], os.environ['ALPACA_PAPER_API_SECRET'], paper=True)

def run(client=None) -> int:
    client = client or build_client()
    order = client.submit_order(order_data=LimitOrderRequest(symbol='AAPL', qty=Decimal('1'), side=OrderSide.SELL, time_in_force=TimeInForce.GTC, order_class=OrderClass.OCO, limit_price=Decimal('200.00'), stop_price=Decimal('50.00'))) 
    ids = [str(leg.id) for leg in getattr(order, 'legs', [])]
    if len(ids) != 2:
        print(f'FAIL: expected 2 OCO legs, got {len(ids)}')
        return 1
    for order_id in ids:
        client.cancel_order_by_id(order_id)
    print(f'PASS: equity OCO legs canceled: {ids}')
    return 0

# scripts/empirical/alpaca_crypto_oco_paper.py
def run_crypto_oco(client=None) -> int:
    client = client or build_client()
    try:
        order = client.submit_order(order_data=LimitOrderRequest(symbol='BTC/USD', qty=Decimal('0.001'), side=OrderSide.SELL, time_in_force=TimeInForce.GTC, order_class=OrderClass.OCO, limit_price=Decimal('200000.00'), stop_price=Decimal('50000.00'))) 
    except Exception as exc:
        print(f'EXPECTED_FAIL: {exc}')
        return 0
    print(f'UNEXPECTED_PASS: {order.id}')
    return 1

# backend/alembic/versions/0022_alpaca_oco_capability.py
from alembic import op
revision = '0022_alpaca_oco_capability'
down_revision = '0021_cr_alpaca_crypto_bracket'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("""UPDATE order_capability SET is_supported=TRUE, notes='Phase 8c equity OCO empirical PASS' WHERE broker_id=(SELECT id FROM brokers WHERE slug='alpaca') AND asset_class='EQUITY' AND order_type='OCO'""")
    op.execute("""UPDATE order_capability SET is_supported=FALSE, notes='Alpaca crypto OCO not supported per empirical gate' WHERE broker_id=(SELECT id FROM brokers WHERE slug='alpaca') AND asset_class='CRYPTO' AND order_type='OCO'""")

def downgrade() -> None:
    op.execute("""UPDATE order_capability SET is_supported=FALSE, notes='Reverted Phase 8c OCO capability flip' WHERE broker_id=(SELECT id FROM brokers WHERE slug='alpaca') AND order_type='OCO'""")
```

- [ ] **Step 4: Run the focused test**

```bash
uv run python scripts/empirical/alpaca_equity_oco_paper.py
uv run python scripts/empirical/alpaca_crypto_oco_paper.py
cd backend && uv run pytest tests/alembic/test_0022_alpaca_oco_capability.py -v
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "OrderClass.OCO|AAPL|BTC/USD|EXPECTED_FAIL|UNEXPECTED_PASS|0022_alpaca_oco_capability|order_type='OCO'" scripts/empirical/alpaca_equity_oco_paper.py scripts/empirical/alpaca_crypto_oco_paper.py backend/alembic/versions/0022_alpaca_oco_capability.py
```

- [ ] **Step 6: Commit**

Subject:

```text
feat(capabilities): set Alpaca OCO support by asset class
```

Body:

```text
Add equity and crypto OCO empirical scripts, then flip equity OCO support on and record crypto OCO as unsupported according to the empirical gate.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-close.1: CHANGELOG.md v0.10.0 section

Files:
- `CHANGELOG.md`

- [ ] **Step 1: Pre-flight grep**

```bash
sed -n '1,80p' CHANGELOG.md
rg -n "^## \[?v?[0-9]+\.[0-9]+\.[0-9]+|Phase 8c|v0.10.0" CHANGELOG.md
```

- [ ] **Step 2: Write tests**

```markdown
## v0.10.0 - 2026-05-06

### Added

- Added Alpaca equity trade write paths for place, cancel, modify, bracket, OCO, and order events.
- Added Alpaca crypto trade support with cash_amount notional ordering and crypto symbol normalization.
- Added nightly real-broker Alpaca equity and crypto workflows backed by paper credentials.

### Changed

- Widened order quantity precision from 8dp to 10dp for crypto-compatible quantities.
- Expanded order capability cache size and added labelled eviction metrics.
- Split Alpaca order capabilities by asset class for equity and crypto support visibility.

### Fixed

- Recorded explicit negative capability rows for unsupported Alpaca crypto bracket and OCO behavior.
- Added empirical paper scripts for Alpaca place/cancel, bracket, and OCO gates.
```

- [ ] **Step 3: Apply the implementation**

```python
from pathlib import Path

path = Path('CHANGELOG.md')
text = path.read_text()
block = '''## v0.10.0 - 2026-05-06

### Added

- Added Alpaca equity trade write paths for place, cancel, modify, bracket, OCO, and order events.
- Added Alpaca crypto trade support with cash_amount notional ordering and crypto symbol normalization.
- Added nightly real-broker Alpaca equity and crypto workflows backed by paper credentials.

### Changed

- Widened order quantity precision from 8dp to 10dp for crypto-compatible quantities.
- Expanded order capability cache size and added labelled eviction metrics.
- Split Alpaca order capabilities by asset class for equity and crypto support visibility.

### Fixed

- Recorded explicit negative capability rows for unsupported Alpaca crypto bracket and OCO behavior.
- Added empirical paper scripts for Alpaca place/cancel, bracket, and OCO gates.
'''
lines = text.splitlines()
insert_at = 1 if lines and lines[0].startswith('#') else 0
updated = '\n'.join(lines[:insert_at] + ['', block.rstrip(), ''] + lines[insert_at:]) + '\n'
path.write_text(updated)
```

- [ ] **Step 4: Run the focused test**

```bash
sed -n '1,70p' CHANGELOG.md
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "v0.10.0|Phase 8c|Alpaca equity|Alpaca crypto|10dp|negative capability" CHANGELOG.md
```

- [ ] **Step 6: Commit**

Subject:

```text
docs(changelog): add v0.10.0 section for Phase 8c Alpaca trade
```

Body:

```text
Prepend the v0.10.0 changelog section summarizing Alpaca equity and crypto trading, capability changes, empirical gates, and precision updates.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-close.2: TASKS.md Phase 8c marked complete

Files:
- `TASKS.md`

- [ ] **Step 1: Pre-flight grep**

```bash
rg -n "Phase 8c|8c|Alpaca trade|in-progress|complete" TASKS.md
sed -n '1,160p' TASKS.md
```

- [ ] **Step 2: Write tests**

```markdown
- Phase 8c - Alpaca trade: complete (completed 2026-05-06)
```

- [ ] **Step 3: Apply the implementation**

```python
from pathlib import Path

path = Path('TASKS.md')
text = path.read_text()
replacements = {
    'Phase 8c - Alpaca trade: in-progress': 'Phase 8c - Alpaca trade: complete (completed 2026-05-06)',
    'Phase 8c: in-progress': 'Phase 8c: complete (completed 2026-05-06)',
}
for old, new in replacements.items():
    text = text.replace(old, new)
path.write_text(text)
```

- [ ] **Step 4: Run the focused test**

```bash
rg -n "Phase 8c.*complete|completed 2026-05-06" TASKS.md
```

- [ ] **Step 5: Verification grep**

```bash
rg -n "Phase 8c.*complete.*2026-05-06|Alpaca trade.*complete" TASKS.md
```

- [ ] **Step 6: Commit**

Subject:

```text
docs(tasks): mark Phase 8c complete
```

Body:

```text
Mark Phase 8c Alpaca trade complete in TASKS.md and record the completion date as 2026-05-06.
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

## Task T-close.3: git tag v0.10.0 deferred to user

Files:
- `git metadata only; no repository file changes`

- [ ] **Step 1: Pre-flight grep**

```bash
git tag --sort=-creatordate | head -10
git log --oneline --decorate -5
```

- [ ] **Step 2: Write tests**

```bash
git tag v0.10.0
git push origin v0.10.0
```

- [ ] **Step 3: Apply the implementation**

```text
N/A - tagging is intentionally deferred. Do not edit files for this task.
```

- [ ] **Step 4: Run the focused test**

```text
N/A - no pytest or pnpm command applies to creating an annotated release tag.
```

- [ ] **Step 5: Verification grep**

```bash
printf '%s\n' 'User runs: git tag v0.10.0 && git push origin v0.10.0'
```

- [ ] **Step 6: Commit**

Subject:

```text
No commit subject; user runs the release tag command manually.
```

Body:

```text
User runs: git tag v0.10.0 && git push origin v0.10.0
```

Do not run this commit during plan authoring. The implementation PR owner runs it after tests pass.

---

---

## Architect-Review Applied

21 findings applied inline (3 CRIT + 7 HIGH + 11 MED). Spec @ commit 82482e4. See spec footer for "Deferred LOWs" list (5 LOWs).

## Estimate

20 days (17 base + 2-day buffer for empirical re-runs per LOW-2).
