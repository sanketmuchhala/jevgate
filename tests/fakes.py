from __future__ import annotations
from typing import Any
from src.calibration import choice_confidence, noul_confidence, score_confidence
from src.questions import BATTERY
from src.schemas import (
    ChoiceSignal, NoulSignal, ReviewRequest, ReviewResponse, ScoreSignal, ScoreSpec, Signal,
    SystemOneResult,
)
from src.system_one import SystemOneError
from src.system_two import SystemTwoError


def choice(top: str, p: float = 0.95, qid: str = "intended_audience") -> ChoiceSignal:
    options = list(BATTERY[qid].options)
    rest = (1.0 - p) / (len(options) - 1)
    probs = {o: (p if o == top else rest) for o in options}
    return ChoiceSignal(choice=top, probabilities=probs, confidence=round(choice_confidence(probs.values()), 4))


def choice_split(probs: dict[str, float], qid: str = "intended_audience") -> ChoiceSignal:
    full = {o: probs.get(o, 0.0) for o in BATTERY[qid].options}
    return ChoiceSignal(
        choice=max(full, key=full.get), probabilities=full, confidence=round(choice_confidence(full.values()), 4),
    )


def score(probs: dict[int, float]) -> ScoreSignal:
    return ScoreSignal(
        score=round(sum(level * p for level, p in probs.items()), 4),
        probabilities=probs,
        confidence=round(score_confidence(probs), 4),
    )


def noul(p: float) -> NoulSignal:
    return NoulSignal(noul=p, confidence=round(noul_confidence(p), 4))


def signals(**overrides: Signal | float) -> dict[str, Signal]:
    """A routine internal record that trips no rule. Floats are shorthand for Noul probabilities."""
    base: dict[str, Signal] = {
        "document_type": choice("operational_record", 0.9, qid="document_type"),
        "intended_audience": choice("internal_team", 0.95),
        "sensitivity": score({0: 0.1, 1: 0.85, 2: 0.05, 3: 0.0}),
        "urgency": score({0: 0.9, 1: 0.1, 2: 0.0, 3: 0.0}),
    }
    for qid, spec in BATTERY.items():
        if spec.type == "noul":
            base[qid] = noul(0.02)
    for qid, value in overrides.items():
        base[qid] = noul(value) if isinstance(value, float) else value
    return base


def external() -> ChoiceSignal:
    return choice("partner_government", 0.9)


class FakeSystemOne:
    def __init__(self, sigs: dict[str, Signal] | None = None, error: str | None = None):
        self.sigs = sigs or signals()
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def evaluate(self, state: dict[str, Any]) -> SystemOneResult:
        self.calls.append(state)
        if self.error:
            raise SystemOneError(self.error)
        return SystemOneResult(model="jev-1.14.0", signals=self.sigs, input_tokens=900, latency_ms=180.0)


class FakeSystemTwo:
    def __init__(self, verdicts: list[dict[str, str]] | None = None, error: str | None = None):
        self.verdicts = verdicts or []
        self.error = error
        self.requests: list[ReviewRequest] = []

    async def review(self, request: ReviewRequest) -> ReviewResponse:
        self.requests.append(request)
        if self.error:
            raise SystemTwoError(self.error)
        return ReviewResponse(model="anthropic/claude-sonnet-5.5", verdicts=self.verdicts)


def jev_payload(sigs: dict[str, Signal], model: str = "jev-1.14.0") -> dict[str, Any]:
    """The JSON body POST /v1/systemone would return for these signals."""
    answers: dict[str, Any] = {}
    for qid, sig in sigs.items():
        spec = BATTERY[qid]
        if isinstance(sig, ChoiceSignal):
            answers[qid] = {"type": "choice", "choice": sig.choice, "confidence": sig.confidence,
                            "probabilities": sig.probabilities}
        elif isinstance(sig, ScoreSignal):
            assert isinstance(spec, ScoreSpec)
            answers[qid] = {"type": "score", "score": sig.score, "confidence": sig.confidence,
                            "legend": {str(i): level for i, level in enumerate(spec.levels)},
                            "probabilities": {str(k): v for k, v in sig.probabilities.items()}}
        else:
            answers[qid] = {"type": "noul", "noul": sig.noul}
    return {"model": model, "answers": answers, "usage": {"input_tokens": 1234, "output_tokens": 60}}

