from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import sqlalchemy as sa
import structlog

from app.core import metrics

log = structlog.get_logger()

_DAILY_TTL = 300
_INTRADAY_TTL = 60

DAILY_INDICATORS = frozenset({"rsi", "sma", "ema", "macd", "bb_pct", "atr"})
INTRADAY_SCALARS = frozenset(
    {"close", "open", "high", "low", "volume", "volume_ratio", "price_vs_high", "price_vs_low"}
)
FUNDAMENTAL_SCALARS = frozenset({"mcap", "pe", "eps_growth"})


def _params_hash(params: dict[str, Any]) -> str:
    return hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:8]


class IndicatorComputer:
    def __init__(self, *, redis: Any, db: Any) -> None:
        self._redis = redis
        self._db = db

    async def compute(
        self,
        name: str,
        params: dict[str, Any],
        *,
        instrument_id: int | None,
        canonical_id: str,
    ) -> float | None:
        if name in FUNDAMENTAL_SCALARS:
            return await self._fundamental(name, instrument_id)
        timeframe = "1d" if name in DAILY_INDICATORS else "1m"
        cache_key = f"scanner:ind:{instrument_id}:{name}:{_params_hash(params)}:{timeframe}"
        cached = await self._redis.get(cache_key)
        if cached is not None:
            metrics.scanner_indicator_cache_hits_total.inc()
            return float(cached)
        metrics.scanner_indicator_cache_misses_total.inc()
        val = await self._recompute(
            name,
            params,
            instrument_id=instrument_id,
            canonical_id=canonical_id,
            timeframe=timeframe,
        )
        if val is not None:
            ttl = _DAILY_TTL if timeframe == "1d" else _INTRADAY_TTL
            await self._redis.setex(cache_key, ttl, str(val))
        return val

    async def _fundamental(self, name: str, instrument_id: int | None) -> float | None:
        if instrument_id is None:
            return None
        try:
            row = await self._db.execute(
                sa.text("SELECT meta->:key AS v FROM instruments WHERE id = :id"),
                {"key": name, "id": instrument_id},
            )
            r = row.fetchone()
            if r and r.v is not None:
                return float(r.v)
        except Exception:
            log.warning("scanner.indicator.fundamental_error", name=name)
        return None

    async def _recompute(
        self,
        name: str,
        params: dict[str, Any],
        *,
        instrument_id: int | None,
        canonical_id: str,
        timeframe: str,
    ) -> float | None:
        if instrument_id is None:
            return None
        try:
            if name == "rsi":
                return await self._rsi(instrument_id, int(params.get("period", 14)))
            if name in ("close", "open", "high", "low", "volume"):
                return await self._latest_bar_field(instrument_id, name)
            if name == "sma":
                return await self._sma(
                    instrument_id, str(params.get("field", "close")), int(params.get("period", 20))
                )
            if name == "ema":
                return await self._ema(
                    instrument_id, str(params.get("field", "close")), int(params.get("period", 20))
                )
            if name == "atr":
                return await self._atr(instrument_id, int(params.get("period", 14)))
            if name == "volume_ratio":
                return await self._volume_ratio(instrument_id, int(params.get("period", 20)))
            if name == "price_vs_high":
                return await self._price_vs_high(instrument_id, int(params.get("days", 52 * 5)))
            if name == "price_vs_low":
                return await self._price_vs_low(instrument_id, int(params.get("days", 52 * 5)))
            if name == "macd":
                return await self._macd(
                    instrument_id,
                    int(params.get("fast", 12)),
                    int(params.get("slow", 26)),
                    int(params.get("signal", 9)),
                )
            if name == "bb_pct":
                return await self._bb_pct(
                    instrument_id, int(params.get("period", 20)), float(params.get("std", 2.0))
                )
        except Exception:
            log.warning("scanner.indicator.recompute_error", name=name, instrument_id=instrument_id)
        return None

    async def _latest_bar_field(self, instrument_id: int, field: str) -> float | None:
        safe_fields = {"close", "open", "high", "low", "volume"}
        if field not in safe_fields:
            return None
        row = await self._db.execute(
            sa.text(
                f"SELECT {field} FROM bars_1m WHERE instrument_id = :id ORDER BY ts DESC LIMIT 1"
            ),
            {"id": instrument_id},
        )
        r = row.fetchone()
        return float(getattr(r, field)) if r else None

    async def _rsi(self, instrument_id: int, period: int) -> float | None:
        rows = await self._db.execute(
            sa.text(
                "SELECT close FROM bars_1d WHERE instrument_id = :id ORDER BY ts DESC LIMIT :n"
            ),
            {"id": instrument_id, "n": period + 1},
        )
        closes = [float(r.close) for r in rows.fetchall()]
        if len(closes) < period + 1:
            return None
        closes = list(reversed(closes))
        gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
        losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    async def _sma(self, instrument_id: int, field: str, period: int) -> float | None:
        safe_fields = {"close", "open", "high", "low", "volume"}
        if field not in safe_fields:
            return None
        rows = await self._db.execute(
            sa.text(
                f"SELECT {field} FROM bars_1d WHERE instrument_id = :id ORDER BY ts DESC LIMIT :n"
            ),
            {"id": instrument_id, "n": period},
        )
        vals = [float(getattr(r, field)) for r in rows.fetchall()]
        return sum(vals) / len(vals) if len(vals) == period else None

    async def _ema(self, instrument_id: int, field: str, period: int) -> float | None:
        safe_fields = {"close", "open", "high", "low", "volume"}
        if field not in safe_fields:
            return None
        rows = await self._db.execute(
            sa.text(
                f"SELECT {field} FROM bars_1d WHERE instrument_id = :id ORDER BY ts ASC LIMIT :n"
            ),
            {"id": instrument_id, "n": period * 2},
        )
        vals = [float(getattr(r, field)) for r in rows.fetchall()]
        if len(vals) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(vals[:period]) / period
        for v in vals[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    async def _atr(self, instrument_id: int, period: int) -> float | None:
        rows = await self._db.execute(
            sa.text(
                "SELECT high, low, close FROM bars_1d WHERE instrument_id = :id "
                "ORDER BY ts DESC LIMIT :n"
            ),
            {"id": instrument_id, "n": period + 1},
        )
        bars = list(reversed(rows.fetchall()))
        if len(bars) < period + 1:
            return None
        trs = []
        for i in range(1, len(bars)):
            hi, lo, pc = float(bars[i].high), float(bars[i].low), float(bars[i - 1].close)
            trs.append(max(hi - lo, abs(hi - pc), abs(lo - pc)))
        return sum(trs[-period:]) / period

    async def _volume_ratio(self, instrument_id: int, period: int) -> float | None:
        rows = await self._db.execute(
            sa.text(
                "SELECT volume FROM bars_1m WHERE instrument_id = :id ORDER BY ts DESC LIMIT :n"
            ),
            {"id": instrument_id, "n": period},
        )
        vols = [float(r.volume) for r in rows.fetchall()]
        if not vols:
            return None
        avg = sum(vols) / len(vols)
        return vols[0] / avg if avg else None

    async def _price_vs_high(self, instrument_id: int, days: int) -> float | None:
        rows = await self._db.execute(
            sa.text("SELECT high FROM bars_1d WHERE instrument_id = :id ORDER BY ts DESC LIMIT :n"),
            {"id": instrument_id, "n": days},
        )
        highs = [float(r.high) for r in rows.fetchall()]
        if not highs:
            return None
        latest_close = await self._latest_bar_field(instrument_id, "close")
        if latest_close is None:
            return None
        return latest_close / max(highs)

    async def _price_vs_low(self, instrument_id: int, days: int) -> float | None:
        rows = await self._db.execute(
            sa.text("SELECT low FROM bars_1d WHERE instrument_id = :id ORDER BY ts DESC LIMIT :n"),
            {"id": instrument_id, "n": days},
        )
        lows = [float(r.low) for r in rows.fetchall()]
        if not lows:
            return None
        latest_close = await self._latest_bar_field(instrument_id, "close")
        if latest_close is None:
            return None
        return latest_close / min(lows)

    async def _macd(self, instrument_id: int, fast: int, slow: int, signal: int) -> float | None:
        fast_ema = await self._ema(instrument_id, "close", fast)
        slow_ema = await self._ema(instrument_id, "close", slow)
        if fast_ema is None or slow_ema is None:
            return None
        return fast_ema - slow_ema

    async def _bb_pct(self, instrument_id: int, period: int, std_mult: float) -> float | None:
        rows = await self._db.execute(
            sa.text(
                "SELECT close FROM bars_1d WHERE instrument_id = :id ORDER BY ts DESC LIMIT :n"
            ),
            {"id": instrument_id, "n": period},
        )
        closes = [float(r.close) for r in rows.fetchall()]
        if len(closes) < period:
            return None
        mean = sum(closes) / period
        std = math.sqrt(sum((c - mean) ** 2 for c in closes) / period)
        if std == 0:
            return 0.5
        latest = closes[0]
        upper = mean + std_mult * std
        lower = mean - std_mult * std
        return (latest - lower) / (upper - lower)
