# Empirical scripts

One-shot scripts that exercise live broker APIs to validate sidecar assumptions.
NOT run in CI — human-invoked with sandbox credentials.

## schwab_place_cancel_paper.py — Phase 8a C0 hard gate

Validates the four assumptions sidecar_schwab depends on:
1. POST `/accounts/{hash}/orders` returns `Location: /orders/{id}` header.
2. `clientOrderId` round-trips on subsequent GET.
3. `executionLeg` shape matches `sidecar_schwab/normalize.py` expectations.
4. Schwab status string set is the 11 documented in spec §6 (no surprises).

### Invoke

```bash
SCHWAB_APP_KEY=... SCHWAB_APP_SECRET=... SCHWAB_PAPER_ACCOUNT_HASH=... \
  uv run python scripts/empirical/schwab_place_cancel_paper.py
```

Optional `SCHWAB_PAPER_SYMBOL` env (default `F` — cheap symbol).

### Output

JSON artifact at `scripts/empirical/artifacts/schwab_c0_<UTC-ts>.json`. Exit 0 = PASS,
exit 1 = FAIL with reason printed to stderr. The artifact is committed as evidence
that the C0 gate passed for a given Phase 8a release candidate.

### Phase 8a status

Run + commit PASS artifact UNBLOCKS Task A5 (Schwab capability flip — flips schwab
column in broker_order_capability from 0 supported to 50 supported) AND Chunk F
(frontend trade ticket modal).
