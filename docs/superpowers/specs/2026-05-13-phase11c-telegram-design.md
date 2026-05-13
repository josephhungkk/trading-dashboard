# Phase 11c — Telegram Bot — Design

**Status:** brainstorm complete, awaiting user approval before implementation plan.
**Date:** 2026-05-13.
**Version:** v0.11.2
**Parent spec:** `docs/superpowers/specs/2026-05-12-phase11-ai-router-alerts-telegram-design.md` §11c

---

## 1. Scope

Phase 11c ships a Telegram bot with three capabilities:

- **Chunk A** — Bot core + outbound alert delivery
- **Chunk B** — Inbound commands + admin surface + per-webhook secret + CSRF header cleanup
- **Chunk C** — Free-form chat → AI router

Phase 11d (trade execution via Telegram) remains a separate sub-phase at v0.11.3.

---

## 2. Library choice

**aiogram 3.28.2** (latest stable). Chosen over `python-telegram-bot` for cleaner async architecture and first-class webhook support. FastAPI integration uses the manual `feed_update()` path since aiogram's `SimpleRequestHandler` is aiohttp-only:

```python
update = Update.model_validate(await request.json(), context={"bot": bot})
result = await dp.feed_update(bot, update)
```

Secret token validation is handled by the FastAPI endpoint before calling `feed_update`.

---

## 3. Webhook mode + Cloudflare Access

Long-poll is **not used**. Webhook mode is viable because CF Access supports a **Bypass policy** on a specific path:

- CF Zero Trust → Access Applications → add Bypass rule for path `/api/telegram/webhook`
- Telegram calls `setWebhook` pointing at `https://<domain>/api/telegram/webhook`
- FastAPI validates `X-Telegram-Bot-Api-Secret-Token` header (shared secret set at `setWebhook` time)
- Requests that fail token validation get 403 before touching any handler

**Ops steps (one-time, documented in CHANGELOG):**
1. Add CF Access Bypass policy on `/api/telegram/webhook`
2. Seed `app_secrets[telegram.bot_token]` + `app_secrets[telegram.webhook_secret]` via admin UI
3. Backend lifespan calls `bot.set_webhook(url, secret_token=webhook_secret)` on startup

---

## 4. Module layout

```
backend/app/
  services/telegram/
    __init__.py
    bot.py            — Bot + Dispatcher singletons; lifespan on_startup/on_shutdown
    allowlist.py      — chat_id allowlist loader from app_config; FastAPI dependency
    commands.py       — /status /accounts /kill_switch /mute /unmute /help handlers
    chat.py           — free-form message → AICompletionClient (chunk C)
    command_log.py    — writes to telegram_command_log hypertable
    rate_limiter.py   — per-chat sliding-window rate limiter (Redis)
  services/alerts/channels/
    telegram.py       — TelegramChannel (was stub; wired in chunk A)
  api/
    telegram.py       — POST /api/telegram/webhook
    admin_telegram.py — admin CRUD endpoints
frontend/src/
  features/admin/telegram/
    AdminTelegramPage.tsx
    BotConfigPanel.tsx
    AllowlistPanel.tsx
    CommandLogPanel.tsx
  routes/admin/telegram.tsx
```

---

## 5. Secrets + config

| Key | Store | Purpose |
|---|---|---|
| `telegram.bot_token` | `app_secrets` | BotFather token |
| `telegram.webhook_secret` | `app_secrets` | `X-Telegram-Bot-Api-Secret-Token` |
| `telegram.chat_id_hash_salt` | `app_secrets` | HMAC salt for Prometheus `chat_id_hash` labels |
| `telegram_allowlist` | `app_config` namespace | JSON array of `{chat_id: int, label: str}` objects |

---

## 6. Data flow

### 6a. Outbound alert delivery (chunk A)

The `config` dict passed by `DeliveryDispatcher` is ignored for Telegram — the channel reads the allowlist directly from `app_config[telegram_allowlist]` rather than per-rule config, because Telegram delivers to all allowlisted users regardless of which rule fired.

```
AlertFire → DeliveryDispatcher.fan_out
  → TelegramChannel.deliver(fire, config)   # config ignored; allowlist from app_config
    → read allowlist from app_config[telegram_allowlist]
    → for each chat_id:
        bot.send_message(chat_id, formatted_text, parse_mode=HTML)
```

Message format:
```
🔔 <b>{user_label}</b>
Verdict: {verdict}
Value: {evaluated_values}
<a href="https://{domain}/alerts/{alert_id}">View alert →</a>
```

`TelegramChannel.deliver` return semantics:
- At least one chat_id delivered → `DeliveryOutcome.sent`
- Bot not initialised (no token seeded) → `DeliveryOutcome.channel_unavailable`
- All chat_ids failed → `DeliveryOutcome.failed`
- Per-chat Telegram 429: honour `retry_after` once, then skip that chat_id for this fire

### 6b. Inbound webhook (chunks B + C)

```
POST /api/telegram/webhook
  → validate X-Telegram-Bot-Api-Secret-Token (403 on mismatch)
  → Update.model_validate(body)
  → dp.feed_update(bot, update)
    → AllowlistMiddleware: reject non-allowlisted chat_id → reply "Unauthorized" + audit log
    → RateLimiterMiddleware: reject over-limit → reply "Too many requests"
    → CommandRouter (chunk B) OR MessageHandler (chunk C)
```

### 6c. Command handlers (chunk B)

| Command | Action |
|---|---|
| `/status` | Returns evaluator status, active alert count, last fire time |
| `/accounts` | Lists accounts with broker, alias, mode, NLV |
| `/kill_switch <broker>` | Calls `PUT /api/admin/accounts/kill-switch` internally; confirms action |
| `/mute <alert_id> [Xm\|Xh\|Xd]` | Sets `alert_rules.status = muted` with optional expiry |
| `/unmute <alert_id>` | Restores `alert_rules.status = active` |
| `/help` | Lists available commands |

Every command: allowlist check → rate limit check → execute → `command_log.py` INSERT → reply.

Per-chat rate limit: **10 commands/min** (Redis sliding window, key `telegram:rl:{chat_id}`).

### 6d. Free-form chat → AI router (chunk C)

```
Non-command message
  → load conversation from Redis key telegram:chat:{chat_id} (24h TTL, cap 20 turns)
  → append user message
  → AICompletionClient.complete(capability=REALTIME_SENTIMENT, messages=history)
  → append assistant reply to Redis
  → bot.send_message(chat_id, reply)
```

Conversation is isolated per `chat_id`. No dashboard data injected (plain LLM chat).

---

## 7. Database

**Alembic 0045** — `telegram_command_log` hypertable:

```sql
CREATE TABLE telegram_command_log (
    id          BIGSERIAL,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    chat_id     BIGINT NOT NULL,
    from_user_id BIGINT NOT NULL,
    command     TEXT NOT NULL,
    args        TEXT,
    outcome     TEXT NOT NULL,  -- 'ok' | 'unauthorized' | 'rate_limited' | 'error'
    latency_ms  INT
);
SELECT create_hypertable('telegram_command_log', 'ts');
```

Retention: 1 year (added to nightly APScheduler sweep alongside `alert_fires`).

---

## 8. API endpoints

### Webhook receiver (`app/api/telegram.py`)

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/telegram/webhook` | `X-Telegram-Bot-Api-Secret-Token` | CF Access Bypass policy required |

### Admin endpoints (`app/api/admin_telegram.py`)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/admin/telegram/config` | JWT admin | Returns masked token + current webhook URL |
| PUT | `/api/admin/telegram/config` | JWT admin + CSRF | Saves token+secret to app_secrets; re-calls set_webhook |
| POST | `/api/admin/telegram/test-message` | JWT admin + CSRF | Sends test to all allowlisted chats |
| GET | `/api/admin/telegram/allowlist` | JWT admin | Returns allowlist array |
| POST | `/api/admin/telegram/allowlist` | JWT admin + CSRF | Adds `{chat_id, label}` entry |
| DELETE | `/api/admin/telegram/allowlist/{chat_id}` | JWT admin + CSRF | Removes entry |
| GET | `/api/admin/telegram/command-log` | JWT admin | Last 50 rows, cursor pagination |

### Deferred-from-11b endpoints (shipped in 11c-B)

| Method | Path | Auth | Notes |
|---|---|---|---|
| PUT | `/api/admin/alerts/webhooks/{id}` | JWT admin + CSRF | Updates webhook config; resolves secret from `app_secrets[alerts.webhook.<id>.secret]` |

---

## 9. Frontend

**Route:** `/admin/telegram` (lazy-loaded, guarded by `isAdmin`).

**AdminTelegramPage** renders three collapsible panels:

**BotConfigPanel**
- Masked bot token field (show/hide toggle)
- Webhook secret field (masked)
- Save button (mints CSRF nonce → PUT `/api/admin/telegram/config`)
- Test message button (POST `/api/admin/telegram/test-message`)
- Current webhook URL display (read-only)

**AllowlistPanel**
- Table: chat_id | label | remove button
- Add row: chat_id (number input) + label (text) + Add button (mints CSRF nonce)
- Remove: per-row delete (mints CSRF nonce)

**CommandLogPanel**
- Read-only table: ts | chat_id | command | args | outcome | latency_ms
- Last 50 rows, "Load more" cursor pagination
- Polling every 30s via TanStack Query

**CSRF header cleanup (11c-B):**
- `CapabilityMapEditor.tsx`: `X-CSRF-Nonce` → `X-Confirm-Nonce` (2 call sites)
- `ProviderKeyCrud.tsx`: `X-CSRF-Nonce` → `X-Confirm-Nonce` (2 call sites)
- Update corresponding tests in `AdminAiPage.test.tsx`

---

## 10. Prometheus metrics

`telegram_*` namespace (7 metrics, matches parent spec §6):

| Metric | Type | Labels |
|---|---|---|
| `telegram_messages_inbound_total` | Counter | `outcome` |
| `telegram_messages_outbound_total` | Counter | `outcome` |
| `telegram_commands_total` | Counter | `command, outcome` |
| `telegram_unauthorized_attempts_total` | Counter | — |
| `telegram_rate_limited_total` | Counter | `kind` (command\|execute) |
| `telegram_bot_api_errors_total` | Counter | `error_class` |
| `telegram_active_conversations` | Gauge | — |

`chat_id` never appears raw in metrics — HMAC hash using `telegram.chat_id_hash_salt`.

---

## 11. Lifespan integration

In `app/main.py`:

```python
# startup (after evaluator)
if bot_token := await get_secret("telegram.bot_token"):
    webhook_secret = await get_secret("telegram.webhook_secret")
    app.state.telegram_bot = await telegram_startup(
        bot_token=bot_token,
        webhook_secret=webhook_secret,
        webhook_url=f"{settings.public_base_url}/api/telegram/webhook",
    )

# shutdown
if hasattr(app.state, "telegram_bot"):
    await telegram_shutdown(app.state.telegram_bot)
```

Bot is **optional** — backend starts cleanly with no token seeded; `TelegramChannel` returns `channel_unavailable` until token is configured via admin UI. No hard startup failure.

---

## 12. Error handling

| Scenario | Behaviour |
|---|---|
| Token not seeded at startup | Bot skipped; `TelegramChannel` returns `channel_unavailable` |
| `set_webhook` fails | Log error + metric; backend still starts; admin UI shows "webhook not set" |
| Inbound request fails token validation | 403 (no body); metric `telegram_unauthorized_attempts_total` |
| Non-allowlisted chat_id | Reply "Unauthorized"; audit log insert; `telegram_unauthorized_attempts_total++` |
| Telegram API 429 on outbound | Honour `retry_after` once; then log + skip |
| Telegram API 5xx | Retry once after 1s; then `DeliveryOutcome.failed` |
| AI router unavailable (chunk C) | Reply "AI unavailable, try again later"; never throws |
| Redis unavailable (rate limiter) | Fail-OPEN — allow request through; log warning |
| command_log INSERT fails | Log warning; do NOT block command reply (fail-open audit) |

---

## 13. Security

- `X-Telegram-Bot-Api-Secret-Token` validated on every inbound webhook request before any processing
- CF Access Bypass scoped narrowly to `/api/telegram/webhook` path only
- Allowlist checked in aiogram middleware before any handler runs — no command executes for non-allowlisted chat_id
- `/kill_switch` audited in `telegram_command_log` with `outcome`
- `chat_id` never logged raw — HMAC hash in structlog processor (same extension as `app/core/logging.py` redactor)
- `telegram.bot_token` stored in `app_secrets` (Fernet-encrypted); never logged
- Redis conversation (chunk C) keyed on `chat_id` only, 24h TTL, no PII beyond message content

---

## 14. Testing targets

**Chunk A (~15 BE tests):**
- `TelegramChannel.deliver` — no token → `channel_unavailable`
- `TelegramChannel.deliver` — all chats delivered → `sent`
- `TelegramChannel.deliver` — all chats fail → `failed`
- `TelegramChannel.deliver` — partial failure → `sent`
- `TelegramChannel.deliver` — 429 with `retry_after` → honours once
- Message format assertion (HTML escaping, link URL shape)

**Chunk B (~20 BE tests + 3 FE tests):**
- Webhook endpoint: valid token → 200; invalid token → 403
- AllowlistMiddleware: unknown chat_id → 200 (Telegram expects 200) + audit row
- RateLimiterMiddleware: 11th command/min → rate_limited outcome
- `/status` handler: returns evaluator stats
- `/kill_switch` handler: calls internal endpoint + audit row
- `/mute` + `/unmute` handlers: DB state transitions
- CSRF header tests for CapabilityMapEditor + ProviderKeyCrud (header name flip)
- Admin allowlist add/remove/list endpoints
- `PUT /api/admin/alerts/webhooks/{id}` secret resolution

**Chunk C (~8 BE tests):**
- Free-form message → AI router called with correct history
- Conversation appended to Redis after reply
- Conversation capped at 20 turns
- AI unavailable → graceful reply
- 24h TTL set on Redis key

---

## 15. Chunk split summary

| Chunk | Tag | Key deliverables |
|---|---|---|
| 11c-A | v0.11.2.0 | aiogram dep + services/telegram/{bot,allowlist} + TelegramChannel wired + lifespan + webhook endpoint + Alembic 0045 + admin config/allowlist/test-message endpoints + AdminTelegramPage panels 1+2 |
| 11c-B | v0.11.2.1 | commands.py + command_log.py + rate_limiter.py + command-log FE panel + PUT /api/admin/alerts/webhooks/{id} + CSRF header flip (CapabilityMapEditor + ProviderKeyCrud) |
| 11c-C | v0.11.2.2 | chat.py + Redis conversation store + AI router integration |

---

## 16. Deferred

- **Monaco editor swap** — separate ticket; ~1.5MB dep decision; `<textarea>` stays for now
- **TicksSubscriber lifespan integration** — depends on Phase 7b.1 quote-engine API surface
- **Telegram webhook mode → long-poll fallback** — not needed; webhook is stable with CF Bypass
- **11d trade execution** — v0.11.3, separate spec
- **Per-chat conversation persistence beyond Redis** — Phase 21+ bot engine

---

## 17. Open questions resolved

| Question | Decision |
|---|---|
| Long-poll vs webhook | Webhook — CF Access Bypass policy on `/api/telegram/webhook` |
| Bot library | aiogram 3.28.2 — cleaner async, FastAPI via `feed_update()` |
| Multi-user allowlist | Yes — `{chat_id, label}` objects in `app_config[telegram_allowlist]` |
| Kill-switch access | All allowlisted users (flat, no sub-roles) |
| Chunk C AI capability | `REALTIME_SENTIMENT` — plain LLM chat, no dashboard data injection |
