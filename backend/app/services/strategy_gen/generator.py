from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

import structlog

from app.services.ai.capabilities import AICapability
from app.services.ai.router import AICompletionClient
from app.services.ai.types import CompletionRequest
from app.services.strategy_gen.metrics import (
    strategy_gen_generated_total,
    strategy_gen_sandbox_latency_seconds,
)
from app.services.strategy_gen.sandbox import SandboxValidationResult, SandboxValidator

logger = structlog.get_logger(__name__)

BASESTR_INTERFACE_CONTRACT = """
Generated strategies must define exactly one concrete subclass of app.bot.base.BaseStrategy.
BaseStrategy requires synchronous on_start(self) -> None and on_bar(self, bar: BarEvent) -> None.
Optional synchronous hooks are on_fill(self, fill: FillEvent) -> None, on_stop(self) -> None,
and on_advisor_reject(self, intent: OrderIntent, decision: AdvisorDecision) -> None.
Generated code must not perform network access, file I/O, subprocess execution, dynamic imports,
eval, exec, or direct access to os, sys, or socket modules.
"""

ALLOWED_IMPORTS_DEFAULT = ["numpy", "pandas", "ta", "math", "decimal", "collections", "itertools"]
PROHIBITED_PATTERNS = [
    "network access",
    "file I/O",
    "subprocess",
    "__import__",
    "eval",
    "exec",
    "os module",
    "sys module",
    "socket",
]


@dataclass(frozen=True)
class StrategyGenerationRequest:
    prompt: str
    caller: str
    jwt_subject: str
    max_tokens: int = 4096
    temperature: float = 0.2
    context: dict[str, Any] | None = None


@dataclass(frozen=True)
class StrategyGenerationResult:
    source: str
    validation: SandboxValidationResult


class StrategyGenerator:
    def __init__(
        self,
        ai_client: AICompletionClient,
        sandbox: SandboxValidator | None = None,
    ) -> None:
        self.ai_client = ai_client
        self.sandbox = sandbox or SandboxValidator(allowed_imports=ALLOWED_IMPORTS_DEFAULT)

    async def generate(self, req: StrategyGenerationRequest) -> StrategyGenerationResult:
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": self._user_prompt(req)},
        ]
        completion_req = CompletionRequest(
            messages=messages,
            capability=AICapability.CODING,
            caller=req.caller,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
        )

        response = await self.ai_client.complete(
            completion_req,
            jwt_subject=req.jwt_subject,
        )

        source = self._extract_code(response.text)

        t0 = time.monotonic()
        validation = self.sandbox.validate_code(source)
        strategy_gen_sandbox_latency_seconds.observe(time.monotonic() - t0)

        outcome = "validated" if validation.ok else "rejected"
        strategy_gen_generated_total.labels(outcome=outcome).inc()

        if not validation.ok:
            logger.info("strategy_gen_sandbox_rejected", errors=validation.errors)

        return StrategyGenerationResult(source=source, validation=validation)

    def _system_prompt(self) -> str:
        return "\n".join(
            [
                "You generate Python trading strategy source code only.",
                BASESTR_INTERFACE_CONTRACT.strip(),
                f"Allowed imports: {', '.join(ALLOWED_IMPORTS_DEFAULT)}.",
                f"Prohibited patterns: {', '.join(PROHIBITED_PATTERNS)}.",
            ]
        )

    def _user_prompt(self, req: StrategyGenerationRequest) -> str:
        if not req.context:
            return req.prompt
        return f"{req.prompt}\n\nContext:\n{req.context}"

    def _extract_code(self, text: str) -> str:
        stripped = text.strip()
        if "```" not in stripped:
            return stripped
        parts = stripped.split("```")
        for part in parts[1::2]:
            candidate = part.strip()
            if candidate.startswith("python"):
                candidate = candidate.removeprefix("python").strip()
            if candidate:
                return candidate
        return stripped


def compute_prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()
