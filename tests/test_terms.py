import pytest
from hypothesis import given
from hypothesis import strategies as st

from aliasgate.terms import build_term_pattern


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
