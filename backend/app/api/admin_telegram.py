from __future__ import annotations

import inspect
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import consume_confirmation_nonce
from app.core.deps import get_config, get_db, get_redis, require_admin_jwt
from app.services.config import ConfigService

ConfigDep = Annotated[ConfigService, Depends(get_config)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Any, Depends(get_redis)]
IdentityDep = Annotated[None, Depends(require_admin_jwt)]
CsrfDep = Annotated[None, Depends(consume_confirmation_nonce)]

router = APIRouter(
    prefix="/api/admin/telegram",
    tags=["admin-telegram"],
    dependencies=[Depends(require_admin_jwt)],
)

_ALLOWLIST_CHANNEL = "app_config:invalidate:telegram_allowlist"


class TelegramConfigPut(BaseModel):
    bot_token: str
    public_base_url: str = ""


class TestMessageIn(BaseModel):
    chat_id: int | None = None
    text: str = "Test message from trading dashboard"


class AllowlistEntryIn(BaseModel):
    chat_id: int
    from_user_id: int
    jwt_subject: str
    label: str


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _get_allowlist_entries(config: ConfigService) -> list[dict[str, Any]]:
    entries = await _maybe_await(config.get_json("telegram_allowlist", "entries", default=[]))
    return entries if isinstance(entries, list) else []


async def _publish_allowlist_invalidation(redis: Any) -> None:
    await redis.publish(_ALLOWLIST_CHANNEL, "1")


@router.get("/config")
async def get_telegram_config(request: Request, config: ConfigDep) -> dict[str, Any]:
    webhook_url = await _maybe_await(config.get("telegram", "public_base_url", ""))
    bot_token = await config.reveal_secret("telegram", "bot_token")
    return {
        "webhook_url": webhook_url or "",
        "webhook_status": getattr(request.app.state, "telegram_webhook_status", "failed"),
        "token_set": bool(bot_token),
    }


@router.put("/config")
async def put_telegram_config(
    body: TelegramConfigPut,
    config: ConfigDep,
    _csrf: CsrfDep,
) -> dict[str, bool]:
    webhook_secret = secrets.token_urlsafe(32)
    await config.set_secret("telegram", "bot_token", body.bot_token)
    await config.set_secret("telegram", "webhook_secret", webhook_secret)
    if body.public_base_url:
        await config.set("telegram", "public_base_url", body.public_base_url, "str")
    return {"ok": True}


@router.post("/test-message")
async def post_test_message(
    body: TestMessageIn,
    _csrf: CsrfDep,
) -> dict[str, bool | str]:
    return {"ok": True, "note": "seed bot token first"}


@router.get("/allowlist")
async def get_allowlist(config: ConfigDep) -> list[dict[str, Any]]:
    return await _get_allowlist_entries(config)


@router.post("/allowlist", status_code=status.HTTP_201_CREATED)
async def post_allowlist(
    body: AllowlistEntryIn,
    config: ConfigDep,
    redis: RedisDep,
    _csrf: CsrfDep,
) -> dict[str, Any]:
    entries = await _get_allowlist_entries(config)
    new_entry = body.model_dump()
    entries.append(new_entry)
    await config.set("telegram_allowlist", "entries", entries, "json")
    await _publish_allowlist_invalidation(redis)
    return new_entry


@router.delete("/allowlist/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_allowlist(
    chat_id: int,
    config: ConfigDep,
    redis: RedisDep,
    _csrf: CsrfDep,
) -> Response:
    entries = await _get_allowlist_entries(config)
    kept = [entry for entry in entries if int(entry.get("chat_id", 0)) != chat_id]
    await config.set("telegram_allowlist", "entries", kept, "json")
    await _publish_allowlist_invalidation(redis)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/command-log")
async def get_command_log(
    db: DbDep,
    limit: int = Query(default=50, ge=1, le=500),
    before_id: int | None = Query(default=None),
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    where = ""
    if before_id is not None:
        where = "WHERE id < :before_id"
        params["before_id"] = before_id
    result = await db.execute(
        text(
            "SELECT id, ts, chat_id, from_user_id, command, args, outcome, latency_ms "
            f"FROM telegram_command_log {where} "
            "ORDER BY ts DESC LIMIT :limit"
        ),
        {**params, "limit": limit},
    )
    return [dict(row) for row in result.mappings().all()]
