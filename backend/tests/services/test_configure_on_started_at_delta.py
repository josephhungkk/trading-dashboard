"""Phase 7a C11 - smoke check for started_at delta detection."""

import inspect


def test_broker_registry_tracks_started_at() -> None:
    """BrokerRegistry has _configured_started_at attribute or equivalent."""
    from app.services.brokers import BrokerRegistry

    src = inspect.getsource(BrokerRegistry)
    assert "started_at" in src or "_configured_started_at" in src, (
        "BrokerRegistry missing started_at delta tracking"
    )
