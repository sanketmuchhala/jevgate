from __future__ import annotations
import time
import uuid
from collections.abc import Mapping
from .calibration import p_at_least
from .grounding import is_grounded_quote
from .policy import evaluate_rules, risk_index
from .schemas import (
    DiscardedVerdict, Escalation, Policy, Proposition, QuestionSpec, ReviewRequest, ReviewResponse, RuleResult,
    Stakes, SystemTwoReport, Timings, TriageDecision, TriageRequest, Truth, Verdict,
)
from .system_one import SystemOne, SystemOneError
from .system_two import SystemTwo, SystemTwoError

MIN_EVIDENCE_CHARS = 8


def verdict_for(rules: list[RuleResult]) -> Verdict:
    """BLOCK beats everything; otherwise any open rule means the outcome is not settled yet."""
    if any(r.truth is Truth.TRUE and r.action == "BLOCK" for r in rules):
        return Verdict.BLOCKED
    if any(r.truth is Truth.UNCERTAIN for r in rules):
        return Verdict.HUMAN_REVIEW
    if any(r.truth is Truth.TRUE for r in rules):
        return Verdict.ROUTED
    return Verdict.CLEARED


def open_condition_stakes(rules: list[RuleResult]) -> dict[str, set[Stakes]]:
    """Conditions still UNCERTAIN inside rules still UNCERTAIN. Uncertainty in a settled rule is ignored."""
    stakes: dict[str, set[Stakes]] = {}
    for rule in rules:
        if rule.truth is not Truth.UNCERTAIN:
            continue
        for cond in rule.conditions:
            if cond.truth is Truth.UNCERTAIN:
                stakes.setdefault(cond.condition_id, set()).add(rule.stakes)
    return stakes


def build_propositions(policy: Policy, rules: list[RuleResult], open_stakes: Mapping[str, set[Stakes]]) -> list[Proposition]:
    probability = {c.condition_id: c.probability for r in rules for c in r.conditions}
    return [
        Proposition(
            id=cid,
            text=policy.conditions[cid].proposition,
            stakes="high" if "high" in open_stakes[cid] else "standard",
            jev_probability=probability[cid],
        )
        for cid in open_stakes
    ]


def is_grounded(evidence: str, document: str) -> bool:
    """A 'yes' must quote the document. Paraphrase or invention does not count."""
    return is_grounded_quote(evidence, document, MIN_EVIDENCE_CHARS)


def apply_review(
    review: ReviewResponse,
    propositions: list[Proposition],
    open_stakes: Mapping[str, set[Stakes]],
    document: str,
) -> tuple[dict[str, Truth], list[DiscardedVerdict]]:
    """Turn reviewer verdicts into condition overrides. Python decides which verdicts count."""
    known = {p.id for p in propositions}
    overrides: dict[str, Truth] = {}
    discarded: list[DiscardedVerdict] = []
    seen: set[str] = set()
    for v in review.verdicts:
        if v.id not in known:
            discarded.append(DiscardedVerdict(id=v.id, reason="UNKNOWN_ID", detail="no open proposition has this ID"))
            continue
        if v.id in seen:
            discarded.append(DiscardedVerdict(id=v.id, reason="DUPLICATE", detail="only the first verdict per ID counts"))
            continue
        seen.add(v.id)
        if v.verdict == "yes":
            if not is_grounded(v.evidence, document):
                discarded.append(DiscardedVerdict(
                    id=v.id, reason="UNGROUNDED_YES", detail="evidence is not a verbatim quote from the document",
                ))
                continue
            overrides[v.id] = Truth.TRUE
        elif v.verdict == "no":
            # evaluate_rules applies FALSE to standard-stakes rules only.
            overrides[v.id] = Truth.FALSE
            if "high" in open_stakes[v.id]:
                discarded.append(DiscardedVerdict(
                    id=v.id, reason="HIGH_STAKES_NO", detail="a reviewer 'no' cannot clear a high-stakes rule",
                ))
    return overrides, discarded


def _unresolved(policy: Policy, rules: list[RuleResult], report: SystemTwoReport) -> list[Escalation]:
    verdicts = {v.id: v for v in report.verdicts}
    discarded = {d.id: d for d in report.discarded}
    out: list[Escalation] = []
    for rule in rules:
        if rule.truth is not Truth.UNCERTAIN:
            continue
        band = policy.bands[rule.stakes]
        for cond in rule.conditions:
            if cond.truth is not Truth.UNCERTAIN:
                continue
            detail = f"Jev P={cond.probability:.2f} is inside the {rule.stakes} band ({band.no}, {band.yes})"
            if cond.condition_id in discarded:
                detail += f"; reviewer verdict discarded: {discarded[cond.condition_id].detail}"
            elif cond.condition_id in verdicts:
                detail += f"; reviewer said {verdicts[cond.condition_id].verdict}"
            elif report.invoked and report.error is None:
                detail += "; reviewer returned no verdict"
            out.append(Escalation(
                code="UNRESOLVED_CONDITION", rule_id=rule.rule_id, condition_id=cond.condition_id, detail=detail,
            ))
    return out


def _offices(rules: list[RuleResult], truth: Truth) -> list[str]:
    return list(dict.fromkeys(r.office for r in rules if r.truth is truth))


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


async def triage(
    request: TriageRequest,
    *,
    policy: Policy,
    battery: Mapping[str, QuestionSpec],
    system_one: SystemOne,
    system_two: SystemTwo | None,
    context_floors: Mapping[str, float] | None = None,
) -> TriageDecision:
    started = time.perf_counter()
    decision_id = uuid.uuid4().hex

    try:
        s1 = await system_one.evaluate({"document": request.document, "metadata": request.metadata})
    except SystemOneError as exc:
        # Fail closed: an unread document is treated as the worst case, never cleared.
        return TriageDecision(
            decision_id=decision_id, policy_version=policy.version, verdict=Verdict.HUMAN_REVIEW,
            decided_by="PENDING_HUMAN", offices=[], review_offices=[], priority="EXPEDITE", risk_index=1.0,
            rules=[], signals={},
            escalations=[Escalation(code="SYSTEM_ONE_UNAVAILABLE", detail=str(exc))],
            system_two=SystemTwoReport(invoked=False), model=None, input_tokens=None,
            timings=Timings(total_ms=_ms(started)),
        )

    policy_started = time.perf_counter()
    rules = evaluate_rules(policy, s1.signals, context_floors=context_floors)
    policy_ms = _ms(policy_started)

    report = SystemTwoReport(invoked=False)
    escalations: list[Escalation] = []
    system_two_ms: float | None = None

    if verdict_for(rules) is Verdict.HUMAN_REVIEW:
        open_stakes = open_condition_stakes(rules)
        propositions = build_propositions(policy, rules, open_stakes)
        if request.mode == "fast" or system_two is None:
            why = "mode=fast" if request.mode == "fast" else "no SYSTEM2_URL configured"
            escalations.append(Escalation(code="SYSTEM_TWO_DISABLED", detail=f"System-2 skipped ({why})"))
            report = SystemTwoReport(invoked=False, propositions=propositions)
        else:
            s2_started = time.perf_counter()
            try:
                review = await system_two.review(ReviewRequest(
                    document=request.document, metadata=request.metadata, propositions=propositions,
                ))
                overrides, discarded = apply_review(review, propositions, open_stakes, request.document)
                rules = evaluate_rules(policy, s1.signals, overrides, context_floors=context_floors)
                report = SystemTwoReport(
                    invoked=True, model=review.model, propositions=propositions,
                    verdicts=review.verdicts, discarded=discarded,
                )
            except SystemTwoError as exc:
                escalations.append(Escalation(code="SYSTEM_TWO_UNAVAILABLE", detail=str(exc)))
                report = SystemTwoReport(invoked=True, propositions=propositions, error=str(exc))
            system_two_ms = _ms(s2_started)

    verdict = verdict_for(rules)
    if verdict is Verdict.HUMAN_REVIEW:
        escalations.extend(_unresolved(policy, rules, report))
        decided_by = "PENDING_HUMAN"
    elif report.invoked and report.error is None:
        decided_by = "SYSTEM_TWO"
    else:
        decided_by = "SYSTEM_ONE"

    expedite = policy.expedite
    urgent = p_at_least(s1.signals[expedite.signal].probabilities, expedite.at_least) >= expedite.probability

    return TriageDecision(
        decision_id=decision_id,
        policy_version=policy.version,
        verdict=verdict,
        decided_by=decided_by,
        offices=_offices(rules, Truth.TRUE),
        review_offices=_offices(rules, Truth.UNCERTAIN),
        priority="EXPEDITE" if urgent else "STANDARD",
        risk_index=risk_index(policy, battery, s1.signals, rules),
        rules=rules,
        signals=s1.signals,
        escalations=escalations,
        system_two=report,
        model=s1.model,
        input_tokens=s1.input_tokens,
        timings=Timings(
            system_one_ms=s1.latency_ms, policy_ms=policy_ms, system_two_ms=system_two_ms, total_ms=_ms(started),
        ),
    )
