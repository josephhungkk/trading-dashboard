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

Strategies live in a `strategies/` directory at the repo root (gitignored; volume-mounted read-only into the bot_worker container at `/strategies`). Each strategy is a Python file containing exactly one class that subclasses `BaseStrategy`:

```python
# app/bot/base.py
from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID

class BaseStrategy(ABC):
    params: dict[str, Any]       # injected at init from bot.params_json
    accounts: list[UUID]         # injected at init from bot.account_ids
    ctx: BotContext              # injected at init

    # Optional: declare a JSON Schema dict here for API-side params validation.
    # If present, POST /api/bots and PUT /api/bots/{id} validate params_json
    # against it and return 400 with field-level errors on mismatch.
    params_schema: dict[str, Any] | None = None

    @abstractmethod
    async def on_start(self) -> None: ...
    # Called once on bot launch. Use for warm-up: subscribe to symbols,
    # load initial state, validate params.

    @abstractmethod
    async def on_bar(self, bar: BarEvent) -> None: ...
    # Called on each bar-complete event at the configured timeframe.
    # Primary decision point for the strategy.

    async def on_fill(self, fill: FillEvent) -> None: ...
    # Optional. Called when a fill event arrives for an order placed by this bot.
    # Fills are routed here by BotFillRouter (see §4.4), not by place_order().

    async def on_stop(self) -> None: ...
    # Optional. Called on graceful shutdown. Responsible for cancelling open
    # orders if desired.
```

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
    ts: datetime            # bar close time (UTC)

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

The supervisor loads strategies in the child process using `importlib.util.spec_from_file_location` — no `eval`, no `exec`. The child scans the loaded module for the single concrete subclass of `BaseStrategy` and instantiates it with injected `params`, `accounts`, and `ctx`.

Before importing, the child installs a `MetaPathFinder` denylist that blocks any attempt to import `app.api.*` or `app.services.orders_service` from within the strategy module. A denied import raises `ImportError` → child exits with `status='error'`, `error_msg='strategy_imports_forbidden_module'`. Metric: `bot_forbidden_import_total{module}`.

Strategies may use any other installed Python library. All side-effects must go through `BotContext`.

### 2.4 Bar Feed — Child-Local Tick→Bar Aggregator

The `quote.<source>.<canonical_id>` Redis pubsub carries **ticks**, not bars. `BarService` is a pull-only DB query service; it does not emit bar-complete events. Therefore each child process runs a `BarAggregator` to convert ticks to bars.

`BarAggregator` (`app/bot/bar_aggregator.py`):
- Subscribes to `quote.*.<canonical_id>` for each symbol registered via `ctx.subscribe(canonical_id)`.
- Maintains a per-symbol OHLCV accumulator keyed by `(canonical_id, timeframe_bucket)`.
- When a tick's timestamp crosses a timeframe boundary, emits a `BarEvent` to a bounded `asyncio.Queue(maxsize=100)` shared with the strategy runner.
- On queue overflow: drops oldest event, emits `bot_bar_events_dropped_total{bot_id}` counter, logs `structlog.warning`.

Metric: `bot_bars_aggregator_unhealthy_total{bot_id}` — incremented when the aggregator task exits unexpectedly (supervisor re-creates the task).

The strategy runner pulls from the queue and calls `await strategy.on_bar(bar)`.

---

## 3. Data Model (Alembic 0061)

> **Note:** Migrations 0059 (`filings`) and 0060 (`earnings`) were shipped in Phase 18.1/18.2. This migration depends on 0060 having widened `risk_decisions.attempt_kind` (for `auto_flat_close`); 0061 widens it further for `bot_place_order`.

### 3.1 `bots`

```sql
CREATE TABLE bots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    strategy_file   TEXT NOT NULL,         -- relative path under /strategies
    params_json     JSONB NOT NULL DEFAULT '{}',
    account_ids     UUID[] NOT NULL,       -- FK-checked at API layer
    version         INT NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'stopped'
                    CHECK (status IN ('stopped','starting','running','pausing','paused','error')),
    error_msg       TEXT,
    mode            TEXT NOT NULL DEFAULT 'paper'
                    CHECK (mode IN ('paper','live')),
    bar_timeframe   TEXT NOT NULL DEFAULT '1m',
    deleted_at      TIMESTAMPTZ,           -- soft-delete
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON bots (status) WHERE deleted_at IS NULL;
```

### 3.2 `bot_risk_caps`

One row per bot. NULL value = inherit the account-level limit from `risk_limits`.

```sql
CREATE TABLE bot_risk_caps (
    bot_id                  UUID PRIMARY KEY REFERENCES bots(id) ON DELETE CASCADE,
    max_position_size       NUMERIC(20,8),   -- pre-filter cap; NULL = use account limit
    max_daily_loss          NUMERIC(20,8),   -- pre-filter cap; NULL = use account limit
    max_open_orders         INT,
    max_order_size          NUMERIC(20,8),
    allowed_asset_classes   TEXT[],          -- NULL = all asset classes allowed
    daily_loss_tz           TEXT NOT NULL DEFAULT 'UTC',  -- market TZ for daily-loss key
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 3.3 `bot_runs` (TimescaleDB hypertable)

```sql
CREATE TABLE bot_runs (
    id              UUID NOT NULL DEFAULT gen_random_uuid(),
    bot_id          UUID NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    version         INT NOT NULL,           -- snapshot of bots.version at start time
    started_at      TIMESTAMPTZ NOT NULL,   -- partition key
    stopped_at      TIMESTAMPTZ,
    stop_reason     TEXT CHECK (stop_reason IN ('manual','error','daily_loss_cap','kill_switch')),
    bar_count       INT NOT NULL DEFAULT 0,
    order_count     INT NOT NULL DEFAULT 0,
    fill_count      INT NOT NULL DEFAULT 0,
    PRIMARY KEY (id, started_at)
);
SELECT create_hypertable('bot_runs', 'started_at', chunk_time_interval => INTERVAL '7 days');
SELECT add_retention_policy('bot_runs', INTERVAL '90 days');
CREATE INDEX ON bot_runs (bot_id, started_at DESC);
```

### 3.4 `bot_orders`

Audit trail linking every bot-placed order to its bot. No `run_id` column — `bot_orders` outlives the 90-day `bot_runs` hypertable retention; the owning run can be reconstructed by time-range join on `placed_at` vs `bot_runs.started_at/stopped_at`.

```sql
CREATE TABLE bot_orders (
    order_id    UUID PRIMARY KEY REFERENCES orders(id) ON DELETE CASCADE,
    bot_id      UUID NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    placed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON bot_orders (bot_id, placed_at DESC);
```

### 3.5 `risk_decisions.attempt_kind` widening

```sql
ALTER TABLE risk_decisions DROP CONSTRAINT risk_decisions_attempt_kind_check;
ALTER TABLE risk_decisions ADD CONSTRAINT risk_decisions_attempt_kind_check
  CHECK (attempt_kind IN (
    'preview', 'place_order', 'modify_order',
    'auto_flat_close',    -- added Phase 18.2 (0060)
    'bot_place_order'     -- added Phase 19 (0061)
  ));
```

`BotContext.place_order()` passes `attempt_kind='bot_place_order'` through to `RiskService.evaluate()` / the audit insert, distinguishing bot-placed orders from operator-placed orders in the `risk_decisions` table.

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

`strategies/` is gitignored at the repo root. Operator places strategy `.py` files there manually or via the admin UI (future phase).

### 4.2 BotSupervisor

`app/bot/supervisor.py` — runs as the main process in `bot_worker`.

**Startup:**
1. Drain `bot:control:{bot_id}` command queues for all known bots (pick up commands sent during supervisor downtime).
2. Query `bots WHERE status IN ('running', 'pausing') AND deleted_at IS NULL` and re-spawn each as a child process (crash recovery).

**Control queue (Redis LIST — replaces pubsub for reliability):**

Commands are delivered via `LPUSH bot:control:{bot_id} <CMD>` (API side) and consumed via `BRPOPLPUSH bot:control:{bot_id} bot:control:inflight:{bot_id}` (supervisor side). After the command is acted on, supervisor removes it from the inflight list. If the supervisor restarts mid-command, the inflight list is drained on startup.

| API action | Command pushed | Supervisor action |
|---|---|---|
| `POST /start` | `START` | Spawn child; INSERT `bot_runs` row with `bots.version` snapshot |
| `POST /stop` | `STOP` | Send `STOP` via child's per-bot control queue; child calls `on_stop()` → exits 0 |
| `POST /pause` | `PAUSE` | Send `PAUSE` via child control queue; child pauses bar feed |
| `POST /resume` | `RESUME` | Send `RESUME` via child control queue; child resumes bar feed |
| `POST /deploy` | `STOP` then `START` | Atomically: `UPDATE bots SET version = version + 1 RETURNING version`; stop old child, spawn new |

Metric: `bot_control_command_timeouts_total{action}` — incremented when a bot remains in `starting` or `pausing` status for >30s (command presumed lost).

**In-band pause/resume (replaces SIGUSR1/2):** The supervisor forwards `PAUSE`/`RESUME` commands to the child via a `multiprocessing.Queue` (per-child, created at spawn time). The child's asyncio loop polls this queue and suspends/resumes the bar feed consumer. No POSIX signals used for control (SIGUSR1/2 conflict with profilers/debuggers).

**Heartbeat monitoring:** each child writes `bot:heartbeat:{bot_id}` Redis key with 10s TTL every 5s. Supervisor polls every 8s. On expiry:
1. Mark `bots.status='error'`, UPDATE `bot_runs.stopped_at`, `stop_reason='error'`
2. Respawn with exponential backoff: 10s → 30s → 60s (3 attempts max)
3. After 3 failures: set `status='error'`, set `error_msg`, stop retrying, publish `bot:status:{id}` event
4. Increment `bot_respawn_total{bot_id}` on each attempt; `bot_unexpected_exit_total{bot_id}` on exit-code mismatch (exit 0 while `status='running'`)

**Exit-code contract:**
- Exit 0 + `status IN ('pausing','stopping')` → supervisor marks `status='stopped'`, no respawn.
- Exit non-zero OR heartbeat expiry → respawn with backoff.
- Exit 0 + `status='running'` → unexpected; log, increment `bot_unexpected_exit_total`, treat as crash.

### 4.3 Child Process

Each bot runs as a `multiprocessing.Process` with its own:
- asyncio event loop
- SQLAlchemy async DB connection pool (**4 connections** — place_order holds 1 during RiskService + audit; get_positions is a concurrent read path)
- Redis connection
- `BotContext` instance
- `BarAggregator` instance
- `multiprocessing.Queue` for in-band PAUSE/RESUME/STOP commands from supervisor

Child process lifecycle:
1. Install `MetaPathFinder` denylist (§2.3)
2. Load strategy file via `importlib`; fail fast if `params_schema` validation fails
3. **Live-mode check:** if `bot.mode='live'`, verify all `account_ids` resolve to `broker_accounts.mode='live'`; if any mismatch → exit with `status='error'`, `error_msg='mode_mismatch'` (this is the authoritative check; API does a cheap pre-flight for UX only)
4. INSERT `bot_runs (bot_id, version, started_at)` — `version` copied from `bots.version` at this moment
5. Instantiate strategy with `params`, `accounts`, `ctx`; call `await strategy.on_start()`
6. Start `BarAggregator` task; subscribe to tick stream for each symbol registered in `on_start()`
7. Poll per-child `multiprocessing.Queue` for PAUSE/RESUME/STOP alongside bar-event loop
8. On each bar-complete (from bounded asyncio.Queue, maxsize=100): `await strategy.on_bar(bar)`
9. On fill routed by `BotFillRouter` (§4.4): `await strategy.on_fill(fill)`
10. On STOP command: cancel bar subscription, `await strategy.on_stop()`, UPDATE `bot_runs.stopped_at + stop_reason='manual'`, exit 0

### 4.4 BotFillRouter

`app/bot/fill_router.py` — a lightweight asyncio task running in the **backend** (not bot_worker), co-located with the existing `OrderFillProcessor`.

When a fill arrives whose `order_id` exists in `bot_orders`:
1. Publish `bot:fill:{bot_id}` Redis pubsub event (child's asyncio loop subscribes)
2. `UPDATE bot_runs SET fill_count = fill_count + 1 WHERE id = :run_id AND started_at = :started_at`
3. `INCRBYFLOAT bot:daily_loss:{bot_id}:{market_tz_date}` by the fill's realised PnL
4. Increment `bot_fill_events_total{bot_id, side}`

Fills do **not** come from `BotContext.place_order()` — that returns an order acknowledgement only. `BotFillRouter` is the single path that routes async broker fills back to bots.

---

## 5. Risk Cap Layer

### 5.1 BotRiskCapService

`app/bot/risk_caps.py` — runs *before* `RiskService.evaluate()` in every `BotContext.place_order()` call. The bot caps are a **pre-filter**, not an override injected into `RiskService` — `EvaluationContext` (frozen dataclass, 22 fields) is not modified. Account-level `risk_limits` remain fully active after the pre-filter passes.

Five checks:

| Check | Fail policy | Rationale |
|---|---|---|
| `qty × price > max_order_size` | **fail-CLOSED** | Money-moving; Redis outage must not let this through |
| `bot open order count ≥ max_open_orders` | fail-OPEN | Non-catastrophic; count query failure is unlikely |
| `bot realised PnL today ≤ −max_daily_loss` | **fail-CLOSED** | Money-moving |
| `allowed_asset_classes IS NOT NULL AND instrument.asset_class NOT IN allowed_asset_classes` | fail-OPEN | Account-level gate still enforces |
| `resulting position size > max_position_size` | **fail-CLOSED** | Money-moving |

Caps are Redis-cached with 60s TTL. Invalidation: `PUT /api/bots/{id}/risk-caps` publishes `bot:risk_caps:invalidate:{bot_id}` to a Redis pubsub channel; both the API process and the bot_worker child subscribe and drop their local LRU cache entry on receipt.

### 5.2 Daily Loss Tracking

`bot:daily_loss:{bot_id}:{market_tz_yyyy_mm_dd}` Redis key:
- Key suffix uses the **account's market calendar timezone** (`bot_risk_caps.daily_loss_tz`, default `'UTC'`), not hardcoded UTC. Pattern: `bot:daily_loss:{bot_id}:{tz_date}`.
- `INCRBYFLOAT` on each fill (written by `BotFillRouter`, §4.4).
- TTL set to seconds until midnight in `daily_loss_tz`.
- On cap hit: supervisor publishes `bot:status:{id}` with `stop_reason='daily_loss_cap'`, sends STOP command to child, which calls `on_stop()` and exits.

---

## 6. BotContext

`app/bot/context.py` — the only surface strategies touch for side-effects.

```python
class BotContext:
    # Fields injected at child-process init.
    bot_id: UUID
    run_id: UUID
    accounts: list[UUID]
    mode: Literal["paper", "live"]
    _orders_svc: OrdersService          # injected; not exposed to strategy
    _risk_cap_svc: BotRiskCapService    # injected; not exposed to strategy

    async def subscribe(self, canonical_id: str) -> None:
        # Registers canonical_id with the child's BarAggregator.

    async def place_order(self, account_id: UUID, req: PlaceOrderRequest) -> OrderResponse:
        # 1. assert account_id in self.accounts      → raises BotAccountError
        # 2. BotRiskCapService.check()               → raises BotRiskCapError on BLOCK
        # 3. OrdersService.place_order(              # full pipeline incl. RiskService
        #        attempt_kind='bot_place_order')
        # 4. INSERT bot_orders(order_id, bot_id, placed_at)
        # 5. UPDATE bot_runs SET order_count = order_count + 1
        # Fills are async — routed back via BotFillRouter (§4.4), not here.

    async def cancel_order(self, order_id: UUID) -> None: ...
    async def get_positions(self, account_id: UUID) -> list[PositionRow]: ...
    async def get_open_orders(self, account_id: UUID) -> list[OrderRow]: ...
    async def get_fills_today(self, account_id: UUID) -> list[FillRow]: ...
```

In `paper` mode, `place_order` routes to the broker's paper account (`broker_accounts.mode='paper'`). Strategies are mode-agnostic; `BotContext` enforces routing.

---

## 7. REST API

`app/api/bots.py` — all endpoints require JWT auth.

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/bots` | Create bot. Validates `account_ids` exist. Validates `params_json` against `params_schema` if present. |
| `GET` | `/api/bots` | List bots. Filters: `status`, `mode`. Cursor pagination on `created_at`. |
| `GET` | `/api/bots/{id}` | Detail + current run stats. |
| `PUT` | `/api/bots/{id}` | Update `name`, `params_json`, `bar_timeframe`. Only when `status='stopped'`. |
| `DELETE` | `/api/bots/{id}` | Soft-delete (`deleted_at = now()`). Only when `status='stopped'`. |
| `GET` | `/api/bots/{id}/runs` | List `bot_runs`. Cursor pagination on `started_at`. |
| `GET` | `/api/bots/{id}/orders` | List `bot_orders` joined to `orders`. Cursor pagination on `placed_at`. |
| `PUT` | `/api/bots/{id}/risk-caps` | Upsert `bot_risk_caps`. Requires CSRF nonce. Publishes `bot:risk_caps:invalidate:{id}`. |
| `POST` | `/api/bots/{id}/start` | Pre-flight: checks account mode match for live bots (UX only; authoritative check is in child). `LPUSH bot:control:{id} START`. Sets `status='starting'`. |
| `POST` | `/api/bots/{id}/stop` | `LPUSH bot:control:{id} STOP`. Sets `status='pausing'` (supervisor confirms `stopped`). |
| `POST` | `/api/bots/{id}/pause` | `LPUSH bot:control:{id} PAUSE`. |
| `POST` | `/api/bots/{id}/resume` | `LPUSH bot:control:{id} RESUME`. |
| `POST` | `/api/bots/{id}/deploy` | `UPDATE bots SET version = version + 1 RETURNING version` (atomic). Then `LPUSH STOP` + `LPUSH START`. |
| `GET` | `/api/bots/strategies` | Lists `.py` files in `/strategies` volume. Returns `[{filename, size, mtime}]`. JWT-only (single-user system). |

### 7.1 WebSocket

`WS /ws/bots/status` — streams bot status events to FE. Redis pubsub `bot:status:*` → conflation (500ms) → WS push. Connection cap: **50** (matches scanner; operators routinely have phone + desktop + secondary monitor open). Frame schema:

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

- **`BotStatusBadge`** — stopped/starting/running/pausing/paused/error with colour coding. Injected into sidebar nav as "N running / M total" summary (not per-bot badges — avoids wall-of-badges when operator has 30+ bots).
- **`BotControlBar`** — start/stop/pause/resume/deploy buttons. Live-mode start requires confirm dialog; reuses existing `useConfirmDialog` hook (same pattern as paper→live mode toggle).
- **`StrategyFilePicker`** — dropdown populated from `GET /api/bots/strategies`.
- **`ParamsEditor`** — Monaco JSON editor (reuses existing `/admin/ai` pattern). Disabled when bot is not stopped.
- **`RiskCapsForm`** — per-field override inputs. NULL input = inherit account limit (shown as placeholder).
- **`BotRunsTable`** — cursor-paginated run history with bar/order/fill counts.
- **`BotOrdersTable`** — cursor-paginated orders linked to existing order detail.

### 8.3 State

Bot list and detail use TanStack Query + WS push (same hybrid pattern as `/portfolio/rollup`). WS status events call `queryClient.invalidateQueries` on the relevant bot. Zustand not needed — no cross-page shared bot state.

---

## 9. Prometheus Metrics

14 metrics under `bot_*` prefix. `bot_on_bar_latency_seconds` drops `bot_id` label (cardinality: 30+ bots × histogram buckets = Prometheus explosion); per-bot latency is exposed via OpenTelemetry traces instead.

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
| `bot_on_bar_latency_seconds` | Histogram | *(no bot_id — see note above)* |
| `bot_bar_events_dropped_total` | Counter | `bot_id` |
| `bot_bars_aggregator_unhealthy_total` | Counter | `bot_id` |
| `bot_active_count` | Gauge | — |
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
| `tests/bot/test_base_strategy.py` | ABC conformance, `params_schema` validation at API layer, `BotContext` method contracts, mode routing |
| `tests/bot/test_bar_aggregator.py` | Tick→bar boundary detection, bounded queue overflow drops oldest + counter, aggregator-unhealthy metric |
| `tests/bot/test_bot_risk_cap_service.py` | All 5 pre-filter checks; fail-CLOSED on money-moving checks under Redis failure; fail-OPEN on non-catastrophic; override vs inherit; Redis daily-loss reset; `daily_loss_tz` key computation |
| `tests/bot/test_bot_context.py` | `place_order`: `bot_orders` row inserted, `attempt_kind='bot_place_order'` threaded to RiskService, unknown `account_id` raises, paper mode routes correctly, fill NOT published here |
| `tests/bot/test_bot_fill_router.py` | Fill for `bot_orders` order → `bot:fill:{id}` published, `fill_count` incremented, daily-loss key updated |
| `tests/bot/test_supervisor.py` | Command queue drain on startup; heartbeat expiry triggers respawn; 3-failure backoff sets error; exit-0-while-running triggers `bot_unexpected_exit_total`; STOP command → exit 0 → `status='stopped'`; crash-recovery re-spawns running bots |
| `tests/bot/test_import_sandbox.py` | Strategy importing `app.api.bots` fails at load → `bot_forbidden_import_total` incremented |
| `tests/bot/test_api.py` | All 14 REST endpoints: lifecycle state machine, CSRF on risk-caps, cursor pagination, strategy listing, deploy atomicity, live-mode pre-flight |
| `tests/bot/test_ws_status.py` | Status change events delivered; heartbeat-loss event; WS cap 50 enforced |
| `tests/bot/test_e2e_bot_lifecycle.py` | Fixture strategy places one order on first bar; asserts `bot_orders` row with `attempt_kind='bot_place_order'` in `risk_decisions`; stop → `bot_runs.stop_reason='manual'` |

Target: **≥80% coverage** on `app/bot/` module.

### 10.2 Frontend (Vitest + RTL)

- Bot list renders "N running / M total" summary in sidebar
- Start button on live-mode bot triggers `useConfirmDialog`
- Params editor disables when bot is not stopped
- WS hook updates bot status on incoming event (mock WS)
- RiskCapsForm sends null for unset fields (inherit behaviour)
- `bot_params_validation_failures_total` counter fires when API rejects bad params

---

## 11. Deferred to Phase 20 / 21

| Item | Phase |
|---|---|
| Backtesting harness (replay `on_bar()` against historical bars) | 20 |
| Per-bot PnL attribution report | 21 |
| LLM-suggested parameter tuning | 21 |
| Shadow-mode strategy promotion | 21 |
| Multi-bot orchestration | 22 |
| Kelly criterion sizing per bot | originally Phase 19 in sizing spec — deferred to Phase 21 (needs backtest stats) |

---

## 12. Security

- Strategy files are loaded read-only from `/strategies` volume. `bot_worker` has no write access to that path.
- `MetaPathFinder` denylist blocks `app.api.*` and `app.services.orders_service` imports from strategy modules (§2.3).
- `BotContext.place_order()` asserts `account_id in self.accounts` before any downstream call.
- Live-mode bots require `mode='live'` at creation. **Authoritative check** is in the child process at `on_start()` — verifies all `account_ids` resolve to `broker_accounts.mode='live'` (§4.3 step 3). API pre-flight is UX-only.
- CSRF nonce required on risk-caps mutation.
- Bot worker has no inbound network ports — outbound to Redis and Postgres only.
- `bot_place_order` in `risk_decisions.attempt_kind` provides forensic separation of bot vs operator orders in the audit trail.
