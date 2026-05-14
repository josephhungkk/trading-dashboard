"""Migration 0047 round-trip test."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from alembic.config import Config

from alembic import command
from app.core.config import settings

pytestmark = [pytest.mark.migrations]


@dataclass(frozen=True)
class _AlembicRunner:
    cfg: Config

    def migrate_up_to(self, revision: str) -> None:
        command.upgrade(self.cfg, revision)

    def migrate_down_one(self) -> None:
        command.downgrade(self.cfg, "-1")

    def migrate_up_one(self) -> None:
        command.upgrade(self.cfg, "+1")


@pytest.fixture
def alembic_runner() -> _AlembicRunner:
    cfg = Config("alembic.ini")
    cfg.config_file_name = None
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", settings.database_url.replace("+asyncpg", ""))
    return _AlembicRunner(cfg)


def test_0047_upgrade_downgrade(alembic_runner: _AlembicRunner) -> None:
    alembic_runner.migrate_up_to("0047_phase12_options")
    alembic_runner.migrate_down_one()
    alembic_runner.migrate_up_one()


def test_asset_class_option_exists() -> None:
    from app.models.instruments import AssetClass

    assert AssetClass.OPTION == "OPTION"
