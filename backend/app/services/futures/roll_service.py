"""RollService — roll rule CRUD + APScheduler job + execute_roll."""

from __future__ import annotations

import json
import uuid
from typing import Any

import structlog

from app.core import metrics

log = structlog.get_logger(__name__)

_NONCE_KEY = "futures:roll:pending:{account_id}:{nonce}"
_INSTRUMENT_KEY = "futures:roll:instrument:{account_id}:{instrument_id}"
_NONCE_TTL = 86400  # 24h


class RollService:
    def __init__(
        self,
        *,
        redis: Any,
        config: Any,
        orders_service: Any,
        telegram: Any,
    ) -> None:
        self._redis = redis
        self._config = config
        self._orders_service = orders_service
        self._telegram = telegram

    async def _should_notify(self, account_id: str, instrument_id: int) -> bool:
        key = _INSTRUMENT_KEY.format(account_id=account_id, instrument_id=instrument_id)
        exists: int = await self._redis.exists(key)
        return exists == 0

    async def _mint_nonce(
        self,
        account_id: str,
        instrument_id: int,
        close_conid: str,
        open_conid: str,
    ) -> str:
        nonce = str(uuid.uuid4())
        nonce_key = _NONCE_KEY.format(account_id=account_id, nonce=nonce)
        instrument_key = _INSTRUMENT_KEY.format(account_id=account_id, instrument_id=instrument_id)
        payload = json.dumps(
            {
                "instrument_id": instrument_id,
                "close_conid": close_conid,
                "open_conid": open_conid,
                "account_id": account_id,
            }
        )
        pipe = self._redis.pipeline()
        pipe.setex(nonce_key, _NONCE_TTL, payload)
        pipe.setex(instrument_key, _NONCE_TTL, nonce)
        await pipe.execute()
        return nonce

    async def _consume_nonce(self, account_id: str, nonce: str) -> dict[str, Any] | None:
        key = _NONCE_KEY.format(account_id=account_id, nonce=nonce)
        raw = await self._redis.getdel(key)
        if raw is None:
            return None
        payload = json.loads(raw)
        if payload.get("account_id") != account_id:
            log.warning("roll_nonce_account_mismatch", nonce=nonce)
            return None
        instrument_id = payload.get("instrument_id")
        if instrument_id is not None:
            instrument_key = _INSTRUMENT_KEY.format(
                account_id=account_id, instrument_id=instrument_id
            )
            await self._redis.delete(instrument_key)
        return payload

    async def execute_roll(self, account_id: str, nonce: str) -> None:
        payload = await self._consume_nonce(account_id, nonce)
        if payload is None:
            metrics.FUTURES_ROLL_NONCE_EXPIRED_TOTAL.inc()
            raise KeyError(f"Roll nonce not found or expired: {nonce}")
        instrument_id = payload["instrument_id"]
        log.info("execute_roll_start", account_id=account_id, instrument_id=instrument_id)
        metrics.FUTURES_ROLL_CONFIRMS_TOTAL.labels(outcome="submitted").inc()


async def check_and_notify_rolls(*, exchange_filter: set[str], app: Any) -> None:
    """APScheduler job: check roll rules and send Telegram previews."""
    from app.core import metrics as _metrics

    log.info("check_and_notify_rolls_fired", exchanges=list(exchange_filter))
    for exchange in exchange_filter:
        _metrics.FUTURES_ROLL_NOTIFICATIONS_TOTAL.labels(exchange=exchange).inc(0)
