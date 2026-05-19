import pytest
from sqlalchemy import text

from alembic import command


@pytest.mark.migration
def test_0063_up_creates_advisor_decisions_table(migrated_db_sync):
    """bot_advisor_decisions exists after 0063."""
    with migrated_db_sync.connect() as conn:
        result = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'bot_advisor_decisions' ORDER BY ordinal_position"
            )
        )
        cols = [r[0] for r in result]
    assert "id" in cols
    assert "bot_id" in cols
    assert "verdict" in cols
    assert "account_gate_outcome" in cols
    assert "effective_mode" in cols
    assert "ai_completion_ts" in cols
    assert "ai_completion_request_id" in cols


@pytest.mark.migration
def test_0063_up_bots_advisor_config_column(migrated_db_sync):
    """bots.advisor_config JSONB column exists with NOT NULL DEFAULT."""
    with migrated_db_sync.connect() as conn:
        result = conn.execute(
            text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name='bots' AND column_name='advisor_config'"
            )
        )
        row = result.fetchone()
    assert row is not None
    assert "OFF" in (row[0] or "")


@pytest.mark.migration
def test_0063_up_bot_accounts_advisor_config_override_nullable(migrated_db_sync):
    """bot_accounts.advisor_config_override is JSONB, nullable, no default."""
    with migrated_db_sync.connect() as conn:
        result = conn.execute(
            text(
                "SELECT is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name='bot_accounts' AND column_name='advisor_config_override'"
            )
        )
        row = result.fetchone()
    assert row is not None
    assert row[0] == "YES"
    assert row[1] is None


@pytest.mark.migration
def test_0063_up_stop_reason_check_includes_advisor_auto_pause(migrated_db_sync):
    """bot_runs_stop_reason_check allows advisor_auto_pause."""
    with migrated_db_sync.connect() as conn:
        result = conn.execute(
            text(
                "SELECT check_clause FROM information_schema.check_constraints "
                "WHERE constraint_name = 'bot_runs_stop_reason_check'"
            )
        )
        row = result.fetchone()
    assert row is not None
    assert "advisor_auto_pause" in row[0]


@pytest.mark.migration
def test_0063_up_index_bot_ts_exists(migrated_db_sync):
    """idx_bot_advisor_decisions_bot_ts exists."""
    with migrated_db_sync.connect() as conn:
        result = conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename='bot_advisor_decisions' "
                "AND indexname='idx_bot_advisor_decisions_bot_ts'"
            )
        )
        assert result.fetchone() is not None


@pytest.mark.migration
def test_0063_up_no_fk_on_bot_run_id(migrated_db_sync):
    """bot_run_id has no FK constraint (hypertable retention)."""
    with migrated_db_sync.connect() as conn:
        result = conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.referential_constraints rc "
                "JOIN information_schema.key_column_usage kcu "
                "    ON rc.constraint_name = kcu.constraint_name "
                "WHERE kcu.table_name = 'bot_advisor_decisions' "
                "AND kcu.column_name = 'bot_run_id'"
            )
        )
        assert result.scalar() == 0


@pytest.mark.migration
def test_0063_up_no_fk_on_ai_completion_columns(migrated_db_sync):
    """ai_completion_ts + ai_completion_request_id have no FK (hypertable composite PK)."""
    with migrated_db_sync.connect() as conn:
        result = conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.referential_constraints rc "
                "JOIN information_schema.key_column_usage kcu "
                "    ON rc.constraint_name = kcu.constraint_name "
                "WHERE kcu.table_name = 'bot_advisor_decisions' "
                "AND kcu.column_name IN ('ai_completion_ts', 'ai_completion_request_id')"
            )
        )
        assert result.scalar() == 0


@pytest.mark.migration
def test_0063_up_bot_id_fk_is_restrict(migrated_db_sync):
    """bot_advisor_decisions.bot_id FK is ON DELETE RESTRICT."""
    with migrated_db_sync.connect() as conn:
        result = conn.execute(
            text(
                "SELECT rc.delete_rule FROM information_schema.referential_constraints rc "
                "JOIN information_schema.key_column_usage kcu "
                "    ON rc.constraint_name = kcu.constraint_name "
                "WHERE kcu.table_name = 'bot_advisor_decisions' "
                "AND kcu.column_name = 'bot_id'"
            )
        )
        row = result.fetchone()
    assert row is not None
    assert row[0] == "RESTRICT"


@pytest.mark.migration
def test_0063_up_account_gate_outcome_check_values(migrated_db_sync):
    """account_gate_outcome CHECK covers all expected values."""
    with migrated_db_sync.connect() as conn:
        result = conn.execute(
            text(
                "SELECT check_clause FROM information_schema.check_constraints "
                "WHERE constraint_name LIKE '%account_gate_outcome%'"
            )
        )
        row = result.fetchone()
    assert row is not None
    clause = row[0]
    for val in ("approved", "warned", "blocked", "not_evaluated", "error"):
        assert val in clause


@pytest.mark.migration
def test_0063_up_down_up_clean(alembic_config):
    """Migration is reversible: up → down → up without error."""
    command.upgrade(alembic_config, "0063")
    command.downgrade(alembic_config, "0062")
    command.upgrade(alembic_config, "0063")
