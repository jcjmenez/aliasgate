import pytest

from aliasgate.names import PARTICLES, TITLES, name_tokens


@pytest.mark.parametrize(
    ("surface", "expected"),
    [
        ("Juan Pérez García", ("juan", "pérez", "garcía")),
        ("Dña. Lucía Romero", ("lucía", "romero")),
        ("María del Mar Rosa", ("maría", "mar", "rosa")),
        ("J. R. Smith", ("smith",)),
        ("Sr.", ()),
    ],
)
def test_the_examples_of_the_slice_give_their_tokens(
    surface: str, expected: tuple[str, ...]
) -> None:
    assert name_tokens(surface, min_length=3) == expected


@pytest.mark.parametrize("title", ["Sr.", "Sr", "sr.", "SR", "Ms", "ms.", "MS.", "Dr.", "DRA"])
def test_titles_are_dropped_with_or_without_the_dot_in_any_case(title: str) -> None:
    assert name_tokens(f"{title} Alice Johnson", min_length=3) == ("alice", "johnson")


@pytest.mark.parametrize("word", sorted(TITLES | PARTICLES))
def test_every_title_and_particle_is_dropped(word: str) -> None:
    assert name_tokens(f"Alice {word} Johnson", min_length=1) == ("alice", "johnson")
    assert name_tokens(word.capitalize(), min_length=1) == ()


def test_the_word_lists_hold_keys() -> None:
    for word in TITLES | PARTICLES:
        assert word == word.casefold().strip()
        assert " " not in word


@pytest.mark.parametrize("word", ["Delia", "Donald", "Sierra", "Mrsa", "Lady-Ann", "Vanessa"])
def test_a_word_that_merely_contains_a_title_or_particle_is_kept(word: str) -> None:
    assert name_tokens(f"{word} Johnson", min_length=3) == (word.casefold(), "johnson")


@pytest.mark.parametrize("initial", ["J.", "J", "j.", "É.", "Ñ"])
def test_initials_are_dropped_whatever_the_minimum(initial: str) -> None:
    assert name_tokens(f"Alice {initial} Johnson", min_length=1) == ("alice", "johnson")


def test_words_shorter_than_the_minimum_are_dropped() -> None:
    assert name_tokens("Li Wei Zhang", min_length=3) == ("wei", "zhang")
    assert name_tokens("Li Wei Zhang", min_length=2) == ("li", "wei", "zhang")
    assert name_tokens("Li Wei Zhang", min_length=4) == ("zhang",)


def test_punctuation_at_the_ends_is_stripped_and_inner_apostrophes_and_hyphens_kept() -> None:
    assert name_tokens("(Johnson), «Alice»", min_length=3) == ("johnson", "alice")
    assert name_tokens("Liam O'Connor", min_length=3) == ("liam", "o'connor")
    assert name_tokens("María-José Romero.", min_length=3) == ("maría-josé", "romero")
    assert name_tokens("¿Lucía? ¡Romero!", min_length=3) == ("lucía", "romero")


def test_a_word_of_punctuation_only_is_dropped() -> None:
    assert name_tokens("Alice -- Johnson ... ¿?", min_length=1) == ("alice", "johnson")


def test_keys_are_nfkc_and_casefolded() -> None:
    full_width = "".join(chr(0xFF21 + ord(char) - ord("A")) for char in "JOHNSON")
    assert name_tokens(f"Alice {full_width}", min_length=3) == (
        "alice",
        "johnson",
    )
    assert name_tokens("ALICE STRAßE", min_length=3) == ("alice", "strasse")


def test_an_empty_or_blank_surface_has_no_tokens() -> None:
    assert name_tokens("", min_length=3) == ()
    assert name_tokens(" \t\n", min_length=3) == ()
