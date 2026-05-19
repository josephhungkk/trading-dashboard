from app.services.advisor.prompts import (
    ALLOWED_ADVICE_TAGS,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
)


def test_prompt_version_present():
    assert isinstance(PROMPT_VERSION, int)
    assert PROMPT_VERSION >= 1


def test_system_prompt_contains_context_fences():
    assert "<<BEGIN_CONTEXT>>" in SYSTEM_PROMPT
    assert "<<END_CONTEXT>>" in SYSTEM_PROMPT


def test_system_prompt_contains_injection_warning():
    assert "prompt injection" in SYSTEM_PROMPT.lower() or "inject" in SYSTEM_PROMPT.lower()


def test_allowed_advice_tags_covers_expected_values():
    expected = {
        "earnings_window",
        "concentration_risk",
        "liquidity_risk",
        "regime_mismatch",
        "stop_too_wide",
        "stop_too_tight",
        "size_too_large",
        "correlated_exposure",
        "low_quality_signal",
        "overtrading",
        "drawdown_breach",
        "other",
    }
    assert expected.issubset(ALLOWED_ADVICE_TAGS)
