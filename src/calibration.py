from __future__ import annotations
from collections.abc import Iterable, Mapping

# Confidence formulas published by TypeSafe (docs.typesafe.ai/confidence).
# Jev returns `confidence` on Choice and Score answers; JevGate uses those values
# as-is and only derives Noul confidence. The Choice and Score versions are here
# so tests can check that the API's numbers follow from its own probabilities.


def noul_confidence(p: float) -> float:
    """Distance from a coin flip: 0 at p=0.5, 1 at p=0 or p=1."""
    return abs(2.0 * p - 1.0)


def choice_confidence(probabilities: Iterable[float]) -> float:
    """How far the top option sits above an even split, on a 0..1 scale."""
    probs = list(probabilities)
    n = len(probs)
    return (max(probs) - 1.0 / n) / (1.0 - 1.0 / n)


def score_confidence(probabilities: Mapping[int, float]) -> float:
    """Like choice_confidence, but probability on a far level costs more than on a neighbor."""
    levels = sorted(probabilities)
    probs = [probabilities[level] for level in levels]
    n = len(probs)
    peak = probs.index(max(probs))
    spread = sum(p * abs(i - peak) for i, p in enumerate(probs))
    even_spread = sum(abs(i - (n - 1) / 2) for i in range(n)) / n
    return max(0.0, 1.0 - spread / even_spread)


def p_at_least(probabilities: Mapping[int, float], level: int) -> float:
    """P(score >= level), read from the level distribution rather than the expected score."""
    return min(1.0, sum(p for lvl, p in probabilities.items() if lvl >= level))


def p_in(probabilities: Mapping[str, float], options: Iterable[str]) -> float:
    """P(the chosen option is one of `options`)."""
    return min(1.0, sum(probabilities.get(option, 0.0) for option in options))
