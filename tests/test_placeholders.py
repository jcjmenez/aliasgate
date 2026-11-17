import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aliasgate.placeholders import (
    MAX_PLACEHOLDER_LENGTH,
    MAX_TOLERANT_LENGTH,
    TOLERANT,
    format_placeholder,
    parse_match,
)
from aliasgate.types import MAX_ENTITY_TYPE_LENGTH

ENTITY_TYPES = st.from_regex(r"[A-Z][A-Z_]{0,39}", fullmatch=True)
CANONICAL = re.compile(r"\[[A-Z][A-Z_]{0,39}_[1-9][0-9]*\]")


def _parse(text: str) -> tuple[str, int]:
    match = TOLERANT.fullmatch(text)
    assert match is not None
    return parse_match(match)


def test_format_placeholder_returns_the_canonical_form() -> None:
    assert format_placeholder("PERSON", 1) == "[PERSON_1]"
    assert format_placeholder("CREDIT_CARD", 12) == "[CREDIT_CARD_12]"


@pytest.mark.parametrize("number", [0, -1])
def test_format_placeholder_rejects_non_positive_numbers(number: int) -> None:
    with pytest.raises(ValueError, match="number"):
        format_placeholder("PERSON", number)


@pytest.mark.parametrize("entity_type", ["person", "PERSON1", "", "A" * 41])
def test_format_placeholder_rejects_invalid_entity_types(entity_type: str) -> None:
    with pytest.raises(ValueError, match="entity type"):
        format_placeholder(entity_type, 1)


def test_format_placeholder_rejects_results_longer_than_the_maximum() -> None:
    longest_type = "A" * MAX_ENTITY_TYPE_LENGTH
    digits_that_fit = MAX_PLACEHOLDER_LENGTH - len(f"[{longest_type}_]")
    largest_fitting = 10**digits_that_fit - 1
    assert len(format_placeholder(longest_type, largest_fitting)) == MAX_PLACEHOLDER_LENGTH
    with pytest.raises(ValueError, match="length"):
        format_placeholder(longest_type, largest_fitting + 1)


def test_length_bounds() -> None:
    assert MAX_PLACEHOLDER_LENGTH == 64
    assert MAX_TOLERANT_LENGTH == 128


@pytest.mark.parametrize(
    "text",
    [
        "[PERSON_1]",
        "[person_1]",
        "[Person_12]",
        "[PERSON 1]",
        "[ PERSON_1 ]",
        "\\[PERSON_1\\]",
        "[CREDIT_CARD_3]",
        "[PERSON\\_1]",
        "[CREDIT\\_CARD\\_3]",
        "[\tPERSON_1\t\t]",
        "[PERSON_123456789]",
    ],
)
def test_tolerant_fully_matches_accepted_variants(text: str) -> None:
    assert TOLERANT.fullmatch(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "[PERSON]",
        "[1]",
        "[PERSON_]",
        "PERSON_1",
        "[PERSON-1]",
        "[PER SON_1]",
        "[CREDIT CARD_3]",
        "[PERSON_01234567890]",
        "[PERSON_1234567890]",
        "[   PERSON_1]",
        "[PERSON_1   ]",
        "[_PERSON_1]",
        "[1PERSON_1]",
        "[ÉLAN_1]",
        "(PERSON_1)",
        "{PERSON_1}",
        "<PERSON_1>",
    ],
)
def test_tolerant_finds_nothing_in_rejected_variants(text: str) -> None:
    assert TOLERANT.search(text) is None


def test_backslash_is_not_consumed_when_only_the_opening_bracket_is_escaped() -> None:
    match = TOLERANT.search("\\[PERSON_1]")
    assert match is not None
    assert match.group(0) == "[PERSON_1]"
    assert match.start() == 1


def test_only_the_closing_bracket_escaped_is_not_a_placeholder() -> None:
    assert TOLERANT.search("[PERSON_1\\]") is None


def test_both_escaped_brackets_are_part_of_the_match() -> None:
    match = TOLERANT.search("see \\[PERSON_1\\] here")
    assert match is not None
    assert match.group(0) == "\\[PERSON_1\\]"


@pytest.mark.parametrize("text", ["[credit_card_3]", "[credit\\_card\\_3]"])
def test_parse_match_removes_backslashes_and_upper_cases_the_type(text: str) -> None:
    assert _parse(text) == ("CREDIT_CARD", 3)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[PERSON 1]", ("PERSON", 1)),
        ("\\[PERSON_1\\]", ("PERSON", 1)),
        ("[ person_42 ]", ("PERSON", 42)),
        ("[CREDIT_CARD 3]", ("CREDIT_CARD", 3)),
        ("[A__1]", ("A_", 1)),
    ],
)
def test_parse_match_handles_every_variant(text: str, expected: tuple[str, int]) -> None:
    assert _parse(text) == expected


@pytest.mark.parametrize("pattern", [r"(x)?(y)?", r"(a)?(b)?(c)?(d)?"])
def test_parse_match_rejects_a_match_of_another_pattern(pattern: str) -> None:
    match = re.fullmatch(pattern, "")
    assert match is not None
    with pytest.raises(ValueError, match="TOLERANT"):
        parse_match(match)


def test_longest_constructible_match_is_within_the_bound() -> None:
    longest_type = "A" + "\\_" * (MAX_ENTITY_TYPE_LENGTH - 1)
    text = "\\[" + "\t\t" + longest_type + "\\_" + "9" * 9 + "\t\t" + "\\]"
    match = TOLERANT.fullmatch(text)
    assert match is not None
    assert len(match.group(0)) <= MAX_TOLERANT_LENGTH
    assert parse_match(match) == ("A" + "_" * (MAX_ENTITY_TYPE_LENGTH - 1), 999_999_999)


@pytest.mark.parametrize(
    "text",
    [
        "\\[" + "A" * (MAX_ENTITY_TYPE_LENGTH + 1) + "_1\\]",
        "[" + "A" * (MAX_ENTITY_TYPE_LENGTH + 1) + "_1]",
        "[PERSON_" + "9" * 10 + "]",
        "[\t\t\tPERSON_1]",
        "[PERSON_1\t\t\t]",
    ],
    ids=[
        "escaped-41-letter-type",
        "41-letter-type",
        "ten-digits",
        "three-tabs-before",
        "three-tabs-after",
    ],
)
def test_one_step_past_each_bound_is_not_a_placeholder(text: str) -> None:
    assert TOLERANT.search(text) is None


_OPEN = st.sampled_from(["[", "\\["])
_CLOSE = st.sampled_from(["]", "\\]"])
_PAD = st.text(alphabet=" \t", max_size=4)
_TYPE_PART = st.lists(st.sampled_from(["A", "z", "_", "\\_"]), min_size=1, max_size=45).map("".join)
_SEPARATOR = st.sampled_from(["_", "\\_", " ", ""])
_DIGITS = st.text(alphabet="0123456789", max_size=12)
_PLACEHOLDER_LIKE = st.tuples(_OPEN, _PAD, _TYPE_PART, _SEPARATOR, _DIGITS, _PAD, _CLOSE).map(
    "".join
)
_CANDIDATES = st.lists(
    _PLACEHOLDER_LIKE | st.text(alphabet="[]\\_ \tAz9x", max_size=20), max_size=6
).map("".join)


@given(_CANDIDATES)
def test_no_match_is_longer_than_max_tolerant_length(text: str) -> None:
    for match in TOLERANT.finditer(text):
        assert len(match.group(0)) <= MAX_TOLERANT_LENGTH


@given(ENTITY_TYPES, st.integers(min_value=1, max_value=10**6))
def test_parse_match_inverts_format_placeholder(entity_type: str, number: int) -> None:
    assert _parse(format_placeholder(entity_type, number)) == (entity_type, number)


@given(ENTITY_TYPES, st.integers(min_value=1, max_value=10**21))
def test_format_placeholder_output_is_canonical_or_rejected(entity_type: str, number: int) -> None:
    try:
        placeholder = format_placeholder(entity_type, number)
    except ValueError:
        assert len(f"[{entity_type}_{number}]") > MAX_PLACEHOLDER_LENGTH
        return
    assert CANONICAL.fullmatch(placeholder) is not None
    assert len(placeholder) <= MAX_PLACEHOLDER_LENGTH
