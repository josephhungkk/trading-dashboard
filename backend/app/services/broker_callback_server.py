"""Backend-side gRPC server that sidecars call into for RequestTokenRefresh.

Listens on internal port 8001 (BACKEND_ADMIN_GRPC env var on sidecar).
Implements `service BackendCallback` from proto/broker/v1/broker.proto.
"""

from __future__ import annotations

import logging
from typing import Any, cast

import grpc  # type: ignore[import-untyped]
from google.protobuf.timestamp_pb2 import Timestamp  # type: ignore[import-untyped]

from app._generated.broker.v1 import broker_pb2 as pb
from app._generated.broker.v1 import broker_pb2_grpc as pbg
from app.services.config import ConfigService
from app.services.schwab_oauth import refresh_with_lock

log = logging.getLogger(__name__)


class BackendCallbackServicer(pbg.BackendCallbackServicer):  # type: ignore[misc]
    """Implements `service BackendCallback`. Only RPC: RequestTokenRefresh."""

    def __init__(self, config_service: ConfigService, db_session_factory: Any) -> None:
        self._config = config_service
        self._db_factory = db_session_factory

    async def RequestTokenRefresh(  # noqa: N802
        self,
        request: pb.TokenRefreshRequest,
        context: grpc.aio.ServicerContext[Any, Any],
    ) -> pb.TokenRefreshResponse:
        if request.broker_id != "schwab":
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"backend RequestTokenRefresh handles broker=schwab, got {request.broker_id}",
            )
            return pb.TokenRefreshResponse()
        app_key = cast(str, await self._config.reveal_secret("broker", "schwab.app_key"))
        app_secret = cast(str, await self._config.reveal_secret("broker", "schwab.app_secret"))
        refresh = cast(
            str,
            await self._config.reveal_secret("broker", "schwab.refresh_token"),
        )
        async with self._db_factory() as db:
            new_a, new_r, issued = await refresh_with_lock(
                db_session=db,
                config_service=self._config,
                app_key=app_key,
                app_secret=app_secret,
                refresh_token=refresh,
            )
        ts = Timestamp()
        ts.FromDatetime(issued)
        return pb.TokenRefreshResponse(
            access_token=new_a,
            refresh_token=new_r,
            access_issued_at=ts,
        )


async def start_backend_callback_server(
    config_service: ConfigService,
    db_session_factory: Any,
) -> grpc.aio.Server:
    server = grpc.aio.server()
    servicer = BackendCallbackServicer(config_service, db_session_factory)
    pbg.add_BackendCallbackServicer_to_server(servicer, server)
    server.add_insecure_port("0.0.0.0:8001")
    await server.start()
    log.info("backend_callback_grpc_started port=8001")
    return server
