import asyncio
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from scripts import evaluate
from scripts.evaluate import (
    CATEGORIES, CorpusError, DocumentResult, Rate, compute_metrics, evaluate_offline, expand_signals, load_corpus,
)
from src.grounding import find_quote_span
from src.policy import DEFAULT_POLICY_PATH, load_policy
from src.questions import BATTERY
from src.schemas import ChoiceSignal, NoulSignal, ScoreSignal, Verdict


class CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = load_policy(DEFAULT_POLICY_PATH, BATTERY)
        cls.docs = load_corpus(evaluate.DEFAULT_CORPUS, cls.policy)

    def test_corpus_has_24_documents_six_per_category(self):
        self.assertEqual(len(self.docs), 24)
        self.assertEqual(len({d.id for d in self.docs}), 24)
        for category in CATEGORIES:
            self.assertEqual(sum(d.category == category for d in self.docs), 6, category)

    def test_every_document_answers_the_whole_battery(self):
        for doc in self.docs:
            self.assertEqual(set(doc.signals), set(BATTERY), doc.id)

    def test_labelled_evidence_is_verbatim(self):
        for doc in self.docs:
            for cid, quote in doc.expected_evidence_substrings.items():
                self.assertIsNotNone(find_quote_span(quote, doc.document), f"{doc.id} {cid}")

    def test_labels_agree_with_the_policy(self):
        offices = {r.rule_id: r.office for r in self.policy.rules}
        for doc in self.docs:
            # Every expected rule's office is expected; HUMAN_REVIEW is the only verdict that leaves rules open.
            self.assertTrue({offices[r] for r in doc.expected_fired_rules} <= doc.expected_offices, doc.id)
            if doc.expected_verdict is Verdict.CLEARED:
                self.assertEqual((doc.expected_fired_rules, doc.expected_offices), ((), frozenset()), doc.id)

    def test_expand_signals_derives_what_jev_reports(self):
        sigs = self.docs[0].signals
        self.assertIsInstance(sigs["intended_audience"], ChoiceSignal)
        self.assertEqual(sigs["intended_audience"].choice, "partner_government")
        self.assertIsInstance(sigs["sensitivity"], ScoreSignal)
        self.assertAlmostEqual(sigs["sensitivity"].score, 0 * 0.02 + 1 * 0.08 + 2 * 0.3 + 3 * 0.6, places=4)
        self.assertEqual(sigs["movement_details"], NoulSignal(noul=0.94, confidence=0.88))

    def test_expand_signals_reports_every_problem(self):
        raw = {"document_type": {"other": 1.0}, "sensitivity": [0.5, 0.5], "made_up": 0.1, "personal_data": 1.4}
        with self.assertRaises(CorpusError) as ctx:
            expand_signals(raw, BATTERY)
        message = str(ctx.exception)
        for fragment in ("unknown question 'made_up'", "document_type: needs a probability for each",
                         "sensitivity: needs a list of 4", "personal_data: needs P(yes)", "urgency: missing"):
            self.assertIn(fragment, message)

    def test_loader_rejects_inconsistent_labels_by_line(self):
        row = json.loads(evaluate.DEFAULT_CORPUS.read_text(encoding="utf-8").splitlines()[0])
        bad = dict(row, id="bad", expected_high_stakes_risk=False, expected_offices=["nobody"],
                   expected_evidence_substrings={"staff_movements": "a sentence the document never says"})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "corpus.jsonl"
            path.write_text(json.dumps(row) + "\n" + json.dumps(bad) + "\n", encoding="utf-8")
            with self.assertRaises(CorpusError) as ctx:
                load_corpus(path, self.policy)
        message = str(ctx.exception)
        self.assertIn("line 2:", message)
        self.assertNotIn("line 1:", message)
        for fragment in ("expected_high_stakes_risk is False", "offices named in the policy", "not a verbatim passage"):
            self.assertIn(fragment, message)


class MetricTests(unittest.TestCase):
    """Metrics on the offline replay. The corpus is hand-authored, so these pin the gate's behavior."""

    @classmethod
    def setUpClass(cls):
        cls.policy = load_policy(DEFAULT_POLICY_PATH, BATTERY)
        cls.docs = load_corpus(evaluate.DEFAULT_CORPUS, cls.policy)
        cls.results = asyncio.run(evaluate_offline(cls.docs, cls.policy))
        cls.fast = compute_metrics(cls.results, cls.policy, "fast")
        cls.hybrid = compute_metrics(cls.results, cls.policy, "hybrid")

    def result(self, doc_id: str) -> DocumentResult:
        return next(r for r in self.results if r.document.id == doc_id)

    def test_rate_handles_an_empty_denominator(self):
        self.assertIsNone(Rate(0, 0).value)
        self.assertEqual(Rate(1, 4).as_dict(), {"value": 0.25, "numerator": 1, "denominator": 4})

    def test_pinned_offline_metrics(self):
        expected = {
            "fast": {"high_stakes_fnr": (1, 9), "false_escalation_rate": (4, 9), "office_routing_exact_match": (19, 24),
                     "verdict_accuracy": (14, 24)},
            "hybrid": {"high_stakes_fnr": (1, 9), "false_escalation_rate": (2, 9), "system2_resolution_rate": (6, 11),
                       "span_grounding_accuracy": (4, 5), "office_routing_exact_match": (20, 24),
                       "verdict_accuracy": (18, 24)},
        }
        for mode, metrics in (("fast", self.fast), ("hybrid", self.hybrid)):
            for key, (num, den) in expected[mode].items():
                self.assertEqual((metrics[key].numerator, metrics[key].denominator), (num, den), f"{mode} {key}")
        self.assertIsNone(self.fast["system2_resolution_rate"])
        self.assertIsNone(self.fast["span_grounding_accuracy"])

    def test_system_two_cannot_add_high_stakes_misses(self):
        # A reviewer 'no' never settles a high-stakes rule, so hybrid can only match fast here.
        self.assertEqual(self.hybrid["high_stakes_fnr"], self.fast["high_stakes_fnr"])
        missed = [r.document.id for r in self.results if evaluate.high_stakes_misses(r, "hybrid", self.policy)[0]]
        self.assertEqual(missed, ["eval-23-disguised-itinerary-missed"])

    def test_settled_documents_never_reach_system_two(self):
        for r in self.results:
            if r.document.category in ("clear_positive", "internal_short_circuit"):
                self.assertFalse(r.decisions["hybrid"].system_two.invoked, r.document.id)
                self.assertIs(r.decisions["fast"].verdict, r.document.expected_verdict, r.document.id)

    def test_trust_rules_show_up_in_the_corpus(self):
        reasons = {r.document.id: [d.reason for d in r.decisions["hybrid"].system_two.discarded] for r in self.results}
        self.assertEqual(reasons["eval-16-draft-aide-memoire-logistics"], ["HIGH_STAKES_NO"])
        self.assertEqual(reasons["eval-18-arbitration-note-paraphrase"], ["UNGROUNDED_YES"])
        self.assertEqual(reasons["eval-22-itinerary-with-reviewer-instruction"], ["HIGH_STAKES_NO"])
        # The injected 'no' does settle a standard-stakes rule: the documented worst case.
        injected = self.result("eval-21-payment-with-reviewer-instruction").decisions["hybrid"]
        self.assertEqual((injected.verdict, injected.decided_by), (Verdict.CLEARED, "SYSTEM_TWO"))

    def test_out_of_context_quote_is_grounded_but_not_correct(self):
        r = self.result("eval-20-summary-disclaims-personal-data")
        (verdict,) = evaluate.accepted_yes_verdicts(r.decisions["hybrid"])
        self.assertIsNotNone(find_quote_span(verdict.evidence, r.document.document))
        self.assertFalse(evaluate.span_matches_label(verdict, r.document))
        self.assertEqual(r.decisions["hybrid"].verdict, Verdict.ROUTED)


class CliTests(unittest.TestCase):
    def test_offline_run_prints_a_table_and_writes_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "eval.json"
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                self.assertEqual(evaluate.main(["--offline", "--out", str(out)]), 0)
            report = json.loads(out.read_text(encoding="utf-8"))
        table = buffer.getvalue()
        self.assertIn("| Metric | System-1 only (fast) | Hybrid (System-1 + System-2) |", table)
        self.assertIn("| High-stakes false negative rate (lower is better) | 11.1% (1/9) | 11.1% (1/9) |", table)
        self.assertIn("test the gate, not the models", table)
        self.assertEqual(report["mode"], "offline")
        self.assertEqual(len(report["documents"]), 24)
        self.assertEqual(report["metrics"]["hybrid"]["false_escalation_rate"],
                         {"value": 0.2222, "numerator": 2, "denominator": 9})
        self.assertIsNone(report["metrics"]["fast"]["span_grounding_accuracy"])

    def test_offline_is_the_default_and_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / f"run{i}.json" for i in range(2)]
            for path in paths:
                with redirect_stdout(io.StringIO()):
                    evaluate.main(["--out", str(path)])
            self.assertEqual(paths[0].read_text(encoding="utf-8"), paths[1].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
