import unittest
from src.calibration import choice_confidence, noul_confidence, p_at_least, p_in, score_confidence


class CalibrationTests(unittest.TestCase):
    def test_noul_confidence_is_distance_from_a_coin_flip(self):
        self.assertEqual(noul_confidence(0.5), 0.0)
        self.assertAlmostEqual(noul_confidence(0.95), 0.9)
        self.assertAlmostEqual(noul_confidence(0.05), 0.9)

    def test_choice_confidence_counts_only_the_top_option(self):
        # Worked example from docs.typesafe.ai/confidence.
        self.assertAlmostEqual(choice_confidence([0.6, 0.3, 0.1]), 0.4)
        self.assertAlmostEqual(choice_confidence([0.6, 0.2, 0.2]), 0.4)
        self.assertAlmostEqual(choice_confidence([1.0, 0.0, 0.0]), 1.0)

    def test_score_confidence_penalizes_distant_levels_more(self):
        self.assertAlmostEqual(score_confidence({0: 0.0, 1: 0.5, 2: 0.5}), 0.25)
        self.assertAlmostEqual(score_confidence({0: 0.5, 1: 0.0, 2: 0.5}), 0.0)
        # The docs' bug_severity example: (0, 0.57, 0.43) -> about 0.35.
        self.assertAlmostEqual(score_confidence({0: 0.0, 1: 0.57, 2: 0.43}), 0.355, places=3)

    def test_api_confidence_follows_from_its_probabilities(self):
        # The quickstart's raw response returns frustration confidence 0.5 for (0.33, 0.67, 0).
        self.assertAlmostEqual(score_confidence({0: 0.33, 1: 0.67, 2: 0.0}), 0.5, places=2)

    def test_distribution_reads(self):
        probs = {0: 0.1, 1: 0.2, 2: 0.3, 3: 0.4}
        self.assertAlmostEqual(p_at_least(probs, 2), 0.7)
        self.assertAlmostEqual(p_at_least(probs, 0), 1.0)
        self.assertAlmostEqual(p_in({"a": 0.5, "b": 0.3, "c": 0.2}, ["a", "c"]), 0.7)
        self.assertEqual(p_in({"a": 1.0}, ["missing"]), 0.0)


if __name__ == "__main__":
    unittest.main()
