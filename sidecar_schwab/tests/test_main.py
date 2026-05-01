"""Phase 7a A4 — config.resolve_port respects env override + falls back safely."""
import logging


def test_resolve_port_default(monkeypatch):
    monkeypatch.delenv("SCHWAB_SIDECAR_PORT", raising=False)
    from sidecar_schwab.config import resolve_port
    assert resolve_port() == 9090


def test_resolve_port_override(monkeypatch):
    monkeypatch.setenv("SCHWAB_SIDECAR_PORT", "12345")
    from sidecar_schwab.config import resolve_port
    assert resolve_port() == 12345


def test_resolve_port_invalid_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("SCHWAB_SIDECAR_PORT", "not-a-number")
    caplog.set_level(logging.WARNING)
    from sidecar_schwab.config import resolve_port
    assert resolve_port() == 9090
    assert "invalid schwab_sidecar_port" in caplog.text.lower()
