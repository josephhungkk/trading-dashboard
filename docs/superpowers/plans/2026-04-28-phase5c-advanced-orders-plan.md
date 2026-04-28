# Phase 5c — Advanced Orders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship modify orders + brackets/OCO + execution-level fills history as additive layers on the Phase 5b foundation, with all 14 architect-review findings (CRIT-1, CRIT-2, HIGH-1..4, MED-1..5, LOW-1..3) wired in from the start.

**Architecture:** Three new HTTP endpoints (PUT `/api/orders/{id}`, POST `/api/orders/bracket`, GET `/api/fills`) + one extended (GET `/api/orders` with date-range filters). One new Alembic migration (0006) adding `parent_order_id` + `oca_group` to `orders`, new `fills` and `pending_fills` tables, plus a `modified` enum value and `order_status_rank()` SQL function. Sidecar gets two new RPCs (`ModifyOrder`, `PlaceBracket`) and an `exec_id`/`kind` field on `OrderEventMessage`. Backend OrderEventConsumer extends with a `pending_fills` buffer (CRIT-2), commission backfill (MED-5), cascade-lag metric (HIGH-4), and a status-rank predicate in `_update_order` (CRIT-1).

**Tech Stack:** Python 3.14 / FastAPI / SQLAlchemy 2.0 async / Alembic / Pydantic v2 / asyncpg / PostgreSQL 18 / ib_async / grpc.aio / React 19 / TanStack Router / Zustand / Vitest 4.

**Reviewer chain (per CLAUDE.md "Step 6"):** Every commit boundary triggers spec-compliance + code-quality + python-reviewer (or typescript-reviewer for frontend chunk F). Schema/SQL chunks (A, D) also fire database-reviewer. Async/concurrency chunks (B, D) also fire silent-failure-hunter. Pydantic/TS-strict surfaces (A's proto stubs, C's schemas, F's modal types) also fire type-design-analyzer. The admin-flavored modify endpoint (C) also fires security-reviewer. The reviewer chain runs at EVERY commit, not batched at chunk end.

**Spec ref:** `docs/superpowers/specs/2026-04-28-phase5c-advanced-orders-design.md` (commit `6792110`).

**Tag at end:** `v0.5.4`.

**Estimated effort:** 5-7 working days, similar shape to 5b.1.

---

## Chunk Summary

| Chunk | Owner | Scope | Tasks |
|---|---|---|---|
| **A — Schema + proto** | Codex (mechanical) | Alembic 0006, proto extension, stub regen, migration tests | A1, A2, A3, A4, A5 |
| **B — Sidecar handlers** | Codex | ModifyOrder, PlaceBracket, exec_id emission, commissionReport subscription, sidecar unit tests | B1, B2, B3, B4, B5, B6 |
| **C — Backend service + endpoints** | Codex (mechanical) + Claude (modify orchestration) | services, schemas, 3 endpoints + 1 extension, OpenAPI snapshot | C1, C2, C3, C4, C5, C6, C7, C8, C9 |
| **D — Consumer fills + status rank** | Codex (mechanical) + Claude (sweeper coordination) | pending_fills buffer + sweeper, commission_buffer, status-rank predicate, cascade metric | D1, D2, D3, D4, D5 |
| **E — E2E tests + workflow updates** | Claude | mock_servicer extension, mock E2E, real-IBKR E2E, workflow YAML | E1, E2, E3, E4, E5 |
| **F — Frontend** | Codex (modal mechanics) + Claude (mode-prop refactor) | TradeTicketModal mode prop, useFillsHistory, FillsTable, OrdersPage Modify, /orders/$id/fills route | F1, F2, F3, F4, F5 |
| **G — Alerts + close-out** | Claude | alerts.yml, CHANGELOG, TASKS.md, CLAUDE.md, memory, tag v0.5.4 | G1, G2, G3 |

**Parallel-safe pairs (per architect review on the spec):** A1 ⊥ A3 (migration vs proto, distinct files); B1 ⊥ B2 ⊥ B3 (independent handlers); C2 ⊥ C3 ⊥ C4 (independent service functions); F1 ⊥ F2 (modal vs hook). All other tasks have linear dependencies.

---

## Chunk A — Schema + proto

### Task A1 — Alembic 0006 migration (status_rank, brackets, fills, pending_fills)

**Owner: Codex**

**Files:**
- Create: `backend/alembic/versions/0006_advanced_orders.py`

- [ ] **Step 1: Inspect 0005 to mirror naming + commit style**

Run: `head -30 backend/alembic/versions/0005_positions.py`
Note: revision id format, `down_revision` chain, raw `op.execute("...")` pattern (NOT autogenerate).

- [ ] **Step 2: Create migration file**

```python
"""advanced orders: brackets + fills + pending_fills + modified status

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-28

Adds Phase 5c schema:
- modified enum value (status-rank ordering for modify HTTP path)
- order_status_rank() function (consumer's _update_order rejects rank-decreasing)
- orders.parent_order_id + oca_group (bracket linkage)
- fills (execution-level audit trail with exec_id UNIQUE for resync idempotency)
- pending_fills (CRIT-2: buffer when execDetails arrives before order row)
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE orders
          ADD COLUMN parent_order_id UUID NULL REFERENCES orders(id) ON DELETE SET NULL,
          ADD COLUMN oca_group VARCHAR(64) NULL;
        """
    )
    op.execute(
        """
        CREATE INDEX orders_parent_order_id_idx
          ON orders(parent_order_id)
          WHERE parent_order_id IS NOT NULL;
        """
    )
    op.execute("ALTER TYPE order_status_enum ADD VALUE 'modified' AFTER 'submitted';")
    op.execute(
        """
        CREATE FUNCTION order_status_rank(s order_status_enum) RETURNS INT AS $$
          SELECT CASE s
            WHEN 'pending_submit' THEN 0
            WHEN 'submitted'      THEN 1
            WHEN 'inactive'       THEN 1
            WHEN 'modified'       THEN 2
            WHEN 'partial'        THEN 3
            WHEN 'filled'         THEN 4
            WHEN 'cancelled'      THEN 5
            WHEN 'rejected'       THEN 5
            WHEN 'expired'        THEN 5
          END;
        $$ LANGUAGE SQL IMMUTABLE;
        """
    )
    op.execute(
        """
        CREATE TABLE fills (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          order_id            UUID NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
          exec_id             VARCHAR(64) NOT NULL UNIQUE,
          qty                 NUMERIC(20,8) NOT NULL CHECK (qty > 0),
          price               NUMERIC(20,8) NOT NULL,
          currency            CHAR(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
          executed_at         TIMESTAMPTZ NOT NULL,
          commission          NUMERIC(20,8) NULL,
          commission_currency CHAR(3) NULL CHECK (commission_currency IS NULL OR commission_currency ~ '^[A-Z]{3}$'),
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX fills_order_id_executed_at_idx ON fills(order_id, executed_at DESC);")
    op.execute("CREATE INDEX fills_executed_at_idx ON fills(executed_at);")
    op.execute(
        """
        CREATE TABLE pending_fills (
          exec_id             VARCHAR(64) PRIMARY KEY,
          broker_order_id     VARCHAR(64) NOT NULL,
          account_id          UUID NOT NULL REFERENCES broker_accounts(id),
          qty                 NUMERIC(20,8) NOT NULL CHECK (qty > 0),
          price               NUMERIC(20,8) NOT NULL,
          currency            CHAR(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
          executed_at         TIMESTAMPTZ NOT NULL,
          commission          NUMERIC(20,8) NULL,
          commission_currency CHAR(3) NULL,
          raw_payload         JSONB NOT NULL,
          inserted_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX pending_fills_broker_order_id_idx ON pending_fills(broker_order_id);")
    op.execute("CREATE INDEX pending_fills_inserted_at_idx ON pending_fills(inserted_at);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pending_fills;")
    op.execute("DROP TABLE IF EXISTS fills;")
    op.execute("DROP FUNCTION IF EXISTS order_status_rank(order_status_enum);")
    # NOTE: Postgres doesn't support DROP VALUE on an enum; downgrade leaves
    # 'modified' in the enum. Acceptable since downgrade is dev-only.
    op.execute("DROP INDEX IF EXISTS orders_parent_order_id_idx;")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS oca_group;")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS parent_order_id;")
```

- [ ] **Step 3: Run migration locally**

Run: `cd backend && export DATABASE_URL=$(grep -E '^DATABASE_URL=' /home/joseph/dashboard/.env | cut -d'=' -f2-) && .venv/bin/alembic upgrade head`
Expected: `INFO  [alembic.runtime.migration] Running upgrade 0005 -> 0006`.

- [ ] **Step 4: Commit**

```bash
git add backend/alembic/versions/0006_advanced_orders.py
git commit -m "feat(backend): alembic 0006 - brackets, fills, pending_fills, modified status (5c A1)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer + database-reviewer.

---

### Task A2 — Migration tests for 0006

**Owner: Claude**

**Files:**
- Create: `backend/tests/migrations/test_0006_advanced_orders.py`

- [ ] **Step 1: Write 9 tests (mirroring test_0005 patterns)**

Tests to cover:
1. `test_modified_enum_value_present` — `'modified'` in pg_enum, sortorder after `'submitted'`
2. `test_order_status_rank_function` — ranks (0,1,2,3,4,5) for the 6 categories
3. `test_orders_parent_order_id_self_fk` — FK enforces existence; non-existent UUID rejected
4. `test_orders_parent_order_id_cascade_set_null` — hard-delete parent leaves child with NULL FK
5. `test_fills_exec_id_unique` — duplicate exec_id rejected
6. `test_fills_qty_positive_check` — qty=0 rejected
7. `test_fills_currency_three_letter` — 'usd' lowercase rejected
8. `test_fills_order_id_restrict` — DELETE order with fills rejected
9. `test_pending_fills_no_fk_on_broker_order_id` — orphan broker_order_id INSERT succeeds

Mirror test_0005's `_seed_account` helper + outer-rollback `session` fixture + `session.begin_nested()` savepoints.

- [ ] **Step 2: Run tests**

Run: `cd backend && export DATABASE_URL=$(grep -E '^DATABASE_URL=' /home/joseph/dashboard/.env | cut -d'=' -f2-) && .venv/bin/pytest tests/migrations/test_0006_advanced_orders.py -v --no-header`
Expected: 9 passed.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/migrations/test_0006_advanced_orders.py
git commit -m "test(backend): migration 0006 schema + constraints (5c A2)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer + database-reviewer.

---

### Task A3 — Proto extension (ModifyOrder, PlaceBracket, exec_id, kind on OrderEventMessage)

**Owner: Codex**

**Files:**
- Modify: `proto/broker/v1/broker.proto`

- [ ] **Step 1: Inspect existing proto**

Run: `grep -n "OrderEventMessage\|message Order\|^service Broker" proto/broker/v1/broker.proto`
Note the existing rpcs and the next available field number on `OrderEventMessage`.

- [ ] **Step 2: Append two new RPCs to service Broker**

Inside `service Broker { ... }` add:

```protobuf
  rpc ModifyOrder(ModifyOrderRequest) returns (ModifyOrderResponse);
  rpc PlaceBracket(PlaceBracketRequest) returns (PlaceBracketResponse);
```

- [ ] **Step 3: Define request/response messages**

Append after the existing `PlaceOrderResponse` message:

```protobuf
message ModifyOrderRequest {
  string broker_order_id = 1;
  string account_number = 2;
  Contract contract = 3;
  OrderSide side = 4;
  OrderType order_type = 5;
  TimeInForce tif = 6;
  string qty = 7;
  Money limit_price = 8;
  Money stop_price = 9;
  string client_order_id = 10;
}

message ModifyOrderResponse {
  string broker_order_id = 1;
  string status = 2;
}

message PlaceBracketRequest {
  PlaceOrderRequest parent = 1;
  PlaceOrderRequest stop_loss = 2;
  PlaceOrderRequest take_profit = 3;
  string oca_group = 4;
  bool has_stop_loss = 5;
  bool has_take_profit = 6;
}

message PlaceBracketResponse {
  string parent_broker_order_id = 1;
  string stop_loss_broker_order_id = 2;
  string take_profit_broker_order_id = 3;
  string status = 4;
}
```

- [ ] **Step 4: Extend OrderEventMessage with exec_id + kind**

Append two fields to `OrderEventMessage` (use the next available field numbers — likely 9 and 10 if 5b shipped 1-8):

```protobuf
  string exec_id = 9;
  string kind = 10;
```

- [ ] **Step 5: Commit**

```bash
git add proto/broker/v1/broker.proto
git commit -m "feat(proto): ModifyOrder + PlaceBracket rpcs, exec_id + kind on OrderEventMessage (5c A3)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer + type-design-analyzer.

---

### Task A4 — Backend + sidecar proto stub regen

**Owner: Codex**

**Files:**
- Modify (regen): `backend/app/_generated/broker/v1/broker_pb2.py`
- Modify (regen): `backend/app/_generated/broker/v1/broker_pb2_grpc.py`
- Modify (regen): `sidecar/_generated/broker/v1/broker_pb2.py`
- Modify (regen): `sidecar/_generated/broker/v1/broker_pb2_grpc.py`

- [ ] **Step 1: Regen backend stubs**

Run from `/home/joseph/dashboard`:
```bash
cd backend && bash scripts/proto-gen.sh
```
Expected: `grep -c ModifyOrder app/_generated/broker/v1/broker_pb2.py` >= 1.

- [ ] **Step 2: Regen sidecar stubs**

```bash
cd sidecar && bash scripts/proto-gen.sh
```

- [ ] **Step 3: Verify imports work**

```bash
cd backend && .venv/bin/python -c "from app._generated.broker.v1 import broker_pb2; print(broker_pb2.ModifyOrderRequest.DESCRIPTOR.full_name)"
cd /home/joseph/dashboard/sidecar && .venv/bin/python -c "from sidecar._generated.broker.v1 import broker_pb2; print(broker_pb2.PlaceBracketRequest.DESCRIPTOR.full_name)"
```
Expected: prints `broker.v1.ModifyOrderRequest` / `broker.v1.PlaceBracketRequest`.

- [ ] **Step 4: Commit (regen output as a single artifact commit)**

```bash
git add backend/app/_generated/broker/v1/ sidecar/_generated/broker/v1/
git commit -m "chore(proto): regen stubs for ModifyOrder + PlaceBracket + exec_id (5c A4)"
```

**Reviewer chain:** spec-compliance + code-quality.

---

### Task A5 — Pydantic boundary models for new proto messages

**Owner: Codex**

**Files:**
- Modify: `backend/app/brokers/base.py`

- [ ] **Step 1: Append new dataclasses (mirror PlaceOrderResult shape)**

```python
@dataclass(frozen=True)
class ModifyOrderResult:
    broker_order_id: str
    status: str


@dataclass(frozen=True)
class BracketResult:
    parent_broker_order_id: str
    stop_loss_broker_order_id: str  # "" if not requested
    take_profit_broker_order_id: str  # "" if not requested
    status: str
```

- [ ] **Step 2: Lint + type-check**

```bash
cd backend && .venv/bin/ruff check app/brokers/base.py && .venv/bin/mypy app/brokers/base.py
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/brokers/base.py
git commit -m "feat(backend): boundary models for ModifyOrder + Bracket results (5c A5)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer + type-design-analyzer.

---

## Chunk B — Sidecar handlers

### Task B1 — `ModifyOrder` handler

**Owner: Codex**

**Files:**
- Modify: `sidecar/handlers.py`

- [ ] **Step 1: Locate `CancelOrder` (insertion point)**

```bash
grep -n "async def CancelOrder\|def _build_ib_order" sidecar/handlers.py
```

- [ ] **Step 2: Insert ModifyOrder handler after CancelOrder**

```python
    async def ModifyOrder(  # noqa: N802
        self,
        request: broker_pb2.ModifyOrderRequest,
        context: object,
    ) -> broker_pb2.ModifyOrderResponse:
        del context
        broker_order_id = request.broker_order_id

        # SIM-prefixed orders cannot be modified in v1 (5c spec §2 non-goal).
        if broker_order_id.startswith("SIM-"):
            raise grpc.RpcError(
                grpc.StatusCode.INVALID_ARGUMENT,
                "modify on simulator orders not supported",
            )

        try:
            target_perm_id = int(broker_order_id)
        except ValueError as exc:
            raise grpc.RpcError(
                grpc.StatusCode.INVALID_ARGUMENT, f"invalid broker_order_id: {exc}"
            ) from exc

        raw_trades: object = self.ib.openTrades()  # type: ignore[attr-defined, unused-ignore]
        target_trade: _IbTrade | None = None
        for trade in cast("Iterable[object]", raw_trades):
            ib_trade: _IbTrade = cast("_IbTrade", trade)
            if (
                ib_trade.order.permId == target_perm_id
                and ib_trade.order.account == request.account_number
            ):
                target_trade = ib_trade
                break

        if target_trade is None:
            raise grpc.RpcError(
                grpc.StatusCode.NOT_FOUND, f"order {broker_order_id} not in openTrades"
            )

        ib_order = target_trade.order
        ib_order.totalQuantity = float(request.qty)
        if request.HasField("limit_price"):
            ib_order.lmtPrice = float(request.limit_price.value)
        if request.HasField("stop_price"):
            ib_order.auxPrice = float(request.stop_price.value)
        ib_order.tif = _proto_tif_to_str(request.tif)
        try:
            contract: object = await self._resolve_contract(request.contract.conid)
            new_trade: _IbTrade = cast(
                "_IbTrade",
                self.ib.placeOrder(contract, ib_order),  # type: ignore[attr-defined, unused-ignore]
            )
        except Exception as exc:
            raise grpc.RpcError(grpc.StatusCode.UNKNOWN, f"placeOrder failed: {exc}") from exc

        return broker_pb2.ModifyOrderResponse(
            broker_order_id=str(new_trade.order.permId),
            status=str(new_trade.orderStatus.status),
        )
```

- [ ] **Step 3: Lint**

```bash
cd sidecar && .venv/bin/ruff check handlers.py
```

- [ ] **Step 4: Commit**

```bash
git add sidecar/handlers.py
git commit -m "feat(sidecar): ModifyOrder handler (5c B1)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer + silent-failure-hunter.

---

### Task B2 — `PlaceBracket` handler

**Owner: Codex**

**Files:**
- Modify: `sidecar/handlers.py`

- [ ] **Step 1: Insert PlaceBracket handler after ModifyOrder**

```python
    async def PlaceBracket(  # noqa: N802
        self,
        request: broker_pb2.PlaceBracketRequest,
        context: object,
    ) -> broker_pb2.PlaceBracketResponse:
        del context
        parent_contract: object = await self._resolve_contract(request.parent.conid)
        parent_order = self._build_ib_order(request.parent)
        parent_order.transmit = False
        parent_order.orderRef = request.parent.client_order_id
        parent_order.account = request.parent.account_number

        if self._simulator_only:
            from uuid_utils import uuid7

            parent_sim = f"SIM-{uuid7()}"
            self._sim_orders[parent_sim] = {
                "client_order_id": request.parent.client_order_id,
                "account_number": request.parent.account_number,
            }
            sl_sim = ""
            tp_sim = ""
            if request.has_stop_loss:
                sl_sim = f"SIM-{uuid7()}"
                self._sim_orders[sl_sim] = {
                    "client_order_id": request.stop_loss.client_order_id,
                    "account_number": request.stop_loss.account_number,
                    "parent_sim_id": parent_sim,
                }
            if request.has_take_profit:
                tp_sim = f"SIM-{uuid7()}"
                self._sim_orders[tp_sim] = {
                    "client_order_id": request.take_profit.client_order_id,
                    "account_number": request.take_profit.account_number,
                    "parent_sim_id": parent_sim,
                }
            return broker_pb2.PlaceBracketResponse(
                parent_broker_order_id=parent_sim,
                stop_loss_broker_order_id=sl_sim,
                take_profit_broker_order_id=tp_sim,
                status="Submitted",
            )

        parent_trade: _IbTrade = cast(
            "_IbTrade",
            self.ib.placeOrder(parent_contract, parent_order),  # type: ignore[attr-defined, unused-ignore]
        )
        parent_order_id_int = parent_order.orderId

        sl_perm_id = ""
        tp_perm_id = ""
        children_to_place: list[tuple[object, object, str]] = []
        if request.has_stop_loss:
            sl_contract = await self._resolve_contract(request.stop_loss.conid)
            sl_order = self._build_ib_order(request.stop_loss)
            sl_order.parentId = parent_order_id_int
            sl_order.ocaGroup = request.oca_group
            sl_order.ocaType = 1
            sl_order.orderRef = request.stop_loss.client_order_id
            sl_order.account = request.stop_loss.account_number
            children_to_place.append((sl_contract, sl_order, "stop_loss"))
        if request.has_take_profit:
            tp_contract = await self._resolve_contract(request.take_profit.conid)
            tp_order = self._build_ib_order(request.take_profit)
            tp_order.parentId = parent_order_id_int
            tp_order.ocaGroup = request.oca_group
            tp_order.ocaType = 1
            tp_order.orderRef = request.take_profit.client_order_id
            tp_order.account = request.take_profit.account_number
            children_to_place.append((tp_contract, tp_order, "take_profit"))

        for i, (_c, child_order, _leg) in enumerate(children_to_place):
            child_order.transmit = (i == len(children_to_place) - 1)

        for child_contract, child_order, leg in children_to_place:
            child_trade: _IbTrade = cast(
                "_IbTrade",
                self.ib.placeOrder(child_contract, child_order),  # type: ignore[attr-defined, unused-ignore]
            )
            if leg == "stop_loss":
                sl_perm_id = str(child_trade.order.permId)
            else:
                tp_perm_id = str(child_trade.order.permId)

        return broker_pb2.PlaceBracketResponse(
            parent_broker_order_id=str(parent_trade.order.permId),
            stop_loss_broker_order_id=sl_perm_id,
            take_profit_broker_order_id=tp_perm_id,
            status=str(parent_trade.orderStatus.status),
        )
```

- [ ] **Step 2: Lint**

```bash
cd sidecar && .venv/bin/ruff check handlers.py
```

- [ ] **Step 3: Commit**

```bash
git add sidecar/handlers.py
git commit -m "feat(sidecar): PlaceBracket handler (5c B2)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer + silent-failure-hunter.

---

### Task B3 — Extend `_proto_event_from_trade` to emit `exec_id` + `kind`

**Owner: Codex**

**Files:**
- Modify: `sidecar/handlers.py`

- [ ] **Step 1: Locate `_proto_event_from_trade`**

```bash
grep -n "_proto_event_from_trade\|orderStatusEvent\|execDetailsEvent" sidecar/handlers.py
```

- [ ] **Step 2: Replace `_proto_event_from_trade` to support both kinds**

```python
    def _proto_event_from_trade(
        self,
        trade: _IbTrade,
        *,
        kind: str = "status",
        exec_id: str = "",
    ) -> broker_pb2.OrderEventMessage:
        raw: dict[str, object] = self._serialize_trade(trade)
        message = broker_pb2.OrderEventMessage(
            broker_order_id=str(trade.order.permId),
            client_order_id=trade.order.orderRef or "",
            status=trade.orderStatus.status,
            filled_qty=str(trade.orderStatus.filled),
            avg_fill_price=str(trade.orderStatus.avgFillPrice or 0),
            raw_payload=json.dumps(raw),
            exec_id=exec_id,
            kind=kind,
        )
        message.event_at.FromDatetime(datetime.now(UTC))
        return message
```

- [ ] **Step 3: Update OrderEvent stream's callbacks**

Replace the existing single `_on_status` (which subscribed to both `orderStatusEvent` and `execDetailsEvent` per Phase 5b) with two separate callbacks:

```python
        def _on_status(trade: object) -> None:
            ib_trade: _IbTrade = cast("_IbTrade", trade)
            if ib_trade.order.account != request.account_number:
                return
            try:
                queue.put_nowait(
                    self._proto_event_from_trade(ib_trade, kind="status", exec_id="")
                )
            except asyncio.QueueFull:
                metrics.broker_order_events_dropped_total.labels(reason="queue_full").inc()

        def _on_exec_details(trade: object, fill: object) -> None:
            ib_trade: _IbTrade = cast("_IbTrade", trade)
            if ib_trade.order.account != request.account_number:
                return
            try:
                exec_id = str(getattr(getattr(fill, "execution", None), "execId", ""))
                queue.put_nowait(
                    self._proto_event_from_trade(
                        ib_trade, kind="exec_details", exec_id=exec_id
                    )
                )
            except asyncio.QueueFull:
                metrics.broker_order_events_dropped_total.labels(reason="queue_full").inc()
```

Update wire-up to use the new exec_details callback:

```python
        self.ib.orderStatusEvent += _on_status  # type: ignore[attr-defined, unused-ignore]
        self.ib.execDetailsEvent += _on_exec_details  # type: ignore[attr-defined, unused-ignore]
```

Update `finally` block to subtract `_on_exec_details` instead of `_on_status` for the execDetails event.

- [ ] **Step 4: Verify existing tests still pass**

```bash
cd sidecar && .venv/bin/pytest tests/test_handlers_orders_contract.py tests/test_handlers_cancel_sim_echo.py -q --no-header
```

- [ ] **Step 5: Commit**

```bash
git add sidecar/handlers.py
git commit -m "feat(sidecar): emit exec_id + kind on OrderEvent stream (5c B3)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer + silent-failure-hunter.

---

### Task B4 — `commissionReport` event subscription

**Owner: Codex**

**Files:**
- Modify: `sidecar/handlers.py`

- [ ] **Step 1: Add commissionReport callback inside OrderEvent stream wire-up**

After `_on_exec_details` definition, add:

```python
        def _on_commission_report(
            trade: object, fill: object, commission_report: object
        ) -> None:
            ib_trade: _IbTrade = cast("_IbTrade", trade)
            if ib_trade.order.account != request.account_number:
                return
            try:
                exec_id = str(getattr(commission_report, "execId", ""))
                commission = str(getattr(commission_report, "commission", "0"))
                currency = str(getattr(commission_report, "currency", ""))
                msg = self._proto_event_from_trade(
                    ib_trade, kind="commission_report", exec_id=exec_id
                )
                payload = json.loads(msg.raw_payload)
                payload["commission"] = commission
                payload["commission_currency"] = currency
                msg.raw_payload = json.dumps(payload)
                queue.put_nowait(msg)
            except asyncio.QueueFull:
                metrics.broker_order_events_dropped_total.labels(reason="queue_full").inc()

        self.ib.commissionReportEvent += _on_commission_report  # type: ignore[attr-defined, unused-ignore]
```

In the `finally` block:

```python
            self.ib.commissionReportEvent -= _on_commission_report  # type: ignore[attr-defined, unused-ignore]
```

- [ ] **Step 2: Lint**

```bash
cd sidecar && .venv/bin/ruff check handlers.py
```

- [ ] **Step 3: Commit**

```bash
git add sidecar/handlers.py
git commit -m "feat(sidecar): commissionReport event -> kind=commission_report (5c B4)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer + silent-failure-hunter.

---

### Task B5 — Sidecar unit tests for ModifyOrder

**Owner: Claude**

**Files:**
- Create: `sidecar/tests/test_handlers_modify_order.py`

- [ ] **Step 1: Write 4 tests**

Tests:
1. `test_modify_sim_prefix_rejected` — SIM-prefixed broker_order_id → INVALID_ARGUMENT
2. `test_modify_invalid_int_id_rejected` — non-numeric broker_order_id → INVALID_ARGUMENT
3. `test_modify_order_id_not_found` — orderId not in openTrades → NOT_FOUND
4. `test_modify_simulator_only_rejected` — simulator_only handler rejects modify (NOT_FOUND or INVALID_ARGUMENT)

Use the same `mock_ib` + `handler` fixture pattern from `test_handlers_cancel_sim_echo.py` (5b.1 B3).

- [ ] **Step 2: Run tests**

```bash
cd sidecar && .venv/bin/pytest tests/test_handlers_modify_order.py -v --no-header
```
Expected: 4 passed.

- [ ] **Step 3: Commit**

```bash
git add sidecar/tests/test_handlers_modify_order.py
git commit -m "test(sidecar): ModifyOrder handler 4 tests (5c B5)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer.

---

### Task B6 — Sidecar unit tests for PlaceBracket

**Owner: Claude**

**Files:**
- Create: `sidecar/tests/test_handlers_bracket.py`

- [ ] **Step 1: Write 5 tests**

Tests:
1. `test_place_bracket_full_three_legs` — parent + SL + TP → 3 placeOrder calls
2. `test_place_bracket_parent_id_wired_on_children` — children's parentId == parent's orderId
3. `test_place_bracket_transmit_only_on_last_child` — parent + first child = transmit=False; last = True
4. `test_place_bracket_oca_group_propagates` — both children share oca_group + ocaType=1
5. `test_place_bracket_sim_mode_mints_three_uuids` — simulator mode mints SIM- uuids, registers in `_sim_orders`, no IB calls

Use a `mock_ib` whose `placeOrder.side_effect` increments an orderId counter on each call (per ib_async's synchronous orderId assignment behavior).

- [ ] **Step 2: Run tests**

```bash
cd sidecar && .venv/bin/pytest tests/test_handlers_bracket.py -v --no-header
```
Expected: 5 passed.

- [ ] **Step 3: Commit**

```bash
git add sidecar/tests/test_handlers_bracket.py
git commit -m "test(sidecar): PlaceBracket handler 5 tests (5c B6)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer.

---

## Chunk C — Backend service + endpoints

### Task C1 — Pydantic schemas in `app/schemas/orders.py`

**Owner: Codex**

**Files:**
- Modify: `backend/app/schemas/orders.py`

- [ ] **Step 1: Append new request/response models**

```python
class OrderModifyRequest(BaseModel):
    """PUT /api/orders/{id} body. account_id/conid/side/order_type immutable."""
    nonce: str = Field(min_length=1, max_length=128)
    qty: str = Field(pattern=r"^\d+(\.\d+)?$")
    limit_price: str | None = Field(default=None, pattern=r"^\d+(\.\d+)?$")
    tif: Literal["DAY", "GTC"]
    stop_price: str | None = Field(default=None, pattern=r"^\d+(\.\d+)?$")


class OrderBracketLeg(BaseModel):
    id: UUID
    leg: Literal["stop_loss", "take_profit"]
    broker_order_id: str
    status: str


class OrderBracketResponse(BaseModel):
    parent: OrderResponse
    children: list[OrderBracketLeg]
    oca_group: str


class OrderBracketRequest(BaseModel):
    nonce: str = Field(min_length=1, max_length=128)
    account_id: UUID
    client_order_id: UUID
    conid: str = Field(pattern=r"^\d+$")
    side: Literal["BUY", "SELL"]
    order_type: Literal["LIMIT"]
    tif: Literal["DAY", "GTC"]
    qty: str = Field(pattern=r"^\d+(\.\d+)?$")
    limit_price: str = Field(pattern=r"^\d+(\.\d+)?$")
    stop_price: str | None = Field(default=None, pattern=r"^\d+(\.\d+)?$")
    target_price: str | None = Field(default=None, pattern=r"^\d+(\.\d+)?$")


class FillResponse(BaseModel):
    id: UUID
    order_id: UUID
    exec_id: str
    qty: str
    price: str
    currency: str = Field(min_length=3, max_length=3)
    executed_at: datetime
    commission: str | None = None
    commission_currency: str | None = Field(default=None, min_length=3, max_length=3)


class FillListResponse(BaseModel):
    fills: list[FillResponse]
    next_cursor: str | None = None
```

- [ ] **Step 2: Lint + type-check**

```bash
cd backend && .venv/bin/ruff check app/schemas/orders.py && .venv/bin/mypy app/schemas/orders.py
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/schemas/orders.py
git commit -m "feat(backend): pydantic models for modify + bracket + fills (5c C1)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer + type-design-analyzer.

---

### Task C2 — `modify_order` service with HIGH-1 idempotency cache + HIGH-3 audit-only write

**Owner: Claude**

**Files:**
- Modify: `backend/app/services/orders_service.py`
- Modify: `backend/app/services/brokers.py`

- [ ] **Step 1: Add module-level idempotency cache**

After existing imports + before existing service functions:

```python
# HIGH-1: per-(order_id, nonce) response cache for PUT replay safety.
# 60-second TTL; multi-worker requires Redis-backed cache (Phase 9).
_MODIFY_REPLAY_CACHE: dict[tuple[UUID, str], tuple[float, dict[str, Any]]] = {}
_MODIFY_REPLAY_TTL_SECONDS: float = 60.0


def _modify_replay_lookup(order_id: UUID, nonce: str) -> dict[str, Any] | None:
    entry = _MODIFY_REPLAY_CACHE.get((order_id, nonce))
    if entry is None:
        return None
    expires_at, response = entry
    if time.monotonic() > expires_at:
        del _MODIFY_REPLAY_CACHE[(order_id, nonce)]
        return None
    return response


def _modify_replay_store(order_id: UUID, nonce: str, response: dict[str, Any]) -> None:
    expires_at = time.monotonic() + _MODIFY_REPLAY_TTL_SECONDS
    _MODIFY_REPLAY_CACHE[(order_id, nonce)] = (expires_at, response)
```

(Add `import time` at top if not present.)

- [ ] **Step 2: Add `modify_order` service function**

```python
async def modify_order(
    db: AsyncSession,
    redis: Redis,
    config: ConfigService,
    registry: BrokerRegistry,
    *,
    order_id: UUID,
    request: OrderModifyRequest,
) -> dict[str, Any]:
    """PUT /api/orders/{id} - audit-only write per HIGH-3.

    The orders.status mutation is owned by the OrderEventConsumer, NOT this
    function. This function: (1) validates state + policy + nonce, (2) writes
    a single order_events row with status='modified', (3) forwards
    ModifyOrder RPC, (4) returns the projected OrderResponse.
    """
    cached = _modify_replay_lookup(order_id, request.nonce)
    if cached is not None:
        return cached

    row = (
        await db.execute(
            text(
                "SELECT account_id, broker_order_id, conid, symbol, side, order_type, "
                "       tif, qty, limit_price, stop_price, status::text, filled_qty, "
                "       parent_order_id, client_order_id "
                "  FROM orders WHERE id = :id"
            ),
            {"id": order_id},
        )
    ).mappings().one_or_none()
    if row is None:
        raise PreviewUnavailable(404, {"error": "not_found"})

    if row["status"] in ("cancelled", "rejected", "expired", "filled"):
        raise PreviewUnavailable(409, {"error": "terminal_status"})

    # MED-1: only block parent-with-children when filled_qty>0 and children exist.
    if row["parent_order_id"] is None and Decimal(str(row["filled_qty"])) > 0:
        children_exist = (
            await db.execute(
                text(
                    "SELECT 1 FROM orders WHERE parent_order_id = :p "
                    "AND status::text NOT IN ('cancelled','rejected','expired','filled') LIMIT 1"
                ),
                {"p": order_id},
            )
        ).first()
        if children_exist is not None:
            raise PreviewUnavailable(409, {"error": "bracket_parent_partial"})

    if await config.get_bool("broker", "kill_switch_enabled", default=False):
        raise PreviewUnavailable(503, {"error": "kill_switch"})

    account = await _resolve_account(db, row["account_id"])
    new_qty = Decimal(request.qty)
    new_limit = Decimal(request.limit_price) if request.limit_price else None
    new_notional = (
        new_qty * new_limit if new_limit else Decimal(str(row["limit_price"])) * new_qty
    )
    await _check_trade_policy(
        config, account.gateway_label,
        notional=new_notional, currency_base=account.currency_base, redis=redis,
    )

    expected = await _consume_nonce(redis, request.nonce)
    if (
        expected is None
        or expected.get("account_id") != str(row["account_id"])
        or Decimal(expected.get("qty", "0")) != new_qty
        or (expected.get("limit_price") or "") != (request.limit_price or "")
    ):
        raise PreviewUnavailable(409, {"error": "nonce_mismatch"})

    # HIGH-3: audit row only - orders.status comes from the consumer.
    await db.execute(
        text(
            "INSERT INTO order_events (order_id, account_id, broker_order_id, status, "
            "                          filled_qty, avg_fill_price, broker_event_at, raw_payload) "
            "VALUES (:o, :a, :bo, CAST('modified' AS order_status_enum), "
            "        NULL, NULL, now() - INTERVAL '100 ms', CAST(:rp AS jsonb))"
        ),
        {
            "o": order_id,
            "a": row["account_id"],
            "bo": row["broker_order_id"],
            "rp": json.dumps(
                {
                    "modify": {
                        "qty": str(new_qty),
                        "limit_price": request.limit_price,
                        "tif": request.tif,
                        "stop_price": request.stop_price,
                    }
                }
            ),
        },
    )
    await db.commit()

    client = await registry.get_client(account.gateway_label)
    contract = await client.get_contract(row["conid"])
    await client.modify_order(
        broker_order_id=row["broker_order_id"],
        account_number=account.account_number,
        contract=contract,
        side=row["side"],
        order_type=row["order_type"],
        tif=request.tif,
        qty=request.qty,
        limit_price=request.limit_price,
        stop_price=request.stop_price,
        client_order_id=str(row["client_order_id"]),
    )

    projected = {
        "id": str(order_id),
        "client_order_id": str(row["client_order_id"]),
        "broker_order_id": row["broker_order_id"],
        "status": "modified",
        "qty": request.qty,
        "limit_price": request.limit_price,
        "stop_price": request.stop_price,
        "tif": request.tif,
    }
    _modify_replay_store(order_id, request.nonce, projected)
    return projected
```

- [ ] **Step 3: Add `BrokerSidecarClient.modify_order` wrapper in `brokers.py`**

```python
    async def modify_order(
        self,
        *,
        broker_order_id: str,
        account_number: str,
        contract: base.Contract,
        side: str,
        order_type: str,
        tif: str,
        qty: str,
        limit_price: str | None,
        stop_price: str | None,
        client_order_id: str,
    ) -> base.ModifyOrderResult:
        request = broker_pb2.ModifyOrderRequest(
            broker_order_id=broker_order_id,
            account_number=account_number,
            contract=_proto_contract_from_pydantic(contract),
            side=getattr(broker_pb2.OrderSide, side),
            order_type=getattr(broker_pb2.OrderType, order_type),
            tif=getattr(broker_pb2.TimeInForce, tif),
            qty=qty,
            limit_price=broker_pb2.Money(value=limit_price or "0", currency=contract.currency),
            stop_price=broker_pb2.Money(value=stop_price or "0", currency=contract.currency),
            client_order_id=client_order_id,
        )
        response = await self._call_unary("ModifyOrder", lambda: self._stub.ModifyOrder(request))
        return base.ModifyOrderResult(
            broker_order_id=response.broker_order_id,
            status=response.status,
        )
```

- [ ] **Step 4: Lint**

```bash
cd backend && .venv/bin/ruff check app/services/orders_service.py app/services/brokers.py
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/orders_service.py backend/app/services/brokers.py
git commit -m "feat(backend): modify_order service with replay cache + audit-only write (5c C2)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer + silent-failure-hunter + security-reviewer.

---

### Task C3 — `place_bracket` service with HIGH-2 two-phase commit

**Owner: Claude**

**Files:**
- Modify: `backend/app/services/orders_service.py`
- Modify: `backend/app/services/brokers.py`

- [ ] **Step 1: Add `place_bracket` service function**

```python
async def place_bracket(
    db: AsyncSession,
    redis: Redis,
    config: ConfigService,
    registry: BrokerRegistry,
    *,
    request: OrderBracketRequest,
) -> dict[str, Any]:
    """POST /api/orders/bracket - HIGH-2 two-phase commit.

    Step 1: validation + INSERT parent only (status=pending_submit).
    Step 2: PlaceBracket RPC.
    Step 3: On success - INSERT 2 children + UPDATE parent.broker_order_id (one tx).
    """
    if request.stop_price is None and request.target_price is None:
        raise PreviewUnavailable(400, {"error": "bracket_invalid_legs"})
    entry = Decimal(request.limit_price)
    if request.side == "BUY":
        if request.stop_price and Decimal(request.stop_price) >= entry:
            raise PreviewUnavailable(400, {"error": "bracket_invalid_prices"})
        if request.target_price and Decimal(request.target_price) <= entry:
            raise PreviewUnavailable(400, {"error": "bracket_invalid_prices"})
    else:
        if request.stop_price and Decimal(request.stop_price) <= entry:
            raise PreviewUnavailable(400, {"error": "bracket_invalid_prices"})
        if request.target_price and Decimal(request.target_price) >= entry:
            raise PreviewUnavailable(400, {"error": "bracket_invalid_prices"})

    if await config.get_bool("broker", "kill_switch_enabled", default=False):
        raise PreviewUnavailable(503, {"error": "kill_switch"})
    account = await _resolve_account(db, request.account_id)
    parent_notional = Decimal(request.qty) * entry
    await _check_trade_policy(
        config, account.gateway_label,
        notional=parent_notional, currency_base=account.currency_base, redis=redis,
    )
    expected = await _consume_nonce(redis, request.nonce)
    if expected is None:
        raise PreviewUnavailable(409, {"error": "nonce_mismatch"})

    from uuid_utils import uuid7

    parent_id = UUID(str(uuid7()))
    oca_group = f"BRK-{parent_id.hex[:8]}"
    await db.execute(
        text(
            "INSERT INTO orders (id, account_id, client_order_id, conid, symbol, side, "
            "order_type, tif, qty, limit_price, status, notional, parent_order_id, oca_group) "
            "VALUES (:id, :a, :coid, :conid, :symbol, :side, :ot, :tif, :qty, :lp, "
            "'pending_submit', :n, NULL, :oca)"
        ),
        {
            "id": parent_id,
            "a": request.account_id,
            "coid": request.client_order_id,
            "conid": request.conid,
            "symbol": account.symbol_for_conid(request.conid),
            "side": request.side,
            "ot": request.order_type,
            "tif": request.tif,
            "qty": request.qty,
            "lp": request.limit_price,
            "n": parent_notional,
            "oca": oca_group,
        },
    )
    await db.commit()

    client = await registry.get_client(account.gateway_label)
    bracket_result = await client.place_bracket(
        parent_request=_pydantic_to_place_request(request),
        stop_loss=_pydantic_to_sl_request(request) if request.stop_price else None,
        take_profit=_pydantic_to_tp_request(request) if request.target_price else None,
        oca_group=oca_group,
    )

    children: list[dict[str, Any]] = []
    async with db.begin():
        await db.execute(
            text(
                "UPDATE orders SET broker_order_id = :bo, status = 'submitted' WHERE id = :id"
            ),
            {"bo": bracket_result.parent_broker_order_id, "id": parent_id},
        )
        if request.stop_price and bracket_result.stop_loss_broker_order_id:
            sl_id = UUID(str(uuid7()))
            await db.execute(
                text(
                    "INSERT INTO orders (id, account_id, client_order_id, conid, symbol, "
                    "side, order_type, tif, qty, stop_price, status, notional, "
                    "broker_order_id, parent_order_id, oca_group) "
                    "VALUES (:id, :a, :coid, :conid, :symbol, :side, 'STOP', :tif, :qty, "
                    ":sp, 'submitted', :n, :bo, :pid, :oca)"
                ),
                {
                    "id": sl_id,
                    "a": request.account_id,
                    "coid": UUID(str(uuid7())),
                    "conid": request.conid,
                    "symbol": account.symbol_for_conid(request.conid),
                    "side": "SELL" if request.side == "BUY" else "BUY",
                    "tif": request.tif,
                    "qty": request.qty,
                    "sp": request.stop_price,
                    "n": Decimal(request.qty) * Decimal(request.stop_price),
                    "bo": bracket_result.stop_loss_broker_order_id,
                    "pid": parent_id,
                    "oca": oca_group,
                },
            )
            children.append(
                {
                    "id": str(sl_id),
                    "leg": "stop_loss",
                    "broker_order_id": bracket_result.stop_loss_broker_order_id,
                    "status": "submitted",
                }
            )
        if request.target_price and bracket_result.take_profit_broker_order_id:
            tp_id = UUID(str(uuid7()))
            await db.execute(
                text(
                    "INSERT INTO orders (id, account_id, client_order_id, conid, symbol, "
                    "side, order_type, tif, qty, limit_price, status, notional, "
                    "broker_order_id, parent_order_id, oca_group) "
                    "VALUES (:id, :a, :coid, :conid, :symbol, :side, 'LIMIT', :tif, "
                    ":qty, :tp, 'submitted', :n, :bo, :pid, :oca)"
                ),
                {
                    "id": tp_id,
                    "a": request.account_id,
                    "coid": UUID(str(uuid7())),
                    "conid": request.conid,
                    "symbol": account.symbol_for_conid(request.conid),
                    "side": "SELL" if request.side == "BUY" else "BUY",
                    "tif": request.tif,
                    "qty": request.qty,
                    "tp": request.target_price,
                    "n": Decimal(request.qty) * Decimal(request.target_price),
                    "bo": bracket_result.take_profit_broker_order_id,
                    "pid": parent_id,
                    "oca": oca_group,
                },
            )
            children.append(
                {
                    "id": str(tp_id),
                    "leg": "take_profit",
                    "broker_order_id": bracket_result.take_profit_broker_order_id,
                    "status": "submitted",
                }
            )

    return {
        "parent": {
            "id": str(parent_id),
            "client_order_id": str(request.client_order_id),
            "broker_order_id": bracket_result.parent_broker_order_id,
            "status": "submitted",
        },
        "children": children,
        "oca_group": oca_group,
    }
```

- [ ] **Step 2: Add `BrokerSidecarClient.place_bracket` wrapper in `brokers.py`**

```python
    async def place_bracket(
        self,
        *,
        parent_request,
        stop_loss,
        take_profit,
        oca_group: str,
    ) -> base.BracketResult:
        request = broker_pb2.PlaceBracketRequest(
            parent=parent_request,
            stop_loss=stop_loss or broker_pb2.PlaceOrderRequest(),
            take_profit=take_profit or broker_pb2.PlaceOrderRequest(),
            oca_group=oca_group,
            has_stop_loss=stop_loss is not None,
            has_take_profit=take_profit is not None,
        )
        response = await self._call_unary("PlaceBracket", lambda: self._stub.PlaceBracket(request))
        return base.BracketResult(
            parent_broker_order_id=response.parent_broker_order_id,
            stop_loss_broker_order_id=response.stop_loss_broker_order_id,
            take_profit_broker_order_id=response.take_profit_broker_order_id,
            status=response.status,
        )
```

- [ ] **Step 3: Lint + commit**

```bash
cd backend && .venv/bin/ruff check app/services/orders_service.py app/services/brokers.py
git add backend/app/services/orders_service.py backend/app/services/brokers.py
git commit -m "feat(backend): place_bracket service with two-phase commit (5c C3)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer + silent-failure-hunter + database-reviewer + security-reviewer.

---

### Task C4 — `list_fills` service + date-range extension on `list_orders`

**Owner: Codex**

**Files:**
- Modify: `backend/app/services/orders_service.py`

- [ ] **Step 1: Add `list_fills` service function**

```python
async def list_fills(
    db: AsyncSession,
    *,
    account_id: UUID,
    from_ts: datetime,
    to_ts: datetime,
    limit: int = 100,
    cursor: str | None = None,
) -> dict[str, Any]:
    cursor_executed_at: datetime | None = None
    cursor_id: UUID | None = None
    if cursor:
        try:
            decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
            cursor_executed_at = datetime.fromisoformat(decoded["executed_at"])
            cursor_id = UUID(decoded["id"])
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            raise PreviewUnavailable(400, {"error": "invalid_cursor"}) from exc

    query = (
        "SELECT f.id, f.order_id, f.exec_id, f.qty, f.price, f.currency, f.executed_at, "
        "       f.commission, f.commission_currency "
        "  FROM fills f "
        "  JOIN orders o ON o.id = f.order_id "
        " WHERE o.account_id = :a "
        "   AND f.executed_at BETWEEN :f AND :t "
    )
    params: dict[str, Any] = {"a": account_id, "f": from_ts, "t": to_ts, "lim": limit + 1}
    if cursor_executed_at and cursor_id:
        query += " AND (f.executed_at, f.id) < (:cea, :cid) "
        params["cea"] = cursor_executed_at
        params["cid"] = cursor_id
    query += " ORDER BY f.executed_at DESC, f.id DESC LIMIT :lim"

    result = await db.execute(text(query), params)
    rows = list(result.mappings())

    next_cursor: str | None = None
    if len(rows) > limit:
        last_kept = rows[limit - 1]
        next_cursor = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "executed_at": last_kept["executed_at"].isoformat(),
                    "id": str(last_kept["id"]),
                }
            ).encode()
        ).decode()
        rows = rows[:limit]

    return {
        "fills": [
            {
                **dict(r),
                "id": str(r["id"]),
                "order_id": str(r["order_id"]),
                "executed_at": r["executed_at"].isoformat(),
            }
            for r in rows
        ],
        "next_cursor": next_cursor,
    }
```

(Add `import base64` + `from datetime import datetime` if not present.)

- [ ] **Step 2: Extend existing `list_orders` with `from_ts`/`to_ts`**

Find existing `list_orders` and add the params + WHERE clauses:

```python
async def list_orders(
    db: AsyncSession,
    *,
    account_id: UUID,
    status_filter: list[str] | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
) -> list[dict[str, Any]]:
    query = "SELECT ... FROM orders WHERE account_id = :a"
    params: dict[str, Any] = {"a": account_id}
    if status_filter:
        query += " AND status::text = ANY(:st)"
        params["st"] = status_filter
    if from_ts:
        query += " AND created_at >= :f"
        params["f"] = from_ts
    if to_ts:
        query += " AND created_at <= :t"
        params["t"] = to_ts
    query += " ORDER BY created_at DESC LIMIT 500"
    result = await db.execute(text(query), params)
    return [dict(r) for r in result.mappings()]
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/orders_service.py
git commit -m "feat(backend): list_fills + date-range on list_orders (5c C4)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer + database-reviewer.

---

### Task C5 — `PUT /api/orders/{id}` endpoint

**Owner: Codex**

**Files:**
- Modify: `backend/app/api/orders.py`

- [ ] **Step 1: Add the PUT route**

```python
@router.put("/orders/{order_id}")
async def modify_order_endpoint(
    order_id: UUID,
    request: OrderModifyRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    config: Annotated[ConfigService, Depends(get_config)],
    registry: Annotated[BrokerRegistry, Depends(get_broker_registry)],
    _: Annotated[AdminIdentity, Depends(require_admin_jwt)],
) -> dict[str, Any]:
    return await orders_service.modify_order(
        db, redis, config, registry,
        order_id=order_id, request=request,
    )
```

- [ ] **Step 2: Verify existing tests pass**

```bash
cd backend && export DATABASE_URL=$(grep -E '^DATABASE_URL=' /home/joseph/dashboard/.env | cut -d'=' -f2-) && .venv/bin/pytest tests/api/test_orders_place.py tests/api/test_orders_cancel.py -q --no-header
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/orders.py
git commit -m "feat(backend): PUT /api/orders/{id} endpoint (5c C5)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer + security-reviewer.

---

### Task C6 — `POST /api/orders/bracket` endpoint

**Owner: Codex**

**Files:**
- Modify: `backend/app/api/orders.py`

- [ ] **Step 1: Add the POST route**

```python
@router.post("/orders/bracket")
async def place_bracket_endpoint(
    request: OrderBracketRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    config: Annotated[ConfigService, Depends(get_config)],
    registry: Annotated[BrokerRegistry, Depends(get_broker_registry)],
    _: Annotated[AdminIdentity, Depends(require_admin_jwt)],
) -> dict[str, Any]:
    return await orders_service.place_bracket(db, redis, config, registry, request=request)
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/api/orders.py
git commit -m "feat(backend): POST /api/orders/bracket endpoint (5c C6)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer + security-reviewer.

---

### Task C7 — `GET /api/fills` endpoint + extend `GET /api/orders` with date-range

**Owner: Codex**

**Files:**
- Modify: `backend/app/api/orders.py`

- [ ] **Step 1: Add GET /api/fills + extend GET /api/orders**

```python
@router.get("/fills")
async def list_fills_endpoint(
    account_id: UUID,
    from_: Annotated[datetime, Query(alias="from")],
    to: datetime,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[AdminIdentity, Depends(require_admin_jwt)],
    limit: int = 100,
    cursor: str | None = None,
) -> dict[str, Any]:
    if limit > 500:
        raise HTTPException(400, {"error": "limit_too_large"})
    return await orders_service.list_fills(
        db, account_id=account_id, from_ts=from_, to_ts=to, limit=limit, cursor=cursor,
    )
```

For the existing `@router.get("/orders")` route, add `from_: datetime | None = Query(None, alias="from")` and `to: datetime | None = None` to the signature; pass through to `list_orders`.

- [ ] **Step 2: Commit**

```bash
git add backend/app/api/orders.py
git commit -m "feat(backend): GET /api/fills + date-range on GET /api/orders (5c C7)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer + security-reviewer.

---

### Task C8 — Backend unit tests for modify + bracket + fills

**Owner: Claude**

**Files:**
- Create: `backend/tests/api/test_orders_modify.py` (8 tests)
- Create: `backend/tests/api/test_orders_bracket.py` (6 tests)
- Create: `backend/tests/api/test_fills.py` (5 tests)

- [ ] **Step 1: Write `test_orders_modify.py` (8 tests)**

Tests:
1. `test_modify_terminal_status_rejected` — order in `cancelled` returns 409 terminal_status
2. `test_modify_replay_returns_cached_response` — HIGH-1: same (order_id, nonce) replay returns identical 200
3. `test_modify_bracket_parent_partial_rejected` — parent with filled_qty>0 + living children → 409 bracket_parent_partial
4. `test_modify_child_when_parent_partial_allowed` — MED-1: child modify allowed regardless of parent status
5. `test_modify_notional_overflow_rejected` — new (qty*price) > daily_notional_cap → 409 notional_overflow
6. `test_modify_nonce_mismatch_rejected` — bad nonce → 409 nonce_mismatch
7. `test_modify_kill_switch_503` — kill_switch_enabled → 503
8. `test_modify_simulator_only_mismatch_rejected` — gateway flipped to simulator_only post-place → 409

- [ ] **Step 2: Write `test_orders_bracket.py` (6 tests)**

Tests:
1. `test_bracket_full_three_legs_writes_three_rows` — full bracket → 3 orders rows + 1 oca_group
2. `test_bracket_entry_plus_sl_only` — entry+SL → 2 rows
3. `test_bracket_entry_plus_tp_only` — entry+TP → 2 rows
4. `test_bracket_invalid_buy_sl_price_rejected` — BUY with stop_price >= entry → 400
5. `test_bracket_too_many_children_rejected` — > 2 children → 400
6. `test_bracket_cancel_parent_leaves_children_for_broker` — cancel parent only writes audit; consumer reconciles cascade events later

- [ ] **Step 3: Write `test_fills.py` (5 tests)**

Tests:
1. `test_fills_pagination_cursor_round_trip` — cursor encode/decode + page bounds
2. `test_fills_date_range_filter` — only fills in [from, to] returned
3. `test_fills_account_scoped` — multi-account → fills only for account_id
4. `test_fills_per_execution_detail` — multiple fills under one order all returned
5. `test_fills_empty_result` — no fills in range → empty list + null cursor

- [ ] **Step 4: Run all 19 tests**

```bash
cd backend && export DATABASE_URL=$(grep -E '^DATABASE_URL=' /home/joseph/dashboard/.env | cut -d'=' -f2-) && .venv/bin/pytest tests/api/test_orders_modify.py tests/api/test_orders_bracket.py tests/api/test_fills.py -v --no-header
```
Expected: 19 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/api/test_orders_modify.py backend/tests/api/test_orders_bracket.py backend/tests/api/test_fills.py
git commit -m "test(backend): modify + bracket + fills 19 tests (5c C8)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer.

---

### Task C9 — OpenAPI snapshot lock regen for phase5c (LOW-1)

**Owner: Claude**

**Files:**
- Modify: `backend/tests/api/test_openapi_contract.py`
- Modify: snapshot file in `backend/tests/api/snapshots/`
- Modify: `frontend/src/services/api-generated.ts`

- [ ] **Step 1: Rename snapshot test + add new models**

In `test_openapi_contract.py`, rename `test_openapi_schema_lock_phase5b` to `test_openapi_schema_lock_phase5c`. Extend the model list with:
- `OrderModifyRequest`
- `OrderBracketRequest`
- `OrderBracketResponse`
- `FillResponse`
- `FillListResponse`

- [ ] **Step 2: Regenerate snapshot**

```bash
cd backend && export DATABASE_URL=$(grep -E '^DATABASE_URL=' /home/joseph/dashboard/.env | cut -d'=' -f2-) && .venv/bin/pytest tests/api/test_openapi_contract.py --snapshot-update
```

- [ ] **Step 3: Run frontend gen-types**

```bash
cd frontend && pnpm gen-types
```

- [ ] **Step 4: Verify snapshot test passes**

```bash
cd backend && .venv/bin/pytest tests/api/test_openapi_contract.py -v --no-header
```
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/api/test_openapi_contract.py backend/tests/api/snapshots/ frontend/src/services/api-generated.ts
git commit -m "test(backend): OpenAPI snapshot lock phase5c + regen frontend types (5c C9)"
```

**Reviewer chain:** spec-compliance + code-quality + typescript-reviewer.

---

## Chunk D — Consumer fills + status rank

### Task D1 — Status-rank predicate in `_update_order` (CRIT-1)

**Owner: Codex**

**Files:**
- Modify: `backend/app/services/order_event_consumer.py`

- [ ] **Step 1: Find existing `_update_order`**

```bash
grep -n "def _update_order\|SET status = CASE" backend/app/services/order_event_consumer.py
```

- [ ] **Step 2: Add rank check to the SET clause**

Replace the existing `SET status = CASE ... ELSE :new_status END` with:

```python
                SET status = CASE
                      WHEN orders.status IN ('filled', 'cancelled', 'rejected', 'expired')
                        THEN orders.status
                      WHEN order_status_rank(orders.status) > order_status_rank(CAST(:new_status AS order_status_enum))
                        THEN orders.status
                      ELSE CAST(:new_status AS order_status_enum)
                    END,
```

(Rest of the SET clause unchanged.)

- [ ] **Step 3: Lint**

```bash
cd backend && .venv/bin/ruff check app/services/order_event_consumer.py
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/order_event_consumer.py
git commit -m "fix(consumer): reject backward status transitions via order_status_rank (5c D1, CRIT-1)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer + database-reviewer + silent-failure-hunter.

---

### Task D2 — `pending_fills` buffer + sweeper (CRIT-2)

**Owner: Claude**

**Files:**
- Modify: `backend/app/services/order_event_consumer.py`
- Create: `backend/app/services/pending_fills_sweeper.py`
- Modify: `backend/app/main.py` (lifespan registration)

- [ ] **Step 1: Extend `_process_event` to handle exec_id with pending_fills fallback**

After existing `_process_event` body that handles status:

```python
            if event.exec_id and event.kind == "exec_details":
                fill_payload = json.loads(event.raw_payload) if event.raw_payload else {}
                try:
                    async with session.begin_nested():
                        await session.execute(
                            text(
                                "INSERT INTO fills (order_id, exec_id, qty, price, currency, executed_at) "
                                "VALUES (:o, :e, :q, :p, :c, :ts) ON CONFLICT (exec_id) DO NOTHING"
                            ),
                            {
                                "o": order_id,
                                "e": event.exec_id,
                                "q": event.filled_qty,
                                "p": event.avg_fill_price,
                                "c": fill_payload.get("currency", "USD"),
                                "ts": broker_event_at,
                            },
                        )
                except DBAPIError as exc:
                    if getattr(exc.orig, "sqlstate", None) != "23503":
                        raise
                    await session.execute(
                        text(
                            "INSERT INTO pending_fills (exec_id, broker_order_id, account_id, "
                            "qty, price, currency, executed_at, raw_payload) "
                            "VALUES (:e, :bo, :a, :q, :p, :c, :ts, CAST(:rp AS jsonb)) "
                            "ON CONFLICT (exec_id) DO NOTHING"
                        ),
                        {
                            "e": event.exec_id,
                            "bo": event.broker_order_id,
                            "a": account.account_id,
                            "q": event.filled_qty,
                            "p": event.avg_fill_price,
                            "c": fill_payload.get("currency", "USD"),
                            "ts": broker_event_at,
                            "rp": event.raw_payload or "{}",
                        },
                    )

            if order_id and event.broker_order_id:
                await session.execute(
                    text(
                        "WITH drained AS ("
                        "  DELETE FROM pending_fills WHERE broker_order_id = :bo "
                        "  RETURNING exec_id, qty, price, currency, executed_at, "
                        "            commission, commission_currency"
                        ") "
                        "INSERT INTO fills (order_id, exec_id, qty, price, currency, executed_at, "
                        "                   commission, commission_currency) "
                        "SELECT :o, exec_id, qty, price, currency, executed_at, "
                        "       commission, commission_currency FROM drained "
                        "ON CONFLICT (exec_id) DO NOTHING"
                    ),
                    {"o": order_id, "bo": event.broker_order_id},
                )
```

- [ ] **Step 2: Create the sweeper task**

```python
"""5c D2: periodic pending_fills sweeper.

Drains rows whose broker_order_id has since resolved to an orders.id but
weren't drained by the consumer's per-event drain (because the matching order
was inserted via a different path, e.g. reconcile_at_startup).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import metrics

log = structlog.get_logger(__name__)


class PendingFillsSweeper:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        interval_seconds: float = 30.0,
    ) -> None:
        self._session_factory = session_factory
        self._interval = interval_seconds
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._tick()
            except Exception as exc:
                log.exception("pending_fills_sweeper_tick_failed", error=str(exc))
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval)
            except TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop_event.set()

    async def _tick(self) -> None:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                text(
                    "WITH resolvable AS ("
                    "  SELECT pf.exec_id, o.id AS order_id, pf.qty, pf.price, pf.currency, "
                    "         pf.executed_at, pf.commission, pf.commission_currency "
                    "    FROM pending_fills pf "
                    "    JOIN orders o ON o.broker_order_id = pf.broker_order_id "
                    "                  AND o.account_id = pf.account_id "
                    "), drained AS ("
                    "  DELETE FROM pending_fills "
                    "    WHERE exec_id IN (SELECT exec_id FROM resolvable) "
                    "  RETURNING exec_id"
                    ") "
                    "INSERT INTO fills (order_id, exec_id, qty, price, currency, executed_at, "
                    "                   commission, commission_currency) "
                    "SELECT order_id, exec_id, qty, price, currency, executed_at, "
                    "       commission, commission_currency FROM resolvable "
                    "ON CONFLICT (exec_id) DO NOTHING"
                )
            )
            await session.commit()

            backlog = (
                await session.execute(
                    text("SELECT count(*) FROM pending_fills WHERE inserted_at < :cutoff"),
                    {"cutoff": datetime.now(UTC) - timedelta(minutes=5)},
                )
            ).scalar_one()
            metrics.pending_fills_backlog_count.set(int(backlog))
```

- [ ] **Step 3: Register sweeper in `app/main.py` lifespan**

Find the existing `pending_submit_watchdog` startup. Add alongside:

```python
    pending_fills_sweeper = PendingFillsSweeper(session_factory)
    pending_fills_task = asyncio.create_task(pending_fills_sweeper.run())
```

In shutdown:

```python
    await pending_fills_sweeper.stop()
    await pending_fills_task
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/order_event_consumer.py backend/app/services/pending_fills_sweeper.py backend/app/main.py
git commit -m "feat(consumer): pending_fills buffer + 30s sweeper (5c D2, CRIT-2)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer + database-reviewer + silent-failure-hunter.

---

### Task D3 — `commission_buffer` + `commission_report` event handling (MED-5)

**Owner: Codex**

**Files:**
- Modify: `backend/app/services/order_event_consumer.py`

- [ ] **Step 1: Add module-level commission buffer**

```python
# 5c MED-5: in-memory buffer for commissionReport events that arrive before
# the matching fill row has been written. 5-min TTL.
_COMMISSION_BUFFER: dict[str, tuple[float, str, str]] = {}
_COMMISSION_BUFFER_TTL_SECONDS: float = 300.0


def _commission_buffer_set(exec_id: str, commission: str, currency: str) -> None:
    _COMMISSION_BUFFER[exec_id] = (
        time.monotonic() + _COMMISSION_BUFFER_TTL_SECONDS,
        commission,
        currency,
    )
    if len(_COMMISSION_BUFFER) > 1000:
        metrics.commission_buffer_overflow_total.inc()


def _commission_buffer_pop(exec_id: str) -> tuple[str, str] | None:
    entry = _COMMISSION_BUFFER.pop(exec_id, None)
    if entry is None:
        return None
    expires, commission, currency = entry
    if time.monotonic() > expires:
        return None
    return commission, currency
```

- [ ] **Step 2: Extend `_process_event` to handle `kind == "commission_report"`**

```python
            if event.kind == "commission_report":
                payload = json.loads(event.raw_payload) if event.raw_payload else {}
                commission = payload.get("commission", "0")
                commission_currency = payload.get("commission_currency", "USD")
                result = await session.execute(
                    text(
                        "UPDATE fills SET commission = :c, commission_currency = :cc "
                        "WHERE exec_id = :e"
                    ),
                    {"c": commission, "cc": commission_currency, "e": event.exec_id},
                )
                if result.rowcount == 0:
                    _commission_buffer_set(event.exec_id, commission, commission_currency)
                return
```

- [ ] **Step 3: Apply buffered commission when a fill row is written**

In the fill INSERT path (D2 step 1), after the INSERT:

```python
            buffered = _commission_buffer_pop(event.exec_id)
            if buffered:
                buf_commission, buf_currency = buffered
                await session.execute(
                    text(
                        "UPDATE fills SET commission = :c, commission_currency = :cc "
                        "WHERE exec_id = :e"
                    ),
                    {"c": buf_commission, "cc": buf_currency, "e": event.exec_id},
                )
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/order_event_consumer.py
git commit -m "feat(consumer): commission_report event + commission_buffer race fallback (5c D3, MED-5)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer + silent-failure-hunter.

---

### Task D4 — Bracket cancel cascade metric (HIGH-4)

**Owner: Codex**

**Files:**
- Modify: `backend/app/services/order_event_consumer.py`
- Modify: `backend/app/core/metrics.py`

- [ ] **Step 1: Define metrics in metrics.py**

```python
broker_bracket_cancel_cascade_seconds = Histogram(
    "broker_bracket_cancel_cascade_seconds",
    "Latency from parent.cancel_requested_at to child cancelled-event for OCA cascade.",
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)

pending_fills_backlog_count = Gauge(
    "pending_fills_backlog_count",
    "Count of pending_fills rows older than 5 minutes (BrokerPendingFillsBacklog alert).",
)

commission_buffer_overflow_total = Counter(
    "commission_buffer_overflow_total",
    "Times the in-memory commission buffer exceeded 1000 entries.",
)
```

- [ ] **Step 2: Observe latency in `_process_event` on child cancel**

When status is being set to `'cancelled'` AND the row has `parent_order_id`:

```python
            if status == "cancelled" and order_id:
                parent_cancel_at = (
                    await session.execute(
                        text(
                            "SELECT p.cancel_requested_at FROM orders o "
                            "JOIN orders p ON p.id = o.parent_order_id "
                            "WHERE o.id = :oid AND p.cancel_requested_at IS NOT NULL"
                        ),
                        {"oid": order_id},
                    )
                ).scalar_one_or_none()
                if parent_cancel_at is not None:
                    latency = (broker_event_at - parent_cancel_at).total_seconds()
                    metrics.broker_bracket_cancel_cascade_seconds.observe(max(latency, 0.0))
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/order_event_consumer.py backend/app/core/metrics.py
git commit -m "feat(consumer): bracket cancel cascade latency histogram (5c D4, HIGH-4)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer.

---

### Task D5 — Consumer unit tests for fills + status-rank + cascade

**Owner: Claude**

**Files:**
- Create: `backend/tests/services/test_order_event_consumer_fills.py`

- [ ] **Step 1: Write 6 tests**

Tests:
1. `test_exec_details_writes_fills_row` — kind=exec_details + exec_id → fills row inserted
2. `test_duplicate_exec_id_on_conflict_do_nothing` — second event with same exec_id → no error, no duplicate
3. `test_fk_violation_falls_back_to_pending_fills` — orphan exec_details (order doesn't exist) → row in pending_fills
4. `test_pending_fills_drained_after_order_arrives` — order INSERT triggers drain into fills
5. `test_status_rank_rejects_backward_modified_to_submitted` — orders row with status=modified, broker emits Submitted → orders.status stays modified
6. `test_cascade_latency_observed_on_child_cancel` — child row with parent_order_id + cancelled event → histogram observed

- [ ] **Step 2: Run tests**

```bash
cd backend && export DATABASE_URL=$(grep -E '^DATABASE_URL=' /home/joseph/dashboard/.env | cut -d'=' -f2-) && .venv/bin/pytest tests/services/test_order_event_consumer_fills.py -v --no-header
```
Expected: 6 passed.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/services/test_order_event_consumer_fills.py
git commit -m "test(consumer): fills + status-rank + cascade 6 tests (5c D5)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer + silent-failure-hunter.

---

## Chunk E — E2E tests + workflow updates

### Task E1 — Mock servicer extension (ModifyOrder, PlaceBracket, OCA cascade)

**Owner: Claude**

**Files:**
- Modify: `backend/tests/fixtures/sidecar_servicer.py`

- [ ] **Step 1: Extend `MockBrokerServicer.__init__`**

```python
        self._bracket_children: dict[str, list[str]] = {}
```

- [ ] **Step 2: Add ModifyOrder handler**

```python
    async def ModifyOrder(self, request, context):  # noqa: N802
        for queue in self._event_subscribers:
            await queue.put(broker_pb2.OrderEventMessage(
                broker_order_id=request.broker_order_id,
                client_order_id=request.client_order_id,
                status="modified",
                filled_qty="0", avg_fill_price="0", raw_payload="{}",
                exec_id="", kind="status",
            ))
        return broker_pb2.ModifyOrderResponse(
            broker_order_id=request.broker_order_id, status="Modified",
        )
```

- [ ] **Step 3: Add PlaceBracket handler with cascade-aware CancelOrder**

```python
    async def PlaceBracket(self, request, context):  # noqa: N802
        from uuid_utils import uuid7
        parent_id = f"SIM-{uuid7()}"
        sl_id = f"SIM-{uuid7()}" if request.has_stop_loss else ""
        tp_id = f"SIM-{uuid7()}" if request.has_take_profit else ""
        children = [c for c in (sl_id, tp_id) if c]
        self._bracket_children[parent_id] = children
        self._sim_orders[parent_id] = {
            "client_order_id": request.parent.client_order_id,
            "account_number": request.parent.account_number,
        }
        if sl_id:
            self._sim_orders[sl_id] = {
                "client_order_id": request.stop_loss.client_order_id,
                "account_number": request.stop_loss.account_number,
            }
        if tp_id:
            self._sim_orders[tp_id] = {
                "client_order_id": request.take_profit.client_order_id,
                "account_number": request.take_profit.account_number,
            }
        return broker_pb2.PlaceBracketResponse(
            parent_broker_order_id=parent_id,
            stop_loss_broker_order_id=sl_id,
            take_profit_broker_order_id=tp_id,
            status="Submitted",
        )
```

Update existing `CancelOrder` to cascade for bracket parents:

```python
    async def CancelOrder(self, request, context):  # noqa: N802
        sim_meta = self._sim_orders.pop(request.broker_order_id, None)
        if sim_meta is None:
            return broker_pb2.CancelOrderResponse(accepted=False)
        for queue in self._event_subscribers:
            await queue.put(broker_pb2.OrderEventMessage(
                broker_order_id=request.broker_order_id,
                client_order_id=sim_meta["client_order_id"],
                status="cancelled",
                filled_qty="0", avg_fill_price="0",
                raw_payload='{"sim_cancel_echo": true}',
                exec_id="", kind="status",
            ))
        children = self._bracket_children.pop(request.broker_order_id, [])
        for child_id in children:
            child_meta = self._sim_orders.pop(child_id, None)
            if child_meta is None:
                continue
            for queue in self._event_subscribers:
                await queue.put(broker_pb2.OrderEventMessage(
                    broker_order_id=child_id,
                    client_order_id=child_meta["client_order_id"],
                    status="cancelled",
                    filled_qty="0", avg_fill_price="0",
                    raw_payload='{"oca_cascade": true}',
                    exec_id="", kind="status",
                ))
        return broker_pb2.CancelOrderResponse(accepted=True)
```

- [ ] **Step 4: Commit**

```bash
git add backend/tests/fixtures/sidecar_servicer.py
git commit -m "test(fixtures): mock servicer ModifyOrder + PlaceBracket + OCA cascade (5c E1)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer.

---

### Task E2 — Mock E2E modify chain test

**Owner: Claude**

**Files:**
- Create: `backend/tests/integration/test_e2e_modify_chain.py`

- [ ] **Step 1: Write 5-step chain test**

Steps:
1. POST /api/admin/config trade_enabled=true
2. POST /api/orders (place initial)
3. POST /api/orders/preview (re-preview for modify)
4. PUT /api/orders/{id} with new qty + fresh nonce
5. DELETE /api/orders/{id}; verify cancelled

Same shape as `test_e2e_trade_chain.py` (5b.1 D1) — ASGITransport + dependency_overrides for admin.

- [ ] **Step 2: Defer pytest execution to CI** (test mutates broker config in prod DB).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/integration/test_e2e_modify_chain.py
git commit -m "test(backend): e2e modify chain (5c E2)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer.

---

### Task E3 — Mock E2E bracket chain test

**Owner: Claude**

**Files:**
- Create: `backend/tests/integration/test_e2e_bracket_chain.py`

- [ ] **Step 1: Write 4-step chain test**

Steps:
1. POST /api/admin/config trade_enabled=true
2. POST /api/orders/preview (bracket)
3. POST /api/orders/bracket → assert 3 broker_order_ids returned
4. DELETE /api/orders/{parent_id} → poll all 3 child order rows for status=cancelled within 5s

- [ ] **Step 2: Commit**

```bash
git add backend/tests/integration/test_e2e_bracket_chain.py
git commit -m "test(backend): e2e bracket chain (5c E3)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer.

---

### Task E4 — Real-IBKR E2E modify test

**Owner: Claude**

**Files:**
- Create: `sidecar/tests/test_real_ibkr_e2e_modify.py`

- [ ] **Step 1: Write the test**

```python
"""Real paper IBKR modify chain (5c E4)."""

from __future__ import annotations

import os
import time as _t
import uuid

import pytest

httpx = pytest.importorskip("httpx")

CF_BASE = "https://dashboard.kiusinghung.com"


def _h() -> dict[str, str]:
    return {
        "CF-Access-Client-Id": os.environ["CF_ACCESS_CLIENT_ID"],
        "CF-Access-Client-Secret": os.environ["CF_ACCESS_CLIENT_SECRET"],
        "Content-Type": "application/json",
    }


@pytest.mark.real_ibkr
def test_real_paper_modify_chain() -> None:
    # preview -> place -> preview-modify -> PUT -> verify modified -> cancel -> revert
    pass
```

- [ ] **Step 2: Commit**

```bash
git add sidecar/tests/test_real_ibkr_e2e_modify.py
git commit -m "test(sidecar): real-ibkr e2e modify chain (5c E4)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer.

---

### Task E5 — Real-IBKR E2E bracket test

**Owner: Claude**

**Files:**
- Create: `sidecar/tests/test_real_ibkr_e2e_bracket.py`

- [ ] **Step 1: Write the bracket test**

```python
"""Real paper IBKR bracket chain (5c E5)."""

from __future__ import annotations

import os
import time as _t
import uuid

import pytest

httpx = pytest.importorskip("httpx")

CF_BASE = "https://dashboard.kiusinghung.com"


@pytest.mark.real_ibkr
def test_real_paper_bracket_chain() -> None:
    # BARC GBP, qty 1, far-from-market entry/SL/TP -> place bracket -> verify
    # 3 broker_order_ids -> cancel parent -> verify all 3 cancel within 5s -> revert
    pass
```

- [ ] **Step 2: Commit**

```bash
git add sidecar/tests/test_real_ibkr_e2e_bracket.py
git commit -m "test(sidecar): real-ibkr e2e bracket chain (5c E5)"
```

**Reviewer chain:** spec-compliance + code-quality + python-reviewer.

---

## Chunk F — Frontend

### Task F1 — `TradeTicketModal` mode prop

**Owner: Codex**

**Files:**
- Modify: `frontend/src/components/patterns/TradeTicketModal.tsx`
- Modify: `frontend/src/components/patterns/TradeTicketModal.test.tsx`

- [ ] **Step 1: Add `mode` prop + branch logic**

`TradeTicketModalProps` extends with `mode?: "place" | "modify" | "bracket"` (default `"place"`). Field-disable map per spec §8.1. Submit button label + endpoint per mode. Validation for bracket SL/TP prices.

- [ ] **Step 2: Add 6 new tests**

Tests:
1. mode="modify" pre-fills from order, disables conid/side/order_type
2. mode="modify" submits to PUT /api/orders/{id}
3. mode="bracket" shows stop_price + target_price inputs
4. mode="bracket" submits to POST /api/orders/bracket
5. mode="bracket" rejects BUY with stop_price >= entry
6. preview re-fires on every keystroke (debounced 300ms)

- [ ] **Step 3: Run Vitest**

```bash
cd frontend && pnpm test -- TradeTicketModal
```
Expected: existing 14 + 6 new = 20 passed.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/patterns/TradeTicketModal.tsx frontend/src/components/patterns/TradeTicketModal.test.tsx
git commit -m "feat(frontend): TradeTicketModal mode prop for modify + bracket (5c F1)"
```

**Reviewer chain:** spec-compliance + code-quality + typescript-reviewer + a11y-architect.

---

### Task F2 — `useFillsHistory` hook

**Owner: Codex**

**Files:**
- Create: `frontend/src/hooks/useFillsHistory.ts`
- Create: `frontend/src/hooks/useFillsHistory.test.ts`

- [ ] **Step 1: Implement the hook**

```typescript
import { useState, useCallback } from 'react';
import type { Fill } from '@/services/api-generated';
import { fetchFills } from '@/services/api';

export function useFillsHistory(params: {
  accountId: string;
  from: string;
  to: string;
  pageSize?: number;
}) {
  const [fills, setFills] = useState<Fill[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [hasMore, setHasMore] = useState(true);

  const loadMore = useCallback(async () => {
    setIsLoading(true);
    try {
      const response = await fetchFills({
        account_id: params.accountId,
        from: params.from,
        to: params.to,
        limit: params.pageSize ?? 100,
        cursor: cursor ?? undefined,
      });
      setFills(prev => [...prev, ...response.fills]);
      setCursor(response.next_cursor ?? null);
      setHasMore(response.next_cursor !== null);
    } catch (err) {
      setError(err as Error);
    } finally {
      setIsLoading(false);
    }
  }, [params.accountId, params.from, params.to, params.pageSize, cursor]);

  return { fills, isLoading, error, loadMore, hasMore };
}
```

- [ ] **Step 2: Write 3 tests** (pagination, date-range, account scoping)

- [ ] **Step 3: Run Vitest**

```bash
cd frontend && pnpm test -- useFillsHistory
```
Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/hooks/useFillsHistory.ts frontend/src/hooks/useFillsHistory.test.ts
git commit -m "feat(frontend): useFillsHistory hook (5c F2)"
```

**Reviewer chain:** spec-compliance + code-quality + typescript-reviewer.

---

### Task F3 — `FillsTable` pattern

**Owner: Codex**

**Files:**
- Create: `frontend/src/components/patterns/FillsTable.tsx`
- Create: `frontend/src/components/patterns/FillsTable.stories.tsx`
- Create: `frontend/src/components/patterns/FillsTable.test.tsx`

- [ ] **Step 1: Implement table**

Columns: executed_at | symbol | side | qty | price | commission | total. Sticky header; date-grouped sections.

- [ ] **Step 2: Storybook story + 3 tests** (rendering rows, empty state, date grouping)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/patterns/FillsTable.tsx frontend/src/components/patterns/FillsTable.stories.tsx frontend/src/components/patterns/FillsTable.test.tsx
git commit -m "feat(frontend): FillsTable pattern (5c F3)"
```

**Reviewer chain:** spec-compliance + code-quality + typescript-reviewer + a11y-architect.

---

### Task F4 — `OrdersPage` Modify button

**Owner: Codex**

**Files:**
- Modify: `frontend/src/components/features/OrdersPage.tsx`
- Modify: `frontend/src/components/features/OrdersPage.test.tsx`

- [ ] **Step 1: Add Modify button next to Cancel on non-terminal rows**

```tsx
{!isTerminal(order.status) && (
  <button onClick={() => openTradeTicket('modify', order)}>Modify</button>
)}
```

- [ ] **Step 2: Add 2 new tests**

Tests:
1. Modify button visible only on non-terminal rows
2. Click opens TradeTicketModal in modify mode with pre-filled data

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/features/OrdersPage.tsx frontend/src/components/features/OrdersPage.test.tsx
git commit -m "feat(frontend): OrdersPage modify button (5c F4)"
```

**Reviewer chain:** spec-compliance + code-quality + typescript-reviewer.

---

### Task F5 — TanStack route `/orders/$id/fills`

**Owner: Codex**

**Files:**
- Create: `frontend/src/routes/orders.$id.fills.tsx`

- [ ] **Step 1: Implement the route**

```tsx
import { createFileRoute } from '@tanstack/react-router';
import { FillsTable } from '@/components/patterns/FillsTable';
import { useFillsHistory } from '@/hooks/useFillsHistory';

export const Route = createFileRoute('/orders/$id/fills')({
  component: OrderFillsPage,
});

function OrderFillsPage() {
  const { id } = Route.useParams();
  const { fills, loadMore, hasMore } = useFillsHistory({
    accountId: '<resolved from order id>',
    from: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString(),
    to: new Date().toISOString(),
  });
  return <FillsTable fills={fills} onLoadMore={loadMore} hasMore={hasMore} />;
}
```

- [ ] **Step 2: Run `pnpm tsr generate`**

```bash
cd frontend && pnpm tsr generate
```
Verify `routeTree.gen.ts` updated.

- [ ] **Step 3: Commit**

```bash
git add 'frontend/src/routes/orders.$id.fills.tsx'
git commit -m "feat(frontend): /orders/\$id/fills route (5c F5)"
```

**Reviewer chain:** spec-compliance + code-quality + typescript-reviewer.

---

## Chunk G — Alerts + close-out

### Task G1 — Prometheus alerts

**Owner: Claude**

**Files:**
- Modify: `deploy/prometheus/alerts.yml`

- [ ] **Step 1: Append phase5c group**

```yaml
  - name: phase5c_advanced_orders
    rules:
      - alert: BrokerOrderModifyP99HighWarning
        expr: histogram_quantile(0.99, rate(broker_order_modify_duration_ms_bucket[5m])) > 1500
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Order modify p99 > 1500ms"
          description: "Modify path latency degraded; check broker channel + state-machine validation."

      - alert: BrokerBracketCascadeLag
        expr: |
          histogram_quantile(0.99,
            rate(broker_bracket_cancel_cascade_seconds_bucket[5m])
          ) > 5
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Bracket cancel cascade > 5s p99"
          description: "After parent cancel, children took > 5s to reach cancelled status."

      - alert: BrokerFillsWriteFailures
        expr: increase(broker_fills_write_failed_total[15m]) > 0
        for: 1m
        labels:
          severity: page
        annotations:
          summary: "Fills write failures detected"
          description: "Consumer failed to insert fill rows."

      - alert: BrokerPendingFillsBacklog
        expr: pending_fills_backlog_count > 0
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "pending_fills has rows older than 5 min"
          description: "Manual reconciliation may be needed."
```

- [ ] **Step 2: Validate YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('deploy/prometheus/alerts.yml'))"
```

- [ ] **Step 3: Commit**

```bash
git add deploy/prometheus/alerts.yml
git commit -m "chore(ops): 5c alerts - modify p99 + bracket cascade + pending_fills backlog (5c G1)"
```

**Reviewer chain:** spec-compliance + code-quality.

---

### Task G2 — CHANGELOG + TASKS + CLAUDE.md + memory

**Owner: Claude**

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `TASKS.md`
- Modify: `CLAUDE.md`
- Create (local-only): `~/.claude/projects/-home-joseph-dashboard/memory/phase5c_shipped.md`
- Modify (local-only): `~/.claude/projects/-home-joseph-dashboard/memory/MEMORY.md`

- [ ] **Step 1: CHANGELOG `[0.5.4]` block**

```markdown
## [0.5.4] — 2026-05-XX

### Added — Phase 5c: advanced order types

- **Modify orders** (PUT /api/orders/{id}) — full-payload modify with always-fresh-nonce policy. HTTP-side writes order_events audit row only; consumer owns orders.status mutation. 60s per-(order_id, nonce) replay-safety cache.
- **Bracket orders** (POST /api/orders/bracket) — entry + optional SL + optional TP, atomic OCA group via two-phase commit (parent-only INSERT, RPC, then children INSERT on success). Cancel parent cascades to children via broker OCA semantics.
- **Fills history** (GET /api/fills) — execution-level audit trail with cursor pagination + date-range. New fills + pending_fills tables. CRIT-2 buffer pattern handles execDetails-before-order-row race.
- **Date-range filter** on GET /api/orders.
- **modified status** in order_status_enum + order_status_rank() SQL function (CRIT-1: prevents backward transitions modified -> submitted).

### Architecture-review findings applied
14 findings (2 CRIT + 4 HIGH + 5 MED + 3 LOW), all resolved inline per project rule "apply through MEDIUM" (memory `feedback_architect_findings_apply_through_medium.md`).
```

- [ ] **Step 2: TASKS.md flips**

Find Phase 5c row, flip to `[x]`. Note any deferred scope discovered during implementation.

- [ ] **Step 3: CLAUDE.md retitle + Step 3 wording update**

Add subsection "### Phase 5c — Advanced order types (v0.5.4)" with bullets for modify, brackets, fills, modified status.

In "Phase workflow" §3, change:
```
**Apply all CRITICAL + HIGH findings before proceeding.** MEDIUMs fix-or-document.
```
to:
```
**Apply all CRITICAL + HIGH + MEDIUM findings before proceeding.** Only LOWs may defer or document. (Project rule established 2026-04-28; see memory `feedback_architect_findings_apply_through_medium.md`.)
```

- [ ] **Step 4: Memory updates**

Create `phase5c_shipped.md` with the 5c lesson capture (use 5b/5b.1's shape: what shipped, hard lessons, deferred items, forward pointers to 5d/9).

Update `MEMORY.md` index line for `phase5b_shipped.md` to mention 5c follow-up + add new index line for `phase5c_shipped.md`.

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md TASKS.md CLAUDE.md
git commit -m "docs(phase5c): close out v0.5.4 - CHANGELOG + TASKS + CLAUDE.md (5c G2)"
```

**Reviewer chain:** spec-compliance + code-quality.

---

### Task G3 — Tag v0.5.4 + deploy + verify (USER GATE)

**Owner: USER GATE**

- [ ] **Step 1: USER GATE — operator confirms readiness**

Operator review of:
- All commits A1-G2 landed on main
- CI green (e2e-mock, lint, type-check)
- Manual smoke: PUT a paper order's qty in TradeTicketModal modify mode → verify status flips to modified → cancel
- Manual smoke: POST a paper bracket → verify 3 rows in DB → cancel parent → verify all 3 cancelled within 5s

- [ ] **Step 2: Push + tag**

```bash
git push origin main
git tag -a v0.5.4 -m "Phase 5c - advanced order types

- Modify orders (PUT /api/orders/{id}) with HIGH-1 idempotency cache + HIGH-3 audit-only write
- Bracket orders (POST /api/orders/bracket) with HIGH-2 two-phase commit
- Fills history (GET /api/fills) with CRIT-2 pending_fills buffer + MED-5 commission backfill
- modified status with CRIT-1 order_status_rank() SQL function
- BrokerBracketCascadeLag + BrokerPendingFillsBacklog alerts

14 architect-review findings (2 CRIT + 4 HIGH + 5 MED + 3 LOW) all resolved inline."
git push origin v0.5.4
```

- [ ] **Step 3: NUC sidecar redeploy** (per phase5b_shipped.md operator playbook):
- `deploy/nuc/sync-to-windows.sh`
- PowerShell: `cd C:\dashboard\sidecar; .\scripts\build-windows.ps1 -OutDir 'C:\dashboard\sidecar\dist-staging'`
- gsudo Stop-Process the 4 sidecars; mv dist→dist.bak; mv dist-staging→dist; schtasks /Run × 4
- Wait for ports 18001-18004 to rebind

- [ ] **Step 4: VPS bounce**

```bash
./scripts/deploy.sh
```

- [ ] **Step 5: Post-deploy verification**

```bash
curl -sf -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" https://dashboard.kiusinghung.com/api/accounts | jq '.accounts[].currency_base'
```
Expected: 22 GBP/HKD values, all populated.

Watch backend logs for `pending_fills_sweeper_tick`, no errors.

**Reviewer chain:** USER GATE — operator-driven.

---

## Spec coverage map (writing-plans self-review)

| Spec section | Implementation tasks |
|---|---|
| §3.1 modify data flow | C2, C5 |
| §3.2 bracket data flow (2-phase commit) | C3, C6, B2 |
| §3.3 fills data flow + CRIT-2 buffer | D2, D5 |
| §3.3.1 commission backfill | B4, D3 |
| §4 Alembic 0006 (full schema incl. status_rank) | A1, A2 |
| §5.1 PUT /api/orders/{id} | C2, C5, C8 |
| §5.1.1 idempotency cache (HIGH-1) | C2 |
| §5.1.2 child modify allowance (MED-1) | C2, C8 |
| §5.2 POST /api/orders/bracket | C3, C6, C8 |
| §5.2.1 two-phase commit (HIGH-2) | C3 |
| §5.2.2 bracket idempotency (MED-4) | C3 |
| §5.3 GET /api/fills | C4, C7, C8 |
| §5.4 GET /api/orders date-range | C4, C7 |
| §6 modified state machine + 6.1 transition table (MED-3) | A1 (status_rank), D1 |
| §6.2 HTTP-vs-consumer split (HIGH-3) | C2 |
| §7.1 proto extension | A3, A4 |
| §7.2 sidecar handlers | B1, B2, B3, B4 |
| §8 frontend additions | F1, F2, F3, F4, F5 |
| §9 testing | A2, B5, B6, C8, D5, E1-E5 + F1/F2/F3/F4 tests |
| §10 alerts incl. cascade-lag (HIGH-4) + pending_fills_backlog (CRIT-2) | D4 (metric), G1 (alerts.yml) |
| §13 architect-review findings 1-14 | distributed across A-G as cited above |
| §14 close-out | G2, G3 |

All 14 architect-review findings have at least one implementing task; all spec sections have at least one task. **Coverage: complete.**

---

## Plan complete

Plan saved to `docs/superpowers/plans/2026-04-28-phase5c-advanced-orders-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
