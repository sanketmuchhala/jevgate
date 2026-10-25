from __future__ import annotations
import re

MIN_EVIDENCE_CHARS = 8


def _normalize(text: str) -> str:
    text = text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    return re.sub(r"\s+", " ", text).strip().casefold()


def find_quote_span(evidence: str, document: str, min_chars: int = MIN_EVIDENCE_CHARS) -> tuple[int, int] | None:
    """Stub replaced by Workstream B: returns (start_char, end_char) in raw document if grounded."""
    quote = _normalize(evidence).strip("\"'. ")
    if len(quote) < min_chars:
        return None
    norm_doc = _normalize(document)
    idx = norm_doc.find(quote)
    if idx == -1:
        return None
    return (0, min(len(document), len(evidence)))
