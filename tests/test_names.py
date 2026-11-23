import pytest

from aliasgate.names import PARTICLES, TITLES, is_person, name_parts, name_tokens, name_words


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


# Slice 17: `and`, the English first name `Don`, name parts and persons.


def test_and_is_a_particle() -> None:
    assert name_tokens("Rhodes, Young and Charles", min_length=3) == ("rhodes", "young", "charles")
    assert "and" in PARTICLES


@pytest.mark.parametrize(
    ("surface", "expected"),
    [
        ("Don Draper", ("don", "draper")),
        ("DON DRAPER", ("don", "draper")),
        ("Mr. Don Draper", ("don", "draper")),
        ("Don J. Smith", ("don", "smith")),
        ("Don Juan Pérez", ("juan", "pérez")),
        ("don Pedro", ("pedro",)),
        ("Don.", ()),
        ("Don", ()),
        ("Don Juan", ("don", "juan")),
        ("don Juan", ("juan",)),
        ("Don. Draper", ("draper",)),
    ],
)
def test_don_is_a_first_name_only_capitalised_undotted_and_before_one_other_name_token(
    surface: str, expected: tuple[str, ...]
) -> None:
    assert name_tokens(surface, min_length=3) == expected


def test_don_keeps_its_written_form_and_respects_the_minimum_length() -> None:
    assert name_words("Don Draper", min_length=3) == (("don", "Don"), ("draper", "Draper"))
    assert name_tokens("Don Draper", min_length=4) == ("draper",)


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("María-José", (("maría", "María"), ("josé", "José"))),
        ("Smith-Li", (("smith", "Smith"),)),
        ("Jean-de", (("jean", "Jean"),)),
        ("Prof-Ann", (("ann", "Ann"),)),
        ("Dr.-Ann", (("ann", "Ann"),)),
        ("Fowler", ()),
        ("Anne--Marie", (("anne", "Anne"), ("marie", "Marie"))),
        ("Ana-del-Río", (("ana", "Ana"), ("río", "Río"))),
        ("Smith-and-Jones", (("smith", "Smith"), ("jones", "Jones"))),
    ],
)
def test_name_parts_are_the_long_enough_parts_that_are_not_titles_or_particles(
    word: str, expected: tuple[tuple[str, str], ...]
) -> None:
    assert name_parts(word, min_length=3) == expected


@pytest.mark.parametrize("hyphen", ["-", "\u2010", "\u2011"])
def test_name_parts_split_on_each_of_the_three_hyphens(hyphen: str) -> None:
    assert name_parts(f"Anne{hyphen}Marie", min_length=3) == (
        ("anne", "Anne"),
        ("marie", "Marie"),
    )


def test_name_parts_keys_are_normalised_and_the_minimum_length_applies() -> None:
    assert name_parts("ANNE-STRAßE", min_length=3) == (("anne", "ANNE"), ("strasse", "STRAßE"))
    assert name_parts("Li-Wei", min_length=3) == (("wei", "Wei"),)
    assert name_parts("Li-Wei", min_length=2) == (("li", "Li"), ("wei", "Wei"))


@pytest.mark.parametrize(
    "surface",
    [
        "Dr. Smith",
        "Mr Smith",
        "J. Smith",
        "J Smith",
        "Sr. Pérez",
        "Anne-Marie",
        "Alice Johnson",
        "Don Draper",
        "J. R. Holloway",
    ],
)
def test_a_name_with_two_tokens_a_title_an_initial_or_two_parts_is_a_person(surface: str) -> None:
    assert is_person(surface, min_length=3)


@pytest.mark.parametrize(
    "surface",
    [
        "El cliente",
        "de la Rosa",
        "Smith",
        "Smith-Li",
        "Don",
        "Don.",
        "Dr.",
        "",
        "Mr. Don",
        "Li Wei",
    ],
)
def test_a_name_with_one_token_and_neither_title_initial_nor_two_parts_is_not_a_person(
    surface: str,
) -> None:
    assert not is_person(surface, min_length=3)


def test_a_don_that_is_a_first_name_is_not_also_a_title() -> None:
    # "Don" is a name token here, so the surface has two of them and no title.
    assert is_person("Don Draper", min_length=3)
    # With a minimum of four, "Don" is too short to be a name token, so it counts as a title.
    assert is_person("Don Draper", min_length=4)
    assert is_person("Don Juan Pérez", min_length=3)
