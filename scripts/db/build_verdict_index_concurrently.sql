-- Phase 10a.5 — non-blocking build of risk_decisions verdict index.
--
-- Alembic 0037b creates this index with a plain CREATE INDEX (locks the
-- table for the duration of the build). For production tables that have
-- accumulated many audit rows, prefer running this script manually before
-- the deploy that contains 0037b. CONCURRENTLY cannot run inside a
-- transaction, so it cannot live in the migration itself.
--
-- Usage (psql, on the prod DB):
--   \i scripts/db/build_verdict_index_concurrently.sql
--
-- The IF NOT EXISTS guard makes this safe to run after 0037b has already
-- created the index — it becomes a no-op.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_risk_decisions_verdict_time
  ON risk_decisions (verdict, evaluated_at DESC);
