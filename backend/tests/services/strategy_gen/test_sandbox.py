import pytest

from app.services.strategy_gen.sandbox import SandboxValidator, compute_source_hash

pytestmark = pytest.mark.no_db

VALID_CODE = """
import math

from app.bot.base import BaseStrategy, BarEvent


class GeneratedStrategy(BaseStrategy):
    def on_start(self) -> None:
        self.started = True

    def on_bar(self, bar: BarEvent) -> None:
        value = math.sqrt(float(bar.close))
        self.last_value = value
"""


def test_valid_code_passes() -> None:
    result = SandboxValidator().validate_code(VALID_CODE)

    assert result.ok


def test_syntax_error_rejected() -> None:
    result = SandboxValidator().validate_code("def foo(:")

    assert not result.ok
    assert "syntax" in result.errors[0]


def test_prohibited_import_rejected() -> None:
    result = SandboxValidator().validate_code("import os")

    assert not result.ok
    assert "os" in " ".join(result.errors)


def test_eval_call_rejected() -> None:
    result = SandboxValidator().validate_code("def foo():\n    eval('1 + 1')")

    assert not result.ok


def test_exec_call_rejected() -> None:
    result = SandboxValidator().validate_code("def foo():\n    exec('x = 1')")

    assert not result.ok


def test_open_call_rejected() -> None:
    result = SandboxValidator().validate_code("def foo():\n    open('/tmp/x')")

    assert not result.ok


def test_allowlist_import_passes() -> None:
    result = SandboxValidator().validate_code("import math\nimport decimal")

    assert result.ok


def test_subclasses_reflection_rejected() -> None:
    result = SandboxValidator().validate_code("().__class__.__mro__[-1].__subclasses__()")

    assert not result.ok


def test_compute_source_hash_deterministic() -> None:
    first = compute_source_hash("import math\n")
    second = compute_source_hash("import math\n")

    assert first == second
    assert len(first) == 64
    assert all(char in "0123456789abcdef" for char in first)


def test_custom_allowed_imports() -> None:
    result = SandboxValidator(allowed_imports=["math"]).validate_code("import pandas")

    assert not result.ok
