import json
from dataclasses import asdict, dataclass
from typing import Any, Protocol


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
    last_exec_id: str = ""


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
        for field, value in raw.items():
            client_order_id = field.decode() if isinstance(field, bytes) else field
            val_str = value.decode() if isinstance(value, bytes) else value
            data = json.loads(val_str)
            self._mem[client_order_id] = OrderState(**data)

    async def get(self, client_order_id: str) -> OrderState | None:
        if client_order_id in self._mem:
            return self._mem[client_order_id]

        raw = await self._redis.hget(self._key, client_order_id)
        if raw is None:
            return None

        val_str = raw.decode() if isinstance(raw, bytes) else raw
        state = OrderState(**json.loads(val_str))
        self._mem[client_order_id] = state
        return state

    async def put(self, state: OrderState) -> None:
        self._mem[state.client_order_id] = state
        await self._redis.hset(
            self._key, state.client_order_id, json.dumps(asdict(state))
        )
        await self._redis.expire(self._key, _TTL_SECONDS)

    async def invalidate_all(self) -> None:
        self._mem.clear()
        await self._redis.delete(self._key)

    def known_client_order_ids(self) -> set[str]:
        return set(self._mem.keys())
