"""Empirically validate Alpaca paper 1m history coverage for AAPL.US."""
from __future__ import annotations

import asyncio
import inspect
import json
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
from app.services.market_calendar import _calendar, is_trading_day

# pragma: empirical

BROKER = "alpaca"
SYMBOL = "AAPL"
CANONICAL_ID = "AAPL.US"
EXCHANGE = "NYSE"
MISSING_TOLERANCE = 0.05
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


def _bars_from_response(response: Any, symbol: str) -> Sequence[Any]:
    data = getattr(response, "data", None)
    if isinstance(data, Mapping):
        rows = data.get(symbol, [])
        return list(cast(Iterable[Any], rows))
    rows = response[symbol] if hasattr(response, "__getitem__") else []
    return list(cast(Iterable[Any], rows))


def _bar_to_json(symbol: str, bar: Any) -> dict[str, Any]:
    timestamp = getattr(bar, "timestamp", None) or getattr(bar, "t", None)
    if not isinstance(timestamp, datetime):
        raise EmpiricalFailureError(f"alpaca bar has invalid timestamp: {timestamp!r}")
    return {
        "broker": BROKER,
        "symbol": f"{symbol}.US",
        "bucket_start": timestamp.astimezone(UTC),
        "open": str(getattr(bar, "open", getattr(bar, "o", ""))),
        "high": str(getattr(bar, "high", getattr(bar, "h", ""))),
        "low": str(getattr(bar, "low", getattr(bar, "l", ""))),
        "close": str(getattr(bar, "close", getattr(bar, "c", ""))),
        "volume": str(getattr(bar, "volume", getattr(bar, "v", ""))),
        "trade_count": int(getattr(bar, "trade_count", getattr(bar, "n", 0)) or 0),
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


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _run() -> Path:
    _add_sidecar_site_packages("sidecar_alpaca")
    from alpaca.data.historical import StockHistoricalDataClient  # type: ignore[import-untyped]
    from alpaca.data.requests import StockBarsRequest  # type: ignore[import-untyped]
    from alpaca.data.timeframe import TimeFrame  # type: ignore[import-untyped]

    api_key = await _load_secret("api_key")
    api_secret = await _load_secret("api_secret")
    client = StockHistoricalDataClient(cast(str, api_key), cast(str, api_secret))
    end = datetime.now(UTC)
    start = end - timedelta(days=30)
    bars_request = StockBarsRequest(
        symbol_or_symbols=[SYMBOL],
        timeframe=TimeFrame.Minute,
        start=start,
        end=end,
        limit=10000,
    )
    response = await _maybe_await(client.get_stock_bars(bars_request))
    bars = [_bar_to_json(SYMBOL, bar) for bar in _bars_from_response(response, SYMBOL)]
    _assert_coverage(bars, start, end)
    path = _artifact_path()
    count = _write_jsonl(path, bars)
    print(f"PASS: broker={BROKER} symbol={CANONICAL_ID} bars={count} artifact={path}")
    return path


def main() -> int:
    try:
        asyncio.run(_run())
    except (Exception,) as exc:  # noqa: B013
        print(f"FAIL: broker={BROKER} reason={exc}", file=sys.stderr)
        return 1
    finally:
        asyncio.run(engine.dispose())
    return 0


if __name__ == "__main__":
    sys.exit(main())
