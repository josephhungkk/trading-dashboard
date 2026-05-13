import json
from unittest.mock import AsyncMock

import pytest

from app.services.alerts.channels.in_app import InAppChannel
from app.services.alerts.delivery import AlertFire, DeliveryDispatcher, DeliveryOutcome


@pytest.mark.asyncio
async def test_in_app_publishes_to_redis() -> None:
    redis = AsyncMock()
    channel = InAppChannel(redis=redis)
    fire = AlertFire(
        fire_id=1,
        alert_id=42,
        jwt_subject="user-1",
        verdict="true",
        evaluated_values={"close": 201.5},
        user_label="AAPL above 200",
    )
    result = await channel.deliver(fire, config={})
    assert result is DeliveryOutcome.sent
    redis.publish.assert_called_once()
    channel_name, payload = redis.publish.call_args.args
    assert channel_name == "alerts:fire:user-1"
    body = json.loads(payload)
    assert body["alert_id"] == 42
    assert body["user_label"] == "AAPL above 200"


@pytest.mark.asyncio
async def test_dispatcher_fans_out_per_channel_isolated() -> None:
    success_channel = AsyncMock()
    success_channel.name = "in_app"
    success_channel.deliver.return_value = DeliveryOutcome.sent

    failing_channel = AsyncMock()
    failing_channel.name = "webhook"
    failing_channel.deliver.side_effect = RuntimeError("network")

    dispatcher = DeliveryDispatcher(
        channels={"in_app": success_channel, "webhook": failing_channel}
    )
    fire = AlertFire(
        fire_id=1,
        alert_id=42,
        jwt_subject="u",
        verdict="true",
        evaluated_values={},
        user_label="x",
    )
    outcomes = await dispatcher.fan_out(fire, channel_keys=["in_app", "webhook"])
    assert outcomes["in_app"] is DeliveryOutcome.sent
    assert outcomes["webhook"] is DeliveryOutcome.failed


from unittest.mock import patch  # noqa: E402

from app.services.alerts.channels.webhook import WebhookChannel  # noqa: E402


@pytest.mark.asyncio
async def test_webhook_5xx_exhausts_retries() -> None:
    http = AsyncMock()
    http.post.return_value = type("R", (), {"status_code": 503})()
    channel = WebhookChannel(http_client=http, per_fire_budget_s=60.0)
    fire = AlertFire(
        fire_id=1,
        alert_id=1,
        jwt_subject="u",
        verdict="true",
        evaluated_values={},
        user_label="x",
    )
    with patch("app.services.alerts.channels.webhook._validate_url"):
        with patch("asyncio.sleep", new=AsyncMock()):
            outcome = await channel.deliver(
                fire,
                config={"url": "https://x.com", "secret": "s", "id": "w1"},
            )
    assert outcome is DeliveryOutcome.failed
    assert http.post.call_count == 4


@pytest.mark.asyncio
async def test_webhook_4xx_no_retry() -> None:
    http = AsyncMock()
    http.post.return_value = type("R", (), {"status_code": 401})()
    channel = WebhookChannel(http_client=http, per_fire_budget_s=60.0)
    fire = AlertFire(
        fire_id=1,
        alert_id=1,
        jwt_subject="u",
        verdict="true",
        evaluated_values={},
        user_label="x",
    )
    with patch("app.services.alerts.channels.webhook._validate_url"):
        outcome = await channel.deliver(
            fire,
            config={"url": "https://x.com", "secret": "s", "id": "w1"},
        )
    assert outcome is DeliveryOutcome.failed
    assert http.post.call_count == 1


@pytest.mark.asyncio
async def test_webhook_signs_with_hmac_sha256_over_body() -> None:
    """Codex chunk-C test-gap MED — assert HMAC signature is computed over
    the exact body bytes with the configured secret using SHA-256."""
    import hashlib
    import hmac

    captured: dict[str, object] = {}

    async def _capture_post(
        url: str,
        *,
        content: bytes,
        headers: dict[str, str],
        timeout: float,  # noqa: ASYNC109 — mimics httpx.post signature
    ) -> object:
        captured["url"] = url
        captured["content"] = content
        captured["headers"] = headers
        return type("R", (), {"status_code": 200})()

    http = AsyncMock()
    http.post.side_effect = _capture_post

    async def _resolver(host: str) -> list[str]:
        return ["8.8.8.8"]

    channel = WebhookChannel(http_client=http, per_fire_budget_s=60.0, resolver=_resolver)
    fire = AlertFire(
        fire_id=11,
        alert_id=22,
        jwt_subject="u",
        verdict="true",
        evaluated_values={"close": 201.5},
        user_label="hmac",
        fired_at_iso="2026-05-13T20:00:00+00:00",
    )
    outcome = await channel.deliver(
        fire, config={"url": "https://public.example/api", "secret": "shh", "id": "w1"}
    )
    assert outcome is DeliveryOutcome.sent
    sent_headers = captured["headers"]
    assert isinstance(sent_headers, dict)
    assert sent_headers["Content-Type"] == "application/json"
    sent_body = captured["content"]
    assert isinstance(sent_body, bytes)
    expected = hmac.new(b"shh", sent_body, hashlib.sha256).hexdigest()
    assert sent_headers["X-Alerts-Signature"] == expected
    # Host header pins to the original hostname (SNI + cert verification).
    assert sent_headers["Host"] == "public.example"
    # URL netloc was rewritten to the resolved IP — TOCTOU-closure assertion.
    assert "//8.8.8.8" in str(captured["url"])


@pytest.mark.asyncio
async def test_webhook_validates_on_every_retry() -> None:
    """Codex chunk-C test-gap MED — the SSRF check must run on every retry so
    a DNS rebind between attempts can't slip a private IP through."""
    http = AsyncMock()
    http.post.return_value = type("R", (), {"status_code": 503})()

    call_count = {"n": 0}

    async def _flipping_resolver(host: str) -> list[str]:
        call_count["n"] += 1
        # Attempt 1: public IP; attempt 2: rebind to RFC1918.
        return ["8.8.8.8"] if call_count["n"] == 1 else ["10.10.0.2"]

    channel = WebhookChannel(http_client=http, per_fire_budget_s=60.0, resolver=_flipping_resolver)
    fire = AlertFire(
        fire_id=1,
        alert_id=1,
        jwt_subject="u",
        verdict="true",
        evaluated_values={},
        user_label="x",
    )
    with patch("asyncio.sleep", new=AsyncMock()):
        outcome = await channel.deliver(
            fire,
            config={"url": "https://rebind.example/h", "secret": "s", "id": "w1"},
        )
    assert outcome is DeliveryOutcome.failed
    # First attempt got through validation + POSTed; second attempt's resolver
    # returned a private IP so we never POST again.
    assert http.post.call_count == 1
    assert call_count["n"] == 2
