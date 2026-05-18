from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any


class AlgoStrategy(StrEnum):
    ADAPTIVE = "ADAPTIVE"
    TWAP = "TWAP"
    VWAP = "VWAP"
    ARRIVAL_PRICE = "ARRIVAL_PRICE"
    ICEBERG = "ICEBERG"
    RESERVE = "RESERVE"
    DARK_ICE = "DARK_ICE"


DISPLAY_ALGOS = frozenset({AlgoStrategy.ICEBERG, AlgoStrategy.RESERVE, AlgoStrategy.DARK_ICE})

ALGO_PARAM_SCHEMAS: dict[str, list[dict[str, Any]]] = {
    "ADAPTIVE": [
        {
            "name": "urgency",
            "type": "enum",
            "values": ["PATIENT", "NORMAL", "URGENT"],
            "required": True,
        }
    ],
    "TWAP": [
        {"name": "start_time", "type": "time", "required": True},
        {"name": "end_time", "type": "time", "required": True},
        {"name": "allow_past_end_time", "type": "boolean", "required": False},
    ],
    "VWAP": [
        {"name": "start_time", "type": "time", "required": True},
        {"name": "end_time", "type": "time", "required": True},
        {"name": "max_pct_vol", "type": "decimal", "required": False},
        {"name": "no_take_liq", "type": "boolean", "required": False},
    ],
    "ARRIVAL_PRICE": [
        {
            "name": "urgency",
            "type": "enum",
            "values": ["PATIENT", "NORMAL", "URGENT"],
            "required": True,
        },
        {"name": "max_pct_vol", "type": "decimal", "required": False},
    ],
    "ICEBERG": [
        {"name": "display_size", "type": "decimal", "required": True},
    ],
    "RESERVE": [
        {"name": "display_size", "type": "decimal", "required": True},
        {"name": "randomize_size", "type": "boolean", "required": False},
    ],
    "DARK_ICE": [
        {"name": "display_size", "type": "decimal", "required": True},
    ],
}

REQUIRED_PARAMS: dict[str, frozenset[str]] = {
    strategy: frozenset(param["name"] for param in params if param.get("required"))
    for strategy, params in ALGO_PARAM_SCHEMAS.items()
}


def _normalize_algo_params(params: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for k, v in params.items():
        if isinstance(v, bool):
            result[k] = "true" if v else "false"
        elif isinstance(v, int):
            result[k] = str(v)
        elif isinstance(v, Decimal):
            result[k] = str(v)
        elif isinstance(v, str):
            result[k] = v
        else:
            raise ValueError(
                f"algo_params[{k!r}]: unsupported type"
                f" {type(v).__name__!r}; expected bool, int, Decimal, or str"
            )
    return result
