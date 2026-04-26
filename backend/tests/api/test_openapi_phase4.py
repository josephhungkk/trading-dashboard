"""Phase 4 OpenAPI schema assertions.

Locks the contract that ships to the frontend so a future refactor can't
silently leak `gateway_label` or `account_number` (M22 boundary stripping)
or drop `degraded_sidecars` (M24 partial-fleet UX).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.cf_access import AdminIdentity
from app.core.deps import require_admin_jwt
from app.main import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[require_admin_jwt] = lambda: AdminIdentity(
        email="test@example.com", kind="user", claims={}
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_phase4_paths_listed(client):
    spec = (await client.get("/openapi.json")).json()
    paths = spec["paths"]

    for required in (
        "/api/accounts",
        "/api/accounts/{account_id}",
        "/api/accounts/{account_id}/summary",
        "/api/accounts/{account_id}/positions",
        "/api/accounts/{account_id}/orders",
    ):
        assert required in paths, f"missing path {required!r} in OpenAPI"


@pytest.mark.asyncio
async def test_account_response_strips_gateway_and_account_number(client):
    spec = (await client.get("/openapi.json")).json()
    schema = spec["components"]["schemas"]["AccountResponse"]
    properties = set(schema["properties"].keys())

    assert "gateway_label" not in properties
    assert "account_number" not in properties

    expected = {"id", "broker_id", "alias", "mode", "currency_base", "display_order"}
    assert expected.issubset(properties)


@pytest.mark.asyncio
async def test_account_list_response_has_degraded_sidecars(client):
    spec = (await client.get("/openapi.json")).json()
    schema = spec["components"]["schemas"]["AccountListResponse"]
    properties = schema["properties"]

    assert "accounts" in properties
    assert "degraded_sidecars" in properties
    assert properties["degraded_sidecars"]["type"] == "array"
    assert properties["degraded_sidecars"]["items"]["type"] == "string"


@pytest.mark.asyncio
async def test_detail_routes_document_503_envelope(client):
    spec = (await client.get("/openapi.json")).json()

    for path in (
        "/api/accounts/{account_id}/summary",
        "/api/accounts/{account_id}/positions",
        "/api/accounts/{account_id}/orders",
    ):
        responses = spec["paths"][path]["get"]["responses"]
        assert "503" in responses, f"missing 503 on {path}"

        examples = responses["503"]["content"]["application/json"]["examples"]
        unreachable = examples["sidecar_unreachable"]["value"]
        maintenance = examples["broker_maintenance"]["value"]

        assert unreachable["error"] == "sidecar_unreachable"
        assert "label" in unreachable
        assert "maintenance window in progress" in maintenance["detail"]
        broker_maintenance = maintenance["broker_maintenance"]
        assert broker_maintenance["active"] is True
        assert broker_maintenance["window"] in {"weekend", "daily"}
        assert "until" in broker_maintenance


@pytest.mark.asyncio
async def test_detail_routes_document_404_envelope(client):
    spec = (await client.get("/openapi.json")).json()

    for path in (
        "/api/accounts/{account_id}",
        "/api/accounts/{account_id}/summary",
        "/api/accounts/{account_id}/positions",
        "/api/accounts/{account_id}/orders",
    ):
        method = "patch" if path == "/api/accounts/{account_id}" else "get"
        responses = spec["paths"][path][method]["responses"]
        assert "404" in responses, f"missing 404 on {method.upper()} {path}"

        example = responses["404"]["content"]["application/json"]["example"]
        assert example["error"] == "not_found"
