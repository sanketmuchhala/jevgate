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

The verdict has the same shape. A fired BLOCK settles the document at once. Otherwise any UNCERTAIN rule means the outcome is still open. Only the UNCERTAIN conditions inside UNCERTAIN rules become propositions for review.

Conjunction only was a deliberate choice. A disjunction is written as two rules with the same office, which keeps every rule readable on its own and keeps the "one FALSE settles it" property.

## 5. System-2 is trusted asymmetrically

The reviewer is an LLM, so its verdicts carry no calibrated probability. Python applies three rules to them:

- **A yes must quote the document.** `evidence` has to be a verbatim substring of the document, compared after normalizing case, whitespace, and curly quotes, and at least 8 characters long. Paraphrase and invention are discarded as `UNGROUNDED_YES`.
- **A no cannot clear a high-stakes rule.** System-2 can settle a standard-stakes condition either way. On a high-stakes condition that Jev was unsure about, it can confirm the risk (yes) but not remove it (no). The rule stays open and goes to its office.
- **Only open propositions count.** Verdicts for IDs Python did not send, and second verdicts for the same ID, are discarded and logged.

This also bounds prompt injection. The document is fenced as data and the reviewer is told to ignore instructions inside it. Suppose an injected document still steers the reviewer. A forged "no" on a high-stakes condition has no effect. A "yes" without a real quote is dropped. A real quote only adds caution. The worst case is that an injected "no" settles a standard-stakes condition such as `legal_exposure`, which costs one ROUTE.

## 6. Fail closed, and keep System-2 off the fast path

If Jev errors, times out, or returns an incomplete or mistyped answer set, the document goes to `HUMAN_REVIEW` with `risk_index = 1.0` and `EXPEDITE`. An unread document is treated as the worst case and is never cleared. If the reviewer fails, its open rules go to a human with `SYSTEM_TWO_UNAVAILABLE`.

The latency budget only covers the System-1 path: one Jev request, a pooled client created once per process (TLS handshakes stay off the hot path), a 1.5 s per-attempt timeout, and one retry. Policy evaluation is pure Python over twelve signals. System-2 adds an LLM call, so it runs only when a rule is still open, and `mode: "fast"` skips it entirely. **This repo contains no measured latency yet.** `scripts/run_samples.py` reports p50, p95, and max for Jev's call, the server total, and the client round trip on the System-1 path. Those are the numbers to cite.

## 7. Why the Vercel AI SDK is on System-2 and not System-1

AI SDK 7 has an experimental `decide()` with `choice`, `score`, and `boolean` questions and a gateway route to Jev, so System-1 could live in TypeScript. JevGate keeps System-1 in Python for three reasons:

1. The service, its contracts, and its policy validation are FastAPI and Pydantic.
2. TypeSafe's Python SDK returns Jev's `confidence` and raises typed errors (`TypeSafeAPITimeoutError`, and so on), and JevGate maps those onto the fail-closed path.
3. `decide()` is marked experimental, and its answer type makes probabilities optional.

The AI SDK is used where it is strongest: a provider-agnostic, schema-constrained LLM call (`generateText` with `Output.object` and a zod schema) behind a small HTTP contract that Python re-validates.

## 8. Limits and next steps

- **Calibration on real data.** Collect labeled documents, check reliability per condition, and set the bands from it. This is the first thing to do before trusting any verdict.
- **Grounding is substring-based.** A reviewer can quote real text out of context. A second check, either a Jev Noul ("does this quote support this proposition?") or a stricter span-level rule, would close part of that gap.
- **The risk index weights are arbitrary.** They only order the queue, but they should be fitted to reviewer priorities.
- **No persistence or auth.** Decisions are returned, not stored. A real deployment needs an append-only decision log, caller authentication, and redaction of documents in logs.
- **Context limits.** Jev 1.14 allows 64k tokens per request and 32k per question. Long records need chunking, with rules evaluated per chunk and combined, or the long-context header, which TypeSafe documents as slower and less accurate.
- **Language.** Jev is strongest in English. Documents in other languages should get a narrower auto-clear band until they are measured.
- **Policy reload** requires a restart. That is fine for a file under review control, but a versioned policy store would allow staged rollouts.
