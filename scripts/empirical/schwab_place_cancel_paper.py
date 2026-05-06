"""Empirically validate Schwab paper order place/cancel assumptions."""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


KNOWN_STATUSES = {
    "AWAITING_PARENT_ORDER",
    "AWAITING_CONDITION",
    "AWAITING_STOP_CONDITION",
    "AWAITING_MANUAL_REVIEW",
    "ACCEPTED",
    "PENDING_ACTIVATION",
    "QUEUED",
    "WORKING",
    "REJECTED",
    "PENDING_CANCEL",
    "CANCELED",
    "PENDING_REPLACE",
    "REPLACED",
    "FILLED",
    "EXPIRED",
    "NEW",
    "AWAITING_RELEASE_TIME",
    "PENDING_ACKNOWLEDGEMENT",
    "PENDING_RECALL",
    "UNKNOWN",
}
REQUIRED_ENV = (
    "SCHWAB_APP_KEY",
    "SCHWAB_APP_SECRET",
    "SCHWAB_PAPER_ACCOUNT_HASH",
    # SCHWAB_REFRESH_TOKEN unblocks the schwabdev tokens-db seed so the script
    # never hits its interactive OAuth flow (which would EOFError in CI).
    "SCHWAB_REFRESH_TOKEN",
)
ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"
TOKENS_DB_PATH = "/tmp/schwab_empirical_tokens.db"  # noqa: S108


def _seed_schwabdev_tokens_db(
    db_path: str,
    *,
    access_token: str,
    refresh_token: str,
) -> None:
    """Pre-populate schwabdev's SQLite tokens table to bypass OAuth.

    Mirrors sidecar_schwab.client.SchwabClient._seed_schwabdev_tokens_db.
    Without this, schwabdev's Tokens.__init__ runs
    `update_tokens(force_refresh_token=True)` when the DB is empty, which
    calls `input()` and EOFErrors here.
    """
    import sqlite3

    if not refresh_token:
        return  # caller already errored on missing env
    now = datetime.now(UTC).isoformat()
    seed_access = access_token or "PLACEHOLDER_AWAITING_REFRESH"
    conn = sqlite3.connect(db_path, check_same_thread=False)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schwabdev (
            access_token_issued TEXT NOT NULL,
            refresh_token_issued TEXT NOT NULL,
            access_token TEXT NOT NULL,
            refresh_token TEXT NOT NULL,
            id_token TEXT NOT NULL,
            expires_in INTEGER,
            token_type TEXT,
            scope TEXT
        );
        """
    )
    cur.execute("DELETE FROM schwabdev")
    cur.execute(
        "INSERT INTO schwabdev VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (now, now, seed_access, refresh_token, "", 1800, "Bearer", "api"),
    )
    conn.commit()
    conn.close()


class EmpiricalFailure(RuntimeError):
    """Raised when an empirical assertion fails."""


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _artifact_path() -> Path:
    return ARTIFACT_DIR / f"schwab_c0_{_utc_stamp()}.json"


def _write_artifact(artifact: dict[str, Any]) -> Path:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    path = _artifact_path()
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _require_env() -> dict[str, str]:
    missing = [key for key in REQUIRED_ENV if os.environ.get(key, "") == ""]
    if missing:
        raise EmpiricalFailure(f"missing required env vars: {', '.join(missing)}")
    return {key: os.environ[key] for key in REQUIRED_ENV}


def _order_payload(client_order_id: str, symbol: str) -> dict[str, Any]:
    return {
        "orderType": "LIMIT",
        "session": "NORMAL",
        "duration": "DAY",
        "orderStrategyType": "SINGLE",
        "price": "1.00",
        "clientOrderId": client_order_id,
        "orderLegCollection": [
            {
                "instruction": "BUY",
                "quantity": 1,
                "instrument": {"symbol": symbol, "assetType": "EQUITY"},
            }
        ],
    }


def _assert(condition: bool, message: str, assertions: dict[str, Any]) -> None:
    assertions[message] = condition
    if not condition:
        raise EmpiricalFailure(message)


def _extract_order_id(location: str) -> str:
    broker_order_id = location.rstrip("/").rsplit("/", 1)[-1]
    if broker_order_id == "":
        raise EmpiricalFailure(f"Location header has no order id: {location}")
    return broker_order_id


def _validate_execution_legs(detail: dict[str, Any]) -> dict[str, Any]:
    checked = 0
    fills = 0
    for activity in detail.get("orderActivityCollection") or []:
        if activity.get("executionType") != "FILL":
            continue
        fills += 1
        execution_legs = activity.get("executionLegs")
        if not isinstance(execution_legs, list):
            raise EmpiricalFailure("FILL activity executionLegs is not a list")
        for leg in execution_legs:
            if not isinstance(leg, dict):
                raise EmpiricalFailure("executionLeg entry is not an object")
            for key in ("legId", "quantity", "time"):
                if key not in leg:
                    raise EmpiricalFailure(f"executionLeg missing expected key: {key}")
            if "price" not in leg and "marketValue" not in detail:
                raise EmpiricalFailure(
                    "executionLeg missing price and order missing marketValue fallback"
                )
            checked += 1
    return {"fill_activities": fills, "execution_legs_checked": checked}


def _run() -> tuple[bool, dict[str, Any]]:
    import schwabdev

    env = _require_env()
    symbol = os.environ.get("SCHWAB_PAPER_SYMBOL", "F")
    client_order_id = f"EMPIRICAL-{int(time.time())}"
    assertions: dict[str, Any] = {}
    result: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "client_order_id": client_order_id,
        "symbol": symbol,
        "assertions": assertions,
        "known_statuses": sorted(KNOWN_STATUSES),
    }

    _seed_schwabdev_tokens_db(
        TOKENS_DB_PATH,
        access_token=os.environ.get("SCHWAB_ACCESS_TOKEN", ""),
        refresh_token=env["SCHWAB_REFRESH_TOKEN"],
    )
    client = schwabdev.Client(
        env["SCHWAB_APP_KEY"],
        env["SCHWAB_APP_SECRET"],
        tokens_db=TOKENS_DB_PATH,
    )
    payload = _order_payload(client_order_id, symbol)
    place = client.order_place(env["SCHWAB_PAPER_ACCOUNT_HASH"], payload)
    result["place"] = {"status_code": place.status_code, "headers": dict(place.headers)}
    _assert(place.status_code in (200, 201), "place status is 200 or 201", assertions)

    location = place.headers.get("Location")
    _assert(location is not None, "place response includes Location header", assertions)
    broker_order_id = _extract_order_id(str(location))
    result["broker_order_id"] = broker_order_id
    assertions["Location broker_order_id extracted"] = True

    time.sleep(2)
    detail_response = client.order_details(env["SCHWAB_PAPER_ACCOUNT_HASH"], broker_order_id)
    detail = detail_response.json()
    result["detail"] = detail
    _assert(
        detail.get("clientOrderId") == client_order_id,
        "clientOrderId round-trips on order detail",
        assertions,
    )

    status = detail.get("status")
    result["observed_status"] = status
    _assert(isinstance(status, str), "order detail status is a string", assertions)
    _assert(status in KNOWN_STATUSES, "order detail status is in known status set", assertions)

    result["execution_leg_shape"] = _validate_execution_legs(detail)
    assertions["executionLeg shape matches normalizer expectations when filled"] = True

    cancel = client.order_cancel(env["SCHWAB_PAPER_ACCOUNT_HASH"], broker_order_id)
    result["cancel"] = {"status_code": cancel.status_code}
    _assert(cancel.status_code in (200, 204), "cancel status is 200 or 204", assertions)

    result["finished_at"] = datetime.now(UTC).isoformat()
    result["outcome"] = "PASS"
    return True, result


def main() -> int:
    try:
        passed, artifact = _run()
    except Exception as exc:
        artifact = {
            "started_at": datetime.now(UTC).isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "outcome": "FAIL",
            "reason": str(exc),
            "exception_type": type(exc).__name__,
        }
        path = _write_artifact(artifact)
        print(f"FAIL: {exc}; artifact={path}", file=sys.stderr)
        return 1

    path = _write_artifact(artifact)
    if passed:
        print(f"PASS: artifact={path}")
        return 0
    print(f"FAIL: artifact={path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
