"""Phase 10a D8 — AccountKillSwitch get/toggle service.

Account-level kill switches are higher-stakes than risk limits (a flip
immediately blocks every order on the account), so we don't cache the
read; every gate evaluation queries the row directly through this
service. The BEFORE UPDATE trigger fn_account_kill_switches_history
mirrors the audit story to risk_limits_history.

The toggle helper enforces the spec's "reason required when enabling"
invariant at the service boundary too (Pydantic already validates it on
the request side), so a stale caller can't slip an empty reason past.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import AsyncGenerator, Callable
from typing import Any, Protocol

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)

ACCOUNT_KILL_SWITCH_INVALIDATION_CHANNEL = "app_config:invalidate:kill_switch"

_SessionFactory = Callable[[], Any]


class RedisLike(Protocol):
    async def publish(self, channel: str, message: bytes | str) -> int: ...


class AccountKillSwitchService:
    """get + toggle account_kill_switches rows."""

    def __init__(
        self,
        *,
        db: AsyncSession | None = None,
        db_factory: _SessionFactory | None = None,
        redis: RedisLike | None = None,
    ) -> None:
        if db is None and db_factory is None:
            raise ValueError("AccountKillSwitchService requires either db or db_factory")
        self._db = db
        self._db_factory = db_factory
        self._redis = redis

    @contextlib.asynccontextmanager
    async def _session(self) -> AsyncGenerator[AsyncSession]:
        if self._db is not None:
            yield self._db
        else:
            if self._db_factory is None:
                raise RuntimeError("AccountKillSwitchService: neither db nor db_factory available")
            async with self._db_factory() as session:
                yield session

    async def _publish_invalidation(self, account_id: uuid.UUID) -> None:
        """D9-fix (spec §4 'kill-switch toggle propagates after pubsub').

        Optional: redis arg is wired through the DI graph but tolerates
        absence (legacy callers, unit tests). Best-effort publish; an
        outage here doesn't fail the toggle.
        """
        if self._redis is None:
            return
        try:
            await self._redis.publish(
                ACCOUNT_KILL_SWITCH_INVALIDATION_CHANNEL,
                json.dumps({"account_id": str(account_id)}).encode(),
            )
        except (ConnectionError, OSError, TimeoutError) as exc:
            log.warning("kill_switch.invalidation_publish_failed", err=str(exc))

    async def get(self, account_id: uuid.UUID) -> dict[str, Any] | None:
        """Return the kill-switch row or None if no row exists (= switch off)."""
        async with self._session() as db:
            result = await db.execute(
                text(
                    """
                    SELECT account_id, is_enabled, reason, enabled_at,
                           enabled_by, updated_at
                      FROM account_kill_switches
                     WHERE account_id = :aid
                    """
                ),
                {"aid": account_id},
            )
            row = result.mappings().one_or_none()
        return dict(row) if row is not None else None

    async def toggle(
        self,
        account_id: uuid.UUID,
        *,
        is_enabled: bool,
        reason: str,
        by: str,
    ) -> dict[str, Any]:
        """UPSERT the kill switch row + return the resulting state.

        When enabling, set enabled_at = now() and enabled_by = by. When
        disabling, clear them (the DB CHECK constraint requires both
        non-NULL only while is_enabled=TRUE). Reason is required when
        enabling — guard duplicated here so a stale FE that bypasses the
        Pydantic validator can't slip an empty reason past.
        """
        if is_enabled and not reason.strip():
            raise ValueError("reason is required when enabling the kill switch")

        async with self._session() as db:
            result = await db.execute(
                text(
                    """
                    INSERT INTO account_kill_switches
                        (account_id, is_enabled, reason, enabled_at, enabled_by)
                    VALUES
                        (:aid, :enabled, :reason,
                         CASE WHEN :enabled THEN now() ELSE NULL END,
                         CASE WHEN :enabled THEN :by ELSE NULL END)
                    ON CONFLICT (account_id) DO UPDATE
                        SET is_enabled = EXCLUDED.is_enabled,
                            reason = EXCLUDED.reason,
                            enabled_at = EXCLUDED.enabled_at,
                            enabled_by = EXCLUDED.enabled_by,
                            updated_at = now()
                    RETURNING account_id, is_enabled, reason, enabled_at,
                              enabled_by, updated_at
                    """
                ),
                {"aid": account_id, "enabled": is_enabled, "reason": reason, "by": by},
            )
            row = dict(result.mappings().one())
            await db.commit()
        await self._publish_invalidation(account_id)
        return row
