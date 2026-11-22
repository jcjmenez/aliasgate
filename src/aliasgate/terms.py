"""Whole-term search patterns, shared by propagation, the dictionary and the leak audit."""

import re


def term_body(surface: str) -> str:
    """The regular expression for `surface` without word boundaries: its tokens, escaped and
    joined by `\\s+`."""
    tokens = surface.split()
    if not tokens:
        raise ValueError("term must contain at least one non-whitespace character")
    return r"\s+".join(re.escape(token) for token in tokens)


def build_term_pattern(surface: str) -> re.Pattern[str]:
    """Pattern matching `surface` as a whole term, case-insensitively, with any whitespace run
    between its tokens (architecture §4.3 step 3)."""
    return re.compile(rf"(?<!\w)(?:{term_body(surface)})(?!\w)", re.IGNORECASE)


def starts_upper(text: str) -> bool:
    """Whether `text` starts with an upper-case or title-case letter, the only places where a
    capitalised entity is replaced and audited (architecture §4.3 step 3, §8)."""
    return text[:1].isupper() or text[:1].istitle()
