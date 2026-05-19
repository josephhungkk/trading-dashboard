from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
import structlog

from app.core import metrics
from app.services.scanner.evaluator import (
    EvaluatorBudgetError,
    EvaluatorParseError,
    ScannerEvaluator,
)
from app.services.scanner.indicators import (
    DAILY_INDICATORS,
    INTRADAY_SCALARS,
    IndicatorComputer,
)
from app.services.scanner.schemas import ScanConfig, UniverseConfig
from app.services.scanner.universe import UniverseResolver

log = structlog.get_logger()

CANDIDATE_COUNT_CAP = 500
_RUN_WALL_CLOCK_S = 60.0
_INSTRUMENT_TIMEOUT_S = 0.25


class ScannerService:
    def __init__(self, *, db_factory: Any, redis: Any, cfg: Any, ai_client: Any = None) -> None:
        self._db_factory = db_factory
        self._redis = redis
        self._cfg = cfg
        self._ai_client = ai_client
        self._evaluator = ScannerEvaluator()
        self._commentary_tasks: set[asyncio.Task[None]] = set()

    async def save_scan(self, config: ScanConfig) -> UUID:
        try:
            self._evaluator.parse(config.rule_expr)
        except EvaluatorParseError as exc:
            raise ValueError(f"rule_expr_parse_error: {exc}") from exc
        except EvaluatorBudgetError as exc:
            raise ValueError(f"rule_expr_budget_exceeded: {exc}") from exc

        async with self._db_factory() as db:
            row = await db.execute(
                sa.text(
                    """
                    INSERT INTO saved_scans
                        (name, universe_config, rule_expr, schedule, market_hours_gate,
                         exchange, llm_depth, alert_id, enabled)
                    VALUES
                        (:name, CAST(:uc AS jsonb), :rule, :sched, :mhg, :exch, :depth,
                         :alert, :enabled)
                    RETURNING id
                    """
                ),
                {
                    "name": config.name,
                    "uc": config.universe_config.model_dump_json(),
                    "rule": config.rule_expr,
                    "sched": config.schedule,
                    "mhg": config.market_hours_gate,
                    "exch": config.exchange,
                    "depth": config.llm_depth,
                    "alert": config.alert_id,
                    "enabled": config.enabled,
                },
            )
            scan_id: UUID = row.fetchone().id
            await db.commit()
        return scan_id

    async def run_scan(
        self,
        *,
        config: ScanConfig | None = None,
        scan_id: UUID | None = None,
        last_universe_snapshot: list[str] | None = None,
    ) -> UUID:
        if config is None and scan_id is None:
            raise ValueError("config or scan_id is required")

        async with self._db_factory() as db:
            if config is None and scan_id is not None:
                config = await self._load_scan_config(db, scan_id)

            if config is None:
                raise ValueError("config or scan_id is required")

            resolver = UniverseResolver(db=db, cfg=self._cfg, redis=self._redis)
            try:
                canonical_ids = await resolver.resolve(config.universe_config)
            except Exception:
                if last_universe_snapshot:
                    canonical_ids = last_universe_snapshot
                    metrics.scanner_universe_stale_total.labels(
                        scan_id=str(scan_id or "adhoc")
                    ).inc()
                else:
                    canonical_ids = []

            metrics.scanner_universe_size.labels(scan_id=str(scan_id or "adhoc")).set(
                len(canonical_ids)
            )

            ast = self._evaluator.parse(config.rule_expr)
            computer = IndicatorComputer(redis=self._redis, db=db)

            run_row = await db.execute(
                sa.text(
                    """
                    INSERT INTO scanner_runs
                        (scan_id, universe_snapshot, rule_expr, status)
                    VALUES (:sid, CAST(:snap AS jsonb), :rule, 'running')
                    RETURNING id
                    """
                ),
                {
                    "sid": scan_id,
                    "snap": json.dumps(canonical_ids),
                    "rule": config.rule_expr,
                },
            )
            run_id: UUID = run_row.fetchone().id
            await db.commit()

            candidates: list[dict[str, Any]] = []
            deadline = asyncio.get_event_loop().time() + _RUN_WALL_CLOCK_S

            for canonical_id in canonical_ids:
                if asyncio.get_event_loop().time() > deadline:
                    await self._fail_run(db, run_id, "wall_clock_exceeded")
                    return run_id

                try:
                    instrument_id = await self._resolve_instrument_id(db, canonical_id)
                    snapshot = await asyncio.wait_for(
                        self._build_snapshot(computer, canonical_id, instrument_id),
                        timeout=_INSTRUMENT_TIMEOUT_S,
                    )
                    matched = self._evaluator.evaluate(ast, snapshot)
                except TimeoutError:
                    metrics.scanner_eval_timeout_total.inc()
                    continue
                except Exception:
                    continue

                if matched:
                    candidates.append(
                        {
                            "instrument_id": instrument_id,
                            "canonical_id": canonical_id,
                            "indicator_snapshot": {
                                k: v for k, v in snapshot.items() if not callable(v)
                            },
                        }
                    )

            if len(candidates) > CANDIDATE_COUNT_CAP:
                candidates.sort(
                    key=lambda c: abs((c["indicator_snapshot"].get("rsi") or 50) - 50),
                    reverse=True,
                )
                candidates = candidates[:CANDIDATE_COUNT_CAP]
                metrics.scanner_candidate_cap_hit_total.labels(
                    scan_id=str(scan_id or "adhoc")
                ).inc()

            for c in candidates:
                await db.execute(
                    sa.text(
                        """
                        INSERT INTO scanner_candidates
                            (run_id, instrument_id, canonical_id, indicator_snapshot, llm_depth)
                        VALUES (:rid, :iid, :cid, CAST(:snap AS jsonb), :depth)
                        """
                    ),
                    {
                        "rid": run_id,
                        "iid": c["instrument_id"],
                        "cid": c["canonical_id"],
                        "snap": json.dumps(c["indicator_snapshot"], default=str),
                        "depth": config.llm_depth,
                    },
                )

            await db.execute(
                sa.text(
                    """
                    UPDATE scanner_runs
                    SET status = 'completed', candidate_count = :cnt, completed_at = now()
                    WHERE id = :rid
                    """
                ),
                {"cnt": len(candidates), "rid": run_id},
            )
            await db.commit()

        metrics.scanner_runs_total.labels(
            mode="saved" if scan_id else "adhoc", status="completed"
        ).inc()
        metrics.scanner_candidates_total.labels(scan_id=str(scan_id or "adhoc")).inc(
            len(candidates)
        )

        frame = json.dumps(
            {
                "v": 1,
                "type": "run_completed",
                "ts": datetime.now(UTC).isoformat(),
                "run_id": str(run_id),
                "scan_id": str(scan_id) if scan_id else None,
                "candidate_count": len(candidates),
            }
        )
        await self._redis.publish(f"scanner:run:{scan_id or 'adhoc'}", frame)

        if config.alert_id and candidates:
            await self._fire_alert(config.alert_id, run_id, candidates)

        if self._ai_client and candidates:
            task = asyncio.create_task(
                self._run_commentary(run_id, candidates, config.llm_depth, scan_id)
            )
            self._commentary_tasks.add(task)
            task.add_done_callback(self._commentary_tasks.discard)

        return run_id

    async def _build_snapshot(
        self,
        computer: IndicatorComputer,
        canonical_id: str,
        instrument_id: int | None,
    ) -> dict[str, Any]:
        snapshot: dict[str, Any] = {}
        all_inds = (
            list(DAILY_INDICATORS)
            + list(INTRADAY_SCALARS)
            + [
                "mcap",
                "pe",
                "eps_growth",
            ]
        )
        for name in all_inds:
            val = await computer.compute(
                name, {}, instrument_id=instrument_id, canonical_id=canonical_id
            )
            snapshot[name] = val

        def make_caller(ind_name: str) -> Any:
            def caller(*args: Any) -> Any:
                return snapshot.get(ind_name)

            return caller

        for name in DAILY_INDICATORS:
            snapshot[name] = make_caller(name)
        return snapshot

    async def _resolve_instrument_id(self, db: Any, canonical_id: str) -> int | None:
        row = await db.execute(
            sa.text("SELECT id FROM instruments WHERE canonical_id = :cid LIMIT 1"),
            {"cid": canonical_id},
        )
        r = row.fetchone()
        return r.id if r else None

    async def _fail_run(self, db: Any, run_id: UUID, error: str) -> None:
        await db.execute(
            sa.text(
                """
                UPDATE scanner_runs
                SET status = 'failed', error = :err, completed_at = now()
                WHERE id = :rid
                """
            ),
            {"err": error, "rid": run_id},
        )
        await db.commit()
        metrics.scanner_runs_total.labels(mode="saved", status="failed").inc()

    async def _load_scan_config(self, db: Any, scan_id: UUID) -> ScanConfig:
        row = await db.execute(sa.text("SELECT * FROM saved_scans WHERE id = :id"), {"id": scan_id})
        r = row.fetchone()
        if not r:
            raise ValueError(f"scan not found: {scan_id}")
        universe_config = r.universe_config
        if isinstance(universe_config, str):
            universe_config = json.loads(universe_config)
        return ScanConfig(
            name=r.name,
            universe_config=UniverseConfig(**universe_config),
            rule_expr=r.rule_expr,
            schedule=r.schedule,
            market_hours_gate=r.market_hours_gate,
            exchange=r.exchange,
            llm_depth=r.llm_depth,
            alert_id=r.alert_id,
            enabled=r.enabled,
        )

    async def _fire_alert(
        self, alert_id: int, run_id: UUID, candidates: list[dict[str, Any]]
    ) -> None:
        try:
            async with self._db_factory() as db:
                await db.execute(
                    sa.text(
                        """
                        INSERT INTO alert_fires (alert_id, jwt_subject, fired_at, verdict,
                                                fire_context)
                        VALUES (:aid, 'scanner', now(), 'FIRED', CAST(:ctx AS jsonb))
                        """
                    ),
                    {
                        "aid": alert_id,
                        "ctx": json.dumps(
                            {
                                "scanner_run_id": str(run_id),
                                "candidate_count": len(candidates),
                            }
                        ),
                    },
                )
                await db.commit()
            metrics.scanner_alert_fires_total.labels(scan_id=str(alert_id)).inc()
        except Exception:
            log.warning("scanner.alert_fire.error", alert_id=alert_id)

    async def _run_commentary(
        self,
        run_id: UUID,
        candidates: list[dict[str, Any]],
        depth: str,
        scan_id: UUID | None,
    ) -> None:
        from app.services.scanner.commentary import generate_commentary

        async with self._db_factory() as db:
            for c in candidates:
                text = await generate_commentary(
                    symbol=c["canonical_id"],
                    indicator_snapshot=c["indicator_snapshot"],
                    depth=depth,
                    ai_client=self._ai_client,
                )
                if text:
                    await db.execute(
                        sa.text(
                            """
                            UPDATE scanner_candidates
                            SET llm_commentary = :text
                            WHERE run_id = :rid AND canonical_id = :cid
                            """
                        ),
                        {"text": text, "rid": run_id, "cid": c["canonical_id"]},
                    )
                    await db.commit()
                    frame = json.dumps(
                        {
                            "v": 1,
                            "type": "commentary_ready",
                            "ts": datetime.now(UTC).isoformat(),
                            "canonical_id": c["canonical_id"],
                            "commentary": text,
                        }
                    )
                    await self._redis.publish(f"scanner:run:{scan_id or 'adhoc'}", frame)
