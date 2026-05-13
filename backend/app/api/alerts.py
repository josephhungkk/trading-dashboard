"""REST endpoints for alerts.

7 operations across 6 URL paths. All gated on require_jwt.
Cross-subject 404 has IDENTICAL body to unknown-id 404 — existence-oracle defence.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import consume_confirmation_nonce
from app.api.ws_auth import require_jwt
from app.core.deps import get_db
from app.services.alerts.exceptions import (
    AlreadyActiveError,
    ParserUnavailableError,
    RuleCrossSubjectError,
    RuleNotFoundError,
)
from app.services.alerts.predicates import PredicateValidationError, validate_schema
from app.services.alerts.rate_limiter import (
    RateLimitExceededError,
    make_create_limiter,
    make_dry_run_limiter,
)
from app.services.alerts.rules import (
    confirm_rule,
    create_rule,
    delete_rule,
    get_rule,
    list_rules,
    update_predicate,
)

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

_CREATE_LIMITER = make_create_limiter()
_DRY_RUN_LIMITER = make_dry_run_limiter()

_NOT_FOUND_BODY = {"error_code": "not_found"}
_RATE_LIMIT_RETRY_AFTER_S = 60

JwtSubject = Annotated[str, Depends(require_jwt)]
DbSession = Annotated[AsyncSession, Depends(get_db)]
# Phase 11b chunk-B-close: mutating routes consume a single-use Redis nonce.
# Header name matches the existing admin convention (X-Confirm-Nonce). The FE
# `services/alerts/api.ts` issues this header via `services/admin/api.ts`.
CsrfNonce = Annotated[None, Depends(consume_confirmation_nonce)]


def _identity_404() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_BODY)


class CreateAlertRequest(BaseModel):
    user_label: str
    original_nl: str
    predicate_json: dict[str, Any] | None = None
    delivery_channels: list[str] = Field(default_factory=lambda: ["in_app"])
    tick_subscribed: bool = False


class UpdatePredicateRequest(BaseModel):
    predicate_json: dict[str, Any]


def _rule_to_dict(rule: Any) -> dict[str, Any]:
    return {
        "id": rule.id,
        "user_label": rule.user_label,
        "original_nl": rule.original_nl,
        "predicate_json": rule.predicate_json,
        "requires_capabilities": rule.requires_capabilities,
        "parse_status": rule.parse_status,
        "delivery_channels": rule.delivery_channels,
        "tick_subscribed": rule.tick_subscribed,
        "status": rule.status,
        "dormancy_reason": rule.dormancy_reason,
        "created_at": rule.created_at.isoformat(),
        "updated_at": rule.updated_at.isoformat(),
    }


@router.post("")
async def create_alert(
    req: CreateAlertRequest,
    request: Request,
    jwt_subject: JwtSubject,
    db: DbSession,
    _csrf: CsrfNonce,
) -> dict[str, Any]:
    try:
        _CREATE_LIMITER.check(jwt_subject)
    except RateLimitExceededError as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error_code": "rate_limited"},
            headers={"Retry-After": str(_RATE_LIMIT_RETRY_AFTER_S)},
        ) from exc

    if req.predicate_json is not None:
        try:
            validate_schema(req.predicate_json)
        except PredicateValidationError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error_code": "invalid_predicate",
                    "schema_errors": exc.schema_errors,
                },
            ) from exc
        rule = await create_rule(
            db,
            jwt_subject=jwt_subject,
            user_label=req.user_label,
            original_nl=req.original_nl,
            predicate_json=req.predicate_json,
            parse_status="manual",
            delivery_channels=req.delivery_channels,
            tick_subscribed=req.tick_subscribed,
        )
        return _rule_to_dict(rule)

    try:
        from app.services.alerts.parser import parse_nl

        client: Any = getattr(request.app.state, "ai_router", None)
        if client is None:
            raise ParserUnavailableError("ai_router unavailable")
        parse_result = await parse_nl(
            client=client,
            original_nl=req.original_nl,
            symbols_user_watches=[],
        )
    except ParserUnavailableError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "parser_unavailable"},
        ) from exc

    if parse_result.parse_status == "failed":
        return {
            "id": None,
            "parse_status": "failed",
            "partial_predicate": parse_result.partial_predicate,
            "suggestions": parse_result.suggestions,
        }

    if parse_result.predicate_json is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "parser_unavailable"},
        )

    rule = await create_rule(
        db,
        jwt_subject=jwt_subject,
        user_label=req.user_label,
        original_nl=req.original_nl,
        predicate_json=parse_result.predicate_json,
        parse_status=parse_result.parse_status,
        parse_metadata=parse_result.parse_metadata,
        delivery_channels=req.delivery_channels,
        tick_subscribed=req.tick_subscribed,
    )
    return _rule_to_dict(rule)


@router.get("")
async def list_alerts(
    jwt_subject: JwtSubject,
    db: DbSession,
) -> dict[str, Any]:
    rules = await list_rules(db, jwt_subject=jwt_subject)
    return {"alerts": [_rule_to_dict(rule) for rule in rules]}


@router.get("/recent-fires")
async def recent_fires(
    jwt_subject: JwtSubject,
    db: DbSession,
    since: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    bounded_limit = min(max(limit, 1), 200)
    if since:
        rows = (
            await db.execute(
                text(
                    "SELECT id, alert_id, fired_at, verdict, fire_context_id "
                    "FROM alert_fires WHERE jwt_subject = :s AND fired_at > :since "
                    "ORDER BY fired_at DESC LIMIT :n"
                ),
                {"s": jwt_subject, "since": since, "n": bounded_limit},
            )
        ).all()
    else:
        rows = (
            await db.execute(
                text(
                    "SELECT id, alert_id, fired_at, verdict, fire_context_id "
                    "FROM alert_fires WHERE jwt_subject = :s "
                    "ORDER BY fired_at DESC LIMIT :n"
                ),
                {"s": jwt_subject, "n": bounded_limit},
            )
        ).all()
    return {
        "fires": [
            {
                "id": row.id,
                "alert_id": row.alert_id,
                "fired_at": row.fired_at.isoformat(),
                "verdict": row.verdict,
            }
            for row in rows
        ]
    }


@router.get("/{alert_id}")
async def get_alert(
    alert_id: int,
    jwt_subject: JwtSubject,
    db: DbSession,
) -> dict[str, Any]:
    try:
        rule = await get_rule(db, rule_id=alert_id, jwt_subject=jwt_subject)
    except (RuleNotFoundError, RuleCrossSubjectError) as exc:
        raise _identity_404() from exc
    return _rule_to_dict(rule)


@router.put("/{alert_id}")
async def put_predicate(
    alert_id: int,
    req: UpdatePredicateRequest,
    jwt_subject: JwtSubject,
    db: DbSession,
    _csrf: CsrfNonce,
) -> dict[str, Any]:
    try:
        rule = await update_predicate(
            db,
            rule_id=alert_id,
            jwt_subject=jwt_subject,
            predicate_json=req.predicate_json,
        )
    except PredicateValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "invalid_predicate",
                "schema_errors": exc.schema_errors,
            },
        ) from exc
    except (RuleNotFoundError, RuleCrossSubjectError) as exc:
        raise _identity_404() from exc
    return _rule_to_dict(rule)


@router.delete("/{alert_id}", status_code=204)
async def delete_alert(
    alert_id: int,
    jwt_subject: JwtSubject,
    db: DbSession,
    _csrf: CsrfNonce,
) -> None:
    try:
        await delete_rule(db, rule_id=alert_id, jwt_subject=jwt_subject)
    except (RuleNotFoundError, RuleCrossSubjectError) as exc:
        raise _identity_404() from exc


@router.post("/{alert_id}/confirm")
async def confirm_alert(
    alert_id: int,
    jwt_subject: JwtSubject,
    db: DbSession,
    _csrf: CsrfNonce,
) -> dict[str, Any]:
    try:
        rule = await confirm_rule(db, rule_id=alert_id, jwt_subject=jwt_subject)
    except AlreadyActiveError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"error_code": "already_active"},
        ) from exc
    except (RuleNotFoundError, RuleCrossSubjectError) as exc:
        raise _identity_404() from exc
    return _rule_to_dict(rule)
