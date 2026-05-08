"""ConfigService — typed DB-backed config + Fernet-encrypted secrets.

Cache coherence contract
------------------------
Writes (``set``, ``set_secret``, ``delete``, ``delete_secret``) invalidate the
local in-process cache and publish a Redis pub/sub message *after* the DB
commit succeeds. Between ``commit()`` and ``publish_invalidation()`` there is
a small window in which peer workers still serve the old value from their own
local caches; the cache's TTL (``ttl_seconds`` on ``ConfigCache``) is the
hard upper bound on staleness if the publish itself fails (e.g., Redis is
down — ``publish_invalidation`` swallows + counts the error). For config
data this is intentional: availability > strict coherence.

Plaintext is never cached locally. ``secrets_cache`` exists only so that
writes to a secret emit a cross-worker invalidation signal; the read path
(``_reveal_typed``) always round-trips to the DB + Fernet.
"""

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


class SecretDecryptError(RuntimeError):
    """Raised when Fernet decryption fails — key rotation mismatch or row tampered."""


@dataclass
class SecretMetadata:
    namespace: str
    key: str
    value_type: str
    # created_at/updated_at are always tz-aware (TIMESTAMPTZ columns via asyncpg).
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


def _encode_plaintext(value: Any, value_type: ValueType) -> bytes:
    if value_type == "json":
        return json.dumps(value).encode()
    if value_type == "bool":
        return b"true" if bool(value) else b"false"
    if value_type == "int":
        return str(int(value)).encode()
    return str(value).encode()


def _decode_plaintext(plaintext: bytes, value_type: str) -> Any:
    if value_type == "json":
        return json.loads(plaintext.decode())
    if value_type == "int":
        return int(plaintext.decode())
    if value_type == "bool":
        return plaintext.decode() == "true"
    return plaintext.decode()


def _pack_config_row(value: Any, value_type: ValueType) -> tuple[str | None, Any]:
    """Return (``value`` column, ``value_json`` column) honoring the
    ``app_config_value_exclusive`` CHECK constraint. Non-json rows bind SQL
    NULL (not JSONB null) to ``value_json``."""
    if value_type == "json":
        return None, value
    if value_type == "bool":
        return ("true" if bool(value) else "false", null())
    if value_type == "int":
        return str(int(value)), null()
    return str(value), null()


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
        # Capture the MultiFernet primary at construction so the reveal path
        # does not reach into library internals on every call. A library
        # upgrade that renames ``_fernets`` now fails loudly at startup
        # instead of silently skipping the PREV-hit metric.
        self._primary_fernet: Fernet | None = (
            fernet._fernets[0] if isinstance(fernet, MultiFernet) else None
        )

    async def _fetch_row(self, ns: str, key: str) -> tuple[Any, str] | None:
        """Fetch a config row from the DB, coerce, cache, and return
        ``(materialized_value, value_type)``. Returns ``None`` if absent.
        Callers apply type-guard / default logic on top."""
        async with self._session_factory() as s:
            stmt = select(AppConfig.value, AppConfig.value_json, AppConfig.value_type).where(
                AppConfig.namespace == ns, AppConfig.key == key
            )
            row = (await s.execute(stmt)).one_or_none()
        if row is None:
            return None
        materialized = _coerce_from_stored(row.value, row.value_json, row.value_type)
        self._cache.set((ns, key), (materialized, row.value_type))
        return materialized, row.value_type

    async def get(self, ns: str, key: str, default: Any = None) -> Any:
        cached = self._cache.get((ns, key))
        if cached is not None:
            metrics.config_ops_total.labels(op="get", kind="config", result="hit").inc()
            return cached[0]
        fetched = await self._fetch_row(ns, key)
        if fetched is None:
            metrics.config_ops_total.labels(op="get", kind="config", result="miss").inc()
            return default
        metrics.config_ops_total.labels(op="get", kind="config", result="ok").inc()
        return fetched[0]

    async def get_int(self, ns: str, key: str, default: int | None = None) -> int | None:
        return cast(int | None, await self._get_typed(ns, key, "int", default))

    async def get_bool(self, ns: str, key: str, default: bool | None = None) -> bool | None:
        return cast(bool | None, await self._get_typed(ns, key, "bool", default))

    async def get_json(self, ns: str, key: str, default: Any = None) -> Any:
        return await self._get_typed(ns, key, "json", default)

    async def _get_typed(self, ns: str, key: str, expected: ValueType, default: Any) -> Any:
        cached = self._cache.get((ns, key))
        if cached is not None:
            value, stored_type = cached
            if stored_type != expected:
                raise ConfigTypeError(
                    f"{ns}.{key} has value_type={stored_type!r}, accessor expected {expected!r}"
                )
            return value
        fetched = await self._fetch_row(ns, key)
        if fetched is None:
            return default
        materialized, stored_type = fetched
        if stored_type != expected:
            raise ConfigTypeError(
                f"{ns}.{key} has value_type={stored_type!r}, accessor expected {expected!r}"
            )
        return materialized

    async def set(self, ns: str, key: str, value: Any, value_type: ValueType = "str") -> AppConfig:
        if "|" in ns or "|" in key:
            raise ValueError("namespace/key cannot contain '|'")
        if value_type not in ("str", "int", "bool", "json"):
            raise ValueError(f"invalid value_type={value_type!r}")
        row_value, row_value_json = _pack_config_row(value, value_type)

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
                delete(AppConfig)
                .where(AppConfig.namespace == ns, AppConfig.key == key)
                .returning(AppConfig.namespace)
            )
            existed = result.first() is not None
            await s.commit()
        self._cache.pop((ns, key))
        await self._cache.publish_invalidation(ns, key)
        metrics.config_ops_total.labels(op="delete", kind="config", result="ok").inc()
        return existed

    async def get_exact(self, ns: str, key: str) -> AppConfig | None:
        """Single-row point lookup by (namespace, key). Avoids N+1 vs cfg.list()."""
        async with self._session_factory() as s:
            stmt = select(AppConfig).where(AppConfig.namespace == ns, AppConfig.key == key)
            return (await s.execute(stmt)).scalar_one_or_none()

    async def list(self, namespace: str | None = None) -> builtins.list[AppConfig]:
        async with self._session_factory() as s:
            stmt = select(AppConfig)
            if namespace is not None:
                stmt = stmt.where(AppConfig.namespace == namespace)
            stmt = stmt.order_by(AppConfig.namespace, AppConfig.key)
            rows = (await s.execute(stmt)).scalars().all()
        metrics.config_ops_total.labels(op="list", kind="config", result="ok").inc()
        return list(rows)

    async def set_secret(
        self, ns: str, key: str, value: Any, value_type: ValueType = "str"
    ) -> AppSecret:
        if "|" in ns or "|" in key:
            raise ValueError("namespace/key cannot contain '|'")
        if value_type not in ("str", "int", "bool", "json"):
            raise ValueError(f"invalid value_type={value_type!r}")
        plaintext = _encode_plaintext(value, value_type)
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

    async def _reveal_typed(
        self, ns: str, key: str, expected: ValueType | None, default: Any
    ) -> Any:
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
        except InvalidToken as exc:
            log.error(
                "fernet_decrypt_failed ns=%s key=%s (APP_SECRET_KEY rotated or row tampered)",
                ns,
                key,
            )
            raise SecretDecryptError(
                f"fernet decryption failed for {ns}.{key}; "
                "verify APP_SECRET_KEY/PREV_KEY rotation state"
            ) from exc
        # TODO(phase2-retro): replace double-decrypt with key-index check (timing oracle).
        if self._primary_fernet is not None:
            try:
                self._primary_fernet.decrypt(row.value_encrypted)
            except InvalidToken:
                metrics.fernet_prev_key_hits_total.inc()
                log.info("fernet_prev_key_hit ns=%s key=%s", ns, key)
        return _decode_plaintext(plaintext, row.value_type)

    async def delete_secret(self, ns: str, key: str) -> bool:
        async with self._session_factory() as s:
            result = await s.execute(
                delete(AppSecret)
                .where(AppSecret.namespace == ns, AppSecret.key == key)
                .returning(AppSecret.namespace)
            )
            existed = result.first() is not None
            await s.commit()
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
