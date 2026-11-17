"""Whole-term search patterns, shared by propagation, the dictionary and the leak audit."""

import re


def build_term_pattern(surface: str) -> re.Pattern[str]:
    """Pattern matching `surface` as a whole term, case-insensitively, with any whitespace run
    between its tokens (architecture §4.3 step 3)."""
    tokens = surface.split()
    if not tokens:
        raise ValueError("term must contain at least one non-whitespace character")
    body = r"\s+".join(re.escape(token) for token in tokens)
    return re.compile(rf"(?<!\w)(?:{body})(?!\w)", re.IGNORECASE)
