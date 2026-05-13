# Phase 11c — Telegram Bot — Design

**Status:** ARCHITECT-REVIEW applied (3 CRIT, 9 HIGH, 9 MED inline; 5 LOW documented). Awaiting user review before implementation plan.
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

**aiogram** (latest stable, 3.28.2 at spec time — install via `uv add aiogram`, lock in lockfile). Chosen over `python-telegram-bot` for cleaner async architecture and first-class webhook support. FastAPI integration uses the manual `feed_update()` path since aiogram's `SimpleRequestHandler` is aiohttp-only:

```python
update = Update.model_validate(await request.json(), context={"bot": bot})
result = await dp.feed_update(bot, update)
```

Secret token validation is handled by the FastAPI endpoint before calling `feed_update`. `feed_update` exceptions are caught at the endpoint level (never propagate as 5xx — always return 200 to Telegram; see §12).

---

## 3. Webhook mode + Cloudflare Access

Long-poll is **not used**. Webhook mode is viable because CF Access supports a **Bypass policy** on a specific path:

- CF Zero Trust → Access Applications → add Bypass rule for path `/api/telegram/webhook`
- Telegram calls `setWebhook` pointing at `https://<domain>/api/telegram/webhook`
- FastAPI validates `X-Telegram-Bot-Api-Secret-Token` header (shared secret set at `setWebhook` time)
- Requests that fail token validation → 403 before touching any handler

**CRIT-3 / HIGH-11 (security hardening):** The Bypass policy alone is not sufficient — a leaked `webhook_secret` would expose the endpoint. Additionally:
- Add a **CF WAF IP restriction rule** pinning `/api/telegram/webhook` to Telegram's published IP ranges (`149.154.160.0/20`, `91.108.4.0/22`). This is the CF firewall rule documented in ops checklist below.
- `telegram.webhook_secret` is **auto-rotated** on every `PUT /api/admin/telegram/config` save — the endpoint generates a new random secret, calls `setWebhook` with it, and stores it in `app_secrets`. Admin never manually types the secret.

**Ops steps (one-time, documented in CHANGELOG):**
1. Add CF Access Bypass policy on `/api/telegram/webhook`
2. Add CF WAF rule: block requests to `/api/telegram/webhook` from IPs outside `149.154.160.0/20` and `91.108.4.0/22`
3. Seed `app_secrets[telegram.bot_token]` via admin UI; `webhook_secret` is auto-generated on first `PUT /api/admin/telegram/config`
4. Backend lifespan calls `bot.set_webhook(url, secret_token=webhook_secret)` on startup (with 3× retry — see §11)

---

## 4. Module layout

```
backend/app/
  services/telegram/
    __init__.py
    bot.py            — Bot + Dispatcher singletons; lifespan on_startup/on_shutdown
    allowlist.py      — chat_id allowlist loader from app_config; Redis-cached + pubsub refresh
    commands.py       — /status /accounts /kill_switch /mute /unmute /help handlers
    chat.py           — free-form message → AICompletionClient (chunk C)
    command_log.py    — writes to telegram_command_log hypertable
    rate_limiter.py   — per-chat sliding-window rate limiter (Redis); two buckets
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
| `telegram.webhook_secret` | `app_secrets` | Auto-generated; `X-Telegram-Bot-Api-Secret-Token`; rotated on every config save |
| `telegram.chat_id_hash_salt` | `app_secrets` | HMAC salt for Prometheus `chat_id_hash` labels and structlog redaction |
| `telegram.public_base_url` | `app_config` | Full base URL e.g. `https://dashboard.example.com`; editable at runtime; used for webhook URL construction in lifespan (per HIGH-12: no `.env` key beyond bootstrap) |
| `telegram_allowlist` | `app_config` namespace | JSON array of `{chat_id: int, from_user_id: int, jwt_subject: str, label: str}` objects |

**Allowlist schema (CRIT-1 fix):** each entry binds `chat_id` + `from_user_id` + `jwt_subject`. This allows `/kill_switch` to scope to accounts owned by `jwt_subject` only. Private-chat enforcement: `AllowlistMiddleware` rejects group/supergroup/channel updates — only `message.chat.type == 'private'` passes.

---

## 6. Data flow

### 6a. Outbound alert delivery (chunk A)

The `config` dict passed by `DeliveryDispatcher` is ignored for Telegram — the channel reads the allowlist directly from `app_config[telegram_allowlist]` rather than per-rule config, because Telegram delivers to all allowlisted users regardless of which rule fired.

**HIGH-8 (concurrent fan-out):** Sends are dispatched with `asyncio.gather` — not serial iteration.

```
AlertFire → DeliveryDispatcher.fan_out
  → TelegramChannel.deliver(fire, config)   # config ignored; allowlist from app_config
    → bot = app.state.telegram_bot; if None → channel_unavailable
    → read allowlist from app_config[telegram_allowlist]
    → asyncio.gather(*[send_one(cid) for cid in chat_ids], return_exceptions=True)
        each send_one: bot.send_message(chat_id, text, parse_mode=HTML) with 5s timeout
```

Message format:
```
🔔 <b>{user_label}</b>
Verdict: {verdict}
Value: {evaluated_values}
<a href="{public_base_url}/alerts/{alert_id}">View alert →</a>
```

`TelegramChannel.deliver` return semantics:
- Bot not initialised (no token) → `DeliveryOutcome.channel_unavailable`
- At least one chat_id delivered → `DeliveryOutcome.sent`
- All chat_ids failed → `DeliveryOutcome.failed`
- Per-chat Telegram 429: honour `retry_after` once per chat, then skip for this fire
- Per-chat Telegram 5xx: retry once after 1s, then skip

**Enabling Telegram delivery (MED-1):** New alerts default to `delivery_channels=["in_app"]`. Owner adds `"telegram"` via `PUT /api/alerts/{id}` channels array. Per-rule config is empty `{}` — allowlist is global. Test: alert with `delivery_channels=["in_app","telegram"]` fires both channels in `fan_out`.

### 6b. Inbound webhook (chunks B + C)

```
POST /api/telegram/webhook
  → validate X-Telegram-Bot-Api-Secret-Token (403 on mismatch; metric telegram_unauthorized_attempts_total)
  → deduplicate: check update_id in Redis (key telegram:seen:{update_id}, TTL 5min); if present → 200 OK (no-op)
  → bot = request.app.state.telegram_bot; if None → 503 (existence-oracle behaviour per MED-4)
  → Update.model_validate(body)
  → only handle Update.message (message.chat.type == 'private' only); other update types → 200 + metric telegram_messages_inbound_total{outcome='unsupported_update_type'}
  → try: await dp.feed_update(bot, update)
    except Exception: log + telegram_bot_api_errors_total++ + bot.send_message("command failed") → return 200
    → AllowlistMiddleware: check (chat_id, from_user_id) in allowlist; reject → reply "Unauthorized" + command_log row + telegram_unauthorized_attempts_total++
    → RateLimiterMiddleware (two buckets — see §6c)
    → CommandRouter (chunk B) OR MessageHandler → background task (chunk C)
  → always return 200 to Telegram (HIGH-4 / MED-3 fix)
```

**HIGH-4 (idempotency):** `update_id` deduplication in Redis with 5min TTL prevents duplicate execution when Telegram retries a timed-out webhook.

**MED-4:** `bot = request.app.state.telegram_bot; if bot is None: return Response(status_code=503)` — returns 503 before any processing when bot is uninitialised. Telegram will retry; operator will see 503 in logs and know to seed the token.

### 6c. Command handlers (chunk B)

**Two-bucket rate limit (MED-3):**
- Read bucket: 10/min per `(chat_id, from_user_id)` — covers `/status`, `/accounts`, `/help`
- Write bucket: 3/min per `(chat_id, from_user_id)` — covers `/kill_switch`, `/mute`, `/unmute`
- Redis sliding window, key `telegram:rl:read:{chat_id}` / `telegram:rl:write:{chat_id}`
- Redis unavailable → fail-OPEN (allow through) + log warning

| Command | Bucket | Action |
|---|---|---|
| `/status` | read | Returns evaluator status, active alert count, last fire time |
| `/accounts` | read | Lists accounts with broker, alias, mode, NLV (scoped to allowlist entry's `jwt_subject`) |
| `/kill_switch <broker>` | write | Service-layer call (see below); replies with per-account outcome |
| `/mute <alert_id> [Xm\|Xh\|Xd]` | write | Sets `alert_rules.muted_until` + `status='disabled'` (see §7) |
| `/unmute <alert_id>` | write | Clears `muted_until`, restores `status='active'` |
| `/help` | read | Lists commands with grammar |

Every command: allowlist check → rate-limit check → execute → `command_log.py` INSERT (fail-open) → reply.

**`/mute` grammar (LOW-1 fix):** format `^\d+([mhd])$` where `m`=minutes, `h`=hours, `d`=days. Default if duration omitted: permanent (until `/unmute`). Invalid input → "Usage: /mute <alert_id> [30m|2h|1d]" reply.

**`/kill_switch` service-layer path (CRIT-2 fix):**
- Does **not** call the HTTP endpoint (which requires a CSRF nonce)
- Calls `AccountKillSwitchService.toggle(account_id, is_enabled=True, reason="telegram:/kill_switch", by=f"telegram:{entry.label}")` directly
- Resolves broker alias → accounts owned by `entry.jwt_subject` only (CRIT-1 fix)
- Service still publishes `app_config:invalidate:kill_switch` pubsub
- `from_user_id` NOT `chat_id` authorises the action (from allowlist entry match)
- Test: service-layer path taken (not HTTP); cross-jwt_subject accounts skipped; partial failure → per-account outcome in reply + audit rows for all (including failed); broker-not-found → user-friendly reply; already-enabled → idempotent + `outcome='noop'` audit row

**Mute expiry enforcement (HIGH-5 fix):** see §7 — `muted_until` column on `alerts` table, not `app_config`.

### 6d. Free-form chat → AI router (chunk C)

**HIGH-13 (conversation race condition):** Per-chat asyncio lock prevents concurrent AI calls for the same `chat_id` corrupting conversation history. Rate-limit: 1 in-flight call per `chat_id`; a second message while one is in flight → "previous reply still in progress, please wait."

**HIGH-4 (webhook timeout):** AI call is dispatched as `asyncio.create_task` and acks the webhook within 1s. Reply is sent via `bot.send_message` when the AI call completes.

```
Non-command message (private chat only)
  → check per-chat lock; if locked → reply "previous reply still in progress"
  → acquire lock
  → background task:
      load conversation from Redis key telegram:chat:{chat_id_hash} (24h TTL, cap 20 turns)
      append user message
      AICompletionClient.complete(capability=REASONING, messages=history)  # MED-9 fix
      append assistant reply to Redis
      bot.send_message(chat_id, reply)
      release lock
  → webhook returns 200 immediately
```

Conversation key uses `chat_id_hash` (HMAC) not raw `chat_id` (HIGH-10 fix).

**MED-9 fix:** capability is `REASONING` (general-purpose LLM chat), not `REALTIME_SENTIMENT` (which is tuned for news-blurb analysis, wrong for conversational use).

AI router unavailable → reply "AI unavailable, try again later"; never throws.

---

## 7. Database

**Alembic 0045** — `telegram_command_log` hypertable + `muted_until` column on `alerts`:

```sql
-- telegram_command_log hypertable
CREATE TABLE telegram_command_log (
    id           BIGSERIAL,
    ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
    chat_id      BIGINT NOT NULL,
    from_user_id BIGINT,          -- nullable (MED-8 fix: anonymous group/channel posts have no from_user)
    command      TEXT NOT NULL,
    args         TEXT,
    outcome      TEXT NOT NULL    CHECK (outcome IN ('ok','unauthorized','rate_limited','error','noop')),
    latency_ms   INT
);
SELECT create_hypertable('telegram_command_log', 'ts');
CREATE INDEX idx_tg_cmd_chat_ts   ON telegram_command_log (chat_id, ts DESC);
CREATE INDEX idx_tg_cmd_outcome_ts ON telegram_command_log (outcome, ts DESC) WHERE outcome != 'ok';

-- muted_until column on alerts (HIGH-6 fix: replaces app_config key approach)
ALTER TABLE alerts ADD COLUMN muted_until TIMESTAMPTZ;
CREATE INDEX idx_alerts_muted_until ON alerts (muted_until) WHERE muted_until IS NOT NULL;
```

**Muted_until enforcement (HIGH-5 + HIGH-6 fix):**
- Evaluator's `status='active'` gate (in `runner.py`) becomes: `WHERE status='active' AND (muted_until IS NULL OR muted_until <= now())`
- APScheduler job (every 60s, added to nightly lifespan alongside retention sweep): `UPDATE alerts SET status='active', muted_until=NULL WHERE status='disabled' AND muted_until IS NOT NULL AND muted_until <= now()`. Notifies originating chat_id on restore.
- `/mute` sets `muted_until` + `status='disabled'`; `/unmute` clears both.
- Test: mute with 1s expiry → APScheduler job restores status='active' after expiry.

**Retention (MED tiered):** Tiered retention for `telegram_command_log`: `outcome='ok'` keep 1 year; `outcome IN ('rate_limited','unauthorized','error')` keep 90 days. Added to nightly APScheduler sweep.

---

## 8. API endpoints

### Webhook receiver (`app/api/telegram.py`)

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/telegram/webhook` | `X-Telegram-Bot-Api-Secret-Token` + CF WAF IP rule | Returns 200 always (except 403 on bad token, 503 if bot=None) |

### Admin endpoints (`app/api/admin_telegram.py`)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/admin/telegram/config` | JWT admin | Returns masked token + current webhook URL + `webhook_status: 'set'\|'retrying'\|'failed'` |
| PUT | `/api/admin/telegram/config` | JWT admin + CSRF | Saves token to app_secrets; auto-generates + rotates webhook_secret; re-calls set_webhook |
| POST | `/api/admin/telegram/test-message` | JWT admin + CSRF | Sends test to a single `chat_id` from request body (or first allowlist entry if absent) — not all chats (LOW-2 fix) |
| GET | `/api/admin/telegram/allowlist` | JWT admin | Returns allowlist array |
| POST | `/api/admin/telegram/allowlist` | JWT admin + CSRF | Adds `{chat_id, from_user_id, jwt_subject, label}` entry; publishes `app_config:invalidate:telegram_allowlist` |
| DELETE | `/api/admin/telegram/allowlist/{chat_id}` | JWT admin + CSRF | Removes entry; publishes `app_config:invalidate:telegram_allowlist` |
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
- Note: webhook secret is auto-managed (not user-visible)
- Save button (mints CSRF nonce → PUT `/api/admin/telegram/config`)
- Test message button with optional chat_id input (POST `/api/admin/telegram/test-message`)
- Current webhook URL display (read-only) + `webhook_status` badge (`set` / `retrying` / `failed`)

**AllowlistPanel**
- Table: chat_id | from_user_id | label | remove button
- Add row: chat_id (number input) + from_user_id (number input) + jwt_subject (select from existing users) + label (text) + Add button (mints CSRF nonce)
- Remove: per-row delete (mints CSRF nonce)

**CommandLogPanel**
- Read-only table: ts | chat_id (hashed display) | command | args | outcome | latency_ms
- Last 50 rows, "Load more" cursor pagination
- Polling every 30s via TanStack Query

**CSRF header cleanup (11c-B — MED-5 note):** Shipped as pre-step before chunk B feature work to avoid coupling:
- `CapabilityMapEditor.tsx`: `X-CSRF-Nonce` → `X-Confirm-Nonce` (2 call sites)
- `ProviderKeyCrud.tsx`: `X-CSRF-Nonce` → `X-Confirm-Nonce` (2 call sites)
- Update corresponding tests in `AdminAiPage.test.tsx`
- Backend already accepts `X-Confirm-Nonce` on these endpoints (same `consume_confirmation_nonce` dep); the old `X-CSRF-Nonce` header is simply never read by BE — FE was sending the wrong name, causing silent no-op CSRF protection. Flip is FE-only.

---

## 10. Prometheus metrics

`telegram_*` namespace (9 metrics — adds 2 histograms vs parent spec §6):

| Metric | Type | Labels |
|---|---|---|
| `telegram_messages_inbound_total` | Counter | `outcome` |
| `telegram_messages_outbound_total` | Counter | `outcome` |
| `telegram_commands_total` | Counter | `command, outcome` |
| `telegram_unauthorized_attempts_total` | Counter | — |
| `telegram_rate_limited_total` | Counter | `kind` (read\|write) |
| `telegram_bot_api_errors_total` | Counter | `error_class` |
| `telegram_active_conversations` | Gauge | — |
| `telegram_send_message_duration_seconds` | Histogram | — (global outbound latency) |
| `telegram_webhook_duration_seconds` | Histogram | `handler` (command\|chat\|middleware_reject) |

`chat_id` never appears raw in metrics — HMAC hash using `telegram.chat_id_hash_salt`.

---

## 11. Lifespan integration

In `app/main.py`:

```python
# startup (after evaluator)
if bot_token := await get_secret("telegram.bot_token"):
    webhook_secret = await get_secret("telegram.webhook_secret")
    public_base_url = await get_config("telegram.public_base_url")
    app.state.telegram_bot = await telegram_startup(
        bot_token=bot_token,
        webhook_secret=webhook_secret,
        webhook_url=f"{public_base_url}/api/telegram/webhook",
    )
    # telegram_startup retries set_webhook 3× (1s/3s/9s backoff)
    # on failure: schedules background retry every 5min; sets app.state.telegram_webhook_status

# shutdown — does NOT call delete_webhook (CRIT-3 fix)
if hasattr(app.state, "telegram_bot"):
    await telegram_shutdown(app.state.telegram_bot)  # closes Bot session only
```

**CRIT-3 fix:** `telegram_shutdown` closes the aiogram `Bot` HTTP session only. It does **not** call `bot.delete_webhook()`. The webhook URL is set idempotently on every startup (`setWebhook` to same URL = no-op). This prevents: (a) inbound update loss during rolling deploys, (b) future multi-replica webhook clobbering.

**HIGH-7 (set_webhook retry):** `telegram_startup` retries `set_webhook` 3× with 1s/3s/9s backoff. After 3 failures, schedules a background task that retries every 5min until success, then exits. `app.state.telegram_webhook_status` tracks `'set'|'retrying'|'failed'`.

Bot is **optional** — backend starts cleanly with no token seeded; `TelegramChannel` returns `channel_unavailable` until token is configured via admin UI.

---

## 12. Error handling

| Scenario | Behaviour |
|---|---|
| Token not seeded at startup | Bot skipped; `TelegramChannel` returns `channel_unavailable` |
| `set_webhook` fails after 3 retries | Background retry every 5min; `webhook_status='retrying'`; FE shows badge |
| Inbound request fails token validation | 403; `telegram_unauthorized_attempts_total++` |
| `bot` is None on inbound webhook | 503; Telegram retries; operator seeds token via admin UI |
| Non-allowlisted `(chat_id, from_user_id)` | Reply "Unauthorized"; command_log row; `telegram_unauthorized_attempts_total++` |
| Group/channel update (not private chat) | 200 + `telegram_messages_inbound_total{outcome='unsupported_update_type'}` |
| Duplicate `update_id` (Telegram retry) | 200 no-op (Redis dedup key hit) |
| `feed_update` exception | Catch-all: 200 returned; `telegram_bot_api_errors_total++`; user-facing "command failed" reply |
| Telegram API 429 on outbound | Honour `retry_after` once per chat; then skip for this fire |
| Telegram API 5xx on outbound | Retry once after 1s; then skip |
| AI router unavailable (chunk C) | Reply "AI unavailable, try again later"; lock released |
| Redis unavailable (rate limiter) | Fail-OPEN — allow request through; log warning |
| Redis unavailable (conversation store) | Reply "conversation unavailable"; does not crash |
| command_log INSERT fails | Log warning; do NOT block command reply (fail-open audit) |
| `/kill_switch` partial account failure | Per-account outcome listed in reply; audit rows for all accounts (including failed) |

---

## 13. Security

- `X-Telegram-Bot-Api-Secret-Token` validated on every inbound webhook request before any processing
- CF Access Bypass scoped narrowly to `/api/telegram/webhook`; CF WAF IP rule pins to Telegram's published ranges
- `telegram.webhook_secret` auto-rotated on every `PUT /api/admin/telegram/config` save (never admin-visible)
- Only `message.chat.type == 'private'` updates processed; group/channel updates 200-acked and dropped
- Allowlist entry binds `(chat_id, from_user_id, jwt_subject)` — not just chat_id; in-process refresh via Redis pubsub `app_config:invalidate:telegram_allowlist` (60s failsafe TTL)
- `/kill_switch` scoped to `jwt_subject` accounts only; no cross-user account access
- `chat_id` and `from_user_id` NEVER logged raw — structured log keys `chat_id` / `from_user_id` replaced by HMAC hash in the structlog processor (`app/core/logging.py` extension); aiogram's own logger set to WARNING to suppress chat_id-bearing INFO messages; all telegram modules use structured keys only (never f-string interpolation with raw chat_id)
- `telegram.bot_token` stored in `app_secrets` (Fernet-encrypted); never logged
- Redis conversation key uses `chat_id_hash` not raw `chat_id`; 24h TTL
- `update_id` deduplication prevents replayed webhook attacks that could re-execute commands

---

## 14. Testing targets

**Chunk A (~18 BE tests):**
- `TelegramChannel.deliver` — bot=None → `channel_unavailable`
- `TelegramChannel.deliver` — all chats delivered → `sent`
- `TelegramChannel.deliver` — all chats fail → `failed`
- `TelegramChannel.deliver` — partial failure → `sent`
- `TelegramChannel.deliver` — 429 with `retry_after` → honours once per chat
- `TelegramChannel.deliver` — 10 chat_ids deliver concurrently (asyncio.gather); total time bounded by max-single-send not sum
- Message format assertion (HTML escaping, link URL uses `public_base_url`)
- Alert with `delivery_channels=["in_app","telegram"]` fires both channels in `fan_out`
- `telegram_shutdown` does NOT call `delete_webhook`

**Chunk B (~25 BE tests + 3 FE tests):**
- Webhook endpoint: valid token → 200; invalid token → 403; bot=None → 503
- Duplicate `update_id` → 200 no-op (no handler called)
- Group chat update → 200 + unsupported_update_type metric (no handler called)
- `AllowlistMiddleware`: unknown `(chat_id, from_user_id)` → 200 + audit row + unauthorized metric
- `AllowlistMiddleware`: allowlist refreshed via pubsub without restart
- Read rate bucket: 11th `/status` in 1min → rate_limited
- Write rate bucket: 4th `/kill_switch` in 1min → rate_limited; read bucket not consumed
- `/status` handler: returns evaluator stats
- `/kill_switch <broker>` — service-layer path taken (not HTTP); matching accounts under jwt_subject toggled; cross-jwt_subject accounts skipped; partial account failure → per-account outcome in reply; broker-not-found → user-friendly reply; already-enabled → `outcome='noop'` audit row
- `/mute 42 30m` → `muted_until` set + `status='disabled'`; `/unmute 42` → `muted_until=NULL` + `status='active'`
- `/mute 42` (no duration) → permanent mute
- `/mute bad_arg` → usage hint reply
- Mute expiry: muted_until in past → APScheduler job restores `status='active'`
- `feed_update` exception → 200 returned + error metric + user-facing "command failed" reply
- CSRF header tests: `CapabilityMapEditor` + `ProviderKeyCrud` send `X-Confirm-Nonce` (not `X-CSRF-Nonce`)
- Admin allowlist add/remove/list endpoints
- `PUT /api/admin/alerts/webhooks/{id}` secret resolution

**Chunk C (~10 BE tests):**
- Free-form message → AI router called with `REASONING` capability + correct history
- Conversation appended to Redis after reply (key uses `chat_id_hash`)
- Conversation capped at 20 turns (oldest evicted)
- Second message while AI in flight → "previous reply still in progress" (lock held)
- AI unavailable → graceful reply; lock released
- 24h TTL set on Redis key
- `chat_id` not present in log output for any inbound flow (structlog redaction test)

---

## 15. Chunk split summary

| Chunk | Tag | Key deliverables |
|---|---|---|
| 11c-A | v0.11.2.0 | `uv add aiogram` + services/telegram/{bot,allowlist} + TelegramChannel wired (concurrent fan-out) + lifespan (set_webhook retry + no delete_webhook) + webhook endpoint (token validate + update_id dedup + bot=None 503) + Alembic 0045 (telegram_command_log + muted_until + indexes) + admin config/allowlist/test-message endpoints + AdminTelegramPage panels 1+2 |
| 11c-B.0 | — | CSRF header flip pre-step: `CapabilityMapEditor.tsx` + `ProviderKeyCrud.tsx` + tests (shipped as own commit before 11c-B feature work) |
| 11c-B | v0.11.2.1 | commands.py (two-bucket RL, service-layer kill_switch, muted_until) + command_log.py (nullable from_user_id, tiered retention) + rate_limiter.py + mute APScheduler job + command-log FE panel + PUT /api/admin/alerts/webhooks/{id} |
| 11c-C | v0.11.2.2 | chat.py (per-chat lock, background task, REASONING capability, hash-keyed Redis conv) |

---

## 16. Deferred

- **Monaco editor swap** — separate ticket; ~1.5MB dep decision; `<textarea>` stays for now
- **TicksSubscriber lifespan integration** — depends on Phase 7b.1 quote-engine API surface
- **11d trade execution** — v0.11.3, separate spec
- **Per-chat conversation persistence beyond Redis** — Phase 21+ bot engine
- **Long-poll fallback** — not needed; webhook stable with CF Bypass + WAF IP rule
- **LOW-1 (help text format):** grammar defined in spec §6c; `/help` handler just needs to list it
- **LOW-2 (test-message):** scoped to single chat_id in spec §8 — not deferred, already fixed inline
- **LOW-3 (retention tiering):** applied inline in §7
- **LOW-4 (callback_query updates):** 11c 200-acks and drops them; 11d wires inline keyboard confirm
- **LOW-5 (aiogram pin):** `uv add aiogram` (no version pin); lockfile is source of truth

---

## 17. Architect review — applied

**Review date:** 2026-05-13. Verdict: BLOCKED → fixed inline.

| Finding | Severity | Resolution |
|---|---|---|
| Kill-switch authz hole: any allowlisted chat controls all accounts | CRIT-1 | Allowlist schema now `{chat_id, from_user_id, jwt_subject, label}`; private-chat-only middleware; /kill_switch scoped to jwt_subject |
| CSRF bypassed on /kill_switch HTTP call | CRIT-2 | /kill_switch uses service layer directly (AccountKillSwitchService.toggle), not HTTP endpoint; documented |
| delete_webhook on shutdown breaks rolling deploys | CRIT-3 | telegram_shutdown closes session only; never calls delete_webhook |
| feed_update blocks webhook handler; Telegram retries = duplicate commands | HIGH-4 | update_id Redis dedup (5min TTL); chunk C AI call goes background task |
| Mute expiry has no enforcer | HIGH-5 | APScheduler job every 60s restores expired mutes |
| app_config mute store: wrong store, no index, slow scans | HIGH-6 | muted_until column on alerts table (Alembic 0045 + index) |
| set_webhook startup failure has no retry | HIGH-7 | 3× retry (1s/3s/9s) + 5min background retry loop; webhook_status field |
| Outbound delivery is serial — head-of-line blocking | HIGH-8 | asyncio.gather concurrent fan-out with 5s per-chat timeout |
| Allowlist refresh missing — middleware cache stales | HIGH-9 | Redis pubsub `app_config:invalidate:telegram_allowlist`; 60s TTL fallback |
| chat_id redaction claim unimplementable as written | HIGH-10 | Structured keys only in telegram modules; structlog processor hashes chat_id/from_user_id keys; aiogram logger → WARNING |
| CF Bypass creates attack surface on leaked secret | HIGH-11 | CF WAF IP rule to Telegram ranges; webhook_secret auto-rotated on every config save |
| settings.public_base_url doesn't exist | HIGH-12 | app_config[telegram.public_base_url]; editable at runtime |
| Conversation Redis race condition | HIGH-13 | Per-chat asyncio lock; in-flight reply → "previous reply still in progress" |
| delivery_channels defaults to in_app only | MED-1 | Documented in §6a; owner adds "telegram" via PUT /api/alerts/{id} |
| telegram_command_log missing indexes | MED-2 | idx_tg_cmd_chat_ts + idx_tg_cmd_outcome_ts in §7 |
| Rate limit single bucket: /status exhausts /kill_switch budget | MED-3 | Two buckets: read (10/min) + write (3/min) |
| feed_update exceptions cause 500 → Telegram retries | MED-4 (originally listed as HIGH) | Catch-all try/except; always 200; bot=None → 503 |
| CSRF header flip bundled with chunk B | MED-5 | 11c-B.0 pre-step commit ships the flip independently |
| No latency metrics | MED-6 | Added telegram_send_message_duration_seconds + telegram_webhook_duration_seconds histograms |
| from_user_id NOT NULL crashes anonymous updates | MED-7 | from_user_id nullable; private-chat-only middleware drops anonymous updates before command_log |
| (Re-labelled from MED-8 in review) from_user_id unused for auth | — | Now part of allowlist entry; used for authz |
| AI capability REALTIME_SENTIMENT wrong for chat | MED-8 | Changed to REASONING in §6d |
| /kill_switch test coverage thin | MED-9 | Expanded test matrix in §14 |
| CF WAF IP note | LOW (applied inline) | §3 ops checklist + §13 security |
| /help grammar unspecified | LOW | §6c documents grammar |
| test-message sends to all chats | LOW | §8 scoped to single chat_id |
| Retention 1 year for noisy outcome rows | LOW | §7 tiered retention |
| callback_query updates unhandled | LOW | §6b 200-acks unsupported types; 11d wires inline keyboard |
| aiogram pin in pyproject | LOW | uv add aiogram (no version); lockfile is truth |
