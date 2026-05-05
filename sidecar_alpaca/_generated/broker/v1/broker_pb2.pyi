import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class BrokerId(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    BROKER_UNSPECIFIED: _ClassVar[BrokerId]
    IBKR: _ClassVar[BrokerId]
    FUTU: _ClassVar[BrokerId]
    SCHWAB: _ClassVar[BrokerId]

class TradingMode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MODE_UNSPECIFIED: _ClassVar[TradingMode]
    LIVE: _ClassVar[TradingMode]
    PAPER: _ClassVar[TradingMode]

class AssetClass(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ASSET_UNSPECIFIED: _ClassVar[AssetClass]
    STOCK: _ClassVar[AssetClass]
    ETF: _ClassVar[AssetClass]
    OPTION: _ClassVar[AssetClass]
    FUTURE: _ClassVar[AssetClass]
    FOREX: _ClassVar[AssetClass]
    CRYPTO: _ClassVar[AssetClass]
    BOND: _ClassVar[AssetClass]
    MUTUAL_FUND: _ClassVar[AssetClass]
    WARRANT: _ClassVar[AssetClass]
    CBBC: _ClassVar[AssetClass]

class OrderSide(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SIDE_UNSPECIFIED: _ClassVar[OrderSide]
    BUY: _ClassVar[OrderSide]
    SELL: _ClassVar[OrderSide]

class OrderType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TYPE_UNSPECIFIED: _ClassVar[OrderType]
    MARKET: _ClassVar[OrderType]
    LIMIT: _ClassVar[OrderType]
    STOP: _ClassVar[OrderType]
    STOP_LIMIT: _ClassVar[OrderType]

class TimeInForce(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TIF_UNSPECIFIED: _ClassVar[TimeInForce]
    DAY: _ClassVar[TimeInForce]
    GTC: _ClassVar[TimeInForce]
    IOC: _ClassVar[TimeInForce]
    FOK: _ClassVar[TimeInForce]

class OrderStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    STATUS_UNSPECIFIED: _ClassVar[OrderStatus]
    PENDING: _ClassVar[OrderStatus]
    SUBMITTED: _ClassVar[OrderStatus]
    PARTIAL: _ClassVar[OrderStatus]
    FILLED: _ClassVar[OrderStatus]
    CANCELLED: _ClassVar[OrderStatus]
    REJECTED: _ClassVar[OrderStatus]
BROKER_UNSPECIFIED: BrokerId
IBKR: BrokerId
FUTU: BrokerId
SCHWAB: BrokerId
MODE_UNSPECIFIED: TradingMode
LIVE: TradingMode
PAPER: TradingMode
ASSET_UNSPECIFIED: AssetClass
STOCK: AssetClass
ETF: AssetClass
OPTION: AssetClass
FUTURE: AssetClass
FOREX: AssetClass
CRYPTO: AssetClass
BOND: AssetClass
MUTUAL_FUND: AssetClass
WARRANT: AssetClass
CBBC: AssetClass
SIDE_UNSPECIFIED: OrderSide
BUY: OrderSide
SELL: OrderSide
TYPE_UNSPECIFIED: OrderType
MARKET: OrderType
LIMIT: OrderType
STOP: OrderType
STOP_LIMIT: OrderType
TIF_UNSPECIFIED: TimeInForce
DAY: TimeInForce
GTC: TimeInForce
IOC: TimeInForce
FOK: TimeInForce
STATUS_UNSPECIFIED: OrderStatus
PENDING: OrderStatus
SUBMITTED: OrderStatus
PARTIAL: OrderStatus
FILLED: OrderStatus
CANCELLED: OrderStatus
REJECTED: OrderStatus

class Empty(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class HealthRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class HealthResponse(_message.Message):
    __slots__ = ("label", "gateway_connected", "gateway_version", "last_tick_at", "sidecar_version", "started_at", "broker_id")
    LABEL_FIELD_NUMBER: _ClassVar[int]
    GATEWAY_CONNECTED_FIELD_NUMBER: _ClassVar[int]
    GATEWAY_VERSION_FIELD_NUMBER: _ClassVar[int]
    LAST_TICK_AT_FIELD_NUMBER: _ClassVar[int]
    SIDECAR_VERSION_FIELD_NUMBER: _ClassVar[int]
    STARTED_AT_FIELD_NUMBER: _ClassVar[int]
    BROKER_ID_FIELD_NUMBER: _ClassVar[int]
    label: str
    gateway_connected: bool
    gateway_version: str
    last_tick_at: _timestamp_pb2.Timestamp
    sidecar_version: str
    started_at: _timestamp_pb2.Timestamp
    broker_id: str
    def __init__(self, label: _Optional[str] = ..., gateway_connected: bool = ..., gateway_version: _Optional[str] = ..., last_tick_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., sidecar_version: _Optional[str] = ..., started_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., broker_id: _Optional[str] = ...) -> None: ...

class Account(_message.Message):
    __slots__ = ("account_number", "mode", "gateway_label", "currency_base", "account_hash")
    ACCOUNT_NUMBER_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    GATEWAY_LABEL_FIELD_NUMBER: _ClassVar[int]
    CURRENCY_BASE_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_HASH_FIELD_NUMBER: _ClassVar[int]
    account_number: str
    mode: TradingMode
    gateway_label: str
    currency_base: str
    account_hash: str
    def __init__(self, account_number: _Optional[str] = ..., mode: _Optional[_Union[TradingMode, str]] = ..., gateway_label: _Optional[str] = ..., currency_base: _Optional[str] = ..., account_hash: _Optional[str] = ...) -> None: ...

class AccountsResponse(_message.Message):
    __slots__ = ("accounts",)
    ACCOUNTS_FIELD_NUMBER: _ClassVar[int]
    accounts: _containers.RepeatedCompositeFieldContainer[Account]
    def __init__(self, accounts: _Optional[_Iterable[_Union[Account, _Mapping]]] = ...) -> None: ...

class AccountRef(_message.Message):
    __slots__ = ("account_number",)
    ACCOUNT_NUMBER_FIELD_NUMBER: _ClassVar[int]
    account_number: str
    def __init__(self, account_number: _Optional[str] = ...) -> None: ...

class Money(_message.Message):
    __slots__ = ("value", "currency")
    VALUE_FIELD_NUMBER: _ClassVar[int]
    CURRENCY_FIELD_NUMBER: _ClassVar[int]
    value: str
    currency: str
    def __init__(self, value: _Optional[str] = ..., currency: _Optional[str] = ...) -> None: ...

class Summary(_message.Message):
    __slots__ = ("net_liquidation", "total_cash", "realized_pnl", "unrealized_pnl", "buying_power", "updated_at")
    NET_LIQUIDATION_FIELD_NUMBER: _ClassVar[int]
    TOTAL_CASH_FIELD_NUMBER: _ClassVar[int]
    REALIZED_PNL_FIELD_NUMBER: _ClassVar[int]
    UNREALIZED_PNL_FIELD_NUMBER: _ClassVar[int]
    BUYING_POWER_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    net_liquidation: Money
    total_cash: Money
    realized_pnl: Money
    unrealized_pnl: Money
    buying_power: Money
    updated_at: _timestamp_pb2.Timestamp
    def __init__(self, net_liquidation: _Optional[_Union[Money, _Mapping]] = ..., total_cash: _Optional[_Union[Money, _Mapping]] = ..., realized_pnl: _Optional[_Union[Money, _Mapping]] = ..., unrealized_pnl: _Optional[_Union[Money, _Mapping]] = ..., buying_power: _Optional[_Union[Money, _Mapping]] = ..., updated_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class SummaryResponse(_message.Message):
    __slots__ = ("summary",)
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    summary: Summary
    def __init__(self, summary: _Optional[_Union[Summary, _Mapping]] = ...) -> None: ...

class Contract(_message.Message):
    __slots__ = ("symbol", "exchange", "currency", "asset_class", "conid", "local_symbol", "multiplier")
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    EXCHANGE_FIELD_NUMBER: _ClassVar[int]
    CURRENCY_FIELD_NUMBER: _ClassVar[int]
    ASSET_CLASS_FIELD_NUMBER: _ClassVar[int]
    CONID_FIELD_NUMBER: _ClassVar[int]
    LOCAL_SYMBOL_FIELD_NUMBER: _ClassVar[int]
    MULTIPLIER_FIELD_NUMBER: _ClassVar[int]
    symbol: str
    exchange: str
    currency: str
    asset_class: AssetClass
    conid: str
    local_symbol: str
    multiplier: str
    def __init__(self, symbol: _Optional[str] = ..., exchange: _Optional[str] = ..., currency: _Optional[str] = ..., asset_class: _Optional[_Union[AssetClass, str]] = ..., conid: _Optional[str] = ..., local_symbol: _Optional[str] = ..., multiplier: _Optional[str] = ...) -> None: ...

class ContractRef(_message.Message):
    __slots__ = ("conid",)
    CONID_FIELD_NUMBER: _ClassVar[int]
    conid: str
    def __init__(self, conid: _Optional[str] = ...) -> None: ...

class ContractResponse(_message.Message):
    __slots__ = ("contract",)
    CONTRACT_FIELD_NUMBER: _ClassVar[int]
    contract: Contract
    def __init__(self, contract: _Optional[_Union[Contract, _Mapping]] = ...) -> None: ...

class Position(_message.Message):
    __slots__ = ("contract", "quantity", "avg_cost", "market_price", "market_value", "unrealized_pnl", "realized_pnl_today", "daily_pnl")
    CONTRACT_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    AVG_COST_FIELD_NUMBER: _ClassVar[int]
    MARKET_PRICE_FIELD_NUMBER: _ClassVar[int]
    MARKET_VALUE_FIELD_NUMBER: _ClassVar[int]
    UNREALIZED_PNL_FIELD_NUMBER: _ClassVar[int]
    REALIZED_PNL_TODAY_FIELD_NUMBER: _ClassVar[int]
    DAILY_PNL_FIELD_NUMBER: _ClassVar[int]
    contract: Contract
    quantity: str
    avg_cost: Money
    market_price: Money
    market_value: Money
    unrealized_pnl: Money
    realized_pnl_today: Money
    daily_pnl: Money
    def __init__(self, contract: _Optional[_Union[Contract, _Mapping]] = ..., quantity: _Optional[str] = ..., avg_cost: _Optional[_Union[Money, _Mapping]] = ..., market_price: _Optional[_Union[Money, _Mapping]] = ..., market_value: _Optional[_Union[Money, _Mapping]] = ..., unrealized_pnl: _Optional[_Union[Money, _Mapping]] = ..., realized_pnl_today: _Optional[_Union[Money, _Mapping]] = ..., daily_pnl: _Optional[_Union[Money, _Mapping]] = ...) -> None: ...

class PositionsResponse(_message.Message):
    __slots__ = ("positions",)
    POSITIONS_FIELD_NUMBER: _ClassVar[int]
    positions: _containers.RepeatedCompositeFieldContainer[Position]
    def __init__(self, positions: _Optional[_Iterable[_Union[Position, _Mapping]]] = ...) -> None: ...

class Order(_message.Message):
    __slots__ = ("order_id", "contract", "side", "order_type", "quantity", "limit_price", "stop_price", "time_in_force", "status", "quantity_filled", "avg_fill_price", "submitted_at", "updated_at", "avg_fill_price_inferred")
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    CONTRACT_FIELD_NUMBER: _ClassVar[int]
    SIDE_FIELD_NUMBER: _ClassVar[int]
    ORDER_TYPE_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    LIMIT_PRICE_FIELD_NUMBER: _ClassVar[int]
    STOP_PRICE_FIELD_NUMBER: _ClassVar[int]
    TIME_IN_FORCE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FILLED_FIELD_NUMBER: _ClassVar[int]
    AVG_FILL_PRICE_FIELD_NUMBER: _ClassVar[int]
    SUBMITTED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    AVG_FILL_PRICE_INFERRED_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    contract: Contract
    side: OrderSide
    order_type: OrderType
    quantity: str
    limit_price: Money
    stop_price: Money
    time_in_force: TimeInForce
    status: OrderStatus
    quantity_filled: str
    avg_fill_price: Money
    submitted_at: _timestamp_pb2.Timestamp
    updated_at: _timestamp_pb2.Timestamp
    avg_fill_price_inferred: bool
    def __init__(self, order_id: _Optional[str] = ..., contract: _Optional[_Union[Contract, _Mapping]] = ..., side: _Optional[_Union[OrderSide, str]] = ..., order_type: _Optional[_Union[OrderType, str]] = ..., quantity: _Optional[str] = ..., limit_price: _Optional[_Union[Money, _Mapping]] = ..., stop_price: _Optional[_Union[Money, _Mapping]] = ..., time_in_force: _Optional[_Union[TimeInForce, str]] = ..., status: _Optional[_Union[OrderStatus, str]] = ..., quantity_filled: _Optional[str] = ..., avg_fill_price: _Optional[_Union[Money, _Mapping]] = ..., submitted_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., updated_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., avg_fill_price_inferred: bool = ...) -> None: ...

class OrdersResponse(_message.Message):
    __slots__ = ("orders",)
    ORDERS_FIELD_NUMBER: _ClassVar[int]
    orders: _containers.RepeatedCompositeFieldContainer[Order]
    def __init__(self, orders: _Optional[_Iterable[_Union[Order, _Mapping]]] = ...) -> None: ...

class PlaceOrderRequest(_message.Message):
    __slots__ = ("account_number", "client_order_id", "conid", "side", "order_type", "tif", "qty", "limit_price", "stop_price")
    ACCOUNT_NUMBER_FIELD_NUMBER: _ClassVar[int]
    CLIENT_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    CONID_FIELD_NUMBER: _ClassVar[int]
    SIDE_FIELD_NUMBER: _ClassVar[int]
    ORDER_TYPE_FIELD_NUMBER: _ClassVar[int]
    TIF_FIELD_NUMBER: _ClassVar[int]
    QTY_FIELD_NUMBER: _ClassVar[int]
    LIMIT_PRICE_FIELD_NUMBER: _ClassVar[int]
    STOP_PRICE_FIELD_NUMBER: _ClassVar[int]
    account_number: str
    client_order_id: str
    conid: str
    side: str
    order_type: str
    tif: str
    qty: str
    limit_price: str
    stop_price: str
    def __init__(self, account_number: _Optional[str] = ..., client_order_id: _Optional[str] = ..., conid: _Optional[str] = ..., side: _Optional[str] = ..., order_type: _Optional[str] = ..., tif: _Optional[str] = ..., qty: _Optional[str] = ..., limit_price: _Optional[str] = ..., stop_price: _Optional[str] = ...) -> None: ...

class PlaceOrderResponse(_message.Message):
    __slots__ = ("broker_order_id", "status")
    BROKER_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    broker_order_id: str
    status: str
    def __init__(self, broker_order_id: _Optional[str] = ..., status: _Optional[str] = ...) -> None: ...

class ModifyOrderRequest(_message.Message):
    __slots__ = ("broker_order_id", "account_number", "contract", "side", "order_type", "tif", "qty", "limit_price", "stop_price", "client_order_id")
    BROKER_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_NUMBER_FIELD_NUMBER: _ClassVar[int]
    CONTRACT_FIELD_NUMBER: _ClassVar[int]
    SIDE_FIELD_NUMBER: _ClassVar[int]
    ORDER_TYPE_FIELD_NUMBER: _ClassVar[int]
    TIF_FIELD_NUMBER: _ClassVar[int]
    QTY_FIELD_NUMBER: _ClassVar[int]
    LIMIT_PRICE_FIELD_NUMBER: _ClassVar[int]
    STOP_PRICE_FIELD_NUMBER: _ClassVar[int]
    CLIENT_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    broker_order_id: str
    account_number: str
    contract: Contract
    side: OrderSide
    order_type: OrderType
    tif: TimeInForce
    qty: str
    limit_price: Money
    stop_price: Money
    client_order_id: str
    def __init__(self, broker_order_id: _Optional[str] = ..., account_number: _Optional[str] = ..., contract: _Optional[_Union[Contract, _Mapping]] = ..., side: _Optional[_Union[OrderSide, str]] = ..., order_type: _Optional[_Union[OrderType, str]] = ..., tif: _Optional[_Union[TimeInForce, str]] = ..., qty: _Optional[str] = ..., limit_price: _Optional[_Union[Money, _Mapping]] = ..., stop_price: _Optional[_Union[Money, _Mapping]] = ..., client_order_id: _Optional[str] = ...) -> None: ...

class ModifyOrderResponse(_message.Message):
    __slots__ = ("broker_order_id", "status")
    BROKER_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    broker_order_id: str
    status: str
    def __init__(self, broker_order_id: _Optional[str] = ..., status: _Optional[str] = ...) -> None: ...

class PlaceBracketRequest(_message.Message):
    __slots__ = ("parent", "stop_loss", "take_profit", "oca_group", "has_stop_loss", "has_take_profit")
    PARENT_FIELD_NUMBER: _ClassVar[int]
    STOP_LOSS_FIELD_NUMBER: _ClassVar[int]
    TAKE_PROFIT_FIELD_NUMBER: _ClassVar[int]
    OCA_GROUP_FIELD_NUMBER: _ClassVar[int]
    HAS_STOP_LOSS_FIELD_NUMBER: _ClassVar[int]
    HAS_TAKE_PROFIT_FIELD_NUMBER: _ClassVar[int]
    parent: PlaceOrderRequest
    stop_loss: PlaceOrderRequest
    take_profit: PlaceOrderRequest
    oca_group: str
    has_stop_loss: bool
    has_take_profit: bool
    def __init__(self, parent: _Optional[_Union[PlaceOrderRequest, _Mapping]] = ..., stop_loss: _Optional[_Union[PlaceOrderRequest, _Mapping]] = ..., take_profit: _Optional[_Union[PlaceOrderRequest, _Mapping]] = ..., oca_group: _Optional[str] = ..., has_stop_loss: bool = ..., has_take_profit: bool = ...) -> None: ...

class PlaceBracketResponse(_message.Message):
    __slots__ = ("parent_broker_order_id", "stop_loss_broker_order_id", "take_profit_broker_order_id", "status")
    PARENT_BROKER_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    STOP_LOSS_BROKER_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    TAKE_PROFIT_BROKER_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    parent_broker_order_id: str
    stop_loss_broker_order_id: str
    take_profit_broker_order_id: str
    status: str
    def __init__(self, parent_broker_order_id: _Optional[str] = ..., stop_loss_broker_order_id: _Optional[str] = ..., take_profit_broker_order_id: _Optional[str] = ..., status: _Optional[str] = ...) -> None: ...

class CancelOrderRequest(_message.Message):
    __slots__ = ("account_number", "broker_order_id")
    ACCOUNT_NUMBER_FIELD_NUMBER: _ClassVar[int]
    BROKER_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    account_number: str
    broker_order_id: str
    def __init__(self, account_number: _Optional[str] = ..., broker_order_id: _Optional[str] = ...) -> None: ...

class CancelOrderResponse(_message.Message):
    __slots__ = ("accepted",)
    ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    accepted: bool
    def __init__(self, accepted: bool = ...) -> None: ...

class OrderEventMessage(_message.Message):
    __slots__ = ("broker_order_id", "client_order_id", "status", "filled_qty", "avg_fill_price", "event_at", "raw_payload", "exec_id", "kind")
    BROKER_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    CLIENT_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    FILLED_QTY_FIELD_NUMBER: _ClassVar[int]
    AVG_FILL_PRICE_FIELD_NUMBER: _ClassVar[int]
    EVENT_AT_FIELD_NUMBER: _ClassVar[int]
    RAW_PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    EXEC_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    broker_order_id: str
    client_order_id: str
    status: str
    filled_qty: str
    avg_fill_price: str
    event_at: _timestamp_pb2.Timestamp
    raw_payload: str
    exec_id: str
    kind: str
    def __init__(self, broker_order_id: _Optional[str] = ..., client_order_id: _Optional[str] = ..., status: _Optional[str] = ..., filled_qty: _Optional[str] = ..., avg_fill_price: _Optional[str] = ..., event_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., raw_payload: _Optional[str] = ..., exec_id: _Optional[str] = ..., kind: _Optional[str] = ...) -> None: ...

class SearchContractsRequest(_message.Message):
    __slots__ = ("query", "asset_class")
    QUERY_FIELD_NUMBER: _ClassVar[int]
    ASSET_CLASS_FIELD_NUMBER: _ClassVar[int]
    query: str
    asset_class: str
    def __init__(self, query: _Optional[str] = ..., asset_class: _Optional[str] = ...) -> None: ...

class SearchContractsResponse(_message.Message):
    __slots__ = ("contracts",)
    CONTRACTS_FIELD_NUMBER: _ClassVar[int]
    contracts: _containers.RepeatedCompositeFieldContainer[Contract]
    def __init__(self, contracts: _Optional[_Iterable[_Union[Contract, _Mapping]]] = ...) -> None: ...

class ConfigureRequest(_message.Message):
    __slots__ = ("unlock_pwd_md5", "rsa_priv_pem", "opend_host", "opend_port", "connection_id", "metadata")
    class MetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    UNLOCK_PWD_MD5_FIELD_NUMBER: _ClassVar[int]
    RSA_PRIV_PEM_FIELD_NUMBER: _ClassVar[int]
    OPEND_HOST_FIELD_NUMBER: _ClassVar[int]
    OPEND_PORT_FIELD_NUMBER: _ClassVar[int]
    CONNECTION_ID_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    unlock_pwd_md5: str
    rsa_priv_pem: str
    opend_host: str
    opend_port: int
    connection_id: str
    metadata: _containers.ScalarMap[str, str]
    def __init__(self, unlock_pwd_md5: _Optional[str] = ..., rsa_priv_pem: _Optional[str] = ..., opend_host: _Optional[str] = ..., opend_port: _Optional[int] = ..., connection_id: _Optional[str] = ..., metadata: _Optional[_Mapping[str, str]] = ...) -> None: ...

class ConfigureResponse(_message.Message):
    __slots__ = ("ok", "detail")
    OK_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    ok: bool
    detail: str
    def __init__(self, ok: bool = ..., detail: _Optional[str] = ...) -> None: ...

class TokenRefreshRequest(_message.Message):
    __slots__ = ("broker_id",)
    BROKER_ID_FIELD_NUMBER: _ClassVar[int]
    broker_id: str
    def __init__(self, broker_id: _Optional[str] = ...) -> None: ...

class TokenRefreshResponse(_message.Message):
    __slots__ = ("access_token", "refresh_token", "access_issued_at")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    REFRESH_TOKEN_FIELD_NUMBER: _ClassVar[int]
    ACCESS_ISSUED_AT_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    refresh_token: str
    access_issued_at: _timestamp_pb2.Timestamp
    def __init__(self, access_token: _Optional[str] = ..., refresh_token: _Optional[str] = ..., access_issued_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...
