"""IBKR Flex automated daily pull and parser."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import uuid
from decimal import Decimal

import httpx
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.cgt import engine, metrics
from app.services.cgt.fx import to_gbp
from app.services.cgt.importers.normaliser import ibkr_trade_to_tax_event, resolve_cgt_track

log = structlog.get_logger(__name__)

_FLEX_BASE = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService"


async def run_import(
    account_id: uuid.UUID,
    flex_token: str,
    flex_query_id: str,
    session: AsyncSession,
) -> dict:
    """Fetch, store, parse, and process IBKR Flex statement."""
    xml_bytes = await _fetch_flex_xml(flex_token, flex_query_id)
    compressed = gzip.compress(xml_bytes)
    sha256 = hashlib.sha256(compressed).hexdigest()

    result = await session.execute(
        text("""
            INSERT INTO broker_statements
              (broker_id, account_id, statement_type, period_start, period_end,
               raw_content, raw_format, raw_sha256)
            VALUES ('ibkr', :a, 'flex_activity', CURRENT_DATE - 1, CURRENT_DATE,
                    :raw, 'gz_xml', :sha)
            ON CONFLICT (broker_id, account_id, statement_type,
                        period_start, period_end, raw_sha256)
            DO NOTHING
            RETURNING id
        """),
        {"a": account_id, "raw": compressed, "sha": sha256},
    )
    stmt_row = result.fetchone()
    if stmt_row is None:
        log.info("cgt.ibkr_flex.already_imported", sha256=sha256)
        metrics.cgt_importer_runs_total.labels(broker="ibkr", status="skipped").inc()
        return {"skipped": True, "trades_imported": 0}

    stmt_id = stmt_row.id
    trades_imported = 0
    income_imported = 0

    try:
        import ibflex  # type: ignore[import-untyped]

        flex = ibflex.parse(xml_bytes)
    except Exception as exc:
        log.error("cgt.ibkr_flex.parse_failed", exc=str(exc))
        metrics.cgt_importer_runs_total.labels(broker="ibkr", status="error").inc()
        raise

    for stmt in flex.FlexStatements:
        for trade in stmt.Trades or []:
            try:
                instrument_id, cgt_class_key = await _resolve_instrument(
                    trade.symbol, getattr(trade, "isin", None), session
                )
                if instrument_id is None:
                    log.warning(
                        "cgt.ibkr_flex.unknown_symbol",
                        symbol=getattr(trade, "symbol", "?"),
                    )
                    metrics.cgt_importer_records_imported_total.labels(
                        broker="ibkr", record_type="unknown_symbol"
                    ).inc()
                    continue
                cgt_track = resolve_cgt_track(getattr(trade, "assetCategory", "STK"))

                gbp_amount, fx_rate, fx_source = await to_gbp(
                    Decimal(str(trade.tradePrice)),
                    trade.currency,
                    trade.dateTime,
                    session,
                )
                _, comm_gbp, _ = await to_gbp(
                    Decimal(str(abs(trade.ibCommission or 0))),
                    trade.ibCommissionCurrency or trade.currency,
                    trade.dateTime,
                    session,
                )

                te = ibkr_trade_to_tax_event(
                    trade=trade,
                    account_id=account_id,
                    instrument_id=instrument_id,
                    cgt_class_key=cgt_class_key,
                    gbp_price=gbp_amount,
                    fx_rate=fx_rate,
                    fx_source=fx_source,
                    commission_gbp=comm_gbp,
                    cgt_track=cgt_track,
                )

                await session.execute(
                    text("""
                    INSERT INTO tax_events
                      (id, fill_id, leg_index, broker_statement_id, external_event_id,
                       source, account_id, instrument_id, cgt_track, event_type, side,
                       is_short_open, is_short_close, qty, price_gbp, commission_native,
                       commission_currency, commission_gbp, fx_rate, fx_source,
                       original_currency, cgt_class_key, bb_remaining_qty, executed_at,
                       bot_id, notes)
                    VALUES (:id, NULL, 0, :bsid, :ext_id, 'broker_statement', :acct,
                            :iid, :track, :etype, :side, :sopen, :sclose, :qty, :price,
                            :cnative, :ccy, :cgbp, :fx, :fxsrc, :origccy, :clskey,
                            :bbqty, :exat, NULL, NULL)
                    ON CONFLICT DO NOTHING
                """),
                    {
                        "id": te.id,
                        "bsid": stmt_id,
                        "ext_id": te.external_event_id,
                        "acct": account_id,
                        "iid": instrument_id,
                        "track": cgt_track,
                        "etype": te.event_type,
                        "side": te.side,
                        "sopen": te.is_short_open,
                        "sclose": te.is_short_close,
                        "qty": te.qty,
                        "price": te.price_gbp,
                        "cnative": te.commission_native,
                        "ccy": te.commission_currency,
                        "cgbp": te.commission_gbp,
                        "fx": te.fx_rate,
                        "fxsrc": te.fx_source,
                        "origccy": te.original_currency,
                        "clskey": te.cgt_class_key,
                        "bbqty": te.bb_remaining_qty,
                        "exat": te.executed_at,
                    },
                )

                await engine.process(te, session)
                trades_imported += 1
                metrics.cgt_importer_records_imported_total.labels(
                    broker="ibkr", record_type="fill"
                ).inc()
            except Exception as exc:
                log.error(
                    "cgt.ibkr_flex.trade_failed",
                    exc=str(exc),
                    symbol=getattr(trade, "symbol", "?"),
                )

    await session.execute(
        text("UPDATE broker_statements SET imported_at = now() WHERE id = :id"),
        {"id": stmt_id},
    )
    metrics.cgt_importer_runs_total.labels(broker="ibkr", status="success").inc()
    log.info("cgt.ibkr_flex.done", trades=trades_imported, income=income_imported)
    return {"trades_imported": trades_imported, "income_imported": income_imported}


async def _fetch_flex_xml(flex_token: str, flex_query_id: str) -> bytes:
    """Two-step Flex Web Service: SendRequest → GetStatement."""
    import xml.etree.ElementTree as ET

    async with httpx.AsyncClient(timeout=60) as client:
        r1 = await client.get(
            f"{_FLEX_BASE}.SendRequest",
            params={"t": flex_token, "q": flex_query_id, "v": "3"},
        )
        r1.raise_for_status()
        root1 = ET.fromstring(r1.content)
        ref_code = root1.findtext("ReferenceCode")
        if not ref_code:
            raise ValueError("No ReferenceCode in Flex SendRequest response")

        for _ in range(5):
            await asyncio.sleep(10)
            r2 = await client.get(
                f"{_FLEX_BASE}.GetStatement",
                params={"t": flex_token, "q": ref_code, "v": "3"},
            )
            if r2.status_code == 200 and b"<FlexQueryResponse" in r2.content:
                return r2.content
        raise TimeoutError("IBKR Flex GetStatement did not return XML after 5 polls")


async def _resolve_instrument(
    symbol: str, isin: str | None, session: AsyncSession
) -> tuple[int | None, str | None]:
    result = await session.execute(
        text("SELECT id FROM instruments WHERE symbol = :s LIMIT 1"),
        {"s": symbol},
    )
    row = result.fetchone()
    instrument_id: int | None = row.id if row else None
    cgt_class_key = isin if isin else f"{symbol}:USD"
    return instrument_id, cgt_class_key
