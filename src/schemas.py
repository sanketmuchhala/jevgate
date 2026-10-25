from __future__ import annotations
from enum import Enum
from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Question battery (what System-1 is asked about every document)
# ---------------------------------------------------------------------------

class ChoiceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["choice"] = "choice"
    instructions: str
    options: dict[str, str] = Field(min_length=2, description="Option name -> when it applies")


class ScoreSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["score"] = "score"
    instructions: str
    levels: list[str] = Field(min_length=2, max_length=10, description="Ordered rubric, level 0 first")


class NoulSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["noul"] = "noul"
    instructions: str


QuestionSpec = Annotated[Union[ChoiceSpec, ScoreSpec, NoulSpec], Field(discriminator="type")]


# ---------------------------------------------------------------------------
# Signals (System-1 answers, normalized)
# ---------------------------------------------------------------------------

class ChoiceSignal(BaseModel):
    type: Literal["choice"] = "choice"
    choice: str
    probabilities: dict[str, float]
    confidence: float = Field(ge=0.0, le=1.0)


class ScoreSignal(BaseModel):
    type: Literal["score"] = "score"
    score: float = Field(description="Expected level, may fall between levels")
    probabilities: dict[int, float]
    confidence: float = Field(ge=0.0, le=1.0)


class NoulSignal(BaseModel):
    type: Literal["noul"] = "noul"
    noul: float = Field(ge=0.0, le=1.0, description="Probability the statement is true")
    confidence: float = Field(ge=0.0, le=1.0, description="|2p - 1|; Jev returns none for Noul, JevGate derives it")


Signal = Annotated[Union[ChoiceSignal, ScoreSignal, NoulSignal], Field(discriminator="type")]


class SystemOneResult(BaseModel):
    model: str = Field(description="Versioned model ID that answered, e.g. jev-1.14.0")
    signals: dict[str, Signal]
    input_tokens: int
    latency_ms: float
    request_id: str | None = None


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

Stakes = Literal["high", "standard"]


class Band(BaseModel):
    """A condition is TRUE at or above `yes`, FALSE at or below `no`, and UNCERTAIN in between."""
    model_config = ConfigDict(extra="forbid")
    yes: float = Field(gt=0.0, lt=1.0)
    no: float = Field(gt=0.0, lt=1.0)

    @model_validator(mode="after")
    def _ordered(self) -> Band:
        if self.no >= self.yes:
            raise ValueError(f"band 'no' ({self.no}) must be below 'yes' ({self.yes})")
        return self


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal: str = Field(description="Question ID in the battery")
    test: Literal["yes", "at_least", "in"]
    value: int | list[str] | None = Field(default=None, description="Level for at_least, options for in")
    proposition: str = Field(description="The condition as a plain yes/no statement, shown to System-2 and auditors")


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_id: str
    title: str
    action: Literal["BLOCK", "ROUTE"]
    office: str = Field(description="Who owns the document when this rule fires")
    stakes: Stakes
    when: list[str] = Field(min_length=1, description="Condition IDs, all of which must hold")
    rationale: str


class Expedite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal: str
    at_least: int
    probability: float = Field(gt=0.0, lt=1.0)


class RiskWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scores: dict[str, float] = Field(description="Score signal ID -> weight of its normalized expected level")
    rules: float = Field(ge=0.0, description="Weight of the most likely rule, scaled by its stakes")
    stakes: dict[Stakes, float]


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_id: str
    version: str
    bands: dict[Stakes, Band]
    expedite: Expedite
    risk_weights: RiskWeights
    conditions: dict[str, Condition]
    rules: list[Rule] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Rule evaluation
# ---------------------------------------------------------------------------

class Truth(str, Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNCERTAIN = "UNCERTAIN"


ResolvedBy = Literal["SYSTEM_ONE", "SYSTEM_TWO", "USER_CONTEXT"]


class ConditionResult(BaseModel):
    condition_id: str
    proposition: str
    probability: float = Field(description="P(condition holds), read from Jev's distribution")
    truth: Truth
    resolved_by: ResolvedBy | None = Field(default=None, description="None while UNCERTAIN")


class RuleResult(BaseModel):
    rule_id: str
    title: str
    action: Literal["BLOCK", "ROUTE"]
    office: str
    stakes: Stakes
    truth: Truth
    conditions: list[ConditionResult]


# ---------------------------------------------------------------------------
# System-2 contract (shared with system2/src/schema.ts)
# ---------------------------------------------------------------------------

class Proposition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(description="Condition ID")
    text: str
    stakes: Stakes = Field(description="Highest stakes among the open rules that use this condition")
    jev_probability: float = Field(ge=0.0, le=1.0)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document: str
    metadata: dict[str, str] = Field(default_factory=dict)
    propositions: list[Proposition] = Field(min_length=1)


class PropositionVerdict(BaseModel):
    id: str
    verdict: Literal["yes", "no", "cannot_determine"]
    evidence: str = Field(default="", description="Verbatim quote from the document supporting a yes")
    rationale: str = ""


class ReviewResponse(BaseModel):
    model: str
    verdicts: list[PropositionVerdict]


class DiscardedVerdict(BaseModel):
    id: str
    reason: Literal["UNKNOWN_ID", "DUPLICATE", "UNGROUNDED_YES", "HIGH_STAKES_NO"]
    detail: str


class SystemTwoReport(BaseModel):
    invoked: bool
    model: str | None = None
    propositions: list[Proposition] = Field(default_factory=list)
    verdicts: list[PropositionVerdict] = Field(default_factory=list)
    discarded: list[DiscardedVerdict] = Field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

class Verdict(str, Enum):
    CLEARED = "CLEARED"            # No rule applies; proceed automatically
    ROUTED = "ROUTED"              # A ROUTE rule fired; send to its office
    BLOCKED = "BLOCKED"            # A BLOCK rule fired; do not distribute
    HUMAN_REVIEW = "HUMAN_REVIEW"  # Open uncertainty could change the outcome


EscalationCode = Literal[
    "SYSTEM_ONE_UNAVAILABLE",   # Jev call failed or timed out; fail closed
    "SYSTEM_TWO_UNAVAILABLE",   # Reviewer call failed or timed out
    "SYSTEM_TWO_DISABLED",      # mode=fast, or no reviewer configured
    "UNRESOLVED_CONDITION",     # A condition is still UNCERTAIN in an open rule
]


class Escalation(BaseModel):
    code: EscalationCode
    rule_id: str | None = None
    condition_id: str | None = None
    detail: str


class TriageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document: str = Field(min_length=1, max_length=100_000)
    metadata: dict[str, str] = Field(default_factory=dict, description="Optional context, e.g. source_unit")
    mode: Literal["fast", "hybrid"] = Field(
        default="hybrid", description="fast: System-1 only, open uncertainty goes to a human; hybrid: try System-2 first"
    )


class Timings(BaseModel):
    system_one_ms: float | None = None
    policy_ms: float | None = None
    system_two_ms: float | None = None
    total_ms: float


class TriageDecision(BaseModel):
    decision_id: str
    policy_version: str
    verdict: Verdict
    decided_by: Literal["SYSTEM_ONE", "SYSTEM_TWO", "PENDING_HUMAN"]
    offices: list[str] = Field(description="Owners of fired rules")
    review_offices: list[str] = Field(description="Owners of rules still open, for HUMAN_REVIEW")
    priority: Literal["EXPEDITE", "STANDARD"]
    risk_index: float = Field(ge=0.0, le=1.0, description="Orders the review queue; never changes a verdict")
    rules: list[RuleResult]
    signals: dict[str, Signal]
    escalations: list[Escalation]
    system_two: SystemTwoReport
    model: str | None
    input_tokens: int | None
    timings: Timings
