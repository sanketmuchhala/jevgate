import unittest
from src.policy import DEFAULT_POLICY_PATH, load_policy
from src.questions import BATTERY
from src.router import is_grounded, triage
from src.schemas import TriageRequest, Truth, Verdict
from tests.fakes import FakeSystemOne, FakeSystemTwo, external, score, signals

DOC = (
    "Minutes, procurement working group. The group agreed to sign the cold-chain contract with "
    "Norvale Logistics and to release the first payment of EUR 48,000 on Friday."
)


def condition(decision, rule_id, condition_id):
    rule = next(r for r in decision.rules if r.rule_id == rule_id)
    return next(c for c in rule.conditions if c.condition_id == condition_id)


class RouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.policy = load_policy(DEFAULT_POLICY_PATH, BATTERY)

    async def run_triage(self, sigs=None, s2=None, mode="hybrid", s1=None, document=DOC):
        s1 = s1 or FakeSystemOne(sigs)
        return await triage(
            TriageRequest(document=document, mode=mode),
            policy=self.policy, battery=BATTERY, system_one=s1, system_two=s2,
        )

    # --- System-1 alone ------------------------------------------------------

    async def test_clean_document_is_cleared_without_system_two(self):
        s2 = FakeSystemTwo()
        d = await self.run_triage(signals(), s2)
        self.assertEqual((d.verdict, d.decided_by), (Verdict.CLEARED, "SYSTEM_ONE"))
        self.assertEqual(s2.requests, [])
        self.assertEqual(d.escalations, [])

    async def test_high_confidence_block_short_circuits_open_rules(self):
        # P1 fires on Jev alone; P7 is open, but nothing it could say would lift a block.
        s2 = FakeSystemTwo()
        d = await self.run_triage(signals(movement_details=0.93, intended_audience=external(), legal_exposure=0.5), s2)
        self.assertEqual((d.verdict, d.decided_by), (Verdict.BLOCKED, "SYSTEM_ONE"))
        self.assertEqual(d.offices, ["security_desk"])
        self.assertEqual(d.review_offices, ["legal_counsel"])
        self.assertEqual(s2.requests, [])

    async def test_route_rule_sends_to_its_office(self):
        d = await self.run_triage(signals(financial_commitment=0.95))
        self.assertEqual((d.verdict, d.offices), (Verdict.ROUTED, ["finance_approver"]))

    async def test_fast_mode_sends_open_uncertainty_to_a_human(self):
        s2 = FakeSystemTwo()
        d = await self.run_triage(signals(financial_commitment=0.55), s2, mode="fast")
        self.assertEqual((d.verdict, d.decided_by), (Verdict.HUMAN_REVIEW, "PENDING_HUMAN"))
        self.assertEqual([e.code for e in d.escalations], ["SYSTEM_TWO_DISABLED", "UNRESOLVED_CONDITION"])
        self.assertEqual(d.escalations[1].condition_id, "financial_commitment")
        self.assertEqual(d.review_offices, ["finance_approver"])
        self.assertEqual(s2.requests, [])

    async def test_system_one_failure_fails_closed(self):
        s2 = FakeSystemTwo()
        d = await self.run_triage(s1=FakeSystemOne(error="TypeSafeAPITimeoutError: timed out"), s2=s2)
        self.assertEqual(d.verdict, Verdict.HUMAN_REVIEW)
        self.assertEqual([e.code for e in d.escalations], ["SYSTEM_ONE_UNAVAILABLE"])
        self.assertEqual((d.risk_index, d.priority), (1.0, "EXPEDITE"))
        self.assertEqual(s2.requests, [])

    # --- System-2 ------------------------------------------------------------

    async def test_only_conditions_that_can_change_the_outcome_reach_system_two(self):
        # movement_details is uncertain too, but P1 is already FALSE on audience.
        s2 = FakeSystemTwo([{"id": "financial_commitment", "verdict": "no"}])
        await self.run_triage(signals(financial_commitment=0.55, movement_details=0.5), s2)
        (request,) = s2.requests
        self.assertEqual([p.id for p in request.propositions], ["financial_commitment"])
        self.assertEqual(request.propositions[0].jev_probability, 0.55)
        self.assertEqual(request.document, DOC)

    async def test_grounded_yes_resolves_the_rule(self):
        s2 = FakeSystemTwo([{"id": "financial_commitment", "verdict": "yes",
                             "evidence": "release the first payment of EUR 48,000 on Friday"}])
        d = await self.run_triage(signals(financial_commitment=0.55), s2)
        self.assertEqual((d.verdict, d.decided_by, d.offices), (Verdict.ROUTED, "SYSTEM_TWO", ["finance_approver"]))
        c = condition(d, "P6", "financial_commitment")
        self.assertEqual((c.truth, c.resolved_by, c.probability), (Truth.TRUE, "SYSTEM_TWO", 0.55))

    async def test_ungrounded_yes_is_discarded(self):
        s2 = FakeSystemTwo([{"id": "financial_commitment", "verdict": "yes",
                             "evidence": "the group approved a budget of EUR 48,000"}])
        d = await self.run_triage(signals(financial_commitment=0.55), s2)
        self.assertEqual(d.verdict, Verdict.HUMAN_REVIEW)
        self.assertEqual([x.reason for x in d.system_two.discarded], ["UNGROUNDED_YES"])
        self.assertIn("not a verbatim quote", d.escalations[-1].detail)

    async def test_reviewer_can_clear_a_standard_rule(self):
        s2 = FakeSystemTwo([{"id": "legal_exposure", "verdict": "no", "rationale": "no legal matter mentioned"}])
        d = await self.run_triage(signals(legal_exposure=0.45), s2)
        self.assertEqual((d.verdict, d.decided_by), (Verdict.CLEARED, "SYSTEM_TWO"))

    async def test_reviewer_cannot_clear_a_high_stakes_rule(self):
        s2 = FakeSystemTwo([{"id": "security_incident", "verdict": "no"}])
        d = await self.run_triage(signals(security_incident=0.4), s2)
        self.assertEqual((d.verdict, d.review_offices), (Verdict.HUMAN_REVIEW, ["security_desk"]))
        self.assertEqual([x.reason for x in d.system_two.discarded], ["HIGH_STAKES_NO"])

    async def test_shared_condition_no_clears_standard_but_preserves_high_stakes_trace(self):
        self.policy.rules[5].when = ["security_incident"]
        s2 = FakeSystemTwo([{"id": "security_incident", "verdict": "no"}])
        d = await self.run_triage(signals(security_incident=0.5), s2)
        self.assertEqual(condition(d, "P6", "security_incident").truth, Truth.FALSE)
        self.assertEqual(condition(d, "P5", "security_incident").truth, Truth.UNCERTAIN)
        self.assertEqual([x.reason for x in d.system_two.discarded], ["HIGH_STAKES_NO"])
        self.assertIn("cannot clear a high-stakes rule", d.escalations[-1].detail)

    async def test_reviewer_can_confirm_a_high_stakes_block(self):
        doc = "Delegation lands 14:10 at Terminal 2; motorcade route via the north bridge to the Hotel Arden."
        s2 = FakeSystemTwo([{"id": "staff_movements", "verdict": "yes",
                             "evidence": "motorcade route via the north bridge to the Hotel Arden"}])
        d = await self.run_triage(signals(movement_details=0.6, intended_audience=external()), s2, document=doc)
        self.assertEqual((d.verdict, d.decided_by, d.offices), (Verdict.BLOCKED, "SYSTEM_TWO", ["security_desk"]))

    async def test_unknown_and_duplicate_verdicts_are_ignored(self):
        s2 = FakeSystemTwo([
            {"id": "made_up", "verdict": "yes", "evidence": "anything at all"},
            {"id": "legal_exposure", "verdict": "no"},
            {"id": "legal_exposure", "verdict": "yes", "evidence": "sign the cold-chain contract"},
        ])
        d = await self.run_triage(signals(legal_exposure=0.45), s2)
        self.assertEqual(d.verdict, Verdict.CLEARED)
        self.assertEqual([x.reason for x in d.system_two.discarded], ["UNKNOWN_ID", "DUPLICATE"])

    async def test_missing_verdict_leaves_the_rule_open(self):
        d = await self.run_triage(signals(legal_exposure=0.45), FakeSystemTwo([]))
        self.assertEqual(d.verdict, Verdict.HUMAN_REVIEW)
        self.assertIn("reviewer returned no verdict", d.escalations[-1].detail)

    async def test_system_two_failure_goes_to_a_human(self):
        d = await self.run_triage(signals(legal_exposure=0.45), FakeSystemTwo(error="HTTP 502: upstream"))
        self.assertEqual(d.verdict, Verdict.HUMAN_REVIEW)
        self.assertEqual([e.code for e in d.escalations], ["SYSTEM_TWO_UNAVAILABLE", "UNRESOLVED_CONDITION"])
        self.assertEqual(d.system_two.error, "HTTP 502: upstream")

    # --- Priority and queue order --------------------------------------------

    async def test_expedite_reads_the_urgency_distribution(self):
        d = await self.run_triage(signals(urgency=score({0: 0.1, 1: 0.3, 2: 0.4, 3: 0.2})))
        self.assertEqual(d.priority, "EXPEDITE")  # P(urgency >= 2) = 0.6
        d = await self.run_triage(signals(urgency=score({0: 0.2, 1: 0.4, 2: 0.3, 3: 0.1})))
        self.assertEqual(d.priority, "STANDARD")  # P(urgency >= 2) = 0.4

    async def test_risk_index_orders_blocked_above_cleared(self):
        clean = await self.run_triage(signals())
        blocked = await self.run_triage(signals(movement_details=0.93, intended_audience=external(),
                                                sensitivity=score({0: 0.0, 1: 0.05, 2: 0.25, 3: 0.7})))
        self.assertLess(clean.risk_index, 0.25)
        self.assertGreater(blocked.risk_index, 0.7)


class GroundingTests(unittest.TestCase):
    def test_quote_must_appear_in_the_document(self):
        doc = "The  Minister’s office\nconfirmed the visit."
        self.assertTrue(is_grounded("the minister's office confirmed", doc))
        self.assertTrue(is_grounded("“confirmed the visit.”", doc))
        self.assertFalse(is_grounded("the minister cancelled the visit", doc))
        self.assertFalse(is_grounded("visit", doc))  # too short to count as evidence


if __name__ == "__main__":
    unittest.main()
