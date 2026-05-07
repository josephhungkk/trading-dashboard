"""Empirically validate Futu paper 1m history coverage for 0700.HK."""
from __future__ import annotations

import asyncio
import json
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from cryptography.fernet import InvalidToken
from sqlalchemy import text

from app.core.config import settings
from app.core.crypto import get_fernet
from app.core.db import SessionLocal, engine

import structlog

from app.services.market_calendar import _calendar, is_trading_day

# pragma: empirical

log = structlog.get_logger("empirical.history")
BROKER = "futu"
FUTU_CODE = "HK.00700"
CANONICAL_ID = "0700.HK"
EXCHANGE = "HKEX"
MISSING_TOLERANCE = 0.05
HK_TZ = ZoneInfo("Asia/Hong_Kong")
REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = REPO_ROOT / "tmp" / "empirical"


class EmpiricalFailureError(RuntimeError):
    """Raised when an empirical assertion fails."""


def _add_sidecar_site_packages(sidecar: str) -> None:
    site_packages = REPO_ROOT / sidecar / ".venv" / "lib" / "python3.14" / "site-packages"
    if site_packages.exists():
        sys.path.insert(0, str(site_packages))


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def _artifact_path() -> Path:
    return ARTIFACT_DIR / f"{BROKER}_history_{_utc_stamp()}.jsonl"


def _decode_plaintext(plaintext: bytes, value_type: str) -> Any:
    if value_type == "json":
        return json.loads(plaintext.decode())
    if value_type == "int":
        return int(plaintext.decode())
    if value_type == "bool":
        return plaintext.decode() == "true"
    return plaintext.decode()


async def _load_secret(name: str, *, required: bool = True) -> str | None:
    candidates = (
        ("paper", f"{BROKER}.{name}"),
        ("broker", f"paper.{BROKER}.{name}"),
    )
    fernet = get_fernet(settings.secret_key, settings.secret_key_prev)
    async with SessionLocal() as session:
        for namespace, key in candidates:
            row = (
                await session.execute(
                    text(
                        "SELECT value_encrypted, value_type FROM app_secrets "
                        "WHERE namespace=:namespace AND key=:key"
                    ),
                    {"namespace": namespace, "key": key},
                )
            ).one_or_none()
            if row is None:
                continue
            try:
                value = _decode_plaintext(fernet.decrypt(row.value_encrypted), row.value_type)
            except InvalidToken as exc:
                raise EmpiricalFailureError(
                    f"cannot decrypt app_secrets {namespace}.{key}"
                ) from exc
            if value is None or str(value) == "":
                break
            return str(value)
    if required:
        raise EmpiricalFailureError(f"missing app_secrets paper.{BROKER}.{name}")
    return None


def _session_minutes(exchange: str, start: datetime, end: datetime) -> dict[date, set[datetime]]:
    cal = _calendar(exchange)
    expected: dict[date, set[datetime]] = {}
    day = start.astimezone(UTC).date()
    last_day = end.astimezone(UTC).date()
    while day <= last_day:
        if is_trading_day(exchange, day):
            session_open = cal.session_open(day.isoformat()).tz_convert("UTC").to_pydatetime()
            session_close = cal.session_close(day.isoformat()).tz_convert("UTC").to_pydatetime()
            cursor = max(cast(datetime, session_open), start).replace(second=0, microsecond=0)
            close_bound = min(cast(datetime, session_close), end)
            minutes: set[datetime] = set()
            while cursor < close_bound:
                minutes.add(cursor)
                cursor += timedelta(minutes=1)
            if minutes:
                expected[day] = minutes
        day += timedelta(days=1)
    return expected


def _assert_coverage(bars: Iterable[Mapping[str, Any]], start: datetime, end: datetime) -> None:
    expected = _session_minutes(EXCHANGE, start, end)
    if not expected:
        raise EmpiricalFailureError("no expected market minutes in requested range")
    observed_by_day: dict[date, set[datetime]] = defaultdict(set)
    for bar in bars:
        bucket = (
            cast(datetime, bar["bucket_start"])
            .astimezone(UTC)
            .replace(second=0, microsecond=0)
        )
        observed_by_day[bucket.date()].add(bucket)
    failures: list[str] = []
    for session_day, minutes in expected.items():
        missing = minutes - observed_by_day.get(session_day, set())
        if len(missing) / len(minutes) > MISSING_TOLERANCE:
            failures.append(
                f"{session_day.isoformat()} missing={len(missing)} expected={len(minutes)}"
            )
    if failures:
        raise EmpiricalFailureError("; ".join(failures[:5]))


def _bar_to_json(row: Mapping[str, Any]) -> dict[str, Any]:
    raw_bucket = datetime.fromisoformat(str(row["time_key"]))
    bucket = raw_bucket.replace(tzinfo=HK_TZ).astimezone(UTC)
    return {
        "broker": BROKER,
        "symbol": CANONICAL_ID,
        "bucket_start": bucket,
        "open": str(row["open"]),
        "high": str(row["high"]),
        "low": str(row["low"]),
        "close": str(row["close"]),
        "volume": str(row["volume"]),
        "trade_count": 0,
    }


def _write_jsonl(path: Path, bars: Iterable[Mapping[str, Any]]) -> int:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as fh:
        for bar in bars:
            row = dict(bar)
            row["bucket_start"] = cast(datetime, row["bucket_start"]).isoformat()
            fh.write(json.dumps(row, sort_keys=True) + "\n")
            count += 1
    return count


async def _run() -> Path:
    _add_sidecar_site_packages("sidecar_futu")
    import futu as ft  # type: ignore[import-untyped]

    host = await _load_secret("opend_host")
    port = int(cast(str, await _load_secret("opend_port")))
    end = datetime.now(UTC)
    start = end - timedelta(days=30)
    quote_ctx = ft.OpenQuoteContext(host=cast(str, host), port=port, is_encrypt=True)
    try:
        ret, data, _page_req_key = await asyncio.to_thread(
            quote_ctx.request_history_kline,
            FUTU_CODE,
            start=start.strftime("%Y-%m-%d %H:%M:%S"),
            end=end.strftime("%Y-%m-%d %H:%M:%S"),
            ktype=ft.KLType.K_1M,
            autype=ft.AuType.QFQ,
            max_count=10000,
        )
    finally:
        quote_ctx.close()
    if ret != ft.RET_OK:
        raise EmpiricalFailureError(f"request_history_kline failed: {data}")
    rows = cast(Iterable[Mapping[str, Any]], data.to_dict("records"))
    bars = [_bar_to_json(row) for row in rows]
    _assert_coverage(bars, start, end)
    path = _artifact_path()
    count = _write_jsonl(path, bars)
    log.info("empirical.pass", broker=BROKER, bars=count, artifact=str(path))
    return path


def main() -> int:
    try:
        asyncio.run(_run())
    except (Exception,) as exc:  # noqa: B013
        log.error("empirical.fail", broker=BROKER, reason=type(exc).__name__, message=str(exc)[:120])
        return 1
    finally:
        asyncio.run(engine.dispose())
    return 0


if __name__ == "__main__":
    sys.exit(main())
