"""ConfigService — typed DB-backed config + Fernet-encrypted secrets."""

import builtins
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import delete, null, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import metrics
from app.models import AppConfig, AppSecret
from app.services.config_cache import ConfigCache

log = logging.getLogger(__name__)

ValueType = Literal["str", "int", "bool", "json"]


class ConfigTypeError(ValueError):
    pass


@dataclass
class SecretMetadata:
    namespace: str
    key: str
    value_type: str
    created_at: datetime
    updated_at: datetime


def _coerce_from_stored(raw: str | None, raw_json: Any, value_type: str) -> Any:
    if value_type == "json":
        return raw_json
    if value_type == "int":
        return int(raw) if raw is not None else None
    if value_type == "bool":
        return raw == "true" if raw is not None else None
    return raw


class ConfigService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        cache: ConfigCache,
        secrets_cache: ConfigCache,
        fernet: Fernet | MultiFernet,
    ) -> None:
        self._session_factory = session_factory
        self._cache = cache
        self._secrets_cache = secrets_cache
        self._fernet = fernet

    async def get(self, ns: str, key: str, default: Any = None) -> Any:
        cached = self._cache.get((ns, key))
        if cached is not None:
            metrics.config_ops_total.labels(op="get", kind="config", result="hit").inc()
            return cached[0]
        async with self._session_factory() as s:
            stmt = select(AppConfig.value, AppConfig.value_json, AppConfig.value_type).where(
                AppConfig.namespace == ns, AppConfig.key == key
            )
            row = (await s.execute(stmt)).one_or_none()
        if row is None:
            metrics.config_ops_total.labels(op="get", kind="config", result="miss").inc()
            return default
        materialized = _coerce_from_stored(row.value, row.value_json, row.value_type)
        self._cache.set((ns, key), (materialized, row.value_type))
        metrics.config_ops_total.labels(op="get", kind="config", result="ok").inc()
        return materialized

    async def get_int(self, ns: str, key: str, default: int | None = None) -> int | None:
        return cast(int | None, await self._get_typed(ns, key, "int", default))

    async def get_bool(self, ns: str, key: str, default: bool | None = None) -> bool | None:
        return cast(bool | None, await self._get_typed(ns, key, "bool", default))

    async def get_json(self, ns: str, key: str, default: Any = None) -> Any:
        return await self._get_typed(ns, key, "json", default)

    async def _get_typed(self, ns: str, key: str, expected: str, default: Any) -> Any:
        cached = self._cache.get((ns, key))
        if cached is not None:
            value, stored_type = cached
            if stored_type != expected:
                raise ConfigTypeError(
                    f"{ns}.{key} has value_type={stored_type!r}, accessor expected {expected!r}"
                )
            return value
        async with self._session_factory() as s:
            stmt = select(AppConfig.value, AppConfig.value_json, AppConfig.value_type).where(
                AppConfig.namespace == ns, AppConfig.key == key
            )
            row = (await s.execute(stmt)).one_or_none()
        if row is None:
            return default
        if row.value_type != expected:
            raise ConfigTypeError(
                f"{ns}.{key} has value_type={row.value_type!r}, accessor expected {expected!r}"
            )
        materialized = _coerce_from_stored(row.value, row.value_json, row.value_type)
        self._cache.set((ns, key), (materialized, row.value_type))
        return materialized

    async def set(self, ns: str, key: str, value: Any, value_type: str = "str") -> AppConfig:
        if value_type not in ("str", "int", "bool", "json"):
            raise ValueError(f"invalid value_type={value_type!r}")

        # For non-json rows, bind SQL NULL (not JSONB null) to value_json so the
        # app_config_value_exclusive CHECK constraint is satisfied. JSONB columns
        # otherwise serialize Python None as JSONB 'null' literal, which is NOT NULL.
        if value_type == "json":
            row_value = None
            row_value_json: Any = value
        elif value_type == "bool":
            row_value = "true" if bool(value) else "false"
            row_value_json = null()
        elif value_type == "int":
            row_value = str(int(value))
            row_value_json = null()
        else:
            row_value = str(value)
            row_value_json = null()

        async with self._session_factory() as s:
            base = pg_insert(AppConfig).values(
                namespace=ns,
                key=key,
                value=row_value,
                value_json=row_value_json,
                value_type=value_type,
            )
            stmt = base.on_conflict_do_update(
                index_elements=["namespace", "key"],
                set_={
                    "value": base.excluded.value,
                    "value_json": base.excluded.value_json,
                    "value_type": base.excluded.value_type,
                    "updated_at": text("now()"),
                },
            ).returning(AppConfig)
            result = await s.execute(stmt)
            row: AppConfig = result.scalar_one()
            await s.commit()

        self._cache.pop((ns, key))
        await self._cache.publish_invalidation(ns, key)
        metrics.config_ops_total.labels(op="set", kind="config", result="ok").inc()
        return row

    async def delete(self, ns: str, key: str) -> bool:
        async with self._session_factory() as s:
            result = await s.execute(
                delete(AppConfig).where(AppConfig.namespace == ns, AppConfig.key == key)
            )
            await s.commit()
            existed = bool(result.rowcount > 0)  # type: ignore[attr-defined]
        self._cache.pop((ns, key))
        await self._cache.publish_invalidation(ns, key)
        metrics.config_ops_total.labels(op="delete", kind="config", result="ok").inc()
        return existed

    async def list(self, namespace: str | None = None) -> builtins.list[AppConfig]:
        async with self._session_factory() as s:
            stmt = select(AppConfig)
            if namespace is not None:
                stmt = stmt.where(AppConfig.namespace == namespace)
            stmt = stmt.order_by(AppConfig.namespace, AppConfig.key)
            rows = (await s.execute(stmt)).scalars().all()
        metrics.config_ops_total.labels(op="list", kind="config", result="ok").inc()
        return list(rows)

    async def set_secret(self, ns: str, key: str, value: Any, value_type: str = "str") -> AppSecret:
        if value_type not in ("str", "int", "bool", "json"):
            raise ValueError(f"invalid value_type={value_type!r}")
        if value_type == "json":
            plaintext = json.dumps(value).encode()
        elif value_type == "bool":
            plaintext = b"true" if bool(value) else b"false"
        elif value_type == "int":
            plaintext = str(int(value)).encode()
        else:
            plaintext = str(value).encode()
        ciphertext = self._fernet.encrypt(plaintext)

        async with self._session_factory() as s:
            base = pg_insert(AppSecret).values(
                namespace=ns,
                key=key,
                value_encrypted=ciphertext,
                value_type=value_type,
            )
            stmt = base.on_conflict_do_update(
                index_elements=["namespace", "key"],
                set_={
                    "value_encrypted": base.excluded.value_encrypted,
                    "value_type": base.excluded.value_type,
                    "updated_at": text("now()"),
                },
            ).returning(AppSecret)
            result = await s.execute(stmt)
            row: AppSecret = result.scalar_one()
            await s.commit()

        self._secrets_cache.pop((ns, key))
        await self._secrets_cache.publish_invalidation(ns, key)
        metrics.config_ops_total.labels(op="set", kind="secret", result="ok").inc()
        return row

    async def get_secret_metadata(self, ns: str, key: str) -> SecretMetadata | None:
        async with self._session_factory() as s:
            stmt = select(
                AppSecret.namespace,
                AppSecret.key,
                AppSecret.value_type,
                AppSecret.created_at,
                AppSecret.updated_at,
            ).where(AppSecret.namespace == ns, AppSecret.key == key)
            row = (await s.execute(stmt)).one_or_none()
        if row is None:
            return None
        return SecretMetadata(
            namespace=row.namespace,
            key=row.key,
            value_type=row.value_type,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def reveal_secret(self, ns: str, key: str, default: Any = None) -> Any:
        return await self._reveal_typed(ns, key, None, default)

    async def reveal_secret_int(self, ns: str, key: str, default: int | None = None) -> int | None:
        return cast(int | None, await self._reveal_typed(ns, key, "int", default))

    async def reveal_secret_bool(
        self, ns: str, key: str, default: bool | None = None
    ) -> bool | None:
        return cast(bool | None, await self._reveal_typed(ns, key, "bool", default))

    async def reveal_secret_json(self, ns: str, key: str, default: Any = None) -> Any:
        return await self._reveal_typed(ns, key, "json", default)

    async def _reveal_typed(self, ns: str, key: str, expected: str | None, default: Any) -> Any:
        async with self._session_factory() as s:
            stmt = select(AppSecret.value_encrypted, AppSecret.value_type).where(
                AppSecret.namespace == ns, AppSecret.key == key
            )
            row = (await s.execute(stmt)).one_or_none()
        if row is None:
            return default
        if expected is not None and row.value_type != expected:
            raise ConfigTypeError(
                f"{ns}.{key} has value_type={row.value_type!r}, accessor expected {expected!r}"
            )
        try:
            plaintext = self._fernet.decrypt(row.value_encrypted)
        except InvalidToken:
            log.error(
                "fernet_decrypt_failed ns=%s key=%s (APP_SECRET_KEY rotated or row tampered)",
                ns,
                key,
            )
            raise
        # Detect PREV-key hit (MultiFernet).
        if isinstance(self._fernet, MultiFernet):
            primary = self._fernet._fernets[0]
            try:
                primary.decrypt(row.value_encrypted)
            except InvalidToken:
                metrics.fernet_prev_key_hits_total.inc()
                log.info("fernet_prev_key_hit ns=%s key=%s", ns, key)

        if row.value_type == "json":
            return json.loads(plaintext.decode())
        if row.value_type == "int":
            return int(plaintext.decode())
        if row.value_type == "bool":
            return plaintext.decode() == "true"
        return plaintext.decode()

    async def delete_secret(self, ns: str, key: str) -> bool:
        async with self._session_factory() as s:
            result = await s.execute(
                delete(AppSecret).where(AppSecret.namespace == ns, AppSecret.key == key)
            )
            await s.commit()
            existed = bool(result.rowcount > 0)  # type: ignore[attr-defined]
        self._secrets_cache.pop((ns, key))
        await self._secrets_cache.publish_invalidation(ns, key)
        metrics.config_ops_total.labels(op="delete", kind="secret", result="ok").inc()
        return existed

    async def list_secrets(self, namespace: str | None = None) -> builtins.list[SecretMetadata]:
        async with self._session_factory() as s:
            stmt = select(
                AppSecret.namespace,
                AppSecret.key,
                AppSecret.value_type,
                AppSecret.created_at,
                AppSecret.updated_at,
            )
            if namespace is not None:
                stmt = stmt.where(AppSecret.namespace == namespace)
            stmt = stmt.order_by(AppSecret.namespace, AppSecret.key)
            rows = (await s.execute(stmt)).all()
        metrics.config_ops_total.labels(op="list", kind="secret", result="ok").inc()
        return [
            SecretMetadata(
                namespace=r.namespace,
                key=r.key,
                value_type=r.value_type,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]
