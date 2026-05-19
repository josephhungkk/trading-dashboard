import pytest

from app.bot.sandbox import DenylistFinder, extract_params_schema


def test_denylist_blocks_app_api():
    finder = DenylistFinder(bot_id="test-bot")
    with pytest.raises(ImportError):
        finder.find_spec("app.api.bots", None, None)


def test_denylist_blocks_orders_service():
    finder = DenylistFinder(bot_id="test-bot")
    with pytest.raises(ImportError):
        finder.find_spec("app.services.orders_service", None, None)


def test_denylist_allows_app_bot():
    finder = DenylistFinder(bot_id="test-bot")
    result = finder.find_spec("app.bot.base", None, None)
    assert result is None


def test_extract_params_schema_returns_none_when_no_schema(tmp_path):
    strategy_file = tmp_path / "strategy_no_schema.py"
    strategy_file.write_text(
        """
from app.bot.base import BaseStrategy, BarEvent

class NoSchemaStrategy(BaseStrategy):
    async def on_start(self): pass
    async def on_bar(self, bar: BarEvent): pass
"""
    )
    result = extract_params_schema(str(strategy_file))
    assert result is None


def test_extract_params_schema_returns_schema_when_set(tmp_path):
    strategy_file = tmp_path / "strategy_with_schema.py"
    strategy_file.write_text(
        """
from app.bot.base import BaseStrategy, BarEvent

class SchemaStrategy(BaseStrategy):
    params_schema = {
        "type": "object",
        "properties": {"threshold": {"type": "number"}},
        "required": ["threshold"],
    }
    async def on_start(self): pass
    async def on_bar(self, bar: BarEvent): pass
"""
    )
    result = extract_params_schema(str(strategy_file))
    assert result is not None
    assert result["properties"]["threshold"]["type"] == "number"


def test_extract_params_schema_timeout_returns_none(tmp_path):
    strategy_file = tmp_path / "slow_strategy.py"
    strategy_file.write_text(
        """
import time
time.sleep(10)
from app.bot.base import BaseStrategy, BarEvent
class SlowStrategy(BaseStrategy):
    async def on_start(self): pass
    async def on_bar(self, bar: BarEvent): pass
"""
    )
    result = extract_params_schema(str(strategy_file), timeout=1)
    assert result is None
