"""Phase 7a C3 — refresh_with_lock smoke check (real PG tests in CI/C4)."""

from app.services.schwab_oauth import (
    SCHWAB_REFRESH_LOCK_ID,
    _persist_tokens_under_lock,
    refresh_with_lock,
    schwab_refresh_lock,
)


def test_advisory_lock_id_is_positive_int32():
    assert SCHWAB_REFRESH_LOCK_ID > 0
    assert SCHWAB_REFRESH_LOCK_ID < 0x80000000


def test_refresh_with_lock_callable():
    """Just ensure the symbol exists and is awaitable."""
    import inspect

    assert inspect.iscoroutinefunction(refresh_with_lock)


def test_helper_symbols_present():
    """Smoke — internal symbols exist for use by C4 OAuth-code-exchange."""
    import inspect

    assert inspect.iscoroutinefunction(_persist_tokens_under_lock)
    # asynccontextmanager-wrapped function exists
    assert callable(schwab_refresh_lock)
