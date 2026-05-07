"""Empirically validate Alpaca paper equity order place/cancel assumptions."""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ACCEPTED_INITIAL_STATUSES = {
    "new",
    "accepted",
    "pending_new",
}
REQUIRED_ENV = (
    "ALPACA_PAPER_API_KEY",
    "ALPACA_PAPER_API_SECRET",
)
ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"
DEFAULT_BASE_URL = "https://paper-api.alpaca.markets"


class EmpiricalFailure(RuntimeError):
    """Raised when an empirical assertion fails."""


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _artifact_path(utc_stamp: str) -> Path:
    return ARTIFACT_DIR / f"alpaca_c0_{utc_stamp}.json"


def _write_artifact(artifact: dict[str, Any], utc_stamp: str) -> Path:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    path = _artifact_path(utc_stamp)
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _require_env() -> dict[str, str]:
    missing = [key for key in REQUIRED_ENV if os.environ.get(key, "") == ""]
    if missing:
        raise EmpiricalFailure(f"missing required env vars: {', '.join(missing)}")
    env = {key: os.environ[key] for key in REQUIRED_ENV}
    env["ALPACA_PAPER_BASE_URL"] = os.environ.get("ALPACA_PAPER_BASE_URL", DEFAULT_BASE_URL)
    return env


def _assert(condition: bool, message: str, assertions: dict[str, Any]) -> None:
    assertions[message] = "PASS" if condition else "FAIL"
    print(f"{assertions[message]}: {message}")
    if not condition:
        raise EmpiricalFailure(message)


def _order_id(order: Any) -> str:
    broker_order_id = str(getattr(order, "id", "") or "")
    if broker_order_id == "":
        raise EmpiricalFailure("submit_order returned empty order id")
    return broker_order_id


def _order_status(order: Any) -> str:
    status = getattr(order, "status", "")
    value = getattr(status, "value", status)
    return str(value).lower()


def _run(utc_stamp: str) -> tuple[bool, dict[str, Any]]:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import LimitOrderRequest

    env = _require_env()
    symbol = "SPY"
    client_order_id = f"oco-empirical-{utc_stamp}"
    assertions: dict[str, Any] = {}
    result: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "symbol": symbol,
        "client_order_id": client_order_id,
        "assertions": assertions,
        "accepted_initial_statuses": sorted(ACCEPTED_INITIAL_STATUSES),
    }

    client = TradingClient(
        env["ALPACA_PAPER_API_KEY"],
        env["ALPACA_PAPER_API_SECRET"],
        paper=True,
        url_override=env["ALPACA_PAPER_BASE_URL"],
    )
    request = LimitOrderRequest(
        symbol=symbol,
        qty=1,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
        limit_price=1.00,
        client_order_id=client_order_id,
    )
    place = client.submit_order(order_data=request)
    broker_order_id = _order_id(place)
    result["place"] = {
        "id": broker_order_id,
        "status": _order_status(place),
        "client_order_id": getattr(place, "client_order_id", None),
    }
    result["broker_order_id"] = broker_order_id
    _assert(broker_order_id != "", "submit_order returns 200/201 with order ID", assertions)

    time.sleep(2)
    detail = client.get_order_by_id(broker_order_id)
    result["detail"] = {
        "id": str(getattr(detail, "id", "") or ""),
        "status": _order_status(detail),
        "client_order_id": getattr(detail, "client_order_id", None),
    }
    _assert(
        str(getattr(detail, "id", "") or "") == broker_order_id,
        "order ID round-trips via get_order_by_id",
        assertions,
    )

    status = _order_status(detail)
    result["observed_status"] = status
    _assert(
        status in ACCEPTED_INITIAL_STATUSES,
        "status is one of: new, accepted, pending_new",
        assertions,
    )

    try:
        client.cancel_order_by_id(broker_order_id)
        cancel_success = True
        result["cancel"] = {"success": True}
    except (Exception,) as exc:
        cancel_success = False
        result["cancel"] = {
            "success": False,
            "reason": str(exc),
            "exception_type": type(exc).__name__,
        }
    _assert(cancel_success, "cancel_order_by_id returns success", assertions)

    _assert(
        getattr(place, "client_order_id", None) == client_order_id,
        "client_order_id preservation",
        assertions,
    )

    result["finished_at"] = datetime.now(UTC).isoformat()
    result["outcome"] = "PASS"
    return True, result


def main() -> int:
    utc_stamp = _utc_stamp()
    try:
        passed, artifact = _run(utc_stamp)
    except (Exception,) as exc:
        artifact = {
            "started_at": datetime.now(UTC).isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "outcome": "FAIL",
            "reason": str(exc),
            "exception_type": type(exc).__name__,
        }
        path = _write_artifact(artifact, utc_stamp)
        print(f"FAIL: {exc}; artifact={path}", file=sys.stderr)
        return 1

    path = _write_artifact(artifact, utc_stamp)
    if passed:
        print(f"PASS: artifact={path}")
        return 0
    print(f"FAIL: artifact={path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
