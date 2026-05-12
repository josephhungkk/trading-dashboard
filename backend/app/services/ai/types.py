"""Phase 11a-B: request/response shapes for services/ai/."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.services.ai.capabilities import AICapability


class ToolDef(BaseModel):
    """HIGH-4 forward-compat placeholder. v0.11.0 rejects non-None."""

    name: str
    description: str
    parameters: dict[str, Any]


class CompletionRequest(BaseModel):
    messages: list[dict[str, str]] = Field(..., min_length=1)
    capability: AICapability
    caller: str = Field(..., description="consumer name for cost ledger")
    response_format: dict[str, Any] | None = None
    max_tokens: int = Field(default=1024, ge=1, le=32768)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    tools: list[ToolDef] | None = None  # HIGH-4 — rejected with 501 at v0.11.0
    force_local_only: bool = False  # CRIT-3 — parser path


class CompletionResult(BaseModel):
    request_id: UUID
    text: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    wall_time_ms: int
    fallback_chain: list[FallbackHop] = Field(default_factory=list)


class FallbackHop(BaseModel):
    """MED-8 — record each attempted provider/model + reason for skipping."""

    from_provider: str
    from_model: str
    reason: str


class Chunk(BaseModel):
    """Streaming chunk shape."""

    delta: str
    finish_reason: Literal["stop", "length", "tool_calls", None] = None


class JobStatus(BaseModel):
    id: UUID
    status: Literal["pending", "warming", "inferring", "completed", "failed", "cancelled"]
    response: CompletionResult | None = None
    error: str | None = None


CompletionResult.model_rebuild()
