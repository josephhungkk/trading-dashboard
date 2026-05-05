# Alpaca Adapter Operator Runbook (Phase 7c)

## Overview

Two in-cluster Docker sidecars (`alpaca-sidecar-live`, `alpaca-sidecar-paper`)
on the `td-net` bridge. API-key auth (no OAuth, no token rotation). Free-tier
Alpaca data — **30-symbol cap per WS endpoint** (equity IEX + crypto v1beta3
each have their own 30-symbol limit).

## Step 0: No CF Access bypass needed

Unlike Schwab, Alpaca uses long-lived API keys with no OAuth callback. All
sidecar↔Alpaca traffic is outbound from the docker network — there is no
public callback path that needs a CF Access policy.

## Step 1: Generate API keys

1. Log into <https://app.alpaca.markets/account/api-keys>.
2. Generate a **paper-trading** key pair first (paper account is required for
   day-1 smoke testing).
3. Generate a **live-trading** key pair only when you're ready to expose live
   data to the dashboard.

Each key pair is `(api_key, api_secret)`. Live keys start with `PK...`; paper
keys start with `PA...`.

## Step 2: Seed `app_secrets`

Forward-compatible schema (Phase 7c MED-2): keys are stored under an
`<account_label>` namespace, defaulting to `"default"`. A future second
Alpaca account can add its own labelled entries without a schema migration.

```bash
# Live
curl -X PUT https://dashboard.kiusinghung.com/api/admin/secrets/broker/alpaca.default.live.api_key \
  -H "Content-Type: application/json" \
  -d '{"value": "PKxxxxxxxxxxxxxxxxxx", "value_type": "str"}'

curl -X PUT https://dashboard.kiusinghung.com/api/admin/secrets/broker/alpaca.default.live.api_secret \
  -H "Content-Type: application/json" \
  -d '{"value": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "value_type": "str"}'

# Paper (same shape)
curl -X PUT https://dashboard.kiusinghung.com/api/admin/secrets/broker/alpaca.default.paper.api_key \
  -H "Content-Type: application/json" \
  -d '{"value": "PAxxxxxxxxxxxxxxxxxx", "value_type": "str"}'

curl -X PUT https://dashboard.kiusinghung.com/api/admin/secrets/broker/alpaca.default.paper.api_secret \
  -H "Content-Type: application/json" \
  -d '{"value": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "value_type": "str"}'
```

## Step 3: Bring up the sidecars

```bash
docker compose -f docker-compose.prod.yml up -d alpaca-sidecar-live alpaca-sidecar-paper
docker compose -f docker-compose.prod.yml restart backend
```

The backend `restart` triggers a fresh `Configure` RPC dispatch on lifespan
boot (5-trigger contract from Phase 7a). Per-mode routing (HIGH-5) ensures
the live sidecar receives only live creds and the paper sidecar receives
only paper creds.

## Step 4: Smoke — accounts

```bash
curl https://dashboard.kiusinghung.com/api/accounts | jq '.accounts[] | select(.broker_id=="alpaca")'
# Expect 2 rows: live + paper.

curl https://dashboard.kiusinghung.com/api/accounts/<id>/positions | jq
# Expect array (possibly empty).
```

## Step 5: Smoke — quotes

Subscribe to BTC via `/ws/quotes`:

```bash
wscat -c wss://dashboard.kiusinghung.com/ws/quotes \
  -H "Cf-Access-Jwt-Assertion: $JWT"
# > {"op":"sub","symbols":["crypto:BTC:US"]}
# Expect quote frames within 5s.
```

Source-router default (Phase 7c) routes `crypto:BTC:US` to `alpaca`. If the
WS upgrade succeeds but no quote frames arrive within 5s, check
`alpaca-sidecar-live` logs for auth-fail or upstream-subscribe-rejected
messages.

## Operations

### Symbol-cap hit (≥25 active)

Alert `AlpacaSymbolCapNear` fires when either endpoint's
`alpaca_subscription_active` gauge reaches 25. Two options:

1. **Prune subscriptions** — remove unused symbols from watchlists. Phase
   7c CRIT-1 layer 1 enforces a backend soft cap at 25 (5-symbol buffer
   below Alpaca's 30 hard cap), so the 26th subscribe attempt is rejected
   with `cap_kind=per_source` before it reaches the sidecar.
2. **Upgrade to Algo Trader Plus** ($99/mo) — unlimited symbols. Update
   `CAP_PER_SOURCE` in `backend/app/services/quotes/registry.py` to a
   higher value matching your new ceiling (or remove for unlimited).

### Subscribe rejected by Alpaca (HIGH-6)

Alert `AlpacaUpstreamSubscribeRejection` fires when Alpaca silently lowers
their cap (e.g. if you previously had 30 and they cut to 20 without
notice). The streamer detects this and:

- Removes the rejected symbol from `_iex_active` / `_crypto_active`.
- Emits a drift sentinel back to the backend, which decrements the
  per-source counter (HIGH-6 — prevents ghost subscriptions).

Operator action: lower `CAP_PER_SOURCE` to `(rejected_count - 5)`,
redeploy, root-cause via Alpaca support.

### Cross-mode pollution probe (HIGH-5)

`alpaca_mode_mismatch_total{label}` should always be 0 in steady state.
Non-zero means the backend tried to send mismatched-mode creds to a
sidecar — investigate `BrokerRegistry`. Most likely cause: operator
mistyped the secret namespace (e.g. seeded `alpaca.default.live.*` but
the sidecar reports `mode=paper`).

### Key rotation

```bash
curl -X PUT https://dashboard.kiusinghung.com/api/admin/secrets/broker/alpaca.default.live.api_key \
  -d '{"value": "PKnewxxx...", "value_type": "str"}'
```

Backend fires `Configure` to `alpaca-sidecar-live` ONLY (per-mode routing).
Paper sidecar's in-memory cred is unchanged.

### Sidecar restart

```bash
docker compose -f docker-compose.prod.yml restart alpaca-sidecar-live
```

Backend's `Health.started_at` delta detection refires `Configure` within
~30s. Check logs:

```bash
docker compose -f docker-compose.prod.yml logs --tail=100 alpaca-sidecar-live
```

## Limits reference (Alpaca free tier)

| Limit | Value | Notes |
|---|---|---|
| Symbols / endpoint | **30** | Equity (IEX) + crypto (v1beta3) cap separately |
| WS connections / endpoint | **1** | Streamer enforces single connection |
| REST calls / minute | **200** | Discoverer fan-out runs every 30s — well under |
| OPRA options | (paid only) | Phase 12 Algo Trader Plus tier |

## Topology reference

```
         (TLS termination at CF tunnel + nginx)
                       │
backend (8001) ─── td-net (Docker bridge, no mTLS) ─── alpaca-sidecar-live  (9091)
                       └────────────────────────────── alpaca-sidecar-paper (9092)
                                                        │
                                                       HTTPS+WSS to:
                                                       api.alpaca.markets
                                                       paper-api.alpaca.markets
                                                       stream.data.alpaca.markets
```

`alpaca-live` and `alpaca-paper` are gateway labels; both share `broker_id="alpaca"`.
The backend's `app_config.broker_gateway_dial` table (Phase 7c HIGH-4)
resolves these labels to the docker-DNS hostnames.

## Forward pointers

- **Phase 7b.2** — Coinbase joins as `crypto.US` fallback after Alpaca.
- **Phase 8** — Trade execution (`PlaceOrder` / `CancelOrder` / `ModifyOrder`)
  alongside Schwab.
- **Phase 12** — Options scaffolding via Alpaca's OPRA WS (separate
  entitlement; requires Algo Trader Plus subscription).
