"""Read-side schema translator for chart_layouts (spec §3 line 347).

Pure functions only — never mutates the DB row.
Translation is idempotent: translating v1 → v1 returns the input unchanged.
"""

from __future__ import annotations


class InvalidLayoutSchema(ValueError):  # noqa: N818
    """Raised when translate_chart_layout cannot perform the requested translation."""


def translate_chart_layout(
    payload: dict[str, object],
    from_version: int,
    to_version: int,
) -> dict[str, object]:
    """Pure function. Never mutates. Returns translated dict at to_version.

    Idempotent (translating v1 -> v1 returns input unchanged).

    Args:
        payload: The raw JSONB payload stored in chart_layouts.
        from_version: Schema version stored alongside the row.
        to_version: Latest schema version (from app_config).

    Returns:
        A new dict at ``to_version``.

    Raises:
        InvalidLayoutSchema: If a downgrade is requested (from_version > to_version).
        NotImplementedError: If a forward-migration path is not yet implemented.
    """
    if from_version == to_version:
        return payload
    if from_version > to_version:
        raise InvalidLayoutSchema(f"cannot downgrade {from_version} -> {to_version}")
    # Future: chained migrations v1 -> v2 -> v3 ...
    # For now (Phase 9 ship): only v1 exists; this branch unreachable.
    raise NotImplementedError(f"translator v{from_version} -> v{to_version} not yet implemented")
