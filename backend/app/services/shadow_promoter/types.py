from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ShadowMetrics(BaseModel):
    sharpe: float
    mar: float
    max_dd: float
    win_rate: float
    avg_trade_pnl: float
    total_trades: int
    window_days: int


class ShadowVsLive(BaseModel):
    shadow_bot_id: UUID
    shadow_metrics: ShadowMetrics
    live_metrics: ShadowMetrics
    delta: dict[str, str]
    comparison_ready: bool


class ShadowComparisonReport(BaseModel):
    live_bot_id: UUID
    shadows: list[ShadowVsLive]
    generated_at: datetime


class ShadowPromotionEvent(BaseModel):
    shadow_bot_id: UUID
    live_bot_id: UUID
    promoted_by: str
    comparison_window_days: int
    comparison_window_start: datetime
    shadow_metrics: ShadowMetrics
    live_metrics: ShadowMetrics
