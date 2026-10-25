from __future__ import annotations
from enum import Enum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .schemas import TriageDecision, Verdict


class AudienceOption(str, Enum):
    INTERNAL_TEAM = "internal_team"
    INSTITUTION_WIDE = "institution_wide"
    PARTNER_GOVERNMENT = "partner_government"
    PUBLIC_RELEASE = "public_release"
    INFER = "infer"


class SubmissionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intended_audience: AudienceOption = AudienceOption.INFER
    release_requested: bool | None = Field(default=None, description="Explicit override for release_requested")
    purpose: str = Field(default="", max_length=500, description="Why the document is being shared or reviewed")
    proposed_action: str = Field(default="", max_length=200, description="e.g. public_disclosure, share_with_ministry, archive")
    source_unit: str = Field(default="", max_length=120)


class EvidenceSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    condition_id: str
    quote: str
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    rationale: str = ""
    source: Literal["SYSTEM_TWO", "LOCATOR"] = "SYSTEM_TWO"


class DispositionType(str, Enum):
    PENDING = "PENDING"
    ACCEPT_FINDING = "ACCEPT_FINDING"
    FALSE_POSITIVE_OVERRIDE = "FALSE_POSITIVE_OVERRIDE"
    WAIVE_RISK = "WAIVE_RISK"
    CARRIED_FORWARD = "CARRIED_FORWARD"


class CaseStatus(str, Enum):
    OPEN = "OPEN"
    CLEARED_BY_MACHINE = "CLEARED_BY_MACHINE"
    APPROVED_BY_HUMAN = "APPROVED_BY_HUMAN"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    BLOCKED_FINAL = "BLOCKED_FINAL"


class RuleDispositionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_id: str
    office: str
    disposition: DispositionType = DispositionType.PENDING
    reviewer: str = ""
    rationale: str = ""
    updated_at: str | None = None
    carried_from_version: int | None = None
    invalidation_note: str | None = None


class CaseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    document: str = Field(min_length=1, max_length=100_000)
    context: SubmissionContext = Field(default_factory=SubmissionContext)
    mode: Literal["fast", "hybrid"] = "hybrid"


class VersionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document: str = Field(min_length=1, max_length=100_000)
    context: SubmissionContext | None = None
    mode: Literal["fast", "hybrid"] = "hybrid"
    change_note: str = Field(default="", max_length=500)


class RuleDispositionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    disposition: Literal["ACCEPT_FINDING", "FALSE_POSITIVE_OVERRIDE", "WAIVE_RISK"]
    reviewer: str = Field(min_length=1, max_length=120)
    office: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=3, max_length=2000)


class CaseAssignmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assigned_office: str | None = Field(default=None, max_length=120)
    assigned_reviewer: str | None = Field(default=None, max_length=120)
    status: CaseStatus | None = None
    reviewer: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=1000)


class CaseVersionDetail(BaseModel):
    version_number: int
    created_at: str
    change_note: str
    document: str
    document_sha256: str
    context: SubmissionContext
    decision: TriageDecision
    evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    rule_dispositions: dict[str, RuleDispositionRecord]
    context_warnings: list[str] = Field(default_factory=list)


class CaseSummary(BaseModel):
    case_id: str
    title: str
    created_at: str
    updated_at: str
    current_version: int
    status: CaseStatus
    assigned_office: str | None = None
    assigned_reviewer: str | None = None
    machine_verdict: Verdict
    decided_by: Literal["SYSTEM_ONE", "SYSTEM_TWO", "PENDING_HUMAN"]
    priority: Literal["EXPEDITE", "STANDARD"]
    risk_index: float
    offices: list[str]
    review_offices: list[str]


class CaseDetail(CaseSummary):
    active_version: CaseVersionDetail
    version_history: list[dict[str, object]] = Field(default_factory=list)
