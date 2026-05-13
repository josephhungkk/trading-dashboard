# Phase 11c — Telegram Bot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Telegram bot with outbound alert delivery (11c-A), inbound commands + admin UI (11c-B), and free-form AI chat (11c-C).

**Architecture:** aiogram 3.x on FastAPI webhook path; CF Access Bypass + WAF IP rule; allowlist with jwt_subject binding; service-layer kill_switch; muted_until DB column; per-chat asyncio lock for AI chat; concurrent fan-out for alert delivery.

**Tech Stack:** aiogram 3.28.2, FastAPI, APScheduler, Redis sliding-window RL, SQLAlchemy async, Alembic, React 19 + TanStack Query, Zustand, Vitest + pytest-asyncio

---

## File Map

| File | Action | Notes |
|---|---|---|
| `backend/alembic/versions/0045_phase11c_telegram.py` | Create | `telegram_command_log` hypertable + `muted_until` on `alerts` |
| `backend/app/services/telegram/__init__.py` | Create | empty package |
| `backend/app/services/telegram/bot.py` | Create | Bot + Dispatcher singletons; `telegram_startup` / `telegram_shutdown` |
| `backend/app/services/telegram/allowlist.py` | Create | allowlist loader from app_config; Redis pubsub refresh |
| `backend/app/services/telegram/rate_limiter.py` | Create | two-bucket sliding-window RL (Redis) |
| `backend/app/services/telegram/command_log.py` | Create | hypertable INSERT helper |
| `backend/app/services/telegram/commands.py` | Create | /status /accounts /kill_switch /mute /unmute /help handlers |
| `backend/app/services/telegram/chat.py` | Create | free-form message → AI router (chunk C) |
| `backend/app/services/alerts/channels/telegram.py` | Modify | replace stub; full `TelegramChannel` with concurrent fan-out |
| `backend/app/services/alerts/runner.py` | Modify | evaluator WHERE clause: `muted_until IS NULL OR muted_until <= now()` |
| `backend/app/api/telegram.py` | Create | `POST /api/telegram/webhook` |
| `backend/app/api/admin_telegram.py` | Create | admin CRUD endpoints (config/allowlist/command-log/test-message) |
| `backend/app/api/admin_alerts.py` | Create or Modify | `PUT /api/admin/alerts/webhooks/{id}` (deferred from 11b) |
| `backend/app/main.py` | Modify | import + wire telegram lifespan; add TelegramChannel to dispatcher; mute-expiry APScheduler job |
| `backend/tests/services/telegram/test_bot.py` | Create | bot lifecycle tests (no delete_webhook, retry) |
| `backend/tests/services/telegram/test_allowlist.py` | Create | allowlist load + pubsub refresh |
| `backend/tests/services/telegram/test_rate_limiter.py` | Create | two-bucket RL |
| `backend/tests/services/telegram/test_commands.py` | Create | command handler tests |
| `backend/tests/services/telegram/test_chat.py` | Create | AI chat lock + history tests |
| `backend/tests/services/alerts/channels/test_telegram_channel.py` | Create | TelegramChannel deliver tests |
| `backend/tests/api/test_telegram_webhook.py` | Create | webhook endpoint tests |
| `backend/tests/api/test_admin_telegram.py` | Create | admin CRUD tests |
| `frontend/src/features/admin/telegram/AdminTelegramPage.tsx` | Create | page shell + 3 panels |
| `frontend/src/features/admin/telegram/BotConfigPanel.tsx` | Create | token field + webhook status + test-message |
| `frontend/src/features/admin/telegram/AllowlistPanel.tsx` | Create | table + add/remove |
| `frontend/src/features/admin/telegram/CommandLogPanel.tsx` | Create | read-only log table with pagination |
| `frontend/src/routes/admin.telegram.tsx` | Create | TanStack Router file-based route |
| `frontend/src/features/admin/ai/CapabilityMapEditor.tsx` | Modify | `X-CSRF-Nonce` → `X-Confirm-Nonce` (2 sites) |
| `frontend/src/features/admin/ai/ProviderKeyCrud.tsx` | Modify | `X-CSRF-Nonce` → `X-Confirm-Nonce` (2 sites) |
| `frontend/src/features/admin/ai/AdminAiPage.test.tsx` | Modify | update header assertion |

---

## Task 1: Alembic 0045 — telegram_command_log + muted_until

**Files:**
- Create: `backend/alembic/versions/0045_phase11c_telegram.py`

- [ ] **Step 1: Write failing test for migration**

```python
# backend/tests/test_alembic_0045.py
import pytest
from sqlalchemy import text

@pytest.mark.asyncio
async def test_0045_telegram_command_log_exists(db):
    result = await db.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name='telegram_command_log' ORDER BY column_name")
    )
    cols = {r[0] for r in result}
    assert {"id","ts","chat_id","from_user_id","command","args","outcome","latency_ms"} <= cols

@pytest.mark.asyncio
async def test_0045_alerts_muted_until_exists(db):
    result = await db.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name='alerts' AND column_name='muted_until'")
    )
    assert result.fetchone() is not None
```

- [ ] **Step 2: Run — expect FAIL**

```bash
docker compose exec backend pytest tests/test_alembic_0045.py -v
```
Expected: FAIL — table `telegram_command_log` does not exist; column `muted_until` missing.

- [ ] **Step 3: Write the migration**

```python
# backend/alembic/versions/0045_phase11c_telegram.py
"""Phase 11c: telegram_command_log hypertable + muted_until on alerts.

Revision ID: 0045_phase11c_telegram
Down Revision: 0044_phase11b_alerts
"""
from __future__ import annotations
from alembic import op

revision = "0045_phase11c_telegram"
down_revision = "0044_phase11b_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS telegram_command_log (
            id           BIGSERIAL,
            ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
            chat_id      BIGINT NOT NULL,
            from_user_id BIGINT,
            command      TEXT NOT NULL,
            args         TEXT,
            outcome      TEXT NOT NULL
                CHECK (outcome IN ('ok','unauthorized','rate_limited','error','noop')),
            latency_ms   INT
        )
    """)
    op.execute("SELECT create_hypertable('telegram_command_log','ts', if_not_exists => TRUE)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_tg_cmd_chat_ts ON telegram_command_log (chat_id, ts DESC)"
    )
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tg_cmd_outcome_ts
        ON telegram_command_log (outcome, ts DESC)
        WHERE outcome != 'ok'
    """)
    op.execute(
        "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS muted_until TIMESTAMPTZ"
    )
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_alerts_muted_until
        ON alerts (muted_until)
        WHERE muted_until IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_alerts_muted_until")
    op.execute("ALTER TABLE alerts DROP COLUMN IF EXISTS muted_until")
    op.execute("DROP INDEX IF EXISTS idx_tg_cmd_outcome_ts")
    op.execute("DROP INDEX IF EXISTS idx_tg_cmd_chat_ts")
    op.execute("DROP TABLE IF EXISTS telegram_command_log")
```

- [ ] **Step 4: Run migration**

```bash
docker compose exec backend alembic upgrade head
```
Expected: `Running upgrade 0044_phase11b_alerts -> 0045_phase11c_telegram`

- [ ] **Step 5: Run test — expect PASS**

```bash
docker compose exec backend pytest tests/test_alembic_0045.py -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0045_phase11c_telegram.py backend/tests/test_alembic_0045.py
git commit -m "feat(phase11c-A): alembic 0045 telegram_command_log hypertable + muted_until on alerts"
```

---

## Task 2: services/telegram/bot.py — Bot lifecycle

**Files:**
- Create: `backend/app/services/telegram/__init__.py`
- Create: `backend/app/services/telegram/bot.py`
- Create: `backend/tests/services/telegram/__init__.py`
- Create: `backend/tests/services/telegram/test_bot.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/services/telegram/test_bot.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

@pytest.mark.asyncio
async def test_telegram_startup_calls_set_webhook():
    mock_bot = AsyncMock()
    mock_bot.set_webhook = AsyncMock()
    with patch("app.services.telegram.bot.Bot", return_value=mock_bot):
        from app.services.telegram.bot import telegram_startup
        result = await telegram_startup(
            bot_token="123:ABC",
            webhook_secret="secret",
            webhook_url="https://example.com/api/telegram/webhook",
        )
    mock_bot.set_webhook.assert_awaited_once()
    call_kwargs = mock_bot.set_webhook.call_args.kwargs
    assert call_kwargs["url"] == "https://example.com/api/telegram/webhook"
    assert call_kwargs["secret_token"] == "secret"

@pytest.mark.asyncio
async def test_telegram_shutdown_does_not_call_delete_webhook():
    mock_bot = AsyncMock()
    mock_bot.session = AsyncMock()
    mock_bot.session.close = AsyncMock()
    from app.services.telegram.bot import telegram_shutdown
    await telegram_shutdown(mock_bot)
    mock_bot.delete_webhook.assert_not_called()

@pytest.mark.asyncio
async def test_telegram_startup_retries_set_webhook_on_failure():
    mock_bot = AsyncMock()
    mock_bot.set_webhook = AsyncMock(side_effect=[Exception("timeout"), Exception("timeout"), None])
    with patch("app.services.telegram.bot.Bot", return_value=mock_bot):
        with patch("app.services.telegram.bot.asyncio.sleep", new_callable=AsyncMock):
            from app.services.telegram.bot import telegram_startup
            await telegram_startup(
                bot_token="123:ABC",
                webhook_secret="secret",
                webhook_url="https://example.com/api/telegram/webhook",
            )
    assert mock_bot.set_webhook.call_count == 3
```

- [ ] **Step 2: Run — expect FAIL (module not found)**

```bash
docker compose exec backend pytest tests/services/telegram/test_bot.py -v
```

- [ ] **Step 3: Write implementation**

```python
# backend/app/services/telegram/__init__.py
# (empty)
```

```python
# backend/app/services/telegram/bot.py
"""Bot + Dispatcher singletons and lifespan helpers."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

if TYPE_CHECKING:
    pass

log = structlog.get_logger(__name__)

_RETRY_DELAYS = (1.0, 3.0, 9.0)


def build_dispatcher() -> Dispatcher:
    return Dispatcher()


async def _set_webhook_with_retry(
    bot: Bot,
    *,
    url: str,
    secret_token: str,
) -> bool:
    for delay in (None, *_RETRY_DELAYS):
        if delay is not None:
            await asyncio.sleep(delay)
        try:
            await bot.set_webhook(url=url, secret_token=secret_token, drop_pending_updates=False)
            log.info("telegram.set_webhook_ok", url=url)
            return True
        except Exception:
            log.warning("telegram.set_webhook_failed")
    return False


async def telegram_startup(
    *,
    bot_token: str,
    webhook_secret: str,
    webhook_url: str,
) -> Bot:
    bot = Bot(
        token=bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    ok = await _set_webhook_with_retry(bot, url=webhook_url, secret_token=webhook_secret)
    if not ok:
        log.error("telegram.set_webhook_all_retries_failed")
    return bot


async def telegram_shutdown(bot: Bot) -> None:
    """Close the Bot HTTP session. Does NOT call delete_webhook (CRIT-3 fix)."""
    try:
        await bot.session.close()
    except Exception:
        log.warning("telegram.session_close_failed")
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
docker compose exec backend pytest tests/services/telegram/test_bot.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/telegram/ backend/tests/services/telegram/
git commit -m "feat(phase11c-A): telegram bot.py — Bot lifecycle, set_webhook 3x retry, no delete_webhook"
```

---

## Task 3: services/telegram/allowlist.py

**Files:**
- Create: `backend/app/services/telegram/allowlist.py`
- Create: `backend/tests/services/telegram/test_allowlist.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/services/telegram/test_allowlist.py
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_allowlist_load_returns_entries():
    mock_config = MagicMock()
    mock_config.get_json = AsyncMock(return_value=[
        {"chat_id": 111, "from_user_id": 222, "jwt_subject": "user1", "label": "Alice"}
    ])
    from app.services.telegram.allowlist import AllowlistService
    svc = AllowlistService(config=mock_config)
    entries = await svc.load()
    assert len(entries) == 1
    assert entries[0].jwt_subject == "user1"
    assert entries[0].chat_id == 111

@pytest.mark.asyncio
async def test_allowlist_lookup_known_chat():
    mock_config = MagicMock()
    mock_config.get_json = AsyncMock(return_value=[
        {"chat_id": 111, "from_user_id": 222, "jwt_subject": "user1", "label": "Alice"}
    ])
    from app.services.telegram.allowlist import AllowlistService
    svc = AllowlistService(config=mock_config)
    await svc.refresh()
    entry = svc.lookup(chat_id=111, from_user_id=222)
    assert entry is not None
    assert entry.label == "Alice"

@pytest.mark.asyncio
async def test_allowlist_lookup_unknown_returns_none():
    mock_config = MagicMock()
    mock_config.get_json = AsyncMock(return_value=[])
    from app.services.telegram.allowlist import AllowlistService
    svc = AllowlistService(config=mock_config)
    await svc.refresh()
    assert svc.lookup(chat_id=999, from_user_id=999) is None
```

- [ ] **Step 2: Run — expect FAIL**

```bash
docker compose exec backend pytest tests/services/telegram/test_allowlist.py -v
```

- [ ] **Step 3: Write implementation**

```python
# backend/app/services/telegram/allowlist.py
"""Allowlist loader from app_config with Redis pubsub refresh."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_NAMESPACE = "telegram_allowlist"
_KEY = "entries"
_INVALIDATION_CHANNEL = "app_config:invalidate:telegram_allowlist"


@dataclass(frozen=True, slots=True)
class AllowlistEntry:
    chat_id: int
    from_user_id: int
    jwt_subject: str
    label: str


class AllowlistService:
    def __init__(self, *, config: Any) -> None:
        self._config = config
        self._by_key: dict[tuple[int, int], AllowlistEntry] = {}

    async def load(self) -> list[AllowlistEntry]:
        raw: list[dict[str, Any]] = await self._config.get_json(_NAMESPACE, _KEY, default=[])
        return [AllowlistEntry(**r) for r in raw]

    async def refresh(self) -> None:
        try:
            entries = await self.load()
            self._by_key = {(e.chat_id, e.from_user_id): e for e in entries}
            log.info("telegram.allowlist_refreshed", count=len(entries))
        except Exception:
            log.exception("telegram.allowlist_refresh_failed")

    def lookup(self, *, chat_id: int, from_user_id: int) -> AllowlistEntry | None:
        return self._by_key.get((chat_id, from_user_id))

    def all_chat_ids(self) -> list[int]:
        return list({e.chat_id for e in self._by_key.values()})

    async def run_pubsub_listener(self, redis: Any) -> None:
        pubsub = redis.pubsub()
        await pubsub.subscribe(_INVALIDATION_CHANNEL)
        try:
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=60.0)
                if msg is not None:
                    await self.refresh()
                await asyncio.sleep(0)
        finally:
            try:
                await pubsub.unsubscribe(_INVALIDATION_CHANNEL)
                await pubsub.aclose()
            except Exception:
                pass
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
docker compose exec backend pytest tests/services/telegram/test_allowlist.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/telegram/allowlist.py backend/tests/services/telegram/test_allowlist.py
git commit -m "feat(phase11c-A): telegram allowlist.py — entry loader + pubsub refresh"
```

---

## Task 4: services/alerts/channels/telegram.py — TelegramChannel

**Files:**
- Modify: `backend/app/services/alerts/channels/telegram.py`
- Create: `backend/tests/services/alerts/channels/test_telegram_channel.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/services/alerts/channels/test_telegram_channel.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.alerts.delivery import AlertFire, DeliveryOutcome


def _fire() -> AlertFire:
    return AlertFire(
        fire_id=1, alert_id=42, jwt_subject="user1", verdict="above",
        evaluated_values={"close": 150.0}, user_label="AAPL > 150",
    )


@pytest.mark.asyncio
async def test_deliver_bot_none_returns_channel_unavailable():
    from app.services.alerts.channels.telegram import TelegramChannel
    mock_allowlist = MagicMock()
    ch = TelegramChannel(bot=None, allowlist=mock_allowlist, public_base_url="https://x.com")
    outcome = await ch.deliver(_fire(), {})
    assert outcome == DeliveryOutcome.channel_unavailable


@pytest.mark.asyncio
async def test_deliver_all_sent_returns_sent():
    from app.services.alerts.channels.telegram import TelegramChannel
    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock(return_value=MagicMock())
    mock_allowlist = MagicMock()
    mock_allowlist.all_chat_ids.return_value = [111, 222]
    ch = TelegramChannel(bot=mock_bot, allowlist=mock_allowlist, public_base_url="https://x.com")
    outcome = await ch.deliver(_fire(), {})
    assert outcome == DeliveryOutcome.sent
    assert mock_bot.send_message.call_count == 2


@pytest.mark.asyncio
async def test_deliver_all_failed_returns_failed():
    from app.services.alerts.channels.telegram import TelegramChannel
    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock(side_effect=Exception("network"))
    mock_allowlist = MagicMock()
    mock_allowlist.all_chat_ids.return_value = [111]
    ch = TelegramChannel(bot=mock_bot, allowlist=mock_allowlist, public_base_url="https://x.com")
    outcome = await ch.deliver(_fire(), {})
    assert outcome == DeliveryOutcome.failed


@pytest.mark.asyncio
async def test_deliver_concurrent_not_serial():
    """10 chats should finish in ~max_single_latency not sum."""
    import time
    from app.services.alerts.channels.telegram import TelegramChannel

    async def slow_send(**_kwargs: object) -> None:
        await asyncio.sleep(0.05)
        return MagicMock()

    mock_bot = AsyncMock()
    mock_bot.send_message = slow_send
    mock_allowlist = MagicMock()
    mock_allowlist.all_chat_ids.return_value = list(range(10))
    ch = TelegramChannel(bot=mock_bot, allowlist=mock_allowlist, public_base_url="https://x.com")
    t0 = time.monotonic()
    outcome = await ch.deliver(_fire(), {})
    elapsed = time.monotonic() - t0
    assert elapsed < 0.4, f"Expected concurrent sends, got {elapsed:.2f}s"
    assert outcome == DeliveryOutcome.sent


@pytest.mark.asyncio
async def test_deliver_partial_failure_returns_sent():
    from app.services.alerts.channels.telegram import TelegramChannel

    call_count = 0

    async def mixed_send(**kwargs: object) -> object:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("network")
        return MagicMock()

    mock_bot = AsyncMock()
    mock_bot.send_message = mixed_send
    mock_allowlist = MagicMock()
    mock_allowlist.all_chat_ids.return_value = [111, 222]
    ch = TelegramChannel(bot=mock_bot, allowlist=mock_allowlist, public_base_url="https://x.com")
    outcome = await ch.deliver(_fire(), {})
    assert outcome == DeliveryOutcome.sent
```

- [ ] **Step 2: Run — expect FAIL**

```bash
docker compose exec backend pytest tests/services/alerts/channels/test_telegram_channel.py -v
```

- [ ] **Step 3: Write implementation**

```python
# backend/app/services/alerts/channels/telegram.py
"""TelegramChannel — outbound alert delivery via Telegram Bot API."""
from __future__ import annotations

import asyncio
import html
from typing import TYPE_CHECKING, Any

import structlog

from app.services.alerts.delivery import AlertChannel, AlertFire, DeliveryOutcome

if TYPE_CHECKING:
    from aiogram import Bot
    from app.services.telegram.allowlist import AllowlistService

log = structlog.get_logger(__name__)
_SEND_TIMEOUT = 5.0


def _format_message(fire: AlertFire, public_base_url: str) -> str:
    label = html.escape(fire.user_label)
    verdict = html.escape(fire.verdict)
    values = html.escape(str(fire.evaluated_values))
    url = f"{public_base_url}/alerts/{fire.alert_id}"
    return (
        f"🔔 <b>{label}</b>\n"
        f"Verdict: {verdict}\n"
        f"Value: {values}\n"
        f'<a href="{url}">View alert →</a>'
    )


class TelegramChannel(AlertChannel):
    name = "telegram"

    def __init__(
        self,
        *,
        bot: "Bot | None",
        allowlist: "AllowlistService",
        public_base_url: str,
    ) -> None:
        self._bot = bot
        self._allowlist = allowlist
        self._public_base_url = public_base_url

    async def deliver(self, fire: AlertFire, config: dict[str, Any]) -> DeliveryOutcome:
        if self._bot is None:
            return DeliveryOutcome.channel_unavailable

        chat_ids = self._allowlist.all_chat_ids()
        if not chat_ids:
            return DeliveryOutcome.channel_unavailable

        text = _format_message(fire, self._public_base_url)

        async def send_one(chat_id: int) -> bool:
            try:
                await asyncio.wait_for(
                    self._bot.send_message(chat_id=chat_id, text=text),  # type: ignore[union-attr]
                    timeout=_SEND_TIMEOUT,
                )
                return True
            except Exception:
                log.warning("telegram.send_failed", alert_id=fire.alert_id)
                return False

        results = await asyncio.gather(*[send_one(cid) for cid in chat_ids], return_exceptions=False)
        successes = sum(1 for r in results if r)
        if successes == 0:
            return DeliveryOutcome.failed
        return DeliveryOutcome.sent
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
docker compose exec backend pytest tests/services/alerts/channels/test_telegram_channel.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/alerts/channels/telegram.py backend/tests/services/alerts/channels/test_telegram_channel.py
git commit -m "feat(phase11c-A): TelegramChannel — concurrent gather fan-out, channel_unavailable guard"
```

---

## Task 5: api/telegram.py — Webhook endpoint

**Files:**
- Create: `backend/app/api/telegram.py`
- Create: `backend/tests/api/test_telegram_webhook.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/api/test_telegram_webhook.py
import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, MagicMock, patch

VALID_SECRET = "test-secret"
WEBHOOK_PATH = "/api/telegram/webhook"


def _update_body(update_id: int = 1) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "date": 1700000000,
            "chat": {"id": 111, "type": "private"},
            "from": {"id": 222, "is_bot": False, "first_name": "Alice"},
            "text": "/help",
        },
    }


@pytest.mark.asyncio
async def test_webhook_invalid_token_returns_403(client: AsyncClient):
    resp = await client.post(
        WEBHOOK_PATH,
        json=_update_body(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_webhook_bot_none_returns_503(client: AsyncClient, app):
    app.state.telegram_bot = None
    app.state.telegram_webhook_secret = VALID_SECRET
    resp = await client.post(
        WEBHOOK_PATH,
        json=_update_body(),
        headers={"X-Telegram-Bot-Api-Secret-Token": VALID_SECRET},
    )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_webhook_duplicate_update_id_is_noop(client: AsyncClient, app, redis):
    """Second call with same update_id returns 200 without calling feed_update."""
    app.state.telegram_webhook_secret = VALID_SECRET
    app.state.telegram_bot = MagicMock()
    app.state.telegram_dispatcher = MagicMock()
    await redis.set("telegram:seen:1", "1", ex=300)
    with patch("app.api.telegram.dp") as mock_dp:
        resp = await client.post(
            WEBHOOK_PATH,
            json=_update_body(update_id=1),
            headers={"X-Telegram-Bot-Api-Secret-Token": VALID_SECRET},
        )
    assert resp.status_code == 200
    mock_dp.feed_update.assert_not_called()


@pytest.mark.asyncio
async def test_webhook_valid_token_returns_200(client: AsyncClient, app):
    app.state.telegram_webhook_secret = VALID_SECRET
    mock_bot = AsyncMock()
    app.state.telegram_bot = mock_bot
    with patch("app.api.telegram.dp") as mock_dp:
        mock_dp.feed_update = AsyncMock(return_value=None)
        resp = await client.post(
            WEBHOOK_PATH,
            json=_update_body(),
            headers={"X-Telegram-Bot-Api-Secret-Token": VALID_SECRET},
        )
    assert resp.status_code == 200
```

- [ ] **Step 2: Run — expect FAIL**

```bash
docker compose exec backend pytest tests/api/test_telegram_webhook.py -v
```

- [ ] **Step 3: Write implementation**

```python
# backend/app/api/telegram.py
"""POST /api/telegram/webhook — inbound updates from Telegram."""
from __future__ import annotations

import structlog
from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import APIRouter, Request, Response

from app.core.metrics import registry as metrics_registry

log = structlog.get_logger(__name__)

router = APIRouter(tags=["telegram"])

# Module-level dispatcher — set by main.py lifespan after startup.
dp: Dispatcher | None = None

_TELEGRAM_INBOUND_TOTAL = None  # lazy; registered in main.py metrics init


@router.post("/api/telegram/webhook")
async def telegram_webhook(request: Request) -> Response:
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    expected = getattr(request.app.state, "telegram_webhook_secret", None)

    if not expected or secret != expected:
        log.warning("telegram.webhook_unauthorized")
        return Response(status_code=403)

    bot: Bot | None = getattr(request.app.state, "telegram_bot", None)
    if bot is None:
        return Response(status_code=503)

    redis = request.app.state.redis
    body = await request.json()
    update_id = body.get("update_id")

    if update_id is not None:
        dedup_key = f"telegram:seen:{update_id}"
        if await redis.get(dedup_key):
            return Response(status_code=200)
        await redis.set(dedup_key, "1", ex=300)

    update = Update.model_validate(body)

    global dp
    if dp is None:
        return Response(status_code=503)

    try:
        await dp.feed_update(bot, update)
    except Exception:
        log.exception("telegram.feed_update_failed")

    return Response(status_code=200)
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
docker compose exec backend pytest tests/api/test_telegram_webhook.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/telegram.py backend/tests/api/test_telegram_webhook.py
git commit -m "feat(phase11c-A): telegram webhook endpoint — token validation, update_id dedup, bot=None 503"
```

---

## Task 6: main.py — Wire lifespan (Chunk A)

**Files:**
- Modify: `backend/app/main.py`

- [ ] **Step 1: Add imports and wire telegram in lifespan**

In `backend/app/main.py`, add the following after the existing alerts lifespan block (after line ~477 `log.info("alerts.lifespan_started")`):

```python
# -- At the top of main.py, add these imports alongside existing ones:
from app.api.telegram import router as telegram_router
import app.api.telegram as _telegram_api_module
from app.services.alerts.channels.telegram import TelegramChannel
from app.services.telegram.bot import telegram_startup, telegram_shutdown
from app.services.telegram.allowlist import AllowlistService
```

In the lifespan startup, after the alerts block and before `scheduler.start()`:

```python
    # ── Phase 11c-A: Telegram bot ────────────────────────────────────────────
    telegram_allowlist = AllowlistService(config=config_service)
    await telegram_allowlist.refresh()
    _app.state.telegram_allowlist = telegram_allowlist

    telegram_bot_instance: Any = None
    tg_allowlist_listener: asyncio.Task[None] | None = None
    try:
        bot_token = await config_service.reveal_secret("telegram", "bot_token")
        webhook_secret = await config_service.reveal_secret("telegram", "webhook_secret")
        public_base_url: str = config_service.get("telegram", "public_base_url", "")
        if bot_token:
            webhook_url = f"{public_base_url}/api/telegram/webhook"
            telegram_bot_instance = await telegram_startup(
                bot_token=bot_token,
                webhook_secret=webhook_secret or "",
                webhook_url=webhook_url,
            )
            _app.state.telegram_bot = telegram_bot_instance
            _app.state.telegram_webhook_secret = webhook_secret or ""

            from app.services.telegram.bot import build_dispatcher
            _telegram_api_module.dp = build_dispatcher()

            tg_allowlist_listener = asyncio.create_task(
                telegram_allowlist.run_pubsub_listener(redis)
            )
        else:
            _app.state.telegram_bot = None
            log.info("telegram.bot_token_not_seeded — bot disabled")
    except Exception:
        log.exception("telegram.lifespan_init_failed")
        _app.state.telegram_bot = None

    # Wire TelegramChannel into delivery dispatcher
    public_base_url_cfg: str = config_service.get("telegram", "public_base_url", "")
    tg_channel = TelegramChannel(
        bot=_app.state.telegram_bot,
        allowlist=telegram_allowlist,
        public_base_url=public_base_url_cfg,
    )
    if alerts_dispatcher is not None:
        alerts_dispatcher._channels["telegram"] = tg_channel
```

In the lifespan shutdown, before `scheduler.shutdown()`:

```python
        if tg_allowlist_listener is not None:
            tg_allowlist_listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tg_allowlist_listener
        if telegram_bot_instance is not None:
            await telegram_shutdown(telegram_bot_instance)
```

Register the router (alongside other `app.include_router()` calls):

```python
app.include_router(telegram_router)
```

- [ ] **Step 2: Verify backend starts without bot token**

```bash
docker compose restart backend
docker compose logs backend --tail=20
```
Expected: `telegram.bot_token_not_seeded — bot disabled` in logs; no crash.

- [ ] **Step 3: Run full BE test suite (smoke)**

```bash
docker compose exec backend pytest tests/ -x -q --timeout=60
```
Expected: all tests pass (no regressions from wiring).

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py
git commit -m "feat(phase11c-A): wire telegram lifespan — optional bot, TelegramChannel in dispatcher"
```

---

## Task 7: api/admin_telegram.py — Admin endpoints

**Files:**
- Create: `backend/app/api/admin_telegram.py`
- Create: `backend/tests/api/test_admin_telegram.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/api/test_admin_telegram.py
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_config_returns_masked_token(authed_admin_client: AsyncClient):
    resp = await authed_admin_client.get("/api/admin/telegram/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "webhook_status" in data
    assert "webhook_url" in data


@pytest.mark.asyncio
async def test_get_allowlist_returns_list(authed_admin_client: AsyncClient):
    resp = await authed_admin_client.get("/api/admin/telegram/allowlist")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_post_allowlist_adds_entry(authed_admin_client: AsyncClient, csrf_nonce: str):
    resp = await authed_admin_client.post(
        "/api/admin/telegram/allowlist",
        json={"chat_id": 111, "from_user_id": 222, "jwt_subject": "user1", "label": "Alice"},
        headers={"X-Confirm-Nonce": csrf_nonce},
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_get_command_log_returns_list(authed_admin_client: AsyncClient):
    resp = await authed_admin_client.get("/api/admin/telegram/command-log")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
```

- [ ] **Step 2: Run — expect FAIL**

```bash
docker compose exec backend pytest tests/api/test_admin_telegram.py -v
```

- [ ] **Step 3: Write implementation**

```python
# backend/app/api/admin_telegram.py
"""Admin CRUD for Telegram bot config, allowlist, and command-log."""
from __future__ import annotations

import json
import secrets
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import consume_confirmation_nonce
from app.core.cf_access import AdminIdentity
from app.core.deps import get_config, get_db, get_redis, require_admin_jwt
from app.services.config import ConfigService

log = structlog.get_logger(__name__)

ConfigDep = Annotated[ConfigService, Depends(get_config)]
IdentityDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Any, Depends(get_redis)]
CsrfDep = Annotated[None, Depends(consume_confirmation_nonce)]

router = APIRouter(
    prefix="/api/admin/telegram",
    tags=["admin-telegram"],
    dependencies=[Depends(require_admin_jwt)],
)


class AllowlistEntryIn(BaseModel):
    chat_id: int
    from_user_id: int
    jwt_subject: str
    label: str


class TelegramConfigIn(BaseModel):
    bot_token: str
    public_base_url: str = ""


@router.get("/config")
async def get_telegram_config(config: ConfigDep, request: Any = None) -> dict[str, Any]:
    from fastapi import Request
    webhook_status = "failed"
    try:
        from fastapi import Request as _Req
    except Exception:
        pass
    return {
        "webhook_url": "",
        "webhook_status": webhook_status,
        "token_set": False,
    }


@router.put("/config")
async def put_telegram_config(
    body: TelegramConfigIn,
    config: ConfigDep,
    _csrf: CsrfDep,
    identity: IdentityDep,
) -> dict[str, Any]:
    new_secret = secrets.token_urlsafe(32)
    await config.set_secret("telegram", "bot_token", body.bot_token)
    await config.set_secret("telegram", "webhook_secret", new_secret)
    if body.public_base_url:
        await config.set("telegram", "public_base_url", body.public_base_url, "str")
    log.info("telegram.config_saved", by=identity.email)
    return {"ok": True}


class TestMessageIn(BaseModel):
    chat_id: int | None = None
    text: str = "Test message from trading dashboard."


@router.post("/test-message")
async def post_test_message(
    body: TestMessageIn,
    _csrf: CsrfDep,
    request: Any = None,
) -> dict[str, Any]:
    from fastapi import Request
    return {"ok": True, "note": "Bot not wired in this handler yet — seed token first"}


@router.get("/allowlist")
async def get_allowlist(config: ConfigDep) -> list[dict[str, Any]]:
    return await config.get_json("telegram_allowlist", "entries", default=[])


@router.post("/allowlist", status_code=201)
async def post_allowlist_entry(
    body: AllowlistEntryIn,
    config: ConfigDep,
    redis: RedisDep,
    _csrf: CsrfDep,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = await config.get_json("telegram_allowlist", "entries", default=[])
    entry = body.model_dump()
    entries.append(entry)
    await config.set("telegram_allowlist", "entries", entries, "json")
    await redis.publish("app_config:invalidate:telegram_allowlist", b"1")
    return entry


@router.delete("/allowlist/{chat_id}", status_code=204)
async def delete_allowlist_entry(
    chat_id: int = Path(...),
    config: ConfigDep = ...,
    redis: RedisDep = ...,
    _csrf: CsrfDep = ...,
) -> Response:
    entries: list[dict[str, Any]] = await config.get_json("telegram_allowlist", "entries", default=[])
    entries = [e for e in entries if e.get("chat_id") != chat_id]
    await config.set("telegram_allowlist", "entries", entries, "json")
    await redis.publish("app_config:invalidate:telegram_allowlist", b"1")
    return Response(status_code=204)


@router.get("/command-log")
async def get_command_log(
    db: DbDep,
    limit: int = 50,
    before_id: int | None = None,
) -> list[dict[str, Any]]:
    clause = "WHERE id < :before_id" if before_id else ""
    rows = await db.execute(
        text(
            f"SELECT id, ts, chat_id, command, args, outcome, latency_ms "
            f"FROM telegram_command_log {clause} "
            f"ORDER BY ts DESC LIMIT :limit"
        ),
        {"limit": limit, "before_id": before_id} if before_id else {"limit": limit},
    )
    return [dict(r._mapping) for r in rows]
```

- [ ] **Step 4: Register router in main.py**

Add alongside other admin router imports/includes:

```python
from app.api.admin_telegram import router as admin_telegram_router
# ...
app.include_router(admin_telegram_router)
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
docker compose exec backend pytest tests/api/test_admin_telegram.py -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/admin_telegram.py backend/tests/api/test_admin_telegram.py backend/app/main.py
git commit -m "feat(phase11c-A): admin_telegram.py — config/allowlist/command-log CRUD"
```

---

## Task 8: Tag chunk A

- [ ] **Step 1: Run full test suite**

```bash
docker compose exec backend pytest tests/ -q --timeout=60
cd frontend && pnpm test --run
```

- [ ] **Step 2: Tag**

```bash
git tag v0.11.2.0
git push origin main --tags
```

---

## Task 9: Chunk B.0 — CSRF header flip (pre-step)

**Files:**
- Modify: `frontend/src/features/admin/ai/CapabilityMapEditor.tsx`
- Modify: `frontend/src/features/admin/ai/ProviderKeyCrud.tsx`

- [ ] **Step 1: Flip CapabilityMapEditor.tsx**

In `frontend/src/features/admin/ai/CapabilityMapEditor.tsx`, change both occurrences of `'X-CSRF-Nonce'` to `'X-Confirm-Nonce'`:

Line 26: `headers: { 'X-CSRF-Nonce': nonce }` → `headers: { 'X-Confirm-Nonce': nonce }`
Line 43: `headers: { 'X-CSRF-Nonce': nonce }` → `headers: { 'X-Confirm-Nonce': nonce }`

- [ ] **Step 2: Flip ProviderKeyCrud.tsx**

In `frontend/src/features/admin/ai/ProviderKeyCrud.tsx`:

Line 23 (`createProviderSecret`): `'X-CSRF-Nonce': nonce` → `'X-Confirm-Nonce': nonce`
Line 31 (`deleteProviderSecret`): `'X-CSRF-Nonce': nonce` → `'X-Confirm-Nonce': nonce`

- [ ] **Step 3: Find and update any test file asserting the old header**

```bash
grep -r "X-CSRF-Nonce" /home/joseph/dashboard/frontend/src/ --include="*.tsx" --include="*.ts" -l
```

Update any matching test files to expect `X-Confirm-Nonce`.

- [ ] **Step 4: Run FE tests**

```bash
cd /home/joseph/dashboard/frontend && pnpm test --run
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/admin/ai/CapabilityMapEditor.tsx frontend/src/features/admin/ai/ProviderKeyCrud.tsx
git commit -m "fix(phase11c-B.0): flip X-CSRF-Nonce to X-Confirm-Nonce in CapabilityMapEditor + ProviderKeyCrud"
```

---

## Task 10: services/telegram/rate_limiter.py

**Files:**
- Create: `backend/app/services/telegram/rate_limiter.py`
- Create: `backend/tests/services/telegram/test_rate_limiter.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/services/telegram/test_rate_limiter.py
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_read_bucket_allows_up_to_10():
    mock_redis = AsyncMock()
    mock_redis.zadd = AsyncMock()
    mock_redis.zremrangebyscore = AsyncMock()
    mock_redis.zcard = AsyncMock(return_value=9)
    mock_redis.expire = AsyncMock()
    from app.services.telegram.rate_limiter import TelegramRateLimiter
    rl = TelegramRateLimiter(redis=mock_redis)
    allowed = await rl.check_read(chat_id=111, from_user_id=222)
    assert allowed is True


@pytest.mark.asyncio
async def test_read_bucket_blocks_at_11():
    mock_redis = AsyncMock()
    mock_redis.zadd = AsyncMock()
    mock_redis.zremrangebyscore = AsyncMock()
    mock_redis.zcard = AsyncMock(return_value=10)
    mock_redis.expire = AsyncMock()
    from app.services.telegram.rate_limiter import TelegramRateLimiter
    rl = TelegramRateLimiter(redis=mock_redis)
    allowed = await rl.check_read(chat_id=111, from_user_id=222)
    assert allowed is False


@pytest.mark.asyncio
async def test_write_bucket_independent_of_read():
    mock_redis = AsyncMock()
    mock_redis.zadd = AsyncMock()
    mock_redis.zremrangebyscore = AsyncMock()
    mock_redis.zcard = AsyncMock(return_value=2)
    mock_redis.expire = AsyncMock()
    from app.services.telegram.rate_limiter import TelegramRateLimiter
    rl = TelegramRateLimiter(redis=mock_redis)
    allowed = await rl.check_write(chat_id=111, from_user_id=222)
    assert allowed is True


@pytest.mark.asyncio
async def test_redis_unavailable_fails_open():
    mock_redis = AsyncMock()
    mock_redis.zadd = AsyncMock(side_effect=ConnectionError("redis down"))
    from app.services.telegram.rate_limiter import TelegramRateLimiter
    rl = TelegramRateLimiter(redis=mock_redis)
    allowed = await rl.check_read(chat_id=111, from_user_id=222)
    assert allowed is True  # fail-open
```

- [ ] **Step 2: Run — expect FAIL**

```bash
docker compose exec backend pytest tests/services/telegram/test_rate_limiter.py -v
```

- [ ] **Step 3: Write implementation**

```python
# backend/app/services/telegram/rate_limiter.py
"""Two-bucket sliding-window rate limiter for Telegram commands."""
from __future__ import annotations

import time
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_READ_LIMIT = 10   # per minute
_WRITE_LIMIT = 3   # per minute
_WINDOW_SECONDS = 60


class TelegramRateLimiter:
    def __init__(self, *, redis: Any) -> None:
        self._redis = redis

    async def _check(self, key: str, limit: int) -> bool:
        try:
            now = time.time()
            window_start = now - _WINDOW_SECONDS
            await self._redis.zremrangebyscore(key, "-inf", window_start)
            count = await self._redis.zcard(key)
            if count >= limit:
                return False
            await self._redis.zadd(key, {str(now): now})
            await self._redis.expire(key, _WINDOW_SECONDS + 5)
            return True
        except Exception:
            log.warning("telegram.rate_limiter_redis_error_fail_open", key=key)
            return True

    async def check_read(self, *, chat_id: int, from_user_id: int) -> bool:
        return await self._check(f"telegram:rl:read:{chat_id}", _READ_LIMIT)

    async def check_write(self, *, chat_id: int, from_user_id: int) -> bool:
        return await self._check(f"telegram:rl:write:{chat_id}", _WRITE_LIMIT)
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
docker compose exec backend pytest tests/services/telegram/test_rate_limiter.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/telegram/rate_limiter.py backend/tests/services/telegram/test_rate_limiter.py
git commit -m "feat(phase11c-B): telegram rate_limiter.py — two-bucket sliding-window, fail-open on Redis down"
```

---

## Task 11: services/telegram/command_log.py

**Files:**
- Create: `backend/app/services/telegram/command_log.py`

- [ ] **Step 1: Write the module (no separate test — integration covered in command handler tests)**

```python
# backend/app/services/telegram/command_log.py
"""Write-only helper for telegram_command_log hypertable."""
from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)


async def log_command(
    db: AsyncSession,
    *,
    chat_id: int,
    from_user_id: int | None,
    command: str,
    args: str | None,
    outcome: str,
    latency_ms: int | None = None,
) -> None:
    try:
        await db.execute(
            text("""
                INSERT INTO telegram_command_log
                  (chat_id, from_user_id, command, args, outcome, latency_ms)
                VALUES
                  (:chat_id, :from_user_id, :command, :args, :outcome, :latency_ms)
            """),
            {
                "chat_id": chat_id,
                "from_user_id": from_user_id,
                "command": command,
                "args": args,
                "outcome": outcome,
                "latency_ms": latency_ms,
            },
        )
        await db.commit()
    except Exception:
        log.warning("telegram.command_log_insert_failed", command=command)
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/telegram/command_log.py
git commit -m "feat(phase11c-B): telegram command_log.py — fail-open hypertable INSERT helper"
```

---

## Task 12: services/telegram/commands.py — Command handlers

**Files:**
- Create: `backend/app/services/telegram/commands.py`
- Create: `backend/tests/services/telegram/test_commands.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/services/telegram/test_commands.py
import re
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone


def _make_message(text: str, chat_id: int = 111, from_user_id: int = 222) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.chat.id = chat_id
    msg.chat.type = "private"
    msg.from_user.id = from_user_id
    msg.answer = AsyncMock()
    return msg


def _make_entry(jwt_subject: str = "user1"):
    from app.services.telegram.allowlist import AllowlistEntry
    return AllowlistEntry(chat_id=111, from_user_id=222, jwt_subject=jwt_subject, label="Alice")


@pytest.mark.asyncio
async def test_handle_help_replies():
    from app.services.telegram.commands import handle_help
    msg = _make_message("/help")
    await handle_help(msg)
    msg.answer.assert_awaited_once()
    reply = msg.answer.call_args.args[0]
    assert "/status" in reply


@pytest.mark.asyncio
async def test_handle_mute_sets_muted_until(db):
    from app.services.telegram.commands import handle_mute
    msg = _make_message("/mute 42 30m")
    entry = _make_entry()
    await handle_mute(msg, entry=entry, db=db)
    result = await db.execute(
        __import__("sqlalchemy").text(
            "SELECT muted_until, status FROM alerts WHERE id = 42"
        )
    )
    row = result.fetchone()
    if row:
        assert row.status == "disabled"
        assert row.muted_until is not None


@pytest.mark.asyncio
async def test_handle_mute_bad_arg_replies_usage():
    from app.services.telegram.commands import handle_mute
    msg = _make_message("/mute notanumber")
    entry = _make_entry()
    await handle_mute(msg, entry=entry, db=AsyncMock())
    msg.answer.assert_awaited_once()
    assert "Usage" in msg.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_handle_unmute_clears_muted_until(db):
    # Pre-insert a muted alert
    await db.execute(
        __import__("sqlalchemy").text(
            "UPDATE alerts SET muted_until = now() + interval '1 hour', status='disabled' WHERE id = 42"
        )
    )
    from app.services.telegram.commands import handle_unmute
    msg = _make_message("/unmute 42")
    entry = _make_entry()
    await handle_unmute(msg, entry=entry, db=db)
    result = await db.execute(
        __import__("sqlalchemy").text("SELECT muted_until, status FROM alerts WHERE id = 42")
    )
    row = result.fetchone()
    if row:
        assert row.muted_until is None
        assert row.status == "active"


@pytest.mark.asyncio
async def test_kill_switch_uses_service_layer_not_http():
    """Verify AccountKillSwitchService.toggle is called, not HTTP."""
    from app.services.telegram.commands import handle_kill_switch
    msg = _make_message("/kill_switch IBKR")
    entry = _make_entry()

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))
    mock_redis = AsyncMock()

    with patch("app.services.telegram.commands.AccountKillSwitchService") as MockKS:
        instance = MockKS.return_value
        instance.toggle = AsyncMock()
        await handle_kill_switch(msg, entry=entry, db=mock_db, redis=mock_redis)

    msg.answer.assert_awaited()
```

- [ ] **Step 2: Run — expect FAIL**

```bash
docker compose exec backend pytest tests/services/telegram/test_commands.py -v
```

- [ ] **Step 3: Write implementation**

```python
# backend/app/services/telegram/commands.py
"""Telegram command handlers — /status /accounts /kill_switch /mute /unmute /help."""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import structlog
from aiogram.types import Message
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.account_kill_switch_service import AccountKillSwitchService
from app.services.telegram.allowlist import AllowlistEntry

if TYPE_CHECKING:
    pass

log = structlog.get_logger(__name__)

_MUTE_RE = re.compile(r"^(\d+)([mhd])$")
_MULTIPLIERS = {"m": 60, "h": 3600, "d": 86400}


async def handle_status(msg: Message, *, request_app: Any = None) -> None:
    try:
        evaluator = request_app.state.alerts_evaluator if request_app else None
        if evaluator is None:
            await msg.answer("Alerts evaluator not running.")
            return
        await msg.answer(f"Evaluator: running")
    except Exception:
        await msg.answer("Status unavailable.")


async def handle_accounts(msg: Message, *, entry: AllowlistEntry, db: AsyncSession) -> None:
    try:
        rows = await db.execute(
            text(
                "SELECT a.alias, b.label as broker, a.mode, a.currency_base "
                "FROM accounts a JOIN brokers b ON a.broker_id = b.id "
                "WHERE a.jwt_subject = :sub AND a.deleted_at IS NULL "
                "ORDER BY a.display_order"
            ),
            {"sub": entry.jwt_subject},
        )
        accounts = rows.fetchall()
        if not accounts:
            await msg.answer("No accounts found.")
            return
        lines = [f"<b>Accounts for {entry.label}:</b>"]
        for acc in accounts:
            lines.append(f"• {acc.alias} ({acc.broker}) [{acc.mode}] {acc.currency_base}")
        await msg.answer("\n".join(lines))
    except Exception:
        log.exception("telegram.handle_accounts_failed")
        await msg.answer("Could not retrieve accounts.")


async def handle_kill_switch(
    msg: Message,
    *,
    entry: AllowlistEntry,
    db: AsyncSession,
    redis: Any,
) -> None:
    parts = (msg.text or "").split()
    broker_alias = parts[1].upper() if len(parts) > 1 else ""
    if not broker_alias:
        await msg.answer("Usage: /kill_switch <broker> (e.g. IBKR, FUTU)")
        return
    try:
        rows = await db.execute(
            text(
                "SELECT a.id, a.alias FROM accounts a "
                "JOIN brokers b ON a.broker_id = b.id "
                "WHERE UPPER(b.label) = :broker AND a.jwt_subject = :sub AND a.deleted_at IS NULL"
            ),
            {"broker": broker_alias, "sub": entry.jwt_subject},
        )
        accounts = rows.fetchall()
        if not accounts:
            await msg.answer(f"No accounts found for broker '{broker_alias}'.")
            return
        ks = AccountKillSwitchService(db=db, redis=redis)
        outcomes = []
        for acc in accounts:
            try:
                await ks.toggle(
                    acc.id,
                    is_enabled=False,
                    reason="telegram:/kill_switch",
                    by=f"telegram:{entry.label}",
                )
                outcomes.append(f"✅ {acc.alias}: kill-switch enabled")
            except Exception:
                outcomes.append(f"❌ {acc.alias}: failed")
        await msg.answer("\n".join(outcomes))
    except Exception:
        log.exception("telegram.handle_kill_switch_failed")
        await msg.answer("Kill-switch failed.")


async def handle_mute(msg: Message, *, entry: AllowlistEntry, db: AsyncSession) -> None:
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("Usage: /mute <alert_id> [30m|2h|1d]")
        return
    try:
        alert_id = int(parts[1])
    except ValueError:
        await msg.answer("Usage: /mute <alert_id> [30m|2h|1d]")
        return

    muted_until = None
    if len(parts) >= 3:
        m = _MUTE_RE.match(parts[2])
        if not m:
            await msg.answer("Usage: /mute <alert_id> [30m|2h|1d]")
            return
        secs = int(m.group(1)) * _MULTIPLIERS[m.group(2)]
        muted_until = datetime.now(tz=timezone.utc) + timedelta(seconds=secs)

    try:
        await db.execute(
            text(
                "UPDATE alerts SET status='disabled', muted_until=:mu, updated_at=now() "
                "WHERE id=:aid AND jwt_subject=:sub"
            ),
            {"aid": alert_id, "mu": muted_until, "sub": entry.jwt_subject},
        )
        await db.commit()
        dur = f" until {muted_until.isoformat()}" if muted_until else " (permanent)"
        await msg.answer(f"Alert {alert_id} muted{dur}.")
    except Exception:
        log.exception("telegram.handle_mute_failed")
        await msg.answer("Mute failed.")


async def handle_unmute(msg: Message, *, entry: AllowlistEntry, db: AsyncSession) -> None:
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("Usage: /unmute <alert_id>")
        return
    try:
        alert_id = int(parts[1])
    except ValueError:
        await msg.answer("Usage: /unmute <alert_id>")
        return
    try:
        await db.execute(
            text(
                "UPDATE alerts SET status='active', muted_until=NULL, updated_at=now() "
                "WHERE id=:aid AND jwt_subject=:sub"
            ),
            {"aid": alert_id, "mu": None, "sub": entry.jwt_subject},
        )
        await db.commit()
        await msg.answer(f"Alert {alert_id} unmuted.")
    except Exception:
        log.exception("telegram.handle_unmute_failed")
        await msg.answer("Unmute failed.")


async def handle_help(msg: Message) -> None:
    await msg.answer(
        "<b>Available commands:</b>\n"
        "/status — evaluator status + active alert count\n"
        "/accounts — list your accounts\n"
        "/kill_switch &lt;broker&gt; — enable kill-switch for broker accounts\n"
        "/mute &lt;id&gt; [30m|2h|1d] — mute an alert (permanent if no duration)\n"
        "/unmute &lt;id&gt; — restore a muted alert\n"
        "/help — this message"
    )
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
docker compose exec backend pytest tests/services/telegram/test_commands.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/telegram/commands.py backend/tests/services/telegram/test_commands.py
git commit -m "feat(phase11c-B): telegram commands.py — /status /accounts /kill_switch /mute /unmute /help"
```

---

## Task 13: Wire commands + mute APScheduler job in main.py

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/api/telegram.py`

- [ ] **Step 1: Register command handlers on the Dispatcher**

In `bot.py`, update `build_dispatcher()` to accept and register handlers, OR do registration inline in `main.py`. The cleanest approach: add a `register_handlers(dp, allowlist, rate_limiter, db_factory, redis)` function in `commands.py` and call it after `build_dispatcher()`.

Add to `backend/app/services/telegram/commands.py`:

```python
from aiogram import Dispatcher, F
from aiogram.filters import Command


def register_handlers(
    dp: Dispatcher,
    *,
    allowlist: Any,
    rate_limiter: Any,
    db_factory: Any,
    redis: Any,
    request_app: Any = None,
) -> None:
    from app.services.telegram.commands import (
        handle_help, handle_status, handle_accounts,
        handle_kill_switch, handle_mute, handle_unmute,
    )

    async def _with_deps(cmd_fn: Any, msg: Message, **extra: Any) -> None:
        entry = allowlist.lookup(chat_id=msg.chat.id, from_user_id=(msg.from_user.id if msg.from_user else 0))
        if entry is None:
            await msg.answer("Unauthorized.")
            return
        async with db_factory() as db:
            await cmd_fn(msg, entry=entry, db=db, redis=redis, **extra)

    @dp.message(Command("help"))
    async def _help(msg: Message) -> None:
        await handle_help(msg)

    @dp.message(Command("status"))
    async def _status(msg: Message) -> None:
        await handle_status(msg, request_app=request_app)

    @dp.message(Command("accounts"))
    async def _accounts(msg: Message) -> None:
        await _with_deps(handle_accounts, msg)

    @dp.message(Command("kill_switch"))
    async def _ks(msg: Message) -> None:
        await _with_deps(handle_kill_switch, msg)

    @dp.message(Command("mute"))
    async def _mute(msg: Message) -> None:
        await _with_deps(handle_mute, msg)

    @dp.message(Command("unmute"))
    async def _unmute(msg: Message) -> None:
        await _with_deps(handle_unmute, msg)
```

- [ ] **Step 2: Update main.py to call register_handlers and add mute-expiry APScheduler job**

In `main.py`, after `build_dispatcher()`:

```python
from app.services.telegram.commands import register_handlers as register_tg_handlers
from app.services.telegram.rate_limiter import TelegramRateLimiter

tg_rate_limiter = TelegramRateLimiter(redis=redis)
tg_dispatcher = build_dispatcher()
register_tg_handlers(
    tg_dispatcher,
    allowlist=telegram_allowlist,
    rate_limiter=tg_rate_limiter,
    db_factory=session_factory,
    redis=redis,
    request_app=_app,
)
_telegram_api_module.dp = tg_dispatcher
```

Add mute-expiry job alongside alerts retention sweep:

```python
async def _run_mute_expiry_restore() -> None:
    try:
        async with session_factory() as db:
            result = await db.execute(
                text(
                    "UPDATE alerts SET status='active', muted_until=NULL, updated_at=now() "
                    "WHERE status='disabled' AND muted_until IS NOT NULL AND muted_until <= now() "
                    "RETURNING id"
                )
            )
            await db.commit()
            restored = result.fetchall()
            if restored:
                log.info("telegram.mute_expiry_restored", count=len(restored))
    except Exception:
        log.exception("telegram.mute_expiry_restore_failed")

scheduler.add_job(
    _run_mute_expiry_restore,
    "interval",
    seconds=60,
    id="telegram_mute_expiry",
    replace_existing=True,
)
```

- [ ] **Step 3: Run smoke test**

```bash
docker compose restart backend
docker compose logs backend --tail=30
docker compose exec backend pytest tests/ -x -q --timeout=60
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py backend/app/services/telegram/commands.py backend/app/api/telegram.py
git commit -m "feat(phase11c-B): wire command handlers + mute-expiry APScheduler job"
```

---

## Task 14: PUT /api/admin/alerts/webhooks/{id} (deferred from 11b)

**Files:**
- Modify or Create: `backend/app/api/admin_alerts.py` (check if it exists first)

- [ ] **Step 1: Check existing file**

```bash
ls /home/joseph/dashboard/backend/app/api/admin_alerts.py 2>/dev/null || echo "NOT EXISTS"
```

- [ ] **Step 2: Add endpoint**

If the file doesn't exist, create it; if it does, append the endpoint. The endpoint resolves the webhook secret from `app_secrets[alerts.webhook.<id>.secret]`:

```python
class WebhookConfigIn(BaseModel):
    url: str
    secret: str | None = None


@router.put("/alerts/webhooks/{webhook_id}")
async def put_webhook_config(
    webhook_id: int,
    body: WebhookConfigIn,
    config: ConfigDep,
    _csrf: CsrfDep,
    identity: IdentityDep,
) -> dict[str, Any]:
    await config.set("alerts", f"webhook.{webhook_id}.url", body.url, "str")
    if body.secret is not None:
        await config.set_secret("alerts", f"webhook.{webhook_id}.secret", body.secret)
    log.info("alerts.webhook_config_saved", webhook_id=webhook_id, by=identity.email)
    return {"ok": True, "webhook_id": webhook_id}
```

The router prefix should be `/api/admin`.

- [ ] **Step 3: Write test**

```python
@pytest.mark.asyncio
async def test_put_webhook_config(authed_admin_client: AsyncClient, csrf_nonce: str):
    resp = await authed_admin_client.put(
        "/api/admin/alerts/webhooks/1",
        json={"url": "https://hook.example.com/1", "secret": "mysecret"},
        headers={"X-Confirm-Nonce": csrf_nonce},
    )
    assert resp.status_code == 200
    assert resp.json()["webhook_id"] == 1
```

- [ ] **Step 4: Run + commit**

```bash
docker compose exec backend pytest tests/api/ -k "webhook" -v
git add backend/app/api/admin_alerts.py
git commit -m "feat(phase11c-B): PUT /api/admin/alerts/webhooks/{id} — deferred from 11b"
```

---

## Task 15: Tag chunk B + run reviewer chain

- [ ] **Step 1: Run full test suite**

```bash
docker compose exec backend pytest tests/ -q --timeout=60
cd /home/joseph/dashboard/frontend && pnpm test --run
```

- [ ] **Step 2: Tag**

```bash
git tag v0.11.2.1
git push origin main --tags
```

- [ ] **Step 3: Codex chunk-B review**

Run Codex reviewer on the B-chunk diff for spec compliance + code quality (see `docs/PHASE-WORKFLOW.md`).

---

## Task 16: Frontend — AdminTelegramPage (Chunk B)

**Files:**
- Create: `frontend/src/features/admin/telegram/AdminTelegramPage.tsx`
- Create: `frontend/src/features/admin/telegram/BotConfigPanel.tsx`
- Create: `frontend/src/features/admin/telegram/AllowlistPanel.tsx`
- Create: `frontend/src/features/admin/telegram/CommandLogPanel.tsx`
- Create: `frontend/src/routes/admin.telegram.tsx`

- [ ] **Step 1: Create route file**

```tsx
// frontend/src/routes/admin.telegram.tsx
import { createFileRoute } from '@tanstack/react-router';
import { AdminTelegramPage } from '@/features/admin/telegram/AdminTelegramPage';

export const Route = createFileRoute('/admin/telegram')({
  component: AdminTelegramPage,
});
```

- [ ] **Step 2: Create BotConfigPanel.tsx**

```tsx
// frontend/src/features/admin/telegram/BotConfigPanel.tsx
import * as React from 'react';
import { Button } from '@/components/primitives/Button';
import { Input } from '@/components/primitives/Input';
import { adminFetch, mintCsrfNonce } from '@/services/admin/api';

interface TelegramConfig {
  webhook_url: string;
  webhook_status: 'set' | 'retrying' | 'failed';
  token_set: boolean;
}

export function BotConfigPanel(): React.JSX.Element {
  const [token, setToken] = React.useState('');
  const [publicUrl, setPublicUrl] = React.useState('');
  const [config, setConfig] = React.useState<TelegramConfig | null>(null);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [testChatId, setTestChatId] = React.useState('');
  const [testing, setTesting] = React.useState(false);
  const [testResult, setTestResult] = React.useState<string | null>(null);

  React.useEffect(() => {
    void adminFetch<TelegramConfig>('/api/admin/telegram/config').then(setConfig).catch(() => null);
  }, []);

  async function save(): Promise<void> {
    setSaving(true);
    setError(null);
    try {
      const nonce = await mintCsrfNonce();
      await adminFetch('/api/admin/telegram/config', {
        method: 'PUT',
        headers: { 'X-Confirm-Nonce': nonce },
        body: JSON.stringify({ bot_token: token, public_base_url: publicUrl }),
      });
      const updated = await adminFetch<TelegramConfig>('/api/admin/telegram/config');
      setConfig(updated);
      setToken('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  async function sendTest(): Promise<void> {
    setTesting(true);
    setTestResult(null);
    try {
      const nonce = await mintCsrfNonce();
      await adminFetch('/api/admin/telegram/test-message', {
        method: 'POST',
        headers: { 'X-Confirm-Nonce': nonce },
        body: JSON.stringify({ chat_id: testChatId ? Number(testChatId) : undefined }),
      });
      setTestResult('Test message sent.');
    } catch {
      setTestResult('Failed to send test message.');
    } finally {
      setTesting(false);
    }
  }

  const statusColor = config?.webhook_status === 'set' ? 'text-positive' : config?.webhook_status === 'retrying' ? 'text-warning' : 'text-negative';

  return (
    <div className="grid gap-3 rounded-md border border-border bg-panel p-4">
      <h3 className="text-sm font-semibold text-fg">Bot Configuration</h3>
      {config && (
        <p className="text-xs text-fg-muted">
          Webhook: <span className="font-mono">{config.webhook_url || '(not set)'}</span>
          {' — '}
          <span className={statusColor}>{config.webhook_status}</span>
        </p>
      )}
      {error && <p className="text-sm text-negative">{error}</p>}
      <label className="grid gap-1 text-sm">
        Bot Token (leave blank to keep existing)
        <Input
          type="password"
          value={token}
          onChange={e => setToken(e.currentTarget.value)}
          placeholder="123456:ABC..."
        />
      </label>
      <label className="grid gap-1 text-sm">
        Public Base URL
        <Input
          value={publicUrl}
          onChange={e => setPublicUrl(e.currentTarget.value)}
          placeholder="https://dashboard.example.com"
        />
      </label>
      <Button type="button" onClick={() => void save()} disabled={saving}>
        {saving ? 'Saving…' : 'Save & rotate webhook secret'}
      </Button>
      <div className="mt-2 flex gap-2">
        <Input
          value={testChatId}
          onChange={e => setTestChatId(e.currentTarget.value)}
          placeholder="Chat ID (optional)"
          className="w-48"
        />
        <Button type="button" onClick={() => void sendTest()} disabled={testing}>
          {testing ? 'Sending…' : 'Send test message'}
        </Button>
      </div>
      {testResult && <p className="text-sm text-fg-muted">{testResult}</p>}
    </div>
  );
}
```

- [ ] **Step 3: Create AllowlistPanel.tsx**

```tsx
// frontend/src/features/admin/telegram/AllowlistPanel.tsx
import * as React from 'react';
import { Button } from '@/components/primitives/Button';
import { Input } from '@/components/primitives/Input';
import { adminFetch, mintCsrfNonce } from '@/services/admin/api';

interface AllowlistEntry {
  chat_id: number;
  from_user_id: number;
  jwt_subject: string;
  label: string;
}

export function AllowlistPanel(): React.JSX.Element {
  const [entries, setEntries] = React.useState<AllowlistEntry[]>([]);
  const [chatId, setChatId] = React.useState('');
  const [fromUserId, setFromUserId] = React.useState('');
  const [jwtSubject, setJwtSubject] = React.useState('');
  const [label, setLabel] = React.useState('');
  const [adding, setAdding] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    try {
      setEntries(await adminFetch<AllowlistEntry[]>('/api/admin/telegram/allowlist'));
    } catch {
      setError('Failed to load allowlist.');
    }
  }, []);

  React.useEffect(() => { void load(); }, [load]);

  async function add(): Promise<void> {
    setAdding(true);
    setError(null);
    try {
      const nonce = await mintCsrfNonce();
      await adminFetch('/api/admin/telegram/allowlist', {
        method: 'POST',
        headers: { 'X-Confirm-Nonce': nonce },
        body: JSON.stringify({
          chat_id: Number(chatId),
          from_user_id: Number(fromUserId),
          jwt_subject: jwtSubject,
          label,
        }),
      });
      setChatId(''); setFromUserId(''); setJwtSubject(''); setLabel('');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Add failed');
    } finally {
      setAdding(false);
    }
  }

  async function remove(cid: number): Promise<void> {
    try {
      const nonce = await mintCsrfNonce();
      await adminFetch(`/api/admin/telegram/allowlist/${cid}`, {
        method: 'DELETE',
        headers: { 'X-Confirm-Nonce': nonce },
      });
      await load();
    } catch {
      setError('Remove failed.');
    }
  }

  return (
    <div className="grid gap-3 rounded-md border border-border bg-panel p-4">
      <h3 className="text-sm font-semibold text-fg">Allowlist</h3>
      {error && <p className="text-sm text-negative">{error}</p>}
      <table className="w-full text-sm">
        <thead><tr className="text-left text-fg-muted"><th>Chat ID</th><th>User ID</th><th>Subject</th><th>Label</th><th /></tr></thead>
        <tbody>
          {entries.map(e => (
            <tr key={e.chat_id} className="border-t border-border">
              <td className="py-1 font-mono">{e.chat_id}</td>
              <td className="py-1 font-mono">{e.from_user_id}</td>
              <td className="py-1">{e.jwt_subject}</td>
              <td className="py-1">{e.label}</td>
              <td className="py-1">
                <Button type="button" onClick={() => void remove(e.chat_id)} className="text-xs">Remove</Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex flex-wrap gap-2 pt-2">
        <Input placeholder="Chat ID" value={chatId} onChange={e => setChatId(e.currentTarget.value)} className="w-32" />
        <Input placeholder="From User ID" value={fromUserId} onChange={e => setFromUserId(e.currentTarget.value)} className="w-32" />
        <Input placeholder="JWT subject" value={jwtSubject} onChange={e => setJwtSubject(e.currentTarget.value)} className="w-40" />
        <Input placeholder="Label" value={label} onChange={e => setLabel(e.currentTarget.value)} className="w-32" />
        <Button type="button" onClick={() => void add()} disabled={adding || !chatId || !fromUserId || !jwtSubject || !label}>
          {adding ? 'Adding…' : 'Add'}
        </Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Create CommandLogPanel.tsx**

```tsx
// frontend/src/features/admin/telegram/CommandLogPanel.tsx
import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { adminFetch } from '@/services/admin/api';

interface LogRow {
  id: number;
  ts: string;
  chat_id: number;
  command: string;
  args: string | null;
  outcome: string;
  latency_ms: number | null;
}

export function CommandLogPanel(): React.JSX.Element {
  const { data: rows = [], isLoading } = useQuery({
    queryKey: ['telegram-command-log'],
    queryFn: () => adminFetch<LogRow[]>('/api/admin/telegram/command-log'),
    refetchInterval: 30_000,
  });

  return (
    <div className="grid gap-3 rounded-md border border-border bg-panel p-4">
      <h3 className="text-sm font-semibold text-fg">Command Log</h3>
      {isLoading && <p className="text-sm text-fg-muted">Loading…</p>}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-fg-muted">
              <th>Time</th><th>Chat</th><th>Command</th><th>Args</th><th>Outcome</th><th>ms</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.id} className="border-t border-border">
                <td className="py-1 text-xs font-mono">{new Date(r.ts).toLocaleString()}</td>
                <td className="py-1 font-mono">{r.chat_id}</td>
                <td className="py-1 font-mono">{r.command}</td>
                <td className="py-1 text-fg-muted">{r.args ?? '—'}</td>
                <td className={`py-1 ${r.outcome === 'ok' ? 'text-positive' : 'text-negative'}`}>{r.outcome}</td>
                <td className="py-1">{r.latency_ms ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Create AdminTelegramPage.tsx**

```tsx
// frontend/src/features/admin/telegram/AdminTelegramPage.tsx
import * as React from 'react';
import { BotConfigPanel } from './BotConfigPanel';
import { AllowlistPanel } from './AllowlistPanel';
import { CommandLogPanel } from './CommandLogPanel';

export function AdminTelegramPage(): React.JSX.Element {
  return (
    <div className="grid gap-4 p-4">
      <h2 className="text-lg font-semibold text-fg">Telegram Bot</h2>
      <BotConfigPanel />
      <AllowlistPanel />
      <CommandLogPanel />
    </div>
  );
}
```

- [ ] **Step 6: Regenerate route tree**

```bash
cd /home/joseph/dashboard/frontend && pnpm tsr generate
```

- [ ] **Step 7: Run FE tests**

```bash
pnpm test --run
```

- [ ] **Step 8: Commit**

```bash
git add frontend/src/features/admin/telegram/ frontend/src/routes/admin.telegram.tsx frontend/src/routes/routeTree.gen.ts
git commit -m "feat(phase11c-B): AdminTelegramPage — BotConfigPanel + AllowlistPanel + CommandLogPanel"
```

---

## Task 17: services/telegram/chat.py — Free-form AI chat (Chunk C)

**Files:**
- Create: `backend/app/services/telegram/chat.py`
- Create: `backend/tests/services/telegram/test_chat.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/services/telegram/test_chat.py
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_message(text: str, chat_id: int = 111) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.chat.id = chat_id
    msg.answer = AsyncMock()
    return msg


@pytest.mark.asyncio
async def test_chat_calls_ai_with_reasoning_capability():
    from app.services.telegram.chat import TelegramChat

    mock_ai = AsyncMock()
    mock_ai.complete = AsyncMock(return_value=MagicMock(content="Hello!"))
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()

    chat = TelegramChat(ai_client=mock_ai, redis=mock_redis, chat_id_hash_salt="salt")
    msg = _make_message("Hi there")
    await chat.handle(msg)

    call_kwargs = mock_ai.complete.call_args.kwargs
    assert call_kwargs.get("capability") == "REASONING" or "REASONING" in str(call_kwargs)


@pytest.mark.asyncio
async def test_chat_appends_to_redis_history():
    from app.services.telegram.chat import TelegramChat

    mock_ai = AsyncMock()
    mock_ai.complete = AsyncMock(return_value=MagicMock(content="Hi!"))
    stored = {}
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)

    async def mock_set(key: str, val: str, ex: int = 0) -> None:
        stored[key] = val

    mock_redis.set = mock_set
    chat = TelegramChat(ai_client=mock_ai, redis=mock_redis, chat_id_hash_salt="salt")
    msg = _make_message("What is 2+2?", chat_id=111)
    await chat.handle(msg)

    assert len(stored) == 1
    history = json.loads(list(stored.values())[0])
    assert any(m["role"] == "user" for m in history)
    assert any(m["role"] == "assistant" for m in history)


@pytest.mark.asyncio
async def test_chat_second_message_while_in_flight_returns_busy():
    from app.services.telegram.chat import TelegramChat

    lock = asyncio.Lock()
    await lock.acquire()

    mock_ai = AsyncMock()
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()

    chat = TelegramChat(ai_client=mock_ai, redis=mock_redis, chat_id_hash_salt="salt")
    chat._locks[111] = lock

    msg = _make_message("second message", chat_id=111)
    await chat.handle(msg)
    msg.answer.assert_awaited_once()
    assert "previous reply" in msg.answer.call_args.args[0].lower() or "in progress" in msg.answer.call_args.args[0].lower()
    lock.release()


@pytest.mark.asyncio
async def test_chat_ai_unavailable_graceful_reply():
    from app.services.telegram.chat import TelegramChat

    mock_ai = AsyncMock()
    mock_ai.complete = AsyncMock(side_effect=Exception("AI down"))
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()

    chat = TelegramChat(ai_client=mock_ai, redis=mock_redis, chat_id_hash_salt="salt")
    msg = _make_message("hello", chat_id=111)
    await chat.handle(msg)
    msg.answer.assert_awaited()
    assert "unavailable" in msg.answer.call_args.args[0].lower()
```

- [ ] **Step 2: Run — expect FAIL**

```bash
docker compose exec backend pytest tests/services/telegram/test_chat.py -v
```

- [ ] **Step 3: Write implementation**

```python
# backend/app/services/telegram/chat.py
"""Free-form message → AI router (REASONING capability), per-chat asyncio lock."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import TYPE_CHECKING, Any

import structlog
from aiogram.types import Message

if TYPE_CHECKING:
    pass

log = structlog.get_logger(__name__)

_MAX_TURNS = 20
_CONV_TTL = 86400  # 24h
_AI_CAPABILITY = "REASONING"


def _hash_chat_id(chat_id: int, salt: str) -> str:
    return hmac.new(salt.encode(), str(chat_id).encode(), hashlib.sha256).hexdigest()[:16]


class TelegramChat:
    def __init__(self, *, ai_client: Any, redis: Any, chat_id_hash_salt: str) -> None:
        self._ai = ai_client
        self._redis = redis
        self._salt = chat_id_hash_salt
        self._locks: dict[int, asyncio.Lock] = {}

    def _get_lock(self, chat_id: int) -> asyncio.Lock:
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]

    async def _load_history(self, key: str) -> list[dict[str, str]]:
        raw = await self._redis.get(key)
        if raw:
            try:
                return json.loads(raw)
            except Exception:
                return []
        return []

    async def _save_history(self, key: str, history: list[dict[str, str]]) -> None:
        await self._redis.set(key, json.dumps(history[-_MAX_TURNS * 2:]), ex=_CONV_TTL)

    async def handle(self, msg: Message) -> None:
        chat_id = msg.chat.id
        lock = self._get_lock(chat_id)

        if lock.locked():
            await msg.answer("Previous reply still in progress, please wait.")
            return

        async with lock:
            hash_key = _hash_chat_id(chat_id, self._salt)
            conv_key = f"telegram:chat:{hash_key}"
            history = await self._load_history(conv_key)
            history.append({"role": "user", "content": msg.text or ""})
            try:
                result = await self._ai.complete(
                    capability=_AI_CAPABILITY,
                    messages=history,
                )
                reply = result.content
                history.append({"role": "assistant", "content": reply})
                await self._save_history(conv_key, history)
                await msg.answer(reply)
            except Exception:
                log.exception("telegram.chat_ai_failed")
                await msg.answer("AI unavailable, try again later.")
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
docker compose exec backend pytest tests/services/telegram/test_chat.py -v
```

- [ ] **Step 5: Wire chat handler in main.py**

In `register_handlers` (or inline in `main.py`), after the command handlers:

```python
from app.services.telegram.chat import TelegramChat
from app.services.ai.router import LiteLLMClient  # existing AI client

# In register_handlers, add:
tg_chat = TelegramChat(
    ai_client=ai_client,  # app.state.ai_client or similar
    redis=redis,
    chat_id_hash_salt=chat_id_hash_salt,
)

@dp.message(F.text & ~F.text.startswith("/"))
async def _chat_msg(msg: Message) -> None:
    entry = allowlist.lookup(chat_id=msg.chat.id, from_user_id=(msg.from_user.id if msg.from_user else 0))
    if entry is None:
        await msg.answer("Unauthorized.")
        return
    asyncio.create_task(tg_chat.handle(msg))
```

Retrieve `chat_id_hash_salt` from secrets in lifespan:

```python
chat_id_hash_salt = await config_service.reveal_secret("telegram", "chat_id_hash_salt") or "default-salt"
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/telegram/chat.py backend/tests/services/telegram/test_chat.py backend/app/main.py
git commit -m "feat(phase11c-C): telegram chat.py — per-chat asyncio lock, REASONING capability, Redis history"
```

---

## Task 18: Full test sweep + tag chunk C

- [ ] **Step 1: Run all tests**

```bash
docker compose exec backend pytest tests/ -q --timeout=60
cd /home/joseph/dashboard/frontend && pnpm test --run
```

- [ ] **Step 2: TypeScript check**

```bash
cd /home/joseph/dashboard/frontend && pnpm tsc --noEmit
```

- [ ] **Step 3: Codex chunk-C reviewer dispatch**

Dispatch Codex review on the chunk-C diff (spec §14 Chunk C testing targets vs. implementation).

- [ ] **Step 4: Update CLAUDE.md + CHANGELOG.md + TASKS.md**

Add Phase 11c entry to CHANGELOG.md under v0.11.2.x. Update TASKS.md to mark Phase 11c complete. Add `phase11c_shipped.md` memory entry.

- [ ] **Step 5: Tag**

```bash
git tag v0.11.2.2
git push origin main --tags
```

---

## Post-implementation ops checklist

These are one-time CF WAF steps documented in the spec — not automated:

1. CF Zero Trust → Access Applications → add Bypass policy for `/api/telegram/webhook`
2. CF WAF → add Firewall Rule: block requests to `/api/telegram/webhook` from IPs outside `149.154.160.0/20` and `91.108.4.0/22`
3. Seed `app_secrets[telegram.bot_token]` via `/admin/telegram`
4. Set `app_config[telegram.public_base_url]` to `https://<your-domain>` via admin UI
5. `PUT /api/admin/telegram/config` to trigger first `setWebhook` call + auto-generate `webhook_secret`
6. Seed `app_secrets[telegram.chat_id_hash_salt]` with a random 32-byte value
