# Design decisions

## 1. Jev answers questions; Python makes the decision

Jev 1.14 returns typed answers: a Choice with a probability per option, a Score with a probability per level, and a Noul with the probability that a statement is true. It never returns prose, so nothing has to be parsed out of generated text. JevGate asks it twelve narrow questions per document, each a judgment a careful reader makes in a second ("does this disclose travel arrangements for named staff?"). JevGate never asks the model to decide "what should happen to this document". Routing is a policy question with an institutional owner, so it lives in `policies/default.json` and in deterministic Python. Governance staff can read and change that file without touching a prompt.

All twelve questions go in **one** request. Jev evaluates them in parallel and independently, so the extra questions cost input tokens and almost no latency. Some questions only matter for some documents; `document_type` currently feeds no rule at all. Asking them speculatively avoids a second round trip, which is the fan-out pattern TypeSafe recommends.

## 2. Conditions are read from the distribution, not from a point estimate

A Score's `score` is an expected value. An expected sensitivity of 2.0 can mean "certainly Confidential" or "half Internal, half Restricted", and those two readings call for different handling. JevGate therefore reads `sensitivity ≥ 3` as `P(level ≥ 3)`, summed from the level probabilities. It reads `audience ∈ {partner_government, public_release}` as the sum of those two options' probabilities. Every condition reduces to one probability, which keeps the band logic uniform across Choice, Score, and Noul.

Jev's own `confidence` is kept on every signal for audit and is surfaced in the API. The gate does not use it directly, because a peaked distribution over the *wrong* options (for this condition) and a flat one can carry the same top-option confidence. The condition probability answers the question the rule actually asks.

## 3. Bands, and why they are asymmetric

A condition is TRUE at `p ≥ yes`, FALSE at `p ≤ no`, and UNCERTAIN in between. TypeSafe suggests 0.7 / 0.3 as a starting point for Noul. JevGate uses that for standard-stakes rules. High-stakes rules keep `yes` at 0.7 but tighten `no` to 0.1.

The asymmetry follows the cost of each error. Wrongly blocking a document costs a reviewer a few minutes. Wrongly clearing a delegation's motorcade route to an outside party is the failure the system exists to prevent. Calling a high-stakes condition FALSE needs strong evidence; calling it TRUE does not, because a TRUE only sends the document to the office that owns the risk.

**These numbers are not tuned.** They are reasoned defaults. Tuning them needs a labeled set of real documents and a per-condition check that Jev's probabilities are calibrated on this domain, such as a reliability curve or expected calibration error. TypeSafe trains for calibration, but calibration is a property measured across groups of predictions on a given distribution of inputs, not a guarantee for one document. The model is pinned to `jev-1.14.0` so that tuned bands do not drift when the `jev-latest` alias moves.

## 4. Three-valued rules: only uncertainty that matters escalates

Each rule is a conjunction of named conditions, evaluated with Kleene logic. One FALSE makes the rule FALSE, all TRUE makes it TRUE, and anything else is UNCERTAIN. This is what keeps the human queue small. P1 ("staff movements" AND "external audience") is FALSE for a memo that clearly stays internal, even if Jev sits at 0.5 on whether the memo mentions travel. That 0.5 cannot change the outcome, so it is neither escalated nor sent to System-2.

The verdict has the same shape. A fired BLOCK settles the document at once. Otherwise any UNCERTAIN rule means the outcome is still open. Only the UNCERTAIN conditions inside UNCERTAIN rules become propositions for triage review. Case evidence harvesting is separate and cannot change a decision.

Conjunction only was a deliberate choice. A disjunction is written as two rules with the same office, which keeps every rule readable on its own and keeps the "one FALSE settles it" property.

## 5. System-2 is trusted asymmetrically

The reviewer is an LLM, so its verdicts carry no calibrated probability. Python applies three rules to them:

- **A yes must quote the document.** `evidence` has to be a verbatim substring of the document, compared after normalizing case, whitespace, and curly quotes, and at least 8 characters long. Paraphrase and invention are discarded as `UNGROUNDED_YES`.
- **A no cannot clear a high-stakes rule.** System-2 can settle a standard-stakes condition either way. On a high-stakes condition that Jev was unsure about, it can confirm the risk (yes) but not remove it (no). The rule stays open and goes to its office.
- **Only open propositions count.** Verdicts for IDs Python did not send, and second verdicts for the same ID, are discarded and logged.

This also bounds prompt injection. The document is fenced as data and the reviewer is told to ignore instructions inside it. Suppose an injected document still steers the reviewer. A forged "no" on a high-stakes condition has no effect. A "yes" without a real quote is dropped. A real quote only adds caution. The worst case is that an injected "no" settles a standard-stakes condition such as `legal_exposure`, which costs one ROUTE.

## 6. Fail closed, and keep System-2 off the fast path

If Jev errors, times out, or returns an incomplete or mistyped answer set, the document goes to `HUMAN_REVIEW` with `risk_index = 1.0` and `EXPEDITE`. An unread document is treated as the worst case and is never cleared. If the reviewer fails, its open rules go to a human with `SYSTEM_TWO_UNAVAILABLE`.

The latency budget only covers the System-1 path: one Jev request, a pooled client created once per process (TLS handshakes stay off the hot path), a 1.5 s per-attempt timeout, and one retry. Policy evaluation is pure Python over twelve signals. System-2 adds an LLM call, so triage calls it only when a rule is still open, and `mode: "fast"` skips it entirely. Hybrid case creation can make an additional quote-harvesting call after the decision; the case creation latency therefore includes that work. **This repo contains no measured latency yet.** `scripts/run_samples.py` reports p50, p95, and max for Jev's call, the server total, and the client round trip on the System-1 path. Those are the numbers to cite.

## 7. Why the Vercel AI SDK is on System-2 and not System-1

AI SDK 7 has an experimental `decide()` with `choice`, `score`, and `boolean` questions and a gateway route to Jev, so System-1 could live in TypeScript. JevGate keeps System-1 in Python for three reasons:

1. The service, its contracts, and its policy validation are FastAPI and Pydantic.
2. TypeSafe's Python SDK returns Jev's `confidence` and raises typed errors (`TypeSafeAPITimeoutError`, and so on), and JevGate maps those onto the fail-closed path.
3. `decide()` is marked experimental, and its answer type makes probabilities optional.

The AI SDK is used where it is strongest: a provider-agnostic, schema-constrained LLM call (`generateText` with `Output.object` and a zod schema) behind a small HTTP contract that Python re-validates.

## 8. Explicit context raises floors and records disagreement

Submission context is passed as model metadata and independently applied as deterministic condition probability floors. Partner-government sharing forces external audience to 1.0; public release also forces public audience; an explicit release request forces that condition. Evaluation takes the maximum of the text-derived probability and the floor on both passes, including after System-2. `USER_CONTEXT` identifies conditions that context settles when the text alone had not established TRUE.

An internal declaration never suppresses text evidence of external sharing. If System-1's external/public probability exceeds the high-stakes no threshold, the case records a mismatch warning; the same applies when release is explicitly denied. The raw signals remain available alongside effective condition probabilities. This avoids allowing a submitter's checkbox to silently clear risk.

## 9. Machine findings and human decisions have separate histories

A version's `TriageDecision` is immutable after creation. Human review writes separate rule dispositions and derives a case status. Reviewers can accept, override, or waive any active finding, including BLOCK rules, with a name, office, and rationale. Accepting a BLOCK finding finalizes the block; overriding or waiving every finding approves the case while preserving its original machine verdict. Accepting a ROUTE finding completes that office's routing requirement.

Prior overrides and waivers can carry forward to a revision when the document hash is identical. For edited documents, every rule condition must increase by no more than 0.05 and every previous supporting quote for the rule must still appear exactly. This heuristic preserves routine approvals but is not semantic equivalence: edits outside a quote and score-stable changes can still matter. Invalidated dispositions return to pending with a reason. Carried records retain reviewer/rationale and the source version; their approval counts toward the new case status. A changed case during asynchronous evaluation produces a conflict instead of overwriting newer review work.

## 10. Grounding supports review without altering machine findings

The case layer reuses grounded yes answers from triage and can ask System-2 for quotes supporting active, System-1-confirmed Noul conditions not already reviewed. The result only populates evidence spans. A no, missing quote, or unavailable reviewer cannot downgrade a settled condition. Fast cases can request evidence later through the ground endpoint, including for unresolved Noul conditions.

The router and case layer share the same locator: case, whitespace, and curly quotes are normalized; outer quote punctuation and periods are stripped; spans map back to the original document. Matches that split a case-fold-expanded character are rejected. This establishes that a quote appears in the document, not that it supports the proposition. The corpus deliberately includes a negated statement with a real but misleading quote.

## 11. SQLite, declared reviewer identity, and audit access

SQLite uses a persistent connection per store, including for `:memory:`, and serialized transactions for version, disposition, case-head, and audit writes. File-backed deployments set `JEVGATE_DB_PATH`; in-memory mode is intended for tests and temporary demonstrations. The API does not log raw document or evidence text. Operators still need to protect the database and backups because stored cases contain those texts.

Reviewer identity is explicitly request-supplied, as is the acting office. `GET /v1/reviewers` derives a directory from recorded review and assignment events and labels entries `REQUEST_SUPPLIED`; it does not imply authentication or authority. `GET /v1/cases/{id}/audit` returns a chronological, paginated event history with optional version filtering. API callers cannot modify or delete audit events. Administrative database access can, so this is an application audit history rather than tamper-evident storage.

An optional shared bearer token protects the workbench data endpoints. It does not establish individual identity or office permissions. Legacy triage/introspection endpoints and static assets retain their existing access behavior. Strong deployment authentication and authorization belong at the gateway until a per-user identity system is implemented.

## 12. Offline evaluation is reproducible, not a claim of model accuracy

The evaluator compares fast and hybrid triage over 24 labelled fictional documents with replayed model signals/verdicts, or optionally live providers. CI runs both language suites, the TypeScript checker, and offline evaluation, and retains the JSON report. The offline report exposes known false negatives, prompt-injection failures, and misleading quotes; a successful run means the pipeline executed, not that every label was matched. Context-floor behavior and human workflow are tested separately through the case API.

## 13. Limits and next steps

- **Calibration on real data.** Collect labeled documents, check reliability per condition, and set the bands from it. This is the first thing to do before trusting any verdict.
- **Grounding is substring-based.** A reviewer can quote real text out of context. A second check, either a Jev Noul ("does this quote support this proposition?") or a stricter span-level rule, would close part of that gap.
- **The risk index weights are arbitrary.** They only order the queue, but they should be fitted to reviewer priorities.
- **Deployment governance.** SQLite persistence and an optional shared token are implemented. Per-user authentication, office-level authorization, retention/backup policy, and tamper-evident audit export remain deployment work.
- **Context limits.** Jev 1.14 allows 64k tokens per request and 32k per question. Long records need chunking, with rules evaluated per chunk and combined, or the long-context header, which TypeSafe documents as slower and less accurate.
- **Language.** Jev is strongest in English. Documents in other languages should get a narrower auto-clear band until they are measured.
- **Policy reload** requires a restart. That is fine for a file under review control, but a versioned policy store would allow staged rollouts.
