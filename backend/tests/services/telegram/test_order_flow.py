"""Tests for telegram order_flow module."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.no_db


def test_telegram_order_metrics_registered() -> None:
    from app.core import metrics

    assert hasattr(metrics, "TELEGRAM_ORDER_ATTEMPTS_TOTAL")
    assert hasattr(metrics, "TELEGRAM_ORDER_PREVIEWS_TOTAL")
    assert hasattr(metrics, "TELEGRAM_ORDER_CONFIRMS_TOTAL")
    assert hasattr(metrics, "TELEGRAM_ORDER_CANCELS_TOTAL")
    assert hasattr(metrics, "TELEGRAM_RATE_LIMITER_TRADE_BLOCK_TOTAL")
    assert hasattr(metrics, "TELEGRAM_ORDER_E2E_SECONDS")
