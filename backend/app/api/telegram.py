from __future__ import annotations

import hmac
from typing import Any

import structlog
from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import APIRouter, Request, Response

log = structlog.get_logger(__name__)
router = APIRouter(tags=["telegram"])
dp: Dispatcher | None = None


@router.post("/api/telegram/webhook")
async def telegram_webhook(request: Request) -> Response:
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    expected = getattr(request.app.state, "telegram_webhook_secret", None)
    if not expected or not hmac.compare_digest(secret, expected):
        return Response(status_code=403)

    bot: Bot | None = getattr(request.app.state, "telegram_bot", None)
    if bot is None:
        return Response(status_code=503)

    redis = request.app.state.redis
    body: dict[str, Any] = await request.json()
    update_id = body.get("update_id")
    if isinstance(update_id, int) and update_id > 0:
        dedup_key = f"telegram:seen:{update_id}"
        was_set = await redis.set(dedup_key, "1", ex=300, nx=True)
        if not was_set:
            return Response(status_code=200)

    if dp is None:
        return Response(status_code=503)

    update = Update.model_validate(body, context={"bot": bot})
    try:
        await dp.feed_update(bot, update)
    except Exception as exc:
        log.exception(
            "telegram.feed_update_failed",
            update_id=update_id,
            error_class=type(exc).__name__,
        )
    return Response(status_code=200)
