"""ExerciseService — exercise elections with idempotency and rate limiting."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)

_RATE_LIMIT_MAX = 5
_RATE_LIMIT_WINDOW = 60.0


class DuplicateElectionError(Exception):
    """Raised when a new idempotency_key conflicts with an existing same-day election."""


class ExerciseRateLimitError(Exception):
    """Raised when the user exceeds the 5/min exercise rate limit."""


class ExerciseService:
    def __init__(self, *, db: AsyncSession, redis: Any, broker_registry: Any) -> None:
        self._db = db
        self._redis = redis
        self._broker_registry = broker_registry
        self._rate_buckets: dict[str, deque[float]] = defaultdict(deque)

    def _check_rate_limit(self, jwt_subject: str) -> None:
        now = time.monotonic()
        bucket = self._rate_buckets[jwt_subject]
        while bucket and now - bucket[0] > _RATE_LIMIT_WINDOW:
            bucket.popleft()
        if len(bucket) >= _RATE_LIMIT_MAX:
            raise ExerciseRateLimitError(f"Exercise rate limit exceeded for {jwt_subject}")
        bucket.append(now)

    async def _find_by_idempotency_key(self, ikey: uuid.UUID) -> dict[str, Any] | None:
        result = await self._db.execute(
            text(
                "SELECT id, idempotency_key, status, broker_ref "
                "FROM exercise_elections WHERE idempotency_key = :ikey"
            ),
            {"ikey": str(ikey)},
        )
        row = result.fetchone()
        if row is None:
            return None
        return {
            "id": str(row[0]),
            "idempotency_key": str(row[1]),
            "status": row[2],
            "broker_ref": row[3],
        }

    async def _insert_election(
        self,
        *,
        account_id: uuid.UUID,
        jwt_subject: str,
        instrument_id: int,
        action: str,
        qty: Decimal,
        idempotency_key: uuid.UUID,
    ) -> dict[str, Any]:
        election_id = uuid.uuid4()
        now = datetime.now(UTC)
        try:
            await self._db.execute(
                text(
                    """
                    INSERT INTO exercise_elections
                        (id, idempotency_key, jwt_subject, account_id,
                         instrument_id, action, qty, status, created_at)
                    VALUES
                        (:id, :ikey, :subject, :acct,
                         :inst, :action, :qty, 'submitted', :now)
                    """
                ),
                {
                    "id": str(election_id),
                    "ikey": str(idempotency_key),
                    "subject": jwt_subject,
                    "acct": str(account_id),
                    "inst": instrument_id,
                    "action": action,
                    "qty": qty,
                    "now": now,
                },
            )
            await self._db.commit()
        except Exception as exc:
            msg = str(exc).lower()
            if "exercise_elections_one_per_day" in msg or "unique" in msg:
                raise DuplicateElectionError(
                    "Election already submitted today for this contract"
                ) from exc
            raise
        return {
            "id": str(election_id),
            "idempotency_key": str(idempotency_key),
            "status": "submitted",
        }

    async def _submit_to_broker(
        self,
        *,
        account_id: uuid.UUID,
        instrument_id: int,
        action: str,
        qty: Decimal,
        idempotency_key: uuid.UUID,
    ) -> dict[str, Any]:
        # Broker dispatch (IBKR exerciseOptions) — wired in sidecar extension task (Chunk F)
        log.info("exercise_submitted_to_broker", account_id=str(account_id), action=action)
        return {"broker_ref": None, "success": True}

    async def elect(
        self,
        account_id: uuid.UUID,
        jwt_subject: str,
        instrument_id: int,
        action: Literal["EXERCISE", "DO_NOT_EXERCISE", "LAPSE"],
        qty: Decimal,
        csrf_nonce: str,
        idempotency_key: uuid.UUID,
    ) -> dict[str, Any]:
        """Submit an exercise election. Idempotent on same idempotency_key."""
        existing = await self._find_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing

        self._check_rate_limit(jwt_subject)

        record = await self._insert_election(
            account_id=account_id,
            jwt_subject=jwt_subject,
            instrument_id=instrument_id,
            action=action,
            qty=qty,
            idempotency_key=idempotency_key,
        )

        broker_result = await self._submit_to_broker(
            account_id=account_id,
            instrument_id=instrument_id,
            action=action,
            qty=qty,
            idempotency_key=idempotency_key,
        )
        log.info("exercise_elected", action=action, broker_ref=broker_result.get("broker_ref"))
        return record

    async def list_pending(
        self,
        account_id: uuid.UUID,
        jwt_subject: str,
    ) -> list[dict[str, Any]]:
        """Return option positions expiring within the next 5 trading sessions."""
        result = await self._db.execute(
            text(
                """
                SELECT p.instrument_id, p.qty,
                       i.meta->>'expiry' AS expiry,
                       i.meta->>'strike' AS strike,
                       i.meta->>'put_call' AS put_call,
                       i.meta->>'multiplier' AS multiplier,
                       i.primary_exchange
                FROM positions p
                JOIN instruments i ON i.id = p.instrument_id
                WHERE p.account_id = :acct
                  AND i.asset_class = 'OPTION'
                  AND p.qty != 0
                  AND (i.meta->>'expiry')::date <= (CURRENT_DATE + INTERVAL '5 days')
                """
            ),
            {"acct": str(account_id)},
        )
        rows = result.fetchall()
        return [
            {
                "instrument_id": row[0],
                "qty": str(row[1]),
                "expiry": row[2],
                "strike": row[3],
                "put_call": row[4],
                "multiplier": row[5],
                "exchange": row[6],
                "spot_unavailable": True,
            }
            for row in rows
        ]
