from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from ib_async import IB, Contract  # type: ignore[import-untyped, unused-ignore]

JsonProducer = Callable[[], Awaitable[Any] | Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record read-only ib_async API traces from a paper IBKR Gateway."
    )
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--client-id", type=int, default=999)
    parser.add_argument("--account")
    return parser.parse_args()


def serialize(value: object) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(serialize(key)): serialize(item) for key, item in value.items()}
    if isinstance(value, set | frozenset):
        return sorted((serialize(item) for item in value), key=_sort_key)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [serialize(item) for item in value]
    if hasattr(value, "_asdict"):
        asdict_value = value._asdict()
        return serialize(asdict_value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: serialize(getattr(value, field.name)) for field in fields(value)}
    if hasattr(value, "__dict__"):
        return serialize(vars(value))
    return str(value)


def _sort_key(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True)
    except TypeError:
        return str(value)


def _item_count(value: Any) -> int:
    if isinstance(value, Mapping | Sequence | set | frozenset) and not isinstance(
        value, str | bytes | bytearray
    ):
        return len(value)
    return 1


def _emit_error(method: str, error: Exception) -> None:
    payload = {
        "error": type(error).__name__,
        "message": str(error),
        "method": method,
    }
    print(json.dumps(payload, sort_keys=True), file=sys.stderr)


async def _maybe_await(value: Awaitable[Any] | Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _record_fixture(out_dir: Path, filename: str, producer: JsonProducer) -> bool:
    try:
        raw_result = await _maybe_await(producer())
        result = serialize(raw_result)
        output_path = out_dir / filename
        with output_path.open("w", encoding="utf-8") as fixture:
            json.dump(result, fixture, indent=2, sort_keys=True)
            fixture.write("\n")
    except Exception as exc:
        _emit_error(filename, exc)
        return False

    print(f"[recorded] {filename} ({_item_count(result)} items)")
    return True


async def run() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    ib = IB()
    ib_api: Any = ib
    success = True
    connected = False

    try:
        await ib_api.connectAsync(args.host, args.port, clientId=args.client_id, timeout=30)
        connected = True

        async def managed_accounts() -> Any:
            result = await ib_api.reqManagedAccountsAsync()
            if args.account is None and isinstance(result, Sequence) and result:
                args.account = str(result[0])
            return result

        async def account_summary() -> Any:
            await ib_api.reqAccountSummaryAsync(
                group="All",
                tags=(
                    "NetLiquidation,TotalCashValue,RealizedPnL,UnrealizedPnL,"
                    "BuyingPower,BASE"
                ),
            )
            return ib_api.accountValues()

        async def positions() -> Any:
            return await ib_api.reqPositionsAsync()

        def open_trades() -> Any:
            return ib_api.openTrades()

        def fills() -> Any:
            return ib_api.fills()

        async def qualify_aapl() -> Any:
            contract = Contract(conId=265598)
            return await ib_api.qualifyContractsAsync(contract)

        calls: tuple[tuple[str, JsonProducer], ...] = (
            ("managed_accounts.json", managed_accounts),
            ("account_summary.json", account_summary),
            ("positions.json", positions),
            ("open_trades.json", open_trades),
            ("fills.json", fills),
            ("qualify_aapl.json", qualify_aapl),
        )

        for filename, producer in calls:
            if not await _record_fixture(args.out_dir, filename, producer):
                success = False
    except Exception as exc:
        _emit_error("connect", exc)
        success = False
    finally:
        if connected:
            try:
                await _maybe_await(ib_api.disconnect())
            except Exception as exc:
                _emit_error("disconnect", exc)
                success = False

    return 0 if success else 1


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
