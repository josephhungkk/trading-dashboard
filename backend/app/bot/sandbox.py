from __future__ import annotations

import importlib.abc
import importlib.util
import json
import resource
import subprocess
import sys
from typing import Any

import structlog

from app.core import metrics

logger = structlog.get_logger(__name__)

_DENYLIST = frozenset({"app.api", "app.services.orders_service"})


class DenylistFinder(importlib.abc.MetaPathFinder):
    """Blocks import of app.api.* and app.services.orders_service in child processes."""

    def __init__(self, bot_id: str) -> None:
        self._bot_id = bot_id

    def find_spec(
        self,
        fullname: str,
        path: Any,
        target: Any = None,
    ) -> None:
        for blocked in _DENYLIST:
            if fullname == blocked or fullname.startswith(blocked + "."):
                metrics.bot_forbidden_import_total.labels(
                    bot_id=self._bot_id, module=fullname
                ).inc()
                raise ImportError(f"strategy_imports_forbidden_module: {fullname!r} not accessible")
        return None


def install_denylist(bot_id: str) -> None:
    """Call once at child process startup before any strategy import."""
    finder = DenylistFinder(bot_id=bot_id)
    sys.meta_path.insert(0, finder)


_EXTRACTION_SCRIPT = """
import json, importlib.util, sys
path = sys.argv[1]
spec = importlib.util.spec_from_file_location("_strategy", path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
from app.bot.base import BaseStrategy
cls = next(
    (c for c in vars(m).values()
     if isinstance(c, type) and issubclass(c, BaseStrategy) and c is not BaseStrategy),
    None,
)
if cls is None:
    print("null")
else:
    print(json.dumps(cls.params_schema))
"""


def extract_params_schema(
    strategy_file: str,
    timeout: int = 5,
) -> dict[str, Any] | None:
    """Run sandboxed subprocess to extract params_schema class attribute.

    Returns the schema dict, None (no schema), or None on any error/timeout.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-c", _EXTRACTION_SCRIPT, strategy_file],
            capture_output=True,
            text=True,
            timeout=timeout,
            preexec_fn=_apply_resource_limits,
        )
        if result.returncode != 0:
            logger.warning(
                "params_schema_extraction_failed",
                strategy_file=strategy_file,
                stderr=result.stderr[:500],
            )
            metrics.bot_params_extraction_oom_total.inc()
            return None
        raw = result.stdout.strip()
        if raw == "null" or not raw:
            return None
        result_obj: dict[str, Any] | None = json.loads(raw)
        return result_obj
    except subprocess.TimeoutExpired:
        logger.warning("params_schema_extraction_timeout", strategy_file=strategy_file)
        metrics.bot_params_extraction_oom_total.inc()
        return None
    except Exception:
        logger.exception("params_schema_extraction_error", strategy_file=strategy_file)
        metrics.bot_params_extraction_oom_total.inc()
        return None


def _apply_resource_limits() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
