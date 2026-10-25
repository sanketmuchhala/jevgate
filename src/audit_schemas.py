from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AuditEvent(BaseModel):
    event_id: str
    case_id: str
    version_number: int
    event_type: str
    actor: str = Field(description="Request-supplied reviewer name or a system actor; not a verified identity")
    payload: dict[str, object]
    created_at: str


class ReviewerIdentity(BaseModel):
    reviewer: str
    office: str | None = None
    last_seen_at: str
    identity_source: Literal["REQUEST_SUPPLIED"] = "REQUEST_SUPPLIED"
