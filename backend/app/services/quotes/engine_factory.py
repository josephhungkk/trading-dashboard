"""Factory that wires the full QuoteEngine pipeline into the lifespan.

Phase 9.5 CRIT-1 fix: ``QuoteEngine`` was defined but never instantiated in
``main.py``, so ``ws.app.state.quote_engine`` was always ``None`` and every
``/ws/quotes`` connection closed with 1011.

``_build_quote_engine`` is the single factory function consumed by the
lifespan. It reads mTLS certs + target hosts from ``ConfigService``, creates
one ``SidecarStream`` per wired source, and assembles the full pipeline.  If
any required secret is missing (broker layer not yet configured) it logs a
warning and returns ``None`` — callers should keep ``state.quote_engine =
None`` in that case; the WS endpoint will 1011 with a structured log rather
than crashing the entire lifespan startup.
"""

from __future__ import annotations

import grpc  # type: ignore[import-untyped]
import structlog
from cryptography.fernet import InvalidToken
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker

from app._generated.broker.v1 import broker_pb2 as pb
from app.services.config import ConfigService
from app.services.config_defaults import (
    DEFAULT_IBKR_GATEWAY_QUOTE_ASSIGNMENT,
    DEFAULT_IBKR_GATEWAY_QUOTE_FALLBACK,
    DEFAULT_QUOTE_SOURCE_PRIORITY,
)
from app.services.quotes.engine import QuoteEngine
from app.services.quotes.registry import SubscriptionRegistry
from app.services.quotes.router import SourceHealthMap, SourceRouter
from app.services.quotes.upstream.sidecar_stream import SidecarStream

_log = structlog.get_logger(__name__)

# ── cap / rate-limit defaults (operator can override via app_config) ──────────
_CAP_PER_WS: int = 100
_CAP_GLOBAL: int = 1000
_SUB_RATE_LIMIT_PER_MINUTE: int = 300

# Sources that use plain insecure gRPC (in-cluster docker — peer trust is
# the network boundary). All others use mTLS over WireGuard.
_INSECURE_SOURCES: frozenset[str] = frozenset({"schwab", "alpaca"})

# Per-source gRPC dial targets (host:port). IBKR and Futu reach the NUC
# over WireGuard; Schwab and Alpaca are in the same docker-compose network
# on the VPS.
_SOURCE_TARGETS: dict[str, str] = {
    "ibkr": "10.10.0.2:18001",  # isa-live; router picks the right gateway
    "futu": "10.10.0.2:18005",
    "schwab": "schwab-sidecar:9090",
    "alpaca": "alpaca-sidecar-live:9091",
}


def _symbol_ref_builder(canonical_id: str) -> pb.SymbolRef:
    """Build a ``SymbolRef`` from a canonical_id string.

    ``raw_symbol`` is set equal to ``canonical_id`` as a safe passthrough —
    each sidecar's ``on_subscribe`` handler maps to its broker-native ticker.
    """
    return pb.SymbolRef(canonical_id=canonical_id, raw_symbol=canonical_id)


async def _noop_on_quote(_q: pb.QuoteMessage) -> None:  # pragma: no cover
    """Placeholder replaced immediately after engine construction."""
    return None


async def build_quote_engine(
    *,
    svc: ConfigService,
    redis: Redis,
    db_factory: async_sessionmaker,  # type: ignore[type-arg]
    host: str = "10.10.0.2",
) -> QuoteEngine | None:
    """Assemble the full quote pipeline and return a ready-to-start
    ``QuoteEngine``, or ``None`` if the broker layer isn't configured yet.

    The caller (lifespan) is responsible for:
    - calling ``await engine.start()`` immediately after this returns.
    - calling ``await engine.stop()`` in the lifespan finally block.
    - closing the gRPC channels stored on ``engine.streams`` (each
      ``SidecarStream`` closes its own channel in ``stop()``).
    """
    # ── mTLS certs (required for IBKR + Futu) ────────────────────────────────
    secret_keys = ("mtls.client_cert_pem", "mtls.client_key_pem", "mtls.ca_bundle_pem")
    secrets: dict[str, bytes] = {}
    for key in secret_keys:
        try:
            value = await svc.reveal_secret("broker", key)
        except InvalidToken as exc:
            _log.warning("quote_engine_factory.mtls_secret_undecryptable", key=key, error=str(exc))
            return None
        if not value:
            _log.warning("quote_engine_factory.mtls_secret_missing", key=key)
            return None
        secrets[key] = str(value).encode()

    cert_pem = secrets["mtls.client_cert_pem"]
    key_pem = secrets["mtls.client_key_pem"]
    ca_bundle_pem = secrets["mtls.ca_bundle_pem"]

    # ── operator-configurable routing config ─────────────────────────────────
    priority_override = await svc.get_json("broker", "quote_source_priority", default=None)
    ibkr_assignment = await svc.get_json("broker", "ibkr_gateway_quote_assignment", default={})
    ibkr_fallback = await svc.get_json("broker", "ibkr_gateway_quote_fallback", default=[])

    router_config: dict[str, object] = {
        "quote_source_priority": priority_override or DEFAULT_QUOTE_SOURCE_PRIORITY,
        "ibkr_gateway_quote_assignment": ibkr_assignment or DEFAULT_IBKR_GATEWAY_QUOTE_ASSIGNMENT,
        "ibkr_gateway_quote_fallback": ibkr_fallback or DEFAULT_IBKR_GATEWAY_QUOTE_FALLBACK,
    }

    health = SourceHealthMap()
    router = SourceRouter(config=router_config, health=health)

    registry = SubscriptionRegistry(
        cap_per_ws=_CAP_PER_WS,
        cap_global=_CAP_GLOBAL,
        sub_rate_limit_per_minute=_SUB_RATE_LIMIT_PER_MINUTE,
    )

    # ── NUC WireGuard host (operator override) ────────────────────────────────
    nuc_host = ((await svc.get("broker", "nuc_wg_host")) or host).strip()
    targets: dict[str, str] = dict(_SOURCE_TARGETS)
    for src in ("ibkr", "futu"):
        port = targets[src].split(":")[-1]
        targets[src] = f"{nuc_host}:{port}"

    # ── one SidecarStream per wired source ────────────────────────────────────
    streams: dict[str, SidecarStream] = {}
    opened_channels: list[grpc.aio.Channel] = []

    for source_id, target in targets.items():
        try:
            if source_id in _INSECURE_SOURCES:
                channel: grpc.aio.Channel = grpc.aio.insecure_channel(target)
            else:
                credentials = grpc.ssl_channel_credentials(
                    root_certificates=ca_bundle_pem,
                    private_key=key_pem,
                    certificate_chain=cert_pem,
                )
                channel = grpc.aio.secure_channel(
                    target,
                    credentials,
                    options=(("grpc.default_authority", f"sidecar-{source_id}"),),
                )
            opened_channels.append(channel)
            stream = SidecarStream(
                source=source_id,
                channel=channel,
                registry=registry,
                on_quote=_noop_on_quote,  # patched after engine construction
                health=health,
                symbol_ref_builder=_symbol_ref_builder,
            )
            streams[source_id] = stream
        except Exception as exc:
            _log.warning(
                "quote_engine_factory.stream_build_failed",
                source=source_id,
                target=target,
                error=str(exc),
            )
            for ch in opened_channels:
                await ch.close(grace=0.5)
            return None

    engine = QuoteEngine(
        registry=registry,
        router=router,
        redis=redis,
        streams=streams,
        db_factory=db_factory,
    )

    # Patch each stream's on_quote callback to the engine's _on_quote handler
    # AFTER engine construction (avoids circular dependency at __init__ time).
    for stream in streams.values():
        stream._on_quote = engine._on_quote

    _log.info(
        "quote_engine_factory.built",
        sources=sorted(streams.keys()),
        cap_per_ws=_CAP_PER_WS,
        cap_global=_CAP_GLOBAL,
    )
    return engine
