import random
import unittest
from src.grounding import MIN_EVIDENCE_CHARS, _normalize, find_quote_span, is_grounded_quote, normalize_with_offsets
from src.router import is_grounded

DOC = (
    "Minutes, procurement working group. The group agreed to sign the cold-chain contract with "
    "Norvale Logistics and to release the first payment of EUR 48,000 on Friday."
)


def stripped(evidence: str) -> str:
    return _normalize(evidence).strip("\"'. ")


def substring_grounded(evidence: str, document: str) -> bool:
    """The router's check before it delegated to src/grounding.py, kept as an independent reference."""
    quote = stripped(evidence)
    return len(quote) >= MIN_EVIDENCE_CHARS and quote in _normalize(document)


class FindQuoteSpanTests(unittest.TestCase):
    def assert_span(self, evidence: str, document: str, expected_raw: str) -> tuple[int, int]:
        span = find_quote_span(evidence, document)
        self.assertIsNotNone(span, f"{evidence!r} not located")
        start, end = span
        self.assertEqual(document[start:end], expected_raw)
        self.assertEqual(_normalize(document[start:end]), _normalize(stripped(evidence)))
        return span

    def test_exact_match_returns_raw_offsets(self):
        start, end = self.assert_span(
            "release the first payment of EUR 48,000 on Friday", DOC, "release the first payment of EUR 48,000 on Friday",
        )
        self.assertEqual(start, DOC.index("release"))
        self.assertEqual(end, DOC.index(" on Friday") + len(" on Friday"))

    def test_case_insensitive_match_keeps_original_case(self):
        self.assert_span("NORVALE LOGISTICS AND TO RELEASE", DOC, "Norvale Logistics and to release")

    def test_whitespace_runs_and_newlines_map_back_to_the_original(self):
        doc = "Cable 14.\nThe  Minister’s office\nconfirmed\t\tthe visit on Tuesday."
        self.assert_span("the minister's office confirmed", doc, "The  Minister’s office\nconfirmed")
        self.assert_span("office confirmed the visit", doc, "office\nconfirmed\t\tthe visit")

    def test_curly_and_straight_quotes_match_either_way(self):
        doc = 'The note said "hold the line on tariffs" twice.'
        self.assert_span("“hold the line on tariffs”", doc, "hold the line on tariffs")
        curly = "The note said “hold the line on tariffs” twice."
        self.assert_span('said "hold the line on tariffs" twice', curly, "said “hold the line on tariffs” twice")
        # Outer quotes are stripped from evidence, so a closing quote mark is not part of the span.
        self.assert_span('said "hold the line on tariffs"', curly, "said “hold the line on tariffs")

    def test_outer_ellipses_quotes_and_periods_are_ignored(self):
        self.assert_span(
            '"...release the first payment of EUR 48,000..."', DOC, "release the first payment of EUR 48,000",
        )
        self.assert_span("  'on Friday.'  ", DOC, "on Friday")

    def test_inner_punctuation_must_match(self):
        self.assertIsNone(find_quote_span("payment of EUR 48000", DOC))
        self.assertIsNone(find_quote_span("release the first payment ... on Friday", DOC))

    def test_short_quotes_do_not_count(self):
        self.assertIsNone(find_quote_span("Friday", DOC))
        self.assertIsNone(find_quote_span('"...EUR 48..."', DOC))  # 6 characters once stripped
        self.assertIsNone(find_quote_span("", DOC))
        self.assertEqual(find_quote_span("Friday", DOC, min_chars=3), (DOC.index("Friday"), DOC.index("Friday") + 6))

    def test_paraphrase_or_invention_is_not_located(self):
        self.assertIsNone(find_quote_span("the group approved a budget of EUR 48,000", DOC))
        self.assertIsNone(find_quote_span("payment to Norvale Logistics", DOC))
        self.assertFalse(is_grounded_quote("the group approved a budget of EUR 48,000", DOC))

    def test_first_occurrence_is_returned(self):
        doc = "Route A: north bridge. Fallback route: north bridge."
        self.assertEqual(find_quote_span("north bridge", doc), (9, 21))

    def test_expanding_case_folds_map_to_the_whole_character(self):
        doc = "Sperrung: Die Straße ist gesperrt bis Montag."
        self.assert_span("die STRASSE ist gesperrt", doc, "Die Straße ist gesperrt")

    def test_a_match_cannot_split_a_folded_character(self):
        # "Maße" folds to "masse"; a quote starting at the second "s" names half a character.
        self.assertIsNone(find_quote_span("se und Gewichte", "Maße und Gewichte"))
        doc = "Maße und Gewichte; Grundsätze, Gänse und Gewichte"
        self.assert_span("se und Gewichte", doc, "se und Gewichte")

    def test_offsets_count_code_points(self):
        doc = "Flag 🚩 raised: release the first payment today."
        start, end = self.assert_span("release the first payment", doc, "release the first payment")
        self.assertEqual(start, doc.index("release"))

    def test_router_gate_keeps_the_substring_rule(self):
        doc = "Cable 14. The Minister’s office confirmed the visit."
        for evidence in ("the minister's office confirmed", "“confirmed the visit.”", "the minister cancelled the visit",
                         "visit", "release the first payment"):
            self.assertEqual(is_grounded(evidence, doc), substring_grounded(evidence, doc), evidence)


class PropertyTests(unittest.TestCase):
    ALPHABET = list("abcdefghij ABCDEFGHIJ.,;'\"") + ["  ", "\n", "\t", "\r\n", " ", "“", "”", "‘", "’"]

    def random_document(self, rng: random.Random, extra: list[str]) -> str:
        return "".join(rng.choices(self.ALPHABET + extra, k=rng.randint(20, 120)))

    def test_offset_normalization_matches_the_plain_normalizer(self):
        rng = random.Random(20261025)
        for _ in range(500):
            doc = self.random_document(rng, ["ß", "İ", "ﬁ", "Σ", "ς"])
            normalized, offsets = normalize_with_offsets(doc)
            self.assertEqual(normalized.strip(), _normalize(doc))
            self.assertEqual(len(normalized), len(offsets))

    def test_every_located_span_round_trips_and_agrees_with_the_substring_rule(self):
        rng = random.Random(1024)
        located = 0
        for _ in range(2000):
            doc = self.random_document(rng, [])
            a, b = sorted(rng.sample(range(len(doc) + 1), 2))
            evidence = doc[a:b]
            span = find_quote_span(evidence, doc)
            self.assertEqual(span is not None, substring_grounded(evidence, doc), repr((evidence, doc)))
            if span is not None:
                located += 1
                self.assertEqual(_normalize(doc[span[0]:span[1]]), stripped(evidence))
        self.assertGreater(located, 500)


if __name__ == "__main__":
    unittest.main()
