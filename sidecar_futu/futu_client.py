"""Owns OpenSecTradeContext lifecycle, cred caching, in-flight init cancellation."""
from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import os
import re
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import structlog
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.serialization import load_pem_private_key

# Path.home() is cross-platform: $HOME on POSIX, %USERPROFILE% on Windows.
# `os.environ["HOME"]` raises KeyError on Windows where the env var is
# USERPROFILE — which is exactly what futu-api's hardcoded log-dir resolver
# checks internally. We override both HOME and USERPROFILE on the fallback
# path so the SDK picks up our writable temp dir on either OS.
_futu_log_dir = Path.home() / ".com.futunn.FutuOpenD" / "Log"
try:
    _futu_log_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=_futu_log_dir):
        pass
except OSError:
    _futu_import_home = Path(tempfile.gettempdir()) / "futu-home"
    _futu_import_home.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(_futu_import_home)
    os.environ["USERPROFILE"] = str(_futu_import_home)

from futu import (  # noqa: E402
    RET_OK,
    Market,
    ModifyOrderOp,
    OpenQuoteContext,
    OpenSecTradeContext,
    OrderType,
    SecurityFirm,
    SecurityType,
    SysConfig,
    TrdMarket,
    TrdSide,
)

from sidecar_futu._generated.broker.v1 import broker_pb2  # noqa: E402

log = structlog.get_logger(__name__)

_BACKOFF_BASE_S = 1.0
_BACKOFF_MAX_S = 30.0
_MD5_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")


async def _run_in_worker_thread(fn: Callable[[], Any]) -> Any:
    loop = asyncio.get_running_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return await loop.run_in_executor(executor, fn)


@dataclass(frozen=True, repr=False)
class FutuCreds:
    unlock_pwd_md5: str
    rsa_priv_pem: str
    opend_host: str
    opend_port: int
    connection_id: str


class FutuClient:
    """Holds creds and the OpenD connection init task."""

    def __init__(self) -> None:
        self._creds: FutuCreds | None = None
        self._init_task: asyncio.Task[None] | None = None
        self._trade_ctx: Any | None = None
        self._rsa_tempfile_path: Path | None = None
        self.gateway_connected: bool = False
        self._accounts_trd_env: dict[str, str] = {}
        self._order_event_queues: dict[str, list[asyncio.Queue[Any]]] = {}
        self._configure_lock = asyncio.Lock()
        atexit.register(self._cleanup_rsa_tempfile)

    def validate(self, request: Any) -> str | None:
        """Return error detail string on rejection, None on success."""
        if not _MD5_PATTERN.match(request.unlock_pwd_md5):
            return "invalid_unlock_pwd_md5"
        rsa_priv_pem = request.rsa_priv_pem.encode()
        try:
            load_pem_private_key(rsa_priv_pem, password=None)
        except (ValueError, TypeError, UnsupportedAlgorithm):
            return "invalid_rsa_pem"
        return None

    async def configure(self, request: Any) -> None:
        """Cache creds and restart the InitConnect background task."""
        async with self._configure_lock:
            if self._init_task is not None and not self._init_task.done():
                self._init_task.cancel()
                try:
                    await self._init_task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    log.warning("futu_init_task_cleanup_error", error=str(exc))
                    raise

            if self._trade_ctx is not None:
                try:
                    await _run_in_worker_thread(self._trade_ctx.close)
                except Exception as exc:
                    log.warning("futu_trade_ctx_close_failed", error=str(exc))
                self._trade_ctx = None

            self._cleanup_rsa_tempfile()

            new_creds = FutuCreds(
                unlock_pwd_md5=request.unlock_pwd_md5,
                rsa_priv_pem=request.rsa_priv_pem,
                opend_host=request.opend_host,
                opend_port=request.opend_port,
                connection_id=request.connection_id,
            )
            self._creds = new_creds
            self.gateway_connected = False
            self._init_task = asyncio.create_task(
                self._init_loop(),
                name="futu-init-connect",
            )

    async def list_accounts(self) -> list[dict[str, Any]]:
        """Return Futu account rows from the active trade context."""
        trade_ctx = self._trade_ctx
        if trade_ctx is None:
            return []

        def _list_accounts() -> list[dict[str, Any]]:
            ret, data = trade_ctx.get_acc_list()
            if ret != RET_OK:
                raise RuntimeError(f"get_acc_list failed: {data}")
            return cast("list[dict[str, Any]]", data.to_dict("records"))

        rows = cast("list[dict[str, Any]]", await _run_in_worker_thread(_list_accounts))
        self._accounts_trd_env = {
            str(row["acc_id"]): row.get("trd_env", "REAL")
            for row in rows
            if row.get("trd_env") in ("REAL", "SIMULATE")
        }
        return rows

    async def get_account_summary(self, account_number: str) -> dict[str, Any]:
        if not self.gateway_connected or self._trade_ctx is None:
            return {}
        trd_env = self._accounts_trd_env.get(account_number, "REAL")
        trade_ctx = self._trade_ctx

        def _query() -> dict[str, Any]:
            try:
                ret, data = trade_ctx.accinfo_query(
                    trd_env=trd_env,
                    acc_id=int(account_number),
                )
            except Exception as exc:
                log.warning(
                    "futu_accinfo_query_exception",
                    account=account_number,
                    trd_env=trd_env,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return {}
            if ret != RET_OK:
                log.warning(
                    "futu_accinfo_query_failed",
                    account=account_number,
                    trd_env=trd_env,
                    msg=str(data),
                )
                return {}
            if data.empty:
                return {}
            return cast("dict[str, Any]", data.iloc[0].to_dict())

        return cast("dict[str, Any]", await _run_in_worker_thread(_query))

    async def get_positions(self, account_number: str) -> list[dict[str, Any]]:
        if not self.gateway_connected or self._trade_ctx is None:
            return []
        trd_env = self._accounts_trd_env.get(account_number, "REAL")
        trade_ctx = self._trade_ctx

        def _query() -> list[dict[str, Any]]:
            ret, data = trade_ctx.position_list_query(
                trd_env=trd_env,
                acc_id=int(account_number),
            )
            if ret != RET_OK:
                log.warning(
                    "futu_position_list_query_failed",
                    account=account_number,
                    trd_env=trd_env,
                    msg=str(data),
                )
                return []
            return cast("list[dict[str, Any]]", data.to_dict("records"))

        return cast("list[dict[str, Any]]", await _run_in_worker_thread(_query))

    async def get_orders(self, account_number: str) -> list[dict[str, Any]]:
        if not self.gateway_connected or self._trade_ctx is None:
            return []
        trd_env = self._accounts_trd_env.get(account_number, "REAL")
        trade_ctx = self._trade_ctx

        def _query() -> list[dict[str, Any]]:
            ret, data = trade_ctx.order_list_query(
                trd_env=trd_env,
                acc_id=int(account_number),
            )
            if ret != RET_OK:
                log.warning(
                    "futu_order_list_query_failed",
                    account=account_number,
                    trd_env=trd_env,
                    msg=str(data),
                )
                return []
            return cast("list[dict[str, Any]]", data.to_dict("records"))

        return cast("list[dict[str, Any]]", await _run_in_worker_thread(_query))

    async def place_order(self, request: broker_pb2.PlaceOrderRequest) -> tuple[str, str]:
        """Place an order through the active trade context."""
        if not self.gateway_connected or self._trade_ctx is None:
            raise RuntimeError("trade context not connected")
        trade_ctx = self._trade_ctx
        trd_env = self._accounts_trd_env.get(request.account_number, "REAL")

        side = self._order_side_name(request.side)
        order_type_name = self._order_type_name(request.order_type)
        futu_order_type = {
            "LIMIT": OrderType.NORMAL,
            "MARKET": OrderType.MARKET,
            "STOP": OrderType.STOP,
            "STOP_LIMIT": OrderType.STOP_LIMIT,
        }.get(order_type_name)
        if futu_order_type is None:
            raise RuntimeError(f"unsupported order_type: {request.order_type}")

        futu_side = {
            "BUY": TrdSide.BUY,
            "SELL": TrdSide.SELL,
        }.get(side)
        if futu_side is None:
            raise RuntimeError(f"unsupported side: {request.side}")

        time_in_force = self._time_in_force_name(request.tif) or "DAY"
        aux_price = self._float_or_none(request.stop_price)

        def _place() -> tuple[str, str]:
            kwargs: dict[str, Any] = {
                "price": self._float_or_zero(request.limit_price),
                "qty": self._qty_number(request.qty),
                "code": request.conid,
                "trd_side": futu_side,
                "order_type": futu_order_type,
                "trd_env": trd_env,
                "acc_id": int(request.account_number),
                "remark": request.client_order_id[:64],
                "time_in_force": time_in_force,
            }
            if order_type_name in {"STOP", "STOP_LIMIT"}:
                kwargs["aux_price"] = aux_price

            ret, data = trade_ctx.place_order(**kwargs)
            if ret != RET_OK:
                raise RuntimeError(f"place_order_failed: {data}")
            return str(data.iloc[0]["order_id"]), "submitted"

        return cast("tuple[str, str]", await _run_in_worker_thread(_place))

    async def cancel_order(self, account_number: str, broker_order_id: str) -> bool:
        """Cancel an order through the active trade context."""
        if not self.gateway_connected or self._trade_ctx is None:
            return False
        trade_ctx = self._trade_ctx
        trd_env = self._accounts_trd_env.get(account_number, "REAL")

        def _cancel() -> bool:
            ret, _ = trade_ctx.modify_order(
                ModifyOrderOp.CANCEL,
                order_id=int(broker_order_id),
                qty=0,
                price=0,
                trd_env=trd_env,
                acc_id=int(account_number),
            )
            return bool(ret == RET_OK)

        return cast("bool", await _run_in_worker_thread(_cancel))

    async def search_contracts(self, query: str) -> list[dict[str, Any]]:
        if not self.gateway_connected or self._creds is None:
            return []
        creds = self._creds
        needle = query.strip().casefold()
        code_list = [query.strip()] if "." in query else None

        def _query() -> list[dict[str, Any]]:
            quote_ctx: Any | None = None
            try:
                quote_ctx = OpenQuoteContext(
                    host=creds.opend_host,
                    port=creds.opend_port,
                    is_encrypt=True,
                )
                ret, data = quote_ctx.get_stock_basicinfo(
                    Market.HK,
                    SecurityType.STOCK,
                    code_list=code_list,
                )
                if ret != RET_OK:
                    log.warning("futu_get_stock_basicinfo_failed", query=query, msg=str(data))
                    return []

                rows: list[dict[str, Any]] = []
                for row in data.to_dict("records"):
                    code = str(row.get("code", ""))
                    name = str(row.get("name", ""))
                    if needle and needle not in code.casefold() and needle not in name.casefold():
                        continue
                    remapped = dict(row)
                    remapped["stock_name"] = name
                    remapped["security_type"] = row.get("stock_type", "")
                    remapped["currency"] = "HKD"
                    rows.append(remapped)
                return rows
            finally:
                if quote_ctx is not None:
                    quote_ctx.close()

        return cast("list[dict[str, Any]]", await _run_in_worker_thread(_query))

    def _on_order_update(self, account_number: str, futu_row: dict[str, Any]) -> None:
        """Called by TradeOrderHandlerBase callback (futu-api thread)."""
        queues = self._order_event_queues.get(account_number, [])
        if not queues:
            return
        from sidecar_futu.normalize import order_event_from_futu_order_row

        event = order_event_from_futu_order_row(futu_row)
        self._dispatch_to_queues(queues, event, account_number)

    def _on_deal_update(self, account_number: str, futu_row: dict[str, Any]) -> None:
        """Called by TradeDealHandlerBase. Emits exec_details + commission_report."""
        queues = self._order_event_queues.get(account_number, [])
        if not queues:
            return
        from sidecar_futu.normalize import (
            commission_event_from_futu_deal_row,
            order_event_from_futu_deal_row,
        )

        for ev in (
            order_event_from_futu_deal_row(futu_row),
            commission_event_from_futu_deal_row(futu_row),
        ):
            self._dispatch_to_queues(queues, ev, account_number)

    def _dispatch_to_queues(
        self,
        queues: list[asyncio.Queue[Any]],
        event: Any,
        account_number: str,
    ) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        for q in queues:
            if loop is not None:
                loop.call_soon_threadsafe(self._safe_put, q, event, account_number)
            else:
                self._safe_put(q, event, account_number)

    def _safe_put(
        self, q: asyncio.Queue[Any], event: Any, account_number: str
    ) -> None:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            log.warning("orderevent_queue_full", account=account_number)

    @staticmethod
    def _enum_name(enum_type: Any, value: object) -> str:
        if isinstance(value, int):
            try:
                return cast("str", enum_type.Name(value))
            except ValueError:
                return ""
        return str(value or "").upper()

    @classmethod
    def _order_side_name(cls, value: object) -> str:
        return cls._enum_name(broker_pb2.OrderSide, value)

    @classmethod
    def _order_type_name(cls, value: object) -> str:
        return cls._enum_name(broker_pb2.OrderType, value)

    @classmethod
    def _time_in_force_name(cls, value: object) -> str:
        return cls._enum_name(broker_pb2.TimeInForce, value)

    @staticmethod
    def _float_or_zero(value: str) -> float:
        return float(value) if value else 0.0

    @staticmethod
    def _float_or_none(value: str) -> float | None:
        return float(value) if value else None

    @staticmethod
    def _qty_number(value: str) -> int | float:
        qty = float(value)
        return int(qty) if qty.is_integer() else qty

    def _write_rsa_tempfile(self) -> None:
        if self._rsa_tempfile_path is not None:
            self._cleanup_rsa_tempfile()
        if self._creds is None:
            raise RuntimeError("FutuClient not configured: missing creds")

        # Round-trip the PEM through cryptography to canonicalize line wrapping.
        # PEM-as-stored-in-app_secrets often loses newlines during JSON encoding
        # (curl/jq strip embedded LFs). cryptography parses single-line PEM
        # tolerantly; pycryptodome (used by futu SDK) is strict and rejects
        # anything <3 lines with "A PEM file must have at least 3 lines".
        # Re-emitting via TraditionalOpenSSL produces canonical multi-line
        # PKCS#1 RSA which pycryptodome accepts.
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            NoEncryption,
            PrivateFormat,
            load_pem_private_key,
        )

        key = load_pem_private_key(
            self._creds.rsa_priv_pem.encode(), password=None
        )
        canonical_pem = key.private_bytes(
            encoding=Encoding.PEM,
            format=PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=NoEncryption(),
        )

        with tempfile.NamedTemporaryFile(delete=False, mode="wb") as rsa_file:
            rsa_file.write(canonical_pem)
            rsa_file_path = Path(rsa_file.name)

        os.chmod(rsa_file_path, stat.S_IRUSR | stat.S_IWUSR)
        self._rsa_tempfile_path = rsa_file_path

    def _cleanup_rsa_tempfile(self) -> None:
        if self._rsa_tempfile_path is None:
            return
        try:
            os.unlink(self._rsa_tempfile_path)
        except FileNotFoundError:
            pass
        self._rsa_tempfile_path = None

    async def _init_attempt(self) -> None:
        if self._creds is None:
            raise RuntimeError("FutuClient not configured: missing creds")
        creds = self._creds
        self._write_rsa_tempfile()
        rsa_path_str = str(self._rsa_tempfile_path)

        def _connect() -> OpenSecTradeContext:
            ctx: Any | None = None
            SysConfig.enable_proto_encrypt(True)
            SysConfig.set_init_rsa_file(rsa_path_str)
            try:
                ctx = OpenSecTradeContext(
                    filter_trdmarket=TrdMarket.HK,
                    host=creds.opend_host,
                    port=creds.opend_port,
                    is_encrypt=True,
                    security_firm=SecurityFirm.FUTUSECURITIES,
                )
                ret, msg = ctx.unlock_trade(password_md5=creds.unlock_pwd_md5)
                if ret != RET_OK:
                    ctx.close()
                    ctx = None
                    raise RuntimeError(f"unlock_trade failed: {msg}")
                return ctx
            except Exception:
                if ctx is not None:
                    ctx.close()
                raise

        ctx = await _run_in_worker_thread(_connect)
        self._trade_ctx = ctx
        self.gateway_connected = True
        log.info("futu_init_connected", host=creds.opend_host)

    async def _init_loop(self) -> None:
        await asyncio.sleep(0)
        backoff = _BACKOFF_BASE_S
        while True:
            try:
                await self._init_attempt()
                backoff = _BACKOFF_BASE_S
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("futu_init_connect_error", error=str(exc))
                self.gateway_connected = False
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX_S)
