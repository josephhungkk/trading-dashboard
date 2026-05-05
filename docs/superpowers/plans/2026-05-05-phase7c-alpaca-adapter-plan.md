# Phase 7c — Alpaca Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 4th broker stack (Alpaca) as a read-only adapter contributing `crypto.US` primary + `stock.US`/`etf.US` fallback quote sources, plus account/position/order surfaces (paper + live).

**Architecture:** In-cluster Docker sidecars (`alpaca-sidecar-live` on port 9091, `alpaca-sidecar-paper` on port 9092) on `td-net` bridge — no mTLS to the sidecar (network boundary), HTTPS+WSS direct to Alpaca. Two upstream WS connections per sidecar (IEX equity + crypto v1beta3). Two-layer 30-symbol cap (backend soft 25 + sidecar hard 30). Per-mode Configure routing with mode-mismatch metric. New `app_config.broker_gateway_dial` table introduces "labeled docker sidecar" pattern (between IBKR's NUC-mTLS and Schwab's fixed dial).

**Tech Stack:** Python 3.14, alpaca-py SDK (isolated to `client.py` per M3), grpcio, FastAPI, SQLAlchemy 2.0 async, structlog, pytest-asyncio, Pydantic v2, Docker. Frontend: TypeScript 6 strict, React 19, Zustand.

**Spec:** [`docs/superpowers/specs/2026-05-05-phase7c-alpaca-adapter-design.md`](../specs/2026-05-05-phase7c-alpaca-adapter-design.md) (post-architect-review v2 at `62c510e`).

**Codex defaults reference:** `/home/joseph/.claude/projects/-home-joseph-dashboard/memory/codex_defaults.md` — every Codex dispatch must inline the relevant pattern verbatim per spec §9.

---

## Chunk A — Proto + broker registry + dial table

**Codex patterns most likely to bite:** A (parens + `as exc:`).

### Task A1: Add `alpaca` to broker_id Literal

**Files:**
- Modify: `backend/app/brokers/registry.py` (BrokerId Literal)

- [ ] **Step 1: Pre-flight grep**

```bash
grep -rn "BrokerId\b\|Literal\[\"ibkr\"" backend/app/brokers/ backend/app/services/ | grep -v __pycache__
```

Expected: ≥3 hits in `brokers/registry.py`, `services/brokers.py`, `services/account_service.py`. Note exact line numbers.

- [ ] **Step 2: Write failing test**

Create `backend/tests/unit/test_broker_id_literal.py`:

```python
from __future__ import annotations

from typing import get_args

from app.brokers.registry import BrokerId


def test_alpaca_in_broker_id_literal() -> None:
    assert "alpaca" in get_args(BrokerId)


def test_existing_brokers_still_present() -> None:
    args = set(get_args(BrokerId))
    assert {"ibkr", "futu", "schwab"}.issubset(args)
```

- [ ] **Step 3: Run test**

```bash
cd backend && .venv/bin/python -m pytest tests/unit/test_broker_id_literal.py -v
```

Expected: FAIL — `"alpaca" not in get_args(BrokerId)`.

- [ ] **Step 4: Add `alpaca` to BrokerId Literal**

Edit `backend/app/brokers/registry.py` — find the `BrokerId = Literal[...]` line, add `"alpaca"` after the last existing entry. Preserve trailing-comma + line ordering.

- [ ] **Step 5: Run tests + lint + commit**

```bash
cd backend && .venv/bin/python -m pytest tests/unit/test_broker_id_literal.py -v
cd backend && .venv/bin/ruff check app/brokers/registry.py && .venv/bin/mypy --strict app/brokers/registry.py
git add backend/app/brokers/registry.py backend/tests/unit/test_broker_id_literal.py
git commit -m "feat(brokers): register alpaca in BrokerId literal (Phase 7c A1)"
```

---

### Task A2: `app_config.broker_gateway_dial` table + lookup helper

**Files:**
- Create: `backend/app/services/broker_dial.py`, `backend/tests/unit/test_broker_dial.py`
- Modify: `backend/app/services/brokers.py` (`build_broker_registry` — try helper first, fall back on None)

- [ ] **Step 1: Locate existing dial logic**

```bash
grep -n "schwab-sidecar\|10.10.0.2:1800\|gateway_label" backend/app/services/brokers.py | head -20
```

- [ ] **Step 2: Write failing test**

Create `backend/tests/unit/test_broker_dial.py`:

```python
from __future__ import annotations

import pytest

from app.services.broker_dial import resolve_dial


def test_resolve_alpaca_live() -> None:
    config = {
        "broker_gateway_dial": {
            "alpaca-live": "alpaca-sidecar-live:9091",
            "alpaca-paper": "alpaca-sidecar-paper:9092",
        },
    }
    assert resolve_dial(config, "alpaca-live") == "alpaca-sidecar-live:9091"


def test_resolve_alpaca_paper() -> None:
    config = {
        "broker_gateway_dial": {"alpaca-paper": "alpaca-sidecar-paper:9092"},
    }
    assert resolve_dial(config, "alpaca-paper") == "alpaca-sidecar-paper:9092"


def test_unknown_label_raises() -> None:
    with pytest.raises(KeyError, match="alpaca-unknown"):
        resolve_dial({"broker_gateway_dial": {}}, "alpaca-unknown")


def test_missing_table_returns_none_for_legacy_label() -> None:
    # Schwab + IBKR don't enter this table this phase — caller falls back.
    assert resolve_dial({}, "schwab", default=None) is None
```

- [ ] **Step 3: Run test**

Expected: FAIL — module not found.

- [ ] **Step 4: Implement `resolve_dial`**

Create `backend/app/services/broker_dial.py`:

```python
"""Gateway-label → dial-address resolution (Phase 7c HIGH-4).

Introduces `broker_gateway_dial` config table for the new "labeled docker
sidecar" sub-pattern (alpaca-live, alpaca-paper). IBKR's NUC+mTLS dials
and Schwab's fixed in-cluster dial are NOT migrated this phase — callers
fall back to their existing logic when this helper returns the sentinel.
"""

from __future__ import annotations

from typing import Any


_TABLE_KEY = "broker_gateway_dial"
_SENTINEL: Any = object()


def resolve_dial(
    config: dict[str, Any],
    gateway_label: str,
    *,
    default: Any = _SENTINEL,
) -> str | None:
    """Resolve a gateway_label to its dial target.

    Raises KeyError if gateway_label is missing AND no default supplied.
    Pass default=None to opt into "fall through to legacy resolver".
    """
    table = config.get(_TABLE_KEY, {})
    if gateway_label in table:
        return str(table[gateway_label])
    if default is not _SENTINEL:
        return default
    raise KeyError(gateway_label)
```

- [ ] **Step 5: Run tests + wire + commit**

```bash
cd backend && .venv/bin/python -m pytest tests/unit/test_broker_dial.py -v
```

In `backend/app/services/brokers.py::build_broker_registry`, before the existing inline dial logic, call `resolve_dial(config, gateway_label, default=None)` and use the returned value if non-None. Fall through to legacy logic on None.

```bash
cd backend && .venv/bin/ruff check app/services/broker_dial.py app/services/brokers.py && .venv/bin/mypy --strict app/services/broker_dial.py
git add backend/app/services/broker_dial.py backend/app/services/brokers.py backend/tests/unit/test_broker_dial.py
git commit -m "feat(brokers): broker_gateway_dial config table + resolver (Phase 7c A2)"
```

---

## Chunk B — `sidecar_alpaca/` skeleton

**Codex patterns:** A (parens + `as exc:`), E (lazy-singleton init failure cleanup).

### Task B1: Codex dispatches the full skeleton

**Files (all under `sidecar_alpaca/`):** `Dockerfile`, `pyproject.toml`, `__init__.py`, `main.py`, `config.py`, `auth.py`, `metrics.py`, `handlers.py`, `tests/__init__.py`, `tests/test_handlers_stub.py`.

- [ ] **Step 1: Codex dispatch** (subagent_type=`codex:codex-rescue`, prefix `--fresh`):

> Phase 7c Chunk B — sidecar_alpaca/ skeleton. Mirror sidecar_schwab/ layout exactly. Files to create (all under /home/joseph/dashboard/sidecar_alpaca/):
>
> 1. **Dockerfile** — Python 3.14 slim, uv install, deps `alpaca-py>=0.30 grpcio>=1.62 structlog>=24 prometheus_client>=0.20 websockets>=13`. ENTRYPOINT runs `python -m sidecar_alpaca.main`. Expose `${GRPC_PORT:-9091}`. Healthcheck stub.
>
> 2. **pyproject.toml** — name `sidecar_alpaca`, deps as above. Ruff config inheriting from repo root.
>
> 3. **__init__.py** — version stub.
>
> 4. **config.py** — module-level constants:
>    - `MODE = os.environ["MODE"]` (must be "live" or "paper"; raise ValueError otherwise)
>    - `GRPC_PORT = int(os.environ.get("GRPC_PORT", "9091"))`
>    - `BACKEND_ADMIN_GRPC = os.environ.get("BACKEND_ADMIN_GRPC", "backend:8001")`
>    - `ALPACA_ACCOUNT_LABEL = os.environ.get("ALPACA_ACCOUNT_LABEL", "default")`
>    - `BASE_URL_REST` from MODE (`https://api.alpaca.markets/v2` for live, `https://paper-api.alpaca.markets/v2` for paper)
>    - `BASE_URL_DATA = "wss://stream.data.alpaca.markets/v2/iex"`
>    - `BASE_URL_DATA_CRYPTO = "wss://stream.data.alpaca.markets/v1beta3/crypto/us"`
>
> 5. **auth.py** — `AuthCache` class with `set_credentials(api_key, api_secret)` (atomic via asyncio.Lock), `get_credentials()` returning frozen tuple, `mode` property. Pattern A on swap path: `except (ValueError, RuntimeError) as exc:`. Pattern E if Configure fails: revert to None, do NOT leave half-applied creds.
>
> 6. **metrics.py** — Prometheus Counter/Gauge defs (verbatim names — Pattern F):
>    - `ALPACA_SIDECAR_UPTIME_SECONDS = Gauge(..., labelnames=["mode"])`
>    - `ALPACA_QUOTE_TICKS_TOTAL = Counter(..., labelnames=["endpoint", "mode"])`
>    - `ALPACA_WS_RECONNECT_TOTAL = Counter(..., labelnames=["endpoint", "reason"])`
>    - `ALPACA_SUBSCRIPTION_ACTIVE = Gauge(..., labelnames=["endpoint", "mode"])`
>    - `ALPACA_UPSTREAM_SUBSCRIBE_REJECTED_TOTAL = Counter(..., labelnames=["endpoint", "reason"])`
>    - `ALPACA_HTTP_REQUESTS_TOTAL = Counter(..., labelnames=["endpoint", "status"])`
>    - `ALPACA_HTTP_RATE_LIMIT_WINDOW_SECONDS = Gauge(..., labelnames=[])`
>    - `ALPACA_HTTP_RATE_LIMIT_REMAINING = Gauge(..., labelnames=[])`
>    - `ALPACA_ACCOUNT_READ_FAILURES_TOTAL = Counter(..., labelnames=["kind"])`
>    - `ALPACA_ENDPOINT_ISOLATION_VIOLATIONS_TOTAL = Counter(..., labelnames=[])`
>
> 7. **handlers.py** — `class AlpacaServicer(broker_pb2_grpc.BrokerServicer)`:
>    - `Configure` — accept payload, validate mode-match, call `auth.set_credentials`, return Empty (Task C1 fills real logic).
>    - `Health` — return `HealthResponse(started_at=Timestamp, mode=config.MODE)`.
>    - `ListManagedAccounts`, `GetAccountSummary`, `GetPositions`, `GetOrders`, `StreamQuotes` — return `UNIMPLEMENTED`.
>    - `PlaceOrder`, `CancelOrder`, `ModifyOrder` — return `UNIMPLEMENTED` (Phase 8).
>    - Pattern A: `except (grpc.RpcError, ValueError, RuntimeError) as exc:`.
>
> 8. **main.py** — `async def serve()` builds gRPC server, registers AlpacaServicer, binds `0.0.0.0:GRPC_PORT`, sets `ALPACA_SIDECAR_UPTIME_SECONDS` at boot, runs forever. Pattern E on init: if `auth.AuthCache()` fails, log + raise, NEVER bind port.
>
> 9. **tests/test_handlers_stub.py** — 1 test asserting Health responds with mode=MODE env. Skip-on-no-grpc-bound if Docker not available.
>
> CONVENTIONS: Python 3.14, type hints everywhere, async-only (where applicable), structlog only, ruff (E,F,W,I,N,UP,B,A,C4,ASYNC,RUF) + ruff format, mypy --strict.
>
> CODEX DEFAULTS:
> - Pattern A — `except (X, Y) as exc:` parens + binding. ruff format strips bare-tuple parens.
> - Pattern E — lazy-singleton init failure must clean up.
>
> Output "B1 done" with file count + line count + any deviations.

- [ ] **Step 2: Lint + mypy + tests + Dockerfile build + commit**

```bash
cd /home/joseph/dashboard/sidecar_alpaca && ../backend/.venv/bin/ruff check . && ../backend/.venv/bin/mypy --strict .
cd /home/joseph/dashboard/sidecar_alpaca && ../backend/.venv/bin/python -m pytest tests/ -v
docker build -t alpaca-sidecar:dev sidecar_alpaca/
git add sidecar_alpaca/
git commit -m "feat(alpaca): sidecar_alpaca/ skeleton + UNIMPLEMENTED stubs (Phase 7c B1)"
```

---

## Chunk C — Configure RPC + AlpacaClient + per-mode routing + boundary strip

**Codex patterns:** A, E.

### Task C1: AlpacaClient (read-only REST surfaces)

**Files:**
- Create: `sidecar_alpaca/client.py`, `sidecar_alpaca/normalize.py`, `sidecar_alpaca/tests/test_client.py`
- Modify: `sidecar_alpaca/handlers.py`

- [ ] **Step 1: Codex dispatch**

> --fresh
>
> Phase 7c C1: AlpacaClient + normalize.py + handler wiring.
>
> CONTEXT:
> - sidecar_schwab/client.py is the template — SDK isolation (M3): only client.py imports `alpaca-py`.
> - alpaca-py: `alpaca.trading.client.TradingClient(api_key, api_secret, paper=bool)`. Methods: `.get_account()`, `.get_all_positions()`, `.get_orders(filter)`, `.get_assets(...)`.
>
> CREATE /home/joseph/dashboard/sidecar_alpaca/client.py:
> - `class AlpacaClient` wraps TradingClient. ONLY this file imports `alpaca.*`.
> - `__init__(self, api_key, api_secret, paper: bool)` — instantiates TradingClient.
> - All async via `asyncio.to_thread`:
>   - `async def list_managed_accounts() -> list[AlpacaAccountDict]`
>   - `async def get_account_summary() -> AlpacaAccountSummaryDict`
>   - `async def get_positions() -> list[AlpacaPositionDict]`
>   - `async def get_orders() -> list[AlpacaOrderDict]`
> - All errors: `except (alpaca.common.exceptions.APIError, ConnectionError) as exc:` (Pattern A) — log + re-raise as `AlpacaClientError` dataclass.
> - Increments ALPACA_HTTP_REQUESTS_TOTAL{endpoint, status} per call.
>
> CREATE /home/joseph/dashboard/sidecar_alpaca/normalize.py:
> - `def to_proto_account(d) -> broker_pb2.Account` — set `account_id` proto field (Alpaca UUID).
> - `def to_proto_position(d) -> broker_pb2.Position` — qty/avg_cost/currency, contract.symbol/exchange/asset_class.
> - `def to_proto_order(d) -> broker_pb2.Order` — order fields + filled_qty + status.
> - `def to_proto_account_summary(d) -> broker_pb2.AccountSummary`.
>
> MODIFY /home/joseph/dashboard/sidecar_alpaca/handlers.py:
> - Replace `ListManagedAccounts` UNIMPLEMENTED: get creds → AlpacaClient → call list_managed_accounts → normalize → return.
> - Same for GetAccountSummary, GetPositions, GetOrders.
> - Wrap with `try: ... except (AlpacaClientError, RuntimeError) as exc:` (Pattern A).
>
> CREATE /home/joseph/dashboard/sidecar_alpaca/tests/test_client.py:
> - test_alpaca_client_list_accounts_normalizes — mock TradingClient.get_account, assert proto Account with account_id populated.
> - test_alpaca_client_get_positions_normalizes — mock get_all_positions, assert proto Position list.
> - test_alpaca_client_handles_api_error — mock to raise APIError, assert AlpacaClientError raised.
>
> CODEX DEFAULTS: Pattern A everywhere; Pattern E (init cleanup if TradingClient fails).
>
> Output "C1 done" with file count + test count.

- [ ] **Step 2: Lint + mypy + tests + commit**

```bash
cd /home/joseph/dashboard/sidecar_alpaca && ../backend/.venv/bin/ruff check . && ../backend/.venv/bin/mypy --strict . && ../backend/.venv/bin/python -m pytest tests/test_client.py -v
git add sidecar_alpaca/
git commit -m "feat(alpaca): AlpacaClient + normalize + read-RPC handlers (Phase 7c C1)"
```

---

### Task C2: Per-mode Configure routing in backend

**Files:**
- Modify: `backend/app/services/broker_registry.py` (or wherever Configure dispatch lives)
- Modify: `backend/app/core/metrics.py` (add `ALPACA_MODE_MISMATCH_TOTAL`)
- Create: `backend/tests/integration/test_alpaca_configure_routing.py`

- [ ] **Step 1: Pre-flight grep**

```bash
grep -rn "Configure\|broker_pb2.ConfigurePayload\|configure_total" backend/app/services/ | head -20
```

- [ ] **Step 2: Write failing test**

```python
"""Per-mode Configure routing — paper sidecar must NEVER see live creds.

Phase 7c HIGH-5.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.core.metrics import ALPACA_MODE_MISMATCH_TOTAL


@pytest.mark.asyncio
async def test_configure_lives_only_to_live_sidecar() -> None:
    """Seeding alpaca.default.live.* fires Configure to alpaca-live ONLY."""
    ...


@pytest.mark.asyncio
async def test_configure_paper_only_to_paper_sidecar() -> None:
    ...


@pytest.mark.asyncio
async def test_cross_mode_pollution_refused() -> None:
    """Backend MUST refuse to send live creds to paper sidecar.

    Asserts alpaca_mode_mismatch_total{label="alpaca-live"} increments.
    """
    before = ALPACA_MODE_MISMATCH_TOTAL.labels(label="alpaca-live")._value.get()
    # Mock Health: sidecar reports mode="paper" but registry tries to send live creds.
    after = ALPACA_MODE_MISMATCH_TOTAL.labels(label="alpaca-live")._value.get()
    assert after == before + 1
```

- [ ] **Step 3: Add metric**

```python
ALPACA_MODE_MISMATCH_TOTAL = Counter(
    "alpaca_mode_mismatch_total",
    "Backend refused to send Configure to a sidecar whose Health-reported mode "
    "did not match the gateway_label-implied mode (Phase 7c HIGH-5).",
    labelnames=["label"],
    registry=registry,
)
```

- [ ] **Step 4: Implement per-mode dispatch**

Before sending Configure:

```python
sidecar_mode = await sidecar.health()  # extract mode field
expected_mode = "live" if gateway_label.endswith("-live") else "paper"
if sidecar_mode != expected_mode:
    ALPACA_MODE_MISMATCH_TOTAL.labels(label=gateway_label).inc()
    log.error(
        "alpaca.configure_refused.mode_mismatch",
        gateway_label=gateway_label,
        sidecar_mode=sidecar_mode,
        expected_mode=expected_mode,
    )
    return
# proceed with Configure
```

For secret-rotation triggers: extract mode from the secret key (`broker.alpaca.<label>.<mode>.api_key`), dispatch Configure ONLY to the matching gateway_label.

- [ ] **Step 5: Run + commit**

```bash
cd backend && .venv/bin/python -m pytest tests/integration/test_alpaca_configure_routing.py -v
cd backend && .venv/bin/ruff check app/services/broker_registry.py app/core/metrics.py && .venv/bin/mypy --strict app/services/broker_registry.py app/core/metrics.py
git add backend/app/services/broker_registry.py backend/app/core/metrics.py backend/tests/integration/test_alpaca_configure_routing.py
git commit -m "feat(alpaca): per-mode configure routing + mode-mismatch metric (Phase 7c C2)"
```

---

### Task C3: `account_id` boundary strip + AccountResponse update

**Files:**
- Modify: `backend/app/api/accounts.py` or `backend/app/schemas/accounts.py` (grep first)
- Modify: `backend/app/services/account_service.py` (`_resolve_account`)
- Create: `backend/tests/api/test_accounts_boundary_strip.py`

- [ ] **Step 1: Pre-flight grep**

```bash
grep -rn "class AccountResponse\|account_hash\|_resolve_account" backend/app/ | grep -v __pycache__ | head -20
```

- [ ] **Step 2: Write failing test**

```python
"""Boundary-strip regression test — no broker-internal IDs leak to FE.

Phase 7c HIGH-2 (Alpaca account_id) carries forward Phase 7a M22.
"""
from __future__ import annotations

import re

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_account_response_strips_alpaca_account_id(client: AsyncClient) -> None:
    """AccountResponse JSON must NOT include any broker-internal UUID."""
    resp = await client.get("/api/accounts")
    if resp.status_code == 503:
        pytest.skip("broker layer not provisioned")
    assert resp.status_code == 200
    body = resp.json()

    uuid_pattern = re.compile(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    )
    for account in body.get("accounts", []):
        assert "account_id" not in account
        assert "alpaca_account_id" not in account
        assert "account_hash" not in account
        for key, value in account.items():
            if key == "id":
                continue  # FE handle is allowed
            if isinstance(value, str) and uuid_pattern.match(value):
                pytest.fail(
                    f"account[{key!r}] = {value!r} matches UUID — leaked broker handle",
                )
```

- [ ] **Step 3: Extend `_resolve_account`**

In `backend/app/services/account_service.py::_resolve_account`, extend the internal mapping to handle `broker_id="alpaca"` alongside Schwab. Same dict structure: `{"id": fe_uuid, "broker_id": "alpaca", "gateway_label": "alpaca-live", "account_number": account_id_or_number}`. Confirm `AccountResponse` has NO `account_id` field — only `id`, `broker_id`, `alias`, `mode`, `currency_base`, `display_order`.

- [ ] **Step 4: Run + commit**

```bash
cd backend && .venv/bin/python -m pytest tests/api/test_accounts_boundary_strip.py -v
git add backend/app/services/account_service.py backend/tests/api/test_accounts_boundary_strip.py
git commit -m "fix(accounts): strip alpaca account_id at boundary (Phase 7c C3 HIGH-2)"
```

---

## Chunk D — IEX equity streamer

**Codex patterns:** A, B (cancel + gather), C (per-callback isolation), D (bounded sets).

### Task D1: AlpacaStreamer — `_iex_loop` + supervisor + Subscribe-vs-Resync

**Files:**
- Create: `sidecar_alpaca/streamer.py`, `tests/test_streamer.py`, `tests/test_streamer_isolation.py`, `tests/test_streamer_resync.py`
- Modify: `sidecar_alpaca/handlers.py` (wire StreamQuotes RPC)

- [ ] **Step 1: Codex dispatch**

> --fresh
>
> Phase 7c D1: AlpacaStreamer (IEX equity WS) with supervisor + per-task isolation + Subscribe-vs-Resync reconnect contract.
>
> CONTEXT:
> - sidecar_schwab/streamer.py is the closest template (in-cluster docker, WSS upstream, tick→QuoteMessage callback).
> - Phase 7b.1 reconnect contract: backend sends Subscribe (full replay) on Health.started_at delta; Resync (diff-only) on gRPC-only reconnect.
> - Alpaca IEX WS: wss://stream.data.alpaca.markets/v2/iex. Auth: send `{"action":"auth","key":..., "secret":...}` after connect. Subscribe: `{"action":"subscribe","trades":[],"quotes":["AAPL","TSLA"],"bars":[]}`.
>
> CREATE /home/joseph/dashboard/sidecar_alpaca/streamer.py:
>
> ```python
> from __future__ import annotations
>
> import asyncio
> import contextlib
> from collections.abc import Callable
>
> import structlog
>
> from sidecar_alpaca import config, metrics
> from sidecar_alpaca._generated.broker.v1 import broker_pb2 as pb
>
> log = structlog.get_logger(__name__)
>
> _MAX_UPSTREAM_SYMBOLS_HARD = 30  # Alpaca free-tier cap (CRIT-1 layer 2)
>
>
> class AlpacaStreamer:
>     """Two-WS supervisor: IEX equity + crypto v1beta3 (D1 ships IEX only)."""
>
>     def __init__(
>         self,
>         tick_callback: Callable[[pb.QuoteMessage], None],
>         get_credentials: Callable[[], tuple[str, str]],
>     ) -> None:
>         self._tick_callback = tick_callback
>         self._get_creds = get_credentials
>         self._iex_active: set[str] = set()  # Pattern D — bounded set
>         self._supervisor_task: asyncio.Task[None] | None = None
>         self._iex_task: asyncio.Task[None] | None = None
>         self._stop = asyncio.Event()
>
>     async def start(self) -> None:
>         self._supervisor_task = asyncio.create_task(self._supervisor_loop())
>
>     async def stop(self) -> None:
>         """Cancel + gather (Pattern B)."""
>         self._stop.set()
>         tasks = [t for t in (self._supervisor_task, self._iex_task) if t and not t.done()]
>         for t in tasks:
>             t.cancel()
>         await asyncio.gather(*tasks, return_exceptions=True)
>
>     async def on_subscribe(self, symbols: list[str]) -> None:
>         """Backend Subscribe op — full WS reconnect + replay (CRIT-2)."""
>         requested = set(symbols)
>         allowed = list(requested - self._iex_active)
>         capacity = _MAX_UPSTREAM_SYMBOLS_HARD - len(self._iex_active)
>         if len(allowed) > capacity:
>             metrics.ALPACA_UPSTREAM_SUBSCRIBE_REJECTED_TOTAL.labels(
>                 endpoint="iex", reason="cap_exceeded",
>             ).inc()
>             allowed = allowed[:capacity]
>         self._iex_active |= set(allowed)
>         metrics.ALPACA_SUBSCRIPTION_ACTIVE.labels(endpoint="iex", mode=config.MODE).set(len(self._iex_active))
>         await self._restart_iex_loop()
>
>     async def on_resync(self, expected: list[str]) -> None:
>         """Backend Resync op — diff-only, NO disconnect (CRIT-2)."""
>         expected_set = set(expected)
>         to_add = expected_set - self._iex_active
>         to_remove = self._iex_active - expected_set
>         if to_add:
>             await self._send_ws_subscribe(list(to_add))
>         if to_remove:
>             await self._send_ws_unsubscribe(list(to_remove))
>         self._iex_active = expected_set
>         metrics.ALPACA_SUBSCRIPTION_ACTIVE.labels(endpoint="iex", mode=config.MODE).set(len(self._iex_active))
>
>     async def _supervisor_loop(self) -> None:
>         """Spawn _iex_loop with restart-on-crash isolation (HIGH-1)."""
>         backoff = 1
>         while not self._stop.is_set():
>             self._iex_task = asyncio.create_task(self._iex_loop())
>             try:
>                 await self._iex_task
>             except asyncio.CancelledError:
>                 break
>             except (Exception,) as exc:  # noqa: BLE001 — supervisor isolates child
>                 log.warning("alpaca.streamer.iex_loop_crash", exc=str(exc))
>                 metrics.ALPACA_WS_RECONNECT_TOTAL.labels(endpoint="iex", reason="loop_crash").inc()
>                 await asyncio.sleep(min(backoff, 60))
>                 backoff = min(backoff * 2, 60)
>             else:
>                 backoff = 1
>
>     async def _iex_loop(self) -> None:
>         """Codex: connect to Alpaca IEX WS using websockets lib, send auth+subscribe,
>         recv loop, parse data frames → QuoteMessage(canonical_id=raw_symbol echo,
>         source='alpaca', received_at=now), call tick_callback. Detect symbol-rejection
>         in error frames; increment ALPACA_UPSTREAM_SUBSCRIBE_REJECTED_TOTAL.
>         """
>         ...
>
>     async def _restart_iex_loop(self) -> None:
>         if self._iex_task and not self._iex_task.done():
>             self._iex_task.cancel()
>             with contextlib.suppress(asyncio.CancelledError):
>                 await self._iex_task
>         metrics.ALPACA_WS_RECONNECT_TOTAL.labels(endpoint="iex", reason="subscribe_replay").inc()
>         self._iex_task = asyncio.create_task(self._iex_loop())
>
>     async def _send_ws_subscribe(self, symbols: list[str]) -> None: ...
>     async def _send_ws_unsubscribe(self, symbols: list[str]) -> None: ...
> ```
>
> Codex: complete `_iex_loop`, `_send_ws_subscribe`, `_send_ws_unsubscribe`. Use `websockets` library. Auth frame: `{"action":"auth","key":..., "secret":...}`. Frame parser → QuoteMessage(canonical_id=raw_symbol echo for now; backend resolves), source="alpaca", received_at=now.
>
> CREATE 3 test files:
>
> 1. **tests/test_streamer.py** — IEX subscribe (mock WS) + cap-hit at 31st symbol + tick_callback receives QuoteMessage. AsyncMock for `websockets.connect`.
>
> 2. **tests/test_streamer_isolation.py** — HIGH-1. Set up streamer with both IEX + a stub crypto child task. Inject IEX 5xx exception. Assert: (a) crypto stub still ticks, (b) `ALPACA_WS_RECONNECT_TOTAL{endpoint="iex", reason="loop_crash"}` incremented, (c) supervisor restarts only IEX child.
>
> 3. **tests/test_streamer_resync.py** — CRIT-2. Pre-populate `_iex_active = {A, B}`. Call `on_resync([B, C])`. Assert: ws_subscribe called for [C], ws_unsubscribe for [A], NO disconnect/reconnect. Compare to `on_subscribe` path which DOES reconnect.
>
> MODIFY /home/joseph/dashboard/sidecar_alpaca/handlers.py:
> - Wire `StreamQuotes` RPC: bidi stream, recv ops (Subscribe/Unsubscribe/Resync), call streamer methods.
> - tick_callback closes over the gRPC servicer's send_message.
>
> CODEX DEFAULTS: Pattern A everywhere; Pattern B (cancel+gather) on stop(); Pattern C (per-callback isolation in tick_callback fan-out); Pattern D (bounded sets — `_iex_active` capped at 30 hard).
>
> Output "D1 done".

- [ ] **Step 2: Lint + mypy + tests + commit**

```bash
cd /home/joseph/dashboard/sidecar_alpaca && ../backend/.venv/bin/ruff check . && ../backend/.venv/bin/mypy --strict . && ../backend/.venv/bin/python -m pytest tests/ -v
git add sidecar_alpaca/streamer.py sidecar_alpaca/handlers.py sidecar_alpaca/tests/test_streamer*.py
git commit -m "feat(alpaca): IEX streamer with supervisor + reconnect contract (Phase 7c D1)"
```

---

## Chunk E — Crypto streamer extension

**Codex patterns:** A, B, C, D.

### Task E1: `_crypto_loop` sibling task

**Files:**
- Modify: `sidecar_alpaca/streamer.py` (add `_crypto_loop`, `_crypto_active`, `on_subscribe_crypto`, `on_resync_crypto`)
- Modify: `sidecar_alpaca/normalize.py` (`_canonical_to_alpaca_crypto` helper)
- Create: `sidecar_alpaca/tests/test_streamer_crypto.py`

- [ ] **Step 1: Codex dispatch**

> --fresh
>
> Phase 7c E1: Extend AlpacaStreamer with `_crypto_loop` (Alpaca v1beta3 crypto WS).
>
> ENDPOINT: wss://stream.data.alpaca.markets/v1beta3/crypto/us. Same auth flow as IEX. Subscribe payload shape same; `quotes` is crypto-pair list like "BTC/USD", "ETH/USD".
>
> SYMBOL FORMAT: canonical_id "crypto:BTC:US" → "BTC/USD". Add `_canonical_to_alpaca_crypto` in normalize.py.
>
> MIRROR IEX structure exactly:
> - `_crypto_active: set[str]` (bounded at 30 — CRIT-1 layer 2)
> - `_crypto_task: asyncio.Task | None`
> - `on_subscribe_crypto(symbols)` — full reconnect + replay
> - `on_resync_crypto(expected)` — diff-only
> - `_crypto_loop()` — connect, auth, recv, parse → QuoteMessage with source="alpaca", canonical_id="crypto:BTC:US"
> - Supervisor `_supervisor_loop` spawns BOTH `_iex_loop` AND `_crypto_loop` as siblings; failure of one MUST NOT cancel the other (HIGH-1).
>
> tests/test_streamer_crypto.py:
> - test_crypto_subscribe_BTC_alpaca_format — assert "BTC/USD" sent to upstream
> - test_crypto_cap_at_30_for_endpoint — separate cap from IEX
> - test_crypto_tick_routed_to_callback — mock incoming, assert QuoteMessage with canonical_id="crypto:BTC:US"
>
> Pattern C critical: IEX 5xx must not cancel crypto, vice versa.
>
> Output "E1 done".

- [ ] **Step 2: Verify isolation regression test still passes**

```bash
cd /home/joseph/dashboard/sidecar_alpaca && ../backend/.venv/bin/python -m pytest tests/test_streamer_isolation.py -v
```

Expected: PASS — IEX 5xx doesn't cancel crypto child.

- [ ] **Step 3: Commit**

```bash
git add sidecar_alpaca/streamer.py sidecar_alpaca/normalize.py sidecar_alpaca/tests/test_streamer_crypto.py
git commit -m "feat(alpaca): crypto v1beta3 streamer + canonical-pair mapping (Phase 7c E1)"
```

---

## Chunk F — Per-source cap + drift detection

**Codex patterns:** A, F (metric labels verbatim).

### Task F1: SubscriptionRegistry per-source soft cap

**Files:**
- Modify: `backend/app/services/quotes/registry.py`
- Modify: `backend/app/core/metrics.py` (extend `quote_subscription_cap_rejected_total` cap_kind values)
- Modify: `backend/tests/unit/test_subscription_registry.py`

- [ ] **Step 1: Pre-flight read**

```bash
sed -n '1,100p' backend/app/services/quotes/registry.py
```

- [ ] **Step 2: Write failing test**

```python
@pytest.mark.asyncio
async def test_per_source_soft_cap_at_25() -> None:
    registry = SubscriptionRegistry(...)
    for i in range(25):
        await registry.subscribe("ws-1", f"crypto:SYM{i}:US", source="alpaca")
    with pytest.raises(CapExceeded, match="per_source"):
        await registry.subscribe("ws-1", "crypto:SYM26:US", source="alpaca")
    assert QUOTE_SUBSCRIPTION_CAP_REJECTED_TOTAL.labels(
        cap_kind="per_source", source="alpaca", asset_class="crypto",
    )._value.get() >= 1


@pytest.mark.asyncio
async def test_per_source_cap_decrements_on_unsubscribe() -> None:
    registry = SubscriptionRegistry(...)
    for i in range(25):
        await registry.subscribe("ws-1", f"crypto:SYM{i}:US", source="alpaca")
    await registry.unsubscribe("ws-1", "crypto:SYM0:US")
    await registry.subscribe("ws-1", "crypto:SYM25:US", source="alpaca")
```

- [ ] **Step 3: Add `_per_source_refs` + cap logic**

```python
_MAX_PER_SOURCE = 25  # Phase 7c CRIT-1 layer 1
_MAX_SOURCES = 32     # Pattern D — bounded refcount table


class SubscriptionRegistry:
    def __init__(self, ...) -> None:
        ...
        self._per_source_refs: dict[str, int] = {}

    async def subscribe(self, ws_id: str, canonical_id: str, *, source: str) -> None:
        async with self._lock:
            ...  # existing per_ws + global checks
            if self._per_source_refs.get(source, 0) >= _MAX_PER_SOURCE:
                QUOTE_SUBSCRIPTION_CAP_REJECTED_TOTAL.labels(
                    cap_kind="per_source",
                    source=source,
                    asset_class=canonical_id.split(":", 1)[0],
                ).inc()
                raise CapExceeded("per_source")
            if len(self._per_source_refs) >= _MAX_SOURCES and source not in self._per_source_refs:
                log.warning("registry.per_source_dict_full", source=source)
                raise CapExceeded("per_source_table")
            self._per_source_refs[source] = self._per_source_refs.get(source, 0) + 1
            ...

    async def unsubscribe(self, ws_id: str, canonical_id: str) -> None:
        async with self._lock:
            ...  # existing decrements
            source = self._route_for(canonical_id)
            if source and source in self._per_source_refs:
                self._per_source_refs[source] -= 1
                if self._per_source_refs[source] <= 0:
                    del self._per_source_refs[source]
```

- [ ] **Step 4: Extend cap_kind label set on existing metric**

In `backend/app/core/metrics.py`, extend `QUOTE_SUBSCRIPTION_CAP_REJECTED_TOTAL` labels to include `source` and `asset_class` (was 1-label `cap_kind`). NOT a new metric — same name, expanded labels (Pattern F + MED-4).

- [ ] **Step 5: Run + commit**

```bash
cd backend && .venv/bin/python -m pytest tests/unit/test_subscription_registry.py -v
git add backend/app/services/quotes/registry.py backend/app/core/metrics.py backend/tests/unit/test_subscription_registry.py
git commit -m "feat(quotes): per-source soft cap + cap_kind=per_source label (Phase 7c F1)"
```

---

### Task F2: Subscribe-rejection drift detection (HIGH-6)

**Files:**
- Modify: `sidecar_alpaca/streamer.py`
- Modify: `backend/app/services/quotes/upstream/sidecar_stream.py`
- Create: `sidecar_alpaca/tests/test_streamer_drift.py`

- [ ] **Step 1: Codex dispatch**

> --fresh
>
> Phase 7c F2: Subscribe-rejection drift detection (HIGH-6).
>
> 1. sidecar_alpaca/streamer.py — when Alpaca's WS responds with an error frame after `subscribe`, OR a subscribed symbol does not appear in any data frame within 5s:
>    - Increment ALPACA_UPSTREAM_SUBSCRIBE_REJECTED_TOTAL{endpoint, reason}. Reasons: "cap_exceeded" / "entitlement" / "unknown".
>    - Remove the symbol from `_iex_active` / `_crypto_active`.
>    - Send a gRPC error response to backend so registry can decrement.
>
> 2. backend/app/services/quotes/upstream/sidecar_stream.py — on receiving SIDECAR_SOURCE_CAP / DRIFT error, decrement registry's per-source counter and emit structured log.
>
> tests/test_streamer_drift.py — feed mock WS error frame; assert metric increment + symbol removed.
>
> CODEX DEFAULTS: Pattern A; Pattern F (label values verbatim — "cap_exceeded" / "entitlement" / "unknown" only).

- [ ] **Step 2: Lint + mypy + tests + commit**

```bash
git add sidecar_alpaca/streamer.py sidecar_alpaca/tests/test_streamer_drift.py backend/app/services/quotes/upstream/sidecar_stream.py
git commit -m "feat(alpaca): subscribe-rejection drift detection (Phase 7c F2 HIGH-6)"
```

---

## Chunk G — Source-router defaults via config_defaults.py

**Codex patterns:** A, F.

### Task G1: `config_defaults.py` + per-key merge

**Files:**
- Create: `backend/app/services/config_defaults.py`
- Modify: `backend/app/services/config_service.py` (per-key merge at load)
- Modify: `backend/app/services/quotes/router.py` (consume merged defaults)
- Create: `backend/tests/integration/test_quote_source_priority_per_key_merge.py`

- [ ] **Step 1: Write failing test**

```python
"""Per-key merge precedence — operator partial override doesn't drop new defaults.

Phase 7c HIGH-3.
"""
from __future__ import annotations

import pytest

from app.services.config_defaults import DEFAULT_QUOTE_SOURCE_PRIORITY
from app.services.config_service import ConfigService


@pytest.mark.asyncio
async def test_per_key_merge_keeps_new_defaults() -> None:
    """Operator overrode stock.UK months ago. New crypto.US default applies."""
    operator_override = {"stock.UK": ["ibkr"]}
    cfg = ConfigService(...)
    await cfg.set_config("quote_source_priority", operator_override)

    effective = await cfg.get_effective_quote_source_priority()
    assert effective["stock.UK"] == ["ibkr"]
    assert effective["crypto.US"] == DEFAULT_QUOTE_SOURCE_PRIORITY["crypto.US"]
    assert effective["crypto.US"][0] == "alpaca"


@pytest.mark.asyncio
async def test_no_operator_override_returns_pure_defaults() -> None:
    cfg = ConfigService(...)
    effective = await cfg.get_effective_quote_source_priority()
    assert effective == DEFAULT_QUOTE_SOURCE_PRIORITY
```

- [ ] **Step 2: Implement `config_defaults.py`**

```python
"""Static default tables for app_config (Phase 7c HIGH-3).

Compile-time defaults; operator can override per-key via POST /api/admin/config.
ConfigService merges per-key (NOT whole-table) so a partial override doesn't
drop new defaults shipped in later phases.
"""
from __future__ import annotations

from typing import Final


DEFAULT_QUOTE_SOURCE_PRIORITY: Final[dict[str, list[str]]] = {
    "stock.US": ["schwab", "alpaca", "ibkr"],
    "etf.US": ["schwab", "alpaca", "ibkr"],
    "index.US": ["schwab", "ibkr"],
    "crypto.US": ["alpaca"],          # 7b.2 will append "coinbase"
    "stock.UK": ["ibkr", "yfinance"],
    "stock.HK": ["futu"],
    "etf.HK": ["futu"],
    "warrant.HK": ["futu"],
    "cbbc.HK": ["futu"],
    "index.HK": ["futu"],
    "stock.EU": ["yfinance"],
    "stock.JP": ["yfinance"],
    "stock.AU": ["yfinance"],
    "stock.CA": ["yfinance"],
    "index.EU": ["ibkr"],
    "forex": [],                      # 7b.2 ships oanda
}
```

- [ ] **Step 3: Modify `ConfigService.get_effective_quote_source_priority()`**

```python
async def get_effective_quote_source_priority(self) -> dict[str, list[str]]:
    override = await self.get_config("quote_source_priority", default={})
    return {
        k: override.get(k, DEFAULT_QUOTE_SOURCE_PRIORITY[k])
        for k in DEFAULT_QUOTE_SOURCE_PRIORITY
    }
```

- [ ] **Step 4: Modify `SourceRouter` to consume merged defaults**

In `backend/app/services/quotes/router.py`, change `_priority_list_for(asset_class, country)` to read from `self._config_service.get_effective_quote_source_priority()` (cached) instead of `self._config["quote_source_priority"]`.

- [ ] **Step 5: Run + commit**

```bash
cd backend && .venv/bin/python -m pytest tests/integration/test_quote_source_priority_per_key_merge.py -v
git add backend/app/services/config_defaults.py backend/app/services/config_service.py backend/app/services/quotes/router.py backend/tests/integration/test_quote_source_priority_per_key_merge.py
git commit -m "feat(quotes): per-key merge for quote_source_priority defaults (Phase 7c G1)"
```

---

### Task G2: Frontend broker picker — alpaca entry

**Files:**
- Modify: `frontend/src/services/types.ts`
- Modify: wherever broker list is enumerated for UI (grep first)

- [ ] **Step 1: Pre-flight grep**

```bash
grep -rn "'ibkr'\s*\|\s*'futu'\|brokers:.*\[.*ibkr" frontend/src/ | head -10
```

- [ ] **Step 2: Add `'alpaca'` to BrokerId Literal**

In `frontend/src/services/types.ts`:

```typescript
export type BrokerId = 'ibkr' | 'futu' | 'schwab' | 'alpaca';
```

- [ ] **Step 3: Add display entry in broker picker / fixtures**

Wherever brokers are enumerated for UI, add `{ id: 'alpaca', name: 'Alpaca' }`.

- [ ] **Step 4: Run frontend checks + commit**

```bash
cd frontend && pnpm typecheck && pnpm test --run
git add frontend/src/services/types.ts frontend/src/components/...
git commit -m "feat(frontend): register alpaca broker in picker (Phase 7c G2)"
```

---

### Task G3: SourceRouter integration test — 4 cases (MED-1)

**Files:**
- Create: `backend/tests/integration/test_alpaca_routing.py`

- [ ] **Step 1: Write 4 tests**

```python
"""SourceRouter integration test — Phase 7c MED-1 4-case coverage."""
from __future__ import annotations

import pytest

from app.services.quotes.engine import QuoteEngine


@pytest.mark.asyncio
async def test_happy_path_routing() -> None:
    """crypto:BTC:US → alpaca; stock.US → schwab (alpaca only fallback)."""
    engine = QuoteEngine(...)
    assert await engine._route_for("crypto:BTC:US") == "alpaca"
    assert await engine._route_for("stock:AAPL:US") == "schwab"


@pytest.mark.asyncio
async def test_schwab_down_reroutes_stock_us_to_alpaca() -> None:
    engine = QuoteEngine(...)
    engine._source_health.mark_unhealthy("schwab")
    assert await engine._route_for("stock:AAPL:US") == "alpaca"


@pytest.mark.asyncio
async def test_both_schwab_alpaca_down_returns_none() -> None:
    """No coinbase fallback for equity until 7b.2."""
    engine = QuoteEngine(...)
    engine._source_health.mark_unhealthy("schwab")
    engine._source_health.mark_unhealthy("alpaca")
    assert await engine._route_for("stock:AAPL:US") is None


@pytest.mark.asyncio
async def test_per_source_cap_hit_rejects_at_registry() -> None:
    """26th alpaca crypto sub rejected at registry; SourceRouter NOT consulted."""
    engine = QuoteEngine(...)
    for i in range(25):
        await engine.subscribe("ws-1", f"crypto:SYM{i}:US")
    with pytest.raises(CapExceeded):
        await engine.subscribe("ws-1", "crypto:SYM26:US")
```

- [ ] **Step 2: Run + commit**

```bash
cd backend && .venv/bin/python -m pytest tests/integration/test_alpaca_routing.py -v
git add backend/tests/integration/test_alpaca_routing.py
git commit -m "test(alpaca): SourceRouter 4-case integration (Phase 7c G3 MED-1)"
```

---

## Chunk H — Compose + tests + runbook + close-out

### Task H1: docker-compose.prod.yml services

**Files:** Modify `docker-compose.prod.yml`.

- [ ] **Step 1: Add `alpaca-sidecar-live` + `alpaca-sidecar-paper`**

```yaml
alpaca-sidecar-live:
  build:
    context: ./sidecar_alpaca
    dockerfile: Dockerfile
  environment:
    MODE: live
    BACKEND_ADMIN_GRPC: backend:8001
    GRPC_PORT: "9091"
    ALPACA_ACCOUNT_LABEL: default
  networks: [td-net]
  restart: unless-stopped
  healthcheck:
    test: ["CMD", "python", "-c", "import socket; s=socket.socket(); s.connect(('localhost', 9091))"]
    interval: 30s
    timeout: 5s
    retries: 3

alpaca-sidecar-paper:
  build:
    context: ./sidecar_alpaca
    dockerfile: Dockerfile
  environment:
    MODE: paper
    BACKEND_ADMIN_GRPC: backend:8001
    GRPC_PORT: "9092"
    ALPACA_ACCOUNT_LABEL: default
  networks: [td-net]
  restart: unless-stopped
  healthcheck:
    test: ["CMD", "python", "-c", "import socket; s=socket.socket(); s.connect(('localhost', 9092))"]
    interval: 30s
    timeout: 5s
    retries: 3
```

Add both services to backend's `depends_on:` list with `condition: service_healthy`.

- [ ] **Step 2: docker compose config dry-run + commit**

```bash
docker compose -f docker-compose.prod.yml config | grep -A 20 "alpaca-sidecar"
git add docker-compose.prod.yml
git commit -m "feat(deploy): docker-compose alpaca-sidecar live + paper services (Phase 7c H1)"
```

---

### Task H2: Operator runbook

**Files:** Create `deploy/runbook-alpaca-setup.md`.

- [ ] **Step 1: Write runbook**

```markdown
# Alpaca Adapter Operator Runbook (Phase 7c)

## Overview

Two in-cluster Docker sidecars (`alpaca-sidecar-live`, `alpaca-sidecar-paper`)
on `td-net` bridge. API-key auth (no OAuth, no token rotation). Free-tier
Alpaca data — 30-symbol cap per WS endpoint.

## Step 0: No CF Access bypass needed

Unlike Schwab, Alpaca uses long-lived API keys with no OAuth callback. All
sidecar↔Alpaca traffic is outbound from the docker network.

## Step 1: Generate API keys

1. Log into <https://app.alpaca.markets/account/api-keys>.
2. Generate a paper-trading key pair (paper account required for testing).
3. Generate a live-trading key pair when ready.

## Step 2: Seed app_secrets

```bash
# Live
curl -X PUT https://dashboard.kiusinghung.com/api/admin/secrets/broker/alpaca.default.live.api_key \
  -H "Content-Type: application/json" \
  -d '{"value": "PKxxxxxxxxxxxxxxxxxx", "value_type": "str"}'

curl -X PUT https://dashboard.kiusinghung.com/api/admin/secrets/broker/alpaca.default.live.api_secret \
  -H "Content-Type: application/json" \
  -d '{"value": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "value_type": "str"}'

# Paper (same shape)
```

## Step 3: Bring up sidecars

```bash
docker compose -f docker-compose.prod.yml up -d alpaca-sidecar-live alpaca-sidecar-paper
docker compose -f docker-compose.prod.yml restart backend
```

## Step 4: Smoke — accounts

```bash
curl https://dashboard.kiusinghung.com/api/accounts | jq '.accounts[] | select(.broker_id=="alpaca")'
curl https://dashboard.kiusinghung.com/api/accounts/<id>/positions | jq
```

## Step 5: Smoke — quotes

```bash
wscat -c wss://dashboard.kiusinghung.com/ws/quotes -H "Cf-Access-Jwt-Assertion: $JWT"
# > {"op":"sub","symbols":["crypto:BTC:US"]}
# Expect frames within 5s.
```

## Operations

### Symbol-cap hit (≥25 active)

Alert `AlpacaSymbolCapNear` fires. Two options:
1. Prune subscriptions.
2. Upgrade to Algo Trader Plus ($99/mo); update `_MAX_PER_SOURCE` in
   `backend/app/services/quotes/registry.py`.

### Subscribe rejected by Alpaca (HIGH-6)

Alert `AlpacaUpstreamSubscribeRejection` fires. Lower `_MAX_PER_SOURCE` to
`(rejected_count - 5)`, redeploy, root-cause via Alpaca support.

### Cross-mode pollution probe (HIGH-5)

`alpaca_mode_mismatch_total{label}` should always be 0 in steady state.
Non-zero means backend tried to send mismatched-mode creds — investigate
`BrokerRegistry`.

### Key rotation

```bash
curl -X PUT https://dashboard.kiusinghung.com/api/admin/secrets/broker/alpaca.default.live.api_key \
  -d '{"value": "PKnewxxx...", "value_type": "str"}'
```

Backend fires `Configure` to `alpaca-sidecar-live` ONLY.

### Sidecar restart

```bash
docker compose -f docker-compose.prod.yml restart alpaca-sidecar-live
```

Backend's `Health.started_at` delta refires `Configure` within ~30s.

## Limits reference

- Free tier: 30 symbols/endpoint, 1 conn/endpoint, 200 REST/min.
- Soft cap (backend): 25 symbols/source.
- Sidecar hard cap: 30/endpoint.
```

- [ ] **Step 2: Commit**

```bash
git add deploy/runbook-alpaca-setup.md
git commit -m "docs(runbook): alpaca operator setup (Phase 7c H2)"
```

---

### Task H3: Test audit + full suite run

- [ ] **Step 1: Audit expected tests**

```bash
ls -la backend/tests/api/test_accounts_boundary_strip.py \
       backend/tests/integration/test_alpaca_configure_routing.py \
       backend/tests/integration/test_alpaca_routing.py \
       backend/tests/integration/test_quote_source_priority_per_key_merge.py \
       backend/tests/unit/test_broker_id_literal.py \
       backend/tests/unit/test_broker_dial.py 2>&1

ls -la sidecar_alpaca/tests/test_streamer.py \
       sidecar_alpaca/tests/test_streamer_isolation.py \
       sidecar_alpaca/tests/test_streamer_resync.py \
       sidecar_alpaca/tests/test_streamer_crypto.py \
       sidecar_alpaca/tests/test_streamer_drift.py \
       sidecar_alpaca/tests/test_handlers_stub.py \
       sidecar_alpaca/tests/test_client.py 2>&1
```

- [ ] **Step 2: Run alpaca-tagged tests + lint + mypy strict**

```bash
cd backend && .venv/bin/python -m pytest tests/ -k alpaca -v
cd /home/joseph/dashboard/sidecar_alpaca && ../backend/.venv/bin/python -m pytest tests/ -v
cd backend && .venv/bin/ruff check app/ && .venv/bin/mypy --strict app/
cd /home/joseph/dashboard/sidecar_alpaca && ../backend/.venv/bin/ruff check . && ../backend/.venv/bin/mypy --strict .
```

Expected: clean.

---

### Task H4: Close-out — CHANGELOG + TASKS + memory + tag

**Files:**
- Modify: `CHANGELOG.md`, `TASKS.md`, `CLAUDE.md`
- Create: `~/.claude/projects/-home-joseph-dashboard/memory/phase7c_alpaca_topology.md`
- Modify: `~/.claude/projects/-home-joseph-dashboard/memory/MEMORY.md`

- [ ] **Step 1: CHANGELOG `[0.7.3]` entry** (under `[Unreleased]`):

```markdown
## [0.7.3] — 2026-05-XX

### Phase 7c — Alpaca adapter

- New `sidecar_alpaca/` Python package, in-cluster Docker on `td-net`,
  insecure-port 9091 (live) / 9092 (paper). API-key auth via app_secrets
  with forward-compat `<account_label>` schema. SDK isolation: only
  `client.py` imports `alpaca-py` (M3).
- Two upstream WS connections per sidecar — IEX equity + crypto v1beta3 —
  with per-task isolation supervisor (HIGH-1). Failure of one endpoint
  cannot cancel the other; verified by `test_streamer_isolation.py`.
- Two-layer 30-symbol cap (CRIT-1): backend `SubscriptionRegistry` soft
  cap at 25 + sidecar `_upstream_active` hard cap at 30.
  `quote_subscription_cap_rejected_total` gains `cap_kind=per_source`
  label value plus `source` + `asset_class`.
- Subscribe vs Resync reconnect contract (CRIT-2): full WS reconnect on
  Subscribe, diff-only on Resync.
- Per-mode Configure routing (HIGH-5): paper sidecar never sees live
  creds; cross-mode probe fires `alpaca_mode_mismatch_total{label}`.
- New `app/services/config_defaults.py` + per-key merge in
  `ConfigService.get_effective_quote_source_priority` (HIGH-3).
- Source-router default: `crypto.US` primary → alpaca; `stock.US`/
  `etf.US` fallback after schwab.
- New `app_config.broker_gateway_dial` table (HIGH-4).
- `account_id` boundary strip (HIGH-2) at `AccountService._resolve_account`.
- Subscribe-rejection drift detection (HIGH-6).
- 11 new metrics (`alpaca_*` family + extended `quote_subscription_cap_*`).
- 6 new alerts in `phase7c_alpaca` group.
- 1 operator runbook (`deploy/runbook-alpaca-setup.md`).
- Trade execution remains UNIMPLEMENTED — Phase 8.
```

- [ ] **Step 2: TASKS.md** — change Phase 7c heading to `*(complete — v0.7.3 · 2026-05-XX)*`, check off all chunk rows.

- [ ] **Step 3: CLAUDE.md** — add to phase-shipped section:

```markdown
- `phase7c_alpaca_topology.md` — Alpaca adapter + 30-symbol cap mechanics + per-mode Configure (v0.7.3)
```

- [ ] **Step 4: Memory file** `~/.claude/projects/-home-joseph-dashboard/memory/phase7c_alpaca_topology.md` — full close-out summary (sections: What shipped, Topology, Auth, Caps, Reconnect contract, Forward pointers).

- [ ] **Step 5: MEMORY.md index entry**:

```markdown
- [Phase 7c Alpaca topology (v0.7.3 · 2026-05-XX)](phase7c_alpaca_topology.md) — sidecar_alpaca/ in-cluster docker (live + paper) + 30-symbol two-layer cap + per-mode Configure routing
```

- [ ] **Step 6: Commit + tag + push**

```bash
git add CHANGELOG.md TASKS.md CLAUDE.md
git commit -m "docs(phase7c): v0.7.3 close-out — changelog + claude.md + tasks.md"
git tag -a v0.7.3 -m "Phase 7c — Alpaca adapter"
git push --follow-tags
```

---

## Self-review

**Spec coverage:**

| Spec section | Plan task | Covered? |
|---|---|---|
| §3.1 Topology | A1, A2, B1, H1 | ✓ |
| §3.2 Auth + per-mode routing | B1 (auth.py), C2 (HIGH-5) | ✓ |
| §3.3 Source-router default + per-key merge | G1 (HIGH-3) | ✓ |
| §4.1 sidecar_alpaca/ layout | B1, C1, D1, E1 | ✓ |
| §4.1 Two-WS supervisor + isolation (HIGH-1) | D1 + E1 + test_streamer_isolation | ✓ |
| §4.1.1 Subscribe vs Resync (CRIT-2) | D1 + test_streamer_resync | ✓ |
| §4.2 Mode-split deployment + dial (HIGH-4) | A2 + H1 | ✓ |
| §4.3 Two-layer cap (CRIT-1) | F1 (backend) + D1/E1 (sidecar) | ✓ |
| §4.4 Cap visibility + drift (HIGH-6) | F2 + alert in §7 | ✓ |
| §4.5 Backend changes + boundary strip (HIGH-2) | A1, C3, G2 | ✓ |
| §4.6 Tests | A1, B1, C1, C2, C3, D1, E1, F1, F2, G1, G3 | ✓ |
| §4.7 Operator runbook | H2 | ✓ |
| §5 Critical numbers | F1 (25 soft) + D1 (30 hard) | ✓ |
| §6 Metrics | B1 + C2 + F1/F2 | ✓ |
| §7 Alerts | H2 (operator wires `alerts.yml` post-deploy) | partial — manual step |
| §8 Risks | mitigations cross-reference into chunks | ✓ |
| §9 Codex routing | each chunk header lists patterns | ✓ |
| §10 Chunk plan A-H | this plan's chunks | ✓ |
| §11 Forward pointers | H4 close-out memory | ✓ |
| §12 Deferred | H4 close-out CHANGELOG | ✓ |

**Placeholder scan:** No "TBD" / "TODO" / "fill in later" left. Code blocks present where needed; Codex prompts include actual signatures + algorithm.

**Type consistency:** `AlpacaStreamer`, `AlpacaClient`, `AlpacaServicer`, `AuthCache`, `_iex_active`, `_crypto_active`, `_per_source_refs`, `BrokerId`, `resolve_dial`, `DEFAULT_QUOTE_SOURCE_PRIORITY`, `get_effective_quote_source_priority` — used consistently throughout.

**Architect findings → chunk mapping:**

| Finding | Chunk |
|---|---|
| CRIT-1 two-layer cap | F1 + D1/E1 |
| CRIT-2 Subscribe vs Resync | D1 |
| HIGH-1 per-task isolation | D1 + test_streamer_isolation |
| HIGH-2 account_id strip | C3 |
| HIGH-3 per-key merge | G1 |
| HIGH-4 broker_gateway_dial table | A2 |
| HIGH-5 per-mode Configure | C2 |
| HIGH-6 subscribe-rejection detection | F2 |
| MED-1 4-case routing test | G3 |
| MED-2 forward-compat schema | B1 + H2 |
| MED-3 rate-limit metrics | B1 |
| MED-4 extend cap_kind not split | F1 |
| MED-5 Codex pattern routing per chunk | this plan's chunk headers |
| MED-6 secrets test full-matrix | C2 |
| MED-7 runbook step 0 | H2 |

---

## Execution handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-05-phase7c-alpaca-adapter-plan.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — Dispatch fresh Codex agents for the heavy chunks (B1, C1, D1, E1, F2 single-Codex-task chunks); Opus-direct for A1, A2, C2, C3, F1, G1, G2, G3, H1-H4 (smaller, mostly verification + wiring). Review between chunks.

**2. Inline Execution** — Execute tasks in this session via `superpowers:executing-plans`, batched with checkpoints at chunk boundaries.

**Which approach?**
