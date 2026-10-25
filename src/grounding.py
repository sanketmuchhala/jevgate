from __future__ import annotations
import re

MIN_EVIDENCE_CHARS = 8

_CURLY = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})
# A whitespace run, or any single other character. `\s` here is the same class
# `_normalize` collapses, so both sides agree on what counts as whitespace.
_TOKEN = re.compile(r"(?P<space>\s+)|.", re.DOTALL)


def _normalize(text: str) -> str:
    """Same as src/router.py:_normalize, so a located span and the router's check agree."""
    text = text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    return re.sub(r"\s+", " ", text).strip().casefold()


def _quote(evidence: str) -> str:
    """The evidence as the router compares it: normalized, outer quotes, periods and spaces removed."""
    return _normalize(evidence).strip("\"'. ")


def normalize_with_offsets(document: str) -> tuple[str, list[tuple[int, int]]]:
    """Normalize `document` and keep, for every normalized character, the raw (start, end) it came from.

    A collapsed whitespace run maps to the whole run, so a span can cover a newline.
    Case folding can expand one character ("ß" becomes "ss"); each expanded character
    maps to the same raw range. Leading and trailing spaces are kept rather than
    stripped: a quote never starts or ends with a space, so they cannot change a match.
    """
    chars: list[str] = []
    offsets: list[tuple[int, int]] = []
    for match in _TOKEN.finditer(document):
        folded = " " if match.group("space") else match.group().translate(_CURLY).casefold()
        chars.append(folded)
        offsets.extend([match.span()] * len(folded))
    return "".join(chars), offsets


def find_quote_span(evidence: str, document: str, min_chars: int = MIN_EVIDENCE_CHARS) -> tuple[int, int] | None:
    """Locate a reviewer's quote in the raw document.

    Returns (start_char, end_char) such that document[start_char:end_char] is the original
    passage, with its own newlines, case, and punctuation, and normalizes to the quote.
    Returns None if the quote is shorter than `min_chars` after normalization or does not
    appear. A match that would split a character whose case-folded form is longer than one
    character (part of an "ß") is not a quote of the document; the next occurrence is tried.
    """
    quote = _quote(evidence)
    if len(quote) < min_chars:
        return None
    normalized, offsets = normalize_with_offsets(document)
    start = normalized.find(quote)
    while start != -1:
        end = start + len(quote)
        starts_cleanly = start == 0 or offsets[start - 1] != offsets[start]
        ends_cleanly = end == len(offsets) or offsets[end] != offsets[end - 1]
        if starts_cleanly and ends_cleanly:
            return offsets[start][0], offsets[end - 1][1]
        start = normalized.find(quote, start + 1)
    return None


def is_grounded_quote(evidence: str, document: str, min_chars: int = MIN_EVIDENCE_CHARS) -> bool:
    return find_quote_span(evidence, document, min_chars) is not None
