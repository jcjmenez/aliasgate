"""Whole-term search patterns, shared by propagation, the dictionary and the leak audit."""

import re
from typing import Final

# A whole term is not preceded by a word character, and not followed by one, nor by an
# apostrophe and a "t" that ends the word, so that "Don" never matches in "Don't" (slice 17).
TERM_START: Final[str] = r"(?<!\w)"
TERM_END: Final[str] = "(?!\\w|['\u2019]t(?!\\w))"


def term_body(surface: str) -> str:
    """The regular expression for `surface` without word boundaries: its tokens, escaped and
    joined by `\\s+`."""
    tokens = surface.split()
    if not tokens:
        raise ValueError("term must contain at least one non-whitespace character")
    return r"\s+".join(re.escape(token) for token in tokens)


def build_term_pattern(surface: str) -> re.Pattern[str]:
    """Pattern matching `surface` as a whole term, case-insensitively, with any whitespace run
    between its tokens, and never directly before an apostrophe (`'` or U+2019) and a `t` that
    ends a word (architecture §4.3 step 3)."""
    return re.compile(f"{TERM_START}(?:{term_body(surface)}){TERM_END}", re.IGNORECASE)


def starts_upper(text: str) -> bool:
    """Whether `text` starts with an upper-case or title-case letter, the only places where a
    capitalised entity is replaced and audited (architecture §4.3 step 3, §8)."""
    return text[:1].isupper() or text[:1].istitle()
