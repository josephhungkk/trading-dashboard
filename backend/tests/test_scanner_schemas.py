import pytest

from app.services.scanner.schemas import (
    ScanConfig,
    UniverseConfig,
)


def test_universe_config_schwab_screener():
    u = UniverseConfig(type="schwab_screener", params={"market": "US"})
    assert u.type == "schwab_screener"


def test_universe_config_tickers():
    u = UniverseConfig(type="tickers", params={"tickers": ["AAPL", "TSLA"]})
    assert u.params["tickers"] == ["AAPL", "TSLA"]


def test_scan_config_defaults():
    cfg = ScanConfig(
        name="RSI scan",
        universe_config=UniverseConfig(type="tickers", params={"tickers": ["AAPL"]}),
        rule_expr="rsi(14) < 30",
        llm_depth="quick",
    )
    assert cfg.schedule is None
    assert cfg.market_hours_gate is False
    assert cfg.enabled is True


def test_scan_config_invalid_llm_depth():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ScanConfig(
            name="x",
            universe_config=UniverseConfig(type="tickers", params={}),
            rule_expr="rsi(14) < 30",
            llm_depth="ultra",  # type: ignore[arg-type]
        )
