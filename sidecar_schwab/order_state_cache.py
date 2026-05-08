import json
from dataclasses import dataclass, field
from typing import Any, Protocol

# CRIT-3: cap the exec_id set so a pathologically-filled order cannot grow
# the Redis hash entry without bound.
_MAX_EXEC_IDS = 1000


class RedisLike(Protocol):
    async def hget(self, name: str, key: str) -> Any: ...

    async def hset(self, name: str, key: str, value: str) -> Any: ...

    async def hgetall(self, name: str) -> dict[Any, Any]: ...

    async def expire(self, name: str, seconds: int) -> Any: ...

    async def delete(self, name: str) -> Any: ...


@dataclass
class OrderState:
    client_order_id: str
    broker_order_id: str
    schwab_status: str
    entered_time_iso: str = ""
    # CRIT-3: set of exec_ids seen so far; replaces the single last_exec_id
    # scalar that caused fills 1..N-1 to be re-emitted on every subsequent poll.
    last_exec_ids: set[str] = field(default_factory=set)


def _state_to_dict(state: OrderState) -> dict[str, Any]:
    """Serialise OrderState to a JSON-safe dict (set → sorted list)."""
    return {
        "client_order_id": state.client_order_id,
        "broker_order_id": state.broker_order_id,
        "schwab_status": state.schwab_status,
        "entered_time_iso": state.entered_time_iso,
        "last_exec_ids": sorted(state.last_exec_ids),
    }


def _state_from_dict(data: dict[str, Any]) -> OrderState:
    """Deserialise OrderState from a JSON dict; handles old last_exec_id format."""
    exec_ids: set[str] = set()
    if "last_exec_ids" in data:
        exec_ids = set(data["last_exec_ids"])
    elif "last_exec_id" in data and data["last_exec_id"]:
        # Forward-compat: migrate old single-string records transparently.
        exec_ids = {data["last_exec_id"]}
    return OrderState(
        client_order_id=data["client_order_id"],
        broker_order_id=data["broker_order_id"],
        schwab_status=data["schwab_status"],
        entered_time_iso=data.get("entered_time_iso", ""),
        last_exec_ids=exec_ids,
    )


_TTL_SECONDS = 7 * 24 * 3600


class OrderStateCache:
    def __init__(
        self, *, redis: RedisLike, gateway_label: str, account_id: str
    ) -> None:
        self._redis = redis
        self._gateway_label = gateway_label
        self._account_id = account_id
        self._key = f"schwab:order_state:{gateway_label}:{account_id}"
        self._mem: dict[str, OrderState] = {}

    async def hydrate(self) -> None:
        raw = await self._redis.hgetall(self._key)
        self._mem = {}
        for field_name, value in raw.items():
            client_order_id = (
                field_name.decode() if isinstance(field_name, bytes) else field_name
            )
            val_str = value.decode() if isinstance(value, bytes) else value
            self._mem[client_order_id] = _state_from_dict(json.loads(val_str))

    async def get(self, client_order_id: str) -> OrderState | None:
        if client_order_id in self._mem:
            return self._mem[client_order_id]

        raw = await self._redis.hget(self._key, client_order_id)
        if raw is None:
            return None

        val_str = raw.decode() if isinstance(raw, bytes) else raw
        state = _state_from_dict(json.loads(val_str))
        self._mem[client_order_id] = state
        return state

    async def put(self, state: OrderState) -> None:
        self._mem[state.client_order_id] = state
        await self._redis.hset(
            self._key, state.client_order_id, json.dumps(_state_to_dict(state))
        )
        await self._redis.expire(self._key, _TTL_SECONDS)

    async def invalidate_all(self) -> None:
        self._mem.clear()
        await self._redis.delete(self._key)

    def known_client_order_ids(self) -> set[str]:
        return set(self._mem.keys())
