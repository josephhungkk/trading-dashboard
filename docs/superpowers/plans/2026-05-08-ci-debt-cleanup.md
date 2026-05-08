# CI Debt Cleanup — Backend Pytest Sweep

**Discovered:** 2026-05-08, after pytest-timeout (60s) was added in commit
231c190 and the `FakeBrokerServicer.OrderEvent` infinite-loop hang was
fixed in 946035f. With the suite no longer hanging at the first test, the
full failure tail surfaced — **114 failures** that have been latent since
the v0.10.0 (2026-05-07) lifespan-init refactor.

This plan is intentionally **scoped separately** from Phase 9.5 (which
already swept the per-phase reviewer chain). The failures here are
test-fixture drift, not code-correctness drift, and can be fixed without
touching production paths.

> Out-of-scope here: Deploy fix is shipped separately (commit d792da8
> seeds OCO into `order_types` to unblock alembic). E2E Mock Trade Chain
> failure has the same root cause as Bucket A and is fixed by the same
> work.

## Failure Buckets

Counted from `gh run view 25570812894 --log-failed | grep '^backend.*Pytest.*FAILED'`.

### Bucket A — Lifespan-init not driven (≈55 tests)

**Pattern:** Test uses `client` fixture from `tests/conftest.py:57` which
opens an `AsyncClient(transport=ASGITransport(app=app))` without invoking
`app.router.lifespan_context(app)`. Lifespan is what wires:

| Module-level state set by lifespan | Read by |
|---|---|
| `set_config_service(svc)` | `app/core/deps.py:get_config_service` |
| `_app.state.capability_svc` | `app/core/deps.py:get_capability_service` |
| `_app.state.redis` | SSE / WS endpoints |
| `set_broker_registry`, `set_account_service` | `/api/accounts`, `/api/orders/*` |

Affected files (sample):

- `tests/api/test_orders_place.py` (10 tests)
- `tests/api/test_orders_preview.py` (10 tests)
- `tests/api/test_oauth_callback_public.py` (1 test)
- `tests/api/test_sse_config_stream.py` (5 tests)

**Fix — Option 1 (recommended):** add an autouse module-scope fixture in
`backend/tests/api/conftest.py` that wraps each test in
`async with app.router.lifespan_context(app):` AND mock-replaces
`build_broker_registry` with a registry pointed at `sidecar_server`
fixture (already exists at `tests/fixtures/sidecar_servicer.py:559`).
Pre-seeds `mtls.client_cert_pem`/`mtls.client_key_pem`/`mtls.ca_bundle_pem`
in `app_secrets` from the fake PKI material.

**Fix — Option 2 (low-effort):** keep the existing pattern; add per-test
`set_config_service`, `app.state.capability_svc = ...`, etc. in each
fixture. More boilerplate, less invasive.

**Effort:** Option 1 = ~1 day, fixes all ≈55 in one go. Option 2 = ~3 days
(per-file conftest mods).

### Bucket B — `Future attached to a different loop` (≈10 tests)

`tests/db/test_alembic_0008.py`, `tests/integration/test_alembic_0009.py`,
similar.

**Pattern:** `pytest-asyncio 1.3.x` per-test event loop scoping. The
fixture creates an asyncpg connection bound to one loop, then a later
phase of the same test runs in a different loop.

**Fix:** set `asyncio_default_fixture_loop_scope = "function"` (current
default is unset → "module" in pytest-asyncio 1.x). OR change the
fixtures to be `function`-scoped explicitly.

**Effort:** ~30 min.

### Bucket C — Snapshot drift (2 tests)

- `tests/api/test_openapi_contract.py::test_openapi_schema_lock_phase5b`
- `tests/api/test_openapi_contract.py::test_openapi_schema_lock_phase5c`

**Pattern:** Phase 6/7/8/9 added endpoints; the locked snapshot is
v0.5.x.

**Fix:** regenerate the snapshot via `uv run pytest --snapshot-update`,
inspect the diff, commit.

**Effort:** ~15 min (review the new fields to make sure they're intended).

### Bucket D — Capability matrix size drift (≈5 tests)

- `tests/integration/test_alembic_0011.py::test_capability_matrix_size`
  — expected 200 rows, got 404.

**Pattern:** Phase 8a/8b/8c migrations (`0011a`, `0013`, `0014`,
`0014a`, `0015`, `0016`, `0017`, `0017a`, `0020`, `0020a`, `0020b`,
`0021_eq`, `0021_cr`, `0022`, `0028`) added many new
`broker_order_capability` rows.

**Fix:** update assertion to the new expected count (compute it from the
seed-file row counts), or change the test to assert ">= 200" instead of
"= 200".

**Effort:** ~30 min.

### Bucket E — Misc assertion drift (≈40 tests)

Examples:

- `test_orders_get::test_get_orders_policy_returns_caps_and_today_notional`
  — `'0.00000000' == '200.00000000'` (today_notional reset behavior).
- Various `test_alembic_*.py` row-count or column-existence assertions.

**Pattern:** Logic moved or schema changed; tests not updated.

**Fix:** Per-test investigation. Most are 1-2 line updates.

**Effort:** ~2-3 days for the long tail.

## Suggested Execution Order

1. **Bucket B** (asyncio loop scoping) — 30 min, unblocks ~10 tests with a
   single config line.
2. **Bucket C** (snapshot drift) — 15 min.
3. **Bucket D** (matrix size) — 30 min.
4. **Bucket A** (lifespan-init) — 1 day; biggest blast radius (~55 tests
   green at once).
5. **Bucket E** (long tail) — 2-3 days, parallelizable across Codex
   tasks, no shared state.

Total: ~1 sprint week if Bucket A done well.

## Companion Issues

- **E2E Mock Trade Chain** (`test_e2e_trade_chain.py::test_full_trade_chain`):
  same root cause as Bucket A but in its own workflow. Will be fixed by the
  same Option-1 pattern (lifespan + monkey-patched broker_registry +
  seeded fake mTLS PKI). Currently in `failure` since v0.10.0; safe to
  mark `pytest.mark.skip(reason="pending Bucket A fix")` until then.

- **Deploy** (backend container unhealthy on VPS): root cause was alembic
  FK violation on `broker_order_capability.order_type='OCO'` because
  prod's `order_types` table was missing the OCO row (0020b ran on prod
  before its `_SEED_ROWS` list was extended). Fixed in commit d792da8
  (migration 0036_seed_oco_order_type, idempotent). Verify via next
  Deploy run.

## Validation Strategy

- Run targeted bucket: `uv run pytest tests/api/ -x` after each batch.
- Compare CI run before/after each bucket lands.
- Don't try to land all buckets in one PR; Bucket A alone is one PR.

## Do NOT Do

- Don't disable `--cov-fail-under=80` to "make CI green". The threshold
  is real — fix tests, don't lower the bar.
- Don't add `pytest.mark.skip` blanket to silence failures. Use it only
  for tests that legitimately require the lifespan/broker fixture rewrite
  (Bucket A pattern), and link the TODO to this doc.
- Don't bundle bucket fixes with feature work. Each PR should be one
  bucket so reviewers can spot regressions.

## Pointers

- Existing fake servicer: `backend/tests/fixtures/sidecar_servicer.py`
  (`FakeBrokerServicer`, `sidecar_server`/`sidecar_client` fixtures).
- Existing `_apply_migrations` autouse session fixture: handles schema
  setup; Bucket A fix needs a complementary `_lifespan` autouse fixture
  for HTTP-driving tests only.
- Existing per-test `config_service` fixture: a model for Bucket A —
  setup that runs once per test, sets the global, yields, optionally
  cleans up.
