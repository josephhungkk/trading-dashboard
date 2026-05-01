import inspect

import pytest

from app.services.broker_registry_factory import BrokerConfigurer, reconfigure_schwab
from app.services.brokers import BrokerSidecarClient


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    pass


def test_reconfigure_schwab_callable() -> None:
    assert inspect.iscoroutinefunction(reconfigure_schwab)


def test_broker_configurer_has_schwab_branch() -> None:
    assert hasattr(BrokerConfigurer, "_configure_schwab")
    assert inspect.iscoroutinefunction(BrokerConfigurer._configure_schwab)


def test_brokers_metadata_kwarg() -> None:
    sig = inspect.signature(BrokerSidecarClient.configure)
    assert "metadata" in sig.parameters


def test_targets_includes_schwab() -> None:
    bc = BrokerConfigurer(config_service=None, registry=None, targets={"futu", "schwab"})
    assert "schwab" in bc.targets
