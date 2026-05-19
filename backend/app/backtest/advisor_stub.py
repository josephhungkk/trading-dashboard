from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.advisor.types import AdvisorVerdict

AdvisorConfig = dict[str, Any]


@dataclass
class VetoInjection:
    bar_index: int
    canonical_id: str
    reasoning: str = "stub veto"


class AdvisorStub:
    """Deterministic in-backtest advisor. Reads veto_injections list from backtest config."""

    def __init__(self, veto_injections: list[VetoInjection]) -> None:
        self._injections: dict[int, VetoInjection] = {v.bar_index: v for v in veto_injections}

    @classmethod
    def from_config(cls, advisor_config: AdvisorConfig | None) -> AdvisorStub:
        """Parse advisor_config JSONB from backtests table."""
        if not advisor_config:
            return cls([])
        raw = advisor_config.get("veto_injections", [])
        injections = [
            VetoInjection(
                bar_index=r["bar_index"],
                canonical_id=r.get("canonical_id", "*"),
                reasoning=r.get("reasoning", "stub veto"),
            )
            for r in raw
        ]
        return cls(injections)

    def review(
        self, bar_index: int, canonical_id: str, intent: Any
    ) -> tuple[AdvisorVerdict, str, int]:
        """Returns (verdict, reasoning, latency_ms). Always fast (0ms)."""
        injection = self._injections.get(bar_index)
        if injection and (injection.canonical_id == "*" or injection.canonical_id == canonical_id):
            return (
                AdvisorVerdict(action="veto", reasoning=injection.reasoning, confidence=None),
                injection.reasoning,
                0,
            )
        return (
            AdvisorVerdict(action="approve", reasoning="stub approve", confidence=None),
            "stub approve",
            0,
        )

    @property
    def enabled(self) -> bool:
        return bool(self._injections)
