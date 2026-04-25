"""gRPC handlers for the IBKR broker sidecar."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, cast

import structlog
from google.protobuf.timestamp_pb2 import Timestamp  # type: ignore[import-untyped]

from sidecar._generated.broker.v1 import broker_pb2, broker_pb2_grpc
from sidecar.normalize import decimal_str, to_money_proto

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ib_async import (  # type: ignore[import-untyped, unused-ignore]
        IB,
    )


logger = structlog.get_logger(__name__)


class BrokerHandlers(broker_pb2_grpc.BrokerServicer):  # type: ignore[misc]
    # The generated BrokerServicer base class is typed Any (proto codegen
    # is not strict-clean). The `misc` ignore documents the intentional
    # subclass-of-Any rather than letting it leak into every caller.
    """Read-only broker service backed by an ib_async IB connection."""

    def __init__(
        self,
        ib: IB,
        label: str,
        version: str,
        last_tick_ref: dict[str, datetime],
    ) -> None:
        self.ib: IB = ib
        self.label: str = label
        self.version: str = version
        self.last_tick_ref: dict[str, datetime] = last_tick_ref

    async def Health(  # noqa: N802 — gRPC servicer methods mirror proto rpc names
        self,
        request: broker_pb2.HealthRequest,
        context: object,
    ) -> broker_pb2.HealthResponse:
        del request, context

        gateway_connected: bool = False
        gateway_version: str = ""
        last_tick_at: Timestamp | None = self._last_tick_timestamp()

        try:
            gateway_connected = bool(self.ib.isConnected())
        except Exception as exc:
            logger.exception(
                "ibkr_health_connection_check_failed",
                label=self.label,
                error=str(exc),
            )

        if gateway_connected:
            try:
                gateway_version = str(self.ib.client.serverVersion())
            except Exception as exc:
                logger.exception(
                    "ibkr_health_server_version_failed",
                    label=self.label,
                    error=str(exc),
                )
                gateway_version = ""

        response: broker_pb2.HealthResponse = broker_pb2.HealthResponse(
            label=self.label,
            gateway_connected=gateway_connected,
            gateway_version=gateway_version,
            sidecar_version=self.version,
        )
        if last_tick_at is not None:
            response.last_tick_at.CopyFrom(last_tick_at)
        return response

    async def ListManagedAccounts(  # noqa: N802 — gRPC rpc name
        self,
        request: broker_pb2.Empty,
        context: object,
    ) -> broker_pb2.AccountsResponse:
        del request, context

        account_numbers: list[str] = []
        account_values: list[object] = []

        try:
            raw_accounts: object = await self.ib.reqManagedAccountsAsync()  # type: ignore[attr-defined]
            managed_accounts: Iterable[object] = cast("Iterable[object]", raw_accounts)
            account_numbers = [str(account) for account in managed_accounts]
        except Exception as exc:
            logger.exception(
                "ibkr_list_managed_accounts_failed",
                label=self.label,
                error=str(exc),
            )

        try:
            raw_values: object = self.ib.accountValues()
            values: Iterable[object] = cast("Iterable[object]", raw_values)
            account_values = list(values)
        except Exception as exc:
            logger.exception(
                "ibkr_account_values_failed",
                label=self.label,
                error=str(exc),
            )

        accounts: list[broker_pb2.Account] = []
        for account_number in account_numbers:
            mode: int = broker_pb2.PAPER if account_number.startswith("D") else broker_pb2.LIVE
            currency_base: str = self._base_currency(account_values, account_number)
            account: broker_pb2.Account = broker_pb2.Account(
                account_number=account_number,
                mode=mode,  # type: ignore[arg-type]
                gateway_label=self.label,
                currency_base=currency_base,
            )
            accounts.append(account)

        return broker_pb2.AccountsResponse(accounts=accounts)

    async def GetAccountSummary(  # noqa: N802 — gRPC rpc name
        self,
        request: broker_pb2.AccountRef,
        context: object,
    ) -> broker_pb2.SummaryResponse:
        del context

        account_number: str = str(request.account_number)
        account_values: list[object] = []

        try:
            raw_values: object = self.ib.accountValues()
            values: Iterable[object] = cast("Iterable[object]", raw_values)
            account_values = [
                value for value in values if str(getattr(value, "account", "")) == account_number
            ]
        except Exception as exc:
            logger.exception(
                "ibkr_account_summary_values_failed",
                label=self.label,
                account_number=account_number,
                error=str(exc),
            )

        values_by_tag: dict[str, object] = {
            str(getattr(value, "tag", "")): value for value in account_values
        }

        summary: broker_pb2.Summary = broker_pb2.Summary(
            net_liquidation=self._money_for_tag(values_by_tag, "NetLiquidation"),
            total_cash=self._money_for_tag(values_by_tag, "TotalCashValue"),
            realized_pnl=self._money_for_tag(values_by_tag, "RealizedPnL"),
            unrealized_pnl=self._money_for_tag(values_by_tag, "UnrealizedPnL"),
            buying_power=self._money_for_tag(values_by_tag, "BuyingPower"),
        )
        return broker_pb2.SummaryResponse(summary=summary)

    def _last_tick_timestamp(self) -> Timestamp | None:
        tick_at: datetime | None = self.last_tick_ref.get("t")
        if tick_at is None:
            tick_at = self.last_tick_ref.get(self.label)
        if tick_at is None:
            return None

        timestamp: Timestamp = Timestamp()
        try:
            timestamp.FromDatetime(tick_at)
        except Exception as exc:
            logger.exception("ibkr_last_tick_timestamp_failed", label=self.label, error=str(exc))
            return None
        return timestamp

    def _base_currency(self, account_values: Iterable[object], account_number: str) -> str:
        # Proto contract (broker/v1/broker.proto §Account.currency_base): "NOT
        # defaulted." Return empty string if the BASE tag isn't cached yet so
        # the backend can distinguish "not loaded" from a real currency.
        for value in account_values:
            tag: str = str(getattr(value, "tag", ""))
            account: str = str(getattr(value, "account", ""))
            if tag == "BASE" and account == account_number:
                currency: str = str(getattr(value, "value", ""))
                if currency:
                    return currency
        return ""

    def _money_for_tag(self, values_by_tag: dict[str, object], tag: str) -> broker_pb2.Money:
        account_value: object | None = values_by_tag.get(tag)
        if account_value is None:
            return to_money_proto(Decimal("0"), "USD")

        raw_value: str = str(getattr(account_value, "value", "0"))
        currency: str = str(getattr(account_value, "currency", "")) or "USD"
        try:
            value: Decimal = Decimal(decimal_str(Decimal(raw_value)))
        except (InvalidOperation, ValueError) as exc:
            logger.exception(
                "ibkr_money_decimal_parse_failed",
                tag=tag,
                value=raw_value,
                error=str(exc),
            )
            value = Decimal("0")

        return to_money_proto(value, currency)
