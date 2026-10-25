"""Evaluate JevGate on the labelled institutional corpus: System-1 only (fast) against hybrid.

    python scripts/evaluate.py                          # offline (default): replay the corpus's mock answers
    python scripts/evaluate.py --out outputs/eval.json  # also write every document's outcome as JSON
    python scripts/evaluate.py --live                   # real Jev (TYPESAFE_API_KEY) and System-2 (SYSTEM2_URL)

Offline mode replays each document's `mock_signals` and `mock_system_two_verdicts`. They are
hand-authored to exercise the gate: bands, Kleene rules, the System-2 trust rules, and quote
grounding. Offline figures therefore measure the policy and router on known inputs, not Jev
or the reviewer; they need no API key and are deterministic. Live mode asks Jev once per
document and reuses that answer for both pipelines, so fast and hybrid differ only in System-2.

Labels, per document:
  expected_verdict      what correct triage returns
  expected_fired_rules  rules whose risk is present: they should fire, or stay open for a
                        person when the expected verdict is HUMAN_REVIEW
  expected_offices      the expected offices + review_offices, as a set
  expected_evidence_substrings
                        condition ID -> a verbatim passage that makes the condition true

Metrics:
  high_stakes_fnr            expected high-stakes rules (P1-P5) the pipeline settled FALSE, so no
                             person sees them; an open rule counts as caught
  false_escalation_rate      documents labelled CLEARED that were blocked, routed, or escalated
  system2_resolution_rate    documents fast mode sent to HUMAN_REVIEW that hybrid settled
  span_grounding_accuracy    accepted System-2 yes verdicts whose quote find_quote_span() locates
                             and whose span overlaps the labelled evidence for that condition
  office_routing_exact_match offices + review_offices equal expected_offices
  verdict_accuracy           verdict equals expected_verdict
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pydantic import ValidationError  # noqa: E402
from src.calibration import choice_confidence, noul_confidence, score_confidence  # noqa: E402
from src.grounding import find_quote_span  # noqa: E402
from src.policy import DEFAULT_POLICY_PATH, load_policy  # noqa: E402
from src.questions import BATTERY  # noqa: E402
from src.router import triage  # noqa: E402
from src.schemas import (  # noqa: E402
    ChoiceSignal, ChoiceSpec, NoulSignal, NoulSpec, Policy, PropositionVerdict, QuestionSpec, ReviewRequest,
    ReviewResponse, ScoreSignal, ScoreSpec, Signal, SystemOneResult, TriageDecision, TriageRequest, Truth, Verdict,
)
from src.system_one import SystemOne, SystemOneError  # noqa: E402
from src.system_two import SystemTwo  # noqa: E402
from src.workbench_schemas import AudienceOption, SubmissionContext  # noqa: E402

DEFAULT_CORPUS = ROOT / "samples" / "eval_corpus.jsonl"
CATEGORIES = ("clear_positive", "internal_short_circuit", "ambiguous_boundary", "adversarial_negation")
PIPELINES = ("fast", "hybrid")
# Jev rounds probabilities, so distributions in the corpus need only sum to one within this.
SUM_TOLERANCE = 0.005
REQUIRED = (
    "id", "title", "category", "document", "context", "mock_signals", "mock_system_two_verdicts",
    "expected_verdict", "expected_fired_rules", "expected_offices", "expected_high_stakes_risk",
    "expected_evidence_substrings",
)


class CorpusError(ValueError):
    """The corpus file is malformed or its labels are inconsistent with the policy."""


@dataclass(frozen=True)
class CorpusDocument:
    id: str
    title: str
    category: str
    document: str
    context: SubmissionContext
    signals: dict[str, Signal]
    system_two_verdicts: list[PropositionVerdict]
    expected_verdict: Verdict
    expected_fired_rules: tuple[str, ...]
    expected_offices: frozenset[str]
    expected_high_stakes_risk: bool
    expected_evidence_substrings: dict[str, str]
    notes: str = ""


# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------

def _probability(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 <= value <= 1.0


def expand_signals(raw: object, battery: Mapping[str, QuestionSpec]) -> dict[str, Signal]:
    """Turn the corpus's compact answers into the signals Jev's adapter produces.

    Choice: {option: probability} over every option. Score: [p(level 0), p(level 1), ...].
    Noul: P(yes). Confidence is derived with TypeSafe's published formulas, as Jev reports it.
    """
    if not isinstance(raw, dict):
        raise CorpusError("mock_signals must be an object")
    problems = [f"unknown question '{qid}'" for qid in raw if qid not in battery]
    signals: dict[str, Signal] = {}
    for qid, spec in battery.items():
        value = raw.get(qid)
        if value is None:
            problems.append(f"{qid}: missing")
        elif isinstance(spec, ChoiceSpec):
            if not isinstance(value, dict) or set(value) != set(spec.options) or not all(map(_probability, value.values())):
                problems.append(f"{qid}: needs a probability for each of {sorted(spec.options)}")
            elif abs(sum(value.values()) - 1.0) > SUM_TOLERANCE:
                problems.append(f"{qid}: probabilities sum to {sum(value.values()):.3f}")
            else:
                signals[qid] = ChoiceSignal(
                    choice=max(value, key=value.get), probabilities=dict(value),
                    confidence=round(choice_confidence(value.values()), 4),
                )
        elif isinstance(spec, ScoreSpec):
            if not isinstance(value, list) or len(value) != len(spec.levels) or not all(map(_probability, value)):
                problems.append(f"{qid}: needs a list of {len(spec.levels)} level probabilities")
            elif abs(sum(value) - 1.0) > SUM_TOLERANCE:
                problems.append(f"{qid}: probabilities sum to {sum(value):.3f}")
            else:
                probs = dict(enumerate(value))
                signals[qid] = ScoreSignal(
                    score=round(sum(level * p for level, p in probs.items()), 4), probabilities=probs,
                    confidence=round(score_confidence(probs), 4),
                )
        elif isinstance(spec, NoulSpec):
            if not _probability(value):
                problems.append(f"{qid}: needs P(yes) between 0 and 1")
            else:
                signals[qid] = NoulSignal(noul=value, confidence=round(noul_confidence(value), 4))
    if problems:
        raise CorpusError("; ".join(problems))
    return signals


def _parse_document(row: object, policy: Policy, battery: Mapping[str, QuestionSpec]) -> CorpusDocument:
    if not isinstance(row, dict):
        raise CorpusError("each line must be a JSON object")
    missing = [key for key in REQUIRED if key not in row]
    if missing:
        raise CorpusError(f"missing {missing}")
    problems: list[str] = []
    rules = {r.rule_id: r for r in policy.rules}
    offices = {r.office for r in policy.rules}

    if row["category"] not in CATEGORIES:
        problems.append(f"category '{row['category']}' is not one of {list(CATEGORIES)}")
    if row["expected_verdict"] not in {v.value for v in Verdict}:
        problems.append(f"expected_verdict '{row['expected_verdict']}' is not a verdict")
    if not isinstance(row["document"], str) or not row["document"].strip():
        problems.append("document is empty")
    fired = row["expected_fired_rules"]
    if not isinstance(fired, list) or any(r not in rules for r in fired) or len(set(fired)) != len(fired):
        problems.append(f"expected_fired_rules {fired} must be distinct rule IDs from the policy")
        fired = []
    expected_offices = row["expected_offices"]
    if not isinstance(expected_offices, list) or any(o not in offices for o in expected_offices):
        problems.append(f"expected_offices {expected_offices} must be offices named in the policy")
        expected_offices = []
    high = any(rules[r].stakes == "high" for r in fired)
    if row["expected_high_stakes_risk"] is not high:
        problems.append(f"expected_high_stakes_risk is {row['expected_high_stakes_risk']}, but the expected rules say {high}")
    evidence = row["expected_evidence_substrings"]
    if not isinstance(evidence, dict):
        problems.append("expected_evidence_substrings must map condition IDs to quotes")
        evidence = {}
    for cid, quote in evidence.items():
        if cid not in policy.conditions:
            problems.append(f"expected_evidence_substrings: unknown condition '{cid}'")
        elif not isinstance(quote, str) or find_quote_span(quote, row["document"]) is None:
            problems.append(f"expected_evidence_substrings[{cid}] is not a verbatim passage of the document")
    try:
        context = SubmissionContext.model_validate(row["context"])
    except ValidationError as exc:
        problems.append(f"context: {exc.errors()[0]['msg']}")
        context = SubmissionContext()
    try:
        verdicts = [PropositionVerdict.model_validate(v) for v in row["mock_system_two_verdicts"]]
    except (ValidationError, TypeError) as exc:
        problems.append(f"mock_system_two_verdicts: {exc}")
        verdicts = []
    for v in verdicts:
        if v.id not in policy.conditions:
            problems.append(f"mock_system_two_verdicts: unknown condition '{v.id}'")
    try:
        signals = expand_signals(row["mock_signals"], battery)
    except CorpusError as exc:
        problems.append(f"mock_signals: {exc}")
        signals = {}
    if problems:
        raise CorpusError("; ".join(problems))
    return CorpusDocument(
        id=row["id"], title=row["title"], category=row["category"], document=row["document"], context=context,
        signals=signals, system_two_verdicts=verdicts, expected_verdict=Verdict(row["expected_verdict"]),
        expected_fired_rules=tuple(fired), expected_offices=frozenset(expected_offices),
        expected_high_stakes_risk=high, expected_evidence_substrings=dict(evidence), notes=row.get("notes", ""),
    )


def load_corpus(
    path: Path = DEFAULT_CORPUS, policy: Policy | None = None, battery: Mapping[str, QuestionSpec] = BATTERY,
) -> list[CorpusDocument]:
    """Parse and validate every line. Reports all problems together, by line number."""
    policy = policy or load_policy(DEFAULT_POLICY_PATH, battery)
    documents: list[CorpusDocument] = []
    problems: list[str] = []
    seen: set[str] = set()
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            doc = _parse_document(json.loads(line), policy, battery)
        except (json.JSONDecodeError, CorpusError) as exc:
            problems.append(f"line {number}: {exc}")
            continue
        if doc.id in seen:
            problems.append(f"line {number}: duplicate id '{doc.id}'")
        seen.add(doc.id)
        documents.append(doc)
    if problems:
        raise CorpusError(f"{path}: " + " | ".join(problems))
    if not documents:
        raise CorpusError(f"{path}: no documents")
    return documents


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------

class ReplaySystemOne:
    """Offline System-1: returns the corpus's signals as if Jev had answered."""

    def __init__(self, signals: Mapping[str, Signal], model: str = "offline-replay"):
        self._result = SystemOneResult(model=model, signals=dict(signals), input_tokens=0, latency_ms=0.0)

    async def evaluate(self, state: Mapping[str, Any]) -> SystemOneResult:
        return self._result


class ReplaySystemTwo:
    """Offline System-2: answers only the propositions it is asked about, as a reviewer would."""

    def __init__(self, verdicts: Sequence[PropositionVerdict], model: str = "offline-replay"):
        self._verdicts = list(verdicts)
        self._model = model

    async def review(self, request: ReviewRequest) -> ReviewResponse:
        asked = {p.id for p in request.propositions}
        return ReviewResponse(model=self._model, verdicts=[v for v in self._verdicts if v.id in asked])


class OnceSystemOne:
    """Live System-1: one Jev call per document, shared by both pipelines so they see the same signals."""

    def __init__(self, inner: SystemOne):
        self._inner = inner
        self._outcome: SystemOneResult | SystemOneError | None = None

    async def evaluate(self, state: Mapping[str, Any]) -> SystemOneResult:
        if self._outcome is None:
            try:
                self._outcome = await self._inner.evaluate(state)
            except SystemOneError as exc:
                self._outcome = exc
        if isinstance(self._outcome, SystemOneError):
            raise self._outcome
        return self._outcome


def context_metadata(context: SubmissionContext) -> dict[str, str]:
    """Explicit submission context, passed to both systems as metadata. 'infer' and unset values are left out."""
    metadata: dict[str, str] = {}
    if context.intended_audience is not AudienceOption.INFER:
        metadata["intended_audience"] = context.intended_audience.value
    if context.release_requested is not None:
        metadata["release_requested"] = str(context.release_requested).lower()
    for key in ("purpose", "proposed_action", "source_unit"):
        if getattr(context, key):
            metadata[key] = getattr(context, key)
    return metadata


@dataclass
class DocumentResult:
    document: CorpusDocument
    decisions: dict[str, TriageDecision] = field(default_factory=dict)


async def evaluate_document(
    doc: CorpusDocument, policy: Policy, battery: Mapping[str, QuestionSpec],
    system_one: SystemOne, system_two: SystemTwo | None,
) -> DocumentResult:
    result = DocumentResult(doc)
    for mode in PIPELINES:
        request = TriageRequest(document=doc.document, metadata=context_metadata(doc.context), mode=mode)
        result.decisions[mode] = await triage(
            request, policy=policy, battery=battery, system_one=system_one, system_two=system_two,
        )
    return result


async def evaluate_offline(
    documents: Sequence[CorpusDocument], policy: Policy, battery: Mapping[str, QuestionSpec] = BATTERY,
) -> list[DocumentResult]:
    return [
        await evaluate_document(doc, policy, battery, ReplaySystemOne(doc.signals), ReplaySystemTwo(doc.system_two_verdicts))
        for doc in documents
    ]


async def evaluate_live(
    documents: Sequence[CorpusDocument], policy: Policy, battery: Mapping[str, QuestionSpec] = BATTERY,
) -> list[DocumentResult]:
    import httpx
    from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy, TypeSafeError
    from src.config import Settings
    from src.system_one import JevSystemOne
    from src.system_two import HttpSystemTwo

    settings = Settings.from_env()
    if not settings.system2_url:
        print("warning: SYSTEM2_URL is not set, so the hybrid pipeline cannot call System-2", file=sys.stderr)
    async with AsyncExitStack() as stack:
        try:
            client = await stack.enter_async_context(AsyncTypeSafeClient(
                model=settings.jev_model,
                base_url=settings.typesafe_base_url,
                timeout=settings.jev_timeout_s,
                retry=RetryPolicy(
                    max_retries=settings.jev_max_retries, timeout=settings.jev_timeout_s * (settings.jev_max_retries + 1),
                ),
            ))
        except TypeSafeError as exc:
            raise SystemExit(f"--live needs Jev credentials: {exc}") from exc
        jev = JevSystemOne(client, battery, settings.jev_model)
        reviewer = None
        if settings.system2_url:
            http = await stack.enter_async_context(httpx.AsyncClient())
            reviewer = HttpSystemTwo(http, settings.system2_url, settings.system2_timeout_s)
        return [await evaluate_document(doc, policy, battery, OnceSystemOne(jev), reviewer) for doc in documents]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Rate:
    numerator: int
    denominator: int

    @property
    def value(self) -> float | None:
        return self.numerator / self.denominator if self.denominator else None

    def as_dict(self) -> dict[str, object]:
        value = self.value
        return {"value": None if value is None else round(value, 4), "numerator": self.numerator,
                "denominator": self.denominator}


def _truths(decision: TriageDecision) -> dict[str, Truth]:
    return {r.rule_id: r.truth for r in decision.rules}


def high_stakes_misses(result: DocumentResult, mode: str, policy: Policy) -> tuple[list[str], int]:
    """(expected high-stakes rules settled FALSE, number of expected high-stakes rules)."""
    stakes = {r.rule_id: r.stakes for r in policy.rules}
    expected = [r for r in result.document.expected_fired_rules if stakes[r] == "high"]
    truths = _truths(result.decisions[mode])
    return [r for r in expected if truths.get(r) is Truth.FALSE], len(expected)


def accepted_yes_verdicts(decision: TriageDecision) -> list[PropositionVerdict]:
    """System-2 yes verdicts the router counted: first per proposition, not discarded."""
    asked = {p.id for p in decision.system_two.propositions}
    discarded = {d.id for d in decision.system_two.discarded if d.reason == "UNGROUNDED_YES"}
    accepted: dict[str, PropositionVerdict] = {}
    for v in decision.system_two.verdicts:
        if v.id in asked and v.id not in accepted and v.verdict == "yes" and v.id not in discarded:
            accepted[v.id] = v
    return list(accepted.values())


def span_matches_label(verdict: PropositionVerdict, doc: CorpusDocument) -> bool:
    span = find_quote_span(verdict.evidence, doc.document)
    label = doc.expected_evidence_substrings.get(verdict.id)
    if span is None or label is None:
        return False
    gold = find_quote_span(label, doc.document)
    return gold is not None and max(span[0], gold[0]) < min(span[1], gold[1])


def compute_metrics(results: Sequence[DocumentResult], policy: Policy, mode: str) -> dict[str, Rate | None]:
    missed = expected_high = 0
    benign = escalated = 0
    open_fast = resolved = 0
    yes_total = yes_located = 0
    offices_ok = verdicts_ok = 0
    for result in results:
        doc, decision = result.document, result.decisions[mode]
        misses, count = high_stakes_misses(result, mode, policy)
        missed, expected_high = missed + len(misses), expected_high + count
        if doc.expected_verdict is Verdict.CLEARED:
            benign += 1
            escalated += decision.verdict is not Verdict.CLEARED
        if mode == "hybrid" and result.decisions["fast"].verdict is Verdict.HUMAN_REVIEW:
            open_fast += 1
            resolved += decision.verdict is not Verdict.HUMAN_REVIEW
        if mode == "hybrid":
            for verdict in accepted_yes_verdicts(decision):
                yes_total += 1
                yes_located += span_matches_label(verdict, doc)
        offices_ok += set(decision.offices) | set(decision.review_offices) == doc.expected_offices
        verdicts_ok += decision.verdict is doc.expected_verdict
    hybrid = mode == "hybrid"
    return {
        "high_stakes_fnr": Rate(missed, expected_high),
        "false_escalation_rate": Rate(escalated, benign),
        "system2_resolution_rate": Rate(resolved, open_fast) if hybrid else None,
        "span_grounding_accuracy": Rate(yes_located, yes_total) if hybrid else None,
        "office_routing_exact_match": Rate(offices_ok, len(results)),
        "verdict_accuracy": Rate(verdicts_ok, len(results)),
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

METRIC_LABELS = {
    "high_stakes_fnr": "High-stakes false negative rate (lower is better)",
    "false_escalation_rate": "False escalation rate (lower is better)",
    "system2_resolution_rate": "System-2 resolution rate",
    "span_grounding_accuracy": "Span grounding accuracy",
    "office_routing_exact_match": "Office routing exact match",
    "verdict_accuracy": "Verdict accuracy",
}


def _cell(rate: Rate | None) -> str:
    if rate is None:
        return "n/a"
    if rate.value is None:
        return f"- (0/{rate.denominator})"
    return f"{rate.value:.1%} ({rate.numerator}/{rate.denominator})"


def _outcome(decision: TriageDecision) -> str:
    offices = sorted(set(decision.offices) | set(decision.review_offices))
    return f"{decision.verdict.value}" + (f" ({', '.join(offices)})" if offices else "")


def _mark(ok: bool) -> str:
    return "yes" if ok else "**no**"


def render_markdown(
    results: Sequence[DocumentResult], metrics: Mapping[str, Mapping[str, Rate | None]], policy: Policy, mode: str,
) -> str:
    source = ("offline replay of hand-authored System-1 signals and System-2 verdicts; these figures test the gate, "
              "not the models") if mode == "offline" else "live Jev and System-2 calls"
    lines = [
        f"# JevGate evaluation: {len(results)} documents, policy {policy.policy_id} {policy.version}",
        "",
        f"Source: {source}.",
        "",
        "| Metric | System-1 only (fast) | Hybrid (System-1 + System-2) |",
        "| --- | ---: | ---: |",
    ]
    for key, label in METRIC_LABELS.items():
        lines.append(f"| {label} | {_cell(metrics['fast'][key])} | {_cell(metrics['hybrid'][key])} |")

    lines += ["", "| Category | Documents | Fast verdict accuracy | Hybrid verdict accuracy |", "| --- | ---: | ---: | ---: |"]
    for category in CATEGORIES:
        group = [r for r in results if r.document.category == category]
        if not group:
            continue
        cells = [_cell(Rate(sum(r.decisions[m].verdict is r.document.expected_verdict for r in group), len(group)))
                 for m in PIPELINES]
        lines.append(f"| {category} | {len(group)} | {cells[0]} | {cells[1]} |")

    lines += ["", "| Document | Category | Expected | Fast | Hybrid | Hybrid correct |", "| --- | --- | --- | --- | --- | :---: |"]
    for r in results:
        doc = r.document
        expected = doc.expected_verdict.value + (f" ({', '.join(sorted(doc.expected_offices))})" if doc.expected_offices else "")
        hybrid = r.decisions["hybrid"]
        correct = hybrid.verdict is doc.expected_verdict and set(hybrid.offices) | set(hybrid.review_offices) == doc.expected_offices
        lines.append(f"| {doc.id} | {doc.category} | {expected} | {_outcome(r.decisions['fast'])} | {_outcome(hybrid)} | {_mark(correct)} |")
    return "\n".join(lines) + "\n"


def _decision_record(decision: TriageDecision) -> dict[str, object]:
    """The parts of a decision that do not change between runs (no IDs, no timings)."""
    return {
        "verdict": decision.verdict.value,
        "decided_by": decision.decided_by,
        "offices": decision.offices,
        "review_offices": decision.review_offices,
        "priority": decision.priority,
        "risk_index": decision.risk_index,
        "rules": {r.rule_id: r.truth.value for r in decision.rules},
        "escalations": [e.code for e in decision.escalations],
        "system_two": {
            "invoked": decision.system_two.invoked,
            "propositions": [p.id for p in decision.system_two.propositions],
            "verdicts": {v.id: v.verdict for v in decision.system_two.verdicts},
            "discarded": [{"id": d.id, "reason": d.reason} for d in decision.system_two.discarded],
        },
        "model": decision.model,
    }


def build_report(
    results: Sequence[DocumentResult], policy: Policy, mode: str, corpus_path: Path,
) -> tuple[dict[str, dict[str, Rate | None]], dict[str, object]]:
    metrics = {m: compute_metrics(results, policy, m) for m in PIPELINES}
    documents = []
    for r in results:
        doc = r.document
        spans = {}
        for v in accepted_yes_verdicts(r.decisions["hybrid"]):
            span = find_quote_span(v.evidence, doc.document)
            spans[v.id] = {"span": list(span) if span else None, "matches_label": span_matches_label(v, doc)}
        documents.append({
            "id": doc.id,
            "category": doc.category,
            "expected": {"verdict": doc.expected_verdict.value, "fired_rules": list(doc.expected_fired_rules),
                         "offices": sorted(doc.expected_offices), "high_stakes_risk": doc.expected_high_stakes_risk},
            **{m: _decision_record(r.decisions[m]) for m in PIPELINES},
            "high_stakes_missed": {m: high_stakes_misses(r, m, policy)[0] for m in PIPELINES},
            "evidence_spans": spans,
        })
    report = {
        "mode": mode,
        "corpus": str(corpus_path.relative_to(ROOT)) if corpus_path.is_relative_to(ROOT) else str(corpus_path),
        "corpus_sha256": hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
        "policy": {"policy_id": policy.policy_id, "version": policy.version},
        "metrics": {m: {k: (v.as_dict() if v else None) for k, v in metrics[m].items()} for m in PIPELINES},
        "documents": documents,
    }
    return metrics, report


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = ap.add_mutually_exclusive_group()
    source.add_argument("--offline", dest="live", action="store_false", help="Replay the corpus's mock answers (default)")
    source.add_argument("--live", dest="live", action="store_true", help="Call Jev and the configured System-2")
    ap.set_defaults(live=False)
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    ap.add_argument("--out", type=Path, help="Write metrics and every document's outcome as JSON")
    args = ap.parse_args(argv)

    policy = load_policy(args.policy, BATTERY)
    documents = load_corpus(args.corpus, policy)
    mode = "live" if args.live else "offline"
    run = evaluate_live if args.live else evaluate_offline
    results = asyncio.run(run(documents, policy))
    metrics, report = build_report(results, policy, mode, args.corpus.resolve())
    print(render_markdown(results, metrics, policy, mode), end="")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
