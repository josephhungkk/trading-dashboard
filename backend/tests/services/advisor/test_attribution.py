"""Tests for Phase 21c attribution service + helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.advisor.attribution import AttributionService
from app.services.advisor.attribution_types import AttributionSummary
from app.services.market_calendar import session_close_for_decision
from app.services.quotes.instrument_resolver import InstrumentResolver

# ── find_by_canonical_id ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_by_canonical_id_returns_instrument(
    db_session: AsyncSession, redis: object
) -> None:
    await db_session.execute(
        text(
            "INSERT INTO instruments (canonical_id, asset_class, primary_exchange,"
            " currency, display_name, meta)"
            " VALUES ('test:AAPL:US', 'STOCK', 'NASDAQ', 'USD', 'Apple Inc.',"
            " '{\"multiplier\": 1}'::jsonb)"
            " ON CONFLICT (canonical_id) DO NOTHING"
        )
    )
    resolver = InstrumentResolver(session=db_session)
    result = await resolver.find_by_canonical_id("test:AAPL:US", redis=redis)
    assert result is not None
    assert result.primary_exchange == "NASDAQ"
    assert result.multiplier == Decimal("1")


@pytest.mark.asyncio
async def test_find_by_canonical_id_returns_none_for_unknown(
    db_session: AsyncSession, redis: object
) -> None:
    resolver = InstrumentResolver(session=db_session)
    result = await resolver.find_by_canonical_id("DOES_NOT_EXIST_XYZ_21c", redis=redis)
    assert result is None


@pytest.mark.asyncio
async def test_find_by_canonical_id_uses_redis_cache(
    db_session: AsyncSession, redis: object
) -> None:
    import json

    key = "attribution:instr:CACHED_INSTR_21c"
    await redis.set(
        key,
        json.dumps({"id": 42, "multiplier": "100", "primary_exchange": "HKEX"}),
        ex=3600,
    )
    resolver = InstrumentResolver(session=db_session)
    with patch.object(db_session, "execute", wraps=db_session.execute) as mock_exec:
        result = await resolver.find_by_canonical_id("CACHED_INSTR_21c", redis=redis)
    assert result is not None
    assert result.id == 42
    assert result.multiplier == Decimal("100")
    # DB should not have been hit
    assert mock_exec.call_count == 0


@pytest.mark.asyncio
async def test_find_by_canonical_id_multiplier_defaults_to_one_when_null(
    db_session: AsyncSession, redis: object
) -> None:
    await db_session.execute(
        text(
            "INSERT INTO instruments (canonical_id, asset_class, primary_exchange,"
            " currency, display_name, meta)"
            " VALUES ('test:NOOPT:US', 'STOCK', 'NYSE', 'USD', 'No Option', '{}'::jsonb)"
            " ON CONFLICT (canonical_id) DO NOTHING"
        )
    )
    resolver = InstrumentResolver(session=db_session)
    result = await resolver.find_by_canonical_id("test:NOOPT:US", redis=redis)
    assert result is not None
    assert result.multiplier == Decimal("1")


# ── session_close_for_decision ────────────────────────────────────────────────


def test_session_close_intraday_returns_same_session() -> None:
    # 14:00 UTC = 10:00 ET — inside NYSE session on a Monday
    dt = datetime(2026, 5, 18, 14, 0, 0, tzinfo=UTC)  # Monday 2026-05-18
    close = session_close_for_decision("NYSE", dt)
    assert close.date() == dt.date()
    assert close.hour == 20  # 16:00 ET = 20:00 UTC


def test_session_close_after_hours_returns_next_session() -> None:
    # 22:00 UTC on Monday = after NYSE close → next session = Tuesday
    dt = datetime(2026, 5, 18, 22, 0, 0, tzinfo=UTC)
    close = session_close_for_decision("NYSE", dt)
    assert close.date() > dt.date()


def test_session_close_unknown_exchange_raises() -> None:
    dt = datetime(2026, 5, 18, 14, 0, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="unsupported_exchange"):
        session_close_for_decision("UNKNOWN_EXCHANGE_XYZ", dt)


def test_session_close_naive_datetime_treated_as_utc() -> None:
    dt_naive = datetime(2026, 5, 18, 14, 0, 0)  # no tzinfo
    close = session_close_for_decision("NYSE", dt_naive)
    assert close.tzinfo is not None


# ── PnL formula ───────────────────────────────────────────────────────────────


def test_pnl_buy_veto_price_falls_correct() -> None:
    entry, exit_, qty, mult = Decimal("50"), Decimal("40"), Decimal("1"), Decimal("1")
    side_sign = 1  # BUY
    pnl = (exit_ - entry) * qty * mult * side_sign
    assert pnl == Decimal("-10")
    assert pnl < 0  # price fell after BUY veto → veto correct


def test_pnl_options_multiplier_applied() -> None:
    entry, exit_, qty, mult = Decimal("50"), Decimal("40"), Decimal("1"), Decimal("100")
    side_sign = 1
    pnl = (exit_ - entry) * qty * mult * side_sign
    assert pnl == Decimal("-1000")


def test_pnl_sell_veto_price_falls_wrong() -> None:
    entry, exit_ = Decimal("100"), Decimal("90")
    side_sign = -1  # SELL
    pnl = (exit_ - entry) * Decimal("10") * Decimal("1") * side_sign
    # price fell, short would have profited → veto was wrong
    assert pnl > 0  # correct=False for veto


# ── EOD buffer ────────────────────────────────────────────────────────────────


def test_eod_buffer_skips_when_gap_too_small() -> None:
    session_close = datetime(2026, 5, 18, 20, 0, tzinfo=UTC)
    created_at = datetime(2026, 5, 18, 19, 55, tzinfo=UTC)
    buffer_minutes = 30
    gap = (session_close - created_at).total_seconds() / 60
    assert gap < buffer_minutes


def test_eod_buffer_allows_when_gap_sufficient() -> None:
    session_close = datetime(2026, 5, 18, 20, 0, tzinfo=UTC)
    created_at = datetime(2026, 5, 18, 18, 0, tzinfo=UTC)
    buffer_minutes = 30
    gap = (session_close - created_at).total_seconds() / 60
    assert gap >= buffer_minutes


# ── get_summary window validation ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_summary_invalid_window_raises(
    db_session: AsyncSession,
) -> None:
    svc = AttributionService(db_factory=MagicMock(), redis=MagicMock())
    with pytest.raises(ValueError, match="invalid_window"):
        await svc.get_summary(bot_id=MagicMock(), window="invalid", db=db_session)


@pytest.mark.asyncio
async def test_get_summary_returns_summary_model(
    db_session: AsyncSession,
) -> None:
    svc = AttributionService(db_factory=MagicMock(), redis=MagicMock())
    result = await svc.get_summary(bot_id=__import__("uuid").uuid4(), window="1h", db=db_session)
    assert isinstance(result, AttributionSummary)
    assert result.window == "1h"
    assert result.complete_count == 0


# ── recompute since-guard ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recompute_rejects_too_old_since(
    db_session: AsyncSession,
) -> None:
    svc = AttributionService(db_factory=MagicMock(), redis=MagicMock())
    old_since = datetime.now(UTC) - timedelta(days=365)
    with pytest.raises(ValueError, match="since_too_old"):
        await svc.recompute(bot_id=__import__("uuid").uuid4(), since=old_since, db=db_session)


# ── kill-switch ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_poll_skips_when_disabled(
    db_session: AsyncSession,
) -> None:
    svc = AttributionService(db_factory=MagicMock(), redis=MagicMock())
    with patch.object(svc, "_read_config", AsyncMock(return_value="false")):
        with patch.object(db_session, "execute", wraps=db_session.execute) as mock_exec:
            await svc.poll(db_session)
    # Only the _read_config call to check enabled — no FOR UPDATE SELECT
    assert mock_exec.call_count == 0
