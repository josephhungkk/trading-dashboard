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

