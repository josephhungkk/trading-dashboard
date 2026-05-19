from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import consume_confirmation_nonce
from app.api.ws_auth import require_jwt
from app.bot.sandbox import extract_params_schema
from app.core.deps import get_db, get_redis
from app.services.advisor.types import AdvisorConfig, AdvisorMode
from app.services.ai.capabilities import AICapability

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/bots", tags=["bots"])

_STRATEGIES_DIR = Path(os.getenv("STRATEGIES_DIR", "/strategies"))

JwtSubject = Annotated[str, Depends(require_jwt)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Any, Depends(get_redis)]


class BotCreate(BaseModel):
    name: str
    strategy_file: str
    params_json: dict[str, Any] = {}
    bar_timeframe: str = "1m"
    mode: str = "paper"
    account_ids: list[UUID] = []


class BotUpdate(BaseModel):
    name: str | None = None
    params_json: dict[str, Any] | None = None
    bar_timeframe: str | None = None


class RiskCapsUpdate(BaseModel):
    max_position_size: float | None = None
    max_daily_loss: float | None = None
    max_open_orders: int | None = None
    max_order_size: float | None = None
    allowed_asset_classes: list[str] | None = None


class AdvisorConfigUpdate(BaseModel):
    mode: AdvisorMode = AdvisorMode.OFF
    capability: AICapability = AICapability.REASONING
    local_only: bool = False
    timeout_ms: int = 3000
    daily_budget_usd: str = "5.00"
    max_qps: float = 2.0
    auto_pause_threshold: int = 0
    auto_pause_window_seconds: int = 300
    min_veto_confidence: float = 0.0


@router.get("/strategies")
async def list_strategies(
    _user: JwtSubject,
) -> list[dict[str, Any]]:
    import asyncio

    def _scan() -> list[dict[str, Any]]:
        if not _STRATEGIES_DIR.exists():
            return []
        out = []
        for f in sorted(_STRATEGIES_DIR.glob("*.py")):
            stat = f.stat()
            out.append(
                {
                    "filename": f.name,
                    "size": stat.st_size,
                    "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                }
            )
        return out

    return await asyncio.get_event_loop().run_in_executor(None, _scan)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_bot(
    body: BotCreate,
    db: DbDep,
    redis: RedisDep,
    _user: JwtSubject,
) -> dict[str, Any]:
    import asyncio

    def _resolve_strategy() -> tuple[Path, bool]:
        sp = (_STRATEGIES_DIR / body.strategy_file).resolve()
        base = _STRATEGIES_DIR.resolve()
        if not str(sp).startswith(str(base) + "/") or sp.suffix != ".py":
            raise ValueError("invalid_strategy_file")
        return sp, sp.exists()

    try:
        strategy_path, strategy_exists = await asyncio.get_event_loop().run_in_executor(
            None, _resolve_strategy
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    params_schema = extract_params_schema(str(strategy_path)) if strategy_exists else None

    row = await db.execute(
        text(
            """
            INSERT INTO bots
              (name, strategy_file, params_json, params_schema_json, mode, bar_timeframe)
            VALUES (:name, :sf, CAST(:pj AS jsonb), CAST(:ps AS jsonb), :mode, :tf)
            RETURNING
              id, name, strategy_file, params_json, status, mode, bar_timeframe,
              version, created_at
            """
        ),
        {
            "name": body.name,
            "sf": body.strategy_file,
            "pj": json.dumps(body.params_json),
            "ps": json.dumps(params_schema) if params_schema is not None else "null",
            "mode": body.mode,
            "tf": body.bar_timeframe,
        },
    )
    bot_row = row.mappings().first()
    if bot_row is None:
        raise HTTPException(status_code=500, detail="insert_failed")
    bot = dict(bot_row)

    for aid in body.account_ids:
        try:
            await db.execute(
                text("INSERT INTO bot_accounts (bot_id, account_id) VALUES (:bid, :aid)"),
                {"bid": bot["id"], "aid": aid},
            )
        except Exception as exc:
            await db.rollback()
            raise HTTPException(status_code=400, detail=f"account_id {aid} not found") from exc

    await db.commit()
    return bot


@router.get("")
async def list_bots(
    db: DbDep,
    _user: JwtSubject,
    status_filter: str | None = None,
    mode: str | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    filters = ["deleted_at IS NULL"]
    params: dict[str, Any] = {}
    if status_filter:
        filters.append("status = :status")
        params["status"] = status_filter
    if mode:
        filters.append("mode = :mode")
        params["mode"] = mode
    if cursor:
        filters.append("created_at < :cursor")
        params["cursor"] = cursor

    where = " AND ".join(filters)
    rows = await db.execute(
        text(f"SELECT * FROM bots WHERE {where} ORDER BY created_at DESC LIMIT 50"),
        params,
    )
    items = [dict(r._mapping) for r in rows.fetchall()]
    next_cursor = items[-1]["created_at"].isoformat() if len(items) == 50 else None
    return {"items": items, "next_cursor": next_cursor}


@router.get("/{bot_id}")
async def get_bot(
    bot_id: UUID,
    db: DbDep,
    _user: JwtSubject,
) -> dict[str, Any]:
    row = await db.execute(
        text("SELECT * FROM bots WHERE id = :id AND deleted_at IS NULL"),
        {"id": bot_id},
    )
    bot = row.mappings().first()
    if bot is None:
        raise HTTPException(status_code=404, detail="bot_not_found")
    return dict(bot)


@router.put("/{bot_id}")
async def update_bot(
    bot_id: UUID,
    body: BotUpdate,
    db: DbDep,
    _user: JwtSubject,
) -> dict[str, Any]:
    row = await db.execute(
        text("SELECT status FROM bots WHERE id = :id AND deleted_at IS NULL"),
        {"id": bot_id},
    )
    bot = row.first()
    if bot is None:
        raise HTTPException(status_code=404, detail="bot_not_found")
    if bot[0] != "stopped":
        raise HTTPException(status_code=409, detail="bot_must_be_stopped")

    set_parts: list[str] = []
    params: dict[str, Any] = {}
    if body.name is not None:
        set_parts.append("name = :name")
        params["name"] = body.name
    if body.params_json is not None:
        set_parts.append("params_json = CAST(:params_json AS jsonb)")
        params["params_json"] = json.dumps(body.params_json)
    if body.bar_timeframe is not None:
        set_parts.append("bar_timeframe = :bar_timeframe")
        params["bar_timeframe"] = body.bar_timeframe

    if not set_parts:
        raise HTTPException(status_code=422, detail="no_fields_to_update")

    params["id"] = bot_id
    set_sql = ", ".join(set_parts)
    result = await db.execute(
        text(f"UPDATE bots SET {set_sql}, updated_at = now() WHERE id = :id RETURNING *"),
        params,
    )
    await db.commit()
    updated = result.mappings().first()
    if updated is None:
        raise HTTPException(status_code=404, detail="bot_not_found")
    return dict(updated)


@router.delete("/{bot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bot(
    bot_id: UUID,
    db: DbDep,
    _user: JwtSubject,
) -> None:
    row = await db.execute(
        text("SELECT status FROM bots WHERE id = :id AND deleted_at IS NULL"),
        {"id": bot_id},
    )
    bot = row.first()
    if bot is None:
        raise HTTPException(status_code=404, detail="bot_not_found")
    if bot[0] != "stopped":
        raise HTTPException(status_code=409, detail="bot_must_be_stopped")
    await db.execute(
        text("UPDATE bots SET deleted_at = now() WHERE id = :id"),
        {"id": bot_id},
    )
    await db.commit()


@router.post("/{bot_id}/accounts")
async def add_account(
    bot_id: UUID,
    account_id: UUID,
    db: DbDep,
    _user: JwtSubject,
) -> dict[str, Any]:
    await _assert_stopped(bot_id, db)
    try:
        await db.execute(
            text("INSERT INTO bot_accounts (bot_id, account_id) VALUES (:bid, :aid)"),
            {"bid": bot_id, "aid": account_id},
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail="account_not_found_or_duplicate") from exc
    return {"bot_id": str(bot_id), "account_id": str(account_id)}


@router.delete("/{bot_id}/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_account(
    bot_id: UUID,
    account_id: UUID,
    db: DbDep,
    _user: JwtSubject,
) -> None:
    await _assert_stopped(bot_id, db)
    await db.execute(
        text("DELETE FROM bot_accounts WHERE bot_id = :bid AND account_id = :aid"),
        {"bid": bot_id, "aid": account_id},
    )
    await db.commit()


@router.get("/{bot_id}/runs")
async def list_runs(
    bot_id: UUID,
    db: DbDep,
    _user: JwtSubject,
    cursor: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"bot_id": bot_id}
    extra = ""
    if cursor:
        extra = "AND started_at < :cursor"
        params["cursor"] = cursor
    rows = await db.execute(
        text(
            f"SELECT * FROM bot_runs WHERE bot_id = :bot_id {extra}"
            " ORDER BY started_at DESC LIMIT 50"
        ),
        params,
    )
    items = [dict(r._mapping) for r in rows.fetchall()]
    next_cursor = items[-1]["started_at"].isoformat() if len(items) == 50 else None
    return {"items": items, "next_cursor": next_cursor}


@router.get("/{bot_id}/orders")
async def list_orders(
    bot_id: UUID,
    db: DbDep,
    _user: JwtSubject,
    cursor: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"bot_id": bot_id}
    extra = ""
    if cursor:
        extra = "AND bo.placed_at < :cursor"
        params["cursor"] = cursor
    rows = await db.execute(
        text(
            f"""
            SELECT bo.order_id, bo.placed_at, o.side, o.qty, o.status, o.account_id
            FROM bot_orders bo
            JOIN orders o ON o.id = bo.order_id
            WHERE bo.bot_id = :bot_id {extra}
            ORDER BY bo.placed_at DESC LIMIT 50
            """
        ),
        params,
    )
    items = [dict(r._mapping) for r in rows.fetchall()]
    next_cursor = items[-1]["placed_at"].isoformat() if len(items) == 50 else None
    return {"items": items, "next_cursor": next_cursor}


@router.put("/{bot_id}/risk-caps")
async def upsert_risk_caps(
    bot_id: UUID,
    body: RiskCapsUpdate,
    db: DbDep,
    redis: RedisDep,
    _user: JwtSubject,
) -> dict[str, Any]:
    await db.execute(
        text(
            """
            INSERT INTO bot_risk_caps
              (bot_id, max_position_size, max_daily_loss,
               max_open_orders, max_order_size, allowed_asset_classes)
            VALUES (:bid, :mps, :mdl, :moo, :mos, CAST(:aac AS TEXT[]))
            ON CONFLICT (bot_id) DO UPDATE SET
                max_position_size = EXCLUDED.max_position_size,
                max_daily_loss = EXCLUDED.max_daily_loss,
                max_open_orders = EXCLUDED.max_open_orders,
                max_order_size = EXCLUDED.max_order_size,
                allowed_asset_classes = EXCLUDED.allowed_asset_classes,
                updated_at = now()
            """
        ),
        {
            "bid": bot_id,
            "mps": body.max_position_size,
            "mdl": body.max_daily_loss,
            "moo": body.max_open_orders,
            "mos": body.max_order_size,
            "aac": body.allowed_asset_classes,
        },
    )
    await db.commit()
    await redis.publish(f"bot:risk_caps:invalidate:{bot_id}", "1")
    return {"bot_id": str(bot_id)}


@router.get("/{bot_id}/advisor-config")
async def get_advisor_config(
    bot_id: UUID,
    db: DbDep,
    _user: JwtSubject,
) -> dict[str, Any]:
    bot_row = await db.execute(
        text("SELECT advisor_config FROM bots WHERE id=:id AND deleted_at IS NULL"),
        {"id": bot_id},
    )
    raw_config = bot_row.scalar_one_or_none()
    if raw_config is None:
        raise HTTPException(status_code=404, detail="bot_not_found")

    override_rows = await db.execute(
        text(
            """
            SELECT account_id, advisor_config_override
            FROM bot_accounts
            WHERE bot_id = :id AND advisor_config_override IS NOT NULL
            """
        ),
        {"id": bot_id},
    )
    account_overrides = {
        str(row.account_id): AdvisorConfig.from_jsonb_dict(
            row.advisor_config_override
        ).to_jsonb_dict()
        for row in override_rows
        if row.advisor_config_override is not None
    }

    return {
        "bot_id": str(bot_id),
        "config": AdvisorConfig.from_jsonb_dict(raw_config).to_jsonb_dict(),
        "account_overrides": account_overrides,
    }


@router.put("/{bot_id}/advisor-config")
async def update_advisor_config(
    bot_id: UUID,
    body: AdvisorConfigUpdate,
    db: DbDep,
    redis: RedisDep,
    _user: JwtSubject,
    _csrf: Annotated[None, Depends(consume_confirmation_nonce)],
) -> dict[str, Any]:
    row = await db.execute(
        text("SELECT id FROM bots WHERE id=:id AND deleted_at IS NULL"),
        {"id": bot_id},
    )
    if row.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="bot_not_found")

    try:
        config = AdvisorConfig.from_jsonb_dict(body.model_dump())
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=jsonable_encoder(exc.errors())) from exc

    cfg = config.to_jsonb_dict()
    await db.execute(
        text("UPDATE bots SET advisor_config = CAST(:cfg AS jsonb) WHERE id = :id"),
        {"cfg": json.dumps(cfg), "id": bot_id},
    )
    await db.commit()
    # Forward new config to any running child via control stream
    cmd_payload = json.dumps(
        {"id": str(uuid.uuid4()), "cmd": "UPDATE_ADVISOR_CONFIG", "config": cfg}
    )
    try:
        await redis.xadd(f"bot:control:{bot_id}", {"data": cmd_payload})
    except Exception:
        logger.warning("advisor_config_xadd_failed", bot_id=str(bot_id))
    return {"bot_id": str(bot_id), "config": cfg}


@router.get("/{bot_id}/advisor-decisions")
async def list_advisor_decisions(
    bot_id: UUID,
    db: DbDep,
    _user: JwtSubject,
    limit: int = Query(default=50, ge=1, le=100),
    before: datetime | None = None,
) -> dict[str, Any]:
    await _assert_bot_exists(bot_id, db)
    params: dict[str, Any] = {"bid": bot_id, "limit": limit}
    before_sql = ""
    if before is not None:
        before_sql = "AND created_at < :before"
        params["before"] = before

    rows = await db.execute(
        text(
            f"""
            SELECT id, verdict, reasoning, confidence, advice_tags, canonical_id,
                   effective_mode, latency_ms, ai_completion_ts, created_at
            FROM bot_advisor_decisions
            WHERE bot_id = :bid {before_sql}
            ORDER BY created_at DESC
            LIMIT :limit
            """
        ),
        params,
    )
    items = [jsonable_encoder(dict(r._mapping)) for r in rows.fetchall()]
    next_before = items[-1]["created_at"] if len(items) == limit and items else None
    return {"items": items, "next_before": next_before}


@router.get("/{bot_id}/advisor-decisions/{decision_id}")
async def get_advisor_decision(
    bot_id: UUID,
    decision_id: int,
    db: DbDep,
    _user: JwtSubject,
) -> dict[str, Any]:
    row = await db.execute(
        text("SELECT * FROM bot_advisor_decisions WHERE id = :did AND bot_id = :bid"),
        {"did": decision_id, "bid": bot_id},
    )
    decision = row.mappings().first()
    if decision is None:
        raise HTTPException(status_code=404, detail="advisor_decision_not_found")
    return jsonable_encoder(dict(decision))


@router.post("/{bot_id}/start")
async def start_bot(
    bot_id: UUID,
    db: DbDep,
    redis: RedisDep,
    _user: JwtSubject,
) -> dict[str, Any]:
    row = await db.execute(
        text("SELECT id FROM bots WHERE id = :id AND deleted_at IS NULL"),
        {"id": bot_id},
    )
    if row.first() is None:
        raise HTTPException(status_code=404, detail="bot_not_found")
    await db.execute(
        text("UPDATE bots SET status='starting', updated_at=now() WHERE id=:id"),
        {"id": bot_id},
    )
    await db.commit()
    cmd_id = str(uuid.uuid4())
    await redis.xadd(f"bot:control:{bot_id}", {"data": json.dumps({"id": cmd_id, "cmd": "START"})})
    return {"bot_id": str(bot_id), "status": "starting"}


@router.post("/{bot_id}/stop")
async def stop_bot(
    bot_id: UUID,
    db: DbDep,
    redis: RedisDep,
    _user: JwtSubject,
) -> dict[str, Any]:
    await db.execute(
        text("UPDATE bots SET status='pausing', updated_at=now() WHERE id=:id"),
        {"id": bot_id},
    )
    await db.commit()
    cmd_id = str(uuid.uuid4())
    await redis.xadd(f"bot:control:{bot_id}", {"data": json.dumps({"id": cmd_id, "cmd": "STOP"})})
    return {"bot_id": str(bot_id), "status": "pausing"}


@router.post("/{bot_id}/pause")
async def pause_bot(
    bot_id: UUID,
    redis: RedisDep,
    _user: JwtSubject,
) -> dict[str, Any]:
    cmd_id = str(uuid.uuid4())
    await redis.xadd(f"bot:control:{bot_id}", {"data": json.dumps({"id": cmd_id, "cmd": "PAUSE"})})
    return {"bot_id": str(bot_id)}


@router.post("/{bot_id}/resume")
async def resume_bot(
    bot_id: UUID,
    redis: RedisDep,
    _user: JwtSubject,
) -> dict[str, Any]:
    cmd_id = str(uuid.uuid4())
    await redis.xadd(f"bot:control:{bot_id}", {"data": json.dumps({"id": cmd_id, "cmd": "RESUME"})})
    return {"bot_id": str(bot_id)}


@router.post("/{bot_id}/deploy")
async def deploy_bot(
    bot_id: UUID,
    db: DbDep,
    redis: RedisDep,
    _user: JwtSubject,
) -> dict[str, Any]:
    row = await db.execute(
        text(
            "UPDATE bots SET version = version + 1, updated_at=now()"
            " WHERE id=:id AND deleted_at IS NULL RETURNING version"
        ),
        {"id": bot_id},
    )
    new_version = row.scalar_one_or_none()
    if new_version is None:
        raise HTTPException(status_code=404, detail="bot_not_found")
    await db.commit()
    cmd_id = str(uuid.uuid4())
    await redis.xadd(f"bot:control:{bot_id}", {"data": json.dumps({"id": cmd_id, "cmd": "DEPLOY"})})
    return {"bot_id": str(bot_id), "version": new_version}


async def _assert_bot_exists(bot_id: UUID, db: AsyncSession) -> None:
    row = await db.execute(
        text("SELECT id FROM bots WHERE id=:id AND deleted_at IS NULL"),
        {"id": bot_id},
    )
    if row.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="bot_not_found")


async def _assert_stopped(bot_id: UUID, db: AsyncSession) -> None:
    row = await db.execute(
        text("SELECT status FROM bots WHERE id = :id AND deleted_at IS NULL"),
        {"id": bot_id},
    )
    bot = row.first()
    if bot is None:
        raise HTTPException(status_code=404, detail="bot_not_found")
    if bot[0] != "stopped":
        raise HTTPException(status_code=409, detail="bot_must_be_stopped")
