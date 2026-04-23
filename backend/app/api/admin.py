"""Admin router: /api/admin/config + /api/admin/secrets + reveal endpoint."""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status

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
from app.core.deps import get_config, require_admin_jwt
from app.services.config import ConfigService

ConfigDep = Annotated[ConfigService, Depends(get_config)]
IdentityDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_jwt)],
)


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
