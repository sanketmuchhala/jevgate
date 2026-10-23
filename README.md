# JevGate

Calibrated policy triage and institutional risk routing. JevGate reads an unstructured institutional document (a mission brief, an operational record, a draft press release, a forwarded email) and decides whether it can proceed, which office must see it, whether it must be stopped, or whether a person has to look at it. For each verdict it also reports how sure it is and why.

It is a hybrid System-1/System-2 engine. System-1 is [Jev 1.14](https://docs.typesafe.ai/models.md) (`jev-1.14.0`), TypeSafe AI's decision model, called from a Python FastAPI service. System-2 is a reasoning LLM behind a small TypeScript service built on the Vercel AI SDK. It only sees the specific propositions System-1 was unsure about.

## Core idea

Jev does not generate text. It answers typed questions with calibrated probability distributions. JevGate keeps a strict boundary on top of that:

1. **The models supply** probabilities (Jev) and verdicts with quoted evidence (System-2).
2. **Python owns** the policy, the thresholds, the three-valued rule logic, which uncertainty matters, which reviewer verdicts count, and the final route.

No model chooses a route. No model can clear a high-stakes rule that Jev was unsure about.

## Architecture

```text
document + metadata
   │
   ▼
┌──────────────────────────────────────────────────────────────────────┐
│ SYSTEM-1 · Jev 1.14 · one request, 12 questions in parallel          │
│   2 Choice  document_type, intended_audience                         │
│   2 Score   sensitivity (0-3), urgency (0-3)                         │
│   8 Noul    personal_data, movement_details, negotiating_position,   │
│             financial_commitment, legal_exposure,                    │
│             security_incident, foreign_official_named,               │
│             release_requested                                        │
└───────────────┬──────────────────────────────────────────────────────┘
                │ probability distributions + confidence
                ▼
┌──────────────────────────────────────────────────────────────────────┐
│ POLICY ENGINE (Python, policies/default.json)                        │
│ • P(condition) from the distribution: P(sensitivity ≥ 3),            │
│   P(audience ∈ {partner_government, public_release}), P(yes)         │
│ • stakes bands → TRUE / FALSE / UNCERTAIN                            │
│     high:     TRUE ≥ 0.7, FALSE ≤ 0.1                                │
│     standard: TRUE ≥ 0.7, FALSE ≤ 0.3                                │
│ • Kleene AND per rule: one FALSE settles it                          │
└───────────────┬──────────────────────────────────────────────────────┘
                │
        BLOCK fired? ── yes ──► BLOCKED  (no reviewer can lift it)
                │ no
        any rule UNCERTAIN? ── no ──► ROUTED / CLEARED  (System-1 path)
                │ yes
                ▼
┌──────────────────────────────────────────────────────────────────────┐
│ SYSTEM-2 · system2/ · Vercel AI SDK generateText + Output.object     │
│ Only the open conditions of open rules, as yes/no propositions.      │
│ Returns yes / no / cannot_determine + verbatim evidence.             │
└───────────────┬──────────────────────────────────────────────────────┘
                │ Python filters: a yes needs a verbatim quote;
                │ a no is ignored by high-stakes rules
                ▼
        re-evaluate ──► BLOCKED / ROUTED / CLEARED, or HUMAN_REVIEW
```

## Key guarantees

- **Calibrated, not thresholded on a point estimate (`src/policy.py`).** A Score condition such as "sensitivity is Restricted" is read as `P(level ≥ 3)` from Jev's level distribution, not from the expected score. A Choice condition sums the probabilities of the options it names.
- **Thresholds scale with stakes.** Each rule is `high` or `standard`. Both bands need `p ≥ 0.7` to call a condition TRUE. To call it FALSE, a high-stakes rule needs `p ≤ 0.1` and a standard one needs `p ≤ 0.3`. In between, the condition is UNCERTAIN. The bands live in the policy file.
- **Only uncertainty that can change the outcome escalates.** Rules are conjunctions evaluated with three-valued (Kleene) logic, so a single confident FALSE settles a rule however unsure Jev is about its other conditions. A memo that clearly stays internal never reaches review because Jev is torn about whether it mentions travel. Uncertain conditions inside settled rules are never sent anywhere.
- **BLOCK short-circuits.** If a BLOCK rule fires on Jev alone, the verdict is final and System-2 is not called. Open rules are listed in `review_offices` for awareness.
- **Asymmetric trust in System-2 (`src/router.py`).** The reviewer rules only on propositions Python sends it. A `yes` counts only if its `evidence` is a verbatim quote from the document; otherwise it is discarded as `UNGROUNDED_YES`. A `no` can settle a standard-stakes rule but never a high-stakes one, so it is logged as `HIGH_STAKES_NO` and the rule goes to a human. Verdicts with unknown or duplicate IDs are discarded. The reviewer can confirm risk but cannot remove high-stakes risk.
- **Fail closed.** If Jev errors or times out, the document becomes `HUMAN_REVIEW` with `risk_index = 1.0` and is never cleared. If System-2 errors, its open rules go to a human.
- **Validated policy.** At startup every condition is checked against the question battery: signals exist, `yes` targets a Noul, `at_least` names a real Score level, `in` names real Choice options and not all of them. Rules must reference existing conditions, rule IDs must be unique, and every condition must be used. All problems are reported together.
- **Pinned model.** Bands tuned against one Jev version should not drift silently, so JevGate sends `jev-1.14.0` rather than the `jev-latest` alias. Every decision records the versioned model ID that answered.

The reasoning behind these choices, and their limits, is in [DECISIONS.md](DECISIONS.md).

## Verdicts

| Verdict | Meaning | `decided_by` |
|---|---|---|
| `CLEARED` | No rule applies. | `SYSTEM_ONE`, or `SYSTEM_TWO` if the reviewer settled the open rules |
| `ROUTED` | A ROUTE rule fired; `offices` lists who owns it. | as above |
| `BLOCKED` | A BLOCK rule fired; do not distribute. | as above |
| `HUMAN_REVIEW` | Open uncertainty could still change the outcome. `escalations` says which rule, which condition, and why. | `PENDING_HUMAN` |

Every decision also carries `priority` (`EXPEDITE` when `P(urgency ≥ 2) ≥ 0.5`) and a `risk_index` (0 to 1) for ordering the review queue. The index is a weighted composite of normalized sensitivity, urgency, and the most likely rule scaled by its stakes. It never changes a verdict. The weights are in the policy file.

## Quick start

### 1. Install

Python 3.10+ (for the TypeSafe SDK) and Node 22.18+ (to run TypeScript directly).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
(cd system2 && npm install)
cp .env.example .env    # then add TYPESAFE_API_KEY, and OPENROUTER_API_KEY for System-2
```

Jev can also be reached through OpenRouter with the same SDK; `.env.example` shows the settings.

### 2. Run

```bash
(cd system2 && npm start)                    # System-2 reviewer on :8787 (optional)
.venv/bin/uvicorn src.app:app --port 8000    # JevGate on :8000
```

```bash
curl -s localhost:8000/v1/triage -H 'content-type: application/json' \
  -d '{"document": "Motorcade route: north bridge to the Hotel Arden, rooms 1204-1206.", "mode": "fast"}'
```

| Endpoint | Purpose |
|---|---|
| `POST /v1/triage` | `{document, metadata?, mode?}` → decision. `mode: "fast"` skips System-2, so open uncertainty goes straight to a human. `"hybrid"` (the default) tries System-2 first. |
| `GET /v1/policy` | The loaded policy: bands, conditions, rules. |
| `GET /v1/questions` | The question battery sent to Jev. |
| `GET /healthz` | Model, policy version, whether System-2 is configured. |
| `GET /docs` | OpenAPI UI. |

### 3. Samples and latency

```bash
.venv/bin/python scripts/run_samples.py                                   # one pass over samples/
.venv/bin/python scripts/run_samples.py --runs 20 --mode fast --out outputs/bench.json
```

`samples/` holds six fictional documents. Each is labeled with the verdict it was written to produce: a routine status note, a delegation itinerary sent to a host ministry, procurement minutes releasing a payment, a stolen-laptop incident, a draft press release that names a foreign official, and an ambiguous forwarded email. The runner prints each sample's verdict and route, then the p50, p95, and max latency of decisions made without System-2. It reports three latency figures: Jev's call alone, the server's total, and the client's round trip.

**No live numbers are in this repo yet.** The tests below run offline. Run the command above with a key to measure the System-1 path on your own network before relying on any latency figure.

### 4. Test

```bash
.venv/bin/python -m unittest discover -s tests -t .    # 43 tests, no API key needed
(cd system2 && npm test && npm run typecheck)          # 4 tests + tsc
```

The Python tests cover:

- the confidence formulas, checked against TypeSafe's published worked examples
- policy validation, with every class of bad reference
- Kleene evaluation, and uncertainty in a settled rule being ignored
- the asymmetric high/standard bands
- BLOCK short-circuiting
- each System-2 rule: grounded yes, ungrounded yes, high-stakes no, unknown and duplicate IDs, missing verdicts
- fail-closed behavior for both systems
- priority and risk ordering
- the HTTP API

The Jev adapter tests drive the real TypeSafe SDK against a mock HTTP transport, so the wire format is tested too. The TypeScript tests use the AI SDK's `MockLanguageModelV4`.

## Layout

```text
src/
  app.py            FastAPI app and lifespan (one pooled Jev client per process)
  questions.py      the 12-question battery sent to Jev in one request
  system_one.py     Jev adapter: battery → TypeSafe SDK → typed signals; fails loudly on gaps
  policy.py         policy loading and validation, P(condition), bands, Kleene rules, risk index
  router.py         the gate: verdict, which propositions go to System-2, which verdicts count
  system_two.py     HTTP client for the reviewer
  schemas.py        Pydantic contracts for everything above
  calibration.py    TypeSafe's confidence formulas and distribution reads
  config.py         settings from the environment
policies/default.json   8 rules over 11 named conditions
system2/            TypeScript reviewer: Vercel AI SDK 7, zod schema, OpenRouter
samples/            6 fictional documents
scripts/run_samples.py  verdict table and latency report against a running server
tests/              43 offline tests
```

All names, countries, and organizations in `samples/` and `tests/` are fictional.
