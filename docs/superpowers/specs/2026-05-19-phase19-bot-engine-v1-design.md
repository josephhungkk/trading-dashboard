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

Strategies live in a `strategies/` directory volume-mounted read-only into the bot_worker container. Each strategy is a Python file containing exactly one class that subclasses `BaseStrategy`:

```python
# app/bot/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from uuid import UUID

class BaseStrategy(ABC):
    params: dict[str, Any]       # injected at init from bot.params_json
    accounts: list[UUID]         # injected at init from bot.account_ids
    ctx: BotContext              # injected at init

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

    async def on_stop(self) -> None: ...
    # Optional. Called on graceful shutdown (SIGTERM). Responsible for
    # cancelling open orders if desired.
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

### 2.3 Strategy Loading

The supervisor loads strategies in the child process using `importlib.util.spec_from_file_location` — no `eval`, no `exec`. The child scans the loaded module for the single concrete subclass of `BaseStrategy` and instantiates it with injected `params`, `accounts`, and `ctx`.

Strategies may use any installed Python library. They must not import from `app.api.*` or touch Redis/DB directly — all side-effects go through `BotContext`.

### 2.4 Bar Feed

The child process subscribes to `quote.<source>.<canonical_id>` Redis pubsub (the existing Phase 7b quote bus). Bar aggregation reuses `BarService` — no new bar infrastructure. The set of symbols to subscribe to is declared by the strategy in `on_start()` via `ctx.subscribe(canonical_id)`.

---

## 3. Data Model (Alembic 0059)

### 3.1 `bots`

```sql
CREATE TABLE bots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    strategy_file   TEXT NOT NULL,         -- relative path under strategies/
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
    max_position_size       NUMERIC(20,8),   -- overrides account-level if non-null
    max_daily_loss          NUMERIC(20,8),   -- overrides account-level if non-null
    max_open_orders         INT,
    max_order_size          NUMERIC(20,8),
    allowed_asset_classes   TEXT[],          -- NULL = all asset classes allowed
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 3.3 `bot_runs` (TimescaleDB hypertable)

```sql
CREATE TABLE bot_runs (
    id              UUID NOT NULL DEFAULT gen_random_uuid(),
    bot_id          UUID NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    version         INT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL,    -- partition key
    stopped_at      TIMESTAMPTZ,
    stop_reason     TEXT CHECK (stop_reason IN ('manual','error','daily_loss_cap','kill_switch')),
    bar_count       INT NOT NULL DEFAULT 0,
    order_count     INT NOT NULL DEFAULT 0,
    fill_count      INT NOT NULL DEFAULT 0,
    PRIMARY KEY (id, started_at)
);
-- Hypertable: chunk_time_interval = 7 days, retention = 90 days
SELECT create_hypertable('bot_runs', 'started_at', chunk_time_interval => INTERVAL '7 days');
SELECT add_retention_policy('bot_runs', INTERVAL '90 days');
CREATE INDEX ON bot_runs (bot_id, started_at DESC);
```

### 3.4 `bot_orders`

Audit trail linking every bot-placed order to its run.

```sql
CREATE TABLE bot_orders (
    order_id    UUID PRIMARY KEY REFERENCES orders(id) ON DELETE CASCADE,
    bot_id      UUID NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    run_id      UUID NOT NULL,  -- no FK: bot_runs has composite PK (id, started_at) on hypertable
    placed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON bot_orders (bot_id, placed_at DESC);
CREATE INDEX ON bot_orders (run_id);
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

### 4.2 BotSupervisor

`app/bot/supervisor.py` — runs as the main process in `bot_worker`.

**Startup:** queries `bots WHERE status IN ('running', 'pausing') AND deleted_at IS NULL` and re-spawns each as a child process (crash recovery).

**Control loop:** subscribes to `bot:control:*` Redis pubsub pattern. Dispatches commands to child processes:

| API action | Redis message | Supervisor action | Child signal |
|---|---|---|---|
| `POST /start` | `bot:control:{id} START` | spawn child | — |
| `POST /stop` | `bot:control:{id} STOP` | send SIGTERM | `on_stop()` → exit |
| `POST /pause` | `bot:control:{id} PAUSE` | send SIGUSR1 | drains, pauses bar feed |
| `POST /resume` | `bot:control:{id} RESUME` | send SIGUSR2 | resumes bar feed |
| `POST /deploy` | `bot:control:{id} STOP` then `START` | stop old, spawn new version | — |

**Heartbeat monitoring:** each child writes `bot:heartbeat:{bot_id}` Redis key with 10s TTL every 5s. Supervisor polls all active bot keys every 8s. On expiry:
1. Mark bot `status='error'`, record `stop_reason='error'` in `bot_runs`
2. Respawn with exponential backoff: 10s → 30s → 60s (3 attempts max)
3. After 3 failures: set `status='error'`, set `error_msg`, stop retrying, publish `bot:status:{id}` event

### 4.3 Child Process

Each bot runs as a `multiprocessing.Process` with its own:
- asyncio event loop
- SQLAlchemy async DB connection pool (2 connections max)
- Redis connection
- `BotContext` instance

The child process lifecycle:
1. Load strategy file via `importlib`
2. Instantiate strategy with `params`, `accounts`, `ctx`
3. Call `await strategy.on_start()`
4. Subscribe to `quote.*.<canonical_id>` for each subscribed symbol
5. On each bar-complete: call `await strategy.on_bar(bar)`
6. On fill event from `bot:fill:{bot_id}`: call `await strategy.on_fill(fill)`
7. On SIGTERM: cancel bar subscription, call `await strategy.on_stop()`, exit 0

---

## 5. Risk Cap Layer

### 5.1 BotRiskCapService

`app/bot/risk_caps.py` — runs *before* `RiskService.evaluate()` in every `BotContext.place_order()` call.

Five checks (all fail-OPEN — a Redis/DB failure does not block the order):

| Check | Verdict |
|---|---|
| `qty × price > max_order_size` | BLOCK |
| `bot open order count ≥ max_open_orders` | BLOCK |
| `bot realised PnL today ≤ −max_daily_loss` | BLOCK |
| `allowed_asset_classes IS NOT NULL AND instrument.asset_class NOT IN allowed_asset_classes` | BLOCK |
| `resulting position size > max_position_size` | BLOCK |

Caps are Redis-cached with 60s TTL (invalidated on `PUT /api/bots/{id}/risk-caps`).

### 5.2 Override Mechanism

When a `bot_risk_caps` row has a non-null value for a field, it is passed as `bot_overrides: BotRiskCaps | None` to `RiskService.evaluate()`. The gate reads from `bot_overrides` first, falls back to the account-level `risk_limits` row. No structural change to the 7 existing checks — just a new optional argument.

### 5.3 Daily Loss Tracking

`bot:daily_loss:{bot_id}:{YYYY-MM-DD}` Redis key (INCRBYFLOAT on each fill, TTL = seconds until midnight UTC). Checked in `BotRiskCapService` against `max_daily_loss`. On cap hit: publishes `bot:status:{id}` with `stop_reason='daily_loss_cap'`, calls `on_stop()`, exits child process.

---

## 6. BotContext

`app/bot/context.py` — the only surface strategies touch for side-effects.

```python
class BotContext:
    # Not a pure dataclass — has async methods. Fields injected at child-process init.
    bot_id: UUID
    run_id: UUID
    accounts: list[UUID]
    mode: Literal["paper", "live"]
    _orders_svc: OrdersService      # injected, not exposed to strategy
    _risk_cap_svc: BotRiskCapService

    async def subscribe(self, canonical_id: str) -> None:
        # Registers canonical_id for bar feed in this child process

    async def place_order(self, account_id: UUID, req: PlaceOrderRequest) -> OrderResponse:
        # 1. assert account_id in self.accounts  → raises BotAccountError
        # 2. BotRiskCapService.check()           → raises BotRiskCapError on BLOCK
        # 3. OrdersService.place_order()         → full pipeline incl. RiskService
        # 4. INSERT bot_orders row
        # 5. publish bot:fill:{bot_id} fill event
        # 6. INCR bot_runs.order_count

    async def cancel_order(self, order_id: UUID) -> None: ...
    async def get_positions(self, account_id: UUID) -> list[PositionRow]: ...
    async def get_open_orders(self, account_id: UUID) -> list[OrderRow]: ...
    async def get_fills_today(self, account_id: UUID) -> list[FillRow]: ...
```

In `paper` mode, `place_order` routes to the broker's paper account (account `mode='paper'` in `broker_accounts`) — same mechanism as the existing dashboard mode toggle. Strategies are mode-agnostic.

---

## 7. REST API

`app/api/bots.py` — all endpoints require JWT auth.

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/bots` | Create bot. Validates `account_ids` exist and belong to JWT subject. |
| `GET` | `/api/bots` | List bots. Filters: `status`, `mode`. Cursor pagination on `created_at`. |
| `GET` | `/api/bots/{id}` | Detail + current run stats. |
| `PUT` | `/api/bots/{id}` | Update `name`, `params_json`, `bar_timeframe`. Only when `status='stopped'`. |
| `DELETE` | `/api/bots/{id}` | Soft-delete (`deleted_at = now()`). Only when `status='stopped'`. |
| `GET` | `/api/bots/{id}/runs` | List `bot_runs`. Cursor pagination on `started_at`. |
| `GET` | `/api/bots/{id}/orders` | List `bot_orders` joined to `orders`. Cursor pagination on `placed_at`. |
| `PUT` | `/api/bots/{id}/risk-caps` | Upsert `bot_risk_caps`. Requires CSRF nonce. Invalidates Redis cache. |
| `POST` | `/api/bots/{id}/start` | Publishes `START` to Redis pubsub. Sets `status='starting'`. |
| `POST` | `/api/bots/{id}/stop` | Publishes `STOP`. Sets `status='pausing'` (supervisor confirms `stopped`). |
| `POST` | `/api/bots/{id}/pause` | Publishes `PAUSE`. |
| `POST` | `/api/bots/{id}/resume` | Publishes `RESUME`. |
| `POST` | `/api/bots/{id}/deploy` | Increments `version`, publishes `STOP` then `START`. |
| `GET` | `/api/bots/strategies` | Lists `.py` files in `strategies/` volume. Returns `[{filename, size, mtime}]`. |

### 7.1 WebSocket

`WS /ws/bots/status` — streams bot status events to FE. Redis pubsub `bot:status:*` → conflation (500ms) → WS push. Connection cap: 10. Frame schema:

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

- **`BotStatusBadge`** — stopped/starting/running/pausing/paused/error with colour coding. Injected into sidebar nav.
- **`BotControlBar`** — start/stop/pause/resume/deploy buttons. Live-mode start requires confirm dialog (same pattern as existing mode toggle).
- **`StrategyFilePicker`** — dropdown populated from `GET /api/bots/strategies`.
- **`ParamsEditor`** — Monaco JSON editor (reuses existing `/admin/ai` pattern). Disabled when bot is not stopped.
- **`RiskCapsForm`** — per-field override inputs. NULL input = inherit account limit (shown as placeholder).
- **`BotRunsTable`** — cursor-paginated run history with bar/order/fill counts.
- **`BotOrdersTable`** — cursor-paginated orders linked to existing order detail.

### 8.3 State

Bot list and detail use TanStack Query + WS push (same hybrid pattern as `/portfolio/rollup`). WS status events call `queryClient.invalidateQueries` on the relevant bot. Zustand not needed — no cross-page shared bot state.

---

## 9. Prometheus Metrics

12 metrics under `bot_*` prefix:

| Metric | Type | Labels |
|---|---|---|
| `bot_starts_total` | Counter | `bot_id`, `mode` |
| `bot_stops_total` | Counter | `bot_id`, `stop_reason` |
| `bot_orders_total` | Counter | `bot_id`, `mode`, `verdict` |
| `bot_daily_loss_cap_hits_total` | Counter | `bot_id` |
| `bot_heartbeat_failures_total` | Counter | `bot_id` |
| `bot_respawn_total` | Counter | `bot_id` |
| `bot_bars_processed_total` | Counter | `bot_id`, `timeframe` |
| `bot_on_bar_latency_seconds` | Histogram | `bot_id` |
| `bot_active_count` | Gauge | — |
| `bot_risk_cap_overrides_total` | Counter | `bot_id`, `field` |
| `bot_fill_events_total` | Counter | `bot_id`, `side` |
| `bot_context_errors_total` | Counter | `bot_id`, `error_type` |

---

## 10. Testing Strategy

### 10.1 Backend (pytest)

| File | Scope |
|---|---|
| `tests/bot/test_base_strategy.py` | ABC conformance, `BotContext` method contracts, mode routing |
| `tests/bot/test_bot_risk_cap_service.py` | All 5 pre-filter checks; override vs inherit per cap field; Redis daily-loss reset |
| `tests/bot/test_bot_context.py` | `place_order` integration: `bot_orders` row inserted, cap pre-filter fires before `RiskService`, unknown account_id raises, paper mode routes correctly |
| `tests/bot/test_supervisor.py` | Heartbeat expiry triggers respawn; 3-failure backoff sets error status; SIGTERM triggers `on_stop()`; crash-recovery re-spawns on startup |
| `tests/bot/test_api.py` | All 14 REST endpoints: lifecycle state machine, CSRF on risk-caps, cursor pagination, strategy listing |
| `tests/bot/test_ws_status.py` | Status change events delivered; heartbeat-loss event; connection cap enforced |
| `tests/bot/test_e2e_bot_lifecycle.py` | Fixture strategy places one order on first bar; asserts `bot_orders` row; stop → `bot_runs.stop_reason='manual'` |

Target: **≥80% coverage** on `app/bot/` module.

### 10.2 Frontend (Vitest + RTL)

- Bot list renders correct status badges for each status value
- Start button on live-mode bot triggers confirm dialog
- Params editor disables when bot is not stopped
- WS hook updates bot status on incoming event (mock WS)
- RiskCapsForm sends null for unset fields (inherit behaviour)

---

## 11. Deferred to Phase 20 / 21

| Item | Phase |
|---|---|
| Backtesting harness (replay `on_bar()` against historical bars) | 20 |
| Per-bot PnL attribution report | 21 |
| LLM-suggested parameter tuning | 21 |
| Shadow-mode strategy promotion | 21 |
| Multi-bot orchestration | 22 |
| Kelly criterion sizing per bot | 19 deferred note (originally Phase 19 in sizing spec) |

---

## 12. Security

- Strategy files are loaded read-only from a volume-mounted directory. The directory is not writable by the bot_worker process.
- No `eval` / `exec` — `importlib` only.
- `BotContext.place_order()` asserts `account_id in self.accounts` before any downstream call — a strategy cannot place orders on accounts it was not configured with.
- Live-mode bots require explicit `mode='live'` at creation time; the API rejects `start` for a live bot if any of its `account_ids` resolves to a `broker_accounts` row with `mode != 'live'` — a bot cannot place live orders via a paper account.
- CSRF nonce required on risk-caps mutation.
- Bot worker has no inbound network ports — it only speaks outbound to Redis and Postgres.
