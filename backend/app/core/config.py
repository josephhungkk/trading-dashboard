"""Bootstrap config — only values needed before the DB is reachable.

DB-backed ConfigService (app_config/app_secrets) lands in Phase 2.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = Field(default="dev", alias="APP_ENV")
    secret_key: str = Field(alias="APP_SECRET_KEY", min_length=32)
    secret_key_prev: str | None = Field(default=None, alias="APP_SECRET_KEY_PREV")
    cors_origins: list[str] = Field(default_factory=list, alias="APP_CORS_ORIGINS")
    database_url: str = Field(alias="DATABASE_URL")
    postgres_pool_size: int = Field(default=5, alias="POSTGRES_POOL_SIZE")
    postgres_max_overflow: int = Field(default=10, alias="POSTGRES_MAX_OVERFLOW")
    pg_ssl_cert_path: str | None = Field(default=None, alias="PG_SSL_CERT_PATH")
    pg_ssl_key_path: str | None = Field(default=None, alias="PG_SSL_KEY_PATH")
    pg_ssl_ca_path: str | None = Field(default=None, alias="PG_SSL_CA_PATH")
    postgres_pool_size_scheduler: int = Field(default=10, alias="POSTGRES_POOL_SIZE_SCHEDULER")
    redis_password: str = Field(alias="REDIS_PASSWORD")
    redis_url: str = Field(alias="REDIS_URL")

    # Phase 2 — CF Access JWT verification
    cf_access_team_domain: str = Field(default="", alias="CF_ACCESS_TEAM_DOMAIN")
    cf_access_audience: str = Field(default="", alias="CF_ACCESS_AUDIENCE")

    # Phase 2 — dev-mode bypass. BOTH env=dev AND IP-match required.
    # Empty list (default) = bypass never fires.
    trusted_dev_nets: list[str] = Field(default_factory=list, alias="TRUSTED_DEV_NETS")


settings = Settings()  # type: ignore[call-arg]
