"""Phase 11b chunk A: exceptions for alerts service.

These map to specific HTTP status codes in app/api/alerts.py (chunk C):
- RuleNotFoundError + RuleCrossSubjectError → 404 with IDENTICAL body
  (existence-oracle defence matching 11a /api/ai/jobs/{id})
- AlreadyActiveError → 409 (confirm called on already-active rule)
- PredicateValidationError lives in predicates.py, re-exported here
- ParserUnavailableError → 503
- ParseFailedError → 200 with parse_status='failed' (graceful-degrade)
- WebhookUrlRejected → DeliveryOutcome.failed (chunk C CRIT-1)
"""

from __future__ import annotations


class RuleNotFoundError(Exception):
    """Rule with given id does not exist (or is soft-deleted)."""


class RuleCrossSubjectError(Exception):
    """Rule exists but belongs to a different jwt_subject — must surface as 404."""


class AlreadyActiveError(Exception):
    """confirm called on an already-active rule (API maps to 409)."""


class ParserUnavailableError(Exception):
    """AI router unavailable for parsing."""


class ParseFailedError(Exception):
    def __init__(self, partial_predicate: dict[str, object] | None, message: str) -> None:
        super().__init__(message)
        self.partial_predicate = partial_predicate


class WebhookUrlRejected(Exception):  # noqa: N818 — name pinned by spec §8 webhook SSRF
    def __init__(self, reason: str) -> None:
        super().__init__(f"webhook url rejected: {reason}")
        self.reason = reason
