"""Alpaca sidecar configuration from environment."""

from __future__ import annotations

import os

MODE = os.environ["MODE"]
if MODE not in {"live", "paper"}:
    raise ValueError("MODE must be 'live' or 'paper'")

GRPC_PORT = int(os.environ.get("GRPC_PORT", "9091"))
BACKEND_ADMIN_GRPC = os.environ.get("BACKEND_ADMIN_GRPC", "backend:8001")
ALPACA_ACCOUNT_LABEL = os.environ.get("ALPACA_ACCOUNT_LABEL", "default")

BASE_URL_REST = (
    "https://api.alpaca.markets/v2"
    if MODE == "live"
    else "https://paper-api.alpaca.markets/v2"
)
BASE_URL_DATA = "wss://stream.data.alpaca.markets/v2/iex"
BASE_URL_DATA_CRYPTO = "wss://stream.data.alpaca.markets/v1beta3/crypto/us"
