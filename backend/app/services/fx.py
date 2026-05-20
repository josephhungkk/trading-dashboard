"""Shared FX rate helper.

Reads fx:mid:{from}:{to} key from Redis FX poller. Returns Decimal("1.0")
on cache miss as a fail-safe.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


async def get_fx_rate(currency: str, redis: Any, base: str = "USD") -> Decimal:
    if currency == base:
        return Decimal("1.0")
    cached = await redis.get(f"fx:mid:{currency}:{base}")
    if cached is None:
        return Decimal("1.0")
    return Decimal(cached.decode() if isinstance(cached, bytes) else cached)
