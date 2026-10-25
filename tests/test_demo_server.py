import unittest
from fastapi.testclient import TestClient
from scripts.demo_server import SEED, build_app, keyword_signals
from src.questions import BATTERY


class DemoServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app, cls.corpus = build_app()
        cls.by_id = {doc.id: doc for doc in cls.corpus.values()}

    def create(self, client, title, document, mode="hybrid"):
        resp = client.post("/v1/cases", json={"title": title, "document": document, "mode": mode})
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()

    def test_corpus_documents_replay_their_labelled_outcome(self):
        with TestClient(self.app) as client:
            for doc_id in SEED:
                doc = self.by_id[doc_id]
                case = self.create(client, doc.title, doc.document)
                decision = case["active_version"]["decision"]
                self.assertEqual(decision["model"], "demo:corpus-replay", doc_id)
                if doc.category in ("clear_positive", "internal_short_circuit"):
                    self.assertEqual(case["machine_verdict"], doc.expected_verdict.value, doc_id)

    def test_other_text_is_labelled_as_a_heuristic_not_jev(self):
        with TestClient(self.app) as client:
            case = self.create(client, "Ad hoc", "To: Ministry of Interior. Motorcade route via the north gate.", mode="fast")
        decision = case["active_version"]["decision"]
        self.assertEqual(decision["model"], "demo:keyword-heuristic")
        self.assertEqual((case["machine_verdict"], decision["offices"]), ("BLOCKED", ["security_desk"]))

    def test_keyword_signals_answer_the_whole_battery(self):
        self.assertEqual(set(keyword_signals("A routine weekly status note.")), set(BATTERY))

    def test_on_demand_grounding_quotes_labelled_evidence(self):
        doc = self.by_id["eval-04-step-payment-minutes"]
        with TestClient(self.app) as client:
            case = self.create(client, doc.title, doc.document, mode="fast")
            self.assertEqual(case["active_version"]["evidence_spans"], [])
            grounded = client.post(f"/v1/cases/{case['case_id']}/versions/1/ground").json()
        (span,) = grounded["active_version"]["evidence_spans"]
        self.assertEqual(span["quote"], doc.expected_evidence_substrings["financial_commitment"])


if __name__ == "__main__":
    unittest.main()
