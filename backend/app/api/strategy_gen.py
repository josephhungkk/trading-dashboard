from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import consume_confirmation_nonce
from app.api.ws_auth import require_jwt
from app.core.deps import get_db, require_admin_jwt
from app.services.strategy_gen.sandbox import compute_source_hash

router = APIRouter(prefix="/api/strategy-gen", tags=["strategy-gen"])


class GeneratedStrategyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    source_hash: str
    llm_model: str
    sandbox_status: str
    sandbox_error: str | None = None
    backtest_id: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime


class GeneratedStrategyDetail(GeneratedStrategyResponse):
    source_code: str
    generation_prompt: str


class GenerateRequest(BaseModel):
    asset_class: str
    market_context: str
    llm_model: str = "ollama/qwen2.5-coder"


class ApproveRequest(BaseModel):
    bot_name: str


@router.get("", response_model=list[GeneratedStrategyResponse])
async def list_strategies(
    session: Annotated[AsyncSession, Depends(get_db)],
    _jwt: Annotated[Any, Depends(require_jwt)],
) -> list[GeneratedStrategyResponse]:
    result = await session.execute(
        text(
            "SELECT id, name, source_hash, llm_model, sandbox_status, sandbox_error,"
            "       backtest_id::text AS backtest_id, approved_by, approved_at, created_at"
            " FROM generated_strategies ORDER BY created_at DESC LIMIT 100"
        )
    )
    rows = result.mappings().all()
    return [GeneratedStrategyResponse(**dict(row)) for row in rows]


@router.get("/{strategy_id}", response_model=GeneratedStrategyDetail)
async def get_strategy(
    strategy_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    _jwt: Annotated[Any, Depends(require_jwt)],
) -> GeneratedStrategyDetail:
    result = await session.execute(
        text(
            "SELECT id, name, source_code, source_hash, generation_prompt, llm_model,"
            "       sandbox_status, sandbox_error, approved_by, approved_at, created_at"
            " FROM generated_strategies WHERE id = :id"
        ),
        {"id": strategy_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return GeneratedStrategyDetail(**dict(row))


@router.post("/generate", status_code=202)
async def generate_strategy(
    body: GenerateRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    _jwt: Annotated[Any, Depends(require_admin_jwt)],
) -> dict[str, Any]:
    name = f"gen_{body.asset_class}_{body.llm_model}"
    prompt_hash = compute_source_hash(body.market_context)
    result = await session.execute(
        text(
            "INSERT INTO generated_strategies"
            " (name, source_code, source_hash, generation_prompt,"
            "  prompt_hash, llm_model, sandbox_status)"
            " VALUES (:name, '', '', :prompt, :prompt_hash, :model, 'pending')"
            " RETURNING id"
        ),
        {
            "name": name,
            "prompt": body.market_context,
            "prompt_hash": prompt_hash,
            "model": body.llm_model,
        },
    )
    row_id = result.scalar_one()
    await session.commit()
    return {"id": row_id, "status": "pending"}


@router.post("/{strategy_id}/approve")
async def approve_strategy(
    strategy_id: int,
    body: ApproveRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    _jwt: Annotated[Any, Depends(require_admin_jwt)],
    _csrf: Annotated[None, Depends(consume_confirmation_nonce)],
) -> dict[str, Any]:
    result = await session.execute(
        text("SELECT id, sandbox_status, name FROM generated_strategies WHERE id = :id"),
        {"id": strategy_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Strategy not found")
    if row["sandbox_status"] != "validated":
        raise HTTPException(status_code=422, detail="strategy must be validated before approval")

    subject: str = getattr(_jwt, "sub", getattr(_jwt, "email", "unknown"))

    async with session.begin_nested():
        sc = f"generated:{strategy_id}"
        bot_result = await session.execute(
            text(
                "INSERT INTO bots"
                " (id, name, strategy_file, strategy_class, status, mode, params_json, created_at)"
                " VALUES (gen_random_uuid(), :name, '', :sc, 'paper_pending', 'paper', '{}', now())"
                " RETURNING id"
            ),
            {"name": body.bot_name, "sc": sc},
        )
        bot_uuid = bot_result.scalar_one()
        await session.execute(
            text(
                "UPDATE generated_strategies"
                " SET sandbox_status='promoted', approved_by=:sub, approved_at=now(),"
                "     promoted_bot_id=:bot_id"
                " WHERE id=:id"
            ),
            {"sub": subject, "id": strategy_id, "bot_id": bot_uuid},
        )
        bot_id_str = str(bot_uuid)

    await session.commit()
    return {"strategy_id": strategy_id, "bot_id": bot_id_str, "status": "paper_pending"}


@router.post("/{strategy_id}/reject")
async def reject_strategy(
    strategy_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    _jwt: Annotated[Any, Depends(require_admin_jwt)],
    _csrf: Annotated[None, Depends(consume_confirmation_nonce)],
) -> dict[str, Any]:
    result = await session.execute(
        text("SELECT id, sandbox_status FROM generated_strategies WHERE id = :id"),
        {"id": strategy_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Strategy not found")
    if row["sandbox_status"] == "promoted":
        raise HTTPException(status_code=422, detail="cannot reject a promoted strategy")

    await session.execute(
        text(
            "UPDATE generated_strategies"
            " SET sandbox_status='rejected', sandbox_error='manually rejected'"
            " WHERE id=:id"
        ),
        {"id": strategy_id},
    )
    await session.commit()
    return {"id": strategy_id, "status": "rejected"}
