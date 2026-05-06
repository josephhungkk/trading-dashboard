"""Phase 8a E4 — Weekly capability drift check.

Probes Schwab paper account with each (order_type, tif) combo from the
broker_order_capability seed marked supported=true. If any previously-supported
combo starts returning 4xx, the test fails — surfacing capability drift before
production traffic hits it.

For Phase 8a (pre-A5 flip), the supported set is empty for schwab, so this
test is intentionally skipped. The real assertions land after A5 ships.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.real_schwab


@pytest.mark.skip(
    reason="Schwab capability supported-set is empty pre-A5; placeholder for post-flip"
)
def test_real_schwab_capability_drift_placeholder() -> None:
    pass
