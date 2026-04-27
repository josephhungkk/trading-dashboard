"""UUIDv7 helper. Insert-ordered server-generated PKs (architect-review R2)."""

from uuid import UUID

import uuid_utils


def uuid7() -> UUID:
    """Return a new UUIDv7 as a stdlib UUID instance."""
    return UUID(bytes=uuid_utils.uuid7().bytes)
