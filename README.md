# JevGate

Calibrated policy triage and institutional risk routing. JevGate reads an unstructured institutional document (a mission brief, an operational record, a draft press release, a forwarded email) and decides whether it can proceed, which office must see it, whether it must be stopped, or whether a person has to look at it. For each verdict it also reports how sure it is and why.

It is a hybrid System-1/System-2 engine. System-1 is [Jev 1.14](https://docs.typesafe.ai/models.md) (`jev-1.14.0`), TypeSafe AI's decision model, called from a Python FastAPI service. System-2 is a reasoning LLM behind a small TypeScript service built on the Vercel AI SDK. For triage, it sees only the specific propositions System-1 was unsure about. The case workbench can also ask it for supporting quotes on active findings, without changing the machine decision.

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
        BLOCK fired? ── yes ──► BLOCKED  (machine verdict stays fixed)
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
- **Only uncertainty that can change the outcome escalates.** Rules are conjunctions evaluated with three-valued (Kleene) logic, so a single confident FALSE settles a rule however unsure Jev is about its other conditions. A memo that clearly stays internal never reaches review because Jev is torn about whether it mentions travel. Uncertain conditions inside settled rules do not trigger a triage review. Case evidence harvesting is a separate, read-only step.
- **BLOCK short-circuits triage.** If a BLOCK rule fires on Jev alone, the machine verdict is settled without System-2. Open rules are listed in `review_offices` for awareness. Hybrid case creation may then harvest quotes; human dispositions can approve a case while its machine verdict remains `BLOCKED`.
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

Python 3.10+ (CI uses 3.12) and Node 22.18+ (CI uses 24 to run TypeScript directly).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
(cd system2 && npm ci)
cp .env.example .env    # then add TYPESAFE_API_KEY, and OPENROUTER_API_KEY for System-2
```

Jev can also be reached through OpenRouter with the same SDK; `.env.example` shows the settings.

### 2. Run

To try the workbench without API keys or model calls:

```bash
.venv/bin/python scripts/demo_server.py --seed   # open http://127.0.0.1:8000/app/
```

The demo seeds eight cases from the evaluation corpus. Decisions are labelled `demo:corpus-replay` for corpus documents or `demo:keyword-heuristic` for other text, so they are distinguishable from Jev output. These replayed and heuristic answers demonstrate the workflow; they do not measure model quality. Cases are kept in memory by default; use `--db outputs/demo.sqlite3` to retain them.

To run with configured model providers instead:

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

Open <http://localhost:8000/app/> for the reviewer workbench. It supports case creation, explicit submission context, evidence highlighting, revision comparison, assignment, and rule dispositions. The top bar stores the reviewer name locally; the optional API token is kept in session storage.

### Case workflow and persistence

Set `JEVGATE_DB_PATH=data/jevgate.db` to persist cases across restarts. The default is `:memory:`, which lasts only for the app lifespan. SQLite stores document text, context, immutable machine decisions, grounded spans, human dispositions, and audit events. No external database service is needed.

Explicit context raises probability floors: partner-government sharing forces `external_audience`; public release forces both audience conditions; a release request forces `release_requested`. Context never lowers text-derived probabilities. Internal declarations or a denied release request that conflict with System-1 produce visible warnings. A condition settled by context is attributed to `USER_CONTEXT`.

Hybrid case creation can harvest quotes for active, System-1-confirmed Noul conditions. Grounding preserves raw document offsets while normalizing whitespace, case, and quotes for matching; outer periods are excluded from the matched quote. Quote harvesting never changes the machine decision. Fast cases can fetch quotes later with the ground endpoint.

| Case status | Meaning |
|---|---|
| `CLEARED_BY_MACHINE` | The machine cleared the document and no rules are active. |
| `OPEN` | At least one active finding still needs a disposition, or evaluation failed. |
| `BLOCKED_FINAL` | A reviewer accepted an active BLOCK finding. |
| `APPROVED_BY_HUMAN` | Every active finding is overridden, waived, carried forward, or accepted as a completed ROUTE requirement. |
| `CHANGES_REQUESTED` | A reviewer requested a revision while findings remain unresolved. |

Every active rule, including BLOCK rules, accepts `ACCEPT_FINDING`, `FALSE_POSITIVE_OVERRIDE`, or `WAIVE_RISK` with a reviewer, office, and rationale. These records never rewrite the machine verdict. Only `OPEN` and `CHANGES_REQUESTED` can be requested directly through assignment updates; final statuses derive from findings.

A revision carries prior overrides and waivers forward if its document hash is identical, or if every condition's probability increases by at most 0.05 and each previous supporting quote remains present. Otherwise the finding returns to `PENDING` with an invalidation note. Earlier versions and audit events remain available. Concurrent case changes during revision evaluation return HTTP 409 so the caller can retry against the latest state.

| Endpoint | Purpose |
|---|---|
| `POST /v1/cases` | Create a case: `{title, document, context?, mode?}`; returns 201. |
| `GET /v1/cases` | Queue; filter by `status`, `office`, and `verdict`. Sorts by expedite, risk, then latest update. |
| `GET /v1/cases/{id}?version=N` | Current or historical version with its document, decision, and dispositions. |
| `POST /v1/cases/{id}/versions` | Evaluate a revision: `{document, context?, mode?, change_note?}`; omitted context inherits the prior version. |
| `PATCH /v1/cases/{id}/versions/{n}/rules/{rule}` | Record `{disposition, reviewer, office, rationale}`. |
| `POST /v1/cases/{id}/versions/{n}/ground` | Fetch grounded quotes without altering the decision; 503 when unavailable. |
| `PATCH /v1/cases/{id}` | Update assignment or request changes; `reviewer` and `note` identify the action in the audit log. |
| `GET /v1/cases/{id}/audit` | Chronological events, optionally filtered by `version`; `limit` defaults to 100 (max 200), `offset` defaults to 0. |
| `GET /v1/reviewers` | Observed request-supplied names and offices, with `last_seen_at` and `identity_source: REQUEST_SUPPLIED`. |
| `GET /v1/samples` | Fictional JSON sample documents for the workbench. |

Set `JEVGATE_API_TOKEN` to require `Authorization: Bearer <token>` for case, sample, reviewer, and audit endpoints. Reviewer names remain **self-declared**: the shared token grants access but does not authenticate an individual or enforce office roles. The directory lists names recorded in review/assignment history, not user accounts. Audit events include actor, version, timestamp, and action payload; creation events contain the document hash, not a duplicate document. They are read-only through the API, but the database is not a tamper-proof audit system.

For backward compatibility, `/v1/triage`, `/v1/policy`, `/v1/questions`, `/healthz`, and static assets remain outside this optional token check. Deployments needing authentication on every endpoint must enforce it at their gateway. The workbench currently uses its local reviewer field; the new reviewer and audit endpoints are available to API clients.

### 3. Samples and latency

```bash
.venv/bin/python scripts/run_samples.py                                   # one pass over samples/
.venv/bin/python scripts/run_samples.py --runs 20 --mode fast --out outputs/bench.json
```

`samples/` holds six fictional documents. Each is labeled with the verdict it was written to produce: a routine status note, a delegation itinerary sent to a host ministry, procurement minutes releasing a payment, a stolen-laptop incident, a draft press release that names a foreign official, and an ambiguous forwarded email. The runner prints each sample's verdict and route, then the p50, p95, and max latency of decisions made without System-2. It reports three latency figures: Jev's call alone, the server's total, and the client's round trip.

**No live numbers are in this repo yet.** The tests below run offline. Run the command above with a key to measure the System-1 path on your own network before relying on any latency figure.

### 4. Test

```bash
.venv/bin/python -W error -m unittest discover -s tests -t .
(cd system2 && npm test && npm run typecheck)
.venv/bin/python scripts/evaluate.py --offline --out outputs/evaluation.json
node tests/e2e/workbench.e2e.mjs --screenshots outputs/e2e-shots
```

The browser test starts its own no-key Python demo server and drives headless Chrome. It requires Node 22+ and Chrome, with no npm dependencies. Set `PYTHON` to choose a Python executable (for example, `PYTHON=python` in CI) or `CHROME_PATH` if Chrome is not found automatically. Missing Chrome skips the local run and fails the run when `CI` is set.

The Python tests cover:

- the confidence formulas, checked against TypeSafe's published worked examples
- policy validation, with every class of bad reference
- Kleene evaluation, and uncertainty in a settled rule being ignored
- the asymmetric high/standard bands
- BLOCK short-circuiting
- each System-2 rule: grounded yes, ungrounded yes, high-stakes no, unknown and duplicate IDs, missing verdicts
- fail-closed behavior for both systems
- priority and risk ordering
- the HTTP API, SQLite persistence, context floors, human dispositions, revision carry-forward, audit history, and request-supplied reviewer identities
- quote normalization and exact raw offsets, including Unicode and whitespace cases
- the evaluation corpus loader, metrics, and offline replay

The Jev adapter tests drive the real TypeSafe SDK against a mock HTTP transport, so the wire format is tested too. The TypeScript tests use the AI SDK's `MockLanguageModelV4`. All tests and offline evaluation run without provider credentials.

`.github/workflows/ci.yml` runs the Python suite with warnings treated as errors, the System-2 tests and type checker, the offline evaluator, and a separate workbench browser job on pushes and pull requests. It uploads the evaluator's JSON report as an artifact and browser screenshots when the browser job fails.

The 24-document corpus includes clear positives, internal short-circuits, ambiguous boundaries, and adversarial/negated statements. Offline evaluation replays hand-authored model answers through triage: it measures gate behavior, not live model quality, case context floors, or human review. The current replay reports hybrid verdict accuracy of 18/24 versus 14/24 for fast mode, with one high-stakes false negative in each mode and a grounded-but-misleading quote. These deliberate failure examples are not a passing accuracy threshold; CI checks that evaluation executes, while unit tests check its metrics. Use `--live` with configured services for model-backed evaluation; do not interpret offline figures as calibration evidence.

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
  cases.py          case orchestration, context, evidence, dispositions, revision carry-forward
  store.py          SQLite transactions, version persistence, audit history
  workbench_schemas.py  shared case/workbench API contracts
  audit_schemas.py   audit events and request-supplied reviewer identities
  grounding.py      normalized quote matching with raw document offsets
  web/              browser workbench served at /app/
policies/default.json   8 rules over 11 named conditions
system2/            TypeScript reviewer: Vercel AI SDK 7, zod schema, OpenRouter
samples/            6 sample documents and a 24-document labelled evaluation corpus
scripts/run_samples.py  verdict table and latency report against a running server
scripts/evaluate.py     offline replay or live evaluation, with JSON metrics
scripts/demo_server.py  no-key demo with corpus replay and keyword heuristics
tests/              offline Python tests
tests/e2e/workbench.e2e.mjs  browser workflow checks against the demo server
.github/workflows/ci.yml  Python, System-2, offline evaluation, and browser checks
```

All names, countries, and organizations in `samples/` and `tests/` are fictional.
