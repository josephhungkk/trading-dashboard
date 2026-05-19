from __future__ import annotations

from typing import Annotated, Any, Literal, cast
from uuid import UUID

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ws_auth import require_jwt
from app.core.deps import get_db
from app.services.scanner.evaluator import (
    EvaluatorBudgetError,
    EvaluatorParseError,
    ScannerEvaluator,
)
from app.services.scanner.schemas import ScanConfig, UniverseConfig

log = structlog.get_logger()
router = APIRouter(prefix="/api/scanner", tags=["scanner"])
_evaluator = ScannerEvaluator()

DbDep = Annotated[AsyncSession, Depends(get_db)]
JwtSubject = Annotated[str, Depends(require_jwt)]


class ValidateRequest(BaseModel):
    rule_expr: str


class ValidateResponse(BaseModel):
    valid: bool
    error: str | None = None


class CreateScanRequest(BaseModel):
    name: str
    universe_config: dict[str, Any]
    rule_expr: str
    schedule: str | None = None
    market_hours_gate: bool = False
    exchange: str | None = None
    llm_depth: str = "quick"
    alert_id: int | None = None
    enabled: bool = True


@router.post("/validate", response_model=ValidateResponse)
async def validate_rule(body: ValidateRequest, _: JwtSubject) -> ValidateResponse:
    try:
        _evaluator.parse(body.rule_expr)
        return ValidateResponse(valid=True)
    except EvaluatorParseError as exc:
        raise HTTPException(
            422, detail={"error": "rule_expr_parse_error", "message": str(exc)}
        ) from exc
    except EvaluatorBudgetError as exc:
        raise HTTPException(
            422, detail={"error": "rule_expr_budget_exceeded", "message": str(exc)}
        ) from exc


@router.post("/scans", status_code=201)
async def create_scan(body: CreateScanRequest, request: Request, _: JwtSubject) -> dict[str, str]:
    try:
        config = ScanConfig(
            name=body.name,
            universe_config=UniverseConfig(**body.universe_config),
            rule_expr=body.rule_expr,
            schedule=body.schedule,
            market_hours_gate=body.market_hours_gate,
            exchange=body.exchange,
            llm_depth=cast(Literal["quick", "deep"], body.llm_depth),
            alert_id=body.alert_id,
            enabled=body.enabled,
        )
        svc = request.app.state.scanner_service
        scan_id = await svc.save_scan(config)
        if body.schedule:
            await request.app.state.scanner_scheduler.schedule_scan(
                scan_id=scan_id,
                cron_expr=body.schedule,
                market_hours_gate=body.market_hours_gate,
                exchange=body.exchange,
            )
        return {"id": str(scan_id)}
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@router.get("/scans")
async def list_scans(db: DbDep, _: JwtSubject) -> list[dict[str, Any]]:
    rows = await db.execute(sa.text("SELECT * FROM saved_scans ORDER BY created_at DESC"))
    return [dict(r._mapping) for r in rows.fetchall()]


@router.get("/scans/{scan_id}")
async def get_scan(scan_id: UUID, db: DbDep, _: JwtSubject) -> dict[str, Any]:
    row = await db.execute(sa.text("SELECT * FROM saved_scans WHERE id = :id"), {"id": scan_id})
    r = row.fetchone()
    if not r:
        raise HTTPException(404)
    return dict(r._mapping)


@router.put("/scans/{scan_id}")
async def update_scan(
    scan_id: UUID, body: CreateScanRequest, db: DbDep, request: Request, _: JwtSubject
) -> dict[str, str]:
    try:
        _evaluator.parse(body.rule_expr)
    except (EvaluatorParseError, EvaluatorBudgetError) as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    await db.execute(
        sa.text(
            """
            UPDATE saved_scans SET name=:name, universe_config=CAST(:uc AS jsonb),
            rule_expr=:rule, schedule=:sched, market_hours_gate=:mhg, exchange=:exch,
            llm_depth=:depth, alert_id=:alert, enabled=:enabled, updated_at=now()
            WHERE id=:id
            """
        ),
        {
            "name": body.name,
            "uc": UniverseConfig(**body.universe_config).model_dump_json(),
            "rule": body.rule_expr,
            "sched": body.schedule,
            "mhg": body.market_hours_gate,
            "exch": body.exchange,
            "depth": body.llm_depth,
            "alert": body.alert_id,
            "enabled": body.enabled,
            "id": scan_id,
        },
    )
    await db.commit()
    if body.schedule and body.enabled:
        await request.app.state.scanner_scheduler.schedule_scan(
            scan_id=scan_id,
            cron_expr=body.schedule,
            market_hours_gate=body.market_hours_gate,
            exchange=body.exchange,
        )
    else:
        await request.app.state.scanner_scheduler.remove_scan(scan_id)
    return {"id": str(scan_id)}


@router.delete("/scans/{scan_id}", status_code=204)
async def delete_scan(scan_id: UUID, db: DbDep, request: Request, _: JwtSubject) -> None:
    await db.execute(
        sa.text("UPDATE saved_scans SET enabled=false, updated_at=now() WHERE id=:id"),
        {"id": scan_id},
    )
    await db.commit()
    await request.app.state.scanner_scheduler.remove_scan(scan_id)


@router.post("/scans/{scan_id}/run", status_code=202)
async def trigger_run(scan_id: UUID, request: Request, _: JwtSubject) -> dict[str, str]:
    import asyncio

    svc = request.app.state.scanner_service
    run_id = await asyncio.shield(svc.run_scan(scan_id=scan_id))
    return {"run_id": str(run_id)}


@router.post("/runs/adhoc", status_code=202)
async def adhoc_run(body: CreateScanRequest, request: Request, _: JwtSubject) -> dict[str, str]:
    import asyncio

    config = ScanConfig(
        name=body.name,
        universe_config=UniverseConfig(**body.universe_config),
        rule_expr=body.rule_expr,
        llm_depth=cast(Literal["quick", "deep"], body.llm_depth),
    )
    svc = request.app.state.scanner_service
    run_id = await asyncio.shield(svc.run_scan(config=config))
    return {"run_id": str(run_id)}


@router.get("/runs")
async def list_runs(
    db: DbDep,
    _: JwtSubject,
    scan_id: UUID | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    where = "WHERE 1=1"
    params: dict[str, Any] = {"limit": limit}
    if scan_id:
        where += " AND scan_id = :scan_id"
        params["scan_id"] = scan_id
    if cursor:
        where += " AND started_at < CAST(:cursor AS timestamptz)"
        params["cursor"] = cursor
    rows = await db.execute(
        sa.text(f"SELECT * FROM scanner_runs {where} ORDER BY started_at DESC LIMIT :limit"),
        params,
    )
    return [dict(r._mapping) for r in rows.fetchall()]


@router.get("/runs/{run_id}")
async def get_run(run_id: UUID, db: DbDep, _: JwtSubject) -> dict[str, Any]:
    row = await db.execute(sa.text("SELECT * FROM scanner_runs WHERE id=:id"), {"id": run_id})
    r = row.fetchone()
    if not r:
        raise HTTPException(404)
    candidates = await db.execute(
        sa.text("SELECT * FROM scanner_candidates WHERE run_id=:rid ORDER BY matched_at"),
        {"rid": run_id},
    )
    return {
        **dict(r._mapping),
        "candidates": [dict(c._mapping) for c in candidates.fetchall()],
    }


@router.get("/runs/{run_id}/candidates")
async def get_candidates(
    run_id: UUID,
    db: DbDep,
    _: JwtSubject,
    limit: int = 50,
    cursor: str | None = None,
) -> list[dict[str, Any]]:
    where = "WHERE run_id = :rid"
    params: dict[str, Any] = {"rid": run_id, "limit": limit}
    if cursor:
        where += " AND matched_at > CAST(:cursor AS timestamptz)"
        params["cursor"] = cursor
    rows = await db.execute(
        sa.text(f"SELECT * FROM scanner_candidates {where} ORDER BY matched_at LIMIT :limit"),
        params,
    )
    return [dict(r._mapping) for r in rows.fetchall()]
