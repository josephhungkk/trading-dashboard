"""Integration tests for ConfigService: CRUD, typed accessors, secrets, cache coherence."""

import fakeredis.aioredis as fakeredis_async
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.crypto import get_fernet
from app.services.config import ConfigService, ConfigTypeError
from app.services.config_cache import ConfigCache


@pytest.fixture
async def engine():
    eng = create_async_engine(settings.database_url, echo=False)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def clean_tables(session_factory):
    db_url = settings.database_url
    if "10.10.0.2" in db_url:
        pytest.skip(
            "Refusing to truncate app_config/app_secrets against the prod DB "
            f"({db_url}). Set DATABASE_URL to test_postgres before running. "
            "See memory feedback_pytest_prod_db_wipe.md."
        )
    async with session_factory() as s:
        await s.execute(text("DELETE FROM app_config"))
        await s.execute(text("DELETE FROM app_secrets"))
        await s.commit()


@pytest.fixture
async def service(session_factory):
    r = fakeredis_async.FakeRedis(decode_responses=False)
    cache = ConfigCache(r, "config:invalidate", "config", ttl_seconds=60)
    secrets_cache = ConfigCache(r, "config:invalidate:secrets", "secret", ttl_seconds=60)
    fernet = get_fernet("test-secret-key", None)
    svc = ConfigService(
        session_factory=session_factory,
        cache=cache,
        secrets_cache=secrets_cache,
        fernet=fernet,
    )
    yield svc
    await r.aclose()


@pytest.mark.asyncio
async def test_set_get_str_roundtrip(service):
    await service.set("telegram", "bot_token", "12345:abc", value_type="str")
    assert await service.get("telegram", "bot_token") == "12345:abc"


@pytest.mark.asyncio
async def test_get_missing_returns_none(service):
    assert await service.get("absent", "key") is None


@pytest.mark.asyncio
async def test_get_missing_returns_default(service):
    assert await service.get("absent", "key", default="fallback") == "fallback"


@pytest.mark.asyncio
async def test_set_get_int(service):
    await service.set("ns", "n", 42, value_type="int")
    assert await service.get_int("ns", "n") == 42


@pytest.mark.asyncio
async def test_get_int_on_str_row_raises(service):
    await service.set("ns", "s", "hello", value_type="str")
    with pytest.raises(ConfigTypeError):
        await service.get_int("ns", "s")


@pytest.mark.asyncio
async def test_set_get_bool(service):
    await service.set("ns", "flag", True, value_type="bool")
    assert await service.get_bool("ns", "flag") is True


@pytest.mark.asyncio
async def test_set_get_json(service):
    await service.set("ns", "cfg", {"a": 1, "b": [2, 3]}, value_type="json")
    assert await service.get_json("ns", "cfg") == {"a": 1, "b": [2, 3]}


@pytest.mark.asyncio
async def test_json_stored_in_jsonb_column(service, session_factory):
    await service.set("ns", "c", {"x": 1}, value_type="json")
    async with session_factory() as s:
        row = (
            (
                await s.execute(
                    text(
                        "SELECT value, value_json, value_type FROM app_config "
                        "WHERE namespace='ns' AND key='c'"
                    )
                )
            )
            .mappings()
            .one()
        )
    assert row["value"] is None
    assert row["value_json"] == {"x": 1}
    assert row["value_type"] == "json"


@pytest.mark.asyncio
async def test_list_and_filter(service):
    await service.set("a", "k1", "v1")
    await service.set("a", "k2", "v2")
    await service.set("b", "k3", "v3")
    all_rows = await service.list()
    assert len(all_rows) == 3
    a_rows = await service.list(namespace="a")
    assert {r.key for r in a_rows} == {"k1", "k2"}


@pytest.mark.asyncio
async def test_delete(service):
    await service.set("n", "k", "v")
    assert await service.delete("n", "k") is True
    assert await service.delete("n", "k") is False
    assert await service.get("n", "k") is None


@pytest.mark.asyncio
async def test_set_is_upsert(service):
    await service.set("n", "k", "v1")
    await service.set("n", "k", "v2")
    assert await service.get("n", "k") == "v2"
    rows = await service.list(namespace="n")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_secret_roundtrip(service):
    await service.set_secret("schwab", "refresh_token", "top-secret", value_type="str")
    assert await service.reveal_secret("schwab", "refresh_token") == "top-secret"


@pytest.mark.asyncio
async def test_secret_stored_encrypted(service, session_factory):
    await service.set_secret("s", "k", "plaintext-here", value_type="str")
    async with session_factory() as s:
        row = (
            (
                await s.execute(
                    text("SELECT value_encrypted FROM app_secrets WHERE namespace='s' AND key='k'")
                )
            )
            .mappings()
            .one()
        )
    assert b"plaintext-here" not in row["value_encrypted"]
    assert len(row["value_encrypted"]) > 20


@pytest.mark.asyncio
async def test_list_secrets_has_no_plaintext(service):
    await service.set_secret("s", "k", "sensitive", value_type="str")
    meta = await service.list_secrets()
    assert len(meta) == 1
    assert not hasattr(meta[0], "value")
    assert not hasattr(meta[0], "value_encrypted")
    assert meta[0].namespace == "s"
    assert meta[0].key == "k"


@pytest.mark.asyncio
async def test_reveal_secret_int(service):
    await service.set_secret("s", "n", 12345, value_type="int")
    assert await service.reveal_secret_int("s", "n") == 12345


@pytest.mark.asyncio
async def test_reveal_secret_json(service):
    await service.set_secret("s", "map", {"key": "val"}, value_type="json")
    assert await service.reveal_secret_json("s", "map") == {"key": "val"}


@pytest.mark.asyncio
async def test_cache_hit_after_first_read(service):
    await service.set("ns", "k", "v1")
    _ = await service.get("ns", "k")
    async with service._session_factory() as s:
        await s.execute(
            text("UPDATE app_config SET value='v-direct' WHERE namespace='ns' AND key='k'")
        )
        await s.commit()
    assert await service.get("ns", "k") == "v1"


@pytest.mark.asyncio
async def test_cache_evict_then_db_update_returns_fresh(service):
    """Confirms cache eviction (simulating a received pub/sub invalidate) causes
    the next read to re-fetch the DB value. Pub/sub wire fidelity itself is
    covered by the opt-in real-Redis test (Task 18)."""
    await service.set("ns", "k", "v1")
    assert await service.get("ns", "k") == "v1"
    service._cache.pop(("ns", "k"))
    async with service._session_factory() as s:
        await s.execute(text("UPDATE app_config SET value='v2' WHERE namespace='ns' AND key='k'"))
        await s.commit()
    assert await service.get("ns", "k") == "v2"


@pytest.mark.asyncio
async def test_multifernet_prev_key_hit_increments_metric(session_factory):
    """Row encrypted under the previous key is decryptable via MultiFernet
    fallback, and the PREV-hit metric is incremented exactly once per reveal."""
    from cryptography.fernet import MultiFernet

    from app.core import metrics
    from app.core.crypto import get_fernet

    prev_fernet = get_fernet("rotated-old-key", None)
    current_fernet = get_fernet("rotated-new-key", "rotated-old-key")
    assert isinstance(current_fernet, MultiFernet)

    r = fakeredis_async.FakeRedis(decode_responses=False)
    cache = ConfigCache(r, "config:invalidate", "config", ttl_seconds=60)
    secrets_cache = ConfigCache(r, "config:invalidate:secrets", "secret", ttl_seconds=60)

    writer = ConfigService(
        session_factory=session_factory,
        cache=cache,
        secrets_cache=secrets_cache,
        fernet=prev_fernet,
    )
    await writer.set_secret("rot", "k", "ancient-value", value_type="str")

    reader = ConfigService(
        session_factory=session_factory,
        cache=cache,
        secrets_cache=secrets_cache,
        fernet=current_fernet,
    )
    before = metrics.fernet_prev_key_hits_total._value.get()  # type: ignore[attr-defined]
    plaintext = await reader.reveal_secret("rot", "k")
    after = metrics.fernet_prev_key_hits_total._value.get()  # type: ignore[attr-defined]

    assert plaintext == "ancient-value"
    assert after == before + 1
    await r.aclose()


@pytest.mark.asyncio
async def test_multifernet_primary_hit_does_not_increment_metric(session_factory):
    """Row freshly encrypted under the current primary key must NOT increment
    the PREV-hit metric."""
    from app.core import metrics
    from app.core.crypto import get_fernet

    current_fernet = get_fernet("rotated-new-key", "rotated-old-key")
    r = fakeredis_async.FakeRedis(decode_responses=False)
    cache = ConfigCache(r, "config:invalidate", "config", ttl_seconds=60)
    secrets_cache = ConfigCache(r, "config:invalidate:secrets", "secret", ttl_seconds=60)
    svc = ConfigService(
        session_factory=session_factory,
        cache=cache,
        secrets_cache=secrets_cache,
        fernet=current_fernet,
    )
    await svc.set_secret("rot", "k2", "fresh-value", value_type="str")

    before = metrics.fernet_prev_key_hits_total._value.get()  # type: ignore[attr-defined]
    plaintext = await svc.reveal_secret("rot", "k2")
    after = metrics.fernet_prev_key_hits_total._value.get()  # type: ignore[attr-defined]

    assert plaintext == "fresh-value"
    assert after == before
    await r.aclose()
