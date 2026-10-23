import unittest
from fastapi.testclient import TestClient
from src.app import create_app
from src.config import Settings
from tests.fakes import FakeSystemOne, FakeSystemTwo, signals


class ApiTests(unittest.TestCase):
    def client(self, s1=None, s2=None):
        app = create_app(settings=Settings(), system_one=s1 or FakeSystemOne(), system_two=s2)
        return TestClient(app)

    def test_triage_round_trip(self):
        s1 = FakeSystemOne(signals(financial_commitment=0.96))
        with self.client(s1) as client:
            resp = client.post("/v1/triage", json={"document": "Pay the vendor.", "metadata": {"source_unit": "ops"}})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual((body["verdict"], body["offices"]), ("ROUTED", ["finance_approver"]))
        self.assertEqual(body["model"], "jev-1.14.0")
        self.assertEqual(body["signals"]["financial_commitment"], {"type": "noul", "noul": 0.96, "confidence": 0.92})
        self.assertEqual(s1.calls, [{"document": "Pay the vendor.", "metadata": {"source_unit": "ops"}}])

    def test_hybrid_mode_calls_the_reviewer(self):
        s2 = FakeSystemTwo([{"id": "legal_exposure", "verdict": "no"}])
        with self.client(FakeSystemOne(signals(legal_exposure=0.5)), s2) as client:
            body = client.post("/v1/triage", json={"document": "Status update."}).json()
        self.assertEqual((body["verdict"], body["decided_by"]), ("CLEARED", "SYSTEM_TWO"))
        self.assertEqual(len(s2.requests), 1)

    def test_request_validation(self):
        with self.client() as client:
            self.assertEqual(client.post("/v1/triage", json={"document": ""}).status_code, 422)
            self.assertEqual(client.post("/v1/triage", json={"document": "x", "mode": "slow"}).status_code, 422)
            self.assertEqual(client.post("/v1/triage", json={"document": "x", "extra": 1}).status_code, 422)

    def test_introspection_endpoints(self):
        with self.client() as client:
            health = client.get("/healthz").json()
            policy = client.get("/v1/policy").json()
            questions = client.get("/v1/questions").json()
        self.assertEqual(health, {"status": "ok", "jev_model": "jev-1.14.0",
                                  "policy_version": policy["version"], "system_two": False})
        self.assertEqual(len(policy["rules"]), 8)
        self.assertEqual(len(questions), 12)


if __name__ == "__main__":
    unittest.main()
