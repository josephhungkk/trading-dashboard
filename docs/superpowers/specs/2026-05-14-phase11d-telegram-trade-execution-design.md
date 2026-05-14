# Phase 11d — Telegram Trade Execution Design

## Goal

Add `/place_order` trade execution to the Telegram bot: a two-step confirmation flow (preview → `/confirm`) that integrates with the existing `orders_service` risk gate and broker dispatch pipeline without modifying any web API code.

## Architecture

### Approach

Telegram-native state machine in a new `order_flow.py` module. On `/place_order`, the handler resolves the instrument (after account selection, so `broker_id` is known), calls `orders_service.preview_order` directly (service layer, not HTTP) with `user_key = f"telegram:{entry.from_user_id}"`, inspects the preview for risk blockers/warnings, and — if not blocked — stores resolved params in Redis under a 120s TTL key and replies with the preview summary. On `/confirm`, the handler atomically GETDELs that key, mints a 30s web-compatible nonce with the correct `{payload_hash, rth_at_mint}` envelope into Redis, and calls `orders_service.place_order` — which runs the full risk gate + PDT/BP counters + broker dispatch unchanged.

This approach keeps `orders_service.py` and all web API endpoints completely untouched. The Telegram GETDEL is the real single-use gate; the 30s web nonce satisfies the existing `place_order` API contract without bypassing it.

**`_preview_payload_hash` and `_is_regular_trading_hours` MUST be imported directly from `orders_service`** (not duplicated). Both functions are module-level in `orders_service.py`; the leading underscore signals internal use but cross-module import is explicitly permitted for this integration. This makes `orders_service` the single source of truth for canonical-form hashing.

**Concurrency note:** The existing single-replica PDT counter constraint documented in CLAUDE.md (Phase 10a) applies to Telegram orders identically. Multi-worker locking is deferred to Phase 24. A `/place_order` received while a prior `/confirm` is already dispatching to the broker is allowed by the state machine (the new pending_order replaces the old one in Redis but the in-flight broker call is not cancelled). This is documented semantics, not a bug; PDT + risk-gate concurrency protections remain active.

### New files

| File | Responsibility |
|---|---|
| `backend/app/services/telegram/order_flow.py` | Parse, resolve, preview, confirm, cancel state machine |
| `backend/tests/services/telegram/test_order_flow.py` | Unit tests (no_db; mocked Redis + orders_service) |

### Modified files

| File | Change |
|---|---|
| `backend/app/services/telegram/commands.py` | Add `/place_order`, `/confirm`, `/cancel_order` handlers; account-selection plain-text handler registered BEFORE AI chat catch-all; update `/help` text |
| `backend/app/services/telegram/rate_limiter.py` | Add `check_trade` bucket (5/min, fail-CLOSED on Redis error) |
| `backend/app/main.py` | Pass `registry`, `capability`, `cfg` into `register_handlers` as optional kwargs |

## Command Syntax

```
/place_order <SYMBOL> <BUY|SELL> <QTY> [--limit <price>] [--stop <price>] [--tif DAY|GTC]
/confirm [LIVE]
/cancel_order
```

**Parsing rules:**
- `<SYMBOL>` — 1–16 alphanumeric + `.` chars; uppercased
- `<QTY>` — positive value matching `DECIMAL_10_PATTERN = r"^\d+(\.\d{1,10})?$"`
- `--limit <price>` — must match `DECIMAL_8_PATTERN = r"^\d+(\.\d{1,8})?$"`; alone → `order_type = LIMIT`
- `--stop <price>` — must match `DECIMAL_8_PATTERN`; requires `--limit` also present → `order_type = STOP_LIMIT`
- `--stop` without `--limit` → rejected (stop-market not supported); reply with usage hint
- `--tif DAY|GTC` → default `DAY`; IOC/FOK/GTD not supported via Telegram
- Unknown flags → rejected with usage hint
- Returns `ParsedOrder` dataclass or `None` on parse failure
- `pydantic.ValidationError` from subsequent `PreviewRequest.model_validate` → log + reply "Invalid order parameters: {field}" with the first failing field name

**Unsupported via Telegram (use web):** cash_amount orders, IOC/FOK/GTD TIFs, fractional shares, non-equity asset classes (futures, options, FX), short-selling without an existing long position (see PENDING_CONFIRM → preview risk check below).

**Live account confirmation:** For LIVE-mode accounts the `/confirm` message MUST include the `LIVE` suffix: `/confirm LIVE`. For paper accounts plain `/confirm` is sufficient. This mirrors the paper→live confirmation gate in the web FE.

## Instrument Resolution

Instrument resolution happens AFTER account selection so `broker_id` is known.

1. Query `instruments WHERE ticker = :symbol AND broker_id = :broker_id LIMIT 1`
2. If not found: call `registry.get_client(broker_label)` → `client.search_contracts(symbol)` → filter to equity contracts (`asset_class == "STOCK"`) preferring `primary_exchange in {SMART, NASDAQ, NYSE, ARCA, SEHK}` → if 0 equity matches or >1 ambiguous match with different primary exchanges → `None`. If exactly one unambiguous match → insert into `instruments` → return `conid`.
3. `registry.get_client` raises `KeyError` → reply "Broker not configured for this account." → `None`
4. `client.search_contracts` raises `BrokerSidecarUnavailable` / `BrokerSidecarTimeout` → reply "Broker temporarily unavailable — try again." → `None`
5. If still not found or ambiguous → reply "Unknown or ambiguous symbol — trade via the web to register it." → `None`

Non-equity asset classes are not supported via Telegram in Phase 11d. If `search_contracts` returns only non-equity matches reply "Non-equity assets not supported on Telegram."

## Account Selection

- Query `broker_accounts WHERE deleted_at IS NULL ORDER BY display_order LIMIT 20`
- **0 results** → reply "No active accounts found."
- **1 result** → use silently, proceed to instrument resolution + preview
- **>1 result (up to 20)** → write `telegram:order:acct_select:{chat_id}:{from_user_id}` (120s TTL, JSON with both parsed order AND account list — see key schema below) → reply numbered list:
  ```
  Multiple accounts — reply with a number:
  1. IBKR1 (IBKR) [paper] USD
  2. FUTU1 (Futu) [live] HKD
  ```
- **>20 accounts** → reply "Too many accounts — please select via the web."

**`telegram:order:acct_select` JSON schema:**
```json
{
  "order": {
    "symbol": "AAPL",
    "side": "BUY",
    "qty": "10",
    "order_type": "MARKET",
    "tif": "DAY",
    "limit_price": null,
    "stop_price": null
  },
  "accounts": [
    {"id": "<uuid>", "alias": "IBKR1", "broker": "IBKR", "mode": "paper", "currency": "USD"},
    ...
  ]
}
```

Both the parsed order AND the account list are stored together in a single key to prevent symbol/account mismatching across racing `/place_order` calls. A new `/place_order` while `telegram:order:acct_select` exists MUST `DEL` the old key first and reply "Replacing previous unconfirmed order selection."

**Account selection reply handler** (plain text matching `^[0-9]+$` when `telegram:order:acct_select` key exists):
- Check write (3/min) + trade (5/min) rate limits first
- Valid index → GETDEL acct_select key → extract order + account from stored JSON → resolve instrument → proceed to preview → write `telegram:order:pending:{chat_id}:{from_user_id}`
- Invalid / out-of-range → reply error; key persists (user can retry)
- Numeric replies consumed by account-selection MUST NOT be forwarded to `TelegramChat.handle`
- **Registration order is load-bearing:** the account-selection handler MUST be registered BEFORE the AI chat catch-all (`F.text & ~F.text.startswith("/")`) in `register_handlers`. Use filter: `F.text.regexp(r'^[0-9]+$')` combined with runtime Redis EXISTS check on the acct_select key.

## Redis State Machine

| Key | TTL | Set by | Consumed by |
|---|---|---|---|
| `telegram:order:pending:{chat_id}:{from_user_id}` | 120s | `/place_order` (after account resolved + preview) | `/confirm` (GETDEL) |
| `telegram:order:acct_select:{chat_id}:{from_user_id}` | 120s | `/place_order` (when >1 account) | Account selection reply (GETDEL) |
| `telegram:rl:trade:{chat_id}:{from_user_id}` | sliding 60s | `check_trade` | `check_trade` (sorted-set sliding window) |

**State transitions:**

```
IDLE
  → /place_order
      → 0 accounts: reply error → IDLE
      → 1 account: resolve instrument → preview → if blocked: reply blockers → IDLE
                                                → if warned: reply preview+warnings → PENDING_CONFIRM
                                                → if ok: reply preview → PENDING_CONFIRM
      → >1 account: write acct_select (with order+accounts) → reply list → ACCT_SELECT
      → >20 accounts: reply error → IDLE

ACCT_SELECT (telegram:order:acct_select exists)
  → plain-number reply: GETDEL acct_select → resolve instrument → preview → (same as above) → PENDING_CONFIRM or IDLE
  → /place_order: DEL acct_select → warn "Replacing previous" → restart flow
  → /cancel_order: DEL acct_select → reply "Cancelled" → IDLE
  → TTL expiry: auto-IDLE

PENDING_CONFIRM (telegram:order:pending exists)
  → /confirm (paper or /confirm LIVE for live accounts): GETDEL pending → mint 30s web nonce → place_order → reply result → IDLE
  → /confirm without LIVE on a live account: reply "Live account requires /confirm LIVE" → stay PENDING_CONFIRM
  → /cancel_order: DEL pending → reply "Cancelled" → IDLE
  → new /place_order: DEL old pending → warn "Replacing previous unconfirmed order" → restart flow
  → TTL expiry: auto-IDLE
```

## Preview Reply Format

The preview reply MUST include:

```
📋 Order Preview
Symbol: AAPL
Side: BUY  Qty: 10  Type: MARKET  TIF: DAY
Account: IBKR1 [paper] USD
Est. notional: ~$1,820.00

⚠️ WARN: concentration_limit: approaching 15% cross-broker concentration
              (repeat any warnings)

Reply /confirm to place. Valid for 120s.
```

For LIVE accounts append: `(Live account — reply /confirm LIVE to place.)`

**Risk blockers:** If `preview_response.risk_blockers` is non-empty, DO NOT write `telegram:order:pending`. Reply:
```
❌ Order blocked by risk gate:
• max_notional_exceeded: Order notional $50,000 exceeds per-order cap $20,000

Use the web to adjust limits or order size.
```

**Risk warnings:** If `preview_response.risk_warnings` is non-empty but no blockers, write `telegram:order:pending` and include the warnings in the reply as shown above. The user proceeding with `/confirm` is the explicit acknowledgement (equivalent to the web FE acknowledge gate).

**Extreme position change / short-sell:** If `preview_response.position_sanity.requires_extra_attestation is True` → reject with "This order would result in an extreme position change — confirm via the web." → DO NOT write pending.

## Confirm Flow (Detail)

On `/confirm [LIVE]` with a valid GETDEL of `telegram:order:pending`:

1. Parse the stored JSON; extract `account_mode` from stored params
2. If `account_mode == "live"` and message text does not end with `LIVE` → reply "Live account requires /confirm LIVE" → RE-WRITE `telegram:order:pending` (restore the key with a fresh 120s TTL, same payload) → return
3. Reconstruct `PreviewRequest`-equivalent dict from stored params
4. Compute `payload_hash` by calling `orders_service._preview_payload_hash(account_id, conid, side, order_type, tif, qty, limit_price, stop_price)` directly (imported; not duplicated)
5. Determine `rth_at_mint = orders_service._is_regular_trading_hours(datetime.now(UTC))` (imported)
6. Mint web nonce: `SET nonce:order:{account_id}:{nonce_uuid} {json.dumps({"payload_hash": hash, "rth_at_mint": rth_at_mint})} EX 30 NX`
7. Assemble `PlaceOrderRequest` dict: `{...stored_params, "nonce": nonce_uuid, "client_order_id": f"telegram-{uuid4()}"}`
8. Call `orders_service.place_order(cfg=cfg, db=db, redis=redis, registry=registry, capability=capability, request_data=request_data)`
9. Dispatch on result:
   - **Success** → reply `"✅ Order placed — ID: {order_id}"` (log `telegram_order_placed`)
   - **`PreviewUnavailable(422)` where `payload.get("error") == "risk_gate_blocked"`** → reply `"❌ Blocked: {blocker list}"` — order not placed
   - **`PreviewUnavailable(422)` where error in `{max_notional_exceeded, daily_notional_exceeded}`** → reply `"❌ {error}: {detail}"` — order not placed
   - **`PreviewUnavailable(422)` where error == `rth_changed`** → reply `"Market session changed since preview — please /place_order again."` — order not placed
   - **`PreviewUnavailable(422)` where error in `{unknown_nonce, payload_mismatch}`** → log ERROR (should not happen in normal flow) + reply `"Internal error — please /place_order again."` — order not placed
   - **`PreviewUnavailable(503)`** → reply `"Broker maintenance in progress. {detail}"` — order not placed
   - **`pydantic.ValidationError`** → log + reply `"Invalid order parameters — please /place_order again."`
   - **Any other exception** → log exception + reply `"Order submission failed — check the web dashboard for status before retrying."`
10. On GETDEL nil → reply `"No pending order (expired or already confirmed). If you believe an order was placed, check the web dashboard before retrying."`

**`client_order_id` prefix:** All Telegram-originated orders use `client_order_id = f"telegram-{uuid4()}"` so they are auditable in the orders table.

**`jwt_subject` threading:** `entry.jwt_subject` from `AllowlistEntry` MUST be set as the context variable that `orders_service` uses for PDT counter keys and risk-audit attribution. Walk the existing `orders_service.place_order` call chain to identify the exact context-var name (Phase 10a implementation) and set it before calling `place_order`. This ensures Telegram orders are counted in PDT in-flight counters and cross-broker concentration checks identically to web orders.

## Rate Limiting

| Command | Buckets checked |
|---|---|
| `/place_order` | write (3/min) AND trade (5/min) |
| `/confirm` | write (3/min) AND trade (5/min) |
| Account selection reply | write (3/min) AND trade (5/min) |
| `/cancel_order` | read (10/min) only — cancel is defensive; must remain accessible when write bucket is exhausted |

New `TelegramRateLimiter` method: `async def check_trade(*, chat_id: int, from_user_id: int) -> bool`
- Key: `telegram:rl:trade:{chat_id}:{from_user_id}`, limit 5, window 60s
- **Fail-CLOSED on Redis error** (return `False`, log warning) — trade rate limit is the only fail-closed bucket because it guards money-moving operations. All existing buckets remain fail-open.

Both write and trade buckets must pass for order commands; fail-closed on trade bucket specifically.

## Handler Wiring (Registration Order)

In `register_handlers`, handlers MUST be added in this order (first registered wins in aiogram):

```python
# 1. Account-selection numeric reply (BEFORE chat catch-all)
@dp.message(F.text.regexp(r"^[0-9]+$"))
async def _acct_select(msg: Message) -> None:
    entry = await _authed(msg)
    if entry is None:
        return
    if not await rate_limiter.check_write(...) or not await rate_limiter.check_trade(...):
        await msg.answer("Rate limit exceeded.")
        return
    consumed = await handle_account_selection(msg, entry=entry, db=..., redis=..., ...)
    if not consumed and tg_chat is not None:
        task = asyncio.create_task(tg_chat.handle(msg))
        task.add_done_callback(_on_chat_task_done)

# 2. /place_order, /confirm, /cancel_order handlers

# 3. AI chat catch-all (LAST)
@dp.message(F.text & ~F.text.startswith("/"))
async def _chat_msg(msg: Message) -> None: ...
```

`register_handlers` signature change is backward-compatible: new params are optional:
```python
def register_handlers(
    dp: Dispatcher,
    *,
    allowlist: Any,
    rate_limiter: Any,
    db_factory: Any,
    redis: Any,
    request_app: Any = None,
    tg_chat: Any = None,
    registry: Any = None,      # new — required for /place_order
    capability: Any = None,    # new — required for /place_order
    cfg: Any = None,           # new — required for /place_order
) -> None:
```

`/place_order`, `/confirm`, and account-selection handlers raise `ValueError` at dispatch time if `registry`, `capability`, or `cfg` is `None` — not at registration time, so existing tests that don't exercise these handlers remain unaffected.

## Security Properties

| Property | Mechanism |
|---|---|
| Single-use pending order | `telegram:order:pending` GETDEL — atomic |
| Transport-mismatch reject | Key only mintable inside Telegram handler; no HTTP path can write it |
| 120s order expiry | TTL on `telegram:order:pending` |
| 30s web nonce | Minted with correct `{payload_hash, rth_at_mint}` envelope; consumed atomically within same `place_order` call |
| Replay protection | GETDEL removes key before dispatch; duplicate `/confirm` gets nil → safe "check dashboard" reply |
| Authorization | `AllowlistEntry` required for all commands; single-tenant |
| Risk gate | Unconditional in `orders_service.place_order` at station 4 — cannot be bypassed |
| Input sanitisation | All user-controlled values `html.escape()`'d before inclusion in replies; qty/prices validated against DECIMAL patterns before storing; HTML injection in symbol rejected by alphanumeric-only parser |
| Rate limiting | Trade bucket fail-CLOSED; write + trade both checked for order commands; cancel uses read bucket only |
| Live account gate | `/confirm LIVE` token required for live accounts; plain `/confirm` rejected with re-written pending key |
| PDT / concentration | `jwt_subject` set from `AllowlistEntry` before `place_order` so Telegram orders counted in risk counters |
| Short-sell / extreme position | `requires_extra_attestation` check at preview time; redirects to web |
| Concurrent in-flight semantics | New `/place_order` replaces pending Redis key; in-flight broker calls are not cancelled; PDT + risk-gate protect against abuse; single-replica constraint documented (Phase 24) |
| html.escape scope | Every value in a Telegram reply is escaped: symbol, qty, prices, alias, broker label, risk-gate message field |

## Metrics

Add to the existing Prometheus registry alongside Phase 11b alert counters:

| Metric | Labels | Incremented when |
|---|---|---|
| `telegram_order_attempts_total` | `result={parsed,invalid_syntax,unknown_symbol,no_accounts,rate_limited,broker_unavailable}` | `/place_order` handler entry |
| `telegram_order_previews_total` | `result={ok,warned,blocked,position_sanity_rejected,unavailable}` | Preview result determined |
| `telegram_order_confirms_total` | `result={placed,risk_blocked,notional_exceeded,rth_changed,nonce_error,maintenance,other_error,expired}` | `/confirm` outcome |
| `telegram_order_cancels_total` | `stage={acct_select,pending_order}` | `/cancel_order` executes a DEL |
| `telegram_rate_limiter_trade_block_total` | — | `check_trade` returns False |
| `telegram_order_e2e_seconds` | `stage={preview,confirm}` | Histogram — preview latency, confirm latency |

Tests MUST assert counters increment for each labelled outcome.

## `order_flow.py` Public Interface

```python
@dataclass(frozen=True, slots=True)
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
) -> str | None:  # returns conid or None; instrument resolution AFTER account selection
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
) -> bool:  # True if message was consumed as account selection (do NOT forward to TelegramChat)
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
| `test_parse_limit_too_many_decimals_rejected` | `--limit 100.123456789` (9 decimals) → None |
| `test_parse_html_injection_in_symbol_rejected` | `<script>` in symbol → parser rejects; reply does not echo raw string |
| `test_resolve_instrument_from_db` | found in instruments → conid returned |
| `test_resolve_instrument_fallback_broker` | DB miss → broker search → equity filter → insert → conid returned |
| `test_resolve_instrument_not_found` | DB miss + broker miss → None |
| `test_resolve_instrument_ambiguous_rejects` | search returns 2 equity matches on different primary exchanges → None |
| `test_resolve_instrument_broker_unavailable` | registry.get_client raises → "temporarily unavailable" reply |
| `test_resolve_instrument_after_account_selection` | instrument resolution uses broker_id from selected account |
| `test_single_account_no_disambiguation` | 1 account → no acct_select key written |
| `test_multi_account_disambiguation_written` | 3 accounts → acct_select key written with order+accounts JSON; reply contains numbered list |
| `test_account_selection_valid_reply` | user replies "2" → correct account resolved, pending_order written |
| `test_account_selection_out_of_range` | user replies "5" with 3 accounts → error reply, key persists |
| `test_account_selection_takes_precedence_over_chat_handler` | numeric reply with acct_select key → handler returns True; tg_chat not called |
| `test_place_order_replaces_acct_select_with_warning` | new /place_order while acct_select exists → DEL old key → reply warns |
| `test_preview_with_blockers_no_pending_written` | preview returns risk_blockers → blocked reply, no pending_order key |
| `test_preview_with_warnings_pending_written_and_user_warned` | preview returns risk_warnings → pending written, warning shown in reply |
| `test_extreme_position_change_rejected_at_telegram` | requires_extra_attestation True → "use web" reply, no pending written |
| `test_confirm_places_order` | GETDEL pending → web nonce minted with rth_at_mint → place_order called → "✅ Order placed" |
| `test_confirm_order_id_prefixed_telegram` | client_order_id in place_order call starts with "telegram-" |
| `test_confirm_expired` | GETDEL nil → "check dashboard before retrying" reply |
| `test_confirm_risk_gate_blocked` | PreviewUnavailable(422, risk_gate_blocked) → blocker reply, no retry |
| `test_confirm_daily_notional_exceeded` | PreviewUnavailable(422, daily_notional_exceeded) → actionable reply |
| `test_confirm_max_notional_exceeded` | PreviewUnavailable(422, max_notional_exceeded) → actionable reply |
| `test_confirm_rth_changed` | PreviewUnavailable(422, rth_changed) → "session changed" reply |
| `test_confirm_maintenance` | PreviewUnavailable(503) → maintenance reply |
| `test_confirm_pydantic_validation_error` | ValidationError from place_order build → "invalid parameters" reply |
| `test_confirm_live_account_requires_live_token` | live account + plain /confirm → rejected; pending key restored |
| `test_paper_account_simple_confirm` | paper account + plain /confirm → placed |
| `test_confirm_double_dispatch_only_one_places_order` | two concurrent /confirm tasks → exactly one places order; other gets "No pending order" |
| `test_place_order_during_inflight_confirm_creates_second_pending` | documents concurrent in-flight semantics |
| `test_acct_select_ttl_expires_then_user_replies_number` | TTL expired → handle_account_selection returns False |
| `test_cancel_clears_both_keys` | DEL pending + acct_select → "Cancelled" |
| `test_new_place_order_warns_about_dropped_pending` | new /place_order with existing pending → "previous cancelled" warning in reply |
| `test_trade_rate_limit_blocks` | check_trade False → rate limit reply before parse |
| `test_order_flow_write_rate_limit_blocks_place_order` | check_write False → rate limit reply |
| `test_check_trade_fails_closed_on_redis_error` | Redis error in check_trade → returns False (fail-closed) |
| `test_telegram_order_attempts_total_increments` | counter increments with correct label on parse failure |
| `test_telegram_order_confirms_total_placed_increments` | counter increments on successful place |
| `test_telegram_order_confirms_total_risk_blocked_increments` | counter increments on risk block |
| `test_jwt_subject_set_for_pdt_counter` | entry.jwt_subject threaded through to orders_service call context |

**`test_rate_limiter.py`** additions:
- `test_check_trade_bucket_independent` — trade bucket does not share state with write bucket
- `test_check_trade_fails_closed_on_redis_error` — Redis exception → False

**`test_commands.py`** additions:
- `test_place_order_handler_unauthorized`
- `test_confirm_handler_unauthorized`
- `test_cancel_order_handler_unauthorized`
- `test_account_selection_handler_registered_before_chat_catch_all`
- `test_register_handlers_without_order_deps_still_registers_read_handlers`
- `test_help_includes_order_commands`

## Deferred (out of scope for 11d)

- Non-equity asset classes via Telegram (futures, options, FX)
- Cash-amount orders via Telegram
- IOC / FOK / GTD TIFs via Telegram
- Bracket / OCO orders via Telegram
- Stop-market orders (unsupported broker-side for IBKR in this phase)
- Order status polling via Telegram (`/order_status <id>`)
- FE admin surface changes (11d is BE + bot only)
- Multi-worker PDT counter locking (Phase 24)
- instrument_id passthrough from resolve_instrument to skip the second DB lookup in orders_service._resolve_instrument_id (future optimization)
