import copy
import unittest
from pydantic import ValidationError
from src.policy import DEFAULT_POLICY_PATH, evaluate_rules, kleene_and, load_policy, validate_policy
from src.questions import BATTERY
from src.schemas import Band, Policy, Truth
from tests.fakes import external, signals


def rule(results, rule_id):
    return next(r for r in results if r.rule_id == rule_id)


class PolicyValidationTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy(DEFAULT_POLICY_PATH, BATTERY)
        self.raw = self.policy.model_dump()

    def problems(self, mutate):
        raw = copy.deepcopy(self.raw)
        mutate(raw)
        return validate_policy(Policy.model_validate(raw), BATTERY)

    def test_default_policy_is_clean(self):
        self.assertEqual(validate_policy(self.policy, BATTERY), [])

    def test_every_bad_reference_is_reported(self):
        def mutate(raw):
            raw["conditions"]["staff_movements"]["signal"] = "no_such_question"
            raw["conditions"]["restricted"]["value"] = 4                 # sensitivity has levels 0..3
            raw["conditions"]["public_audience"]["value"] = ["press"]    # not an option
            raw["conditions"]["personal_data"]["test"] = "at_least"      # noul cannot take at_least
            raw["rules"][1]["rule_id"] = "P1"                            # duplicate
            raw["rules"][2]["when"].append("ghost")                      # unknown condition
        problems = " | ".join(self.problems(mutate))
        for fragment in ("unknown signal 'no_such_question'", "at_least value must be a level in 1..3",
                         "unknown options ['press']", "needs a score signal", "duplicate rule_id",
                         "unknown condition 'ghost'"):
            self.assertIn(fragment, problems)

    def test_condition_that_is_always_true_is_rejected(self):
        options = list(BATTERY["intended_audience"].options)
        problems = self.problems(lambda raw: raw["conditions"]["external_audience"].update(value=options))
        self.assertTrue(any("always true" in p for p in problems))

    def test_unused_condition_is_rejected(self):
        problems = self.problems(lambda raw: raw["rules"].pop())  # P8 is the only user of public_audience
        self.assertTrue(any("public_audience: not used" in p for p in problems))

    def test_band_must_be_ordered(self):
        with self.assertRaises(ValidationError):
            Band(yes=0.3, no=0.7)


class RuleEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy(DEFAULT_POLICY_PATH, BATTERY)

    def test_kleene_and(self):
        T, F, U = Truth.TRUE, Truth.FALSE, Truth.UNCERTAIN
        self.assertIs(kleene_and([T, T]), T)
        self.assertIs(kleene_and([T, U]), U)
        self.assertIs(kleene_and([U, F]), F)

    def test_uncertainty_that_cannot_change_the_outcome_is_ignored(self):
        # Jev is torn on staff movements, but the memo clearly stays internal, so P1 cannot fire.
        results = evaluate_rules(self.policy, signals(movement_details=0.5))
        p1 = rule(results, "P1")
        self.assertIs(p1.truth, Truth.FALSE)
        self.assertIs(p1.conditions[0].truth, Truth.UNCERTAIN)
        self.assertIs(p1.conditions[1].truth, Truth.FALSE)

    def test_high_stakes_band_is_stricter_about_clearing(self):
        # p = 0.2 clears a standard rule (no <= 0.3) but not a high-stakes one (no <= 0.1).
        results = evaluate_rules(self.policy, signals(financial_commitment=0.2, security_incident=0.2))
        self.assertIs(rule(results, "P6").truth, Truth.FALSE)
        self.assertIs(rule(results, "P5").truth, Truth.UNCERTAIN)

    def test_condition_probability_comes_from_the_distribution(self):
        results = evaluate_rules(self.policy, signals(movement_details=0.9, intended_audience=external()))
        p1 = rule(results, "P1")
        self.assertIs(p1.truth, Truth.TRUE)
        # P(partner_government or public_release) = 0.9 + 0.025
        self.assertAlmostEqual(p1.conditions[1].probability, 0.925)

    def test_reviewer_no_never_clears_a_high_stakes_rule(self):
        sigs = signals(security_incident=0.5, financial_commitment=0.5)
        overrides = {"security_incident": Truth.FALSE, "financial_commitment": Truth.FALSE}
        results = evaluate_rules(self.policy, sigs, overrides)
        self.assertIs(rule(results, "P5").truth, Truth.UNCERTAIN)
        p6 = rule(results, "P6")
        self.assertIs(p6.truth, Truth.FALSE)
        self.assertEqual(p6.conditions[0].resolved_by, "SYSTEM_TWO")

    def test_override_never_touches_a_condition_jev_settled(self):
        results = evaluate_rules(self.policy, signals(financial_commitment=0.95), {"financial_commitment": Truth.FALSE})
        p6 = rule(results, "P6")
        self.assertIs(p6.truth, Truth.TRUE)
        self.assertEqual(p6.conditions[0].resolved_by, "SYSTEM_ONE")


if __name__ == "__main__":
    unittest.main()
