# Phase 2 — Auth + DB-backed config service — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move runtime configuration out of `.env` into two Postgres tables (`app_config`, `app_secrets`), with a `ConfigService` layer caching in-memory and invalidating across workers via Redis pub/sub. Protect `/api/admin/*` with CF Access JWT verification. Ship `v0.2.0`.

**Architecture:** `ConfigService` (services/config.py) sits between FastAPI routes and Postgres. Each worker has a per-instance dict cache (5-min TTL); writes publish `ns|key` on Redis `config:invalidate`; subscribers evict. CF Access JWT verified via `PyJWKClient` (RS256, kid-miss force-refresh, accepts `email` or `common_name` claim). Fernet key from HKDF-SHA256 of `APP_SECRET_KEY`; `MultiFernet([primary, prev])` supports rolling rotation via optional `APP_SECRET_KEY_PREV`. Dev-mode bypass double-gated (`APP_ENV=dev` AND client IP ∈ `TRUSTED_DEV_NETS`).

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2 async, asyncpg, Pydantic v2, Postgres 18 (JSONB column), Redis 7 (pub/sub), `cryptography` (Fernet + HKDF + MultiFernet), `pyjwt[crypto]` (PyJWKClient), `prometheus-client`.

**Spec:** `docs/superpowers/specs/2026-04-22-phase2-auth-config-design.md` (architect-reviewed; 14 findings applied).

**Estimated:** 25 tasks across 11 chunks.

---

## Chunk A — Dependencies + bootstrap settings + entrypoint

### Task 1: Add new Python dependencies via `uv`

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`

- [ ] **Step 1.1: Add runtime deps**

```bash
cd /mnt/c/dashboard/backend
uv add cryptography "redis[hiredis]" "pyjwt[crypto]" prometheus-client
```

Expected: each line prints `Added <pkg> vX.Y.Z`. `pyproject.toml` `[project.dependencies]` gains 4 entries. `uv.lock` regenerated.

- [ ] **Step 1.2: Add test-only dep**

```bash
cd /mnt/c/dashboard/backend
uv add --dev "fakeredis[asyncio]"
```

Expected: `Added fakeredis vX.Y.Z`. `pyproject.toml` `[dependency-groups.dev]` gains `fakeredis[asyncio]`.

- [ ] **Step 1.3: Sync + verify**

```bash
cd /mnt/c/dashboard/backend
uv sync --frozen
uv run python -c "import cryptography, jwt, redis, prometheus_client; print('deps ok')"
```

Expected output: `deps ok`.

- [ ] **Step 1.4: Commit**

```bash
cd /mnt/c/dashboard
git add backend/pyproject.toml backend/uv.lock
git commit -m "feat(backend): add cryptography, redis, pyjwt, prometheus-client deps"
```

---

### Task 2: Extend `Settings` with 4 new bootstrap env vars

**Files:**
- Modify: `backend/app/core/config.py`

- [ ] **Step 2.1: Read current file**

Read `backend/app/core/config.py` so the Edit tool has it in context.

- [ ] **Step 2.2: Replace the Settings class body**

Replace the entire `class Settings(BaseSettings): ...` block with:

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = Field(default="dev", alias="APP_ENV")
    secret_key: str = Field(alias="APP_SECRET_KEY")
    secret_key_prev: str | None = Field(default=None, alias="APP_SECRET_KEY_PREV")
    cors_origins: list[str] = Field(default_factory=list, alias="APP_CORS_ORIGINS")
    database_url: str = Field(alias="DATABASE_URL")
    postgres_pool_size: int = Field(default=5, alias="POSTGRES_POOL_SIZE")
    postgres_max_overflow: int = Field(default=10, alias="POSTGRES_MAX_OVERFLOW")
    redis_password: str = Field(alias="REDIS_PASSWORD")
    redis_url: str = Field(alias="REDIS_URL")

    # Phase 2 — CF Access JWT verification
    cf_access_team_domain: str = Field(default="", alias="CF_ACCESS_TEAM_DOMAIN")
    cf_access_audience: str = Field(default="", alias="CF_ACCESS_AUDIENCE")

    # Phase 2 — dev-mode bypass. BOTH env=dev AND IP-match required.
    # Empty list (default) = bypass never fires.
    trusted_dev_nets: list[str] = Field(default_factory=list, alias="TRUSTED_DEV_NETS")
```

Leave the `settings = Settings()` line at the bottom intact.

- [ ] **Step 2.3: Verify settings loads**

```bash
cd /mnt/c/dashboard/backend
uv run python -c "from app.core.config import settings; print(settings.env, settings.cf_access_team_domain)"
```

Expected: `dev` then empty string (defaults when keys absent from `.env`). No errors.

- [ ] **Step 2.4: Commit**

```bash
cd /mnt/c/dashboard
git add backend/app/core/config.py
git commit -m "feat(core): add cf-access + dev-bypass + prev-key settings"
```

---

### Task 3: Create `scripts/entrypoint.sh` + update Dockerfile to ENTRYPOINT+CMD

**Files:**
- Create: `backend/scripts/entrypoint.sh`
- Modify: `backend/Dockerfile`

- [ ] **Step 3.1: Create entrypoint.sh**

`mkdir -p /mnt/c/dashboard/backend/scripts`, then write `backend/scripts/entrypoint.sh`:

```sh
#!/bin/sh
# Container entrypoint: migrate, then exec whatever CMD / compose command was passed.
# Alembic uses pg_advisory_lock internally so multi-worker starts serialize.
set -eu

echo "==> alembic upgrade head"
/app/.venv/bin/alembic upgrade head

echo "==> exec: $*"
exec "$@"
```

- [ ] **Step 3.2: chmod + syntax check**

```bash
cd /mnt/c/dashboard
chmod +x backend/scripts/entrypoint.sh
sh -n backend/scripts/entrypoint.sh
```

Expected: no output.

- [ ] **Step 3.3: Read current Dockerfile then update**

Read `backend/Dockerfile`. Replace the last line (currently `CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`) with:

```dockerfile
COPY scripts/ ./scripts/
RUN chmod +x ./scripts/entrypoint.sh

ENTRYPOINT ["./scripts/entrypoint.sh"]
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

The ENTRYPOINT + CMD split means prod compose's `command:` override (which replaces only CMD) still runs through our alembic step.

- [ ] **Step 3.4: Smoke-build the image**

```bash
cd /mnt/c/dashboard
docker compose build backend 2>&1 | tail -15
```

Expected: build succeeds.

- [ ] **Step 3.5: Commit**

```bash
cd /mnt/c/dashboard
git add backend/scripts/entrypoint.sh backend/Dockerfile
git commit -m "feat(backend): entrypoint.sh runs alembic upgrade before uvicorn"
```

---

## Chunk B — SQLAlchemy models + Alembic migration 0001

### Task 4: SQLAlchemy declarative Base + `AppConfig` / `AppSecret` models

**Files:**
- Create: `backend/app/models/base.py`
- Create: `backend/app/models/config.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/alembic/env.py`

- [ ] **Step 4.1: Write `app/models/base.py`**

```python
"""Declarative Base for all ORM models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared base for all SQLAlchemy models in this project."""
```

- [ ] **Step 4.2: Write `app/models/config.py`**

```python
"""SQLAlchemy models for app_config and app_secrets (Phase 2)."""

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, LargeBinary, PrimaryKeyConstraint, String, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AppConfig(Base):
    __tablename__ = "app_config"

    namespace: Mapped[str] = mapped_column(String, nullable=False)
    key: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[str | None] = mapped_column(String, nullable=True)
    value_json: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)
    value_type: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        PrimaryKeyConstraint("namespace", "key"),
        CheckConstraint(
            "value_type IN ('str','int','bool','json')",
            name="app_config_value_type_check",
        ),
        CheckConstraint(
            "(value_type = 'json' AND value_json IS NOT NULL AND value IS NULL)"
            " OR "
            "(value_type <> 'json' AND value IS NOT NULL AND value_json IS NULL)",
            name="app_config_value_exclusive",
        ),
        Index("ix_app_config_updated_at", "updated_at", postgresql_using="btree"),
    )


class AppSecret(Base):
    __tablename__ = "app_secrets"

    namespace: Mapped[str] = mapped_column(String, nullable=False)
    key: Mapped[str] = mapped_column(String, nullable=False)
    value_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    value_type: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        PrimaryKeyConstraint("namespace", "key"),
        CheckConstraint(
            "value_type IN ('str','int','bool','json')",
            name="app_secrets_value_type_check",
        ),
        Index("ix_app_secrets_updated_at", "updated_at", postgresql_using="btree"),
    )
```

- [ ] **Step 4.3: Replace `app/models/__init__.py`**

```python
"""ORM models."""

from app.models.base import Base
from app.models.config import AppConfig, AppSecret

__all__ = ["Base", "AppConfig", "AppSecret"]
```

- [ ] **Step 4.4: Wire `target_metadata` in alembic env**

Read `backend/alembic/env.py`. Replace:

```python
# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
# No ORM metadata yet — first models land in Phase 2 (app_config, app_secrets).
target_metadata = None
```

with:

```python
# Import models so their tables are registered with Base.metadata.
from app.models import Base  # noqa: E402

target_metadata = Base.metadata
```

- [ ] **Step 4.5: Verify models import cleanly**

```bash
cd /mnt/c/dashboard/backend
uv run python -c "from app.models import Base, AppConfig, AppSecret; print(sorted(Base.metadata.tables.keys()))"
```

Expected: `['app_config', 'app_secrets']`.

- [ ] **Step 4.6: Commit**

```bash
cd /mnt/c/dashboard
git add backend/app/models/ backend/alembic/env.py
git commit -m "feat(models): app_config + app_secrets sqlalchemy models with checks"
```

---

### Task 5: Alembic migration 0001 (hand-written, not autogenerated)

**Files:**
- Create: `backend/alembic/versions/0001_app_config_and_secrets.py`

- [ ] **Step 5.1: Confirm PG18 is reachable from WSL**

```bash
timeout 5 bash -c '</dev/tcp/10.10.0.2/5432' && echo "pg reachable" || echo "pg unreachable — start Windows pg service"
```

Expected: `pg reachable`. If unreachable, start the `postgresql-x64-18` Windows service before continuing.

- [ ] **Step 5.2: Write migration file directly**

Create `backend/alembic/versions/0001_app_config_and_secrets.py`:

```python
"""app_config and app_secrets tables.

Revision ID: 0001
Revises:
Create Date: 2026-04-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_config",
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.String(), nullable=True),
        sa.Column("value_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("value_type", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("namespace", "key"),
        sa.CheckConstraint(
            "value_type IN ('str','int','bool','json')",
            name="app_config_value_type_check",
        ),
        sa.CheckConstraint(
            "(value_type = 'json' AND value_json IS NOT NULL AND value IS NULL)"
            " OR "
            "(value_type <> 'json' AND value IS NOT NULL AND value_json IS NULL)",
            name="app_config_value_exclusive",
        ),
    )
    op.create_index(
        "ix_app_config_updated_at",
        "app_config",
        [sa.text("updated_at DESC")],
    )

    op.create_table(
        "app_secrets",
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("value_type", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("namespace", "key"),
        sa.CheckConstraint(
            "value_type IN ('str','int','bool','json')",
            name="app_secrets_value_type_check",
        ),
    )
    op.create_index(
        "ix_app_secrets_updated_at",
        "app_secrets",
        [sa.text("updated_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_app_secrets_updated_at", table_name="app_secrets")
    op.drop_table("app_secrets")
    op.drop_index("ix_app_config_updated_at", table_name="app_config")
    op.drop_table("app_config")
```

- [ ] **Step 5.3: Upgrade → tables exist**

```bash
cd /mnt/c/dashboard/backend
uv run alembic upgrade head
```

Expected last line: `INFO  [alembic.runtime.migration] Running upgrade  -> 0001, app_config and app_secrets`.

- [ ] **Step 5.4: Verify via asyncpg one-liner**

```bash
cd /mnt/c/dashboard/backend
PW=$(grep -E '^DATABASE_URL=' /mnt/c/dashboard/.env | sed -E 's|.*://[^:]+:([^@]+)@.*|\1|') PW="$PW" uv run python <<'PYEOF'
import os, asyncio, asyncpg
async def main():
    conn = await asyncpg.connect(f"postgresql://trader:{os.environ['PW']}@10.10.0.2:5432/dashboard")
    rows = await conn.fetch(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public' "
        "AND table_name IN ('app_config','app_secrets') ORDER BY table_name;"
    )
    print([r['table_name'] for r in rows])
    await conn.close()
asyncio.run(main())
PYEOF
```

Expected: `['app_config', 'app_secrets']`.

- [ ] **Step 5.5: Downgrade → tables gone; upgrade again → back**

```bash
cd /mnt/c/dashboard/backend
uv run alembic downgrade base
uv run alembic upgrade head
```

Expected: clean `downgrade ... -> base` then clean `upgrade  -> 0001`.

- [ ] **Step 5.6: Commit**

```bash
cd /mnt/c/dashboard
git add backend/alembic/versions/0001_app_config_and_secrets.py
git commit -m "feat(db): alembic 0001 — app_config + app_secrets tables with checks"
```

---

## Chunk C — Fernet crypto with PREV-key rotation

### Task 6: `core/crypto.py` + `test_crypto.py` (TDD)

**Files:**
- Create: `backend/tests/test_crypto.py`
- Create: `backend/app/core/crypto.py`

- [ ] **Step 6.1: Write failing tests first**

Create `backend/tests/test_crypto.py`:

```python
"""Crypto primitives for Fernet-encrypted secrets."""

import pytest
from cryptography.fernet import InvalidToken

from app.core.crypto import derive_fernet_key, get_fernet, encrypt_bytes, decrypt_bytes


def test_derive_is_deterministic():
    k1 = derive_fernet_key("my-secret-key-xyz")
    k2 = derive_fernet_key("my-secret-key-xyz")
    assert k1 == k2
    assert len(k1) == 44  # base64-encoded 32 bytes = 44 chars


def test_derive_differs_on_input_change():
    assert derive_fernet_key("a") != derive_fernet_key("b")


def test_encrypt_decrypt_roundtrip():
    fernet = get_fernet("test-key-123", None)
    plaintext = b"hello world"
    ct = encrypt_bytes(fernet, plaintext)
    assert ct != plaintext
    assert decrypt_bytes(fernet, ct) == plaintext


def test_encrypt_decrypt_empty_bytes():
    fernet = get_fernet("k", None)
    assert decrypt_bytes(fernet, encrypt_bytes(fernet, b"")) == b""


def test_encrypt_decrypt_large_blob():
    fernet = get_fernet("k", None)
    blob = b"x" * (1024 * 1024)  # 1 MB
    assert decrypt_bytes(fernet, encrypt_bytes(fernet, blob)) == blob


def test_decrypt_with_wrong_key_raises():
    f1 = get_fernet("key-a", None)
    f2 = get_fernet("key-b", None)
    ct = encrypt_bytes(f1, b"secret")
    with pytest.raises(InvalidToken):
        decrypt_bytes(f2, ct)


def test_multifernet_prev_key_fallback():
    """Data encrypted with the PREV key still decrypts when PRIMARY is new."""
    old_fernet = get_fernet("old-key", None)
    ct_old = encrypt_bytes(old_fernet, b"stored-under-old-key")

    rotated_fernet = get_fernet("new-key", "old-key")  # primary, prev
    assert decrypt_bytes(rotated_fernet, ct_old) == b"stored-under-old-key"

    ct_new = encrypt_bytes(rotated_fernet, b"fresh")
    assert decrypt_bytes(rotated_fernet, ct_new) == b"fresh"

    primary_only = get_fernet("new-key", None)
    with pytest.raises(InvalidToken):
        decrypt_bytes(primary_only, ct_old)
    assert decrypt_bytes(primary_only, ct_new) == b"fresh"


def test_get_fernet_none_prev_equals_single_fernet():
    """Passing None for prev should give a functional Fernet (not MultiFernet with empty)."""
    f = get_fernet("k", None)
    ct = encrypt_bytes(f, b"data")
    assert decrypt_bytes(f, ct) == b"data"
```

- [ ] **Step 6.2: Run test — expect import failure**

```bash
cd /mnt/c/dashboard/backend
uv run pytest tests/test_crypto.py -v 2>&1 | tail -5
```

Expected: `ModuleNotFoundError: No module named 'app.core.crypto'`.

- [ ] **Step 6.3: Write `app/core/crypto.py`**

```python
"""Fernet-based encryption for app_secrets.

Key is derived deterministically from APP_SECRET_KEY via HKDF-SHA256.
MultiFernet([primary, prev]) supports rolling rotation: new writes encrypt
with primary; reads fall back to prev if set.
"""

import base64

from cryptography.fernet import Fernet, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_HKDF_SALT = b"dashboard.v1"
_HKDF_INFO = b"app_secrets.fernet.v1"


def derive_fernet_key(app_secret_key: str) -> bytes:
    """Derive a 44-byte base64-encoded Fernet key from APP_SECRET_KEY via HKDF-SHA256.

    Deterministic for a given input; changes in salt/info produce a different key.
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    )
    raw = hkdf.derive(app_secret_key.encode())
    return base64.urlsafe_b64encode(raw)


def get_fernet(app_secret_key: str, prev_key: str | None) -> Fernet | MultiFernet:
    """Return a Fernet (prev=None) or MultiFernet([primary, prev])."""
    primary = Fernet(derive_fernet_key(app_secret_key))
    if prev_key is None:
        return primary
    prev = Fernet(derive_fernet_key(prev_key))
    # MultiFernet: encrypt uses [0]; decrypt tries each in order.
    return MultiFernet([primary, prev])


def encrypt_bytes(fernet: Fernet | MultiFernet, plaintext: bytes) -> bytes:
    return fernet.encrypt(plaintext)


def decrypt_bytes(fernet: Fernet | MultiFernet, ciphertext: bytes) -> bytes:
    return fernet.decrypt(ciphertext)
```

- [ ] **Step 6.4: Run test — expect pass**

```bash
cd /mnt/c/dashboard/backend
uv run pytest tests/test_crypto.py -v 2>&1 | tail -15
```

Expected: `8 passed`.

- [ ] **Step 6.5: Coverage check (must be 100% for crypto)**

```bash
cd /mnt/c/dashboard/backend
uv run pytest tests/test_crypto.py --cov=app.core.crypto --cov-report=term-missing 2>&1 | tail -10
```

Expected: `app/core/crypto.py ... 100%` coverage.

- [ ] **Step 6.6: Commit**

```bash
cd /mnt/c/dashboard
git add backend/app/core/crypto.py backend/tests/test_crypto.py
git commit -m "feat(crypto): fernet + hkdf + multifernet prev-key rotation"
```

---

## Chunk D — CF Access JWT verification

### Task 7: `core/cf_access.py` — `CFAccessVerifier` + `AdminIdentity` + dev-bypass

**Files:**
- Create: `backend/app/core/cf_access.py`
- Create: `backend/tests/test_cf_access.py`

- [ ] **Step 7.1: Write tests first**

Create `backend/tests/test_cf_access.py`:

```python
"""CF Access JWT verification tests."""

import time
from unittest.mock import Mock

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.cf_access import (
    AdminIdentity,
    CFAccessVerifier,
    NoIdentityClaimError,
    client_ip_in_trusted_nets,
)


@pytest.fixture(scope="module")
def rsa_keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return priv, priv.public_key()


@pytest.fixture(scope="module")
def rsa_private_pem(rsa_keypair):
    priv, _ = rsa_keypair
    return priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@pytest.fixture(scope="module")
def rsa_public_pem(rsa_keypair):
    _, pub = rsa_keypair
    return pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _issue_jwt(priv_pem: bytes, claims: dict, kid: str = "test-kid") -> str:
    return jwt.encode(claims, priv_pem, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def verifier(rsa_public_pem):
    """Verifier whose JWKS always returns our test public key."""
    v = CFAccessVerifier(
        team_domain="test.cloudflareaccess.com",
        audience="test-aud",
        trusted_dev_nets=[],
        env="prod",
    )
    mock_key = Mock()
    mock_key.key = rsa_public_pem
    v._jwks_client = Mock()
    v._jwks_client.get_signing_key_from_jwt = Mock(return_value=mock_key)
    return v


def test_valid_jwt_with_email_claim(verifier, rsa_private_pem):
    now = int(time.time())
    token = _issue_jwt(
        rsa_private_pem,
        {
            "email": "alice@example.com",
            "iss": "https://test.cloudflareaccess.com",
            "aud": "test-aud",
            "iat": now,
            "exp": now + 300,
        },
    )
    identity = verifier.verify(token, client_ip="1.2.3.4")
    assert isinstance(identity, AdminIdentity)
    assert identity.email == "alice@example.com"
    assert identity.kind == "user"


def test_valid_jwt_with_common_name_claim(verifier, rsa_private_pem):
    now = int(time.time())
    token = _issue_jwt(
        rsa_private_pem,
        {
            "common_name": "dashboard-ci-smoke",
            "iss": "https://test.cloudflareaccess.com",
            "aud": "test-aud",
            "iat": now,
            "exp": now + 300,
        },
    )
    identity = verifier.verify(token, client_ip="1.2.3.4")
    assert identity.email == "dashboard-ci-smoke"
    assert identity.kind == "service_token"


def test_jwt_with_neither_claim_raises(verifier, rsa_private_pem):
    now = int(time.time())
    token = _issue_jwt(
        rsa_private_pem,
        {
            "iss": "https://test.cloudflareaccess.com",
            "aud": "test-aud",
            "iat": now,
            "exp": now + 300,
        },
    )
    with pytest.raises(NoIdentityClaimError):
        verifier.verify(token, client_ip="1.2.3.4")


def test_expired_jwt_raises(verifier, rsa_private_pem):
    now = int(time.time())
    token = _issue_jwt(
        rsa_private_pem,
        {
            "email": "alice@example.com",
            "iss": "https://test.cloudflareaccess.com",
            "aud": "test-aud",
            "iat": now - 600,
            "exp": now - 60,
        },
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        verifier.verify(token, client_ip="1.2.3.4")


def test_wrong_issuer_raises(verifier, rsa_private_pem):
    now = int(time.time())
    token = _issue_jwt(
        rsa_private_pem,
        {
            "email": "a@b",
            "iss": "https://evil.example.com",
            "aud": "test-aud",
            "iat": now,
            "exp": now + 300,
        },
    )
    with pytest.raises(jwt.InvalidIssuerError):
        verifier.verify(token, client_ip="1.2.3.4")


def test_wrong_audience_raises(verifier, rsa_private_pem):
    now = int(time.time())
    token = _issue_jwt(
        rsa_private_pem,
        {
            "email": "a@b",
            "iss": "https://test.cloudflareaccess.com",
            "aud": "wrong-aud",
            "iat": now,
            "exp": now + 300,
        },
    )
    with pytest.raises(jwt.InvalidAudienceError):
        verifier.verify(token, client_ip="1.2.3.4")


def test_tampered_signature_raises(verifier, rsa_private_pem):
    now = int(time.time())
    token = _issue_jwt(
        rsa_private_pem,
        {
            "email": "a@b",
            "iss": "https://test.cloudflareaccess.com",
            "aud": "test-aud",
            "iat": now,
            "exp": now + 300,
        },
    )
    parts = token.split(".")
    parts[2] = parts[2][:-4] + "XXXX"
    bad = ".".join(parts)
    with pytest.raises(jwt.InvalidSignatureError):
        verifier.verify(bad, client_ip="1.2.3.4")


def test_kid_miss_forces_refresh(rsa_public_pem, rsa_private_pem):
    v = CFAccessVerifier(
        team_domain="test.cloudflareaccess.com",
        audience="test-aud",
        trusted_dev_nets=[],
        env="prod",
    )
    mock_key = Mock()
    mock_key.key = rsa_public_pem
    call_count = {"n": 0}

    def side_effect(token):
        call_count["n"] += 1
        if call_count["n"] == 1:
            from jwt.exceptions import PyJWKClientError
            raise PyJWKClientError("unknown kid")
        return mock_key

    v._jwks_client = Mock()
    v._jwks_client.get_signing_key_from_jwt = Mock(side_effect=side_effect)

    now = int(time.time())
    token = _issue_jwt(
        rsa_private_pem,
        {
            "email": "a@b",
            "iss": "https://test.cloudflareaccess.com",
            "aud": "test-aud",
            "iat": now,
            "exp": now + 300,
        },
    )
    identity = v.verify(token, client_ip="1.2.3.4")
    assert identity.email == "a@b"
    assert call_count["n"] == 2
    v._jwks_client.invalidate_cache.assert_called_once()


def test_dev_bypass_when_env_dev_and_ip_in_list():
    v = CFAccessVerifier(
        team_domain="",
        audience="",
        trusted_dev_nets=["10.10.0.0/24"],
        env="dev",
    )
    identity = v.check_dev_bypass(client_ip="10.10.0.5")
    assert identity is not None
    assert identity.email == "dev@localhost"
    assert identity.kind == "dev-bypass"


def test_dev_bypass_inactive_when_env_dev_but_ip_not_in_list():
    v = CFAccessVerifier(
        team_domain="",
        audience="",
        trusted_dev_nets=["10.10.0.0/24"],
        env="dev",
    )
    assert v.check_dev_bypass(client_ip="88.208.197.219") is None


def test_dev_bypass_inactive_when_env_prod_even_with_ip_match():
    v = CFAccessVerifier(
        team_domain="",
        audience="",
        trusted_dev_nets=["10.10.0.0/24"],
        env="prod",
    )
    assert v.check_dev_bypass(client_ip="10.10.0.5") is None


def test_dev_bypass_inactive_when_trusted_nets_empty():
    v = CFAccessVerifier(
        team_domain="",
        audience="",
        trusted_dev_nets=[],
        env="dev",
    )
    assert v.check_dev_bypass(client_ip="10.10.0.5") is None


def test_prod_with_trusted_dev_nets_is_a_config_smell(caplog):
    """Startup should log CRITICAL if prod env has non-empty trusted_dev_nets."""
    import logging
    with caplog.at_level(logging.CRITICAL, logger="app.core.cf_access"):
        CFAccessVerifier(
            team_domain="",
            audience="",
            trusted_dev_nets=["10.10.0.0/24"],
            env="prod",
        ).check_startup_config_smell()
    assert any("dev_bypass_config_smell" in rec.message for rec in caplog.records)


def test_client_ip_in_trusted_nets():
    nets = ["10.10.0.0/24", "192.168.1.0/24"]
    assert client_ip_in_trusted_nets("10.10.0.5", nets)
    assert client_ip_in_trusted_nets("192.168.1.254", nets)
    assert not client_ip_in_trusted_nets("8.8.8.8", nets)
    assert not client_ip_in_trusted_nets("10.11.0.1", nets)
    assert not client_ip_in_trusted_nets("invalid-ip", nets)


def test_client_ip_empty_nets_always_false():
    assert not client_ip_in_trusted_nets("10.10.0.5", [])
```

- [ ] **Step 7.2: Run test — expect import error**

```bash
cd /mnt/c/dashboard/backend
uv run pytest tests/test_cf_access.py -v 2>&1 | tail -5
```

Expected: `ModuleNotFoundError: No module named 'app.core.cf_access'`.

- [ ] **Step 7.3: Write `app/core/cf_access.py`**

```python
"""CF Access JWT verification + dev-mode bypass gating.

Uses pyjwt's PyJWKClient for JWKS caching (1-hour). On kid-miss, forces an
immediate refresh. Accepts `email` (Google login) OR `common_name` (service
token) as identity.

Dev-mode bypass requires BOTH APP_ENV=dev AND client IP in TRUSTED_DEV_NETS.
"""

import logging
from dataclasses import dataclass, field
from ipaddress import ip_address, ip_network
from typing import Any, Literal

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError

log = logging.getLogger(__name__)

AdminKind = Literal["user", "service_token", "dev-bypass"]


class NoIdentityClaimError(Exception):
    """Raised when neither `email` nor `common_name` is present in JWT claims."""


@dataclass
class AdminIdentity:
    email: str
    kind: AdminKind
    claims: dict[str, Any] = field(default_factory=dict)


def client_ip_in_trusted_nets(client_ip: str, nets: list[str]) -> bool:
    """Return True if client_ip is contained in any CIDR in nets."""
    if not nets:
        return False
    try:
        addr = ip_address(client_ip)
    except ValueError:
        return False
    for n in nets:
        try:
            if addr in ip_network(n, strict=False):
                return True
        except ValueError:
            continue
    return False


class CFAccessVerifier:
    def __init__(
        self,
        team_domain: str,
        audience: str,
        trusted_dev_nets: list[str],
        env: str,
    ) -> None:
        self.team_domain = team_domain
        self.audience = audience
        self.trusted_dev_nets = trusted_dev_nets
        self.env = env
        self._jwks_client: PyJWKClient | Any = (
            PyJWKClient(
                f"https://{team_domain}/cdn-cgi/access/certs",
                cache_keys=True,
                lifespan=3600,
            )
            if team_domain
            else None
        )

    def check_startup_config_smell(self) -> None:
        if self.env == "prod" and self.trusted_dev_nets:
            log.critical(
                "dev_bypass_config_smell: env=prod with trusted_dev_nets=%s — "
                "dev-bypass is disabled in prod but config shape is suspicious",
                self.trusted_dev_nets,
            )

    def check_dev_bypass(self, client_ip: str) -> AdminIdentity | None:
        if self.env != "dev":
            return None
        if not client_ip_in_trusted_nets(client_ip, self.trusted_dev_nets):
            return None
        return AdminIdentity(email="dev@localhost", kind="dev-bypass", claims={})

    def verify(self, token: str, client_ip: str) -> AdminIdentity:
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token).key
        except (PyJWKClientError, KeyError):
            self._jwks_client.invalidate_cache()
            signing_key = self._jwks_client.get_signing_key_from_jwt(token).key

        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            issuer=f"https://{self.team_domain}",
            audience=self.audience,
        )

        identity = claims.get("email") or claims.get("common_name")
        if not identity:
            raise NoIdentityClaimError("jwt missing identity claim")

        kind: AdminKind = "user" if claims.get("email") else "service_token"
        return AdminIdentity(email=identity, kind=kind, claims=claims)
```

- [ ] **Step 7.4: Run test — expect pass**

```bash
cd /mnt/c/dashboard/backend
uv run pytest tests/test_cf_access.py -v 2>&1 | tail -20
```

Expected: `13 passed`.

- [ ] **Step 7.5: Coverage check**

```bash
cd /mnt/c/dashboard/backend
uv run pytest tests/test_cf_access.py --cov=app.core.cf_access --cov-report=term-missing 2>&1 | tail -10
```

Expected: `app/core/cf_access.py ... 100%` (or 99% with one CIDR-malformed branch; acceptable).

- [ ] **Step 7.6: Commit**

```bash
cd /mnt/c/dashboard
git add backend/app/core/cf_access.py backend/tests/test_cf_access.py
git commit -m "feat(auth): cf-access jwt verifier with dev-bypass double-gate"
```

---

## Chunk E — ConfigService + Redis pub/sub cache

### Task 8: `core/metrics.py` — prometheus counters + gauges

**Files:**
- Create: `backend/app/core/metrics.py`

- [ ] **Step 8.1: Write `app/core/metrics.py`**

```python
"""prometheus-client counters/gauges for Phase 2 observability."""

from prometheus_client import CollectorRegistry, Counter, Gauge

registry = CollectorRegistry(auto_describe=True)


cf_jwt_verification_total = Counter(
    "cf_jwt_verification_total",
    "CF Access JWT verification outcomes",
    labelnames=["result"],
    registry=registry,
)

config_ops_total = Counter(
    "config_ops_total",
    "Config/secret operations",
    labelnames=["op", "kind", "result"],
    registry=registry,
)

config_cache_size = Gauge(
    "config_cache_size",
    "Entries currently in the per-worker cache",
    labelnames=["kind"],
    registry=registry,
)

redis_publish_fail_total = Counter(
    "redis_publish_fail_total",
    "Redis publish errors during config invalidation",
    labelnames=["channel"],
    registry=registry,
)

redis_subscribe_reconnect_total = Counter(
    "redis_subscribe_reconnect_total",
    "Redis subscribe reconnect attempts",
    labelnames=["channel"],
    registry=registry,
)

fernet_prev_key_hits_total = Counter(
    "fernet_prev_key_hits_total",
    "Reveals decrypted via the PREV Fernet key (rotation indicator)",
    registry=registry,
)

admin_secret_reveal_total = Counter(
    "admin_secret_reveal_total",
    "Plaintext reveal operations on /api/admin/secrets/*/reveal",
    labelnames=["actor_kind"],
    registry=registry,
)
```

- [ ] **Step 8.2: Smoke-import**

```bash
cd /mnt/c/dashboard/backend
uv run python -c "from app.core.metrics import registry, cf_jwt_verification_total; print('metrics ok')"
```

Expected: `metrics ok`.

- [ ] **Step 8.3: Commit**

```bash
cd /mnt/c/dashboard
git add backend/app/core/metrics.py
git commit -m "feat(metrics): prometheus counters and gauges for phase 2"
```

---

### Task 9: `services/config_cache.py` — in-memory cache + Redis pub/sub listener

**Files:**
- Create: `backend/app/services/config_cache.py`
- Create: `backend/tests/test_config_cache.py`

- [ ] **Step 9.1: Write tests**

Create `backend/tests/test_config_cache.py`:

```python
"""Tests for config_cache — in-memory dict + TTL + pub/sub listener."""

import asyncio

import fakeredis.aioredis as fakeredis_async
import pytest

from app.core.metrics import registry  # noqa: F401
from app.services.config_cache import ConfigCache


@pytest.fixture
async def redis():
    r = fakeredis_async.FakeRedis(decode_responses=False)
    yield r
    await r.aclose()


@pytest.mark.asyncio
async def test_cache_hit_miss(redis):
    cache = ConfigCache(
        redis=redis, channel="config:invalidate", kind_label="config", ttl_seconds=60
    )
    assert cache.get(("telegram", "bot_token")) is None
    cache.set(("telegram", "bot_token"), "abc")
    assert cache.get(("telegram", "bot_token")) == "abc"


@pytest.mark.asyncio
async def test_cache_expires_after_ttl(redis):
    cache = ConfigCache(
        redis=redis, channel="config:invalidate", kind_label="config", ttl_seconds=0
    )
    cache.set(("ns", "k"), "v")
    assert cache.get(("ns", "k")) is None


@pytest.mark.asyncio
async def test_cache_pop(redis):
    cache = ConfigCache(
        redis=redis, channel="config:invalidate", kind_label="config", ttl_seconds=60
    )
    cache.set(("a", "b"), "x")
    assert cache.get(("a", "b")) == "x"
    cache.pop(("a", "b"))
    assert cache.get(("a", "b")) is None


@pytest.mark.asyncio
async def test_publish_invalidation(redis):
    cache = ConfigCache(
        redis=redis, channel="config:invalidate", kind_label="config", ttl_seconds=60
    )
    pubsub = redis.pubsub()
    await pubsub.subscribe("config:invalidate")
    await asyncio.sleep(0.05)

    await cache.publish_invalidation("telegram", "bot_token")
    await asyncio.sleep(0.05)

    msgs = []
    async for msg in pubsub.listen():
        if msg["type"] == "message":
            msgs.append(msg["data"])
            break
    assert b"telegram|bot_token" in msgs
    await pubsub.unsubscribe("config:invalidate")
    await pubsub.aclose()


@pytest.mark.asyncio
async def test_publish_swallows_errors(redis, caplog):
    import logging
    from unittest.mock import AsyncMock

    cache = ConfigCache(
        redis=redis, channel="config:invalidate", kind_label="config", ttl_seconds=60
    )
    cache.redis.publish = AsyncMock(side_effect=ConnectionError("no redis"))
    with caplog.at_level(logging.WARNING, logger="app.services.config_cache"):
        await cache.publish_invalidation("ns", "k")
    assert any("publish failed" in rec.message.lower() for rec in caplog.records)


@pytest.mark.asyncio
async def test_listener_evicts_on_message(redis):
    cache = ConfigCache(
        redis=redis, channel="config:invalidate:test", kind_label="config", ttl_seconds=60
    )
    cache.set(("ns", "key"), "stale")

    task = asyncio.create_task(cache.run_listener())
    await asyncio.sleep(0.1)

    await redis.publish("config:invalidate:test", b"ns|key")
    await asyncio.sleep(0.15)

    assert cache.get(("ns", "key")) is None

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
```

- [ ] **Step 9.2: Run — expect import error**

```bash
cd /mnt/c/dashboard/backend
uv run pytest tests/test_config_cache.py -v 2>&1 | tail -5
```

- [ ] **Step 9.3: Write `app/services/config_cache.py`**

```python
"""Per-worker in-memory cache + Redis pub/sub listener for invalidation."""

import asyncio
import logging
import time
from typing import Any

from redis.asyncio import Redis

from app.core import metrics

log = logging.getLogger(__name__)


class ConfigCache:
    def __init__(
        self,
        redis: Redis,
        channel: str,
        kind_label: str,
        ttl_seconds: int = 300,
    ) -> None:
        self.redis = redis
        self.channel = channel
        self.kind_label = kind_label
        self.ttl_seconds = ttl_seconds
        self._store: dict[tuple[str, str], tuple[Any, float]] = {}

    def get(self, key: tuple[str, str]) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, ts = entry
        if self.ttl_seconds <= 0 or (time.monotonic() - ts) > self.ttl_seconds:
            self._store.pop(key, None)
            metrics.config_cache_size.labels(kind=self.kind_label).set(len(self._store))
            return None
        return value

    def set(self, key: tuple[str, str], value: Any) -> None:
        self._store[key] = (value, time.monotonic())
        metrics.config_cache_size.labels(kind=self.kind_label).set(len(self._store))

    def pop(self, key: tuple[str, str]) -> None:
        self._store.pop(key, None)
        metrics.config_cache_size.labels(kind=self.kind_label).set(len(self._store))

    async def publish_invalidation(self, namespace: str, key: str) -> None:
        payload = f"{namespace}|{key}".encode()
        try:
            await self.redis.publish(self.channel, payload)
        except Exception as e:
            log.warning(
                "config_cache publish failed: channel=%s ns=%s key=%s err=%s",
                self.channel, namespace, key, e,
            )
            metrics.redis_publish_fail_total.labels(channel=self.channel).inc()

    async def run_listener(self) -> None:
        attempt = 0
        while True:
            try:
                async with self.redis.pubsub() as pubsub:
                    await pubsub.subscribe(self.channel)
                    attempt = 0
                    async for msg in pubsub.listen():
                        if msg["type"] != "message":
                            continue
                        try:
                            ns, key = msg["data"].decode().split("|", 1)
                            self.pop((ns, key))
                        except (UnicodeDecodeError, ValueError):
                            log.warning("bad invalidation payload: %r", msg["data"])
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning(
                    "config_cache listener disconnected: channel=%s attempt=%d err=%s",
                    self.channel, attempt, e,
                )
                metrics.redis_subscribe_reconnect_total.labels(channel=self.channel).inc()
                await asyncio.sleep(min(2**attempt, 30))
                attempt += 1
```

- [ ] **Step 9.4: Run test — expect pass**

```bash
cd /mnt/c/dashboard/backend
uv run pytest tests/test_config_cache.py -v 2>&1 | tail -15
```

Expected: `6 passed`.

- [ ] **Step 9.5: Commit**

```bash
cd /mnt/c/dashboard
git add backend/app/services/config_cache.py backend/tests/test_config_cache.py
git commit -m "feat(services): config cache with ttl + redis pub/sub invalidation"
```

---

### Task 10: `services/config.py` — `ConfigService` CRUD + typed accessors + secrets

**Files:**
- Create: `backend/app/services/config.py`
- Create: `backend/tests/test_config_service.py`

- [ ] **Step 10.1: Write tests**

Create `backend/tests/test_config_service.py`:

```python
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
            await s.execute(
                text(
                    "SELECT value, value_json, value_type FROM app_config "
                    "WHERE namespace='ns' AND key='c'"
                )
            )
        ).mappings().one()
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
            await s.execute(
                text("SELECT value_encrypted FROM app_secrets WHERE namespace='s' AND key='k'")
            )
        ).mappings().one()
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
        await s.execute(text("UPDATE app_config SET value='v-direct' WHERE namespace='ns' AND key='k'"))
        await s.commit()
    assert await service.get("ns", "k") == "v1"


@pytest.mark.asyncio
async def test_cache_invalidation_via_pubsub(service):
    await service.set("ns", "k", "v1")
    assert await service.get("ns", "k") == "v1"
    service._cache.pop(("ns", "k"))
    async with service._session_factory() as s:
        await s.execute(text("UPDATE app_config SET value='v2' WHERE namespace='ns' AND key='k'"))
        await s.commit()
    assert await service.get("ns", "k") == "v2"
```

- [ ] **Step 10.2: Run — expect import error**

```bash
cd /mnt/c/dashboard/backend
uv run pytest tests/test_config_service.py -v 2>&1 | tail -5
```

- [ ] **Step 10.3: Write `app/services/config.py`**

```python
"""ConfigService — typed DB-backed config + Fernet-encrypted secrets."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import metrics
from app.models import AppConfig, AppSecret
from app.services.config_cache import ConfigCache

log = logging.getLogger(__name__)

ValueType = Literal["str", "int", "bool", "json"]


class ConfigTypeError(ValueError):
    pass


@dataclass
class SecretMetadata:
    namespace: str
    key: str
    value_type: str
    created_at: datetime
    updated_at: datetime


def _coerce_from_stored(raw: str | None, raw_json: Any, value_type: str) -> Any:
    if value_type == "json":
        return raw_json
    if value_type == "int":
        return int(raw) if raw is not None else None
    if value_type == "bool":
        return raw == "true" if raw is not None else None
    return raw


class ConfigService:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        cache: ConfigCache,
        secrets_cache: ConfigCache,
        fernet: Fernet | MultiFernet,
    ) -> None:
        self._session_factory = session_factory
        self._cache = cache
        self._secrets_cache = secrets_cache
        self._fernet = fernet

    async def get(self, ns: str, key: str, default: Any = None) -> Any:
        cached = self._cache.get((ns, key))
        if cached is not None:
            metrics.config_ops_total.labels(op="get", kind="config", result="hit").inc()
            return cached[0]
        async with self._session_factory() as s:
            stmt = select(
                AppConfig.value, AppConfig.value_json, AppConfig.value_type
            ).where(AppConfig.namespace == ns, AppConfig.key == key)
            row = (await s.execute(stmt)).one_or_none()
        if row is None:
            metrics.config_ops_total.labels(op="get", kind="config", result="miss").inc()
            return default
        materialized = _coerce_from_stored(row.value, row.value_json, row.value_type)
        self._cache.set((ns, key), (materialized, row.value_type))
        metrics.config_ops_total.labels(op="get", kind="config", result="ok").inc()
        return materialized

    async def get_int(self, ns: str, key: str, default: int | None = None) -> int | None:
        return await self._get_typed(ns, key, "int", default)

    async def get_bool(self, ns: str, key: str, default: bool | None = None) -> bool | None:
        return await self._get_typed(ns, key, "bool", default)

    async def get_json(self, ns: str, key: str, default: Any = None) -> Any:
        return await self._get_typed(ns, key, "json", default)

    async def _get_typed(self, ns: str, key: str, expected: str, default: Any) -> Any:
        cached = self._cache.get((ns, key))
        if cached is not None:
            value, stored_type = cached
            if stored_type != expected:
                raise ConfigTypeError(
                    f"{ns}.{key} has value_type={stored_type!r}, accessor expected {expected!r}"
                )
            return value
        async with self._session_factory() as s:
            stmt = select(
                AppConfig.value, AppConfig.value_json, AppConfig.value_type
            ).where(AppConfig.namespace == ns, AppConfig.key == key)
            row = (await s.execute(stmt)).one_or_none()
        if row is None:
            return default
        if row.value_type != expected:
            raise ConfigTypeError(
                f"{ns}.{key} has value_type={row.value_type!r}, accessor expected {expected!r}"
            )
        materialized = _coerce_from_stored(row.value, row.value_json, row.value_type)
        self._cache.set((ns, key), (materialized, row.value_type))
        return materialized

    async def set(self, ns: str, key: str, value: Any, value_type: str = "str") -> AppConfig:
        if value_type not in ("str", "int", "bool", "json"):
            raise ValueError(f"invalid value_type={value_type!r}")

        if value_type == "json":
            row_value = None
            row_value_json = value
        elif value_type == "bool":
            row_value = "true" if bool(value) else "false"
            row_value_json = None
        elif value_type == "int":
            row_value = str(int(value))
            row_value_json = None
        else:
            row_value = str(value)
            row_value_json = None

        async with self._session_factory() as s:
            stmt = pg_insert(AppConfig).values(
                namespace=ns,
                key=key,
                value=row_value,
                value_json=row_value_json,
                value_type=value_type,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["namespace", "key"],
                set_={
                    "value": stmt.excluded.value,
                    "value_json": stmt.excluded.value_json,
                    "value_type": stmt.excluded.value_type,
                    "updated_at": text("now()"),
                },
            ).returning(AppConfig)
            result = await s.execute(stmt)
            row = result.scalar_one()
            await s.commit()

        self._cache.pop((ns, key))
        await self._cache.publish_invalidation(ns, key)
        metrics.config_ops_total.labels(op="set", kind="config", result="ok").inc()
        return row

    async def delete(self, ns: str, key: str) -> bool:
        async with self._session_factory() as s:
            result = await s.execute(
                delete(AppConfig).where(
                    AppConfig.namespace == ns, AppConfig.key == key
                )
            )
            await s.commit()
            existed = result.rowcount > 0
        self._cache.pop((ns, key))
        await self._cache.publish_invalidation(ns, key)
        metrics.config_ops_total.labels(op="delete", kind="config", result="ok").inc()
        return existed

    async def list(self, namespace: str | None = None) -> list[AppConfig]:
        async with self._session_factory() as s:
            stmt = select(AppConfig)
            if namespace is not None:
                stmt = stmt.where(AppConfig.namespace == namespace)
            stmt = stmt.order_by(AppConfig.namespace, AppConfig.key)
            rows = (await s.execute(stmt)).scalars().all()
        metrics.config_ops_total.labels(op="list", kind="config", result="ok").inc()
        return list(rows)

    async def set_secret(
        self, ns: str, key: str, value: Any, value_type: str = "str"
    ) -> AppSecret:
        if value_type not in ("str", "int", "bool", "json"):
            raise ValueError(f"invalid value_type={value_type!r}")
        if value_type == "json":
            plaintext = json.dumps(value).encode()
        elif value_type == "bool":
            plaintext = (b"true" if bool(value) else b"false")
        elif value_type == "int":
            plaintext = str(int(value)).encode()
        else:
            plaintext = str(value).encode()
        ciphertext = self._fernet.encrypt(plaintext)

        async with self._session_factory() as s:
            stmt = pg_insert(AppSecret).values(
                namespace=ns,
                key=key,
                value_encrypted=ciphertext,
                value_type=value_type,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["namespace", "key"],
                set_={
                    "value_encrypted": stmt.excluded.value_encrypted,
                    "value_type": stmt.excluded.value_type,
                    "updated_at": text("now()"),
                },
            ).returning(AppSecret)
            result = await s.execute(stmt)
            row = result.scalar_one()
            await s.commit()

        self._secrets_cache.pop((ns, key))
        await self._secrets_cache.publish_invalidation(ns, key)
        metrics.config_ops_total.labels(op="set", kind="secret", result="ok").inc()
        return row

    async def get_secret_metadata(self, ns: str, key: str) -> SecretMetadata | None:
        async with self._session_factory() as s:
            stmt = select(
                AppSecret.namespace,
                AppSecret.key,
                AppSecret.value_type,
                AppSecret.created_at,
                AppSecret.updated_at,
            ).where(AppSecret.namespace == ns, AppSecret.key == key)
            row = (await s.execute(stmt)).one_or_none()
        if row is None:
            return None
        return SecretMetadata(
            namespace=row.namespace,
            key=row.key,
            value_type=row.value_type,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def reveal_secret(self, ns: str, key: str, default: Any = None) -> Any:
        return await self._reveal_typed(ns, key, None, default)

    async def reveal_secret_int(self, ns: str, key: str, default: int | None = None) -> int | None:
        return await self._reveal_typed(ns, key, "int", default)

    async def reveal_secret_bool(self, ns: str, key: str, default: bool | None = None) -> bool | None:
        return await self._reveal_typed(ns, key, "bool", default)

    async def reveal_secret_json(self, ns: str, key: str, default: Any = None) -> Any:
        return await self._reveal_typed(ns, key, "json", default)

    async def _reveal_typed(
        self, ns: str, key: str, expected: str | None, default: Any
    ) -> Any:
        async with self._session_factory() as s:
            stmt = select(AppSecret.value_encrypted, AppSecret.value_type).where(
                AppSecret.namespace == ns, AppSecret.key == key
            )
            row = (await s.execute(stmt)).one_or_none()
        if row is None:
            return default
        if expected is not None and row.value_type != expected:
            raise ConfigTypeError(
                f"{ns}.{key} has value_type={row.value_type!r}, accessor expected {expected!r}"
            )
        try:
            plaintext = self._fernet.decrypt(row.value_encrypted)
        except InvalidToken:
            log.error(
                "fernet_decrypt_failed ns=%s key=%s (APP_SECRET_KEY rotated or row tampered)",
                ns, key,
            )
            raise
        # Detect PREV-key hit (MultiFernet).
        if isinstance(self._fernet, MultiFernet):
            primary = self._fernet._fernets[0]
            try:
                primary.decrypt(row.value_encrypted)
            except InvalidToken:
                metrics.fernet_prev_key_hits_total.inc()
                log.info("fernet_prev_key_hit ns=%s key=%s", ns, key)

        if row.value_type == "json":
            return json.loads(plaintext.decode())
        if row.value_type == "int":
            return int(plaintext.decode())
        if row.value_type == "bool":
            return plaintext.decode() == "true"
        return plaintext.decode()

    async def delete_secret(self, ns: str, key: str) -> bool:
        async with self._session_factory() as s:
            result = await s.execute(
                delete(AppSecret).where(AppSecret.namespace == ns, AppSecret.key == key)
            )
            await s.commit()
            existed = result.rowcount > 0
        self._secrets_cache.pop((ns, key))
        await self._secrets_cache.publish_invalidation(ns, key)
        metrics.config_ops_total.labels(op="delete", kind="secret", result="ok").inc()
        return existed

    async def list_secrets(self, namespace: str | None = None) -> list[SecretMetadata]:
        async with self._session_factory() as s:
            stmt = select(
                AppSecret.namespace,
                AppSecret.key,
                AppSecret.value_type,
                AppSecret.created_at,
                AppSecret.updated_at,
            )
            if namespace is not None:
                stmt = stmt.where(AppSecret.namespace == namespace)
            stmt = stmt.order_by(AppSecret.namespace, AppSecret.key)
            rows = (await s.execute(stmt)).all()
        metrics.config_ops_total.labels(op="list", kind="secret", result="ok").inc()
        return [
            SecretMetadata(
                namespace=r.namespace,
                key=r.key,
                value_type=r.value_type,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]
```

- [ ] **Step 10.4: Run tests**

```bash
cd /mnt/c/dashboard/backend
uv run pytest tests/test_config_service.py -v 2>&1 | tail -25
```

Expected: `18 passed`.

- [ ] **Step 10.5: Commit**

```bash
cd /mnt/c/dashboard
git add backend/app/services/config.py backend/tests/test_config_service.py
git commit -m "feat(services): configservice with typed accessors + fernet secrets"
```

---

## Chunk F — Admin router (CRUD + reveal + 422 on URL mismatch)

### Task 11: Pydantic schemas

**Files:**
- Create: `backend/app/api/schemas.py`

- [ ] **Step 11.1: Write `app/api/schemas.py`**

```python
"""Pydantic request/response shapes for admin routes."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ValueType = Literal["str", "int", "bool", "json"]
NAMESPACE_PATTERN = r"^[a-z][a-z0-9_-]*$"
KEY_PATTERN = r"^[a-z][a-z0-9_.-]*$"


class ConfigIn(BaseModel):
    namespace: str = Field(min_length=1, max_length=64, pattern=NAMESPACE_PATTERN)
    key: str = Field(min_length=1, max_length=128, pattern=KEY_PATTERN)
    value: Any
    value_type: ValueType = "str"


class ConfigInUpsert(BaseModel):
    namespace: str | None = Field(default=None, max_length=64, pattern=NAMESPACE_PATTERN)
    key: str | None = Field(default=None, max_length=128, pattern=KEY_PATTERN)
    value: Any
    value_type: ValueType = "str"


class ConfigOut(BaseModel):
    namespace: str
    key: str
    value: Any
    value_type: str
    created_at: datetime
    updated_at: datetime


class SecretIn(BaseModel):
    namespace: str = Field(min_length=1, max_length=64, pattern=NAMESPACE_PATTERN)
    key: str = Field(min_length=1, max_length=128, pattern=KEY_PATTERN)
    value: Any
    value_type: ValueType = "str"


class SecretInUpsert(BaseModel):
    namespace: str | None = Field(default=None, max_length=64, pattern=NAMESPACE_PATTERN)
    key: str | None = Field(default=None, max_length=128, pattern=KEY_PATTERN)
    value: Any
    value_type: ValueType = "str"


class SecretMetadataOut(BaseModel):
    namespace: str
    key: str
    value_type: str
    created_at: datetime
    updated_at: datetime


class SecretRevealOut(BaseModel):
    namespace: str
    key: str
    value: Any
    value_type: str
```

- [ ] **Step 11.2: Commit**

```bash
cd /mnt/c/dashboard
git add backend/app/api/schemas.py
git commit -m "feat(api): pydantic request/response schemas for admin routes"
```

---

### Task 12: Admin router + tests

**Files:**
- Create: `backend/app/api/admin.py`
- Create: `backend/tests/test_admin_api.py`

- [ ] **Step 12.1: Write tests**

Create `backend/tests/test_admin_api.py`:

```python
"""End-to-end admin router tests — auth dep overridden."""

from collections.abc import AsyncIterator

import fakeredis.aioredis as fakeredis_async
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.cf_access import AdminIdentity
from app.core.config import settings
from app.core.crypto import get_fernet
from app.core.deps import get_config
from app.main import app
from app.services.config import ConfigService
from app.services.config_cache import ConfigCache


@pytest.fixture(scope="module")
def engine():
    return create_async_engine(settings.database_url, echo=False)


@pytest.fixture(scope="module")
def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def clean_tables(session_factory):
    async with session_factory() as s:
        await s.execute(text("DELETE FROM app_config"))
        await s.execute(text("DELETE FROM app_secrets"))
        await s.commit()


@pytest.fixture
async def client(session_factory) -> AsyncIterator[TestClient]:
    r = fakeredis_async.FakeRedis(decode_responses=False)
    cache = ConfigCache(r, "config:invalidate", "config", ttl_seconds=60)
    secrets_cache = ConfigCache(r, "config:invalidate:secrets", "secret", ttl_seconds=60)
    fernet = get_fernet("test-key-stable", None)
    service = ConfigService(session_factory, cache, secrets_cache, fernet)

    from app.core.deps import require_admin_jwt
    app.dependency_overrides[require_admin_jwt] = lambda: AdminIdentity(
        email="test@example.com", kind="user", claims={}
    )
    app.dependency_overrides[get_config] = lambda: service

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
    await r.aclose()


def test_list_empty(client):
    assert client.get("/api/admin/config").json() == []


def test_list_after_inserts(client):
    for i in range(3):
        client.post(
            "/api/admin/config",
            json={"namespace": "a", "key": f"k{i}", "value": f"v{i}", "value_type": "str"},
        )
    assert len(client.get("/api/admin/config").json()) == 3


def test_list_namespace_filter(client):
    client.post("/api/admin/config", json={"namespace": "a", "key": "k", "value": "v"})
    client.post("/api/admin/config", json={"namespace": "b", "key": "k", "value": "v"})
    assert {e["namespace"] for e in client.get("/api/admin/config?namespace=a").json()} == {"a"}


def test_get_existing(client):
    client.post("/api/admin/config", json={"namespace": "n", "key": "k", "value": "v"})
    resp = client.get("/api/admin/config/n/k")
    assert resp.status_code == 200
    assert resp.json()["value"] == "v"


def test_get_missing_404(client):
    assert client.get("/api/admin/config/absent/k").status_code == 404


def test_post_valid_201(client):
    resp = client.post(
        "/api/admin/config",
        json={"namespace": "x", "key": "y", "value": "z", "value_type": "str"},
    )
    assert resp.status_code == 201


def test_post_json_value_stored(client):
    resp = client.post(
        "/api/admin/config",
        json={"namespace": "n", "key": "cfg", "value": {"nested": 1}, "value_type": "json"},
    )
    assert resp.status_code == 201
    assert resp.json()["value"] == {"nested": 1}


def test_post_invalid_value_type_422(client):
    resp = client.post(
        "/api/admin/config",
        json={"namespace": "n", "key": "k", "value": "v", "value_type": "FLOAT"},
    )
    assert resp.status_code == 422


def test_post_invalid_namespace_pattern_422(client):
    resp = client.post(
        "/api/admin/config",
        json={"namespace": "UpperCase", "key": "k", "value": "v"},
    )
    assert resp.status_code == 422


def test_post_duplicate_409(client):
    client.post("/api/admin/config", json={"namespace": "n", "key": "k", "value": "v"})
    resp = client.post("/api/admin/config", json={"namespace": "n", "key": "k", "value": "v2"})
    assert resp.status_code == 409


def test_put_creates_if_missing(client):
    resp = client.put(
        "/api/admin/config/n/k",
        json={"namespace": "n", "key": "k", "value": "v", "value_type": "str"},
    )
    assert resp.status_code == 200


def test_put_updates_existing(client):
    client.post("/api/admin/config", json={"namespace": "n", "key": "k", "value": "v1"})
    resp = client.put(
        "/api/admin/config/n/k",
        json={"namespace": "n", "key": "k", "value": "v2", "value_type": "str"},
    )
    assert resp.json()["value"] == "v2"


def test_put_body_ns_mismatch_url_422(client):
    resp = client.put(
        "/api/admin/config/foo/k",
        json={"namespace": "bar", "key": "k", "value": "v"},
    )
    assert resp.status_code == 422
    assert "mismatch" in resp.json()["detail"].lower()


def test_put_body_omits_ns_fills_from_url(client):
    resp = client.put(
        "/api/admin/config/n/k",
        json={"value": "v", "value_type": "str"},
    )
    assert resp.status_code == 200
    assert resp.json()["namespace"] == "n"


def test_delete_existing_204(client):
    client.post("/api/admin/config", json={"namespace": "n", "key": "k", "value": "v"})
    assert client.delete("/api/admin/config/n/k").status_code == 204


def test_delete_missing_also_204(client):
    assert client.delete("/api/admin/config/absent/key").status_code == 204


def test_post_secret_metadata_only_in_response(client):
    resp = client.post(
        "/api/admin/secrets",
        json={"namespace": "s", "key": "k", "value": "sensitive", "value_type": "str"},
    )
    assert resp.status_code == 201
    assert "value" not in resp.json()


def test_get_secret_metadata_no_plaintext(client):
    client.post(
        "/api/admin/secrets",
        json={"namespace": "s", "key": "k", "value": "secret", "value_type": "str"},
    )
    resp = client.get("/api/admin/secrets/s/k")
    assert resp.status_code == 200
    assert "value" not in resp.json()


def test_list_secrets_metadata_only(client):
    client.post(
        "/api/admin/secrets",
        json={"namespace": "s", "key": "k", "value": "x", "value_type": "str"},
    )
    resp = client.get("/api/admin/secrets")
    assert resp.status_code == 200
    assert all("value" not in e for e in resp.json())


def test_reveal_returns_plaintext_and_nostore_header(client):
    client.post(
        "/api/admin/secrets",
        json={"namespace": "s", "key": "k", "value": "p@ssw0rd", "value_type": "str"},
    )
    resp = client.post("/api/admin/secrets/s/k/reveal")
    assert resp.status_code == 200
    assert resp.json()["value"] == "p@ssw0rd"
    assert "no-store" in resp.headers.get("cache-control", "")
    assert resp.headers.get("x-content-type-options") == "nosniff"


def test_reveal_missing_404(client):
    assert client.post("/api/admin/secrets/absent/k/reveal").status_code == 404


def test_delete_secret_idempotent(client):
    client.post("/api/admin/secrets", json={"namespace": "s", "key": "k", "value": "x"})
    assert client.delete("/api/admin/secrets/s/k").status_code == 204
    assert client.delete("/api/admin/secrets/s/k").status_code == 204
```

- [ ] **Step 12.2: Write `app/api/admin.py`**

```python
"""Admin router: /api/admin/config + /api/admin/secrets + reveal endpoint."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.schemas import (
    ConfigIn,
    ConfigInUpsert,
    ConfigOut,
    SecretIn,
    SecretInUpsert,
    SecretMetadataOut,
    SecretRevealOut,
)
from app.core import metrics
from app.core.cf_access import AdminIdentity
from app.core.deps import get_config, require_admin_jwt
from app.services.config import ConfigService

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_jwt)],
)


def _parse_typed_value(raw: str | None, value_type: str) -> Any:
    if raw is None:
        return None
    if value_type == "int":
        return int(raw)
    if value_type == "bool":
        return raw == "true"
    return raw


def _row_to_config_out(row: Any) -> ConfigOut:
    materialized = (
        row.value_json if row.value_type == "json"
        else _parse_typed_value(row.value, row.value_type)
    )
    return ConfigOut(
        namespace=row.namespace,
        key=row.key,
        value=materialized,
        value_type=row.value_type,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/config", response_model=list[ConfigOut])
async def list_config(
    namespace: str | None = None,
    cfg: ConfigService = Depends(get_config),
):
    rows = await cfg.list(namespace)
    return [_row_to_config_out(r) for r in rows]


@router.get("/config/{namespace}/{key}", response_model=ConfigOut)
async def get_config_entry(
    namespace: str,
    key: str,
    cfg: ConfigService = Depends(get_config),
):
    rows = await cfg.list(namespace)
    for r in rows:
        if r.key == key:
            return _row_to_config_out(r)
    raise HTTPException(status_code=404, detail="not found")


@router.post("/config", response_model=ConfigOut, status_code=status.HTTP_201_CREATED)
async def create_config(
    body: ConfigIn,
    cfg: ConfigService = Depends(get_config),
    identity: AdminIdentity = Depends(require_admin_jwt),
):
    existing = [r for r in await cfg.list(body.namespace) if r.key == body.key]
    if existing:
        raise HTTPException(status_code=409, detail="already exists")
    row = await cfg.set(body.namespace, body.key, body.value, body.value_type)
    log.info(
        "admin_config_set ns=%s key=%s actor=%s kind=%s",
        body.namespace, body.key, identity.email, identity.kind,
    )
    return _row_to_config_out(row)


@router.put("/config/{namespace}/{key}", response_model=ConfigOut)
async def put_config(
    namespace: str,
    key: str,
    body: ConfigInUpsert,
    cfg: ConfigService = Depends(get_config),
    identity: AdminIdentity = Depends(require_admin_jwt),
):
    if body.namespace is not None and body.namespace != namespace:
        raise HTTPException(status_code=422, detail="body ns/key mismatch URL")
    if body.key is not None and body.key != key:
        raise HTTPException(status_code=422, detail="body ns/key mismatch URL")
    row = await cfg.set(namespace, key, body.value, body.value_type)
    log.info(
        "admin_config_put ns=%s key=%s actor=%s kind=%s",
        namespace, key, identity.email, identity.kind,
    )
    return _row_to_config_out(row)


@router.delete("/config/{namespace}/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_config(
    namespace: str,
    key: str,
    cfg: ConfigService = Depends(get_config),
    identity: AdminIdentity = Depends(require_admin_jwt),
):
    existed = await cfg.delete(namespace, key)
    log.info(
        "admin_config_delete ns=%s key=%s actor=%s row_existed=%s",
        namespace, key, identity.email, existed,
    )
    return Response(status_code=204)


@router.get("/secrets", response_model=list[SecretMetadataOut])
async def list_secrets(
    namespace: str | None = None,
    cfg: ConfigService = Depends(get_config),
):
    rows = await cfg.list_secrets(namespace)
    return [
        SecretMetadataOut(
            namespace=r.namespace,
            key=r.key,
            value_type=r.value_type,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


@router.get("/secrets/{namespace}/{key}", response_model=SecretMetadataOut)
async def get_secret_metadata(
    namespace: str,
    key: str,
    cfg: ConfigService = Depends(get_config),
):
    meta = await cfg.get_secret_metadata(namespace, key)
    if meta is None:
        raise HTTPException(status_code=404, detail="not found")
    return SecretMetadataOut(
        namespace=meta.namespace,
        key=meta.key,
        value_type=meta.value_type,
        created_at=meta.created_at,
        updated_at=meta.updated_at,
    )


@router.post("/secrets", response_model=SecretMetadataOut, status_code=status.HTTP_201_CREATED)
async def create_secret(
    body: SecretIn,
    cfg: ConfigService = Depends(get_config),
    identity: AdminIdentity = Depends(require_admin_jwt),
):
    if await cfg.get_secret_metadata(body.namespace, body.key) is not None:
        raise HTTPException(status_code=409, detail="already exists")
    row = await cfg.set_secret(body.namespace, body.key, body.value, body.value_type)
    log.info(
        "admin_secret_set ns=%s key=%s actor=%s kind=%s",
        body.namespace, body.key, identity.email, identity.kind,
    )
    return SecretMetadataOut(
        namespace=row.namespace,
        key=row.key,
        value_type=row.value_type,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.put("/secrets/{namespace}/{key}", response_model=SecretMetadataOut)
async def put_secret(
    namespace: str,
    key: str,
    body: SecretInUpsert,
    cfg: ConfigService = Depends(get_config),
    identity: AdminIdentity = Depends(require_admin_jwt),
):
    if body.namespace is not None and body.namespace != namespace:
        raise HTTPException(status_code=422, detail="body ns/key mismatch URL")
    if body.key is not None and body.key != key:
        raise HTTPException(status_code=422, detail="body ns/key mismatch URL")
    row = await cfg.set_secret(namespace, key, body.value, body.value_type)
    log.info(
        "admin_secret_put ns=%s key=%s actor=%s kind=%s",
        namespace, key, identity.email, identity.kind,
    )
    return SecretMetadataOut(
        namespace=row.namespace,
        key=row.key,
        value_type=row.value_type,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("/secrets/{namespace}/{key}/reveal", response_model=SecretRevealOut)
async def reveal_secret(
    namespace: str,
    key: str,
    response: Response,
    cfg: ConfigService = Depends(get_config),
    identity: AdminIdentity = Depends(require_admin_jwt),
):
    meta = await cfg.get_secret_metadata(namespace, key)
    if meta is None:
        raise HTTPException(status_code=404, detail="not found")

    if meta.value_type == "int":
        value = await cfg.reveal_secret_int(namespace, key)
    elif meta.value_type == "bool":
        value = await cfg.reveal_secret_bool(namespace, key)
    elif meta.value_type == "json":
        value = await cfg.reveal_secret_json(namespace, key)
    else:
        value = await cfg.reveal_secret(namespace, key)

    response.headers["Cache-Control"] = "no-store, private"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Pragma"] = "no-cache"

    metrics.admin_secret_reveal_total.labels(actor_kind=identity.kind).inc()
    log.info(
        "admin_secret_reveal ns=%s key=%s actor=%s kind=%s",
        namespace, key, identity.email, identity.kind,
    )
    return SecretRevealOut(
        namespace=namespace,
        key=key,
        value=value,
        value_type=meta.value_type,
    )


@router.delete("/secrets/{namespace}/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_secret(
    namespace: str,
    key: str,
    cfg: ConfigService = Depends(get_config),
    identity: AdminIdentity = Depends(require_admin_jwt),
):
    existed = await cfg.delete_secret(namespace, key)
    log.info(
        "admin_secret_delete ns=%s key=%s actor=%s row_existed=%s",
        namespace, key, identity.email, existed,
    )
    return Response(status_code=204)
```

- [ ] **Step 12.3: Commit (tests will pass in Task 14 once deps.py is wired)**

```bash
cd /mnt/c/dashboard
git add backend/app/api/admin.py backend/tests/test_admin_api.py
git commit -m "feat(api): admin router with crud + reveal + idempotent delete"
```

---

## Chunk G — /metrics endpoint

### Task 13: `/metrics` route gated by admin auth

**Files:**
- Create: `backend/app/api/metrics.py`
- Create: `backend/tests/test_metrics.py`

- [ ] **Step 13.1: Write `app/api/metrics.py`**

```python
"""Prometheus /metrics endpoint, gated by admin auth."""

from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.core import metrics as metrics_module
from app.core.deps import require_admin_jwt

router = APIRouter(dependencies=[Depends(require_admin_jwt)])


@router.get("/metrics")
async def get_metrics() -> Response:
    data = generate_latest(metrics_module.registry)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)
```

- [ ] **Step 13.2: Write `backend/tests/test_metrics.py`**

```python
"""Tests for /metrics endpoint."""

from fastapi.testclient import TestClient

from app.core.cf_access import AdminIdentity
from app.core.deps import require_admin_jwt
from app.main import app


def test_metrics_returns_prometheus_text_with_dep_override():
    app.dependency_overrides[require_admin_jwt] = lambda: AdminIdentity(
        email="t@x", kind="user", claims={}
    )
    with TestClient(app) as c:
        resp = c.get("/metrics")
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert "cf_jwt_verification_total" in body
    assert "config_ops_total" in body


def test_metrics_without_dep_override_401():
    with TestClient(app) as c:
        assert c.get("/metrics").status_code == 401
```

- [ ] **Step 13.3: Commit**

```bash
cd /mnt/c/dashboard
git add backend/app/api/metrics.py backend/tests/test_metrics.py
git commit -m "feat(api): /metrics prometheus endpoint gated by admin auth"
```

---

## Chunk H — wiring: deps.py + main.py

### Task 14: `core/deps.py` — `require_admin_jwt` + `get_config`

**Files:**
- Modify: `backend/app/core/deps.py`

- [ ] **Step 14.1: Read current deps.py, then replace**

```python
"""FastAPI dependency providers."""

import logging
from collections.abc import AsyncGenerator

from fastapi import HTTPException, Request
from jwt.exceptions import (
    ExpiredSignatureError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidSignatureError,
    PyJWKClientError,
    PyJWTError,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.cf_access import (
    AdminIdentity,
    CFAccessVerifier,
    NoIdentityClaimError,
    client_ip_in_trusted_nets,
)
from app.core.config import settings
from app.core.db import SessionLocal

log = logging.getLogger(__name__)

_verifier = CFAccessVerifier(
    team_domain=settings.cf_access_team_domain,
    audience=settings.cf_access_audience,
    trusted_dev_nets=settings.trusted_dev_nets,
    env=settings.env,
)
_verifier.check_startup_config_smell()

_config_service = None


def set_config_service(svc) -> None:
    """Called by main.py lifespan to wire the live ConfigService singleton."""
    global _config_service
    _config_service = svc


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


def get_config():
    if _config_service is None:
        raise RuntimeError("ConfigService not initialized — lifespan startup didn't wire it")
    return _config_service


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


async def require_admin_jwt(request: Request) -> AdminIdentity:
    client_ip = _client_ip(request)

    bypass = _verifier.check_dev_bypass(client_ip)
    if bypass is not None:
        return bypass

    # Prod + non-empty trusted_dev_nets + matching IP = config smell, refuse hard
    if (
        settings.env == "prod"
        and settings.trusted_dev_nets
        and client_ip_in_trusted_nets(client_ip, settings.trusted_dev_nets)
    ):
        metrics.cf_jwt_verification_total.labels(result="dev_bypass_in_prod").inc()
        log.critical(
            "dev_bypass_attempted_in_prod client_ip=%s — refusing with 500",
            client_ip,
        )
        raise HTTPException(status_code=500, detail="internal error")

    token = request.headers.get("Cf-Access-Jwt-Assertion")
    if not token:
        metrics.cf_jwt_verification_total.labels(result="missing_header").inc()
        raise HTTPException(status_code=401, detail="missing cf-access jwt")

    try:
        identity = _verifier.verify(token, client_ip=client_ip)
        metrics.cf_jwt_verification_total.labels(result="ok").inc()
        return identity
    except ExpiredSignatureError as e:
        metrics.cf_jwt_verification_total.labels(result="expired").inc()
        raise HTTPException(status_code=401, detail="jwt expired") from e
    except InvalidSignatureError as e:
        metrics.cf_jwt_verification_total.labels(result="bad_signature").inc()
        log.warning("jwt signature verification failed")
        raise HTTPException(status_code=401, detail="jwt signature verification failed") from e
    except (InvalidIssuerError, InvalidAudienceError) as e:
        metrics.cf_jwt_verification_total.labels(result="bad_claims").inc()
        log.warning("jwt issuer/audience invalid: %s", e)
        raise HTTPException(status_code=401, detail="jwt claims invalid") from e
    except NoIdentityClaimError as e:
        metrics.cf_jwt_verification_total.labels(result="no_identity").inc()
        log.warning("jwt missing identity claim")
        raise HTTPException(status_code=401, detail="jwt missing identity claim") from e
    except PyJWKClientError as e:
        msg = str(e).lower()
        if "kid" in msg or "not found" in msg:
            metrics.cf_jwt_verification_total.labels(result="kid_miss").inc()
            raise HTTPException(status_code=401, detail="jwt signing key unknown") from e
        metrics.cf_jwt_verification_total.labels(result="jwks_fetch_fail").inc()
        log.error("jwks fetch failed: %s", e)
        raise HTTPException(status_code=503, detail="identity service unavailable") from e
    except PyJWTError as e:
        metrics.cf_jwt_verification_total.labels(result="other_jwt_error").inc()
        log.warning("jwt error: %s", e)
        raise HTTPException(status_code=401, detail="jwt error") from e
```

- [ ] **Step 14.2: Commit**

```bash
cd /mnt/c/dashboard
git add backend/app/core/deps.py
git commit -m "feat(deps): require_admin_jwt with dev-bypass guard + get_config"
```

---

### Task 15: `main.py` — register routers + lifespan + listener tasks

**Files:**
- Modify: `backend/app/main.py`

- [ ] **Step 15.1: Replace main.py**

```python
"""FastAPI app entrypoint."""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.admin import router as admin_router
from app.api.metrics import router as metrics_router
from app.core.config import settings
from app.core.crypto import get_fernet
from app.core.db import SessionLocal, engine
from app.core.deps import set_config_service
from app.core.logging import configure_logging
from app.services.config import ConfigService
from app.services.config_cache import ConfigCache

configure_logging()
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> Any:
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    config_cache = ConfigCache(redis, "config:invalidate", "config", ttl_seconds=300)
    secrets_cache = ConfigCache(redis, "config:invalidate:secrets", "secret", ttl_seconds=300)
    fernet = get_fernet(settings.secret_key, settings.secret_key_prev)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    svc = ConfigService(session_factory, config_cache, secrets_cache, fernet)
    set_config_service(svc)

    listener_config = asyncio.create_task(config_cache.run_listener())
    listener_secrets = asyncio.create_task(secrets_cache.run_listener())

    log.info("startup_ok env=%s", settings.env)
    try:
        yield
    finally:
        listener_config.cancel()
        listener_secrets.cancel()
        for t in (listener_config, listener_secrets):
            try:
                await t
            except asyncio.CancelledError:
                pass
        await redis.aclose()
        await engine.dispose()


app = FastAPI(title="Trading Dashboard", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin_router)
app.include_router(metrics_router)


@app.get("/health")
async def health() -> dict[str, str]:
    db_ok = "ok"
    try:
        async with SessionLocal() as s:
            await s.execute(text("SELECT 1"))
    except Exception:
        db_ok = "unreachable"
    return {"status": "ok", "env": settings.env, "db": db_ok}
```

- [ ] **Step 15.2: Run full backend test suite**

```bash
cd /mnt/c/dashboard/backend
uv run pytest -v 2>&1 | tail -30
```

Expected: all tests pass (crypto, cf_access, config_cache, config_service, admin_api, metrics, health).

- [ ] **Step 15.3: Commit**

```bash
cd /mnt/c/dashboard
git add backend/app/main.py
git commit -m "feat(main): register admin + metrics routers; configservice lifespan"
```

---

## Chunk I — auth integration test + real-Redis pub/sub test + migration test

### Task 16: `test_admin_auth.py` — real `require_admin_jwt` with JWKS fixture

**Files:**
- Create: `backend/tests/test_admin_auth.py`

- [ ] **Step 16.1: Write**

```python
"""Real require_admin_jwt — valid/expired/wrong signer/service-token/dev-bypass."""

import time
from unittest.mock import Mock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt import encode as jwt_encode

from app.main import app


@pytest.fixture(scope="module")
def rsa_priv():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    yield priv


@pytest.fixture(scope="module")
def rsa_priv_pem(rsa_priv):
    return rsa_priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@pytest.fixture(scope="module")
def rsa_pub_pem(rsa_priv):
    return rsa_priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


@pytest.fixture
def patch_verifier(rsa_pub_pem):
    from app.core import deps as deps_module
    mock_key = Mock()
    mock_key.key = rsa_pub_pem
    with patch.object(deps_module._verifier, "_jwks_client") as mock_client:
        mock_client.get_signing_key_from_jwt = Mock(return_value=mock_key)
        mock_client.invalidate_cache = Mock()
        with patch.object(deps_module._verifier, "team_domain", "test.cloudflareaccess.com"):
            with patch.object(deps_module._verifier, "audience", "test-aud"):
                yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _make_jwt(priv_pem: bytes, claims: dict, kid: str = "test-kid") -> str:
    return jwt_encode(claims, priv_pem, algorithm="RS256", headers={"kid": kid})


def test_missing_header_401(client):
    assert client.get("/api/admin/config").status_code == 401


def test_malformed_jwt_401(client, patch_verifier):
    r = client.get(
        "/api/admin/config",
        headers={"Cf-Access-Jwt-Assertion": "not.a.jwt"},
    )
    assert r.status_code == 401


def test_valid_email_jwt_200(client, patch_verifier, rsa_priv_pem):
    now = int(time.time())
    tok = _make_jwt(
        rsa_priv_pem,
        {
            "email": "alice@example.com",
            "iss": "https://test.cloudflareaccess.com",
            "aud": "test-aud",
            "iat": now,
            "exp": now + 300,
        },
    )
    r = client.get("/api/admin/config", headers={"Cf-Access-Jwt-Assertion": tok})
    assert r.status_code == 200


def test_service_token_common_name_200(client, patch_verifier, rsa_priv_pem):
    now = int(time.time())
    tok = _make_jwt(
        rsa_priv_pem,
        {
            "common_name": "dashboard-ci-smoke",
            "iss": "https://test.cloudflareaccess.com",
            "aud": "test-aud",
            "iat": now,
            "exp": now + 300,
        },
    )
    r = client.get("/api/admin/config", headers={"Cf-Access-Jwt-Assertion": tok})
    assert r.status_code == 200


def test_jwt_without_identity_claim_401(client, patch_verifier, rsa_priv_pem):
    now = int(time.time())
    tok = _make_jwt(
        rsa_priv_pem,
        {
            "iss": "https://test.cloudflareaccess.com",
            "aud": "test-aud",
            "iat": now,
            "exp": now + 300,
        },
    )
    r = client.get("/api/admin/config", headers={"Cf-Access-Jwt-Assertion": tok})
    assert r.status_code == 401
    assert "identity" in r.json()["detail"].lower()


def test_expired_jwt_401(client, patch_verifier, rsa_priv_pem):
    now = int(time.time())
    tok = _make_jwt(
        rsa_priv_pem,
        {
            "email": "a@b",
            "iss": "https://test.cloudflareaccess.com",
            "aud": "test-aud",
            "iat": now - 600,
            "exp": now - 60,
        },
    )
    r = client.get("/api/admin/config", headers={"Cf-Access-Jwt-Assertion": tok})
    assert r.status_code == 401
    assert "expired" in r.json()["detail"].lower()
```

- [ ] **Step 16.2: Run**

```bash
cd /mnt/c/dashboard/backend
uv run pytest tests/test_admin_auth.py -v 2>&1 | tail -15
```

Expected: `6 passed`.

- [ ] **Step 16.3: Commit**

```bash
cd /mnt/c/dashboard
git add backend/tests/test_admin_auth.py
git commit -m "test(auth): integration tests for require_admin_jwt"
```

---

### Task 17: `test_migration.py` — upgrade/downgrade round-trip + CHECK constraint

**Files:**
- Create: `backend/tests/test_migration.py`

- [ ] **Step 17.1: Write**

```python
"""Alembic migration round-trip + CHECK constraint enforcement."""

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings


def _sync_url() -> str:
    return settings.database_url.replace("+asyncpg", "")


def test_upgrade_head_creates_tables():
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", _sync_url())
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")  # idempotent


@pytest.mark.asyncio
async def test_both_tables_present():
    eng = create_async_engine(settings.database_url)
    try:
        async with eng.connect() as conn:
            res = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' "
                    "AND table_name IN ('app_config','app_secrets')"
                )
            )
            tables = {r[0] for r in res.all()}
        assert tables == {"app_config", "app_secrets"}
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_check_constraint_value_exclusive():
    eng = create_async_engine(settings.database_url)
    try:
        async with eng.begin() as conn:
            with pytest.raises(IntegrityError):
                await conn.execute(
                    text(
                        "INSERT INTO app_config (namespace, key, value, value_json, value_type) "
                        "VALUES ('x', 'y', 'both', '{}'::jsonb, 'str')"
                    )
                )
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_check_constraint_value_type_enum():
    eng = create_async_engine(settings.database_url)
    try:
        async with eng.begin() as conn:
            with pytest.raises(IntegrityError):
                await conn.execute(
                    text(
                        "INSERT INTO app_config (namespace, key, value, value_type) "
                        "VALUES ('x', 'y', 'z', 'FLOAT')"
                    )
                )
    finally:
        await eng.dispose()
```

- [ ] **Step 17.2: Run**

```bash
cd /mnt/c/dashboard/backend
uv run pytest tests/test_migration.py -v 2>&1 | tail -10
```

Expected: `4 passed`.

- [ ] **Step 17.3: Commit**

```bash
cd /mnt/c/dashboard
git add backend/tests/test_migration.py
git commit -m "test(db): migration round-trip + check-constraint assertions"
```

---

### Task 18: Opt-in real-Redis pub/sub fidelity test + CI service

**Files:**
- Modify: `backend/tests/test_config_cache.py`
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 18.1: Append real-Redis test to `test_config_cache.py`**

At the end of `backend/tests/test_config_cache.py`, append:

```python
import os

import redis.asyncio as real_redis_asyncio


@pytest.mark.skipif(
    os.environ.get("CI_USE_REAL_REDIS") != "1",
    reason="set CI_USE_REAL_REDIS=1 to run against a real redis:7-alpine service",
)
@pytest.mark.asyncio
async def test_real_redis_pubsub_fidelity():
    url = os.environ.get("CI_REDIS_URL", "redis://localhost:6379/0")
    r = real_redis_asyncio.from_url(url, decode_responses=False)
    try:
        cache = ConfigCache(
            redis=r, channel="config:invalidate:real", kind_label="config", ttl_seconds=60
        )
        cache.set(("ns", "k"), "stale")

        task = asyncio.create_task(cache.run_listener())
        await asyncio.sleep(0.2)
        await r.publish("config:invalidate:real", b"ns|k")
        await asyncio.sleep(0.3)
        assert cache.get(("ns", "k")) is None

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    finally:
        await r.aclose()
```

- [ ] **Step 18.2: Add redis service to CI workflow**

Read `.github/workflows/ci.yml`. In the `backend` job's `services:` block (which currently has only `postgres`), add a `redis:` service after `postgres`:

```yaml
      redis:
        image: redis:7-alpine
        ports: ['6379:6379']
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 5s
          --health-retries 10
```

And in the `Pytest` step's `env:` block, add two lines:

```yaml
          CI_USE_REAL_REDIS: '1'
          CI_REDIS_URL: redis://localhost:6379/0
```

- [ ] **Step 18.3: Commit**

```bash
cd /mnt/c/dashboard
git add backend/tests/test_config_cache.py .github/workflows/ci.yml
git commit -m "test(ci): real redis service + opt-in pubsub fidelity test"
```

---

## Chunk J — .env.example + extended Playwright smoke

### Task 19: Update `.env.example` with 4 new keys

**Files:**
- Modify: `.env.example`

- [ ] **Step 19.1: Append to `.env.example`**

Read existing file, then append at the end:

```
# --- Phase 2 — Auth + DB-backed config service ---
# CF Access team domain (e.g. kiusinghung.cloudflareaccess.com) and the Dashboard
# app's "Application Audience Tag" (from CF dashboard → Access → Apps → Dashboard).
# Both required in prod; dev leaves them empty and uses the dev-bypass path below.
CF_ACCESS_TEAM_DOMAIN=
CF_ACCESS_AUDIENCE=

# Optional — set during APP_SECRET_KEY rotation windows. When set, MultiFernet
# decrypts ciphertexts encrypted with the OLD key. Clear this once all rows are
# re-encrypted under the new primary.
APP_SECRET_KEY_PREV=

# Dev-mode bypass — require BOTH env=dev AND client IP in this CIDR list to bypass
# CF Access JWT verification. Empty = bypass never fires (safest; prod default).
# Typical dev value: ["10.10.0.0/24"]  (covers the WireGuard mesh).
TRUSTED_DEV_NETS=[]
```

- [ ] **Step 19.2: Commit**

```bash
cd /mnt/c/dashboard
git add .env.example
git commit -m "docs(env): add phase 2 bootstrap keys (cf-access, prev, trusted-dev)"
```

---

### Task 20: Extend Playwright smoke test with admin round-trip

**Files:**
- Modify: `tests/e2e/smoke.spec.ts`

- [ ] **Step 20.1: Append two new test blocks inside the existing describe**

Read `tests/e2e/smoke.spec.ts`. Inside the `test.describe('Phase 1 smoke', () => { ... })` block, before its closing brace, add:

```ts
  test('admin config round-trip via service token', async ({ request }) => {
    const ns = 'test';
    const key = `phase2_smoke_${Date.now()}`;
    const postResp = await request.post(`/api/admin/config`, {
      data: { namespace: ns, key, value: 'ok', value_type: 'str' },
    });
    expect(postResp.status()).toBe(201);

    const getResp = await request.get(`/api/admin/config/${ns}/${key}`);
    expect(getResp.status()).toBe(200);
    expect((await getResp.json()).value).toBe('ok');

    const delResp = await request.delete(`/api/admin/config/${ns}/${key}`);
    expect(delResp.status()).toBe(204);
  });

  test('admin secret reveal via service token', async ({ request }) => {
    const ns = 'test';
    const key = `phase2_secret_${Date.now()}`;
    const postResp = await request.post(`/api/admin/secrets`, {
      data: { namespace: ns, key, value: 's3cr3t-value', value_type: 'str' },
    });
    expect(postResp.status()).toBe(201);

    const revealResp = await request.post(
      `/api/admin/secrets/${ns}/${key}/reveal`,
    );
    expect(revealResp.status()).toBe(200);
    expect((await revealResp.json()).value).toBe('s3cr3t-value');
    expect(revealResp.headers()['cache-control']).toContain('no-store');

    const delResp = await request.delete(`/api/admin/secrets/${ns}/${key}`);
    expect(delResp.status()).toBe(204);
  });
```

- [ ] **Step 20.2: Commit (don't run locally — production must ship first)**

```bash
cd /mnt/c/dashboard
git add tests/e2e/smoke.spec.ts
git commit -m "test(e2e): extend smoke with admin config + secret reveal roundtrips"
```

---

## Chunk K — close-out (docs + tag + push + verify)

### Task 21: Update CLAUDE.md — "Configuration Storage" now active

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 21.1: Find + edit**

In `CLAUDE.md`, find the section `## Configuration Storage`. Update the parenthetical "(Phase 2+; Phase 0 has no DB-backed config yet.)" to "(Phase 2+; active as of v0.2.0.)". Update "(Phase 2+)" references to "(from Phase 2 onward)". At the end of the section, append:

```
**As of v0.2.0:** `ConfigService` is live. Read values via:

    from app.core.deps import get_config
    svc = get_config()
    value = await svc.get("namespace", "key", default="...")
    secret = await svc.reveal_secret("namespace", "secret_key")

Admin writes via `POST /api/admin/config` (curl from CI via CF Access service token, or browser via Google login cookie). Secret plaintext reveal only via `POST /api/admin/secrets/:ns/:key/reveal` (GET returns metadata only; reveal is audit-logged).
```

- [ ] **Step 21.2: Commit**

```bash
cd /mnt/c/dashboard
git add CLAUDE.md
git commit -m "docs(claude): configuration storage is live as of v0.2.0"
```

---

### Task 22: Update CHANGELOG.md with v0.2.0 entry

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 22.1: Replace `## [Unreleased]` block**

Read `CHANGELOG.md`. Replace:

```md
## [Unreleased]
```

with:

```md
## [Unreleased]

## [0.2.0] — 2026-04-22
### Added
- DB-backed runtime config (`app_config` TEXT/JSONB; `app_secrets` Fernet).
- ConfigService with per-worker in-memory cache + 5-min TTL + Redis pub/sub invalidation.
- CF Access JWT verification via `PyJWKClient`; accepts `email` or `common_name` claims.
- Dev-mode auth bypass double-gated by `APP_ENV=dev` AND IP in `TRUSTED_DEV_NETS`.
- `MultiFernet([primary, prev])` rolling-rotation support via `APP_SECRET_KEY_PREV`.
- `/api/admin/config` CRUD + `/api/admin/secrets` CRUD + `/api/admin/secrets/:ns/:key/reveal`.
- `/metrics` Prometheus endpoint (gated by admin auth).
- 8 new backend test files; coverage ≥ 85%; 100% on `cf_access` + `crypto`.
- `scripts/entrypoint.sh` — runs `alembic upgrade head` before `uvicorn`.
- Playwright smoke extended with two admin round-trip tests.

### Changed
- Backend Dockerfile uses ENTRYPOINT + CMD split so compose `command:` overrides still run migrations.
- `.env.example` documents 4 new keys: `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUDIENCE`, `APP_SECRET_KEY_PREV`, `TRUSTED_DEV_NETS`.
- CI workflow adds a `redis:7-alpine` service for one pub/sub fidelity test.
```

- [ ] **Step 22.2: Commit**

```bash
cd /mnt/c/dashboard
git add CHANGELOG.md
git commit -m "docs: changelog [0.2.0] entry for phase 2"
```

---

### Task 23: Update TASKS.md — mark Phase 2 complete

**Files:**
- Modify: `TASKS.md`

- [ ] **Step 23.1: Edit the Phase 2 line**

Replace:

```
## Phase 2 — Auth + DB-backed config service (app_config, app_secrets)  *(next)*
```

with:

```
## Phase 2 — Auth + DB-backed config service  *(complete — v0.2.0 · 2026-04-22)*
- [x] Alembic migration 0001 — app_config + app_secrets tables with CHECK constraints
- [x] `core/crypto.py` — Fernet via HKDF + MultiFernet prev-key rotation
- [x] `core/cf_access.py` — CF Access JWT verifier via PyJWKClient + dev-bypass double-gate
- [x] `core/metrics.py` — prometheus-client counters + gauges
- [x] `services/config.py` + `services/config_cache.py` — ConfigService + Redis pub/sub invalidation
- [x] `api/admin.py` — full CRUD + reveal endpoint + idempotent DELETE + PUT URL-vs-body 422
- [x] `api/metrics.py` — /metrics endpoint gated by admin auth
- [x] 8 new test files; ≥85% coverage (100% on cf_access + crypto)
- [x] Backend container `scripts/entrypoint.sh` runs alembic upgrade before uvicorn
- [x] Extended Playwright smoke with admin round-trip tests
- [x] .env.example updated with 4 new keys
- [x] v0.2.0 tagged and pushed

## Phase 3 — Frontend shell (mocks)  *(next)*
```

(Remove existing `## Phase 3 — Frontend shell (mocks)` line if present earlier.)

- [ ] **Step 23.2: Commit**

```bash
cd /mnt/c/dashboard
git add TASKS.md
git commit -m "docs: mark phase 2 complete in tasks.md"
```

---

### Task 24: Pre-flight sweep — lint + typecheck + test + compose + build

**Files:** (none — verification only)

- [ ] **Step 24.1: Backend sweep**

```bash
cd /mnt/c/dashboard/backend
uv run ruff check . && uv run ruff format --check . && uv run mypy app/ && uv run pytest --cov=app --cov-report=term-missing 2>&1 | tail -30
```

Expected: all green; coverage ≥ 85% overall; `app/core/cf_access.py` + `app/core/crypto.py` at 100%. If below target, add more test cases and re-commit.

- [ ] **Step 24.2: Frontend sweep**

```bash
cd /mnt/c/dashboard/frontend
export PATH="$HOME/.npm-global/bin:$PATH"
pnpm lint && pnpm stylelint && pnpm typecheck && pnpm test && pnpm build 2>&1 | tail -10
```

Expected: all green (Phase 2 doesn't change frontend source).

- [ ] **Step 24.3: Compose config + docker build**

```bash
cd /mnt/c/dashboard
docker compose -f docker-compose.prod.yml config > /dev/null && echo "compose prod ✓"
docker compose build backend 2>&1 | tail -10
```

Expected: compose validates; backend image builds.

---

### Task 25: Push + tag v0.2.0 + watch CI + verify production

**Files:** (none — git + CI + verification)

- [ ] **Step 25.1: Push commits**

```bash
cd /mnt/c/dashboard
git log --oneline origin/main..HEAD | wc -l
git push origin main 2>&1 | tail -5
```

Expected: push succeeds; `main -> main` ref moves forward.

- [ ] **Step 25.2: Tag v0.2.0**

```bash
cd /mnt/c/dashboard
git tag -a v0.2.0 -m "Phase 2: auth + db-backed config service"
git push origin v0.2.0 2>&1 | tail -3
```

Expected: `[new tag] v0.2.0 -> v0.2.0`.

- [ ] **Step 25.3: Watch CI + Deploy**

```bash
cd /mnt/c/dashboard
gh run list --limit 4 --repo josephhungkk/trading-dashboard 2>&1 | head -4
```

Get the two most recent run IDs (CI + Deploy), then:

```bash
# Replace <CI_RUN_ID> and <DEPLOY_RUN_ID> with the actual IDs above
until gh run view <CI_RUN_ID>     --json status --jq '.status' 2>/dev/null | grep -q completed; do sleep 15; done
until gh run view <DEPLOY_RUN_ID> --json status --jq '.status' 2>/dev/null | grep -q completed; do sleep 15; done
gh run view <CI_RUN_ID>     --json conclusion --jq '.conclusion'
gh run view <DEPLOY_RUN_ID> --json conclusion --jq '.conclusion'
```

Expected: both print `success`.

- [ ] **Step 25.4: Criterion — migration applied in prod**

```bash
ssh -p 2222 trader@88.208.197.219 'docker compose -f /home/trader/trading-dashboard/docker-compose.prod.yml exec -T backend /app/.venv/bin/alembic current' 2>&1 | tail -3
```

Expected: `0001 (head)`.

- [ ] **Step 25.5: Criterion — curl admin config + reveal from NUC**

```bash
source ~/.secrets/cf-access-env

# config
curl -sS -w "\nHTTP %{http_code}\n" \
  -X POST https://dashboard.kiusinghung.com/api/admin/config \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"namespace":"test","key":"phase2","value":"ok","value_type":"str"}'

curl -sS -w "\nHTTP %{http_code}\n" \
  https://dashboard.kiusinghung.com/api/admin/config/test/phase2 \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET"

# secret + reveal
curl -sS -w "\nHTTP %{http_code}\n" \
  -X POST https://dashboard.kiusinghung.com/api/admin/secrets \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"namespace":"test","key":"ps2","value":"s3cr3t","value_type":"str"}'

curl -sS -i -X POST \
  https://dashboard.kiusinghung.com/api/admin/secrets/test/ps2/reveal \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  | head -20
```

Expected: `201`, `200 + value=ok`, `201`, `200 + cache-control: no-store + value=s3cr3t`.

- [ ] **Step 25.6: Criterion — 401 without token**

```bash
curl -sS -w "\nHTTP %{http_code}\n" -o /dev/null \
  https://dashboard.kiusinghung.com/api/admin/config
```

Expected: `HTTP 302` (CF Access redirect) — gate still works.

- [ ] **Step 25.7: Criterion — multi-worker invalidation**

```bash
ssh -p 2222 trader@88.208.197.219 '
cd /home/trader/trading-dashboard
docker compose -f docker-compose.prod.yml up -d --scale backend=2
sleep 15
docker compose -f docker-compose.prod.yml ps | grep backend
'
```

Expected: 2 backend containers, both healthy.

```bash
source ~/.secrets/cf-access-env
curl -sS -X POST https://dashboard.kiusinghung.com/api/admin/config \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"namespace":"test","key":"multi","value":"v1","value_type":"str"}' \
  -w "\n"
curl -sS -X PUT https://dashboard.kiusinghung.com/api/admin/config/test/multi \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"value":"v2","value_type":"str"}'
sleep 5
for i in 1 2 3 4 5; do
  curl -sSf https://dashboard.kiusinghung.com/api/admin/config/test/multi \
    -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
    -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
    | grep -oE '"value":"[^"]+"'
done

ssh -p 2222 trader@88.208.197.219 'docker compose -f /home/trader/trading-dashboard/docker-compose.prod.yml up -d --scale backend=1'
```

Expected: all 5 reads show `"value":"v2"`.

- [ ] **Step 25.8: Criterion — /metrics**

```bash
source ~/.secrets/cf-access-env
curl -sS https://dashboard.kiusinghung.com/metrics \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  | head -30
```

Expected: prometheus text with `cf_jwt_verification_total` and `config_ops_total` counters.

- [ ] **Step 25.9: Cleanup test data**

```bash
source ~/.secrets/cf-access-env
for path in "test/phase2" "test/multi"; do
  curl -sS -X DELETE "https://dashboard.kiusinghung.com/api/admin/config/$path" \
    -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
    -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET"
done
curl -sS -X DELETE https://dashboard.kiusinghung.com/api/admin/secrets/test/ps2 \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET"
```

Expected: all return 204.

---

## Self-review (author's checklist, run before execution handoff)

**Spec coverage** — each §8a item has a task:

| Spec item | Task(s) |
|---|---|
| Two tables + reversible migration | 4, 5 |
| ConfigService + cache + pub/sub + Fernet | 6, 8, 9, 10 |
| CF Access JWT + dev-bypass double-gate | 7, 14 |
| Admin REST API + reveal + idempotent DELETE + PUT 422 | 11, 12 |
| /metrics endpoint | 8, 13 |
| 8 test files + ≥85% coverage + 100% auth/crypto | 6, 7, 9, 10, 12, 13, 16, 17, 24 |
| `scripts/entrypoint.sh` | 3 |
| `.env.example` 4 new keys | 19 |
| Playwright smoke extended | 20 |
| CLAUDE.md update | 21 |
| CHANGELOG + TASKS + tag v0.2.0 | 22, 23, 25 |
| Real-Redis pub/sub test in CI | 18 |

All spec items covered.

**Placeholder scan** — no `TBD`, `TODO`, `implement later`, or unexplained "Similar to Task N". Every code block is complete.

**Type consistency** — `AdminIdentity`, `ConfigService`, `ConfigCache`, `SecretMetadata`, `ConfigTypeError`, `CFAccessVerifier`, `NoIdentityClaimError` defined once; references match.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-22-phase2-auth-config-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, two-stage review (spec-compliance + code-quality) after each, plus the per-task review chain codified in CLAUDE.md §"Step 6" (language-specific reviewer + security-reviewer + database-reviewer + type-design-analyzer at commit boundaries for auth/secret/schema paths). Fast iteration, clean context per task.

**2. Inline Execution** — Execute tasks in this session via `superpowers:executing-plans`, batch with checkpoints for review.

Which approach?
