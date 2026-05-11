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
import uuid
from collections.abc import AsyncGenerator, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SessionFactory = Callable[[], Any]


class AccountKillSwitchService:
    """get + toggle account_kill_switches rows."""

    def __init__(
        self,
        *,
        db: AsyncSession | None = None,
        db_factory: _SessionFactory | None = None,
    ) -> None:
        if db is None and db_factory is None:
            raise ValueError("AccountKillSwitchService requires either db or db_factory")
        self._db = db
        self._db_factory = db_factory

    @contextlib.asynccontextmanager
    async def _session(self) -> AsyncGenerator[AsyncSession]:
        if self._db is not None:
            yield self._db
        else:
            assert self._db_factory is not None
            async with self._db_factory() as session:
                yield session

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
        return row
