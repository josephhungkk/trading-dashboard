"""Phase 10b.2 §11.4 — PortfolioRollupService golden tests (GV1, GV2, GV6, GV10)."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

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


async def _seed_account(broker: str, native: str, nlv: str):
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


async def _soft_delete_others(keep_ids: list) -> None:
    """Soft-delete every broker_account NOT in keep_ids — so the rollup
    SELECT sees a deterministic set across test runs."""
    async with SessionLocal() as s:
        async with s.begin():
            await s.execute(
                text(
                    "UPDATE broker_accounts SET deleted_at = now() "
                    "WHERE id NOT IN :keep AND deleted_at IS NULL"
                ).bindparams(bindparam("keep", expanding=True)),
                {"keep": [str(i) for i in keep_ids]},
            )


async def _restore_others(our_ids: list) -> None:
    async with SessionLocal() as s:
        async with s.begin():
            await s.execute(
                text(
                    "UPDATE broker_accounts SET deleted_at = NULL "
                    "WHERE id NOT IN :ours AND deleted_at IS NOT NULL"
                ).bindparams(bindparam("ours", expanding=True)),
                {"ours": [str(i) for i in our_ids]},
            )


async def _cleanup_accounts(ids: list) -> None:
    async with SessionLocal() as s:
        async with s.begin():
            await s.execute(_DELETE_ACCOUNTS_SQL, {"ids": [str(i) for i in ids]})


async def test_gv1_single_usd_account_base_gbp(db_session, redis) -> None:
    """GV1 — 10000 USD, FX USD/GBP=0.7912, base GBP → 7912.00."""
    await redis.flushdb()
    aid = await _seed_account("ibkr", "USD", "10000")
    await _soft_delete_others([aid])
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
        await _restore_others([aid])
        await _cleanup_accounts([aid])


async def test_gv2_usd_plus_hkd_base_gbp(db_session, redis) -> None:
    """GV2 — 10000 USD + 50000 HKD, base GBP → 7912 + 5075 = 12987.00."""
    await redis.flushdb()
    aid_usd = await _seed_account("ibkr", "USD", "10000")
    aid_hkd = await _seed_account("futu", "HKD", "50000")
    ids = [aid_usd, aid_hkd]
    await _soft_delete_others(ids)
    await redis.set("fx:mid:USD:GBP", "0.7912")
    await redis.set("fx:mid:HKD:GBP", "0.1015")

    try:
        service = PortfolioRollupService(db_session, redis)
        live = await service.compute_live("GBP")
        assert live.total_nlv_base == Decimal("12987.00")
        assert live.fx_rates.get("USD/GBP") == Decimal("0.7912")
        assert live.fx_rates.get("HKD/GBP") == Decimal("0.1015")
    finally:
        await _restore_others(ids)
        await _cleanup_accounts(ids)


async def test_gv6_all_fx_unavailable_raises_503(db_session, redis) -> None:
    """GV6 — no FX rates seeded; all non-init accounts fail → 503."""
    await redis.flushdb()
    aid_usd = await _seed_account("ibkr", "USD", "10000")
    aid_hkd = await _seed_account("futu", "HKD", "50000")
    ids = [aid_usd, aid_hkd]
    await _soft_delete_others(ids)

    try:
        service = PortfolioRollupService(db_session, redis)
        with pytest.raises(PreviewUnavailable) as exc_info:
            await service.compute_live("GBP")
        assert exc_info.value.payload == {
            "error": "fx_rate_unavailable",
            "pair": "all",
        }
    finally:
        await _restore_others(ids)
        await _cleanup_accounts(ids)


async def test_gv10_partial_fx_outage(db_session, redis) -> None:
    """GV10 — USD + GBP work; HKD/GBP missing → 200 partial."""
    await redis.flushdb()
    aid_usd = await _seed_account("ibkr", "USD", "10000")
    aid_hkd = await _seed_account("futu", "HKD", "50000")
    aid_gbp = await _seed_account("schwab", "GBP", "1000")
    ids = [aid_usd, aid_hkd, aid_gbp]
    await _soft_delete_others(ids)
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
        await _restore_others(ids)
        await _cleanup_accounts(ids)
