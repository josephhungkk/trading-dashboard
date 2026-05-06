"""Admin router: /api/admin/config + /api/admin/secrets + reveal endpoint."""

import logging
import re
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, ValidationError, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
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
from app.services.config import ConfigService
from app.services.order_capability_service import KNOWN_BROKERS, OrderCapabilityService

ConfigDep = Annotated[ConfigService, Depends(get_config)]
IdentityDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Any, Depends(get_redis)]

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_jwt)],
)

_CONFIRMATION_NONCES: set[str] = set()


class OrderCapabilityWrite(BaseModel):
    broker_id: str
    order_type: str
    time_in_force: str
    is_supported: bool
    notes: str

    @field_validator("notes")
    @classmethod
    def notes_printable_ascii(cls, v: str) -> str:
        if len(v) > 256:
            raise ValueError("notes exceeds 256 characters")
        if not re.fullmatch(r"[\x20-\x7E]*", v):
            raise ValueError("notes must be printable ASCII only")
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
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


async def require_confirmation_nonce(
    x_confirm_nonce: Annotated[str | None, Header(alias="X-Confirm-Nonce")] = None,
) -> None:
    if x_confirm_nonce is None or x_confirm_nonce not in _CONFIRMATION_NONCES:
        raise HTTPException(status_code=403, detail={"error": {"code": "missing_csrf"}})
    _CONFIRMATION_NONCES.discard(x_confirm_nonce)


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
    namespace: str,
    key: str,
    cfg: ConfigDep,
) -> ConfigOut:
    rows = await cfg.list(namespace)
    for r in rows:
        if r.key == key:
            return _row_to_config_out(r)
    raise HTTPException(status_code=404, detail="not found")


@router.post("/config", response_model=ConfigOut, status_code=status.HTTP_201_CREATED)
async def create_config(
    body: ConfigIn,
    cfg: ConfigDep,
    identity: IdentityDep,
) -> ConfigOut:
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
    namespace: str,
    key: str,
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
    namespace: str,
    key: str,
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
    namespace: str,
    key: str,
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


@router.put("/secrets/{namespace}/{key}", response_model=SecretMetadataOut)
async def put_secret(
    namespace: str,
    key: str,
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
    namespace: str,
    key: str,
    response: Response,
    cfg: ConfigDep,
    identity: IdentityDep,
) -> SecretRevealOut:
    meta = await cfg.get_secret_metadata(namespace, key)
    if meta is None:
        raise HTTPException(status_code=404, detail="not found")

    if meta.value_type == "int":
        value: Any = await cfg.reveal_secret_int(namespace, key)
    elif meta.value_type == "bool":
        value = await cfg.reveal_secret_bool(namespace, key)
    elif meta.value_type == "json":
        value = await cfg.reveal_secret_json(namespace, key)
    else:
        value = await cfg.reveal_secret(namespace, key)

    response.headers["Cache-Control"] = "no-store, private"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Pragma"] = "no-cache"

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
async def issue_confirmation_nonce() -> dict[str, str]:
    nonce = secrets.token_urlsafe(32)
    _CONFIRMATION_NONCES.add(nonce)
    return {"nonce": nonce}


@router.post("/order-capabilities", status_code=200)
async def set_order_capability(
    body: Annotated[OrderCapabilityWrite, Depends(parse_order_capability_write)],
    db: DbDep,
    redis: RedisDep,
    _csrf: Annotated[None, Depends(require_confirmation_nonce)],
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

    await db.execute(
        text(
            """
            INSERT INTO broker_order_capability (
                broker_id, order_type, time_in_force, is_supported, notes, updated_at
            )
            VALUES (
                :broker_id, :order_type, :time_in_force, :is_supported, :notes, NOW()
            )
            ON CONFLICT (broker_id, order_type, time_in_force)
            DO UPDATE SET is_supported = EXCLUDED.is_supported,
                          notes = EXCLUDED.notes,
                          updated_at = NOW()
            """
        ),
        {
            "broker_id": body.broker_id,
            "order_type": body.order_type,
            "time_in_force": body.time_in_force,
            "is_supported": body.is_supported,
            "notes": body.notes,
        },
    )
    await db.commit()

    metrics.order_capability_admin_writes_total.inc()
    await OrderCapabilityService(db, redis).publish_invalidation(body.broker_id)

    return {"ok": True}


@router.delete("/secrets/{namespace}/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_secret(
    namespace: str,
    key: str,
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

    if label not in SIDECAR_BROKERS:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_gateway_label", "known": sorted(SIDECAR_BROKERS)},
        )

    registry = get_broker_registry()
    client = await registry.get_client(label)
    response = await client._call(
        method="ListManagedAccounts",
        rpc=client.stub.ListManagedAccounts,
        request=broker_pb2.Empty(),
    )
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
    log.info(
        "admin_list_broker_account_hashes label=%s actor=%s rows=%d",
        label,
        identity.email,
        len(rows),
    )
    return rows
