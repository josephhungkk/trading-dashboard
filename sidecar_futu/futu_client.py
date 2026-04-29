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

_futu_log_dir = Path(os.environ["HOME"]) / ".com.futunn.FutuOpenD" / "Log"
try:
    _futu_log_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=_futu_log_dir):
        pass
except OSError:
    _futu_import_home = Path(tempfile.gettempdir()) / "futu-home"
    _futu_import_home.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(_futu_import_home)

from futu import (  # noqa: E402
    RET_OK,
    Market,
    OpenQuoteContext,
    OpenSecTradeContext,
    SecurityFirm,
    SecurityType,
    SysConfig,
    TrdMarket,
)

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
            ret, data = trade_ctx.accinfo_query(
                trd_env=trd_env,
                acc_id=int(account_number),
            )
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

    def _write_rsa_tempfile(self) -> None:
        if self._rsa_tempfile_path is not None:
            self._cleanup_rsa_tempfile()
        if self._creds is None:
            raise RuntimeError("FutuClient not configured: missing creds")

        with tempfile.NamedTemporaryFile(delete=False, mode="w") as rsa_file:
            rsa_file.write(self._creds.rsa_priv_pem)
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
