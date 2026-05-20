from __future__ import annotations

import multiprocessing
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def run_strategy_worker(
    source_code: str,
    event_queue: multiprocessing.Queue[Any],
    intent_queue: multiprocessing.Queue[Any],
) -> None:
    """Execute in a spawned child process: compile + instantiate + event loop."""
    from RestrictedPython import compile_restricted
    from RestrictedPython.Guards import safe_globals

    try:
        bytecode = compile_restricted(source_code, "<strategy>", "exec")
    except Exception as exc:
        logger.error("strategy_worker_compile_failed", exc=str(exc))
        return

    restricted_globals: dict[str, Any] = dict(safe_globals)
    restricted_globals["__builtins__"] = safe_globals.get("__builtins__", {})

    try:
        exec(bytecode, restricted_globals)
    except Exception as exc:
        logger.error("strategy_worker_exec_failed", exc=str(exc))
        return

    # Find the BaseStrategy subclass
    from app.bot.base import BaseStrategy

    strategy_cls = None
    for obj in restricted_globals.values():
        if isinstance(obj, type) and issubclass(obj, BaseStrategy) and obj is not BaseStrategy:
            strategy_cls = obj
            break

    if strategy_cls is None:
        logger.error("strategy_worker_no_strategy_class_found")
        return

    try:
        strategy = strategy_cls()
        strategy.on_start()
    except Exception as exc:
        logger.error("strategy_worker_on_start_failed", exc=str(exc))
        return

    import queue

    # Event loop: read BarEvents, emit OrderIntents
    while True:
        try:
            event = event_queue.get(timeout=1.0)
        except queue.Empty:
            continue
        except BrokenPipeError:
            break

        if event is None:  # sentinel: shutdown
            break

        try:
            strategy.on_bar(event)
        except Exception as exc:
            logger.warning("strategy_worker_on_bar_error", exc=str(exc))
