"""Tests for ExerciseService — pending filter, idempotency, 409, rate limit."""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.no_db, pytest.mark.asyncio]


def _make_service(*, db=None, redis=None, broker_registry=None):
    from app.services.options.exercise_service import ExerciseService

    db = db or AsyncMock()
    redis = redis or AsyncMock()
    broker_registry = broker_registry or MagicMock()
    return ExerciseService(db=db, redis=redis, broker_registry=broker_registry)


@pytest.mark.asyncio
async def test_elect_idempotent_same_key_returns_existing() -> None:
    """Resending the same idempotency_key should return existing record without broker call."""
    svc = _make_service()
    ikey = uuid.uuid4()
    existing_row = {
        "id": str(uuid.uuid4()),
        "idempotency_key": str(ikey),
        "status": "submitted",
    }
    svc._find_by_idempotency_key = AsyncMock(return_value=existing_row)
    svc._submit_to_broker = AsyncMock()
    svc._check_rate_limit = MagicMock()

    result = await svc.elect(
        account_id=uuid.uuid4(),
        jwt_subject="user@example.com",
        instrument_id=42,
        action="EXERCISE",
        qty=Decimal("1"),
        idempotency_key=ikey,
    )

    svc._submit_to_broker.assert_not_called()
    assert result["idempotency_key"] == str(ikey)


@pytest.mark.asyncio
async def test_elect_duplicate_same_day_raises_409() -> None:
    """New idempotency_key for same (account, instrument, date) should raise 409."""
    from app.services.options.exercise_service import DuplicateElectionError

    svc = _make_service()
    svc._find_by_idempotency_key = AsyncMock(return_value=None)
    svc._check_rate_limit = MagicMock()
    svc._insert_election = AsyncMock(side_effect=DuplicateElectionError("duplicate"))

    with pytest.raises(DuplicateElectionError):
        await svc.elect(
            account_id=uuid.uuid4(),
            jwt_subject="user@example.com",
            instrument_id=42,
            action="EXERCISE",
            qty=Decimal("1"),
            idempotency_key=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_elect_rate_limit_enforced() -> None:
    """Exceeding 5/min rate limit should raise ExerciseRateLimitError."""
    from app.services.options.exercise_service import ExerciseRateLimitError

    svc = _make_service()
    svc._find_by_idempotency_key = AsyncMock(return_value=None)

    def raise_rate_limit(subject: str) -> None:
        raise ExerciseRateLimitError("rate limit exceeded")

    svc._check_rate_limit = raise_rate_limit

    with pytest.raises(ExerciseRateLimitError):
        await svc.elect(
            account_id=uuid.uuid4(),
            jwt_subject="user@example.com",
            instrument_id=42,
            action="EXERCISE",
            qty=Decimal("1"),
            idempotency_key=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_elect_new_key_submits_to_broker() -> None:
    """Fresh idempotency_key should insert and call broker."""
    svc = _make_service()
    ikey = uuid.uuid4()
    svc._find_by_idempotency_key = AsyncMock(return_value=None)
    svc._check_rate_limit = MagicMock()
    svc._insert_election = AsyncMock(
        return_value={"id": str(uuid.uuid4()), "idempotency_key": str(ikey), "status": "submitted"}
    )
    svc._submit_to_broker = AsyncMock(return_value={"broker_ref": "BR-001", "success": True})

    result = await svc.elect(
        account_id=uuid.uuid4(),
        jwt_subject="user@example.com",
        instrument_id=42,
        action="DO_NOT_EXERCISE",
        qty=Decimal("2"),
        idempotency_key=ikey,
    )

    svc._submit_to_broker.assert_called_once()
    assert result["status"] == "submitted"


@pytest.mark.asyncio
async def test_find_by_idempotency_key_returns_none_when_not_found() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=AsyncMock(fetchone=MagicMock(return_value=None)))
    svc = _make_service(db=db)
    result = await svc._find_by_idempotency_key(uuid.uuid4(), "user@example.com")
    assert result is None


@pytest.mark.asyncio
async def test_find_by_idempotency_key_returns_dict_when_found() -> None:
    ikey = uuid.uuid4()
    rid = uuid.uuid4()
    row = (rid, ikey, "submitted", "BR-001")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=AsyncMock(fetchone=MagicMock(return_value=row)))
    svc = _make_service(db=db)
    result = await svc._find_by_idempotency_key(ikey, "user@example.com")
    assert result["status"] == "submitted"
    assert result["broker_ref"] == "BR-001"


@pytest.mark.asyncio
async def test_insert_election_raises_duplicate_on_unique_violation() -> None:
    from app.services.options.exercise_service import DuplicateElectionError

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=Exception("exercise_elections_one_per_day unique"))
    svc = _make_service(db=db)
    with pytest.raises(DuplicateElectionError):
        await svc._insert_election(
            account_id=uuid.uuid4(),
            jwt_subject="u",
            instrument_id=1,
            action="EXERCISE",
            qty=Decimal("1"),
            idempotency_key=uuid.uuid4(),
        )
