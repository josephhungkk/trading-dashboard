from __future__ import annotations

import datetime as dt
from decimal import Decimal

import fakeredis.aioredis as fakeredis_async
import pytest

from bar_aggregator.app.wal import GapDetectedError, WAL, WALTickRecord

pytestmark = [pytest.mark.unit]


@pytest.mark.asyncio
async def test_append_writes_xadd_entry() -> None:
    redis = fakeredis_async.FakeRedis(decode_responses=True)
    wal = WAL(redis=redis, shard=0, flush_interval_ms=1000)
    record = _tick_record(instrument_id=42)

    entry_id = await wal.append(record)

    entries = await redis.xrange("wal:bar_aggregator:0:42")
    assert len(entries) == 1
    assert entries[0][0] == entry_id
    assert entries[0][1] == {
        "kind": "tick",
        "instrument_id": "42",
        "source": "schwab",
        "ts": "2026-05-07T15:30:00+00:00",
        "price": "100.25",
        "volume": "3",
        "bid": "",
        "ask": "",
    }


@pytest.mark.asyncio
async def test_ack_flushed_xtrim_minid_only() -> None:
    redis = fakeredis_async.FakeRedis(decode_responses=True)
    wal = WAL(redis=redis, shard=0, flush_interval_ms=1000)
    entry_ids = [
        await wal.append(_tick_record(instrument_id=42, price=Decimal(index)))
        for index in range(5)
    ]

    await wal.ack_flushed(instrument_id=42, last_entry_id=entry_ids[2])

    entries = await redis.xrange("wal:bar_aggregator:0:42")
    assert [entry_id for entry_id, _fields in entries] == entry_ids[2:]


@pytest.mark.asyncio
async def test_replay_emits_in_xadd_order() -> None:
    redis = fakeredis_async.FakeRedis(decode_responses=True)
    wal = WAL(redis=redis, shard=0, flush_interval_ms=1000)
    records = [
        _tick_record(instrument_id=42, price=Decimal(index))
        for index in range(3)
    ]
    entry_ids = [await wal.append(record) for record in records]

    replayed = [record async for record in wal.replay(instrument_id=42)]

    assert [record.entry_id for record in replayed] == entry_ids
    assert [record.price for record in replayed] == [Decimal("0"), Decimal("1"), Decimal("2")]


@pytest.mark.asyncio
async def test_replay_raises_gap_detected_on_lag() -> None:
    redis = fakeredis_async.FakeRedis(decode_responses=True)
    wal = WAL(redis=redis, shard=0, flush_interval_ms=1000)
    t0 = dt.datetime(2026, 5, 7, 15, 30, tzinfo=dt.UTC)
    await wal.set_last_flushed(instrument_id=42, ts=t0)
    await wal.append(_tick_record(instrument_id=42, ts=t0 + dt.timedelta(seconds=2.5)))

    with pytest.raises(GapDetectedError):
        _ = [record async for record in wal.replay(instrument_id=42)]


@pytest.mark.asyncio
async def test_replay_no_instrument_scans_all_shards_keys() -> None:
    redis = fakeredis_async.FakeRedis(decode_responses=True)
    wal = WAL(redis=redis, shard=0, flush_interval_ms=1000)
    first_id = await wal.append(_tick_record(instrument_id=42))
    second_id = await wal.append(_quote_record(instrument_id=43))

    replayed = [record async for record in wal.replay()]

    assert {(record.instrument_id, record.entry_id) for record in replayed} == {
        (42, first_id),
        (43, second_id),
    }


def _tick_record(
    *,
    instrument_id: int,
    ts: dt.datetime | None = None,
    price: Decimal = Decimal("100.25"),
) -> WALTickRecord:
    return WALTickRecord(
        entry_id="",
        instrument_id=instrument_id,
        source="schwab",
        ts=ts or dt.datetime(2026, 5, 7, 15, 30, tzinfo=dt.UTC),
        price=price,
        volume=Decimal("3"),
        bid=None,
        ask=None,
        kind="tick",
    )


def _quote_record(*, instrument_id: int) -> WALTickRecord:
    return WALTickRecord(
        entry_id="",
        instrument_id=instrument_id,
        source="ibkr",
        ts=dt.datetime(2026, 5, 7, 15, 30, 1, tzinfo=dt.UTC),
        price=None,
        volume=None,
        bid=Decimal("100.10"),
        ask=Decimal("100.20"),
        kind="quote",
    )
