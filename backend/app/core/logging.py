"""structlog configuration with secret and account redaction.

The redaction processor scrubs secret-looking strings and account identifiers
under ``account``, ``account_number``, and ``acctNumber`` keys, including nested
dicts such as broker ``raw_payload`` values.
"""

import logging
import re
import sys
from typing import Any

import structlog

from app.core.config import settings

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}"),
    re.compile(r"api_key=[^&\s]+"),
    re.compile(r"schwab[._-]?(password|totp_secret|app_secret|refresh_token|access_token)", re.I),
]
_ACCOUNT_KEYS = frozenset(
    {
        "account",
        "account_number",
        "acctNumber",
        "nonce",
        "token",
        "jwt",
        "authorization",
        "access_token",
        "refresh_token",
    }
)
_REDACTED = "<redacted>"


def _redact_secrets(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _REDACTED if key in _ACCOUNT_KEYS else _redact_value(value)
        for key, value in event_dict.items()
    }


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        redacted = value
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub("[REDACTED]", redacted)
        return redacted
    if isinstance(value, dict):
        return {
            key: _REDACTED if key in _ACCOUNT_KEYS else _redact_value(nested)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    return value


def configure_logging() -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _redact_secrets,
    ]
    if settings.env == "dev":
        processors.append(structlog.dev.ConsoleRenderer(colors=True))
    else:
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
