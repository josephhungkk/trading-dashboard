"""Phase 6 — Futu HK test fixtures (HK.XXXXX symbols, numeric account IDs).

Declarative proto-shape Contract + Account fixtures consumed by integration
tests that exercise the futu code path (E3 e2e chain, E4 reconfigure cycle,
plus any future broker-agnostic test parametrizations).
"""

from __future__ import annotations

from app._generated.broker.v1 import broker_pb2

FUTU_HK_STOCK = broker_pb2.Contract(
    symbol="HK.00700",
    exchange="SEHK",
    currency="HKD",
    asset_class=broker_pb2.AssetClass.STOCK,
    conid="HK.00700",
    local_symbol="Tencent",
)

FUTU_HK_ETF = broker_pb2.Contract(
    symbol="HK.02800",
    exchange="SEHK",
    currency="HKD",
    asset_class=broker_pb2.AssetClass.ETF,
    conid="HK.02800",
    local_symbol="Tracker Fund",
)

FUTU_HK_WARRANT = broker_pb2.Contract(
    symbol="HK.13234",
    exchange="SEHK",
    currency="HKD",
    asset_class=broker_pb2.AssetClass.WARRANT,
    conid="HK.13234",
    local_symbol="WARRANT-700-X",
)

FUTU_HK_CBBC = broker_pb2.Contract(
    symbol="HK.62345",
    exchange="SEHK",
    currency="HKD",
    asset_class=broker_pb2.AssetClass.CBBC,
    conid="HK.62345",
    local_symbol="CBBC-700-BULL",
)

FUTU_LIVE_ACCOUNT = broker_pb2.Account(
    account_number="11111111",
    mode=broker_pb2.TradingMode.LIVE,
    gateway_label="futu",
    currency_base="HKD",
)

FUTU_PAPER_ACCOUNT = broker_pb2.Account(
    account_number="22222222",
    mode=broker_pb2.TradingMode.PAPER,
    gateway_label="futu",
    currency_base="HKD",
)
