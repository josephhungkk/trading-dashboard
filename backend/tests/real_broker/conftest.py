"""Real-broker test gates.

Tests under backend/tests/real_broker/ require live broker credentials and
sandbox access. They are gated behind pytest markers (``real_schwab``,
``real_futu``, ``real_ibkr``, ``real_alpaca_equity``).

Phase 11a CI-debt (2026-05-13): gating shifted from env vars to DB rows.
The conftest now reads from ``DATABASE_URL`` (which the test harness points
at ``test_postgres`` after ``copy-prod-creds-to-test-pg.sh`` mirrors prod
``app_secrets`` + ``app_config``). Env vars are kept as a fallback so
ad-hoc local runs still work without a DB.

A test marker auto-skips when none of its required secrets are present
(neither in DB nor env). Tests that previously read ``os.environ[X]`` can
keep doing that; the env-var fallback path lets the harness export
DB-sourced values to ``os.environ`` before tests run (see
``_export_db_secrets_to_env``).

Phase 10a.5.1 C4: this directory is a standalone uv project (see
``pyproject.toml`` here). The sys.path insert below makes ``app.*`` imports
resolve to ``backend/app/`` when this project is invoked from
``backend/tests/real_broker/`` (nightly workflows; local pytest from
``backend/`` works without this).
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest  # sys.path insert must precede app.* imports

# Marker -> required (secret_slots, config_slots, env_fallbacks). A marker
# is enabled when EVERY secret_slot and config_slot resolves (DB-or-env).
# env_fallbacks names the legacy env var to populate so existing test
# code that reads os.environ[...] keeps working.
_MarkerSpec = dict[str, list[tuple[str, str, str]]]
"""Each tuple is (namespace, key, env_var_name)."""

_SCHWAB: _MarkerSpec = {
    "secrets": [
        ("broker", "schwab.app_key", "SCHWAB_APP_KEY"),
        ("broker", "schwab.app_secret", "SCHWAB_APP_SECRET"),
    ],
    "config": [
        ("testing", "schwab_paper_account_hash", "SCHWAB_PAPER_ACCOUNT_HASH"),
    ],
}
_FUTU: _MarkerSpec = {
    "secrets": [
        ("broker", "futu.rsa_priv_pem", "FUTU_RSA_PRIV_PEM"),
    ],
    "config": [
        ("broker", "futu.opend_host", "FUTU_HOST"),
        ("broker", "futu.opend_port", "FUTU_PORT"),
    ],
}
_ALPACA_EQUITY: _MarkerSpec = {
    "secrets": [
        ("broker", "alpaca-paper.api_key", "ALPACA_PAPER_API_KEY"),
        ("broker", "alpaca-paper.api_secret", "ALPACA_PAPER_API_SECRET"),
    ],
    "config": [],
}
_IBKR: _MarkerSpec = {
    "secrets": [
        ("broker", "mtls.client_cert_pem", "IBKR_MTLS_CLIENT_CERT_PEM"),
        ("broker", "mtls.client_key_pem", "IBKR_MTLS_CLIENT_KEY_PEM"),
        ("broker", "mtls.ca_bundle_pem", "IBKR_MTLS_CA_BUNDLE_PEM"),
    ],
    "config": [
        ("testing", "ibkr_paper_account", "IBKR_PAPER_ACCOUNT"),
    ],
}

_MARKERS: dict[str, _MarkerSpec] = {
    "real_schwab": _SCHWAB,
    "real_futu": _FUTU,
    "real_alpaca_equity": _ALPACA_EQUITY,
    "real_ibkr": _IBKR,
}

# Cache the (db-resolved) values across test items so we don't re-query
# the DB once per test. None = "absent", str = "present, this is the value".
_resolved_secrets: dict[tuple[str, str], str | None] = {}
_resolved_config: dict[tuple[str, str], str | None] = {}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--case",
        action="store",
        default="market_spy",
        help=(
            "real-broker scenario name "
            "(market_spy | trail_amount_spy | gtd_limit_spy | "
            "trail_percent_spy | moc_spy | gtd_limit_spy | limit_spy | trail_spy)"
        ),
    )


@pytest.fixture
def case(request: pytest.FixtureRequest) -> str:
    return request.config.getoption("--case")  # type: ignore[no-any-return]


async def _load_db_values(
    slots: Iterable[tuple[str, str]], kind: str
) -> dict[tuple[str, str], str | None]:
    """Reveal each (ns, key) via ConfigService. Returns ``None`` for absent
    rows or rows that decrypt to the literal placeholder ``REPLACE_ME``
    (so half-seeded prod doesn't masquerade as ready).

    ``kind`` is "secret" or "config".
    """
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.core.crypto import get_fernet
    from app.services.config import ConfigService
    from app.services.config_cache import ConfigCache

    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    config_cache = ConfigCache(redis, "config:invalidate", "config", ttl_seconds=300)
    secrets_cache = ConfigCache(redis, "config:invalidate:secrets", "secret", ttl_seconds=300)
    fernet = get_fernet(settings.secret_key, settings.secret_key_prev)
    cfg = ConfigService(session_factory, config_cache, secrets_cache, fernet)

    out: dict[tuple[str, str], str | None] = {}
    try:
        for ns, key in slots:
            try:
                if kind == "secret":
                    val = await cfg.reveal_secret(ns, key)
                else:
                    val = await cfg.get(ns, key)
            except Exception:
                val = None
            if val is None or val == "REPLACE_ME":
                out[(ns, key)] = None
            else:
                out[(ns, key)] = str(val)
    finally:
        await engine.dispose()
        await redis.aclose()
    return out


def _resolve_marker(spec: _MarkerSpec) -> tuple[bool, list[str]]:
    """Return (all_present, list_of_missing_descriptions)."""
    missing: list[str] = []
    for ns, key, env_var in spec["secrets"]:
        slot = (ns, key)
        db_val = _resolved_secrets.get(slot)
        env_val = os.environ.get(env_var, "")
        if db_val is None and not env_val:
            missing.append(f"app_secrets[{ns}/{key}] or env {env_var}")
        elif db_val is not None and not env_val:
            # Export DB-sourced value so legacy test code that reads
            # os.environ[X] continues to work.
            os.environ[env_var] = db_val
    for ns, key, env_var in spec["config"]:
        slot = (ns, key)
        db_val = _resolved_config.get(slot)
        env_val = os.environ.get(env_var, "")
        if db_val is None and not env_val:
            missing.append(f"app_config[{ns}/{key}] or env {env_var}")
        elif db_val is not None and not env_val:
            os.environ[env_var] = db_val
    return (not missing, missing)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # Collect every (ns, key) we might need across all markers, then do
    # one DB pass per kind.
    all_secret_slots: set[tuple[str, str]] = set()
    all_config_slots: set[tuple[str, str]] = set()
    for spec in _MARKERS.values():
        all_secret_slots.update((ns, key) for ns, key, _ in spec["secrets"])
        all_config_slots.update((ns, key) for ns, key, _ in spec["config"])

    try:
        _resolved_secrets.update(asyncio.run(_load_db_values(all_secret_slots, "secret")))
        _resolved_config.update(asyncio.run(_load_db_values(all_config_slots, "config")))
    except Exception as exc:
        # If the DB is unreachable, treat every slot as absent. Tests will
        # fall back to env vars or skip.
        for slot in all_secret_slots:
            _resolved_secrets.setdefault(slot, None)
        for slot in all_config_slots:
            _resolved_config.setdefault(slot, None)
        # Surface once so the run log shows why everything skipped.
        config.pluginmanager.get_plugin("terminalreporter").write_line(  # type: ignore[union-attr]
            f"[real_broker] DB lookup failed; falling back to env-only gates: {exc!r}",
            yellow=True,
        )

    for marker, spec in _MARKERS.items():
        present, missing = _resolve_marker(spec)
        if present:
            continue
        skip_mark = pytest.mark.skip(reason=f"{marker} tests need: {'; '.join(missing)}")
        for item in items:
            if marker in item.keywords:
                item.add_marker(skip_mark)
