import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from src.app import create_app
from src.config import Settings
from src.store import CaseStore
from src.workbench_schemas import CaseAssignmentUpdate
from tests.fakes import FakeSystemOne, FakeSystemTwo, choice, score, signals

QUOTE = "The delegation travels by motorcade to Hotel Arden."
DOCUMENT = QUOTE + "\nAn unrelated administrative paragraph."


def condition(body, rule_id, cid):
    rule = next(r for r in body["active_version"]["decision"]["rules"] if r["rule_id"] == rule_id)
    return next(c for c in rule["conditions"] if c["condition_id"] == cid)


class CaseTests(unittest.TestCase):
    def setUp(self):
        self.s1 = FakeSystemOne(signals(movement_details=0.9))
        self.s2 = FakeSystemTwo([{"id": "staff_movements", "verdict": "yes", "evidence": QUOTE,
                                  "rationale": "Specific delegation movements."}])
        self.store = CaseStore()
        self.addCleanup(self.store.close)
        self.app = create_app(settings=Settings(api_token=None), system_one=self.s1, system_two=self.s2,
                              case_store=self.store)
        self.client = self.enterContext(TestClient(self.app))

    def create(self, **changes):
        body = {"title": "Delegation review", "document": DOCUMENT,
                "context": {"intended_audience": "partner_government"}, "mode": "hybrid"}
        body.update(changes)
        response = self.client.post("/v1/cases", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def dispose(self, case, disposition="FALSE_POSITIVE_OVERRIDE", rule_id="P1", version=None):
        number = version or case["current_version"]
        response = self.client.patch(f'/v1/cases/{case["case_id"]}/versions/{number}/rules/{rule_id}',
                                    json={"disposition": disposition, "reviewer": "A. Reviewer",
                                          "office": "security_desk", "rationale": "Reviewed and documented finding."})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def revise(self, case, document=DOCUMENT, **kwargs):
        response = self.client.post(f'/v1/cases/{case["case_id"]}/versions',
                                    json={"document": document, **kwargs})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_explicit_external_audience_forces_block(self):
        case = self.create()
        c = condition(case, "P1", "external_audience")
        self.assertEqual((c["truth"], c["resolved_by"], c["probability"]), ("TRUE", "USER_CONTEXT", 1.0))
        self.assertEqual((case["machine_verdict"], case["status"]), ("BLOCKED", "OPEN"))
        self.assertEqual(self.s1.calls[0]["metadata"]["intended_audience"], "partner_government")

    def test_internal_declaration_does_not_suppress_public_text(self):
        self.s1.sigs = signals(movement_details=0.9, intended_audience=choice("public_release", 0.85))
        case = self.create(context={"intended_audience": "internal_team"})
        self.assertEqual(case["machine_verdict"], "BLOCKED")
        self.assertEqual(condition(case, "P1", "external_audience")["resolved_by"], "SYSTEM_ONE")
        warnings = case["active_version"]["context_warnings"]
        self.assertTrue(any("internal_team" in w and "external_audience" in w for w in warnings))
        self.assertTrue(any("public_audience" in w and "0.85" in w for w in warnings))

    def test_public_and_release_context_floors_and_mismatch(self):
        self.s1.sigs = signals(foreign_official_named=0.9,
                               sensitivity=score({0: 0.0, 1: 0.0, 2: 0.1, 3: 0.9}))
        case = self.create(context={"intended_audience": "public_release", "release_requested": True})
        for rid, cid in (("P8", "public_audience"), ("P2", "release_requested")):
            self.assertEqual(condition(case, rid, cid)["resolved_by"], "USER_CONTEXT")
        self.s1.sigs = signals(release_requested=0.8)
        case = self.create(context={"release_requested": False})
        self.assertTrue(any("release_requested=False" in w for w in case["active_version"]["context_warnings"]))
        self.assertEqual(condition(case, "P2", "release_requested")["probability"], 0.8)

    def test_hybrid_harvests_grounded_quotes(self):
        case = self.create()
        span, = case["active_version"]["evidence_spans"]
        self.assertEqual(span["quote"], QUOTE)
        self.assertEqual(DOCUMENT[span["start_char"]:span["end_char"]], span["quote"])
        self.assertEqual([p.id for p in self.s2.requests[0].propositions], ["staff_movements"])
        self.assertFalse(case["active_version"]["decision"]["system_two"]["invoked"])

    def test_harvesting_no_cannot_downgrade_system_one(self):
        self.s2.verdicts = [{"id": "staff_movements", "verdict": "no"}]
        case = self.create()
        self.assertEqual(case["machine_verdict"], "BLOCKED")
        self.assertEqual(condition(case, "P1", "staff_movements")["truth"], "TRUE")
        self.assertEqual(case["active_version"]["evidence_spans"], [])
        self.assertEqual(len(self.s2.requests), 1)

    def test_existing_triage_quotes_are_used_without_resending(self):
        self.s1.sigs = signals(movement_details=0.5)
        case = self.create()
        self.assertEqual(case["machine_verdict"], "BLOCKED")
        self.assertEqual(condition(case, "P1", "staff_movements")["resolved_by"], "SYSTEM_TWO")
        self.assertEqual(len(case["active_version"]["evidence_spans"]), 1)
        self.assertEqual(len(self.s2.requests), 1)

    def test_ungrounded_and_unknown_quotes_are_not_stored(self):
        self.s2.verdicts = [{"id": "staff_movements", "verdict": "yes", "evidence": "Invented itinerary absent from document"},
                            {"id": "unknown", "verdict": "yes", "evidence": QUOTE}]
        self.assertEqual(self.create()["active_version"]["evidence_spans"], [])

    def test_override_block_preserves_machine_decision(self):
        case = self.create()
        changed = self.dispose(case)
        self.assertEqual(changed["status"], "APPROVED_BY_HUMAN")
        self.assertEqual(changed["machine_verdict"], "BLOCKED")
        self.assertEqual(changed["active_version"]["decision"], case["active_version"]["decision"])

    def test_accept_block_is_final_and_waiver_can_clear_it(self):
        case = self.create()
        self.assertEqual(self.dispose(case, "ACCEPT_FINDING")["status"], "BLOCKED_FINAL")
        self.assertEqual(self.dispose(case, "WAIVE_RISK")["status"], "APPROVED_BY_HUMAN")

    def test_accept_route_clears_routing_requirement(self):
        self.s1.sigs = signals(financial_commitment=0.9)
        case = self.create()
        self.assertEqual(self.dispose(case, "ACCEPT_FINDING", "P6")["status"], "APPROVED_BY_HUMAN")

    def test_all_active_rules_must_be_addressed_including_uncertainty(self):
        self.s1.sigs = signals(movement_details=0.9, legal_exposure=0.5)
        case = self.create(mode="fast")
        self.assertEqual(self.dispose(case)["status"], "OPEN")
        self.assertEqual(self.dispose(case, "ACCEPT_FINDING", "P7")["status"], "APPROVED_BY_HUMAN")

    def test_removing_offending_paragraph_clears_revision(self):
        case = self.create()
        self.s1.sigs = signals()
        revised = self.revise(case, "Routine administrative update.")
        self.assertEqual((revised["current_version"], revised["status"]), (2, "CLEARED_BY_MACHINE"))
        self.assertEqual(revised["active_version"]["rule_dispositions"], {})
        original = self.client.get(f'/v1/cases/{case["case_id"]}?version=1').json()
        self.assertEqual(original["active_version"]["decision"], case["active_version"]["decision"])
        self.assertEqual(len(revised["version_history"]), 2)

    def test_stable_override_carries_across_multiple_revisions(self):
        case = self.dispose(self.create())
        revised = self.revise(case, QUOTE + "\nUpdated administrative paragraph.")
        record = revised["active_version"]["rule_dispositions"]["P1"]
        self.assertEqual((record["disposition"], record["carried_from_version"]), ("CARRIED_FORWARD", 1))
        self.assertEqual(record["reviewer"], "A. Reviewer")
        self.assertEqual(revised["status"], "APPROVED_BY_HUMAN")
        third = self.revise(revised, QUOTE + "\nAnother update.")
        self.assertEqual(third["active_version"]["rule_dispositions"]["P1"]["carried_from_version"], 2)

    def test_probability_increase_invalidates_override(self):
        case = self.dispose(self.create())
        self.s1.sigs = signals(movement_details=0.96)
        revised = self.revise(case, DOCUMENT + " More context.")
        record = revised["active_version"]["rule_dispositions"]["P1"]
        self.assertEqual(record["disposition"], "PENDING")
        self.assertIn("Prior FALSE_POSITIVE_OVERRIDE from v1 invalidated", record["invalidation_note"])
        self.assertEqual(revised["status"], "OPEN")

    def test_probability_increase_at_five_percent_boundary_carries(self):
        case = self.dispose(self.create())
        self.s1.sigs = signals(movement_details=0.95)
        revised = self.revise(case, DOCUMENT + " Administrative correction.")
        self.assertEqual(revised["active_version"]["rule_dispositions"]["P1"]["disposition"], "CARRIED_FORWARD")

    def test_changed_quote_invalidates_override(self):
        case = self.dispose(self.create())
        revised = self.revise(case, DOCUMENT.replace("Hotel Arden", "Hotel Birch"))
        record = revised["active_version"]["rule_dispositions"]["P1"]
        self.assertEqual(record["disposition"], "PENDING")
        self.assertIsNotNone(record["invalidation_note"])

    def test_identical_document_preserves_override_even_if_score_changes(self):
        case = self.dispose(self.create())
        self.s1.sigs = signals(movement_details=0.99)
        revised = self.revise(case)
        self.assertEqual(revised["active_version"]["rule_dispositions"]["P1"]["disposition"], "CARRIED_FORWARD")

    def test_fast_case_can_be_grounded_on_demand_without_changing_decision(self):
        case = self.create(mode="fast")
        self.assertEqual(self.s2.requests, [])
        endpoint = f'/v1/cases/{case["case_id"]}/versions/1/ground'
        response = self.client.post(endpoint)
        self.assertEqual(response.status_code, 200)
        grounded = response.json()
        self.assertEqual(grounded["active_version"]["decision"], case["active_version"]["decision"])
        self.assertEqual(grounded["active_version"]["evidence_spans"][0]["quote"], QUOTE)
        self.client.post(endpoint)
        self.assertEqual(len(self.s2.requests), 1)

    def test_provider_failure_preserves_case_and_ground_returns_503(self):
        self.s2.error = "Provider unavailable"
        case = self.create()
        self.assertEqual(case["machine_verdict"], "BLOCKED")
        self.assertIn("grounding unavailable", case["active_version"]["context_warnings"][0])
        response = self.client.post(f'/v1/cases/{case["case_id"]}/versions/1/ground')
        self.assertEqual(response.status_code, 503)

    def test_system_one_failure_remains_open(self):
        self.s1.error = "Unavailable"
        case = self.create()
        self.assertEqual((case["machine_verdict"], case["status"]), ("HUMAN_REVIEW", "OPEN"))

    def test_assignment_changes_requested_and_status_cannot_bypass_findings(self):
        case = self.create()
        url = f'/v1/cases/{case["case_id"]}'
        response = self.client.patch(url, json={"assigned_office": "review_team", "assigned_reviewer": "Ada",
                                                "status": "CHANGES_REQUESTED"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "CHANGES_REQUESTED")
        self.assertEqual(response.json()["assigned_reviewer"], "Ada")
        self.assertEqual(self.client.patch(url, json={"status": "APPROVED_BY_HUMAN"}).status_code, 422)
        self.assertEqual(self.client.patch(url, json={"assigned_reviewer": None}).json()["assigned_reviewer"], None)
        self.assertEqual(self.dispose(case)["status"], "APPROVED_BY_HUMAN")

    def test_queue_sorting_filters_and_samples(self):
        blocked = self.create(mode="fast")
        self.s1.sigs = signals(urgency=score({0: 0.0, 1: 0.0, 2: 1.0, 3: 0.0}))
        urgent = self.create(mode="fast")
        self.s1.sigs = signals()
        clean = self.create(mode="fast")
        queue = self.client.get("/v1/cases").json()
        self.assertEqual([c["case_id"] for c in queue], [urgent["case_id"], blocked["case_id"], clean["case_id"]])
        filtered = self.client.get("/v1/cases?status=OPEN&office=security_desk&verdict=BLOCKED").json()
        self.assertEqual([c["case_id"] for c in filtered], [blocked["case_id"]])
        self.assertNotIn("active_version", queue[0])
        self.assertTrue(all("document" in sample for sample in self.client.get("/v1/samples").json()))

    def test_missing_resources_and_invalid_updates(self):
        self.assertEqual(self.client.get("/v1/cases/missing").status_code, 404)
        case = self.create()
        base = f'/v1/cases/{case["case_id"]}'
        self.assertEqual(self.client.get(base + "?version=99").status_code, 404)
        body = {"disposition": "WAIVE_RISK", "reviewer": "Ada", "office": "security", "rationale": "Reviewed."}
        self.assertEqual(self.client.patch(base + "/versions/1/rules/P8", json=body).status_code, 404)
        for field in ("reviewer", "office", "rationale"):
            self.assertEqual(self.client.patch(base + "/versions/1/rules/P1", json={**body, field: "   "}).status_code, 422)
        self.assertEqual(self.client.post("/v1/cases", json={"title": "", "document": "x"}).status_code, 422)

    def test_old_version_disposition_does_not_change_current_status(self):
        case = self.create()
        self.s1.sigs = signals()
        revised = self.revise(case, "Routine internal update.")
        self.dispose(revised, "ACCEPT_FINDING", version=1)
        current = self.client.get(f'/v1/cases/{case["case_id"]}').json()
        self.assertEqual(current["status"], "CLEARED_BY_MACHINE")

    def test_revision_does_not_overwrite_case_changes_made_during_evaluation(self):
        case = self.create()
        evaluate = self.s1.evaluate

        async def evaluate_with_concurrent_assignment(state):
            result = await evaluate(state)
            self.app.state.cases.update_assignment(case["case_id"], CaseAssignmentUpdate(assigned_reviewer="Ada"))
            return result

        self.s1.evaluate = evaluate_with_concurrent_assignment
        response = self.client.post(f'/v1/cases/{case["case_id"]}/versions', json={"document": DOCUMENT})
        self.assertEqual(response.status_code, 409)
        current = self.client.get(f'/v1/cases/{case["case_id"]}').json()
        self.assertEqual(current["current_version"], 1)
        self.assertEqual(current["assigned_reviewer"], "Ada")
        self.assertEqual(len(current["version_history"]), 1)

    def test_transaction_failure_rolls_back_case_and_audit_changes(self):
        case = self.create()
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with self.store.transaction() as db:
                db.execute("UPDATE cases SET title = ? WHERE case_id = ?", ("Changed", case["case_id"]))
                self.store.audit(case["case_id"], 1, "TEST_EVENT", "tester", {})
                raise RuntimeError("rollback")
        self.assertEqual(self.store.get_case(case["case_id"]).title, case["title"])
        self.assertEqual(self.store.connection.execute(
            "SELECT count(*) FROM audit_events WHERE event_type = 'TEST_EVENT'",
        ).fetchone()[0], 0)

    def test_default_memory_store_persists_across_requests(self):
        app = create_app(settings=Settings(api_token=None, db_path=Path(":memory:")), system_one=self.s1)
        with TestClient(app) as client:
            case = client.post("/v1/cases", json={"title": "Memory", "document": DOCUMENT, "mode": "fast"}).json()
            self.assertEqual(client.get(f'/v1/cases/{case["case_id"]}').json(), case)

    def test_optional_token_protects_workbench_without_changing_legacy_contracts(self):
        app = create_app(settings=Settings(api_token="test-secret"), system_one=self.s1, case_store=self.store)
        with TestClient(app) as client:
            self.assertEqual(client.get("/v1/cases").status_code, 401)
            self.assertEqual(client.get("/v1/cases", headers={"Authorization": "Bearer test-secret"}).status_code, 200)
            self.assertEqual(client.get("/healthz").status_code, 200)
            self.assertEqual(client.post("/v1/triage", json={"document": "Routine record"}).status_code, 200)

    def test_sqlite_roundtrip_and_audit_survive_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "cases.db"
            with CaseStore(path) as store:
                app = create_app(settings=Settings(api_token=None), system_one=self.s1, system_two=self.s2, case_store=store)
                with TestClient(app) as client:
                    case = client.post("/v1/cases", json={"title": "Saved", "document": DOCUMENT}).json()
            with CaseStore(path) as reopened:
                restored = reopened.get_case(case["case_id"])
                self.assertEqual(restored.active_version.document, DOCUMENT)
                self.assertEqual(restored.active_version.decision.model_dump(mode="json"), case["active_version"]["decision"])
                event = reopened.connection.execute("SELECT * FROM audit_events").fetchone()
                self.assertEqual(event["event_type"], "CASE_CREATED")
                self.assertNotIn("document", json.loads(event["payload_json"]))


if __name__ == "__main__":
    unittest.main()
