"""Unit tests for oco_group_id_for_ibkr determinism, uniqueness, and format."""

from __future__ import annotations

import re
import uuid

from app.services.oco_orchestrator import oco_group_id_for_ibkr


def test_group_id_fits_32_chars() -> None:
    for _ in range(50):
        result = oco_group_id_for_ibkr(uuid.uuid4())
        assert len(result) <= 32, result


def test_group_id_is_deterministic() -> None:
    link_id = uuid.uuid4()
    assert oco_group_id_for_ibkr(link_id) == oco_group_id_for_ibkr(link_id)


def test_group_id_unique_across_links() -> None:
    seen = {oco_group_id_for_ibkr(uuid.uuid4()) for _ in range(100)}
    assert len(seen) == 100  # extremely high collision resistance expected


def test_group_id_format() -> None:
    result = oco_group_id_for_ibkr(uuid.UUID(int=0))
    assert re.match(r"^OCO-[0-9a-f]{24}$", result), result
