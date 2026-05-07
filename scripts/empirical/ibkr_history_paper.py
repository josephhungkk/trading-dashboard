"""Empirically validate IBKR paper 1m history coverage for AAPL.US."""
from __future__ import annotations

import asyncio
import json
import socket
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from cryptography.fernet import InvalidToken
from sqlalchemy import text

from app.core.config import settings
from app.core.crypto import get_fernet
from app.core.db import SessionLocal, engine

import structlog

from app.services.market_calendar import _calendar, is_trading_day

# pragma: empirical

log = structlog.get_logger("empirical.history")
BROKER = "ibkr"
SYMBOL = "AAPL"
CANONICAL_ID = "AAPL.US"
EXCHANGE = "NYSE"
MISSING_TOLERANCE = 0.05
REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = REPO_ROOT / "tmp" / "empirical"


class EmpiricalFailureError(RuntimeError):
    """Raised when an empirical assertion fails."""


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


def _bar_to_json(bar: Any) -> dict[str, Any]:
    raw_date = getattr(bar, "date", None)
    if isinstance(raw_date, datetime):
        bucket = raw_date.astimezone(UTC)
    elif isinstance(raw_date, str):
        bucket = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).astimezone(UTC)
    else:
        raise EmpiricalFailureError(f"ibkr bar has invalid date: {raw_date!r}")
    return {
        "broker": BROKER,
        "symbol": CANONICAL_ID,
        "bucket_start": bucket,
        "open": str(getattr(bar, "open", "")),
        "high": str(getattr(bar, "high", "")),
        "low": str(getattr(bar, "low", "")),
        "close": str(getattr(bar, "close", "")),
        "volume": str(getattr(bar, "volume", "")),
        "trade_count": int(getattr(bar, "barCount", 0) or 0),
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


def _default_client_id() -> int:
    return (sum(socket.gethostname().encode("utf-8")) % 900) + 100


async def _run() -> Path:
    from ib_async import IB, Stock  # type: ignore[import-untyped]

    host = await _load_secret("host", required=False) or "127.0.0.1"
    port = int(await _load_secret("port", required=False) or "7497")
    client_id = int(await _load_secret("client_id", required=False) or str(_default_client_id()))
    ib = IB()
    try:
        await ib.connectAsync(host, port, clientId=client_id, timeout=30)
        contract = Stock(SYMBOL, "SMART", "USD")
        qualified = await ib.qualifyContractsAsync(contract)
        if qualified:
            contract = qualified[0]
        end = datetime.now(UTC)
        start = end - timedelta(days=30)
        raw_bars = await ib.reqHistoricalDataAsync(
            contract,
            endDateTime=end,
            durationStr=f"{int((end - start).total_seconds())} S",
            barSizeSetting="1 min",
            whatToShow="TRADES",
            useRTH=False,
            formatDate=2,
        )
        bars = [_bar_to_json(bar) for bar in cast(Sequence[Any], raw_bars)]
    finally:
        if ib.isConnected():
            ib.disconnect()
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
