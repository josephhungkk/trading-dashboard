from __future__ import annotations

import multiprocessing
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.strategy_gen.metrics import strategy_gen_load_hash_mismatch_total
from app.services.strategy_gen.sandbox import SandboxValidator, compute_source_hash

logger = structlog.get_logger(__name__)

_MEMORY_MB_DEFAULT = 512
_CPU_SECONDS_DEFAULT = 30


class StrategyLoadError(Exception):
    pass


async def load_generated_strategy(
    strategy_id: int,
    db: AsyncSession,
    *,
    memory_mb: int = _MEMORY_MB_DEFAULT,
    cpu_seconds: int = _CPU_SECONDS_DEFAULT,
) -> tuple[str, multiprocessing.Queue[Any], multiprocessing.Queue[Any]]:
    """Validate, spawn, and return (bot_id_str, event_queue, intent_queue).

    Raises StrategyLoadError on:
    - strategy not found
    - sandbox_status != 'promoted'
    - source_hash mismatch
    - AST re-validation failure
    """
    result = await db.execute(
        text(
            "SELECT source_code, source_hash, sandbox_status"
            " FROM generated_strategies WHERE id = :id FOR UPDATE"
        ),
        {"id": strategy_id},
    )
    row = result.one_or_none()
    if row is None:
        raise StrategyLoadError(f"generated strategy {strategy_id} not found")

    source_code, stored_hash, sandbox_status = row
    if sandbox_status != "promoted":
        raise StrategyLoadError(
            f"generated strategy {strategy_id} sandbox_status={sandbox_status!r};"
            " must be 'promoted'"
        )

    # M4: re-hash on every load
    actual_hash = compute_source_hash(source_code)
    if actual_hash != stored_hash:
        strategy_gen_load_hash_mismatch_total.inc()
        await db.execute(
            text(
                "UPDATE generated_strategies"
                " SET sandbox_status='rejected', sandbox_error='tampered: hash mismatch'"
                " WHERE id = :id"
            ),
            {"id": strategy_id},
        )
        await db.commit()
        raise StrategyLoadError(
            f"generated strategy {strategy_id}: source_hash mismatch (tampered)"
        )

    # M4: re-run AST allowlist walk
    validator = SandboxValidator()
    validation = validator.validate_code(source_code)
    if not validation.ok:
        errors_str = "; ".join(validation.errors)
        await db.execute(
            text(
                "UPDATE generated_strategies"
                " SET sandbox_status='rejected', sandbox_error=:err"
                " WHERE id = :id"
            ),
            {"id": strategy_id, "err": f"re-validation failed: {errors_str}"},
        )
        await db.commit()
        raise StrategyLoadError(
            f"generated strategy {strategy_id}: AST re-validation failed: {errors_str}"
        )

    event_queue: multiprocessing.Queue[Any] = multiprocessing.Queue(maxsize=100)
    intent_queue: multiprocessing.Queue[Any] = multiprocessing.Queue(maxsize=100)

    ctx = multiprocessing.get_context("spawn")
    p = ctx.Process(
        target=_strategy_worker_entry,
        args=(source_code, event_queue, intent_queue, memory_mb, cpu_seconds),
        daemon=True,
    )
    p.start()
    logger.info(
        "generated_strategy_spawned",
        strategy_id=strategy_id,
        pid=p.pid,
    )
    return str(strategy_id), event_queue, intent_queue


def _strategy_worker_entry(
    source_code: str,
    event_queue: multiprocessing.Queue[Any],
    intent_queue: multiprocessing.Queue[Any],
    memory_mb: int,
    cpu_seconds: int,
) -> None:
    """Entry point for generated strategy child process."""
    import resource as _resource

    _resource.setrlimit(_resource.RLIMIT_AS, (memory_mb * 1024 * 1024, _resource.RLIM_INFINITY))
    _resource.setrlimit(_resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))

    from app.bot.strategy_worker import run_strategy_worker

    run_strategy_worker(source_code, event_queue, intent_queue)
