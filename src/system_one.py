from __future__ import annotations
import time
from collections.abc import Mapping
from typing import Any, Protocol
from typesafe_sdk import AsyncTypeSafeClient, Choice, ChoiceAnswer, Noul, NoulAnswer, Score, ScoreAnswer, TypeSafeError
from .calibration import noul_confidence
from .schemas import (
    ChoiceSignal, ChoiceSpec, NoulSignal, NoulSpec, QuestionSpec, ScoreSignal, ScoreSpec, Signal, SystemOneResult,
)


class SystemOneError(RuntimeError):
    """Jev could not answer the battery. The router fails closed on this."""


class SystemOne(Protocol):
    async def evaluate(self, state: Mapping[str, Any]) -> SystemOneResult: ...


def build_questions(battery: Mapping[str, QuestionSpec]) -> dict[str, Choice | Score | Noul]:
    questions: dict[str, Choice | Score | Noul] = {}
    for qid, spec in battery.items():
        if isinstance(spec, ChoiceSpec):
            questions[qid] = Choice(instructions=spec.instructions, criteria=spec.options)
        elif isinstance(spec, ScoreSpec):
            questions[qid] = Score(instructions=spec.instructions, criteria=spec.levels)
        else:
            questions[qid] = Noul(instructions=spec.instructions)
    return questions


class JevSystemOne:
    """One Jev request per document: every battery question, evaluated in parallel."""

    def __init__(self, client: AsyncTypeSafeClient, battery: Mapping[str, QuestionSpec], model: str):
        self._client = client
        self._battery = dict(battery)
        self._questions = build_questions(battery)
        self._model = model

    async def evaluate(self, state: Mapping[str, Any]) -> SystemOneResult:
        start = time.perf_counter()
        try:
            response = await self._client.system_one(state=dict(state), questions=self._questions, model=self._model)
        except TypeSafeError as exc:
            raise SystemOneError(f"{type(exc).__name__}: {exc}") from exc
        latency_ms = (time.perf_counter() - start) * 1000

        signals: dict[str, Signal] = {}
        problems: list[str] = []
        for qid, spec in self._battery.items():
            answer = response.answers.get(qid)
            if isinstance(spec, ChoiceSpec) and isinstance(answer, ChoiceAnswer):
                if answer.choice not in spec.options:
                    problems.append(f"{qid}: choice '{answer.choice}' is not an option")
                    continue
                signals[qid] = ChoiceSignal(
                    choice=answer.choice, probabilities=dict(answer.probabilities), confidence=answer.confidence,
                )
            elif isinstance(spec, ScoreSpec) and isinstance(answer, ScoreAnswer):
                signals[qid] = ScoreSignal(
                    score=answer.score,
                    probabilities={int(level): p for level, p in answer.probabilities.items()},
                    confidence=answer.confidence,
                )
            elif isinstance(spec, NoulSpec) and isinstance(answer, NoulAnswer):
                signals[qid] = NoulSignal(noul=answer.noul, confidence=round(noul_confidence(answer.noul), 4))
            else:
                problems.append(f"{qid}: expected a {spec.type} answer, got {type(answer).__name__}")
        if problems:
            raise SystemOneError("incomplete answers: " + "; ".join(problems))

        return SystemOneResult(
            model=response.model,
            signals=signals,
            input_tokens=response.usage.input_tokens,
            latency_ms=round(latency_ms, 1),
            request_id=response.request_id,
        )
