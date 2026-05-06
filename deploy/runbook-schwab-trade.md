# Schwab Trade Runbook

Covers the 6 Prometheus alerts shipped at v0.8.0-rc1 for Phase 8a (Schwab order execution +
order-capability gate). All metric names are the API contract — do not rename.

## Phase 8a deferred

The capability flip endpoint (A5: `POST /api/admin/order-capabilities/{broker}`) and the
frontend order-capability UI land in v0.8.0 proper, not rc1. This runbook covers only the
metrics and alerting wired at rc1.

---

## SchwabOrderPollerStalled

**Anchor:** `#schwaborderpollerstalled`

The fast-cadence (2 s) Schwab order poller was recently active for an account but its
iteration rate has dropped to zero. The sidecar is no longer polling for order status updates.

**Probable causes:**
- `sidecar_schwab` container crashed or was OOM-killed.
- Schwab token expired and the `Configure` RPC blocked on refresh, then timed out.
- The account was deregistered from the gateway while the container was running.

**Diagnostic steps:**
```
docker compose -f /home/joseph/dashboard/docker-compose.yml logs --tail=100 sidecar_schwab
```
Grafana query: `sum by (gateway_label, account_id)(rate(schwab_order_poller_iterations_total{cadence="2s"}[5m]))`

**Mitigation:**
Restart the sidecar: `docker compose restart sidecar_schwab`. If token has expired,
re-authorize via `/api/schwab/authorize` in the UI and restart. If the account was removed,
silence the alert.

---

## SchwabPlaceOrderError

**Anchor:** `#schwabplaceordererror`

More than 10% of place-order REST calls (endpoint `"/accounts.orders.place"`) returned
non-2xx status over the past 5 minutes.

**Probable causes:**
- Schwab access token expired mid-request.
- Schwab API outage or rate-limit (429).
- Invalid order payload rejected by Schwab (400).

**Diagnostic steps:**
```
docker compose logs --tail=200 sidecar_schwab | grep -i "error\|status="
```
Grafana: `sum by (status)(rate(schwab_http_requests_total{endpoint="/accounts.orders.place"}[5m]))`

Check Schwab API status at `https://developer.schwab.com/`.

**Mitigation:**
If 401/403 — trigger re-authorize flow. If 429 — back off; the sidecar retries with
exponential backoff but the order queue may need draining. If 400 — inspect the payload via
`app/services/order_capability_service.py` validation.

---

## SchwabOrderEventGap

**Anchor:** `#schwabordereventgap`

The poller is iterating in fast cadence (2 s) but `schwab_order_event_emitted_total` shows
zero events over 5 minutes. Active orders are being polled but no status transitions are
being detected or fanned out.

**Probable causes:**
- No orders are actually in flight (benign — cadence will downgrade after idle timeout).
- Fan-out is broken: `SCHWAB_FANOUT_SUBSCRIBER_DROPPED_TOTAL` may be climbing.
- Order status is stuck in an intermediate state Schwab is not advancing.

**Diagnostic steps:**
```
docker compose logs --tail=100 sidecar_schwab | grep -i "event\|fanout\|dropped"
```
Grafana: `schwab_fanout_subscriber_dropped_total` rate; `schwab_order_event_emitted_total` by `kind`.

**Mitigation:**
If no active orders, silence or let cadence auto-downgrade. If fan-out is dropping,
restart `sidecar_schwab`. If Schwab is stalling order updates, check the Schwab platform.

---

## CapabilityCacheChurn

**Anchor:** `#capabilitycachechurn`

The Redis pubsub is invalidating the order-capability cache more than 100 times per hour.
This causes excessive round-trips to `order_capabilities` and degrades latency.

**Probable causes:**
- Frequent admin writes to `order_capabilities` via `POST /api/admin/order-capabilities`.
- Recurring sidecar reconnects re-triggering registration logic.
- A loop writing the same capability rows repeatedly.

**Diagnostic steps:**
```sql
-- Run on NUC postgres (psql -h 10.10.0.2 -U trader dashboard):
SELECT broker_id, updated_at FROM order_capabilities ORDER BY updated_at DESC LIMIT 20;
```
Grafana: `rate(order_capability_admin_writes_total[1h])` and
`rate(order_capability_pubsub_invalidations_total[1h])`.

**Mitigation:**
Trace writes to `app/services/order_capability_service.py`. If caused by sidecar reconnects,
investigate network stability between backend and `sidecar_schwab`. If caused by a looping
admin script, stop the script.

---

## UnknownBroker

**Anchor:** `#unknownbroker`

A capability check was called with a `broker_id` that does not exist in the code registry.
This indicates drift between the `order_capabilities` DB table and the registered brokers.

**Probable causes:**
- A new broker was added to the DB but not to the capability registry in code.
- A typo in a `broker_id` constant.
- A stale row left in `order_capabilities` after a broker was removed.

**Diagnostic steps:**
```sql
SELECT broker_id FROM order_capabilities ORDER BY broker_id;
```
Compare against the broker enum in `app/services/order_capability_service.py`. Check the
spike time in Grafana: `increase(order_capability_check_total{result="unknown_broker"}[5m])`.

**Mitigation:**
Add the missing broker to the registry and redeploy backend, or delete the stale DB row.
This alert is severity `page` because unknown_broker silently blocks order submission.

---

## CapabilityPubsubFailures

**Anchor:** `#capabilitypubsubfailures`

The backend failed to publish a capability-invalidation event to Redis pubsub. Downstream
sidecar caches will not be notified of capability changes until they expire naturally.

**Probable causes:**
- Redis is down or unreachable from the backend container.
- Redis connection pool exhausted under load.
- Network partition between backend and Redis on the WireGuard link.

**Diagnostic steps:**
```
redis-cli -h 10.10.0.2 ping
docker compose logs --tail=50 backend | grep -i "pubsub\|redis"
```
Grafana: `increase(order_capability_pubsub_failures_total[5m])`.

**Mitigation:**
If Redis is down, restart it and bounce the backend (`docker compose restart backend`).
If pool exhaustion, increase `REDIS_POOL_SIZE` in the backend config.
Capability cache will self-heal on next natural expiry even without pubsub.
