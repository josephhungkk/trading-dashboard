"""Unit-test conftest: add repo root to sys.path so sidecar packages are importable."""

from __future__ import annotations

import sys
from pathlib import Path

# backend/tests/unit/ → backend/tests/ → backend/ → repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
