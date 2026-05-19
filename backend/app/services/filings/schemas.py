from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, model_validator


class FilingRow(BaseModel):
    id: uuid.UUID
    instrument_id: int | None = None
    canonical_id: str | None = None
    source: str
    form_type: str
    filing_date: datetime
    period_of_report: date | None = None
    title: str
    url: str
    raw_text: str | None = None
    llm_summary: str | None = None
    llm_summary_at: datetime | None = None
    captured_at: datetime

    @model_validator(mode="after")
    def instrument_or_canonical_required(self) -> FilingRow:
        if self.instrument_id is None and self.canonical_id is None:
            raise ValueError("instrument_id or canonical_id must be set")
        return self

    model_config = {"from_attributes": True}


class FilingFeedCursorRow(BaseModel):
    source: str
    last_cursor: str
    updated_at: datetime

    model_config = {"from_attributes": True}
