"""Phase 11b-C2: SSRF defence tests for the webhook channel.

Architect CRIT-1: the user-supplied webhook URL must be validated for
scheme + hostname + IP class + port BEFORE any HTTP call is issued, and
re-validated on every retry to defeat DNS rebinding.
"""

from __future__ import annotations

import pytest

from app.services.alerts.channels.webhook import _validate_url
from app.services.alerts.exceptions import WebhookUrlRejected


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/hook",
        "ftp://example.com/hook",
        "https://localhost/hook",
        "https://litellm:4000/hook",
        "https://10.10.0.2/hook",
        "https://127.0.0.1/hook",
        "https://192.168.1.1/hook",
        "https://169.254.169.254/hook",
        "https://[::1]/hook",
        "https://example.local/hook",
        "https://example.internal/hook",
        "https://example.com:22/hook",
    ],
)
def test_validate_url_rejects(url: str) -> None:
    with pytest.raises(WebhookUrlRejected):
        _validate_url(url, _resolver=lambda h: ["8.8.8.8"])


def test_validate_url_rejects_dns_rebind() -> None:
    with pytest.raises(WebhookUrlRejected):
        _validate_url(
            "https://attacker.example.com/hook",
            _resolver=lambda h: ["10.10.0.2"],
        )


def test_validate_url_accepts_public_https() -> None:
    _validate_url(
        "https://api.pushover.net/1/messages.json",
        _resolver=lambda h: ["8.8.8.8"],
    )
