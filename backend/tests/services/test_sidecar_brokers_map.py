"""Phase 7a A5 — SIDECAR_BROKERS includes schwab; per-label host override works."""

from app.services.broker_registry_factory import (
    SIDECAR_BROKERS,
    SIDECAR_HOSTS,
    SIDECAR_PORTS,
    resolve_target,
)


def test_schwab_in_sidecar_brokers():
    assert SIDECAR_BROKERS["schwab"] == "schwab"
    assert SIDECAR_PORTS["schwab"] == 9090
    assert SIDECAR_HOSTS["schwab"] == "schwab-sidecar"


def test_existing_brokers_unchanged():
    """Don't break Phase 4 + 6 wiring."""
    assert SIDECAR_BROKERS["isa-live"] == "ibkr"
    assert SIDECAR_BROKERS["isa-paper"] == "ibkr"
    assert SIDECAR_BROKERS["normal-live"] == "ibkr"
    assert SIDECAR_BROKERS["normal-paper"] == "ibkr"
    assert SIDECAR_BROKERS["futu"] == "futu"
    assert SIDECAR_PORTS["isa-live"] == 18001
    assert SIDECAR_PORTS["futu"] == 18005


def test_resolve_target_falls_back_to_default_host():
    """IBKR labels have no SIDECAR_HOSTS entry → use the default."""
    assert resolve_target("isa-live", default_host="10.10.0.2") == "10.10.0.2:18001"
    assert resolve_target("futu", default_host="10.10.0.2") == "10.10.0.2:18005"


def test_resolve_target_uses_per_label_host_override():
    """Schwab is overridden to schwab-sidecar (docker-compose hostname)."""
    assert resolve_target("schwab", default_host="10.10.0.2") == "schwab-sidecar:9090"
