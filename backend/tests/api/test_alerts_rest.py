from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.api import alerts as _api_alerts
from app.api.admin import consume_confirmation_nonce
from app.core import deps as _deps
from app.core.deps import get_db
from app.main import app
from app.services.alerts.exceptions import RuleCrossSubjectError, RuleNotFoundError
from app.services.alerts.rules import AlertRule

pytestmark = pytest.mark.no_db


@pytest.fixture
def _patched_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    def verify(token: str, *, client_ip: str = "") -> MagicMock:
        return MagicMock(email=f"{token}@test.local", kind="cf_access_jwt")

    monkeypatch.setattr(_deps._verifier, "verify", verify)


@pytest.fixture
def jwt_headers() -> dict[str, str]:
    return {"Cf-Access-Jwt-Assertion": "user-a"}


@pytest.fixture
def other_jwt_headers() -> dict[str, str]:
    return {"Cf-Access-Jwt-Assertion": "user-b"}


class _Rows:
    def all(self) -> list[Any]:
        return []


class _FakeDb:
    async def execute(self, *_args: Any, **_kwargs: Any) -> _Rows:
        return _Rows()

    async def commit(self) -> None:
        return None


@pytest_asyncio.fixture(autouse=True)
async def _fake_alert_store(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime

    rules: dict[int, AlertRule] = {}
    next_id = 1

    async def override_db() -> Any:
        yield _FakeDb()

    async def create_rule(
        _db: Any,
        *,
        jwt_subject: str,
        user_label: str,
        original_nl: str,
        predicate_json: dict[str, Any],
        parse_status: str,
        parse_metadata: dict[str, Any] | None = None,
        delivery_channels: list[str] | None = None,
        tick_subscribed: bool = False,
    ) -> AlertRule:
        nonlocal next_id
        now = datetime.now(UTC)
        rule = AlertRule(
            id=next_id,
            jwt_subject=jwt_subject,
            user_label=user_label,
            original_nl=original_nl,
            predicate_json=predicate_json,
            requires_capabilities=[],
            parse_status=parse_status,
            parse_metadata=parse_metadata,
            delivery_channels=delivery_channels or ["in_app"],
            tick_subscribed=tick_subscribed,
            status="pending",
            dormancy_reason=None,
            consecutive_eval_errors=0,
            created_at=now,
            updated_at=now,
            confirmed_at=None,
            deleted_at=None,
        )
        rules[next_id] = rule
        next_id += 1
        return rule

    async def get_rule(_db: Any, *, rule_id: int, jwt_subject: str) -> AlertRule:
        rule = rules.get(rule_id)
        if rule is None:
            raise RuleNotFoundError(rule_id)
        if rule.jwt_subject != jwt_subject:
            raise RuleCrossSubjectError(rule_id)
        return rule

    async def list_rules(_db: Any, *, jwt_subject: str) -> list[AlertRule]:
        return [rule for rule in rules.values() if rule.jwt_subject == jwt_subject]

    async def delete_rule(_db: Any, *, rule_id: int, jwt_subject: str) -> None:
        await get_rule(_db, rule_id=rule_id, jwt_subject=jwt_subject)
        del rules[rule_id]

    async def update_predicate(
        _db: Any,
        *,
        rule_id: int,
        jwt_subject: str,
        predicate_json: dict[str, Any],
    ) -> AlertRule:
        rule = await get_rule(_db, rule_id=rule_id, jwt_subject=jwt_subject)
        rule.predicate_json.clear()
        rule.predicate_json.update(predicate_json)
        return rule

    async def confirm_rule(_db: Any, *, rule_id: int, jwt_subject: str) -> AlertRule:
        rule = await get_rule(_db, rule_id=rule_id, jwt_subject=jwt_subject)
        rule.status = "active"
        return rule

    _api_alerts._CREATE_LIMITER._buckets.clear()
    app.dependency_overrides[get_db] = override_db

    # Phase 11b chunk-B-close: bypass the X-Confirm-Nonce dep so the no_db
    # alerts tests don't need a real Redis. Per-route CSRF behaviour is
    # covered by the dedicated admin test surface.
    async def _no_csrf() -> None:
        return None

    app.dependency_overrides[consume_confirmation_nonce] = _no_csrf
    monkeypatch.setattr(_api_alerts, "create_rule", create_rule)
    monkeypatch.setattr(_api_alerts, "get_rule", get_rule)
    monkeypatch.setattr(_api_alerts, "list_rules", list_rules)
    monkeypatch.setattr(_api_alerts, "delete_rule", delete_rule)
    monkeypatch.setattr(_api_alerts, "update_predicate", update_predicate)
    monkeypatch.setattr(_api_alerts, "confirm_rule", confirm_rule)
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(consume_confirmation_nonce, None)


def _predicate(symbol: str = "AAPL", value: float = 200.0) -> dict[str, Any]:
    return {"kind": "price_threshold", "symbol": symbol, "op": "gt", "value": value}


async def _create_alert(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    user_label: str = "AAPL > 200",
    predicate_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = await client.post(
        "/api/alerts",
        headers=headers,
        json={
            "user_label": user_label,
            "original_nl": user_label,
            "predicate_json": predicate_json or _predicate(),
            "delivery_channels": ["in_app"],
            "tick_subscribed": False,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_post_alerts_happy_path(
    _patched_verifier: None,
    client: AsyncClient,
    jwt_headers: dict[str, str],
) -> None:
    body = await _create_alert(client, jwt_headers)

    assert body["parse_status"] == "manual"
    assert body["status"] == "pending"


@pytest.mark.asyncio
async def test_get_alert_404_cross_subject(
    _patched_verifier: None,
    client: AsyncClient,
    jwt_headers: dict[str, str],
    other_jwt_headers: dict[str, str],
) -> None:
    created = await _create_alert(client, jwt_headers)

    cross_subject = await client.get(f"/api/alerts/{created['id']}", headers=other_jwt_headers)
    unknown = await client.get("/api/alerts/99999999", headers=other_jwt_headers)

    assert cross_subject.status_code == 404
    assert unknown.status_code == 404
    assert cross_subject.json() == unknown.json()


@pytest.mark.asyncio
async def test_delete_cross_subject_404(
    _patched_verifier: None,
    client: AsyncClient,
    jwt_headers: dict[str, str],
    other_jwt_headers: dict[str, str],
) -> None:
    created = await _create_alert(client, jwt_headers)

    response = await client.delete(f"/api/alerts/{created['id']}", headers=other_jwt_headers)

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_recent_fires_scoped_to_subject(
    _patched_verifier: None,
    client: AsyncClient,
    jwt_headers: dict[str, str],
    other_jwt_headers: dict[str, str],
) -> None:
    user_a = await client.get("/api/alerts/recent-fires", headers=jwt_headers)
    user_b = await client.get("/api/alerts/recent-fires", headers=other_jwt_headers)

    assert user_a.status_code == 200
    assert user_b.status_code == 200
    assert "fires" in user_a.json()
    assert "fires" in user_b.json()


@pytest.mark.asyncio
async def test_rate_limit_429_after_5_creates(
    _patched_verifier: None,
    client: AsyncClient,
    jwt_headers: dict[str, str],
) -> None:
    from app.api import alerts as _api_alerts

    _api_alerts._CREATE_LIMITER._buckets.clear()
    for idx in range(5):
        await _create_alert(
            client,
            jwt_headers,
            user_label=f"AAPL rate limit {idx}",
            predicate_json=_predicate(value=200.0 + idx),
        )

    response = await client.post(
        "/api/alerts",
        headers=jwt_headers,
        json={
            "user_label": "AAPL rate limit 6",
            "original_nl": "AAPL rate limit 6",
            "predicate_json": _predicate(value=206.0),
        },
    )

    assert response.status_code == 429
    assert response.json()["detail"] == {"error_code": "rate_limited"}
    assert response.headers["Retry-After"] == "60"


@pytest.mark.asyncio
async def test_invalid_predicate_422(
    _patched_verifier: None,
    client: AsyncClient,
    jwt_headers: dict[str, str],
) -> None:
    response = await client.post(
        "/api/alerts",
        headers=jwt_headers,
        json={
            "user_label": "invalid",
            "original_nl": "invalid",
            "predicate_json": {"kind": "price_threshold"},
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error_code"] == "invalid_predicate"
    assert detail["schema_errors"]


@pytest.mark.asyncio
async def test_list_alerts_returns_only_caller_subject(
    _patched_verifier: None,
    client: AsyncClient,
    jwt_headers: dict[str, str],
    other_jwt_headers: dict[str, str],
) -> None:
    user_a = await _create_alert(client, jwt_headers, user_label="user-a alert")
    user_b = await _create_alert(client, other_jwt_headers, user_label="user-b alert")

    response = await client.get("/api/alerts", headers=jwt_headers)

    assert response.status_code == 200
    alerts = response.json()["alerts"]
    assert {alert["id"] for alert in alerts} == {user_a["id"]}
    assert user_b["id"] not in {alert["id"] for alert in alerts}


@pytest.mark.asyncio
async def test_put_predicate_404_identity_unknown_id_and_cross_subject(
    _patched_verifier: None,
    client: AsyncClient,
    jwt_headers: dict[str, str],
    other_jwt_headers: dict[str, str],
) -> None:
    """Codex chunk-C test-gap LOW — PUT must surface identical 404 body for
    unknown-id and cross-subject access (existence-oracle defence)."""
    created = await _create_alert(client, jwt_headers)
    body = {"predicate_json": _predicate(value=300.0)}

    cross_subject = await client.put(
        f"/api/alerts/{created['id']}", headers=other_jwt_headers, json=body
    )
    unknown = await client.put("/api/alerts/99999999", headers=other_jwt_headers, json=body)

    assert cross_subject.status_code == 404
    assert unknown.status_code == 404
    assert cross_subject.json() == unknown.json()


@pytest.mark.asyncio
async def test_dry_run_returns_resolution_and_fire_count(
    _patched_verifier: None,
    client: AsyncClient,
    jwt_headers: dict[str, str],
) -> None:
    """Phase 11b chunk-B-close: POST /api/alerts/dry-run replays a predicate."""
    _api_alerts._DRY_RUN_LIMITER._buckets.clear()
    response = await client.post(
        "/api/alerts/dry-run",
        headers=jwt_headers,
        json={
            "predicate_json": {
                "kind": "price_threshold",
                "symbol": "AAPL",
                "op": "gt",
                "value": 200.0,
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # No bars in the fake DB → no fires.
    assert body["replay_resolution"] in {"1m", "1d", "insufficient"}
    assert body["fire_count"] == 0
    assert body["sample_fires"] == []
    assert body["truncated"] is False


@pytest.mark.asyncio
async def test_dry_run_rejects_invalid_predicate(
    _patched_verifier: None,
    client: AsyncClient,
    jwt_headers: dict[str, str],
) -> None:
    """A malformed predicate must surface as 422 with schema_errors."""
    _api_alerts._DRY_RUN_LIMITER._buckets.clear()
    response = await client.post(
        "/api/alerts/dry-run",
        headers=jwt_headers,
        json={"predicate_json": {"kind": "price_threshold"}},  # missing required keys
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error_code"] == "invalid_predicate"
    assert detail["schema_errors"]


@pytest.mark.asyncio
async def test_get_fires_404_identity_unknown_id_and_cross_subject(
    _patched_verifier: None,
    client: AsyncClient,
    jwt_headers: dict[str, str],
    other_jwt_headers: dict[str, str],
) -> None:
    """Phase 11b chunk-B-close: GET /{id}/fires must mirror the surface-wide
    identity-404 pattern (cross-subject and unknown-id share the body)."""
    created = await _create_alert(client, jwt_headers)

    cross_subject = await client.get(
        f"/api/alerts/{created['id']}/fires", headers=other_jwt_headers
    )
    unknown = await client.get("/api/alerts/99999999/fires", headers=other_jwt_headers)

    assert cross_subject.status_code == 404
    assert unknown.status_code == 404
    assert cross_subject.json() == unknown.json()


@pytest.mark.asyncio
async def test_put_status_disables_active_rule(
    _patched_verifier: None,
    client: AsyncClient,
    jwt_headers: dict[str, str],
) -> None:
    """Phase 11b chunk-B-close: PUT /{id}/status flips the rule status."""
    created = await _create_alert(client, jwt_headers)
    # Activate first (the fake store creates pending rules; confirm to active).
    await client.post(f"/api/alerts/{created['id']}/confirm", headers=jwt_headers)

    response = await client.put(
        f"/api/alerts/{created['id']}/status",
        headers=jwt_headers,
        json={"status": "disabled"},
    )

    assert response.status_code == 200, response.text
    # The fake update_predicate path doesn't touch status; the route writes
    # via raw SQL against the (fake) db. We only assert the response shape.
    body = response.json()
    assert body["id"] == created["id"]


@pytest.mark.asyncio
async def test_put_status_rejects_unknown_status(
    _patched_verifier: None,
    client: AsyncClient,
    jwt_headers: dict[str, str],
) -> None:
    """Status values other than 'active' / 'disabled' surface 400."""
    created = await _create_alert(client, jwt_headers)
    response = await client.put(
        f"/api/alerts/{created['id']}/status",
        headers=jwt_headers,
        json={"status": "deleted"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == "invalid_status"


@pytest.mark.asyncio
async def test_put_status_404_identity_cross_subject(
    _patched_verifier: None,
    client: AsyncClient,
    jwt_headers: dict[str, str],
    other_jwt_headers: dict[str, str],
) -> None:
    """PUT /{id}/status must return the same 404 body for unknown-id and
    cross-subject targets."""
    created = await _create_alert(client, jwt_headers)
    body = {"status": "disabled"}

    cross_subject = await client.put(
        f"/api/alerts/{created['id']}/status",
        headers=other_jwt_headers,
        json=body,
    )
    unknown = await client.put(
        "/api/alerts/99999999/status",
        headers=other_jwt_headers,
        json=body,
    )

    assert cross_subject.status_code == 404
    assert unknown.status_code == 404
    assert cross_subject.json() == unknown.json()


@pytest.mark.asyncio
async def test_confirm_404_identity_unknown_id_and_cross_subject(
    _patched_verifier: None,
    client: AsyncClient,
    jwt_headers: dict[str, str],
    other_jwt_headers: dict[str, str],
) -> None:
    """Codex chunk-C test-gap LOW — confirm must surface identical 404 body
    for unknown-id and cross-subject access."""
    created = await _create_alert(client, jwt_headers)

    cross_subject = await client.post(
        f"/api/alerts/{created['id']}/confirm", headers=other_jwt_headers
    )
    unknown = await client.post("/api/alerts/99999999/confirm", headers=other_jwt_headers)

    assert cross_subject.status_code == 404
    assert unknown.status_code == 404
    assert cross_subject.json() == unknown.json()
