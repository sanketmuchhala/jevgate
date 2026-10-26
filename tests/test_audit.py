import unittest

from fastapi.testclient import TestClient

from src.app import create_app
from src.config import Settings
from src.store import CaseStore
from tests.fakes import FakeSystemOne, signals


class AuditApiTests(unittest.TestCase):
    def setUp(self):
        self.store = CaseStore()
        self.addCleanup(self.store.close)
        self.app = create_app(settings=Settings(api_token=None), case_store=self.store,
                              system_one=FakeSystemOne(signals(financial_commitment=0.9)))
        self.client = self.enterContext(TestClient(self.app))
        self.case = self.client.post("/v1/cases", json={
            "title": "Payment review", "document": "Approve the vendor payment.", "mode": "fast",
        }).json()
        self.url = f'/v1/cases/{self.case["case_id"]}'

    def disposition(self, reviewer="Ada", disposition="ACCEPT_FINDING"):
        response = self.client.patch(self.url + "/versions/1/rules/P6", json={
            "disposition": disposition, "reviewer": reviewer, "office": "finance_approver",
            "rationale": "Checked the approval record.",
        })
        self.assertEqual(response.status_code, 200, response.text)

    def test_audit_records_actor_and_preserves_each_disposition(self):
        self.disposition()
        self.disposition("Bo", "WAIVE_RISK")
        response = self.client.get(self.url + "/audit")
        self.assertEqual(response.status_code, 200)
        events = response.json()
        self.assertEqual([e["event_type"] for e in events],
                         ["CASE_CREATED", "RULE_DISPOSITION_UPDATED", "RULE_DISPOSITION_UPDATED"])
        self.assertEqual([e["actor"] for e in events], ["submitter", "Ada", "Bo"])
        self.assertEqual(events[1]["payload"]["disposition"], "ACCEPT_FINDING")
        self.assertEqual(events[2]["payload"]["disposition"], "WAIVE_RISK")
        self.assertEqual(len({e["event_id"] for e in events}), 3)
        self.assertTrue(all(e["case_id"] == self.case["case_id"] and e["version_number"] == 1 for e in events))
        self.assertEqual(self.client.get(self.url).json()["active_version"]["decision"],
                         self.case["active_version"]["decision"])
        self.assertNotIn("document", events[0]["payload"])
        self.assertNotIn("evidence", events[1]["payload"])
        self.assertEqual(events[0]["payload"]["document_sha256"], self.case["active_version"]["document_sha256"])

    def test_audit_filters_version_and_pages_in_stable_order(self):
        self.disposition()
        self.client.post(self.url + "/versions", json={"document": "Approve payment after review.", "mode": "fast"})
        events = self.client.get(self.url + "/audit").json()
        pages = [self.client.get(self.url + f"/audit?limit=1&offset={i}").json()[0] for i in range(len(events))]
        self.assertEqual(pages, events)
        self.assertEqual(self.client.get(self.url + "/audit?version=2").json(), [events[-1]])
        self.assertEqual(self.client.get(self.url + "/audit?offset=100").json(), [])
        for query in ("limit=0", "limit=201", "offset=-1", "version=0"):
            self.assertEqual(self.client.get(self.url + "/audit?" + query).status_code, 422)

    def test_missing_case_or_version_returns_404(self):
        self.assertEqual(self.client.get("/v1/cases/missing/audit").status_code, 404)
        self.assertEqual(self.client.get(self.url + "/audit?version=99").status_code, 404)

    def test_reviewer_directory_retains_request_names_without_claiming_authentication(self):
        self.assertEqual(self.client.get("/v1/reviewers").json(), [])
        self.disposition("Bo")
        self.disposition("Ada")
        self.disposition("Ada", "WAIVE_RISK")
        identities = self.client.get("/v1/reviewers").json()
        self.assertEqual([r["reviewer"] for r in identities], ["Ada", "Bo"])
        self.assertTrue(all(r["office"] == "finance_approver" for r in identities))
        self.assertTrue(all(r["identity_source"] == "REQUEST_SUPPLIED" for r in identities))
        events = self.client.get(self.url + "/audit").json()
        self.assertEqual(identities[0]["last_seen_at"], events[-1]["created_at"])

    def test_assignment_actor_is_not_confused_with_assignee(self):
        self.client.patch(self.url, json={"reviewer": "Coordinator", "assigned_reviewer": "Assignee",
                                          "assigned_office": "security_desk", "note": "Please review."})
        event = self.client.get(self.url + "/audit").json()[-1]
        self.assertEqual(event["actor"], "Coordinator")
        self.assertEqual(event["payload"]["assigned_reviewer"], "Assignee")
        identities = self.client.get("/v1/reviewers").json()
        self.assertEqual([r["reviewer"] for r in identities], ["Coordinator"])
        self.assertIsNone(identities[0]["office"])

    def test_audit_is_read_only_and_rejected_updates_add_no_event(self):
        self.assertEqual(self.client.delete(self.url + "/audit").status_code, 405)
        response = self.client.patch(self.url + "/versions/1/rules/P6", json={
            "disposition": "WAIVE_RISK", "reviewer": " ", "office": "finance", "rationale": "Reviewed.",
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(len(self.client.get(self.url + "/audit").json()), 1)
        self.assertEqual(self.client.get("/v1/reviewers").json(), [])

    def test_audit_does_not_include_other_cases(self):
        self.client.post("/v1/cases", json={"title": "Other", "document": "Other document", "mode": "fast"})
        self.assertEqual(len(self.client.get(self.url + "/audit").json()), 1)

    def test_audit_and_identity_use_existing_token_protection(self):
        app = create_app(settings=Settings(api_token="audit-test"), case_store=self.store,
                         system_one=FakeSystemOne())
        with TestClient(app) as client:
            for path in (self.url + "/audit", "/v1/reviewers"):
                self.assertEqual(client.get(path).status_code, 401)
                self.assertEqual(client.get(path, headers={"Authorization": "Bearer wrong"}).status_code, 401)
                self.assertEqual(client.get(path, headers={"Authorization": "Bearer audit-test"}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
