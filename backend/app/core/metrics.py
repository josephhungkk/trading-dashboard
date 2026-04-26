"""prometheus-client counters/gauges for Phase 2 observability."""

from prometheus_client import CollectorRegistry, Counter, Gauge

registry = CollectorRegistry(auto_describe=True)


cf_jwt_verification_total = Counter(
    "cf_jwt_verification_total",
    "CF Access JWT verification outcomes",
    labelnames=["result"],
    registry=registry,
)

config_ops_total = Counter(
    "config_ops_total",
    "Config/secret operations",
    labelnames=["op", "kind", "result"],
    registry=registry,
)

config_cache_size = Gauge(
    "config_cache_size",
    "Entries currently in the per-worker cache",
    labelnames=["kind"],
    registry=registry,
)

redis_publish_fail_total = Counter(
    "redis_publish_fail_total",
    "Redis publish errors during config invalidation",
    labelnames=["channel"],
    registry=registry,
)

redis_subscribe_reconnect_total = Counter(
    "redis_subscribe_reconnect_total",
    "Redis subscribe reconnect attempts",
    labelnames=["channel"],
    registry=registry,
)

fernet_prev_key_hits_total = Counter(
    "fernet_prev_key_hits_total",
    "Reveals decrypted via the PREV Fernet key (rotation indicator)",
    registry=registry,
)

admin_secret_reveal_total = Counter(
    "admin_secret_reveal_total",
    "Plaintext reveal operations on /api/admin/secrets/*/reveal",
    labelnames=["actor_kind"],
    registry=registry,
)

avg_cost_unit_suspected_wrong_total = Counter(
    "avg_cost_unit_suspected_wrong_total",
    "Positions where avg_cost appears to be in wrong currency unit (GBX vs GBP)",
    labelnames=["account_id"],
    registry=registry,
)
