"""Base classes for the bot engine: BaseStrategy ABC, BarEvent, FillEvent."""

from __future__ import annotations

import abc
import dataclasses
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from app.bot.context import BotContext
    from app.services.advisor.types import AdvisorDecision, OrderIntent


@dataclasses.dataclass(frozen=True)
class BarEvent:
    canonical_id: str
    timeframe: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    ts: datetime


@dataclasses.dataclass(frozen=True)
class FillEvent:
    order_id: UUID
    account_id: UUID
    canonical_id: str
    side: str
    qty: Decimal
    price: Decimal
    filled_at: datetime


class BaseStrategy(abc.ABC):
    """Abstract base class for all bot strategies.

    Subclasses must implement on_start() and on_bar().
    on_fill() and on_stop() are optional noop hooks.
    """

    params: dict
    accounts: list[UUID]
    ctx: BotContext
    params_schema: dict | None = None

    @abc.abstractmethod
    def on_start(self) -> None:
        """Called once when the bot engine starts the strategy."""
        ...

    @abc.abstractmethod
    def on_bar(self, bar: BarEvent) -> None:
        """Process a completed bar event."""
        ...

    def on_fill(self, fill: FillEvent) -> None:  # noqa: B027
        """Handle a fill event (noop by default)."""

    def on_stop(self) -> None:  # noqa: B027
        """Called when the bot engine stops the strategy (noop by default)."""

    def on_advisor_reject(  # noqa: B027
        self,
        intent: OrderIntent,
        decision: AdvisorDecision,
    ) -> None:
        """Called when the advisor vetoes an order. Noop by default.

        Sync hook — must not block the event loop.
        """
