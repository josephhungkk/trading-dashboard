"""gRPC Broker service handlers for the Futu sidecar."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from google.protobuf.timestamp_pb2 import Timestamp

from sidecar_futu import metrics
from sidecar_futu._generated.broker.v1 import broker_pb2, broker_pb2_grpc
from sidecar_futu.futu_client import FutuClient
from sidecar_futu.normalize import (
    AccountMapped,
    AccountSkipped,
    account_from_futu_row,
    summary_from_futu_row,
)

log = structlog.get_logger(__name__)


class BrokerHandlers(broker_pb2_grpc.BrokerServicer):  # type: ignore[misc]
    # Generated BrokerServicer is typed Any; the ignore documents the
    # intentional subclass-of-Any rather than letting it leak.
    """Implements the proto Broker service for Futu."""

    def __init__(self, *, started_at: datetime, simulator: bool = True) -> None:
        self._started_at = started_at
        self._sim_mode = simulator
        self._client = FutuClient()

    async def Health(  # noqa: N802
        self,
        request: broker_pb2.HealthRequest,
        context: Any,
    ) -> broker_pb2.HealthResponse:
        ts = Timestamp()
        ts.FromDatetime(self._started_at)
        return broker_pb2.HealthResponse(
            label="futu",
            gateway_connected=self._client.gateway_connected,
            gateway_version="",
            sidecar_version="0.6.0",
            started_at=ts,
            broker_id="futu",
        )

    async def Configure(  # noqa: N802
        self,
        request: broker_pb2.ConfigureRequest,
        context: Any,
    ) -> broker_pb2.ConfigureResponse:
        detail = self._client.validate(request)
        if detail is not None:
            log.warning("configure_rejected", detail=detail)
            return broker_pb2.ConfigureResponse(ok=False, detail=detail)
        await self._client.configure(request)
        log.info("configure_accepted")
        return broker_pb2.ConfigureResponse(ok=True, detail="")

    async def ListManagedAccounts(  # noqa: N802
        self,
        request: broker_pb2.Empty,
        context: Any,
    ) -> broker_pb2.AccountsResponse:
        accounts: list[broker_pb2.Account] = []
        for row in await self._client.list_accounts():
            result = account_from_futu_row(row)
            if isinstance(result, AccountSkipped):
                metrics.broker_normalize_unknown_total.labels(
                    label="futu", field="trd_env"
                ).inc()
                log.warning("futu_normalize_unknown_trd_env", row=row)
                continue
            assert isinstance(result, AccountMapped)
            accounts.append(result.account)
        return broker_pb2.AccountsResponse(accounts=accounts)

    async def GetAccountSummary(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: Any,
    ) -> broker_pb2.SummaryResponse:
        row = await self._client.get_account_summary(request.account_number)
        summary = summary_from_futu_row(row, account_number=request.account_number)
        return broker_pb2.SummaryResponse(summary=summary)
