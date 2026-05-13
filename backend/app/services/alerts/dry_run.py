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

from app.services.alerts.predicates import evaluate, referenced_symbols

# Spec §7 resolution thresholds (seconds). Centralized so a future spec
# revision changes one constant rather than scattered literals.
_INSUFFICIENT_BELOW_SECONDS = 60
_DAILY_AT_OR_ABOVE_SECONDS = 86_400


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
        # Spec §4 schema requires minItems: 1, but this helper is also called
        # by the "test predicate" button on raw user JSON before validation.
        # Bail to 'insufficient' so an empty composite can't pretend to fire.
        if not children:
            return "insufficient"
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
    if 0 < window < _INSUFFICIENT_BELOW_SECONDS:
        return "insufficient"
    if window >= _DAILY_AT_OR_ABOVE_SECONDS:
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

    Composite predicates that reference multiple symbols all read from the
    SAME series — production callers pass interleaved/aligned series; the
    state dict populates ``prices`` + ``bars`` for every referenced symbol
    so each child's evaluator sees its own symbol.
    """
    resolution = _pick_resolution(predicate)
    if resolution == "insufficient":
        return DryRunResult(replay_resolution="insufficient", fire_count=0)

    series = bars_1m if resolution == "1m" else bars_1d
    symbols = referenced_symbols(predicate) or {predicate.get("symbol") or "X"}
    fires: list[dict[str, Any]] = []
    fire_count = 0
    # Replay starts at index 0 so non-windowed predicates (price_threshold,
    # order_event, ai_signal, news_event, unknown) get every bar evaluated.
    # Multi-bar predicates (pct_change_window, ma_cross, volume_spike) gate
    # themselves internally on insufficient series length.
    for i in range(len(series)):
        window_bars = series[: i + 1]
        last_close = window_bars[-1]["close"]
        state = {
            "prices": dict.fromkeys(symbols, last_close),
            "bars": dict.fromkeys(symbols, window_bars),
        }
        try:
            fired = evaluate(predicate, state)
        except Exception:
            # per-bar isolation (spec §7): one bad bar must not abort the replay
            continue
        if not fired:
            continue
        fire_count += 1
        # Spec §7: sample_fires bounded to ``max_samples``. Append only while
        # under the cap; ``truncated`` reports whether more were observed.
        if len(fires) < max_samples:
            fires.append({"ts": window_bars[-1]["ts"], "close": last_close})

    return DryRunResult(
        replay_resolution=resolution,
        fire_count=fire_count,
        sample_fires=fires,
        truncated=fire_count > max_samples,
    )
