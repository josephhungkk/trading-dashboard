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
