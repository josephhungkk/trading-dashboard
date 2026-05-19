from __future__ import annotations

import dataclasses
import math
from datetime import datetime
from decimal import Decimal
from typing import Any

import exchange_calendars as ecals


@dataclasses.dataclass
class ClosedTrade:
    canonical_id: str
    side: str
    qty: Decimal
    entry_price: Decimal
    exit_price: Decimal
    entry_slippage: Decimal
    exit_slippage: Decimal
    commission: Decimal
    pnl: Decimal
    forced_close: bool
    opened_at: datetime
    closed_at: datetime


class MetricsComputer:
    def __init__(self, *, exchange: str = "NYSE") -> None:
        self._exchange = exchange

    def compute(self, trades: list[ClosedTrade], bar_timestamps: list[datetime]) -> dict[str, Any]:
        if not bar_timestamps:
            return self._empty_report()

        pnl_at: dict[datetime, Decimal] = {}
        for trade in sorted(trades, key=lambda t: t.closed_at):
            pnl_at[trade.closed_at] = pnl_at.get(trade.closed_at, Decimal("0")) + trade.pnl

        cum_pnl = Decimal("0")
        pnl_curve: list[tuple[str, float]] = []
        dd_curve: list[tuple[str, float]] = []
        peak = Decimal("0")
        max_dd = Decimal("0")

        for ts in bar_timestamps:
            cum_pnl += pnl_at.get(ts, Decimal("0"))
            pnl_curve.append((ts.isoformat(), float(cum_pnl)))
            if cum_pnl > peak:
                peak = cum_pnl
            dd = (peak - cum_pnl) / max(abs(peak), Decimal("1")) * 100 if peak > 0 else Decimal("0")
            max_dd = max(max_dd, dd)
            dd_curve.append((ts.isoformat(), float(dd)))

        total_return_pct = float(cum_pnl)

        sharpe = self._compute_sharpe(pnl_curve, bar_timestamps)

        start = bar_timestamps[0]
        end = bar_timestamps[-1]
        years = max((end - start).days / 365.25, 1 / 365.25)
        cagr = (1 + total_return_pct / 100) ** (1 / years) - 1 if total_return_pct > -100 else -1.0
        mar = cagr / (float(max_dd) / 100) if max_dd > 0 else None

        closed_non_forced = [t for t in trades if not t.forced_close]
        winning = [t for t in closed_non_forced if t.pnl > 0]
        forced_close_pnl = sum((t.pnl for t in trades if t.forced_close), Decimal("0"))

        return {
            "sharpe": sharpe,
            "mar": round(mar, 4) if mar is not None else None,
            "max_drawdown_pct": round(float(max_dd), 4),
            "total_return_pct": round(total_return_pct, 4),
            "total_trades": len(trades),
            "win_rate": round(len(winning) / len(closed_non_forced), 4)
            if closed_non_forced
            else None,
            "avg_trade_pnl": round(float(sum(t.pnl for t in trades) / len(trades)), 4)
            if trades
            else None,
            "forced_close_pnl": float(forced_close_pnl),
            "pnl_curve": pnl_curve,
            "drawdown_curve": dd_curve,
            "trades": [self._trade_to_dict(t) for t in trades],
        }

    def _compute_sharpe(
        self, pnl_curve: list[tuple[str, float]], bar_ts: list[datetime]
    ) -> float | None:
        if len(pnl_curve) < 2:
            return None
        try:
            cal = ecals.get_calendar(self._exchange)
            start = bar_ts[0]
            end = bar_ts[-1]
            sessions = cal.sessions_in_range(start.date(), end.date())
        except ValueError:
            return None
        except KeyError:
            return None
        except AttributeError:
            return None

        pnl_by_ts = dict(pnl_curve)
        session_pnls: list[float] = []
        prev = 0.0
        for session in sessions:
            val = prev
            for k, v in pnl_by_ts.items():
                if k.startswith(session.isoformat()):
                    val = v
            session_pnls.append(val - prev)
            prev = val

        if len(session_pnls) < 2:
            return None
        mean = sum(session_pnls) / len(session_pnls)
        variance = sum((x - mean) ** 2 for x in session_pnls) / len(session_pnls)
        std = math.sqrt(variance)
        if std == 0:
            return None
        return round(mean / std * math.sqrt(252), 4)

    def _empty_report(self) -> dict[str, Any]:
        return {
            "sharpe": None,
            "mar": None,
            "max_drawdown_pct": 0.0,
            "total_return_pct": 0.0,
            "total_trades": 0,
            "win_rate": None,
            "avg_trade_pnl": None,
            "forced_close_pnl": 0.0,
            "pnl_curve": [],
            "drawdown_curve": [],
            "trades": [],
        }

    def _trade_to_dict(self, t: ClosedTrade) -> dict[str, Any]:
        return {
            "canonical_id": t.canonical_id,
            "side": t.side,
            "qty": float(t.qty),
            "entry_price": float(t.entry_price),
            "exit_price": float(t.exit_price),
            "entry_slippage": float(t.entry_slippage),
            "exit_slippage": float(t.exit_slippage),
            "commission": float(t.commission),
            "pnl": float(t.pnl),
            "forced_close": t.forced_close,
            "opened_at": t.opened_at.isoformat(),
            "closed_at": t.closed_at.isoformat(),
        }
