"""Placeholder format `[TYPE_N]` or `[TYPE_N_V]` and the tolerant pattern that finds them again."""

import re
from typing import Final

from aliasgate.types import validate_entity_type

MAX_PLACEHOLDER_LENGTH: Final = 64  # canonical placeholders
MAX_TOLERANT_LENGTH: Final = 128  # any match of TOLERANT

# The variant letters of a link group, in the order they are given out. The first surface of a
# group has no letter, so "A" is never used.
VARIANT_LETTERS: Final = "BCDEFGHIJKLMNOPQRSTUVWXYZ"

# Architecture §5.2, copied verbatim. Every quantifier is bounded so that no match can exceed
# MAX_TOLERANT_LENGTH; the stream restorer depends on that bound. The longest match is 101
# characters: escaped brackets (2 + 2), two tabs on each side (2 + 2), a 40-unit type of which
# 39 units are escaped underscores (79), an escaped separator (2), nine digits (9), an escaped
# separator and a variant letter (3). Do not simplify this pattern without re-running the
# property tests.
_BODY = (
    r"[ \t]{0,2}([A-Za-z](?:\\?_|[A-Za-z]){0,39}?)(?:\\?_| )(\d{1,9})"
    r"(?:(?:\\?_| )([A-Za-z]))?[ \t]{0,2}"
)
TOLERANT: Final[re.Pattern[str]] = re.compile(rf"\\\[{_BODY}\\\]|\[{_BODY}\]")


def format_placeholder(entity_type: str, number: int, variant: str | None = None) -> str:
    """Return the canonical placeholder: "[ORG_1]" without a variant, "[ORG_1_B]" with one."""
    validate_entity_type(entity_type)
    if number < 1:
        raise ValueError("placeholder number must be at least 1")
    if variant is None:
        placeholder = f"[{entity_type}_{number:d}]"
    elif len(variant) == 1 and variant in VARIANT_LETTERS:
        placeholder = f"[{entity_type}_{number:d}_{variant}]"
    else:
        # The value itself is not echoed, as for entity types.
        raise ValueError(
            f"placeholder variant must be one letter from B to Z (got a string of length "
            f"{len(variant)})"
        )
    if len(placeholder) > MAX_PLACEHOLDER_LENGTH:
        raise ValueError(
            f"placeholder length {len(placeholder)} exceeds the maximum of {MAX_PLACEHOLDER_LENGTH}"
        )
    return placeholder


def parse_match(match: re.Match[str]) -> tuple[str, int, str | None]:
    """Return (entity type, number, variant) for a match of TOLERANT. Backslashes are removed
    from the type, the type and the variant are upper-cased, and the variant is None when the
    match has none."""
    if match.re.groups != TOLERANT.groups:
        raise ValueError("match is not a match of TOLERANT")
    type_text, number_text, variant = match.group(1, 2, 3)
    if type_text is None:
        type_text, number_text, variant = match.group(4, 5, 6)
    if type_text is None or number_text is None:
        raise ValueError("match is not a match of TOLERANT")
    return (
        type_text.replace("\\", "").upper(),
        int(number_text),
        None if variant is None else variant.upper(),
    )
