from __future__ import annotations

import pytest

from app.bot.strategy_loader import StrategyLoadError
from app.services.strategy_gen.sandbox import SandboxValidator, compute_source_hash

pytestmark = pytest.mark.no_db


def test_strategy_load_error_is_exception() -> None:
    exc = StrategyLoadError("test error")
    assert str(exc) == "test error"
    assert isinstance(exc, Exception)


def test_sandbox_validator_valid_code() -> None:
    source = """
import math

class MyStrategy:
    def on_start(self):
        self.started = True
    def on_bar(self, bar):
        v = math.sqrt(float(bar.close))
"""
    validator = SandboxValidator()
    result = validator.validate_code(source)
    assert result.ok


def test_sandbox_validator_prohibited_os() -> None:
    source = "import os\n\ndef foo(): pass"
    result = SandboxValidator().validate_code(source)
    assert not result.ok
    assert any("os" in e for e in result.errors)


def test_sandbox_validator_subclasses_rejected() -> None:
    source = "x = ().__class__.__mro__[-1].__subclasses__()"
    result = SandboxValidator().validate_code(source)
    assert not result.ok


def test_compute_source_hash_sha256() -> None:
    h = compute_source_hash("hello")
    assert len(h) == 64
    assert h == compute_source_hash("hello")
    assert h != compute_source_hash("world")


def test_compute_source_hash_encoding() -> None:
    h1 = compute_source_hash("import math\n")
    h2 = compute_source_hash("import math\n")
    assert h1 == h2
