"""Empirically validate Schwab paper OCO place/cancel assumptions (Phase 8b T-O.10).

Mirrors scripts/empirical/schwab_place_cancel_paper.py but submits a 2-leg
one-cancels-other parent. Hard-gates the Schwab OCO capability flip in
Alembic 0017 -- if this PASSes, the Schwab native OCO branch in
oco_orchestrator.dispatch_oco_alpaca_equity-style adapter is provably correct
against paper. If it FAILs, ship the Alembic with is_supported=FALSE + notes.

Run: SCHWAB_APP_KEY=... SCHWAB_APP_SECRET=... SCHWAB_PAPER_ACCOUNT_HASH=... \\
     SCHWAB_REFRESH_TOKEN=... python scripts/empirical/schwab_oco_paper.py
"""
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
NON_REJECTED_STATUSES = KNOWN_STATUSES - {"REJECTED"}
REQUIRED_ENV = (
    "SCHWAB_APP_KEY",
    "SCHWAB_APP_SECRET",
    "SCHWAB_PAPER_ACCOUNT_HASH",
    "SCHWAB_REFRESH_TOKEN",
)
ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"
TOKENS_DB_PATH = "/tmp/schwab_oco_empirical_tokens.db"  # noqa: S108


def _seed_schwabdev_tokens_db(
    db_path: str,
    *,
    access_token: str,
    refresh_token: str,
) -> None:
    """Pre-populate schwabdev's SQLite tokens table to bypass interactive OAuth.

    Mirrors schwab_place_cancel_paper.py exactly -- without this, schwabdev's
    Tokens.__init__ runs `update_tokens(force_refresh_token=True)` when the DB
    is empty, which calls `input()` and EOFErrors in non-interactive runs.
    """
    import sqlite3

    from datetime import timedelta

    if not refresh_token:
        return
    now_dt = datetime.now(UTC)
    now = now_dt.isoformat()
    if access_token:
        seed_access = access_token
        access_issued = now
    else:
        seed_access = "PLACEHOLDER_AWAITING_REFRESH"
        access_issued = (now_dt - timedelta(hours=2)).isoformat()
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
        (access_issued, now, seed_access, refresh_token, "", 1800, "Bearer", "api"),
    )
    conn.commit()
    conn.close()


class EmpiricalFailure(RuntimeError):
    """Raised when an empirical assertion fails."""


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _artifact_path() -> Path:
    return ARTIFACT_DIR / f"schwab_oco_{_utc_stamp()}.json"


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


def _oco_payload(symbol: str) -> dict[str, Any]:
    """Schwab REST OCO payload -- 2 child legs both unfillable.

    Schwab uses orderStrategyType="OCO" + childOrderStrategies (NOT
    complexOrderStrategyType="OCO" -- that's for compound option strategies).
    Each child is a complete SINGLE order. The parent is a wrapper.

    Both children are deeply unfillable BUYs:
      - Leg A: LIMIT @ $1 (unfillable below market)
      - Leg B: LIMIT @ $1.01 (also unfillable; using LIMIT for both because
               STOP+LIMIT shape varies by symbol and we want a clean test)
    """
    return {
        "orderStrategyType": "OCO",
        "childOrderStrategies": [
            {
                "orderType": "LIMIT",
                "session": "NORMAL",
                "duration": "DAY",
                "orderStrategyType": "SINGLE",
                "price": "1.00",
                "orderLegCollection": [
                    {
                        "instruction": "BUY",
                        "quantity": 1,
                        "instrument": {"symbol": symbol, "assetType": "EQUITY"},
                    }
                ],
            },
            {
                "orderType": "LIMIT",
                "session": "NORMAL",
                "duration": "DAY",
                "orderStrategyType": "SINGLE",
                "price": "1.01",
                "orderLegCollection": [
                    {
                        "instruction": "BUY",
                        "quantity": 1,
                        "instrument": {"symbol": symbol, "assetType": "EQUITY"},
                    }
                ],
            },
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


def _extract_child_ids(detail: dict[str, Any]) -> list[str]:
    """Pull child orderIds out of an OCO parent detail response."""
    children = detail.get("childOrderStrategies") or []
    ids: list[str] = []
    for child in children:
        if not isinstance(child, dict):
            continue
        cid = child.get("orderId")
        if cid is not None:
            ids.append(str(cid))
    return ids


def _run() -> tuple[bool, dict[str, Any]]:
    import schwabdev

    env = _require_env()
    symbol = os.environ.get("SCHWAB_PAPER_SYMBOL", "F")
    assertions: dict[str, Any] = {}
    result: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "symbol": symbol,
        "strategy": "OCO",
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

    payload = _oco_payload(symbol)
    place = client.place_order(env["SCHWAB_PAPER_ACCOUNT_HASH"], payload)
    result["place"] = {"status_code": place.status_code, "headers": dict(place.headers)}
    _assert(place.status_code in (200, 201), "place status is 200 or 201", assertions)

    location = place.headers.get("Location")
    _assert(location is not None, "place response includes Location header", assertions)
    parent_order_id = _extract_order_id(str(location))
    result["parent_order_id"] = parent_order_id
    assertions["parent broker_order_id extracted from Location"] = True

    # Schwab returns children asynchronously -- give the broker a moment.
    time.sleep(2)
    detail_response = client.order_details(env["SCHWAB_PAPER_ACCOUNT_HASH"], parent_order_id)
    detail = detail_response.json()
    result["detail"] = detail
    _assert(
        str(detail.get("orderId")) == parent_order_id,
        "parent broker_order_id round-trips on order detail",
        assertions,
    )

    parent_strategy = detail.get("orderStrategyType")
    _assert(
        parent_strategy == "OCO",
        "parent orderStrategyType is OCO",
        assertions,
    )

    child_ids = _extract_child_ids(detail)
    result["child_order_ids"] = child_ids
    _assert(len(child_ids) == 2, "exactly 2 child orderIds returned", assertions)
    _assert(
        len(set(child_ids)) == 2,
        "the 2 child orderIds are distinct",
        assertions,
    )

    # Each child should be in a non-rejected status.
    children = detail.get("childOrderStrategies") or []
    for idx, child in enumerate(children):
        status = child.get("status")
        result.setdefault("child_statuses", []).append({"index": idx, "status": status})
        _assert(
            isinstance(status, str),
            f"child[{idx}] status is a string",
            assertions,
        )
        _assert(
            status in NON_REJECTED_STATUSES,
            f"child[{idx}] status ({status}) is not REJECTED",
            assertions,
        )

    # Cancelling the parent should cascade to both children.
    cancel = client.cancel_order(env["SCHWAB_PAPER_ACCOUNT_HASH"], parent_order_id)
    result["cancel"] = {"status_code": cancel.status_code}
    _assert(cancel.status_code in (200, 204), "cancel parent status is 200 or 204", assertions)

    # Verify the cascade by re-fetching the detail.
    time.sleep(2)
    after_response = client.order_details(env["SCHWAB_PAPER_ACCOUNT_HASH"], parent_order_id)
    after = after_response.json()
    result["after_cancel"] = after
    after_children = after.get("childOrderStrategies") or []
    cancelled_terminals = {"CANCELED", "PENDING_CANCEL", "REJECTED"}
    cascaded = sum(
        1 for child in after_children if child.get("status") in cancelled_terminals
    )
    _assert(
        cascaded == 2,
        f"cancel cascaded to both children (got {cascaded}/2 in cancelled-terminal state)",
        assertions,
    )

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
