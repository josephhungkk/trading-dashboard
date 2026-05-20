import os
import ssl
from unittest.mock import MagicMock, patch


def test_cert_dsn_when_cert_env_set():
    """db.py builds SSL connect_args when PG_SSL_CERT_PATH is set."""
    env = {
        "DATABASE_URL": "postgresql+asyncpg://dashboard_user@10.10.0.2:5432/dashboard",
        "PG_SSL_CERT_PATH": "/run/secrets/pg_client.crt",
        "PG_SSL_KEY_PATH": "/run/secrets/pg_client.key",
        "PG_SSL_CA_PATH": "/run/secrets/pg_ca.crt",
    }
    mock_ctx = MagicMock(spec=ssl.SSLContext)

    with patch.dict(os.environ, env, clear=False):
        with patch("ssl.create_default_context", return_value=mock_ctx):
            import importlib

            import app.core.config as cfg_mod
            import app.core.db as db_mod

            importlib.reload(cfg_mod)
            importlib.reload(db_mod)
            from app.core.db import _build_connect_args

            args = _build_connect_args()
            assert args.get("ssl") is not None


def test_no_ssl_when_cert_env_absent():
    """db.py returns no ssl key when PG_SSL_CERT_PATH is absent."""
    env_patch = {"PG_SSL_CERT_PATH": ""}  # explicitly absent / empty
    with patch.dict(os.environ, env_patch, clear=False):
        # Temporarily clear PG_SSL_CERT_PATH from settings
        import importlib

        import app.core.config as cfg_mod

        importlib.reload(cfg_mod)
        import app.core.db as db_mod

        importlib.reload(db_mod)
        from app.core.db import _build_connect_args

        # Patch settings directly so cert_path is None
        with patch.object(cfg_mod.settings, "pg_ssl_cert_path", None):
            args = _build_connect_args()
        assert args.get("ssl") is None
