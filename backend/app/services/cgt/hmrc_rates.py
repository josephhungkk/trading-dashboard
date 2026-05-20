from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date

import httpx
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.cgt import metrics

log = structlog.get_logger(__name__)
_TARGET_CURRENCIES = {"USD", "HKD", "EUR", "JPY", "CAD", "AUD", "CHF", "CNH", "CNY"}
_URL_2021_PLUS = "https://www.trade-tariff.service.gov.uk/api/v2/exchange_rates/files/monthly_xml_{year}-{month:02d}.xml"
_URL_PRE_2021 = (
    "http://www.hmrc.gov.uk/softwaredevelopers/rates/exrates-monthly-{month:02d}{year2}.xml"
)


async def fetch_and_store_rates(period_month: date, session: AsyncSession) -> None:
    """Fetch HMRC monthly FX rates for period_month and upsert into DB."""
    month_str = f"{period_month.year}-{period_month.month:02d}"
    try:
        xml_bytes = await _fetch_xml(period_month)
        rates = _parse_xml(xml_bytes)
        for currency, rate in rates.items():
            if currency not in _TARGET_CURRENCIES:
                continue
            await session.execute(
                text("""
                    INSERT INTO hmrc_fx_rates (currency, period_month, rate_gbp, source)
                    VALUES (:c, :m, :r, 'hmrc_monthly')
                    ON CONFLICT (currency, period_month) DO UPDATE
                      SET rate_gbp = EXCLUDED.rate_gbp
                """),
                {"c": currency, "m": period_month, "r": rate},
            )
        metrics.cgt_hmrc_fx_fetch_total.labels(status="success", period_month=month_str).inc()
        log.info("cgt.hmrc_rates.fetched", period_month=month_str, count=len(rates))
    except Exception as exc:
        metrics.cgt_hmrc_fx_fetch_total.labels(status="error", period_month=month_str).inc()
        log.error("cgt.hmrc_rates.fetch_failed", period_month=month_str, exc=str(exc))


async def _fetch_xml(period_month: date) -> bytes:
    if period_month.year >= 2021:
        url = _URL_2021_PLUS.format(year=period_month.year, month=period_month.month)
    else:
        url = _URL_PRE_2021.format(month=period_month.month, year2=str(period_month.year)[-2:])
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


def _parse_xml(xml_bytes: bytes) -> dict[str, object]:
    root = ET.fromstring(xml_bytes)
    rates: dict[str, object] = {}
    for item in root.iter("exchangeRate"):
        code_el = item.find("currencyCode")
        rate_el = item.find("rateNew")
        if code_el is not None and rate_el is not None and code_el.text and rate_el.text:
            rates[code_el.text.strip()] = rate_el.text.strip()
    return rates
