from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping

from .grounding import find_quote_span
from .policy import condition_probability
from .router import triage
from .schemas import (
    NoulSpec, Policy, Proposition, QuestionSpec, ReviewRequest, TriageDecision, TriageRequest,
    Truth, Verdict,
)
from .store import CaseConflict, CaseNotFound, CaseStore, utc_now
from .system_one import SystemOne
from .system_two import SystemTwo, SystemTwoError
from .workbench_schemas import (
    CaseAssignmentUpdate, CaseCreateRequest, CaseDetail, CaseStatus, CaseSummary,
    CaseVersionDetail, DispositionType, EvidenceSpan, RuleDispositionRecord,
    RuleDispositionUpdate, SubmissionContext, VersionCreateRequest,
)


def context_inputs(context: SubmissionContext) -> tuple[dict[str, str], dict[str, float]]:
    values = context.model_dump(mode="json", exclude_none=True)
    metadata = {k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in values.items()}
    floors: dict[str, float] = {}
    if context.intended_audience in ("partner_government", "public_release"):
        floors["external_audience"] = 1.0
    if context.intended_audience == "public_release":
        floors["public_audience"] = 1.0
    if context.release_requested is True:
        floors["release_requested"] = 1.0
    return metadata, floors


def derive_case_status(
    decision: TriageDecision,
    dispositions: Mapping[str, RuleDispositionRecord],
    current_status: CaseStatus | None = None,
) -> CaseStatus:
    active = [r for r in decision.rules if r.truth is not Truth.FALSE]
    if decision.verdict is Verdict.CLEARED and not active:
        return CaseStatus.CLEARED_BY_MACHINE
    def disposition(rule):
        record = dispositions.get(rule.rule_id)
        return record.disposition if record else DispositionType.PENDING
    if any(r.action == "BLOCK" and disposition(r) is DispositionType.ACCEPT_FINDING for r in active):
        return CaseStatus.BLOCKED_FINAL
    cleared = {DispositionType.FALSE_POSITIVE_OVERRIDE, DispositionType.WAIVE_RISK, DispositionType.CARRIED_FORWARD}
    if active and all(disposition(r) in cleared or (
        r.action == "ROUTE" and disposition(r) is DispositionType.ACCEPT_FINDING
    ) for r in active):
        return CaseStatus.APPROVED_BY_HUMAN
    if current_status is CaseStatus.CHANGES_REQUESTED:
        return current_status
    return CaseStatus.OPEN


class CaseService:
    def __init__(self, store: CaseStore, *, policy: Policy, battery: Mapping[str, QuestionSpec],
                 system_one: SystemOne, system_two: SystemTwo | None):
        self.store, self.policy, self.battery = store, policy, battery
        self.system_one, self.system_two = system_one, system_two

    def _warnings(self, context: SubmissionContext, decision: TriageDecision) -> list[str]:
        checks = {}
        if context.intended_audience in ("internal_team", "institution_wide"):
            checks.update({cid: context.intended_audience.value for cid in ("external_audience", "public_audience")})
        if context.release_requested is False:
            checks["release_requested"] = "release_requested=False"
        warnings = []
        for cid, declaration in checks.items():
            cond = self.policy.conditions.get(cid)
            if cond is None or cond.signal not in decision.signals:
                continue
            probability = condition_probability(cond, decision.signals[cond.signal])
            if probability > self.policy.bands["high"].no:
                warnings.append(f"Context mismatch: Submitter declared {declaration}, but System-1 "
                                f"detected {cid} signal (P={probability:.2f}).")
        return warnings

    @staticmethod
    def _extract(verdicts, document: str, allowed: set[str]) -> list[EvidenceSpan]:
        spans = []
        seen = set()
        for verdict in verdicts:
            if verdict.id not in allowed or verdict.id in seen:
                continue
            seen.add(verdict.id)
            if verdict.verdict != "yes":
                continue
            location = find_quote_span(verdict.evidence, document)
            if location is not None:
                start, end = location
                if 0 <= start < end <= len(document):
                    spans.append(EvidenceSpan(condition_id=verdict.id, quote=document[start:end],
                                              start_char=start, end_char=end, rationale=verdict.rationale))
        return spans

    async def _ground(self, version: CaseVersionDetail, *, on_demand: bool = False) -> list[EvidenceSpan]:
        decision = version.decision
        allowed = {p.id for p in decision.system_two.propositions}
        spans = {s.condition_id: s for s in version.evidence_spans}
        for span in self._extract(decision.system_two.verdicts, version.document, allowed):
            spans.setdefault(span.condition_id, span)
        sent = {p.id for p in decision.system_two.propositions} if decision.system_two.invoked else set()
        propositions: dict[str, Proposition] = {}
        for rule in decision.rules:
            if rule.truth is Truth.FALSE:
                continue
            for cond in rule.conditions:
                cid = cond.condition_id
                definition = self.policy.conditions.get(cid)
                if definition is None or not isinstance(self.battery.get(definition.signal), NoulSpec):
                    continue
                if cid in spans or (not on_demand and cid in sent):
                    continue
                if not on_demand and not (cond.truth is Truth.TRUE and cond.resolved_by == "SYSTEM_ONE"):
                    continue
                if cond.truth is Truth.FALSE:
                    continue
                previous = propositions.get(cid)
                propositions[cid] = Proposition(
                    id=cid, text=cond.proposition, jev_probability=cond.probability,
                    stakes="high" if rule.stakes == "high" or (previous and previous.stakes == "high") else "standard",
                )
        if propositions and self.system_two is not None:
            metadata, _ = context_inputs(version.context)
            # Quote harvesting deliberately never applies review verdicts to the machine decision.
            review = await self.system_two.review(ReviewRequest(
                document=version.document, metadata=metadata, propositions=list(propositions.values()),
            ))
            for span in self._extract(review.verdicts, version.document, set(propositions)):
                spans[span.condition_id] = span
        return list(spans.values())

    async def _evaluate(self, document: str, context: SubmissionContext, mode: str,
                        number: int, change_note: str) -> CaseVersionDetail:
        metadata, floors = context_inputs(context)
        decision = await triage(TriageRequest(document=document, metadata=metadata, mode=mode),
                                policy=self.policy, battery=self.battery, system_one=self.system_one,
                                system_two=self.system_two, context_floors=floors)
        version = CaseVersionDetail(
            version_number=number, created_at=utc_now(), change_note=change_note, document=document,
            document_sha256=hashlib.sha256(document.encode("utf-8")).hexdigest(),
            context=context, decision=decision, context_warnings=self._warnings(context, decision),
            rule_dispositions={r.rule_id: RuleDispositionRecord(rule_id=r.rule_id, office=r.office)
                               for r in decision.rules if r.truth is not Truth.FALSE},
        )
        # Existing grounded review answers are useful even when no supplementary call is needed.
        version.evidence_spans = self._extract(decision.system_two.verdicts, document,
                                               {p.id for p in decision.system_two.propositions})
        if mode == "hybrid" and self.system_two is not None:
            try:
                version.evidence_spans = await self._ground(version)
            except SystemTwoError:
                # Optional grounding cannot prevent saving a completed triage decision.
                version.context_warnings.append("Evidence grounding unavailable; retry using the ground endpoint.")
        return version

    async def create_case(self, request: CaseCreateRequest) -> CaseDetail:
        version = await self._evaluate(request.document, request.context, request.mode, 1, "")
        case_id = uuid.uuid4().hex
        status = derive_case_status(version.decision, version.rule_dispositions)
        with self.store.transaction() as db:
            db.execute("INSERT INTO cases VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (case_id, request.title, version.created_at, version.created_at, 1, status.value, None, None))
            self.store.insert_version(case_id, version)
            self.store.audit(case_id, 1, "CASE_CREATED", "submitter", {"document_sha256": version.document_sha256})
            return self.store.get_case(case_id)

    @staticmethod
    def _carry_forward(previous: CaseVersionDetail, version: CaseVersionDetail) -> None:
        old_rules = {r.rule_id: r for r in previous.decision.rules}
        for rule in version.decision.rules:
            if rule.truth is Truth.FALSE:
                continue
            old = previous.rule_dispositions.get(rule.rule_id)
            if old is None or old.disposition not in (
                DispositionType.FALSE_POSITIVE_OVERRIDE, DispositionType.WAIVE_RISK, DispositionType.CARRIED_FORWARD,
            ):
                continue
            old_rule = old_rules.get(rule.rule_id)
            old_probabilities = {c.condition_id: c.probability for c in old_rule.conditions} if old_rule else {}
            stable = all(c.condition_id in old_probabilities and
                         c.probability - old_probabilities[c.condition_id] <= 0.05 + 1e-9 for c in rule.conditions)
            condition_ids = {c.condition_id for c in rule.conditions}
            quotes_present = all(s.quote in version.document for s in previous.evidence_spans
                                 if s.condition_id in condition_ids)
            if previous.document_sha256 == version.document_sha256 or (stable and quotes_present):
                version.rule_dispositions[rule.rule_id] = old.model_copy(update={
                    "disposition": DispositionType.CARRIED_FORWARD,
                    "carried_from_version": previous.version_number, "updated_at": utc_now(), "invalidation_note": None,
                })
            else:
                version.rule_dispositions[rule.rule_id].invalidation_note = (
                    f"Prior {old.disposition.value} from v{previous.version_number} invalidated "
                    "because document text or risk score changed."
                )

    async def create_version(self, case_id: str, request: VersionCreateRequest) -> CaseDetail:
        previous = self.store.get_case(case_id)
        context = request.context if request.context is not None else previous.active_version.context
        version = await self._evaluate(request.document, context, request.mode,
                                       previous.current_version + 1, request.change_note)
        with self.store.transaction() as db:
            current = self.store.get_case(case_id)
            if current.updated_at != previous.updated_at or current.current_version != previous.current_version:
                raise CaseConflict("Case changed during evaluation; retry the revision")
            self._carry_forward(current.active_version, version)
            status = derive_case_status(version.decision, version.rule_dispositions)
            self.store.insert_version(case_id, version)
            db.execute("UPDATE cases SET current_version = ?, status = ?, updated_at = ? WHERE case_id = ?",
                       (version.version_number, status.value, utc_now(), case_id))
            self.store.audit(case_id, version.version_number, "VERSION_CREATED", "submitter",
                             {"document_sha256": version.document_sha256, "previous_version": previous.current_version})
            return self.store.get_case(case_id)

    def update_disposition(self, case_id: str, number: int, rule_id: str,
                           request: RuleDispositionUpdate) -> CaseDetail:
        if not all(value.strip() for value in (request.reviewer, request.office, request.rationale)):
            raise ValueError("Reviewer, office, and rationale must contain non-whitespace text")
        with self.store.transaction() as db:
            detail = self.store.get_case(case_id, number)
            version = detail.active_version
            if rule_id not in version.rule_dispositions:
                raise CaseNotFound("Active rule not found in this case version")
            record = RuleDispositionRecord(rule_id=rule_id, **request.model_dump(), updated_at=utc_now())
            version.rule_dispositions[rule_id] = record
            self.store.save_disposition(case_id, number, record)
            if number == detail.current_version:
                status = derive_case_status(version.decision, version.rule_dispositions, detail.status)
                db.execute("UPDATE cases SET status = ?, updated_at = ? WHERE case_id = ?",
                           (status.value, utc_now(), case_id))
            self.store.audit(case_id, number, "RULE_DISPOSITION_UPDATED", request.reviewer,
                             record.model_dump(mode="json"))
            return self.store.get_case(case_id, number)

    async def ground_case_version(self, case_id: str, version_number: int) -> CaseDetail:
        detail = self.store.get_case(case_id, version_number)
        if self.system_two is None:
            raise SystemTwoError("System-2 is not configured")
        spans = await self._ground(detail.active_version, on_demand=True)
        with self.store.transaction() as db:
            current = self.store.get_case(case_id, version_number)
            merged = {s.condition_id: s for s in current.active_version.evidence_spans}
            merged.update({s.condition_id: s for s in spans})
            db.execute("UPDATE case_versions SET evidence_spans_json = ? WHERE case_id = ? AND version_number = ?",
                       (json.dumps([s.model_dump() for s in merged.values()]), case_id, version_number))
            db.execute("UPDATE cases SET updated_at = ? WHERE case_id = ?", (utc_now(), case_id))
            self.store.audit(case_id, version_number, "EVIDENCE_GROUNDED", "system_two",
                             {"condition_ids": list(merged)})
            return self.store.get_case(case_id, version_number)

    def update_assignment(self, case_id: str, request: CaseAssignmentUpdate) -> CaseDetail:
        if request.status not in (None, CaseStatus.OPEN, CaseStatus.CHANGES_REQUESTED):
            raise ValueError("Only OPEN or CHANGES_REQUESTED can be set explicitly; other statuses derive from findings")
        with self.store.transaction() as db:
            detail = self.store.get_case(case_id)
            version = detail.active_version
            status = detail.status
            if request.status is not None:
                status = derive_case_status(version.decision, version.rule_dispositions, request.status)
            office = request.assigned_office if "assigned_office" in request.model_fields_set else detail.assigned_office
            reviewer = request.assigned_reviewer if "assigned_reviewer" in request.model_fields_set else detail.assigned_reviewer
            db.execute("UPDATE cases SET assigned_office = ?, assigned_reviewer = ?, status = ?, updated_at = ? WHERE case_id = ?",
                       (office, reviewer, status.value, utc_now(), case_id))
            self.store.audit(case_id, detail.current_version, "CASE_UPDATED", request.reviewer,
                             request.model_dump(mode="json", exclude_unset=True))
            return self.store.get_case(case_id)

    def list_cases(self, *, status: CaseStatus | None = None, office: str | None = None,
                   verdict: Verdict | None = None) -> list[CaseSummary]:
        summaries = []
        for case_id in self.store.case_ids():
            detail = self.store.get_case(case_id)
            if status is not None and detail.status != status:
                continue
            if verdict is not None and detail.machine_verdict != verdict:
                continue
            if office is not None and office not in [detail.assigned_office, *detail.offices, *detail.review_offices]:
                continue
            summaries.append(CaseSummary(**detail.model_dump(include=set(CaseSummary.model_fields))))
        return sorted(summaries, key=lambda c: (c.priority == "EXPEDITE", c.risk_index, c.updated_at), reverse=True)
