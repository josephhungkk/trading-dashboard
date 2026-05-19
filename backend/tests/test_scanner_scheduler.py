from app.services.scanner.scheduler import PRESET_CRONS, ScannerScheduler


def make_scheduler():
    from unittest.mock import MagicMock

    sched = ScannerScheduler.__new__(ScannerScheduler)
    sched._scheduler = MagicMock()
    sched._svc = MagicMock()
    sched._locks = {}
    return sched


def test_cron_validation_valid():
    sched = make_scheduler()
    assert sched.validate_cron("*/5 * * * *") is True


def test_cron_validation_invalid():
    sched = make_scheduler()
    assert sched.validate_cron("not_a_cron") is False


def test_preset_shortcuts():
    assert "every_5m" in PRESET_CRONS
    assert "every_15m" in PRESET_CRONS
    assert "hourly" in PRESET_CRONS
    assert "market_open" in PRESET_CRONS
