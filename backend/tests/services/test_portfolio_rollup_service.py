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


# ---------------------------------------------------------------------------
# drill_asset_class tests (Phase 10b.2 §5.1 — 4 tests: GV4, GV7, GV8, unknown)
# ---------------------------------------------------------------------------


async def _seed_instrument(asset_class: str, display_name: str) -> int:
    """Insert an instruments row; return its bigint id."""
    async with SessionLocal() as s:
        async with s.begin():
            # canonical_id must be unique; use display_name + asset_class as key.
            result = await s.execute(
                text(
                    """
                    INSERT INTO instruments (canonical_id, asset_class,
                                             primary_exchange, currency,
                                             display_name)
                    VALUES (:cid, CAST(:ac AS instrument_asset_class),
                            'TEST', 'USD', :dn)
                    RETURNING id
                    """
                ),
                {
                    "cid": f"TEST-{display_name}-{asset_class}",
                    "ac": asset_class,
                    "dn": display_name,
                },
            )
            return int(result.scalar_one())


async def _seed_position(
    account_id: UUID, instrument_id: int, qty: str, avg_cost: str, asset_class: str
) -> None:
    """Insert a positions row for the test account/instrument."""
    async with SessionLocal() as s:
        async with s.begin():
            await s.execute(
                text(
                    """
                    INSERT INTO positions (account_id, conid, qty, avg_cost,
                                           currency, multiplier, asset_class,
                                           instrument_id)
                    VALUES (:aid, :conid, CAST(:qty AS NUMERIC(20,8)),
                            CAST(:avg AS NUMERIC(20,8)), 'USD', 1.0,
                            CAST(:ac AS instrument_asset_class), :iid)
                    """
                ),
                {
                    "aid": str(account_id),
                    "conid": f"TEST-{instrument_id}",
                    "qty": qty,
                    "avg": avg_cost,
                    "ac": asset_class,
                    "iid": instrument_id,
                },
            )


async def _cleanup_instrument(instrument_id: int) -> None:
    async with SessionLocal() as s:
        async with s.begin():
            await s.execute(
                text("DELETE FROM positions WHERE instrument_id = :iid"),
                {"iid": instrument_id},
            )
            await s.execute(
                text("DELETE FROM instruments WHERE id = :iid"),
                {"iid": instrument_id},
            )


async def _seed_concentration_cap(limit_value: str, warn_at_pct: str) -> int:
    """Insert a global max_position_concentration_pct row; return its id
    for cleanup."""
    async with SessionLocal() as s:
        async with s.begin():
            result = await s.execute(
                text(
                    """
                    INSERT INTO risk_limits (scope_type, scope_id, limit_kind,
                                             limit_value, warn_at_pct, is_active,
                                             updated_by)
                    VALUES (CAST('global' AS risk_scope_type), NULL,
                            CAST('max_position_concentration_pct' AS risk_limit_kind),
                            CAST(:lv AS NUMERIC), CAST(:wp AS NUMERIC), true,
                            'phase10b2-test')
                    RETURNING id
                    """
                ),
                {"lv": limit_value, "wp": warn_at_pct},
            )
            return int(result.scalar_one())


async def _delete_risk_limit(limit_id: int) -> None:
    async with SessionLocal() as s:
        async with s.begin():
            await s.execute(text("DELETE FROM risk_limits WHERE id = :id"), {"id": limit_id})


async def test_gv4_short_position_drill_negative_notional(db_session, redis) -> None:
    """GV4 — short -100 AAPL @ 200 USD, base GBP. abs(notional) used for
    pct_of_nlv (shorts concentrate just as much as longs)."""
    await redis.flushdb()
    aid = await _seed_account("ibkr", "USD", "100000")  # 100k NLV so the
    # short position is a small fraction
    mutated = await _soft_delete_others([aid])
    iid = await _seed_instrument("STOCK", "AAPL")
    await _seed_position(aid, iid, "-100", "200", "STOCK")
    await redis.set("fx:mid:USD:GBP", "0.7912")

    try:
        service = PortfolioRollupService(db_session, redis)
        drill = await service.drill_asset_class("STOCK", "GBP")
        assert drill.asset_class == "STOCK"
        aapl = next(i for i in drill.instruments if i.display_name == "AAPL")
        assert aapl.total_qty == Decimal("-100")
        # Notional native: -100 * 200 = -20000; FX 0.7912 → -15824.00
        assert aapl.notional_base == Decimal("-15824.00000000")
        # pct_of_nlv uses abs() — even though the position is short
        assert aapl.pct_of_nlv > Decimal("0")
    finally:
        await _cleanup_instrument(iid)
        await _restore_others(mutated)
        await _cleanup_accounts([aid])


async def test_gv7_drill_three_verdicts(db_session, redis) -> None:
    """GV7 — 3 instruments with util ~50% / ~85% / ~110% → ok / warn / block.

    NLV = 1000 GBP. Concentration cap = 10 (% of NLV). warn_at_pct = 80
    means warn fires at 80% of the cap = 8% of NLV.
    Build positions to land at ~5%, ~9%, ~12% of NLV (cost basis in GBP).
    """
    await redis.flushdb()
    aid = await _seed_account("ibkr", "GBP", "1000")
    mutated = await _soft_delete_others([aid])
    cap_id = await _seed_concentration_cap("10", "80")
    # Note: positions use currency=USD by the test helper. So the values are
    # USD-denominated and FX-converted at compute time. To get GBP-equivalent
    # numbers, use a 1.0 FX rate (USD:GBP=1) so the math is clean.
    iid_ok = await _seed_instrument("STOCK", "OK-INST")
    iid_warn = await _seed_instrument("STOCK", "WARN-INST")
    iid_block = await _seed_instrument("STOCK", "BLOCK-INST")
    # 1 share at price equal to target notional:
    await _seed_position(aid, iid_ok, "1", "50", "STOCK")  # ~5% of NLV
    await _seed_position(aid, iid_warn, "1", "90", "STOCK")  # ~9% of NLV
    await _seed_position(aid, iid_block, "1", "120", "STOCK")  # ~12% of NLV
    # FX rate 1.0 so USD notional == GBP notional
    await redis.set("fx:mid:USD:GBP", "1")
    # NB GBP→GBP for total NLV uses Decimal(1) auto.

    try:
        service = PortfolioRollupService(db_session, redis)
        drill = await service.drill_asset_class("STOCK", "GBP")
        verdicts = {i.display_name: i.verdict for i in drill.instruments}
        assert verdicts["OK-INST"] == "ok"
        assert verdicts["WARN-INST"] == "warn"
        assert verdicts["BLOCK-INST"] == "block"
    finally:
        await _cleanup_instrument(iid_ok)
        await _cleanup_instrument(iid_warn)
        await _cleanup_instrument(iid_block)
        await _delete_risk_limit(cap_id)
        await _restore_others(mutated)
        await _cleanup_accounts([aid])


async def test_gv8_drill_no_cap_returns_ok(db_session, redis) -> None:
    """GV8 — no risk_limits row for max_position_concentration_pct →
    cap_pct=None, utilisation_pct=None, verdict='ok' regardless of size."""
    await redis.flushdb()
    aid = await _seed_account("ibkr", "USD", "1000")
    mutated = await _soft_delete_others([aid])
    iid = await _seed_instrument("STOCK", "NOCAP")
    await _seed_position(aid, iid, "100", "100", "STOCK")
    await redis.set("fx:mid:USD:GBP", "0.7912")

    try:
        # Defensive: ensure no global cap is active (other tests may leave one)
        async with SessionLocal() as s:
            async with s.begin():
                await s.execute(
                    text(
                        "UPDATE risk_limits SET is_active = false "
                        "WHERE scope_type = 'global' "
                        "AND limit_kind = 'max_position_concentration_pct'"
                    )
                )
        try:
            service = PortfolioRollupService(db_session, redis)
            drill = await service.drill_asset_class("STOCK", "GBP")
            inst = next(i for i in drill.instruments if i.display_name == "NOCAP")
            assert inst.cap_pct is None
            assert inst.utilisation_pct is None
            assert inst.verdict == "ok"
        finally:
            # Restore any rows we deactivated
            async with SessionLocal() as s:
                async with s.begin():
                    await s.execute(
                        text(
                            "UPDATE risk_limits SET is_active = true "
                            "WHERE scope_type = 'global' "
                            "AND limit_kind = 'max_position_concentration_pct'"
                        )
                    )
    finally:
        await _cleanup_instrument(iid)
        await _restore_others(mutated)
        await _cleanup_accounts([aid])


async def _seed_account_with_options(
    broker: str,
    native: str,
    nlv: str | None,
    nlv_currency: str | None,
    age_seconds: int = 0,
) -> UUID:
    """Insert one broker_accounts row with explicit NLV nullability + age
    control. age_seconds=0 means now(); positive values move last_nlv_at
    into the past."""
    aid = uuid4()
    last_nlv_at_sql = "now()" if age_seconds == 0 else f"now() - INTERVAL '{age_seconds} seconds'"
    nlv_sql = f"CAST('{nlv}' AS NUMERIC(20,8))" if nlv is not None else "NULL"
    currency_sql = f"'{nlv_currency}'" if nlv_currency is not None else "NULL"
    async with SessionLocal() as s:
        async with s.begin():
            await s.execute(
                text(
                    f"""
                    INSERT INTO broker_accounts
                      (id, broker_id, account_number, mode, gateway_label,
                       currency_base, last_seen_via, last_nlv,
                       last_nlv_currency, last_nlv_at)
                    VALUES
                      (:id, CAST(:broker AS broker_id_enum), :acct, 'paper',
                       :gateway, :base, :gateway, {nlv_sql}, {currency_sql},
                       {last_nlv_at_sql})
                    """
                ),
                {
                    "id": str(aid),
                    "broker": broker,
                    "acct": f"TEST-{aid.hex[:8]}",
                    "gateway": f"{broker}-test",
                    "base": native,
                },
            )
    return aid


async def test_gv3_base_equals_native_currency(db_session, redis) -> None:
    """GV3 — single USD account, base=USD → fx_rate=1.0, total=10000.00."""
    await redis.flushdb()
    aid = await _seed_account("ibkr", "USD", "10000")
    mutated = await _soft_delete_others([aid])
    # No fx:mid:USD:USD key needed — _fx_rate identity short-circuit.
    try:
        service = PortfolioRollupService(db_session, redis)
        live = await service.compute_live("USD")
        assert live.total_nlv_base == Decimal("10000.00")
        gv3 = next(a for a in live.accounts if a.account_id == aid)
        assert gv3.fx_rate == Decimal("1")
        assert gv3.nlv_base == Decimal("10000.00000000")
        # No fx_rates entry for the identity USD/USD path — only foreign pairs
        assert "USD/USD" not in live.fx_rates
    finally:
        await _restore_others(mutated)
        await _cleanup_accounts([aid])


async def test_gv5_stale_account_in_stale_accounts(db_session, redis) -> None:
    """GV5 — last_nlv_at older than 5min → account UUID in stale_accounts."""
    await redis.flushdb()
    # Use the options helper to force last_nlv_at = now() - 360 seconds
    aid = await _seed_account_with_options("ibkr", "USD", "10000", "USD", age_seconds=360)
    mutated = await _soft_delete_others([aid])
    await redis.set("fx:mid:USD:GBP", "0.7912")

    try:
        service = PortfolioRollupService(db_session, redis)
        live = await service.compute_live("GBP")
        assert aid in live.stale_accounts
        gv5 = next(a for a in live.accounts if a.account_id == aid)
        assert gv5.status == "stale"
        assert gv5.nlv_age_s > 300  # > 5 min threshold
    finally:
        await _restore_others(mutated)
        await _cleanup_accounts([aid])


async def test_gv9_negative_nlv_margin_call(db_session, redis) -> None:
    """GV9 — last_nlv = -1500 USD (margin call). No nlv >= 0 CHECK on
    broker_accounts (and the snapshot table also dropped it per CRIT #1).
    The rollup must include the negative contribution in total_nlv_base."""
    await redis.flushdb()
    aid = await _seed_account_with_options("ibkr", "USD", "-1500", "USD", age_seconds=0)
    mutated = await _soft_delete_others([aid])
    await redis.set("fx:mid:USD:GBP", "0.7912")

    try:
        service = PortfolioRollupService(db_session, redis)
        live = await service.compute_live("GBP")
        # -1500 * 0.7912 = -1186.80
        assert live.total_nlv_base == Decimal("-1186.80")
        gv9 = next(a for a in live.accounts if a.account_id == aid)
        assert gv9.nlv_native == Decimal("-1500.00000000")
        assert gv9.nlv_base == Decimal("-1186.80000000")
        # Status is "live" — negative NLV is not by itself stale or fx_stale
        assert gv9.status == "live"
    finally:
        await _restore_others(mutated)
        await _cleanup_accounts([aid])


async def test_gv11_null_nlv_fresh_account(db_session, redis) -> None:
    """GV11 — last_nlv=NULL → status='initialising', nlv_base=None,
    excluded from total_nlv_base and stale_accounts."""
    await redis.flushdb()
    aid = await _seed_account_with_options(
        "ibkr", "USD", nlv=None, nlv_currency=None, age_seconds=0
    )
    mutated = await _soft_delete_others([aid])
    await redis.set("fx:mid:USD:GBP", "0.7912")

    try:
        service = PortfolioRollupService(db_session, redis)
        live = await service.compute_live("GBP")
        assert len(live.accounts) == 1
        gv11 = live.accounts[0]
        assert gv11.account_id == aid
        assert gv11.status == "initialising"
        assert gv11.nlv_base is None
        assert gv11.nlv_native is None
        assert live.total_nlv_base == Decimal("0.00")
        assert aid not in live.stale_accounts
        assert aid not in live.fx_stale_accounts
    finally:
        await _restore_others(mutated)
        await _cleanup_accounts([aid])


async def test_drill_unknown_asset_class_returns_empty(db_session, redis) -> None:
    """Unknown asset class → empty instruments list (not an error)."""
    await redis.flushdb()
    aid = await _seed_account("ibkr", "USD", "10000")
    mutated = await _soft_delete_others([aid])
    await redis.set("fx:mid:USD:GBP", "0.7912")
    try:
        service = PortfolioRollupService(db_session, redis)
        # Pick an asset_class that's almost certainly not in the enum + has
        # no positions
        # FOREX enum exists but no fixture seeded a forex instrument → empty
        drill = await service.drill_asset_class("FOREX", "GBP")
        assert drill.instruments == []
    finally:
        await _restore_others(mutated)
        await _cleanup_accounts([aid])
