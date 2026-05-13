"""Phase 11b chunk B4: resolution-aware predicate replay.

Picks a bar resolution based on the predicate's time-window slot:

- ``window < 60s`` → ``insufficient``; UI requires "I understand backtest is
  unreliable" checkbox before Confirm activates (spec §7).
- ``60s ≤ window < 24h`` → replay against ``bars_1m`` (last 24h).
- ``window ≥ 24h`` → replay against ``bars_1d`` CAGG (last 30d).

Composite predicates take the most-restrictive child resolution: any
``insufficient`` leaf bubbles up; otherwise ``1m`` unless every leaf is
``1d``.

The replay is best-effort: per-bar evaluator exceptions are caught and
skipped (matches the runtime evaluator's fail-isolation contract). Sample
fires are capped at ``max_samples`` (default 10) with a ``truncated`` flag
so the FE can show "10 of N fires shown".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.alerts.predicates import evaluate


@dataclass(slots=True)
class DryRunResult:
    replay_resolution: str  # '1m' | '1d' | 'insufficient'
    fire_count: int
    sample_fires: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False


def _pick_resolution(predicate: dict[str, Any]) -> str:
    kind = predicate.get("kind")
    if kind in {"composite_and", "composite_or"}:
        children = predicate.get("children", [])
        if not children:
            return "1m"
        children_resolutions = [_pick_resolution(c) for c in children]
        if "insufficient" in children_resolutions:
            return "insufficient"
        if all(r == "1d" for r in children_resolutions):
            return "1d"
        return "1m"
    # window_seconds wins; volume_spike uses vs_window_minutes (x 60 for compare)
    window = predicate.get("window_seconds")
    if window is None:
        vs_window_minutes = predicate.get("vs_window_minutes")
        window = vs_window_minutes * 60 if vs_window_minutes is not None else 0
    if 0 < window < 60:
        return "insufficient"
    if window >= 86400:
        return "1d"
    return "1m"


def replay(
    *,
    predicate: dict[str, Any],
    bars_1m: list[dict[str, Any]],
    bars_1d: list[dict[str, Any]],
    max_samples: int = 10,
) -> DryRunResult:
    """Replay a predicate against the appropriate bar series.

    Series must each be sorted ascending by ``ts``. Each bar is a dict with
    at least ``ts``, ``close``, ``volume`` keys.
    """
    resolution = _pick_resolution(predicate)
    if resolution == "insufficient":
        return DryRunResult(replay_resolution="insufficient", fire_count=0)

    series = bars_1m if resolution == "1m" else bars_1d
    symbol = predicate.get("symbol") or "X"
    fires: list[dict[str, Any]] = []
    for i in range(2, len(series)):
        window_bars = series[: i + 1]
        state = {
            "prices": {symbol: window_bars[-1]["close"]},
            "bars": {symbol: window_bars},
        }
        try:
            if evaluate(predicate, state):
                fires.append({"ts": window_bars[-1]["ts"], "close": window_bars[-1]["close"]})
        except Exception:
            # per-bar isolation (spec §7): one bad bar must not abort the replay
            continue

    truncated = len(fires) > max_samples
    return DryRunResult(
        replay_resolution=resolution,
        fire_count=len(fires),
        sample_fires=fires[:max_samples],
        truncated=truncated,
    )
