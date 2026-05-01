"""playwright-stealth bootstrap — masks automation fingerprints."""
from __future__ import annotations

from typing import Any

from playwright_stealth import Stealth


async def apply_stealth(context: Any) -> None:
    await Stealth().apply_stealth_async(context)
