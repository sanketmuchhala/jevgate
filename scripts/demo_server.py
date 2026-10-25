"""Run JevGate and the reviewer workbench with no API keys.

    python scripts/demo_server.py                 # http://127.0.0.1:8000/app/
    python scripts/demo_server.py --seed          # also open eight cases from the evaluation corpus
    python scripts/demo_server.py --port 8765 --db outputs/demo.sqlite3

Neither model is called. Documents in samples/eval_corpus.jsonl, including the workbench's six
presets, get the corpus's hand-authored System-1 answers and System-2 verdicts. Any other text
gets keyword-based System-1 answers and no System-2 opinion. Every decision records its model as
"demo:...", so nothing here can be mistaken for Jev's output or used as evidence of model quality.
"""
from __future__ import annotations
import argparse
import re
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from scripts.evaluate import DEFAULT_CORPUS, CorpusDocument, expand_signals, load_corpus  # noqa: E402
from src.app import create_app  # noqa: E402
from src.config import Settings  # noqa: E402
from src.policy import DEFAULT_POLICY_PATH, load_policy  # noqa: E402
from src.questions import BATTERY  # noqa: E402
from src.schemas import PropositionVerdict, ReviewRequest, ReviewResponse, Signal, SystemOneResult  # noqa: E402

# Opened by --seed: one per routing outcome the workbench shows differently.
SEED = (
    "eval-07-internal-travel-planning", "eval-20-summary-disclaims-personal-data", "eval-18-arbitration-note-paraphrase",
    "eval-16-draft-aide-memoire-logistics", "eval-15-ministry-letter-personal-mobile", "eval-01-mission-aide-memoire-route",
    "eval-05-field-laptop-breach", "eval-13-forwarded-trade-notes",
)


def keyword_signals(text: str) -> dict[str, Signal]:
    """Crude System-1 stand-in for text outside the corpus, such as an edited revision."""
    t = text.casefold()
    has = lambda *words: any(w in t for w in words)  # noqa: E731
    if has("for immediate release", "press"):
        top = {"public_release": 0.85}
    elif has("ministry of", "government of"):
        top = {"partner_government": 0.85}
    elif has("internal", "task team only", "not for circulation"):
        top = {"internal_team": 0.9}
    else:
        top = {"internal_team": 0.3, "unclear": 0.4}
    audiences = list(BATTERY["intended_audience"].options)
    share = (1 - sum(top.values())) / (len(audiences) - len(top))
    doc_types = list(BATTERY["document_type"].options)
    return expand_signals({
        "document_type": {o: 0.5 if o == "other" else 0.5 / (len(doc_types) - 1) for o in doc_types},
        "intended_audience": {o: top.get(o, share) for o in audiences},
        "sensitivity": [0.0, 0.05, 0.15, 0.8] if has("strictly confidential", "restricted") else [0.1, 0.8, 0.1, 0.0],
        "urgency": [0.0, 0.1, 0.3, 0.6] if has("immediate", "urgent") else [0.8, 0.2, 0.0, 0.0],
        "movement_details": 0.88 if has("motorcade", "flight", "hotel", "route") else 0.04,
        "negotiating_position": 0.85 if has("red line", "fallback") else 0.03,
        "financial_commitment": 0.8 if re.search(r"\b(usd|eur)\b", t) and has("pay", "release", "transfer") else 0.04,
        "security_incident": 0.9 if has("stolen", "breach", "incident") else 0.03,
        "personal_data": 0.75 if has("mobile", "home address", "id number", "passport") else 0.04,
        "foreign_official_named": 0.8 if re.search(r"\bminister (of [a-z]+ )?[a-z]+ [a-z]+", t) else 0.05,
        "legal_exposure": 0.8 if has("arbitration", "litigation", "sanction", "export licence") else 0.03,
        "release_requested": 0.8 if has("be released", "for publication", "for immediate release") else 0.04,
    }, BATTERY)


class DemoSystemOne:
    def __init__(self, corpus: Mapping[str, CorpusDocument]):
        self._corpus = corpus

    async def evaluate(self, state: Mapping[str, Any]) -> SystemOneResult:
        doc = self._corpus.get(state["document"])
        if doc is not None:
            return SystemOneResult(model="demo:corpus-replay", signals=doc.signals, input_tokens=0, latency_ms=0.0)
        return SystemOneResult(model="demo:keyword-heuristic", signals=keyword_signals(state["document"]),
                               input_tokens=0, latency_ms=0.0)


class DemoSystemTwo:
    """Replays corpus verdicts. When asked for quotes the corpus has no verdict for, quotes the labelled evidence."""

    def __init__(self, corpus: Mapping[str, CorpusDocument]):
        self._corpus = corpus

    async def review(self, request: ReviewRequest) -> ReviewResponse:
        doc = self._corpus.get(request.document)
        verdicts = []
        for p in request.propositions:
            replay = next((v for v in doc.system_two_verdicts if v.id == p.id), None) if doc else None
            if replay is not None:
                verdicts.append(replay)
            elif doc is not None and p.id in doc.expected_evidence_substrings:
                verdicts.append(PropositionVerdict(
                    id=p.id, verdict="yes", evidence=doc.expected_evidence_substrings[p.id],
                    rationale="Demo: the corpus's labelled evidence for this condition.",
                ))
            else:
                verdicts.append(PropositionVerdict(
                    id=p.id, verdict="cannot_determine", rationale="Demo: no labelled answer for this text.",
                ))
        return ReviewResponse(model="demo:corpus-replay", verdicts=verdicts)


def build_app(db: str = ":memory:"):
    policy = load_policy(DEFAULT_POLICY_PATH, BATTERY)
    corpus = {doc.document: doc for doc in load_corpus(DEFAULT_CORPUS, policy)}
    return create_app(
        settings=Settings(db_path=Path(db), api_token=None), policy=policy,
        system_one=DemoSystemOne(corpus), system_two=DemoSystemTwo(corpus),
    ), corpus


def seed(base_url: str, corpus: Mapping[str, CorpusDocument]) -> None:
    by_id = {doc.id: doc for doc in corpus.values()}
    with httpx.Client(base_url=base_url, timeout=30) as client:
        for _ in range(100):
            try:
                client.get("/healthz").raise_for_status()
                break
            except httpx.HTTPError:
                time.sleep(0.1)
        for doc_id in SEED:
            doc = by_id[doc_id]
            client.post("/v1/cases", json={
                "title": doc.title, "document": doc.document,
                "context": doc.context.model_dump(mode="json"), "mode": "hybrid",
            }).raise_for_status()
    print(f"seeded {len(SEED)} cases", flush=True)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--db", default=":memory:", help="SQLite file for cases; the default keeps nothing after exit")
    ap.add_argument("--seed", action="store_true", help="Open eight cases from the evaluation corpus at startup")
    args = ap.parse_args(argv)

    app, corpus = build_app(args.db)
    base_url = f"http://{args.host}:{args.port}"
    if args.seed:
        threading.Thread(target=seed, args=(base_url, corpus), daemon=True).start()
    print(f"JevGate demo (no model calls): {base_url}/app/", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
