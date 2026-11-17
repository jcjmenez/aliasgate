"""Placeholder format `[TYPE_N]` and the tolerant pattern used to find placeholders again."""

import re
from typing import Final

from aliasgate.types import validate_entity_type

MAX_PLACEHOLDER_LENGTH: Final = 64  # canonical placeholders
MAX_TOLERANT_LENGTH: Final = 128  # any match of TOLERANT

# Architecture §5.2, copied verbatim. Every quantifier is bounded so that no match can exceed
# MAX_TOLERANT_LENGTH; the stream restorer depends on that bound. Do not simplify this pattern
# without re-running the property tests.
_BODY = r"[ \t]{0,2}([A-Za-z](?:\\?_|[A-Za-z]){0,39}?)(?:\\?_| )(\d{1,9})[ \t]{0,2}"
TOLERANT: Final[re.Pattern[str]] = re.compile(rf"\\\[{_BODY}\\\]|\[{_BODY}\]")


def format_placeholder(entity_type: str, number: int) -> str:
    """Return the canonical placeholder, e.g. format_placeholder("PERSON", 1) == "[PERSON_1]"."""
    validate_entity_type(entity_type)
    if number < 1:
        raise ValueError("placeholder number must be at least 1")
    placeholder = f"[{entity_type}_{number:d}]"
    if len(placeholder) > MAX_PLACEHOLDER_LENGTH:
        raise ValueError(
            f"placeholder length {len(placeholder)} exceeds the maximum of {MAX_PLACEHOLDER_LENGTH}"
        )
    return placeholder


def parse_match(match: re.Match[str]) -> tuple[str, int]:
    """Return (entity type, number) for a match of TOLERANT: take groups 1-2 or 3-4, whichever
    matched, remove backslashes from the type and upper-case it."""
    if match.re.groups != TOLERANT.groups:
        raise ValueError("match is not a match of TOLERANT")
    type_text, number_text = match.group(1, 2)
    if type_text is None:
        type_text, number_text = match.group(3, 4)
    if type_text is None or number_text is None:
        raise ValueError("match is not a match of TOLERANT")
    return type_text.replace("\\", "").upper(), int(number_text)
