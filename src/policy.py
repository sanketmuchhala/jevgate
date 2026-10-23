from __future__ import annotations
import json
from collections.abc import Mapping
from pathlib import Path
from .calibration import p_at_least, p_in
from .schemas import (
    Band, ChoiceSignal, ChoiceSpec, Condition, ConditionResult, NoulSignal, NoulSpec, Policy,
    QuestionSpec, RuleResult, ScoreSignal, ScoreSpec, Signal, Truth,
)

DEFAULT_POLICY_PATH = Path(__file__).resolve().parent.parent / "policies" / "default.json"


class PolicyError(ValueError):
    """The policy file references signals, options, levels, or conditions that do not exist."""


def load_policy(path: Path, battery: Mapping[str, QuestionSpec]) -> Policy:
    policy = Policy.model_validate(json.loads(Path(path).read_text()))
    problems = validate_policy(policy, battery)
    if problems:
        raise PolicyError(f"{path}: " + "; ".join(problems))
    return policy


def validate_policy(policy: Policy, battery: Mapping[str, QuestionSpec]) -> list[str]:
    """Every reference in the policy must resolve against the question battery. Returns all problems."""
    problems: list[str] = []

    for stakes in ("high", "standard"):
        if stakes not in policy.bands:
            problems.append(f"bands: missing '{stakes}'")
        if stakes not in policy.risk_weights.stakes:
            problems.append(f"risk_weights.stakes: missing '{stakes}'")

    for cid, cond in policy.conditions.items():
        spec = battery.get(cond.signal)
        if spec is None:
            problems.append(f"condition {cid}: unknown signal '{cond.signal}'")
            continue
        if cond.test == "yes":
            if not isinstance(spec, NoulSpec):
                problems.append(f"condition {cid}: test 'yes' needs a noul signal, '{cond.signal}' is {spec.type}")
            if cond.value is not None:
                problems.append(f"condition {cid}: test 'yes' takes no value")
        elif cond.test == "at_least":
            if not isinstance(spec, ScoreSpec):
                problems.append(f"condition {cid}: test 'at_least' needs a score signal, '{cond.signal}' is {spec.type}")
            elif not isinstance(cond.value, int) or not 1 <= cond.value < len(spec.levels):
                problems.append(f"condition {cid}: at_least value must be a level in 1..{len(spec.levels) - 1}")
        elif cond.test == "in":
            if not isinstance(spec, ChoiceSpec):
                problems.append(f"condition {cid}: test 'in' needs a choice signal, '{cond.signal}' is {spec.type}")
            elif not isinstance(cond.value, list) or not cond.value:
                problems.append(f"condition {cid}: 'in' value must be a nonempty list of options")
            else:
                unknown = [o for o in cond.value if o not in spec.options]
                if unknown:
                    problems.append(f"condition {cid}: unknown options {unknown} for '{cond.signal}'")
                if len(set(cond.value)) == len(spec.options):
                    problems.append(f"condition {cid}: 'in' lists every option, so it is always true")

    seen_rules: set[str] = set()
    used: set[str] = set()
    for rule in policy.rules:
        if rule.rule_id in seen_rules:
            problems.append(f"rule {rule.rule_id}: duplicate rule_id")
        seen_rules.add(rule.rule_id)
        if len(set(rule.when)) != len(rule.when):
            problems.append(f"rule {rule.rule_id}: repeats a condition")
        for cid in rule.when:
            if cid not in policy.conditions:
                problems.append(f"rule {rule.rule_id}: unknown condition '{cid}'")
            used.add(cid)
    for cid in sorted(set(policy.conditions) - used):
        problems.append(f"condition {cid}: not used by any rule")

    exp = policy.expedite
    exp_spec = battery.get(exp.signal)
    if not isinstance(exp_spec, ScoreSpec):
        problems.append(f"expedite: '{exp.signal}' must be a score signal")
    elif not 1 <= exp.at_least < len(exp_spec.levels):
        problems.append(f"expedite: at_least must be a level in 1..{len(exp_spec.levels) - 1}")

    for sid in policy.risk_weights.scores:
        if not isinstance(battery.get(sid), ScoreSpec):
            problems.append(f"risk_weights.scores: '{sid}' must be a score signal")
    if sum(policy.risk_weights.scores.values()) + policy.risk_weights.rules <= 0:
        problems.append("risk_weights: weights sum to zero")

    return problems


def condition_probability(cond: Condition, signal: Signal) -> float:
    """P(condition holds), taken from the full distribution Jev returned."""
    if cond.test == "yes" and isinstance(signal, NoulSignal):
        return signal.noul
    if cond.test == "at_least" and isinstance(signal, ScoreSignal):
        return p_at_least(signal.probabilities, int(cond.value))
    if cond.test == "in" and isinstance(signal, ChoiceSignal):
        return p_in(signal.probabilities, cond.value)
    raise PolicyError(f"signal type {signal.type} does not support test '{cond.test}'")


def band_truth(p: float, band: Band) -> Truth:
    if p >= band.yes:
        return Truth.TRUE
    if p <= band.no:
        return Truth.FALSE
    return Truth.UNCERTAIN


def kleene_and(truths: list[Truth]) -> Truth:
    """One FALSE settles a conjunction, so uncertainty elsewhere in the rule cannot matter."""
    if Truth.FALSE in truths:
        return Truth.FALSE
    if all(t is Truth.TRUE for t in truths):
        return Truth.TRUE
    return Truth.UNCERTAIN


def evaluate_rules(
    policy: Policy,
    signals: Mapping[str, Signal],
    overrides: Mapping[str, Truth] | None = None,
) -> list[RuleResult]:
    """Evaluate every rule. `overrides` holds System-2 rulings on conditions System-1 left UNCERTAIN.

    A System-2 FALSE is never applied to a high-stakes rule: the reviewer can confirm
    a high-stakes finding but cannot clear one that Jev was unsure about.
    """
    overrides = overrides or {}
    results: list[RuleResult] = []
    for rule in policy.rules:
        band = policy.bands[rule.stakes]
        conditions: list[ConditionResult] = []
        for cid in rule.when:
            cond = policy.conditions[cid]
            p = condition_probability(cond, signals[cond.signal])
            truth = band_truth(p, band)
            resolved_by = None if truth is Truth.UNCERTAIN else "SYSTEM_ONE"
            override = overrides.get(cid)
            if truth is Truth.UNCERTAIN and override is not None:
                if override is Truth.TRUE or (override is Truth.FALSE and rule.stakes == "standard"):
                    truth, resolved_by = override, "SYSTEM_TWO"
            conditions.append(ConditionResult(
                condition_id=cid, proposition=cond.proposition, probability=round(p, 4),
                truth=truth, resolved_by=resolved_by,
            ))
        results.append(RuleResult(
            rule_id=rule.rule_id, title=rule.title, action=rule.action, office=rule.office,
            stakes=rule.stakes, truth=kleene_and([c.truth for c in conditions]), conditions=conditions,
        ))
    return results


def risk_index(
    policy: Policy,
    battery: Mapping[str, QuestionSpec],
    signals: Mapping[str, Signal],
    rules: list[RuleResult],
) -> float:
    """Composite 0..1 score for ordering the review queue. Weights live in the policy file."""
    weights = policy.risk_weights
    total = 0.0
    for sid, weight in weights.scores.items():
        spec, signal = battery[sid], signals[sid]
        total += weight * signal.score / (len(spec.levels) - 1)

    def likelihood(rule: RuleResult) -> float:
        # Fuzzy AND over the conditions; a System-2 ruling counts as certain.
        return min(
            (1.0 if c.truth is Truth.TRUE else 0.0) if c.resolved_by == "SYSTEM_TWO" else c.probability
            for c in rule.conditions
        )

    total += weights.rules * max((weights.stakes[r.stakes] * likelihood(r) for r in rules), default=0.0)
    return round(min(1.0, total / (sum(weights.scores.values()) + weights.rules)), 3)
