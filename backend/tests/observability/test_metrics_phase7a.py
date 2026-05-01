"""Phase 7a A7 — Schwab Prometheus metrics registered with correct label sets."""

from app.core.metrics import (
    SCHWAB_ACCESS_TOKEN_AGE_SECONDS,
    SCHWAB_ACCOUNT_HASH_REFRESH_TOTAL,
    SCHWAB_HTTP_REQUESTS_TOTAL,
    SCHWAB_OAUTH_CALLBACK_TOTAL,
    SCHWAB_OAUTH_START_TOTAL,
    SCHWAB_REFRESH_TOKEN_AGE_HOURS,
    SCHWAB_REFRESH_TOKEN_USES_PER_24H,
    SCHWAB_SIDECAR_TOKEN_DRIFT_SECONDS,
    SCHWAB_TIER2_LAST_RUN_TIMESTAMP_SECONDS,
    SCHWAB_TIER2_REFRESH_TOTAL,
)


def test_oauth_start_counter():
    SCHWAB_OAUTH_START_TOTAL.inc()


def test_oauth_callback_labels():
    SCHWAB_OAUTH_CALLBACK_TOTAL.labels(path="public", result="success").inc()
    SCHWAB_OAUTH_CALLBACK_TOTAL.labels(path="admin", result="state_mismatch").inc()


def test_account_hash_refresh_labels():
    for r in ("initial", "rotation_detected", "404_retry"):
        SCHWAB_ACCOUNT_HASH_REFRESH_TOTAL.labels(reason=r).inc()


def test_http_requests_labels():
    SCHWAB_HTTP_REQUESTS_TOTAL.labels(endpoint="/accounts", status="200").inc()
    SCHWAB_HTTP_REQUESTS_TOTAL.labels(endpoint="/accountNumbers", status="429").inc()


def test_tier2_refresh_labels():
    for r in (
        "success",
        "login_failed",
        "mfa_failed",
        "dom_changed",
        "network_error",
        "auto_disabled",
    ):
        SCHWAB_TIER2_REFRESH_TOTAL.labels(result=r).inc()


def test_gauge_set():
    SCHWAB_ACCESS_TOKEN_AGE_SECONDS.set(1500)
    SCHWAB_REFRESH_TOKEN_AGE_HOURS.set(72)
    SCHWAB_REFRESH_TOKEN_USES_PER_24H.set(2)
    SCHWAB_SIDECAR_TOKEN_DRIFT_SECONDS.set(0)
    SCHWAB_TIER2_LAST_RUN_TIMESTAMP_SECONDS.set(1714492800)
