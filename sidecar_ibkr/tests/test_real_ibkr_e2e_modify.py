"""Real paper IBKR modify chain (5c E4).

Stub. Implementation: preview -> place -> preview-modify -> PUT modify ->
verify orders.status = modified -> cancel -> revert trade_enabled.
Runs only when CI_USE_REAL_IBKR=1 and CF_ACCESS_* secrets are set.
"""

from __future__ import annotations

import os

import pytest

# httpx is a backend dependency, not a sidecar runtime dep. The CI workflow
# (nightly-real-ibkr.yml) installs it. Local sidecar test runs skip the
# module entirely if httpx isn't present.
httpx = pytest.importorskip("httpx")

CF_BASE = "https://dashboard.kiusinghung.com"


def _headers() -> dict[str, str]:
    return {
        "CF-Access-Client-Id": os.environ["CF_ACCESS_CLIENT_ID"],
        "CF-Access-Client-Secret": os.environ["CF_ACCESS_CLIENT_SECRET"],
        "Content-Type": "application/json",
    }


@pytest.mark.real_ibkr
def test_real_paper_modify_chain() -> None:
    """Placeholder: filled by next canary task."""
    pass
