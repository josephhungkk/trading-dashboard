# Phase 11d — Telegram Trade Execution Design

## Goal

Add `/place_order` trade execution to the Telegram bot: a two-step confirmation flow (preview → `/confirm`) that integrates with the existing `orders_service` risk gate and broker dispatch pipeline without modifying any web API code.

## Architecture

### Approach

Telegram-native state machine in a new `order_flow.py` module. On `/place_order`, the handler resolves the instrument and account, calls `orders_service.preview_order` directly (service layer, not HTTP), stores resolved params in Redis under a 60s TTL key, and replies with the preview. On `/confirm`, the handler atomically GETDEL's that key, mints a short-lived (10s) web-compatible nonce directly into Redis, and calls `orders_service.place_order` — which runs the full risk gate + PDT/BP counters + broker dispatch unchanged.

This approach keeps `orders_service.py` and all web API endpoints completely untouched. The Telegram GETDEL is the real single-use gate; the 10s web nonce satisfies the existing `place_order` API contract without bypassing it.

### New files

| File | Responsibility |
|---|---|
| `backend/app/services/telegram/order_flow.py` | Parse, resolve, preview, confirm, cancel state machine |
| `backend/tests/services/telegram/test_order_flow.py` | Unit tests (no_db; mocked Redis + orders_service) |

### Modified files

| File | Change |
|---|---|
| `backend/app/services/telegram/commands.py` | Add `/place_order`, `/confirm`, `/cancel_order` handlers + account-selection plain-text handler |
| `backend/app/services/telegram/rate_limiter.py` | Add `check_trade` bucket (5/min) |
| `backend/app/main.py` | Pass `registry`, `capability`, `cfg` into `register_handlers` |

## Command Syntax

```
/place_order <SYMBOL> <BUY|SELL> <QTY> [--limit <price>] [--stop <price>] [--tif DAY|GTC]
/confirm
/cancel_order
```

**Parsing rules:**
- `<SYMBOL>` — 1–10 alphanumeric + `.` chars; uppercased
- `<QTY>` — positive value matching `DECIMAL_10_PATTERN`
- `--limit <price>` alone → `order_type = LIMIT`
- `--stop <price>` + `--limit <price>` → `order_type = STOP_LIMIT`
- `--stop` without `--limit` → rejected (stop-market not supported); reply with usage hint
- `--tif DAY|GTC` → default `DAY`
- Unknown flags → rejected with usage hint
- Returns `ParsedOrder` dataclass or `None` on failure

## Instrument Resolution

1. Query `instruments WHERE ticker = :symbol AND broker_id = :broker_id LIMIT 1`
2. If not found: call `registry.get_client(broker_label)` → `client.search_contracts(symbol)` → take first equity match → insert into `instruments` → return `conid`
3. If still not found: return `None` → reply "Unknown symbol — trade it via the web first to register it."

## Account Selection

- Query `broker_accounts WHERE deleted_at IS NULL ORDER BY display_order`
- **0 results** → reply "No active accounts found."
- **1 result** → use silently, proceed to preview
- **>1 result** → write `tg:acct_select:{chat_id}:{from_user_id}` (120s TTL, JSON list of accounts) → reply numbered list:
  ```
  Multiple accounts — reply with a number:
  1. IBKR1 (IBKR) [paper] USD
  2. FUTU1 (Futu) [live] HKD
  ```

**Account selection reply handler** (plain text matching `^[0-9]+$` when `tg:acct_select` key exists):
- Valid index → GETDEL acct_select key → resolve account → proceed to preview → write `tg:pending_order`
- Invalid / out-of-range → reply error; key persists (user can retry)
- This handler runs before the AI chat catch-all; acct_select key presence is the discriminator

## Redis State Machine

| Key | TTL | Set by | Consumed by |
|---|---|---|---|
| `tg:pending_order:{chat_id}:{from_user_id}` | 60s | `/place_order` (after account resolved + preview) | `/confirm` (GETDEL) |
| `tg:acct_select:{chat_id}:{from_user_id}` | 120s | `/place_order` (when >1 account) | Account selection reply (GETDEL) |
| `tg:rl:trade:{chat_id}:{from_user_id}` | sliding 60s | `check_trade` | `check_trade` (sorted-set sliding window) |

**State transitions:**

```
IDLE
  → /place_order
      → 0 accounts: reply error → IDLE
      → 1 account: preview → write pending_order → PENDING_CONFIRM
      → >1 account: write acct_select → reply list → ACCT_SELECT

ACCT_SELECT (tg:acct_select exists)
  → plain-number reply: GETDEL acct_select → preview → write pending_order → PENDING_CONFIRM
  → /cancel_order: DEL acct_select → reply "Cancelled" → IDLE
  → TTL expiry: auto-IDLE

PENDING_CONFIRM (tg:pending_order exists)
  → /confirm: GETDEL pending_order → mint 10s web nonce → place_order → reply result → IDLE
  → /cancel_order: DEL pending_order → reply "Cancelled" → IDLE
  → new /place_order: DEL old pending_order → restart flow
  → TTL expiry: auto-IDLE
```

## Confirm Flow (Detail)

On `/confirm` with a valid GETDEL:

1. Reconstruct `PreviewRequest`-equivalent from stored params
2. Compute `payload_hash` using the same algorithm as `orders_service._preview_payload_hash` (SHA-256 of canonical JSON `{account_id, conid, side, order_type, tif, qty, limit_price, stop_price}`). Either import the private function or inline the equivalent — implementation decision at code time.
3. Mint web nonce: `SET nonce:order:{account_id}:{nonce_uuid} {payload_json} EX 10 NX`
4. Assemble `PlaceOrderRequest` dict with `nonce=nonce_uuid`, `client_order_id=uuid4()`
5. Call `orders_service.place_order(cfg, db, redis, registry, capability, request_data)`
6. On success → reply `"✅ Order placed — ID: {order_id}"`
7. On `RiskGateBlockedError` → reply escaped blocker list; order not placed; no nonce re-minted
8. On `PreviewUnavailable(503)` → reply maintenance message
9. On any other exception → log + reply "Order failed, try again."

## Rate Limiting

Three buckets; all use the existing sliding-window sorted-set pattern (`ZADD` / `ZREMRANGEBYSCORE` / `ZCARD`):

| Command | Buckets checked |
|---|---|
| `/place_order` | write (3/min) AND trade (5/min) |
| `/confirm` | write (3/min) AND trade (5/min) |
| Account selection reply | write (3/min) AND trade (5/min) |
| `/cancel_order` | write (3/min) only |

New `TelegramRateLimiter` method: `check_trade(*, chat_id: int, from_user_id: int) -> bool`
Key: `telegram:rl:trade:{chat_id}:{from_user_id}`, limit 5, window 60s.

Both buckets must pass; fail-open on Redis error (same as existing buckets).

## Security Properties

| Property | Mechanism |
|---|---|
| Single-use pending order | `tg:pending_order` GETDEL — atomic |
| Transport-mismatch reject | Key only mintable inside Telegram handler; no HTTP path can write it |
| 60s order expiry | TTL on `tg:pending_order` |
| 10s web nonce | Minted immediately before `place_order`; consumed atomically within same call |
| Replay protection | GETDEL removes key before dispatch; duplicate `/confirm` gets nil |
| Authorization | `AllowlistEntry` required for all commands; single-tenant |
| Risk gate | Unconditional in `orders_service.place_order` at station 4 — cannot be bypassed |
| Input sanitisation | All user strings `html.escape()`'d in replies; qty/prices regex-validated before storing |
| Rate limiting | Belt-and-suspenders: write bucket + trade bucket for all order commands |

## `order_flow.py` Public Interface

```python
@dataclass
class ParsedOrder:
    symbol: str
    side: Literal["BUY", "SELL"]
    qty: str
    order_type: Literal["MARKET", "LIMIT", "STOP_LIMIT"]
    tif: Literal["DAY", "GTC"]
    limit_price: str | None
    stop_price: str | None

def parse_place_order(text: str) -> ParsedOrder | None: ...

async def resolve_instrument(
    symbol: str, *, db: AsyncSession, registry: Any, broker_label: str
) -> str | None:  # returns conid or None
    ...

async def handle_place_order(
    msg: Message,
    *,
    entry: AllowlistEntry,
    db: AsyncSession,
    redis: Any,
    registry: Any,
    capability: Any,
    cfg: Any,
) -> None: ...

async def handle_confirm(
    msg: Message,
    *,
    entry: AllowlistEntry,
    db: AsyncSession,
    redis: Any,
    registry: Any,
    capability: Any,
    cfg: Any,
) -> None: ...

async def handle_cancel_order(
    msg: Message,
    *,
    entry: AllowlistEntry,
    redis: Any,
) -> None: ...

async def handle_account_selection(
    msg: Message,
    *,
    entry: AllowlistEntry,
    db: AsyncSession,
    redis: Any,
    registry: Any,
    capability: Any,
    cfg: Any,
) -> bool:  # True if the message was consumed as account selection
    ...
```

## Test Plan

**`test_order_flow.py`** (`pytestmark = pytest.mark.no_db`):

| Test | Covers |
|---|---|
| `test_parse_market_order` | symbol/side/qty → MKT, DAY |
| `test_parse_limit_order` | `--limit` → LIMIT |
| `test_parse_stop_limit_order` | `--stop` + `--limit` → STOP_LIMIT |
| `test_parse_stop_only_rejected` | `--stop` without `--limit` → None |
| `test_parse_invalid_qty` | non-numeric qty → None |
| `test_parse_unknown_flag` | unknown flag → None |
| `test_resolve_instrument_from_db` | found in instruments → conid returned |
| `test_resolve_instrument_fallback_broker` | DB miss → broker search → insert → conid returned |
| `test_resolve_instrument_not_found` | DB miss + broker miss → None |
| `test_single_account_no_disambiguation` | 1 account → no acct_select key written |
| `test_multi_account_disambiguation_written` | 3 accounts → acct_select key written, reply contains numbered list |
| `test_account_selection_valid_reply` | user replies "2" → correct account resolved, pending_order written |
| `test_account_selection_out_of_range` | user replies "5" with 3 accounts → error reply, key persists |
| `test_confirm_places_order` | GETDEL pending_order → web nonce minted → place_order called → "✅ Order placed" |
| `test_confirm_expired` | GETDEL nil → "No pending order" reply |
| `test_confirm_risk_gate_blocked` | RiskGateBlockedError → blocker reply, no second nonce minted |
| `test_confirm_maintenance` | PreviewUnavailable(503) → maintenance reply |
| `test_cancel_clears_both_keys` | DEL pending_order + acct_select → "Cancelled" |
| `test_trade_rate_limit_blocks` | check_trade False → rate limit reply before parse |
| `test_write_rate_limit_blocks` | check_write False → rate limit reply |

**`test_rate_limiter.py`** additions:
- `test_check_trade_bucket_independent` — trade bucket does not share state with write bucket

**`test_commands.py`** additions:
- `test_place_order_handler_unauthorized`
- `test_confirm_handler_unauthorized`
- `test_cancel_order_handler_unauthorized`

## Deferred (out of scope for 11d)

- Bracket / OCO orders via Telegram
- Stop-market orders (unsupported broker-side for IBKR in this phase)
- Order status polling via Telegram (`/order_status <id>`)
- FE admin surface changes (11d is BE + bot only)
