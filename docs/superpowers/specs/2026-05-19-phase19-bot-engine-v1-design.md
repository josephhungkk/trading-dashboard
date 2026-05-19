# Phase 19 — Bot Engine v1 (Rule-Based)

**Version:** v0.19.0  
**Date:** 2026-05-19  
**Depends on:** Phase 18 (scanner + earnings hooks), Phase 10a (risk gate), Phase 11a (AI router)

---

## 1. Overview

Phase 19 ships a rule-based bot engine: a separate Docker service (`bot_worker`) that runs user-defined strategy plugins against live bar feeds, routes orders through the existing risk gate, and streams status to the frontend. Each bot runs in its own dedicated child process supervised by a `BotSupervisor`. Paper-mode is the default; live-mode requires explicit opt-in.

Phase 20 (backtesting harness) will replay historical bars through the same `BaseStrategy` interface defined here.

---

## 2. Strategy Plugin Model

### 2.1 BaseStrategy ABC

Strategies live in a `strategies/` directory at the repo root (gitignored; volume-mounted read-only into both `backend` and `bot_worker` containers at `/strategies`). Each strategy is a Python file containing exactly one class that subclasses `BaseStrategy`:

```python
# app/bot/base.py
from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID

class BaseStrategy(ABC):
    params: dict[str, Any]       # injected at init from bot.params_json
    accounts: list[UUID]         # injected at init from bot.account_ids
    ctx: BotContext              # injected at init

    # Optional: declare a JSONSchema dict here for API-side params validation.
    # The API extracts this via a sandboxed subprocess (see §2.1 extraction) at
    # bot create/update time; validates params_json and returns 400 on mismatch.
    params_schema: dict[str, Any] | None = None

    @abstractmethod
    async def on_start(self) -> None: ...
    # Called once on bot launch, AFTER BarAggregator is started (but bar delivery
    # is paused until on_start() returns). Subscribe symbols here via ctx.subscribe().

    @abstractmethod
    async def on_bar(self, bar: BarEvent) -> None: ...
    # Called on each bar-complete event. Primary decision point.

    async def on_fill(self, fill: FillEvent) -> None: ...
    # Optional. Routed here by BotFillRouter (§4.4), not by place_order().

    async def on_stop(self) -> None: ...
    # Optional. Called on graceful shutdown. Cancel open orders here if desired.
```

**`params_schema` extraction:** When `POST /api/bots` or `PUT /api/bots/{id}` is called, the API runs a sandboxed subprocess:
```
python -c "import json, importlib.util; spec = importlib.util.spec_from_file_location('s', '<path>'); m = spec.loader.load_module(); cls = next(c for c in vars(m).values() if isinstance(c, type) and issubclass(c, BaseStrategy) and c is not BaseStrategy); print(json.dumps(cls.params_schema))"
```
with a 5s timeout and the MetaPathFinder denylist active. Result is cached in `bots.params_schema_json` (see §3.1). If `params_schema` is non-null, `params_json` is validated against it; mismatch → 400 with field-level errors. Counter: `bot_params_validation_failures_total`.

### 2.2 BarEvent and FillEvent

```python
@dataclass(frozen=True)
class BarEvent:
    canonical_id: str
    timeframe: str          # '1m', '5m', '1h', '1d'
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    ts: datetime            # bar close time (UTC for intraday; session-close for daily)

@dataclass(frozen=True)
class FillEvent:
    order_id: UUID
    account_id: UUID
    canonical_id: str
    side: str               # 'buy' | 'sell'
    qty: Decimal
    price: Decimal
    filled_at: datetime
```

### 2.3 Strategy Loading & Import Sandbox

The child process loads strategies using `importlib.util.spec_from_file_location` — no `eval`, no `exec`. It scans the loaded module for the single concrete subclass of `BaseStrategy` and instantiates it with injected `params`, `accounts`, and `ctx`.

Before importing, the child installs a `MetaPathFinder` denylist blocking `app.api.*` and `app.services.orders_service`. A denied import → `ImportError` → child exits `status='error'`, `error_msg='strategy_imports_forbidden_module'`. Metric: `bot_forbidden_import_total{bot_id, module}`.

All side-effects must go through `BotContext`.

### 2.4 Bar Feed — Child-Local Tick→Bar Aggregator

`quote.<source>.<canonical_id>` Redis pubsub carries **ticks**, not bars. `BarService` is pull-only. Each child runs a `BarAggregator` (`app/bot/bar_aggregator.py`) to convert ticks to bars.

**Boundary computation:**
- `1m`, `5m`, `15m`, `30m`, `1h` → UTC-boundary modulo timeframe (standard OHLCV convention).
- `1d`, `1w` → market session-close boundary via `MarketCalendar` (Phase 5b surface). Uses the primary exchange of the instrument to select the right calendar.

**Startup:** `BarAggregator` starts **before** `on_start()` is called, but bar delivery to the strategy queue is **paused** until `on_start()` returns. Ticks arriving during warm-up are accumulated in the running bar; no bar-complete events are emitted into the queue during pause. Partial bars at startup (bars whose open tick was missed) are skipped once delivery unpauses. Metric: `bot_partial_bars_skipped_total{bot_id}`.

**Queue:** A bounded `asyncio.Queue(maxsize=100)` sits between `BarAggregator` and the strategy runner. On overflow: drop oldest, emit `bot_bar_events_dropped_total{bot_id}`, `structlog.warning`.

Metric: `bot_bars_aggregator_unhealthy_total{bot_id}` — incremented when the aggregator task exits unexpectedly.

---

## 3. Data Model (Alembic 0061)

> **Note:** 0059 = Phase 18.1 filings; 0060 = Phase 18.2 earnings. This migration adds `bot_place_order` to the existing 11-value `risk_decisions.attempt_kind` allowlist (see §3.5).

### 3.1 `bots`

```sql
CREATE TABLE bots (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT NOT NULL,
    strategy_file       TEXT NOT NULL,         -- relative path under /strategies
    params_json         JSONB NOT NULL DEFAULT '{}',
    params_schema_json  JSONB,                 -- extracted from params_schema class attr; NULL = no schema
    version             INT NOT NULL DEFAULT 1,
    status              TEXT NOT NULL DEFAULT 'stopped'
                        CHECK (status IN ('stopped','starting','running','pausing','paused','error')),
    error_msg           TEXT CHECK (length(error_msg) <= 2000),  -- truncated at writer
    mode                TEXT NOT NULL DEFAULT 'paper'
                        CHECK (mode IN ('paper','live')),
    bar_timeframe       TEXT NOT NULL DEFAULT '1m',
    deleted_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON bots (status) WHERE deleted_at IS NULL;
```

### 3.2 `bot_accounts` (replaces `bots.account_ids` UUID array)

Join table with proper FK integrity. Replaces the UUID array approach.

```sql
CREATE TABLE bot_accounts (
    bot_id      UUID NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    account_id  UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE RESTRICT,
    PRIMARY KEY (bot_id, account_id)
);
CREATE INDEX ON bot_accounts (account_id);
```

`ON DELETE RESTRICT` on `account_id` prevents silent orphaning — operator must remove accounts from bots before deleting a `broker_accounts` row.

### 3.3 `bot_risk_caps`

One row per bot. NULL value = inherit the account-level limit from `risk_limits`. Daily-loss is tracked **per (bot, account, day)** (see §5.2); `daily_loss_tz` is derived at runtime from the account's primary market calendar, not stored here.

```sql
CREATE TABLE bot_risk_caps (
    bot_id                  UUID PRIMARY KEY REFERENCES bots(id) ON DELETE CASCADE,
    max_position_size       NUMERIC(20,8),   -- pre-filter cap; NULL = use account limit
    max_daily_loss          NUMERIC(20,8),   -- pre-filter cap; NULL = use account limit
    max_open_orders         INT,
    max_order_size          NUMERIC(20,8),
    allowed_asset_classes   TEXT[],          -- NULL = all asset classes allowed
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 3.4 `bot_runs` (TimescaleDB hypertable)

`order_count` and `fill_count` are **removed** — computed on-demand via `bot_orders` count queries (avoids the run_id linkage problem after 90-day retention drops rows).

```sql
CREATE TABLE bot_runs (
    id              UUID NOT NULL DEFAULT gen_random_uuid(),
    bot_id          UUID NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    version         INT NOT NULL,           -- snapshot of bots.version at start time
    started_at      TIMESTAMPTZ NOT NULL,   -- partition key
    stopped_at      TIMESTAMPTZ,
    stop_reason     TEXT CHECK (stop_reason IN ('manual','error','daily_loss_cap','kill_switch')),
    bar_count       INT NOT NULL DEFAULT 0,
    PRIMARY KEY (id, started_at)
);
SELECT create_hypertable('bot_runs', 'started_at', chunk_time_interval => INTERVAL '7 days');
SELECT add_retention_policy('bot_runs', INTERVAL '90 days');
CREATE INDEX ON bot_runs (bot_id, started_at DESC);
```

`order_count` / `fill_count` for a run are computed as:
```sql
SELECT COUNT(*) FROM bot_orders WHERE bot_id = :bot_id
  AND placed_at BETWEEN :started_at AND COALESCE(:stopped_at, now())
```

### 3.5 `bot_orders`

```sql
CREATE TABLE bot_orders (
    order_id    UUID PRIMARY KEY REFERENCES orders(id) ON DELETE CASCADE,
    bot_id      UUID NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    placed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON bot_orders (bot_id, placed_at DESC);
```

No `run_id` — avoids FK integrity problem with hypertable composite PK + 90-day retention. Run ownership is reconstructed by time-range join when needed.

### 3.6 `risk_decisions.attempt_kind` widening

The existing constraint from 0060 allows 11 values. 0061 adds `bot_place_order`:

```sql
ALTER TABLE risk_decisions DROP CONSTRAINT risk_decisions_attempt_kind_check;
ALTER TABLE risk_decisions ADD CONSTRAINT risk_decisions_attempt_kind_check
  CHECK (attempt_kind IN (
    'preview', 'place', 'modify', 'place_order', 'modify_order',
    'combo_preview', 'combo_place', 'combo_autoclose',
    'telegram', 'telegram_confirm',
    'earnings_hook_flat',
    'bot_place_order'    -- added Phase 19 (0061)
  ));
```

---

## 4. Process Architecture

### 4.1 Docker Service

```yaml
# docker-compose.yml addition
bot_worker:
  build: ./backend
  entrypoint: ["python", "-m", "app.bot.supervisor"]
  volumes:
    - ./strategies:/strategies:ro
  environment:
    - DATABASE_URL=${DATABASE_URL}
    - REDIS_URL=${REDIS_URL}
  restart: unless-stopped
  depends_on:
    - backend
```

`backend` also mounts `/strategies:ro` for the `params_schema` extraction subprocess (§2.1). `strategies/` is gitignored at repo root.

### 4.2 BotSupervisor

`app/bot/supervisor.py` — runs as the main process in `bot_worker`.

**Startup:**
1. Drain inflight command keys for all known bots (skip already-executed command IDs).
2. Query `bots WHERE status IN ('running', 'pausing') AND deleted_at IS NULL`; re-spawn each as a child process (crash recovery).

**Control queue — Redis Streams with consumer groups:**

Commands use `XADD bot:control:{bot_id}` (API) and a consumer group `supervisor` with `XREADGROUP` + `XACK` (supervisor). Built-in ack/redeliver semantics: if supervisor crashes mid-command, the unacked entry is redeliverable via `XAUTOCLAIM` on next startup. Command payload: `{id: uuid, cmd: START|STOP|PAUSE|RESUME|DEPLOY}`. Recently-executed command IDs are tracked in a Redis SET (`bot:control:done:{bot_id}`, 1h TTL) to skip duplicates on redeliver.

| API action | Stream entry | Supervisor action |
|---|---|---|
| `POST /start` | `{cmd: START}` | Spawn child; INSERT `bot_runs` with `bots.version` snapshot |
| `POST /stop` | `{cmd: STOP}` | Send STOP via child's `multiprocessing.Queue`; child calls `on_stop()` → exits 0 |
| `POST /pause` | `{cmd: PAUSE}` | Forward via child queue; child pauses bar delivery |
| `POST /resume` | `{cmd: RESUME}` | Forward via child queue; child resumes |
| `POST /deploy` | `{cmd: DEPLOY}` | `UPDATE bots SET version = version + 1 RETURNING version` (atomic); STOP old child; spawn new |

Metric: `bot_control_command_timeouts_total{action}` — status stuck in `starting`/`pausing` >30s.

**In-band pause/resume:** Supervisor forwards PAUSE/RESUME/STOP to child via per-child `multiprocessing.Queue`. No POSIX signals for control.

**Heartbeat monitoring:** each child writes `bot:heartbeat:{bot_id}` Redis key (10s TTL) every 5s. Supervisor polls every 8s. On expiry:
1. Mark `bots.status='error'`; UPDATE `bot_runs.stopped_at`, `stop_reason='error'`
2. Respawn with backoff: 10s → 30s → 60s (3 attempts max)
3. After 3 failures: set `status='error'`, `error_msg`, stop retrying, publish `bot:status:{id}`
4. Metrics: `bot_respawn_total{bot_id}` per attempt; `bot_unexpected_exit_total{bot_id}` on exit-code mismatch

**Exit-code contract:**
- Exit 0 + `status IN ('pausing','stopping')` → mark `status='stopped'`, no respawn.
- Exit non-zero OR heartbeat expiry → respawn with backoff.
- Exit 0 + `status='running'` → log, increment `bot_unexpected_exit_total`, treat as crash.

### 4.3 Child Process

Each bot runs as a `multiprocessing.Process` with its own:
- asyncio event loop
- SQLAlchemy async DB connection pool (4 connections)
- Redis connection
- `BotContext` instance
- `BarAggregator` instance
- `multiprocessing.Queue` for PAUSE/RESUME/STOP from supervisor

Child process lifecycle:
1. Install `MetaPathFinder` denylist (§2.3)
2. Load strategy file via `importlib`; validate `params_json` against `params_schema` if set
3. **Authoritative live-mode check:** if `bot.mode='live'`, verify all `bot_accounts.account_id` resolve to `broker_accounts.mode='live'`; mismatch → exit `status='error'`, `error_msg='mode_mismatch'`
4. INSERT `bot_runs(bot_id, version, started_at)` — `version` from `bots.version` at this moment
5. Start `BarAggregator` task (bar delivery paused)
6. Instantiate strategy; call `await strategy.on_start()` (symbols registered here via `ctx.subscribe()`)
7. Unpause bar delivery; begin consuming bar queue
8. Poll per-child `multiprocessing.Queue` for control commands alongside bar loop
9. On each bar-complete: `await strategy.on_bar(bar)` (histogram: `bot_on_bar_latency_seconds`)
10. On fill event via `bot:fill:{bot_id}` Redis pubsub: `await strategy.on_fill(fill)`
11. On STOP: cancel bar subscription, `await strategy.on_stop()`, UPDATE `bot_runs.stopped_at + stop_reason='manual'`, exit 0

**Per-call mode-drift check in `BotContext.place_order`:** re-verifies `broker_accounts.mode` matches `self.mode` (60s Redis cache). Mismatch → `BotModeMismatchError` → child exits `error_msg='mode_drift'`.

### 4.4 BotFillRouter

`app/bot/fill_router.py` — asyncio task running in **backend**, co-located with `OrderFillProcessor`.

When a fill arrives whose `order_id` exists in `bot_orders`:
1. Publish `bot:fill:{bot_id}` Redis pubsub (child subscribes; triggers `on_fill()`)
2. `INCRBYFLOAT bot:daily_loss:{bot_id}:{account_id}:{tz_date}` by fill's realised PnL (per-account key — see §5.2)
3. Increment `bot_fill_events_total{bot_id, side}`

Fills do **not** come from `BotContext.place_order()`. `BotFillRouter` is the sole fill-routing path.

---

## 5. Risk Cap Layer

### 5.1 BotRiskCapService

`app/bot/risk_caps.py` — pure pre-filter before `RiskService.evaluate()`. `EvaluationContext` (frozen, 22 fields) is not modified.

Five checks:

| Check | Fail policy | Rationale |
|---|---|---|
| `qty × price > max_order_size` | **fail-CLOSED** | Money-moving |
| `bot open order count ≥ max_open_orders` | fail-OPEN | Non-catastrophic |
| `bot realised PnL today (per account) ≤ −max_daily_loss` | **fail-CLOSED** | Money-moving |
| `allowed_asset_classes IS NOT NULL AND instrument.asset_class NOT IN allowed_asset_classes` | fail-OPEN | Account gate still enforces |
| `resulting position size > max_position_size` | **fail-CLOSED** | Money-moving |

Caps cached 60s in Redis. Invalidation: `PUT /api/bots/{id}/risk-caps` publishes `bot:risk_caps:invalidate:{bot_id}`; API and bot_worker child both subscribe and evict.

### 5.2 Daily Loss Tracking (per account)

Redis key: `bot:daily_loss:{bot_id}:{account_id}:{tz_date}` where `tz_date` is computed from the **account's primary market calendar timezone** (derived via `MarketCalendar` from `broker_accounts.primary_exchange`; e.g. `US/Eastern` for NYSE accounts, `Asia/Hong_Kong` for HKEX). TTL = seconds until midnight in that timezone.

Written by `BotFillRouter` (§4.4). Read by `BotRiskCapService` — sums across all of the bot's accounts to get total daily PnL before comparing to `max_daily_loss`.

---

## 6. BotContext & Order Placement

`app/bot/context.py` and `app/bot/orders_facade.py`.

**`BotOrdersFacade`** — thin class holding injected dependencies (`db`, `redis`, `cfg`, `registry`, `capability`) and exposing bot-flavoured order operations. Avoids injecting a non-existent "OrdersService" class (orders_service.py is a module of module-level coroutines).

```python
class BotOrdersFacade:
    async def place_order(
        self,
        account_id: UUID,
        canonical_id: str,
        side: str,
        qty: Decimal,
        order_type: str,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
        tif: str = "DAY",
        algo_strategy: str | None = None,
        conid: int | None = None,   # optional: skip InstrumentResolver if caller knows it
    ) -> OrderResponse:
        # Resolves conid via InstrumentResolver if not supplied (handles new positions).
        # Calls place_order_internal(issuer='bot', attempt_kind='bot_place_order', ...).
        # place_order_internal fabricates nonce f"internal:bot:{client_order_id}";
        # no Redis preview-mint required.
        ...

    async def cancel_order(self, order_id: UUID, account_id: UUID) -> None:
        # Calls orders_service.cancel_order (no risk gate; conid resolved via bot_orders join).
        # Emits bot_fill_events_total{side='cancel'} for consistency.
        ...
```

**`place_order_internal` wiring:**  `issuer` Literal is widened to include `"bot"`. `attempt_kind` is derived as `"bot_place_order"` when `issuer == "bot"`. The internal path skips Redis nonce validation and uses `InstrumentResolver` for conid lookup, so both new and existing positions are handled.

**`BotContext`** — the strategy-facing surface:

```python
class BotContext:
    bot_id: UUID
    run_id: UUID
    accounts: list[UUID]
    mode: Literal["paper", "live"]
    _facade: BotOrdersFacade
    _risk_cap_svc: BotRiskCapService

    async def subscribe(self, canonical_id: str) -> None:
        # Registers canonical_id with the child's BarAggregator.

    async def place_order(self, account_id: UUID, **kwargs) -> OrderResponse:
        # 1. assert account_id in self.accounts          → BotAccountError
        # 2. re-verify broker_accounts.mode (60s cache)  → BotModeMismatchError on drift
        # 3. BotRiskCapService.check()                   → BotRiskCapError on BLOCK
        # 4. BotOrdersFacade.place_order(attempt_kind='bot_place_order')
        # 5. INSERT bot_orders(order_id, bot_id, placed_at)
        # 6. Increment bot_runs.bar_count [no — update order_count on-demand]

    async def cancel_order(self, order_id: UUID) -> None:
        # delegates to BotOrdersFacade.cancel_order; verifies order_id in bot_orders
    
    async def get_positions(self, account_id: UUID) -> list[PositionRow]:
        # DB positions table — eventual-consistent, always available

    async def get_open_orders(self, account_id: UUID) -> list[OrderRow]:
        # DB orders WHERE status IN ('working','submitted') AND account_id = ?

    async def get_fills_today(self, account_id: UUID) -> list[FillRow]:
        # DB order_fills WHERE account_id = ? AND filled_at >= session_open(account_tz)
```

---

## 7. REST API

`app/api/bots.py` — all endpoints require JWT auth.

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/bots` | Create bot. Validates accounts exist via `bot_accounts`. Extracts + caches `params_schema_json`. Validates `params_json` if schema present. |
| `GET` | `/api/bots` | List bots. Filters: `status`, `mode`. Cursor pagination on `created_at`. |
| `GET` | `/api/bots/{id}` | Detail + on-demand run stats (order/fill counts computed from `bot_orders`). |
| `PUT` | `/api/bots/{id}` | Update `name`, `params_json`, `bar_timeframe`. Only when `status='stopped'`. |
| `DELETE` | `/api/bots/{id}` | Soft-delete. Only when `status='stopped'`. |
| `GET` | `/api/bots/{id}/runs` | List `bot_runs`. Cursor pagination on `started_at`. |
| `GET` | `/api/bots/{id}/orders` | List `bot_orders` joined to `orders`. Cursor pagination on `placed_at`. |
| `PUT` | `/api/bots/{id}/risk-caps` | Upsert `bot_risk_caps`. CSRF nonce. Publishes `bot:risk_caps:invalidate:{id}`. |
| `POST` | `/api/bots/{id}/start` | Pre-flight account-mode check (UX). `XADD bot:control:{id} cmd=START`. Sets `status='starting'`. |
| `POST` | `/api/bots/{id}/stop` | `XADD cmd=STOP`. Sets `status='pausing'`. |
| `POST` | `/api/bots/{id}/pause` | `XADD cmd=PAUSE`. |
| `POST` | `/api/bots/{id}/resume` | `XADD cmd=RESUME`. |
| `POST` | `/api/bots/{id}/deploy` | Atomic `UPDATE bots SET version = version + 1 RETURNING version`. `XADD STOP` + `XADD START`. |
| `GET` | `/api/bots/strategies` | Lists `.py` files in `/strategies`. Returns `[{filename, size, mtime: ISO8601}]`. JWT-only. |

### 7.1 WebSocket

`WS /ws/bots/status` — Redis pubsub `bot:status:*` → conflation (500ms) → WS push. Connection cap: **50**. Frame schema:

```json
{
  "type": "status_change | heartbeat_loss | fill | daily_loss_cap",
  "bot_id": "uuid",
  "status": "running | error | ...",
  "data": {}
}
```

---

## 8. Frontend

### 8.1 Routes

| Route | Component | Description |
|---|---|---|
| `/bots` | `BotsPage` | Bot list with status badges, controls, today's PnL |
| `/bots/new` | `BotCreatePage` | Create form |
| `/bots/{id}` | `BotDetailPage` | Detail: runs, orders, risk caps, params, error log |

### 8.2 Key Components

- **`BotStatusBadge`** — sidebar nav shows `"N running · K errors / M total"` (K links to error-filtered list). Not per-bot badges.
- **`BotControlBar`** — start/stop/pause/resume/deploy. Live-mode start: `useConfirmDialog` hook (same as paper→live toggle).
- **`StrategyFilePicker`** — dropdown from `GET /api/bots/strategies`.
- **`ParamsEditor`** — Monaco JSON editor (reuses `/admin/ai` pattern). Disabled when not stopped.
- **`RiskCapsForm`** — per-field override inputs; NULL = inherit (shown as placeholder).
- **`BotRunsTable`** — cursor-paginated; order/fill counts from on-demand query.
- **`BotOrdersTable`** — cursor-paginated; links to existing order detail.

### 8.3 State

TanStack Query + WS push hybrid (same as `/portfolio/rollup`). WS events call `queryClient.invalidateQueries`. Zustand not needed.

---

## 9. Prometheus Metrics

17 metrics under `bot_*` prefix. `bot_on_bar_latency_seconds` has no `bot_id` label (cardinality: 30+ bots × histogram buckets).

| Metric | Type | Labels |
|---|---|---|
| `bot_starts_total` | Counter | `bot_id`, `mode` |
| `bot_stops_total` | Counter | `bot_id`, `stop_reason` |
| `bot_orders_total` | Counter | `bot_id`, `mode`, `verdict` |
| `bot_daily_loss_cap_hits_total` | Counter | `bot_id` |
| `bot_heartbeat_failures_total` | Counter | `bot_id` |
| `bot_respawn_total` | Counter | `bot_id` |
| `bot_unexpected_exit_total` | Counter | `bot_id` |
| `bot_bars_processed_total` | Counter | `bot_id`, `timeframe` |
| `bot_on_bar_latency_seconds` | Histogram | *(no bot_id)* |
| `bot_bar_events_dropped_total` | Counter | `bot_id` |
| `bot_partial_bars_skipped_total` | Counter | `bot_id` |
| `bot_bars_aggregator_unhealthy_total` | Counter | `bot_id` |
| `bot_active_count` | Gauge | `mode` |
| `bot_fill_events_total` | Counter | `bot_id`, `side` |
| `bot_context_errors_total` | Counter | `bot_id`, `error_type` |
| `bot_forbidden_import_total` | Counter | `bot_id`, `module` |
| `bot_control_command_timeouts_total` | Counter | `action` |
| `bot_params_validation_failures_total` | Counter | — |

---

## 10. Testing Strategy

### 10.1 Backend (pytest)

| File | Scope |
|---|---|
| `tests/bot/test_base_strategy.py` | ABC conformance; `params_schema` extraction subprocess; API-side validation; mode routing |
| `tests/bot/test_bar_aggregator.py` | Tick→bar boundary (UTC intraday + market-calendar daily); bounded queue overflow; pause-before-on_start; partial bar skipped on resume |
| `tests/bot/test_bot_risk_cap_service.py` | All 5 checks; fail-CLOSED on money-moving caps under Redis failure; fail-OPEN on non-catastrophic; per-account daily-loss key with market-TZ |
| `tests/bot/test_bot_orders_facade.py` | `place_order_internal` with `issuer='bot'`; conid resolution via InstrumentResolver for new positions; `attempt_kind='bot_place_order'` in risk_decisions; `cancel_order` emits cancel metric |
| `tests/bot/test_bot_context.py` | `place_order`: `bot_orders` row inserted, mode-drift check fires, unknown account raises; `get_positions/orders/fills` read from DB not sidecar |
| `tests/bot/test_bot_fill_router.py` | Fill for bot_orders order → `bot:fill` published; per-account daily-loss key updated |
| `tests/bot/test_supervisor.py` | Redis Stream ack/redeliver; duplicate command ID skipped; heartbeat expiry respawn; exit-code contract; crash recovery on startup |
| `tests/bot/test_import_sandbox.py` | Strategy importing `app.api.bots` → `bot_forbidden_import_total` incremented |
| `tests/bot/test_api.py` | All 14 endpoints; lifecycle state machine; CSRF; pagination; deploy atomicity; `bot_accounts` FK rejects unknown account |
| `tests/bot/test_ws_status.py` | Status events delivered; error count in sidebar summary; WS cap 50 |
| `tests/bot/test_e2e_bot_lifecycle.py` | Fixture strategy places one order on first bar; `bot_orders` row; `attempt_kind='bot_place_order'` in `risk_decisions`; stop → `bot_runs.stop_reason='manual'` |

Target: **≥80% coverage** on `app/bot/` module.

### 10.2 Frontend (Vitest + RTL)

- Sidebar badge shows "N running · K errors / M total"
- Live-mode start triggers `useConfirmDialog`
- Params editor disabled when not stopped
- WS hook updates status on event (mock WS)
- RiskCapsForm sends null for unset fields

---

## 11. Deferred to Phase 20 / 21

| Item | Phase |
|---|---|
| Backtesting harness (replay `on_bar()` against historical bars) | 20 |
| Per-bot PnL attribution report | 21 |
| LLM-suggested parameter tuning | 21 |
| Shadow-mode strategy promotion | 21 |
| Multi-bot orchestration | 22 |
| Kelly criterion sizing per bot | Phase 21 (needs backtest stats) |

---

## 12. Security

- `/strategies` mounted read-only; `bot_worker` has no write access.
- `MetaPathFinder` denylist blocks `app.api.*` and `app.services.orders_service` in child processes and in `params_schema` extraction subprocess.
- `BotContext.place_order()` asserts `account_id in self.accounts` and re-verifies `broker_accounts.mode` (60s cache) on every call.
- Authoritative live-mode check in child at `on_start()` (§4.3 step 3); API pre-flight is UX only.
- `bot_accounts.ON DELETE RESTRICT` prevents silent account-reference orphaning.
- CSRF nonce on risk-caps mutation.
- Bot worker: no inbound ports; outbound to Redis and Postgres only.
- `bot_place_order` in `risk_decisions.attempt_kind` provides forensic audit separation.
- `error_msg` capped at 2000 chars (CHECK constraint + writer truncation).
