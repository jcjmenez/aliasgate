import pytest
from hypothesis import given
from hypothesis import strategies as st

from aliasgate.terms import TERM_END, TERM_START, build_term_pattern


@pytest.mark.parametrize("text", ["Juan Pérez", "JUAN  PÉREZ", "juan\npérez", "Juan\t \nPérez"])
def test_pattern_matches_case_and_whitespace_variants(text: str) -> None:
    assert build_term_pattern("Juan Pérez").fullmatch(text) is not None


@pytest.mark.parametrize("text", ["xJuan Pérez", "Juan Pérezx", "Juan Pérez2", "_Juan Pérez"])
def test_pattern_does_not_match_inside_a_longer_word(text: str) -> None:
    assert build_term_pattern("Juan Pérez").search(text) is None


@pytest.mark.parametrize(
    ("text", "expected_start"),
    [("(Juan Pérez)", 1), ("Juan Pérez.", 0), ("Dear Juan Pérez, hello", 5), ("-juan pérez-", 1)],
)
def test_pattern_matches_next_to_punctuation(text: str, expected_start: int) -> None:
    match = build_term_pattern("Juan Pérez").search(text)
    assert match is not None
    assert match.start() == expected_start
    assert match.group(0).casefold() == "juan pérez"


def test_surface_with_regex_metacharacters_is_matched_literally() -> None:
    pattern = build_term_pattern("A+B (C)")
    assert pattern.search("see A+B (C) now") is not None
    assert pattern.search("see AAB C now") is None
    assert pattern.search("see AB (C) now") is None


def test_surrounding_whitespace_of_the_surface_is_ignored() -> None:
    assert build_term_pattern("  ACME Corporation\n").fullmatch("acme corporation") is not None


def test_single_token_surface_does_not_require_whitespace() -> None:
    pattern = build_term_pattern("ACME")
    assert pattern.fullmatch("acme") is not None
    assert pattern.search("ACMEs") is None


@pytest.mark.parametrize("surface", ["", " ", "\t\n", "\u00a0"])
def test_surface_without_non_whitespace_characters_raises(surface: str) -> None:
    with pytest.raises(ValueError, match="whitespace"):
        build_term_pattern(surface)


_SURFACES = st.text(alphabet=st.characters(codec="utf-8"), min_size=1, max_size=20).filter(
    lambda s: s.strip() != ""
)


@given(_SURFACES)
def test_pattern_always_matches_its_own_stripped_surface(surface: str) -> None:
    assert build_term_pattern(surface).fullmatch(surface.strip()) is not None


@given(st.lists(st.text(alphabet="abcÉé+.(", min_size=1, max_size=5), min_size=1, max_size=4))
def test_pattern_matches_any_whitespace_between_tokens(tokens: list[str]) -> None:
    pattern = build_term_pattern(" ".join(tokens))
    assert pattern.fullmatch("\n \t".join(tokens)) is not None


# Slice 17: a whole term is never followed by 't and the end of a word.


@pytest.mark.parametrize("text", ["Don't", "Don\u2019t", "DON'T", "don't go"])
def test_a_term_does_not_match_before_an_apostrophe_t_that_ends_the_word(text: str) -> None:
    assert build_term_pattern("Don").search(text) is None


@pytest.mark.parametrize("text", ["Don's", "Don,", "Don.", "Don 't", "Don'tcha"])
def test_a_term_still_matches_before_other_endings(text: str) -> None:
    match = build_term_pattern("Don").search(text)
    assert match is not None
    assert match.span() == (0, 3)


def test_the_boundary_applies_to_every_term_and_keeps_inner_apostrophes() -> None:
    assert build_term_pattern("Can").search("I can't") is None
    assert build_term_pattern("Won").search("it won\u2019t") is None
    assert build_term_pattern("O'Connor").fullmatch("O'Connor") is not None
    assert build_term_pattern("Juan Pérez").search("Juan Pérez't") is None


def test_the_shared_boundaries_are_the_ones_build_term_pattern_uses() -> None:
    pattern = build_term_pattern("Don")
    assert pattern.pattern == f"{TERM_START}(?:Don){TERM_END}"
    assert TERM_START == r"(?<!\w)"
    assert TERM_END == "(?!\\w|['\u2019]t(?!\\w))"
