"""Phase 7a B6 - H3: 404 from hash-keyed path -> refresh -> retry once."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from sidecar_schwab.client import SchwabAccountHashStaleError
from sidecar_schwab.handlers import BrokerServicer


@pytest.mark.asyncio
async def test_404_triggers_hash_refresh_and_retry_once():
    servicer = BrokerServicer()
    servicer._client = MagicMock()
    servicer._client.get_account_details = AsyncMock(
        side_effect=[
            SchwabAccountHashStaleError(
                "404 - account_hash may have rotated",
                status_code=404,
                endpoint="/accounts",
            ),
            {"securitiesAccount": {"accountNumber": "X", "type": "MARGIN"}},
        ]
    )
    servicer._client.refresh_hashes = AsyncMock(return_value={"X": "NEW_HASH"})
    servicer._client.hash_for = lambda n: "HASH_VAL"

    result = await servicer._fetch_account_with_404_retry("X")
    assert result["securitiesAccount"]["accountNumber"] == "X"
    servicer._client.refresh_hashes.assert_called_once_with(reason="404_retry")
    assert servicer._client.get_account_details.call_count == 2


@pytest.mark.asyncio
async def test_second_404_surfaces_typed_error():
    servicer = BrokerServicer()
    servicer._client = MagicMock()
    servicer._client.get_account_details = AsyncMock(
        side_effect=SchwabAccountHashStaleError(
            "404 - account_hash may have rotated",
            status_code=404,
            endpoint="/accounts",
        )
    )
    servicer._client.refresh_hashes = AsyncMock(return_value={"X": "NEW_HASH"})
    servicer._client.hash_for = lambda n: "HASH"

    with pytest.raises(SchwabAccountHashStaleError) as exc_info:
        await servicer._fetch_account_with_404_retry("X")
    assert exc_info.value.status_code == 404
    assert servicer._client.get_account_details.call_count == 2
