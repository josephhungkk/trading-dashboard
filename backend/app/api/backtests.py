from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from pydantic import BaseModel, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ws_auth import require_jwt
from app.bot.sandbox import extract_params_schema
from app.core.deps import get_db, get_redis

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/bots/{bot_id}/backtests", tags=["backtests"])

_STRATEGIES_DIR = Path("/strategies")
_BACKTESTABLE_ASSET_CLASSES = {"STOCK", "ETF", "FUTURE", "OPTION", "CRYPTO"}

JwtSubject = Annotated[str, Depends(require_jwt)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Any, Depends(get_redis)]


class BacktestSubmitRequest(BaseModel):
    canonical_id: str
    timeframe: str
    start_date: date
    end_date: date
    slippage_bps: float | None = None
    slippage_atr_pct: float | None = None
    bars_source: str = "db"

    @model_validator(mode="after")
    def validate_slippage_xor(self) -> BacktestSubmitRequest:
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        bps_set = self.slippage_bps is not None
        atr_set = self.slippage_atr_pct is not None
        if bps_set == atr_set:  # both set or neither set
            raise ValueError("exactly one of slippage_bps or slippage_atr_pct must be provided")
        if self.bars_source not in ("db", "backfill", "csv"):
            raise ValueError("bars_source must be db, backfill, or csv")
        return self


async def _get_bot_or_404(bot_id: UUID, db: AsyncSession) -> dict:
    result = await db.execute(
        text("SELECT * FROM bots WHERE id=:id AND deleted_at IS NULL"),
        {"id": str(bot_id)},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="not_found")
    return dict(row)


async def _get_backtest_or_404(backtest_id: UUID, bot_id: UUID, db: AsyncSession) -> dict:
    result = await db.execute(
        text("SELECT * FROM backtests WHERE id=:bid AND bot_id=:bot"),
        {"bid": str(backtest_id), "bot": str(bot_id)},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="not_found")
    return dict(row)


@router.post("", status_code=202)
async def submit_backtest(
    bot_id: UUID,
    body: BacktestSubmitRequest,
    user: JwtSubject,
    db: DbDep,
    redis: RedisDep,
) -> dict:
    bot = await _get_bot_or_404(bot_id, db)

    # Validate asset class
    result = await db.execute(
        text("SELECT asset_class FROM instruments WHERE canonical_id=:cid LIMIT 1"),
        {"cid": body.canonical_id},
    )
    row = result.one_or_none()
    if row is None or row[0] not in _BACKTESTABLE_ASSET_CLASSES:
        raise HTTPException(status_code=422, detail="asset_class_not_backtestable")

    # Validate params_snapshot against current schema
    strategy_path = _STRATEGIES_DIR / bot["strategy_file"]
    schema = extract_params_schema(str(strategy_path)) or {}
    for key, field in schema.items():
        if field.get("required") and key not in (bot.get("params_json") or {}):
            raise HTTPException(status_code=422, detail=f"missing_required_param: {key}")
    schema_hash = hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()

    commission_cfg = await _build_commission_cfg(bot_id, db)

    # CSV validation
    if body.bars_source == "csv":
        result = await db.execute(
            text("""SELECT id FROM backtest_bar_uploads
                    WHERE canonical_id=:cid AND timeframe=:tf
                      AND uploaded_at >= now() - interval '24 hours'
                    ORDER BY uploaded_at DESC LIMIT 1"""),
            {"cid": body.canonical_id, "tf": body.timeframe},
        )
        if result.one_or_none() is None:
            raise HTTPException(status_code=422, detail="no_recent_csv_upload")

    # Insert
    result = await db.execute(
        text("""INSERT INTO backtests(bot_id, status, timeframe, canonical_id,
                    start_date, end_date, slippage_bps, slippage_atr_pct,
                    commission_cfg, params_snapshot, params_schema_hash, bars_source)
                VALUES(:bot_id,'queued',:tf,:cid,:sd,:ed,:sbps,:satr,:ccfg,:ps,:psh,:bsrc)
                RETURNING id"""),
        {
            "bot_id": str(bot_id),
            "tf": body.timeframe,
            "cid": body.canonical_id,
            "sd": body.start_date,
            "ed": body.end_date,
            "sbps": body.slippage_bps,
            "satr": body.slippage_atr_pct,
            "ccfg": json.dumps(commission_cfg),
            "ps": json.dumps(bot.get("params_json", {})),
            "psh": schema_hash,
            "bsrc": body.bars_source,
        },
    )
    backtest_id = str(result.scalar_one())
    await db.commit()
    await redis.rpush("backtest:queue", backtest_id)
    return {"job_id": backtest_id}


@router.get("")
async def list_backtests(
    bot_id: UUID,
    user: JwtSubject,
    db: DbDep,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, le=100),
) -> dict:
    await _get_bot_or_404(bot_id, db)
    params: dict = {"bot_id": str(bot_id), "limit": limit + 1}
    cursor_clause = ""
    if cursor:
        cursor_clause = "AND bt.created_at < :cursor"
        params["cursor"] = cursor
    result = await db.execute(
        text(f"""SELECT id, status, timeframe, canonical_id, start_date, end_date,
                        progress_pct, created_at, completed_at
                 FROM backtests WHERE bot_id=:bot_id {cursor_clause}
                 ORDER BY created_at DESC LIMIT :limit"""),
        params,
    )
    rows = [dict(r._mapping) for r in result]
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = rows[-1]["created_at"].isoformat()
    return {"items": rows, "next_cursor": next_cursor}


@router.get("/{backtest_id}")
async def get_backtest(
    bot_id: UUID,
    backtest_id: UUID,
    user: JwtSubject,
    db: DbDep,
) -> dict:
    return await _get_backtest_or_404(backtest_id, bot_id, db)


@router.delete("/{backtest_id}", status_code=204)
async def delete_backtest(
    bot_id: UUID,
    backtest_id: UUID,
    user: JwtSubject,
    db: DbDep,
    redis: RedisDep,
    cascade: bool = Query(default=False),
) -> None:
    row = await _get_backtest_or_404(backtest_id, bot_id, db)
    # Check for children
    children = await db.execute(
        text("SELECT COUNT(*) FROM backtests WHERE parent_backtest_id=:id"),
        {"id": str(backtest_id)},
    )
    child_count = children.scalar_one()
    if child_count > 0 and not cascade:
        raise HTTPException(status_code=409, detail={"children": child_count})

    if row["status"] in ("queued", "running"):
        await redis.set(f"backtest:cancel:{backtest_id}", "1", ex=3600)

    await db.execute(text("DELETE FROM backtests WHERE id=:id"), {"id": str(backtest_id)})
    await db.commit()


@router.post("/upload-bars")
async def upload_bars(
    bot_id: UUID,
    user: JwtSubject,
    db: DbDep,
    file: UploadFile,
    canonical_id: str = Query(...),
    timeframe: str = Query(...),
) -> dict:
    await _get_bot_or_404(bot_id, db)

    # Resolve instrument
    result = await db.execute(
        text("SELECT id FROM instruments WHERE canonical_id=:cid LIMIT 1"),
        {"cid": canonical_id},
    )
    instr_row = result.one_or_none()
    if instr_row is None:
        raise HTTPException(status_code=422, detail="instrument_not_found")
    instrument_id = instr_row[0]

    content = await file.read()
    rows = _parse_csv(content.decode())

    # Insert upload metadata
    result = await db.execute(
        text("""INSERT INTO backtest_bar_uploads(canonical_id, timeframe, bar_count)
                VALUES(:cid,:tf,:bc) RETURNING id"""),
        {"cid": canonical_id, "tf": timeframe, "bc": len(rows)},
    )
    upload_id = result.scalar_one()

    # Insert bars
    for row_data in rows:
        await db.execute(
            text(
                "INSERT INTO backtest_bars"
                "(upload_id,instrument_id,bucket_start,open,high,low,close,volume)"
                " VALUES(:uid,:iid,:ts,:o,:h,:l,:c,:v)"
                " ON CONFLICT DO NOTHING"
            ),
            {"uid": str(upload_id), "iid": instrument_id, **row_data},
        )
    await db.commit()
    return {"upload_id": str(upload_id), "canonical_id": canonical_id, "bar_count": len(rows)}


def _parse_csv(content: str) -> list[dict]:
    import csv
    import io
    from decimal import Decimal

    reader = csv.DictReader(io.StringIO(content))
    rows = []
    for r in reader:
        ts_raw = r.get("timestamp") or r.get("Timestamp")
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except ValueError:
            ts = datetime.fromtimestamp(int(str(ts_raw)) / 1000, tz=UTC)
        rows.append(
            {
                "ts": ts,
                "o": Decimal(r["open"]),
                "h": Decimal(r["high"]),
                "l": Decimal(r["low"]),
                "c": Decimal(r["close"]),
                "v": Decimal(r["volume"]) if r.get("volume") else None,
            }
        )
    return rows


async def _build_commission_cfg(bot_id: UUID, db: AsyncSession) -> dict:
    # Get broker from bot_accounts
    result = await db.execute(
        text("""SELECT ba.broker_id FROM bot_accounts boa
                JOIN broker_accounts ba ON ba.id = boa.account_id
                WHERE boa.bot_id=:bid LIMIT 1"""),
        {"bid": str(bot_id)},
    )
    broker_row = result.one_or_none()
    active_broker_id = broker_row[0] if broker_row else "ibkr"
    return {
        "captured_at": datetime.now(UTC).isoformat(),
        "active_broker_id": active_broker_id,
        "schedules": {
            "ibkr": {"per_share": 0.005, "min_per_order": 1.00, "tier": "fixed"},
            "futu": {"per_trade_hkd": 30.0},
            "schwab": {"us_equity": 0.0},
            "alpaca": {"us_equity": 0.0},
        },
    }
