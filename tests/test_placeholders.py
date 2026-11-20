import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aliasgate.placeholders import (
    MAX_PLACEHOLDER_LENGTH,
    MAX_TOLERANT_LENGTH,
    TOLERANT,
    VARIANT_LETTERS,
    format_placeholder,
    parse_match,
)
from aliasgate.types import MAX_ENTITY_TYPE_LENGTH

ENTITY_TYPES = st.from_regex(r"[A-Z][A-Z_]{0,39}", fullmatch=True)
CANONICAL = re.compile(r"\[[A-Z][A-Z_]{0,39}_[1-9][0-9]*(?:_[B-Z])?\]")
VARIANTS = st.none() | st.sampled_from(VARIANT_LETTERS)

# The pattern of v0.1.0, kept to check that every placeholder it found is still found.
_V010_BODY = r"[ \t]{0,2}([A-Za-z](?:\\?_|[A-Za-z]){0,39}?)(?:\\?_| )(\d{1,9})[ \t]{0,2}"
V010_TOLERANT = re.compile(rf"\\\[{_V010_BODY}\\\]|\[{_V010_BODY}\]")


def _parse(text: str) -> tuple[str, int, str | None]:
    match = TOLERANT.fullmatch(text)
    assert match is not None
    return parse_match(match)


def test_format_placeholder_returns_the_canonical_form() -> None:
    assert format_placeholder("PERSON", 1) == "[PERSON_1]"
    assert format_placeholder("CREDIT_CARD", 12) == "[CREDIT_CARD_12]"


def test_format_placeholder_with_a_variant_appends_the_letter() -> None:
    assert format_placeholder("ORG", 1, "B") == "[ORG_1_B]"
    assert format_placeholder("CREDIT_CARD", 3, "Z") == "[CREDIT_CARD_3_Z]"
    assert format_placeholder("ORG", 1, None) == format_placeholder("ORG", 1) == "[ORG_1]"


def test_variant_letters_are_b_to_z() -> None:
    assert VARIANT_LETTERS == "BCDEFGHIJKLMNOPQRSTUVWXYZ"


@pytest.mark.parametrize("variant", ["A", "b", "BB", "1", "", "É", "BCD"])
def test_format_placeholder_rejects_invalid_variants_without_repeating_them(variant: str) -> None:
    with pytest.raises(ValueError, match="variant") as excinfo:
        format_placeholder("ORG", 1, variant)
    if variant:
        assert f"'{variant}'" not in str(excinfo.value)
        assert f": {variant}" not in str(excinfo.value)


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


def test_format_placeholder_counts_the_variant_in_the_length() -> None:
    longest_type = "A" * MAX_ENTITY_TYPE_LENGTH
    digits_that_fit = MAX_PLACEHOLDER_LENGTH - len(f"[{longest_type}__Z]")
    largest_fitting = 10**digits_that_fit - 1
    assert len(format_placeholder(longest_type, largest_fitting, "Z")) == MAX_PLACEHOLDER_LENGTH
    with pytest.raises(ValueError, match="length"):
        format_placeholder(longest_type, largest_fitting + 1, "Z")


def test_the_longest_placeholder_with_nine_digits_and_a_variant_has_54_characters() -> None:
    longest_type = "A" * MAX_ENTITY_TYPE_LENGTH
    assert len(format_placeholder(longest_type, 999_999_999, "Z")) == 54


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
        "[ORG_1_B]",
        "[org_1_b]",
        "[ORG 1 B]",
        "[ORG_1 B]",
        "[ ORG_1_B ]",
        "[ORG\\_1\\_B]",
        "\\[ORG_1_B\\]",
        "[CREDIT_CARD_3_Z]",
        "[ORG_1_A]",
    ],
)
def test_tolerant_fully_matches_placeholders_with_a_variant(text: str) -> None:
    assert TOLERANT.fullmatch(text) is not None


@pytest.mark.parametrize(
    "text",
    ["[ORG_1_]", "[ORG_1B]", "[ORG_1_BB]", "[ORG_1__B]", "[ORG_1_1]", "[ORG_1-B]", "[ORG_1_É]"],
)
def test_tolerant_finds_nothing_in_rejected_variant_forms(text: str) -> None:
    assert TOLERANT.search(text) is None


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
    assert _parse(text) == ("CREDIT_CARD", 3, None)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[PERSON 1]", ("PERSON", 1, None)),
        ("\\[PERSON_1\\]", ("PERSON", 1, None)),
        ("[ person_42 ]", ("PERSON", 42, None)),
        ("[CREDIT_CARD 3]", ("CREDIT_CARD", 3, None)),
        ("[A__1]", ("A_", 1, None)),
        ("[ORG_1]", ("ORG", 1, None)),
        ("[org_1 b]", ("ORG", 1, "B")),
        ("[ORG\\_1\\_C]", ("ORG", 1, "C")),
        ("\\[credit_card_3_z\\]", ("CREDIT_CARD", 3, "Z")),
        ("[ORG_1_A]", ("ORG", 1, "A")),
    ],
)
def test_parse_match_handles_every_variant(
    text: str, expected: tuple[str, int, str | None]
) -> None:
    assert _parse(text) == expected


@pytest.mark.parametrize("pattern", [r"(x)?(y)?", r"(a)?(b)?(c)?(d)?", r"(a)?(b)?(c)?(d)?(e)?(f)?"])
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
    assert parse_match(match) == ("A" + "_" * (MAX_ENTITY_TYPE_LENGTH - 1), 999_999_999, None)


def test_longest_constructible_match_with_a_variant_is_101_characters() -> None:
    longest_type = "A" + "\\_" * (MAX_ENTITY_TYPE_LENGTH - 1)
    text = "\\[" + "\t\t" + longest_type + "\\_" + "9" * 9 + "\\_" + "z" + "\t\t" + "\\]"
    match = TOLERANT.fullmatch(text)
    assert match is not None
    assert len(match.group(0)) == 101 <= MAX_TOLERANT_LENGTH
    assert parse_match(match) == ("A" + "_" * (MAX_ENTITY_TYPE_LENGTH - 1), 999_999_999, "Z")


@pytest.mark.parametrize(
    "text",
    [
        "\\[" + "A" * (MAX_ENTITY_TYPE_LENGTH + 1) + "_1\\]",
        "[" + "A" * (MAX_ENTITY_TYPE_LENGTH + 1) + "_1]",
        "[PERSON_" + "9" * 10 + "]",
        "[\t\t\tPERSON_1]",
        "[PERSON_1\t\t\t]",
        "[PERSON_1_BB]",
        "\\[PERSON_1\\_BB\\]",
    ],
    ids=[
        "escaped-41-letter-type",
        "41-letter-type",
        "ten-digits",
        "three-tabs-before",
        "three-tabs-after",
        "two-letter-variant",
        "escaped-two-letter-variant",
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
_VARIANT_PART = st.tuples(_SEPARATOR, st.text(alphabet="AbZz1É", max_size=3)).map("".join)
_PLACEHOLDER_LIKE = st.tuples(
    _OPEN, _PAD, _TYPE_PART, _SEPARATOR, _DIGITS, st.just("") | _VARIANT_PART, _PAD, _CLOSE
).map("".join)
_CANDIDATES = st.lists(
    _PLACEHOLDER_LIKE | st.text(alphabet="[]\\_ \tAbz9x", max_size=20), max_size=6
).map("".join)


@given(_CANDIDATES)
def test_no_match_is_longer_than_max_tolerant_length(text: str) -> None:
    for match in TOLERANT.finditer(text):
        assert len(match.group(0)) <= MAX_TOLERANT_LENGTH


def _parse_v010(match: re.Match[str]) -> tuple[str, int]:
    type_text, number_text = match.group(1, 2) if match.group(1) else match.group(3, 4)
    return type_text.replace("\\", "").upper(), int(number_text)


@settings(max_examples=500)
@given(_CANDIDATES)
def test_every_match_of_the_v010_pattern_is_still_found_without_a_variant(text: str) -> None:
    found = {(match.start(), match.end()): match for match in TOLERANT.finditer(text)}
    for old in V010_TOLERANT.finditer(text):
        match = found.get((old.start(), old.end()))
        assert match is not None
        assert parse_match(match) == (*_parse_v010(old), None)


@given(ENTITY_TYPES, st.integers(min_value=1, max_value=10**6), VARIANTS)
def test_parse_match_inverts_format_placeholder(
    entity_type: str, number: int, variant: str | None
) -> None:
    assert _parse(format_placeholder(entity_type, number, variant)) == (
        entity_type,
        number,
        variant,
    )


@given(ENTITY_TYPES, st.integers(min_value=1, max_value=10**21), VARIANTS)
def test_format_placeholder_output_is_canonical_or_rejected(
    entity_type: str, number: int, variant: str | None
) -> None:
    suffix = "" if variant is None else f"_{variant}"
    try:
        placeholder = format_placeholder(entity_type, number, variant)
    except ValueError:
        assert len(f"[{entity_type}_{number}{suffix}]") > MAX_PLACEHOLDER_LENGTH
        return
    assert CANONICAL.fullmatch(placeholder) is not None
    assert len(placeholder) <= MAX_PLACEHOLDER_LENGTH
