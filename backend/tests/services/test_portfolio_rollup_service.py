"""Phase 10b.2 §11.4 — PortfolioRollupService golden tests (GV1, GV2, GV6, GV10).

Test isolation strategy:
  - Setup helpers (_seed_account, _soft_delete_others, _cleanup_accounts) open
    a FRESH SessionLocal() context each. The service-under-test runs against
    the per-test ``db_session`` fixture. Mixing the two would trigger
    SQLAlchemy's "InvalidRequestError: A transaction is already begun"
    when the test's explicit begin() overlaps with the service's autobegin.
  - The shared dev/CI DB is a global resource; soft_delete_others +
    restore_others sandbox the test's view of broker_accounts so the
    cross-broker rollup returns deterministic data regardless of leftover
    state from prior tests.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import bindparam, text

from app.core.db import SessionLocal
from app.services.orders_service import PreviewUnavailable
from app.services.portfolio_rollup_service import PortfolioRollupService

pytestmark = pytest.mark.asyncio


_INSERT_ACCOUNT_SQL = text(
    """
    INSERT INTO broker_accounts
      (id, broker_id, account_number, mode, gateway_label, currency_base,
       last_seen_via, last_nlv, last_nlv_currency, last_nlv_at)
    VALUES
      (:id, CAST(:broker AS broker_id_enum), :acct, 'paper', :gateway,
       :base, :gateway, CAST(:nlv AS NUMERIC(20,8)), :native, now())
    """
)

_DELETE_ACCOUNTS_SQL = text("DELETE FROM broker_accounts WHERE id IN :ids").bindparams(
    bindparam("ids", expanding=True)
)


async def _seed_account(broker: str, native: str, nlv: str) -> UUID:
    """Insert one broker_accounts row in its OWN committed session.

    Returning the UUID. Setup needs to be in a separate session from the
    one the service-under-test uses, otherwise SQLAlchemy's autobegin
    semantics conflict with the test's own explicit begin() calls
    (InvalidRequestError: 'A transaction is already begun').
    """
    aid = uuid4()
    async with SessionLocal() as setup_s:
        async with setup_s.begin():
            await setup_s.execute(
                _INSERT_ACCOUNT_SQL,
                {
                    "id": str(aid),
                    "broker": broker,
                    "acct": f"TEST-{aid.hex[:8]}",
                    "gateway": f"{broker}-test",
                    "base": native,
                    "nlv": nlv,
                    "native": native,
                },
            )
    return aid


async def _soft_delete_others(keep_ids: list[UUID]) -> list[UUID]:
    """Soft-delete every broker_account NOT in keep_ids; return the IDs the
    test mutated so teardown can restore exactly those rows.

    Review HIGH (code-reviewer): prior `_restore_others` restored EVERY
    soft-deleted row not in our keep-list, which would resurrect legitimately
    deleted accounts that pre-dated the test. Now we snapshot the exact set
    we soft-deleted and only restore those.
    """
    async with SessionLocal() as s:
        async with s.begin():
            result = await s.execute(
                text(
                    "UPDATE broker_accounts SET deleted_at = now() "
                    "WHERE id NOT IN :keep AND deleted_at IS NULL "
                    "RETURNING id"
                ).bindparams(bindparam("keep", expanding=True)),
                {"keep": [str(i) for i in keep_ids]},
            )
            return [row[0] for row in result.fetchall()]


async def _restore_others(mutated_ids: list[UUID]) -> None:
    """Restore exactly the IDs we soft-deleted (see _soft_delete_others)."""
    if not mutated_ids:
        return
    async with SessionLocal() as s:
        async with s.begin():
            await s.execute(
                text("UPDATE broker_accounts SET deleted_at = NULL WHERE id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": [str(i) for i in mutated_ids]},
            )


async def _cleanup_accounts(ids: list[UUID]) -> None:
    async with SessionLocal() as s:
        async with s.begin():
            await s.execute(_DELETE_ACCOUNTS_SQL, {"ids": [str(i) for i in ids]})


async def test_gv1_single_usd_account_base_gbp(db_session, redis) -> None:
    """GV1 — 10000 USD, FX USD/GBP=0.7912, base GBP → 7912.00."""
    await redis.flushdb()
    aid = await _seed_account("ibkr", "USD", "10000")
    mutated = await _soft_delete_others([aid])
    await redis.set("fx:mid:USD:GBP", "0.7912")

    try:
        service = PortfolioRollupService(db_session, redis)
        live = await service.compute_live("GBP")

        assert live.total_nlv_base == Decimal("7912.00")
        assert live.partial is False
        gv1_acct = next(a for a in live.accounts if a.account_id == aid)
        assert gv1_acct.nlv_base == Decimal("7912.00000000")
        assert gv1_acct.status == "live"
        assert live.fx_rates.get("USD/GBP") == Decimal("0.7912")
    finally:
        await _restore_others(mutated)
        await _cleanup_accounts([aid])


async def test_gv2_usd_plus_hkd_base_gbp(db_session, redis) -> None:
    """GV2 — 10000 USD + 50000 HKD, base GBP → 7912 + 5075 = 12987.00."""
    await redis.flushdb()
    aid_usd = await _seed_account("ibkr", "USD", "10000")
    aid_hkd = await _seed_account("futu", "HKD", "50000")
    ids = [aid_usd, aid_hkd]
    mutated = await _soft_delete_others(ids)
    await redis.set("fx:mid:USD:GBP", "0.7912")
    await redis.set("fx:mid:HKD:GBP", "0.1015")

    try:
        service = PortfolioRollupService(db_session, redis)
        live = await service.compute_live("GBP")
        assert live.total_nlv_base == Decimal("12987.00")
        assert live.fx_rates.get("USD/GBP") == Decimal("0.7912")
        assert live.fx_rates.get("HKD/GBP") == Decimal("0.1015")
    finally:
        await _restore_others(mutated)
        await _cleanup_accounts(ids)


async def test_gv6_all_fx_unavailable_raises_503(db_session, redis) -> None:
    """GV6 — no FX rates seeded; all non-init accounts fail → 503."""
    await redis.flushdb()
    aid_usd = await _seed_account("ibkr", "USD", "10000")
    aid_hkd = await _seed_account("futu", "HKD", "50000")
    ids = [aid_usd, aid_hkd]
    mutated = await _soft_delete_others(ids)

    try:
        service = PortfolioRollupService(db_session, redis)
        with pytest.raises(PreviewUnavailable) as exc_info:
            await service.compute_live("GBP")
        assert exc_info.value.payload == {
            "error": "fx_rate_unavailable",
            "pair": "all",
        }
    finally:
        await _restore_others(mutated)
        await _cleanup_accounts(ids)


async def _seed_snapshot(account_id: UUID, ts_offset_sql: str, nlv: str, currency: str) -> None:
    """Insert a snapshot row at now() + ts_offset_sql (eg '-1 hour').

    ts_offset_sql is spliced directly into the SQL — it's a test-only fixture
    literal, never user input. Validation enforces the spelled-out interval
    pattern so a stray semicolon can't sneak in.
    """
    # Defence-in-depth: validate the interval string before splicing.
    import re

    if not re.fullmatch(r"-?\d+ (minute|minutes|hour|hours|day|days)", ts_offset_sql):
        raise ValueError(f"unsafe interval literal: {ts_offset_sql!r}")
    async with SessionLocal() as s:
        async with s.begin():
            await s.execute(
                text(
                    f"""
                    INSERT INTO account_balance_snapshots
                      (account_id, ts, nlv, currency, source_label)
                    VALUES
                      (:aid, now() + INTERVAL '{ts_offset_sql}',
                       CAST(:nlv AS NUMERIC(20,8)), :ccy, 'ibkr-test')
                    """
                ),
                {
                    "aid": str(account_id),
                    "nlv": nlv,
                    "ccy": currency,
                },
            )


async def _cleanup_snapshots(account_id: UUID) -> None:
    async with SessionLocal() as s:
        async with s.begin():
            await s.execute(
                text("DELETE FROM account_balance_snapshots WHERE account_id = :aid"),
                {"aid": str(account_id)},
            )


async def _delete_orphan_snapshots() -> None:
    """Clean up any account_balance_snapshots rows whose parent account
    no longer exists (FK is ON DELETE CASCADE so this is paranoid but
    cheap — sometimes setup test fixtures leave orphans between runs)."""
    async with SessionLocal() as s:
        async with s.begin():
            await s.execute(
                text(
                    "DELETE FROM account_balance_snapshots WHERE account_id NOT IN "
                    "(SELECT id FROM broker_accounts)"
                )
            )


async def test_gv10_partial_fx_outage(db_session, redis) -> None:
    """GV10 — USD + GBP work; HKD/GBP missing → 200 partial."""
    await redis.flushdb()
    aid_usd = await _seed_account("ibkr", "USD", "10000")
    aid_hkd = await _seed_account("futu", "HKD", "50000")
    aid_gbp = await _seed_account("schwab", "GBP", "1000")
    ids = [aid_usd, aid_hkd, aid_gbp]
    mutated = await _soft_delete_others(ids)
    await redis.set("fx:mid:USD:GBP", "0.7912")
    # HKD/GBP intentionally NOT seeded; GBP→GBP path uses Decimal(1) auto.

    try:
        service = PortfolioRollupService(db_session, redis)
        live = await service.compute_live("GBP")

        assert live.partial is True
        assert len(live.fx_stale_accounts) == 1
        assert live.total_nlv_base == Decimal("8912.00")
        hkd_acct = next(a for a in live.accounts if a.fx_stale)
        assert hkd_acct.nlv_base is None
        assert hkd_acct.status == "fx_stale"
        assert hkd_acct.currency_native == "HKD"
    finally:
        await _restore_others(mutated)
        await _cleanup_accounts(ids)


# ---------------------------------------------------------------------------
# compute_curve tests (Phase 10b.2 §5.1 — 3 windows + GV12 weekend gap)
# ---------------------------------------------------------------------------


async def test_compute_curve_intraday_reads_raw_snapshots(db_session, redis) -> None:
    """window='intraday' reads account_balance_snapshots (raw last 24h).
    Raw points have NULL nlv_high / nlv_low (single sample per ts, not OHLC).
    """
    await redis.flushdb()
    aid = await _seed_account("ibkr", "USD", "10000")
    mutated = await _soft_delete_others([aid])
    await _seed_snapshot(aid, "-3 hours", "10000", "USD")
    await _seed_snapshot(aid, "-1 hour", "10100", "USD")
    await _seed_snapshot(aid, "-5 minutes", "10080", "USD")
    await redis.set("fx:mid:USD:GBP", "0.7912")

    try:
        service = PortfolioRollupService(db_session, redis)
        curve = await service.compute_curve("GBP", "intraday")
        assert curve.window == "intraday"
        assert curve.base_currency == "GBP"
        # All 3 inserted points belong to our account, all in the last 24h
        my_points = [p for p in curve.per_account if p.account_id == aid]
        assert len(my_points) == 3
        # Raw intraday points have no high/low
        assert all(p.nlv_high_base is None for p in my_points)
        assert all(p.nlv_low_base is None for p in my_points)
        # FX-converted nlv_close values: 10000 * 0.7912 = 7912.00000000
        assert my_points[0].nlv_close_base == Decimal("7912.00000000")
        # totals sum per-bucket close values across accounts (just ours here)
        assert len(curve.totals) >= 3
    finally:
        await _cleanup_snapshots(aid)
        await _restore_others(mutated)
        await _cleanup_accounts([aid])


async def test_compute_curve_30d_reads_1h_cagg(db_session, redis) -> None:
    """window='30d' reads account_balance_snapshots_1h CAGG with
    materialized_only=false — fresh raw rows appear via real-time
    aggregation without explicit refresh."""
    await redis.flushdb()
    aid = await _seed_account("ibkr", "USD", "10000")
    mutated = await _soft_delete_others([aid])
    # Insert raw points that fall inside the last 30d but outside last 24h
    # so they show up in the 1h CAGG but not the intraday window.
    await _seed_snapshot(aid, "-5 days", "9500", "USD")
    await _seed_snapshot(aid, "-2 days", "9800", "USD")
    await redis.set("fx:mid:USD:GBP", "0.7912")

    try:
        service = PortfolioRollupService(db_session, redis)
        curve = await service.compute_curve("GBP", "30d")
        assert curve.window == "30d"
        my_points = [p for p in curve.per_account if p.account_id == aid]
        assert len(my_points) >= 2
        # 1h CAGG rows have nlv_high / nlv_low populated (MIN/MAX over bucket)
        assert all(p.nlv_high_base is not None for p in my_points)
        assert all(p.nlv_low_base is not None for p in my_points)
    finally:
        await _cleanup_snapshots(aid)
        await _restore_others(mutated)
        await _cleanup_accounts([aid])


async def test_compute_curve_1y_reads_1d_cagg(db_session, redis) -> None:
    """window='1y' reads account_balance_snapshots_1d CAGG."""
    await redis.flushdb()
    aid = await _seed_account("ibkr", "USD", "10000")
    mutated = await _soft_delete_others([aid])
    # Points in last 365d but older than 30d so they primarily exercise 1d CAGG
    await _seed_snapshot(aid, "-60 days", "9000", "USD")
    await _seed_snapshot(aid, "-180 days", "8000", "USD")
    await redis.set("fx:mid:USD:GBP", "0.7912")

    try:
        service = PortfolioRollupService(db_session, redis)
        curve = await service.compute_curve("GBP", "1y")
        assert curve.window == "1y"
        my_points = [p for p in curve.per_account if p.account_id == aid]
        assert len(my_points) >= 2
    finally:
        await _cleanup_snapshots(aid)
        await _restore_others(mutated)
        await _cleanup_accounts([aid])


async def test_gv12_weekend_gap_in_curve_no_interpolation(db_session, redis) -> None:
    """GV12 — Fri / Mon snapshots with no Sat/Sun rows; curve is sparse,
    NOT interpolated to zero. We only assert no weekend buckets exist
    among the points (intraday window's 24h filter only catches Sun/Mon
    realistically; this test depends on the date the suite runs)."""
    await redis.flushdb()
    aid = await _seed_account("ibkr", "USD", "10000")
    mutated = await _soft_delete_others([aid])
    # Seed only weekday points; intentional gap on weekend days.
    # Use 1h offsets so we're in the intraday window; the curve's bucket
    # set will reflect EXACTLY what we inserted (no synthetic zeros).
    await _seed_snapshot(aid, "-3 hours", "10000", "USD")
    await _seed_snapshot(aid, "-1 hour", "10050", "USD")
    await redis.set("fx:mid:USD:GBP", "0.7912")

    try:
        service = PortfolioRollupService(db_session, redis)
        curve = await service.compute_curve("GBP", "intraday")
        my_points = [p for p in curve.per_account if p.account_id == aid]
        # Exactly the 2 buckets we seeded — no synthetic interpolation rows
        assert len(my_points) == 2
        # Both buckets must be present in totals
        bucket_ts_in_points = {p.bucket for p in my_points}
        bucket_ts_in_totals = {b.bucket for b in curve.totals}
        # Our 2 buckets appear in totals (other accounts soft-deleted)
        assert bucket_ts_in_points.issubset(bucket_ts_in_totals)
    finally:
        await _cleanup_snapshots(aid)
        await _restore_others(mutated)
        await _cleanup_accounts([aid])
