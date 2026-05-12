"""Admin router: /api/admin/config + /api/admin/secrets + reveal endpoint."""
# TODO(phase2-retro): split into admin_config / admin_secrets / admin_capabilities (560+ lines).

import re
import secrets
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Response, status
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.capabilities import KNOWN_ASSET_CLASSES
from app.api.schemas import (
    KEY_PATTERN,
    NAMESPACE_PATTERN,
    ConfigIn,
    ConfigInUpsert,
    ConfigOut,
    SecretIn,
    SecretInUpsert,
    SecretMetadataOut,
    SecretRevealOut,
)
from app.core import metrics
from app.core.cf_access import AdminIdentity
from app.core.deps import get_config, get_db, get_redis, require_admin_jwt
from app.services.config import ConfigService, SecretDecryptError
from app.services.order_capability_service import KNOWN_BROKERS, OrderCapabilityService

ConfigDep = Annotated[ConfigService, Depends(get_config)]
IdentityDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Any, Depends(get_redis)]

log = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_jwt)],
)

# HIGH-7: nonce storage moved to Redis (SETEX + GETDEL) — multi-worker safe,
# TTL-bounded, race-free.  The old in-process set is gone.
_NONCE_TTL_SECONDS = 300
_NONCE_KEY_PREFIX = "csrf:order-cap:"


class OrderCapabilityWrite(BaseModel):
    broker_id: str
    # HIGH-1: asset_class added to match the 0018-widened PK.
    asset_class: str
    order_type: str
    time_in_force: str
    is_supported: bool
    notes: str

    @field_validator("asset_class")
    @classmethod
    def asset_class_known(cls, v: str) -> str:
        if v not in KNOWN_ASSET_CLASSES:
            raise ValueError(f"unknown asset_class; allowed: {sorted(KNOWN_ASSET_CLASSES)}")
        return v

    @field_validator("notes")
    @classmethod
    def notes_printable_ascii(cls, v: str) -> str:
        if len(v) > 256:
            raise ValueError("notes exceeds 256 characters")
        if not re.fullmatch(r"[\x20-\x7E]*", v):
            raise ValueError("notes must be printable ASCII only")
        return v


_LITELLM_PLACEHOLDER_KEY = "sk-bootstrap-rotate-me"


class LiteLLMMasterKeyRotate(BaseModel):
    # security-reviewer M2: 32 chars is the conventional minimum for
    # bearer tokens used in API auth (NIST SP 800-63B guidance for 16
    # bytes of random entropy expressed in base64-ish form).
    value: str = Field(min_length=32, max_length=256)

    @field_validator("value")
    @classmethod
    def _reject_bootstrap_placeholder(cls, v: str) -> str:
        # security-reviewer M1: the bootstrap placeholder is committed
        # to source; rejecting it on rotation keeps an operator who
        # accidentally pastes it from re-arming the known-public default.
        if v == _LITELLM_PLACEHOLDER_KEY:
            raise ValueError(
                "value must not equal the bootstrap placeholder; choose a fresh random key"
            )
        return v


async def parse_order_capability_write(
    body: Annotated[dict[str, Any], Body()],
) -> OrderCapabilityWrite:
    try:
        return OrderCapabilityWrite.model_validate(body)
    except ValidationError as exc:
        # 400 only for notes content-validation failures (printable ASCII, length).
        # Missing required fields (type="missing") return 422 per FastAPI convention.
        notes_value_errors = [
            err
            for err in exc.errors()
            if tuple(err["loc"]) == ("notes",) and err["type"] != "missing"
        ]
        if notes_value_errors:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": "invalid_notes"}},
            ) from exc
        # Strip non-serializable ctx values (Pydantic v2 puts the original
        # exception object in ctx["error"] which is not JSON-serializable).
        errors = [{k: v for k, v in err.items() if k != "ctx"} for err in exc.errors()]
        raise HTTPException(status_code=422, detail=errors) from exc


async def consume_confirmation_nonce(
    x_confirm_nonce: Annotated[str | None, Header(alias="X-Confirm-Nonce")] = None,
    redis: RedisDep = None,
) -> None:
    """HIGH-7: consume a single-use Redis nonce (GETDEL).  403 if absent/expired."""
    if x_confirm_nonce is None:
        raise HTTPException(status_code=403, detail={"error": {"code": "missing_csrf"}})
    deleted = await redis.delete(f"{_NONCE_KEY_PREFIX}{x_confirm_nonce}")
    if not deleted:
        raise HTTPException(status_code=403, detail={"error": {"code": "missing_csrf"}})


def _parse_typed_value(raw: str | None, value_type: str) -> Any:
    if raw is None:
        return None
    if value_type == "int":
        return int(raw)
    if value_type == "bool":
        return raw == "true"
    return raw


def _row_to_config_out(row: Any) -> ConfigOut:
    materialized = (
        row.value_json
        if row.value_type == "json"
        else _parse_typed_value(row.value, row.value_type)
    )
    return ConfigOut(
        namespace=row.namespace,
        key=row.key,
        value=materialized,
        value_type=row.value_type,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/config", response_model=list[ConfigOut])
async def list_config(
    cfg: ConfigDep,
    namespace: str | None = None,
) -> list[ConfigOut]:
    rows = await cfg.list(namespace)
    return [_row_to_config_out(r) for r in rows]


@router.get("/config/{namespace}/{key}", response_model=ConfigOut)
async def get_config_entry(
    namespace: Annotated[str, Path(pattern=NAMESPACE_PATTERN, max_length=128)],
    key: Annotated[str, Path(pattern=KEY_PATTERN, max_length=128)],
    cfg: ConfigDep,
) -> ConfigOut:
    # HIGH-7: single-row point lookup instead of list()-then-loop (N+1).
    row = await cfg.get_exact(namespace, key)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return _row_to_config_out(row)


@router.post("/config", response_model=ConfigOut, status_code=status.HTTP_201_CREATED)
async def create_config(
    body: ConfigIn,
    cfg: ConfigDep,
    identity: IdentityDep,
) -> ConfigOut:
    # TODO(phase2-retro): replace pre-check + upsert with atomic
    # INSERT ... ON CONFLICT DO NOTHING + RETURNING xmax (TOCTOU).
    existing = [r for r in await cfg.list(body.namespace) if r.key == body.key]
    if existing:
        raise HTTPException(status_code=409, detail="already exists")
    row = await cfg.set(body.namespace, body.key, body.value, body.value_type)
    log.info(
        "admin_config_set ns=%s key=%s actor=%s kind=%s",
        body.namespace,
        body.key,
        identity.email,
        identity.kind,
    )
    return _row_to_config_out(row)


@router.put("/config/{namespace}/{key}", response_model=ConfigOut)
async def put_config(
    namespace: Annotated[str, Path(pattern=NAMESPACE_PATTERN, max_length=128)],
    key: Annotated[str, Path(pattern=KEY_PATTERN, max_length=128)],
    body: ConfigInUpsert,
    cfg: ConfigDep,
    identity: IdentityDep,
) -> ConfigOut:
    if body.namespace is not None and body.namespace != namespace:
        raise HTTPException(status_code=422, detail="body ns/key mismatch URL")
    if body.key is not None and body.key != key:
        raise HTTPException(status_code=422, detail="body ns/key mismatch URL")
    row = await cfg.set(namespace, key, body.value, body.value_type)
    log.info(
        "admin_config_put ns=%s key=%s actor=%s kind=%s",
        namespace,
        key,
        identity.email,
        identity.kind,
    )
    return _row_to_config_out(row)


@router.delete("/config/{namespace}/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_config(
    namespace: Annotated[str, Path(pattern=NAMESPACE_PATTERN, max_length=128)],
    key: Annotated[str, Path(pattern=KEY_PATTERN, max_length=128)],
    cfg: ConfigDep,
    identity: IdentityDep,
) -> Response:
    existed = await cfg.delete(namespace, key)
    log.info(
        "admin_config_delete ns=%s key=%s actor=%s row_existed=%s",
        namespace,
        key,
        identity.email,
        existed,
    )
    return Response(status_code=204)


@router.get("/secrets", response_model=list[SecretMetadataOut])
async def list_secrets(
    cfg: ConfigDep,
    namespace: str | None = None,
) -> list[SecretMetadataOut]:
    rows = await cfg.list_secrets(namespace)
    return [
        SecretMetadataOut(
            namespace=r.namespace,
            key=r.key,
            value_type=r.value_type,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


@router.get("/secrets/{namespace}/{key}", response_model=SecretMetadataOut)
async def get_secret_metadata(
    namespace: Annotated[str, Path(pattern=NAMESPACE_PATTERN, max_length=128)],
    key: Annotated[str, Path(pattern=KEY_PATTERN, max_length=128)],
    cfg: ConfigDep,
) -> SecretMetadataOut:
    meta = await cfg.get_secret_metadata(namespace, key)
    if meta is None:
        raise HTTPException(status_code=404, detail="not found")
    return SecretMetadataOut(
        namespace=meta.namespace,
        key=meta.key,
        value_type=meta.value_type,
        created_at=meta.created_at,
        updated_at=meta.updated_at,
    )


@router.post("/secrets", response_model=SecretMetadataOut, status_code=status.HTTP_201_CREATED)
async def create_secret(
    body: SecretIn,
    cfg: ConfigDep,
    identity: IdentityDep,
) -> SecretMetadataOut:
    if await cfg.get_secret_metadata(body.namespace, body.key) is not None:
        raise HTTPException(status_code=409, detail="already exists")
    row = await cfg.set_secret(body.namespace, body.key, body.value, body.value_type)
    log.info(
        "admin_secret_set ns=%s key=%s actor=%s kind=%s",
        body.namespace,
        body.key,
        identity.email,
        identity.kind,
    )
    return SecretMetadataOut(
        namespace=row.namespace,
        key=row.key,
        value_type=row.value_type,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.put("/secrets/ai/litellm_master_key", status_code=200)
async def put_litellm_master_key(
    body: LiteLLMMasterKeyRotate,
    cfg: ConfigDep,
    identity: IdentityDep,
    redis: RedisDep,
    _csrf: Annotated[None, Depends(consume_confirmation_nonce)],
) -> dict[str, bool]:
    """Phase 11a-A.5 HIGH-5: zero-restart rotation.

    Write order is Redis-first then app_secrets (security-reviewer H2):
    Redis is the live source LiteLLM reads, app_secrets is the recovery
    source the lifespan reads at startup. Writing Redis first means a
    failure during Redis.set leaves both stores at the OLD value
    (safe — operator can retry). Writing app_secrets first would have
    left app_secrets ahead of Redis on a Redis.set failure (silent
    divergence; lifespan would later reconcile but only after restart).
    """
    try:
        await redis.set("ai:litellm_master_key", body.value)
    except Exception as exc:  # silent-failure M1: surface Redis failure as 503
        log.error(
            "admin_litellm_master_key_redis_set_failed",
            actor=identity.email,
            kind=identity.kind,
            error_class=type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="redis write failed; rotation not applied — retry",
        ) from exc
    await cfg.set_secret("ai", "litellm_master_key", body.value, "str")
    log.info(
        "admin_litellm_master_key_put",
        actor=identity.email,
        kind=identity.kind,
    )
    return {"ok": True}


@router.put("/secrets/{namespace}/{key}", response_model=SecretMetadataOut)
async def put_secret(
    namespace: Annotated[str, Path(pattern=NAMESPACE_PATTERN, max_length=128)],
    key: Annotated[str, Path(pattern=KEY_PATTERN, max_length=128)],
    body: SecretInUpsert,
    cfg: ConfigDep,
    identity: IdentityDep,
) -> SecretMetadataOut:
    if body.namespace is not None and body.namespace != namespace:
        raise HTTPException(status_code=422, detail="body ns/key mismatch URL")
    if body.key is not None and body.key != key:
        raise HTTPException(status_code=422, detail="body ns/key mismatch URL")
    row = await cfg.set_secret(namespace, key, body.value, body.value_type)
    log.info(
        "admin_secret_put ns=%s key=%s actor=%s kind=%s",
        namespace,
        key,
        identity.email,
        identity.kind,
    )
    return SecretMetadataOut(
        namespace=row.namespace,
        key=row.key,
        value_type=row.value_type,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("/secrets/{namespace}/{key}/reveal", response_model=SecretRevealOut)
async def reveal_secret(
    namespace: Annotated[str, Path(pattern=NAMESPACE_PATTERN, max_length=128)],
    key: Annotated[str, Path(pattern=KEY_PATTERN, max_length=128)],
    response: Response,
    cfg: ConfigDep,
    identity: IdentityDep,
) -> SecretRevealOut:
    meta = await cfg.get_secret_metadata(namespace, key)
    if meta is None:
        raise HTTPException(status_code=404, detail="not found")

    try:
        if meta.value_type == "int":
            value: Any = await cfg.reveal_secret_int(namespace, key)
        elif meta.value_type == "bool":
            value = await cfg.reveal_secret_bool(namespace, key)
        elif meta.value_type == "json":
            value = await cfg.reveal_secret_json(namespace, key)
        else:
            value = await cfg.reveal_secret(namespace, key)
    except SecretDecryptError as exc:
        log.error("secret_decrypt_failed ns=%s key=%s err=%s", namespace, key, exc)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "secret_decryption_failed",
                "hint": "verify APP_SECRET_KEY/PREV_KEY rotation state",
            },
        ) from exc

    response.headers["Cache-Control"] = "no-store, private"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"

    metrics.admin_secret_reveal_total.labels(actor_kind=identity.kind).inc()
    log.info(
        "admin_secret_reveal ns=%s key=%s actor=%s kind=%s",
        namespace,
        key,
        identity.email,
        identity.kind,
    )
    return SecretRevealOut(
        namespace=namespace,
        key=key,
        value=value,
        value_type=meta.value_type,
    )


@router.post("/csrf/issue", status_code=200)
async def issue_confirmation_nonce(redis: RedisDep) -> dict[str, str]:
    """HIGH-7: mint a Redis-backed single-use nonce (SETEX, TTL 300 s)."""
    nonce = secrets.token_urlsafe(32)
    await redis.set(f"{_NONCE_KEY_PREFIX}{nonce}", "1", ex=_NONCE_TTL_SECONDS)
    return {"nonce": nonce}


@router.post("/order-capabilities", status_code=200)
async def set_order_capability(
    body: Annotated[OrderCapabilityWrite, Depends(parse_order_capability_write)],
    identity: IdentityDep,
    db: DbDep,
    redis: RedisDep,
    _csrf: Annotated[None, Depends(consume_confirmation_nonce)],
) -> dict[str, bool]:
    if body.broker_id not in KNOWN_BROKERS:
        raise HTTPException(400, detail={"error": {"code": "unknown_broker_id"}})

    result = await db.execute(
        text("SELECT 1 FROM order_types WHERE code = :c"),
        {"c": body.order_type},
    )
    if result.fetchone() is None:
        raise HTTPException(400, detail={"error": {"code": "unknown_order_type_code"}})

    result = await db.execute(
        text("SELECT 1 FROM time_in_force WHERE code = :c"),
        {"c": body.time_in_force},
    )
    if result.fetchone() is None:
        raise HTTPException(400, detail={"error": {"code": "unknown_time_in_force_code"}})

    # HIGH-6: capture prior state before update for audit log.
    prior_row = await db.execute(
        text(
            """
            SELECT is_supported, notes FROM broker_order_capability
             WHERE broker_id = :b AND asset_class = :a
               AND order_type = :t AND time_in_force = :tif
            """
        ),
        {
            "b": body.broker_id,
            "a": body.asset_class,
            "t": body.order_type,
            "tif": body.time_in_force,
        },
    )
    prior = prior_row.first()

    # HIGH-1: ON CONFLICT target widened to 4-column PK added in migration 0018.
    await db.execute(
        text(
            """
            INSERT INTO broker_order_capability (
                broker_id, asset_class, order_type, time_in_force,
                is_supported, notes, updated_at
            )
            VALUES (
                :broker_id, :asset_class, :order_type, :time_in_force,
                :is_supported, :notes, NOW()
            )
            ON CONFLICT (broker_id, asset_class, order_type, time_in_force)
            DO UPDATE SET is_supported = EXCLUDED.is_supported,
                          notes = EXCLUDED.notes,
                          updated_at = NOW()
            """
        ),
        {
            "broker_id": body.broker_id,
            "asset_class": body.asset_class,
            "order_type": body.order_type,
            "time_in_force": body.time_in_force,
            "is_supported": body.is_supported,
            "notes": body.notes,
        },
    )
    await db.commit()

    # HIGH-6: audit log with actor + before/after.
    log.info(
        "order_capability.set",
        actor=identity.email,
        broker_id=body.broker_id,
        asset_class=body.asset_class,
        order_type=body.order_type,
        time_in_force=body.time_in_force,
        was_supported=(bool(prior.is_supported) if prior else None),
        now_supported=body.is_supported,
    )

    metrics.order_capability_admin_writes_total.inc()
    await OrderCapabilityService(redis=redis, db=db).publish_invalidation(body.broker_id)

    return {"ok": True}


@router.delete("/secrets/{namespace}/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_secret(
    namespace: Annotated[str, Path(pattern=NAMESPACE_PATTERN, max_length=128)],
    key: Annotated[str, Path(pattern=KEY_PATTERN, max_length=128)],
    cfg: ConfigDep,
    identity: IdentityDep,
) -> Response:
    existed = await cfg.delete_secret(namespace, key)
    log.info(
        "admin_secret_delete ns=%s key=%s actor=%s row_existed=%s",
        namespace,
        key,
        identity.email,
        existed,
    )
    return Response(status_code=204)


@router.get("/brokers/{label}/account-hashes")
async def list_broker_account_hashes(
    label: str,
    identity: IdentityDep,
) -> list[dict[str, str]]:
    """Operator-only: returns sidecar-side (account_number, account_hash, mode)
    for a broker gateway label.

    Used to discover SCHWAB_PAPER_ACCOUNT_HASH for the C0 empirical hard-gate
    script (and equivalent operator workflows for other brokers). Bypasses the
    boundary-stripping in AccountResponse / _account_from_proto by reading
    response.accounts directly from the sidecar's ListManagedAccounts gRPC.

    `label` is the gateway label (e.g. "schwab", "isa-paper", "futu",
    "alpaca-paper") — not the broker_id.
    """
    from app._generated.broker.v1 import broker_pb2
    from app.core.deps import get_broker_registry
    from app.services.broker_registry_factory import SIDECAR_BROKERS
    from app.services.brokers import BrokerSidecarTimeout, BrokerSidecarUnavailable

    if label not in SIDECAR_BROKERS:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_gateway_label", "known": sorted(SIDECAR_BROKERS)},
        )

    registry = get_broker_registry()
    try:
        client = await registry.get_client(label)
    except KeyError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "sidecar_not_registered",
                "label": label,
                "hint": (
                    "BrokerRegistry has no client for this label; "
                    "check broker_registry_factory wiring"
                ),
            },
        ) from exc

    try:
        response = await client._call(
            method="ListManagedAccounts",
            rpc=client.stub.ListManagedAccounts,
            request=broker_pb2.Empty(),
        )
    except BrokerSidecarTimeout as exc:
        log.warning("admin_list_broker_account_hashes_timeout label=%s err=%s", label, exc)
        raise HTTPException(
            status_code=504,
            detail={"error": "sidecar_timeout", "label": label, "detail": str(exc)},
        ) from exc
    except BrokerSidecarUnavailable as exc:
        # HIGH-3: grpc_details stays in the server log; the client only sees a
        # safe hint so internal gRPC details do not leak to the browser.
        log.warning(
            "admin_list_broker_account_hashes_unavailable label=%s grpc_code=%s details=%s",
            label,
            exc.grpc_code,
            exc.grpc_details,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "error": "sidecar_unavailable",
                "label": label,
                "hint": (
                    "If grpc_code=FAILED_PRECONDITION, the sidecar needs Configure (token refresh "
                    "or app_key/app_secret seed). If UNAUTHENTICATED, the Schwab refresh_token "
                    "is expired — re-authorize via the Schwab integration UI."
                ),
            },
        ) from exc

    rows = [
        {
            "account_number": acct.account_number,
            "account_hash": acct.account_hash,
            "mode": broker_pb2.TradingMode.Name(acct.mode),
            "gateway_label": acct.gateway_label,
            "currency_base": acct.currency_base,
        }
        for acct in response.accounts
    ]
    # HIGH-3: log every access with actor so account_hash exposure is auditable.
    log.info(
        "admin.account_hashes.fetched",
        actor=identity.email,
        label=label,
        account_count=len(rows),
    )
    return rows
