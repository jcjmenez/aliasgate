"""Organisation words (slice 19): the words of an ORG surface and the name without its legal
form."""

import pytest
from aliasgate.orgs import (
    CONNECTORS,
    GENERIC_WORDS,
    LEGAL_FORMS,
    OrgWord,
    OrgWordKind,
    bare_name,
    org_words,
)

import generate_org_corpus
from aliasgate.common_words import COMMON_WORDS
from aliasgate.mapping import normalize_key
from aliasgate.names import PARTICLES

D, G, C, L, X = (  # X: other
    OrgWordKind.DISTINCTIVE,
    OrgWordKind.GENERIC,
    OrgWordKind.COMMON,
    OrgWordKind.LEGAL,
    OrgWordKind.OTHER,
)


def _kinds(surface: str, min_length: int = 3) -> list[tuple[str, OrgWordKind]]:
    return [(word.key, word.kind) for word in org_words(surface, min_length=min_length)]


# The examples of the slice file --------------------------------------------------------------


@pytest.mark.parametrize(
    ("surface", "kinds", "bare"),
    [
        (
            "Brightwater Logistics Ltd",
            [("brightwater", D), ("logistics", G), ("ltd", L)],
            "Brightwater Logistics",
        ),
        (
            "Transportes Ventisquero S.L.",
            [("transportes", G), ("ventisquero", D), ("s.l", L)],
            "Transportes Ventisquero",
        ),
        (
            "Construcciones Albarrán y Ledesma S.A.",
            [("construcciones", G), ("albarrán", D), ("y", X), ("ledesma", D), ("s.a", L)],
            "Construcciones Albarrán y Ledesma",
        ),
        (
            "Rhodes, Young and Charles",
            [("rhodes", D), ("young", C), ("and", X), ("charles", D)],
            "Rhodes, Young and Charles",
        ),
        ("The Brightwater Company", [("the", X), ("brightwater", D), ("company", L)], None),
        ("Inversiones EY S.Coop.", [("inversiones", G), ("ey", X), ("s.coop", L)], None),
        ("ACME Corporation", [("acme", D), ("corporation", L)], None),
        ("Globex S.L.", [("globex", D), ("s.l", L)], None),
        (
            "(Brightwater Logistics) Ltd",
            [("brightwater", D), ("logistics", G), ("ltd", L)],
            "Brightwater Logistics",
        ),
    ],
)
def test_the_examples_of_the_slice_file(
    surface: str, kinds: list[tuple[str, OrgWordKind]], bare: str | None
) -> None:
    assert _kinds(surface) == kinds
    assert bare_name(surface, min_length=3) == bare


def test_each_word_keeps_its_written_form_without_the_punctuation_at_its_ends() -> None:
    assert org_words("(Brightwater, Logistics) S.L.", min_length=3) == (
        OrgWord("brightwater", "Brightwater", D),
        OrgWord("logistics", "Logistics", G),
        OrgWord("s.l", "S.L", L),
    )
    assert org_words("O'Brien-Hale Ltd", min_length=3)[0] == OrgWord(
        "o'brien-hale", "O'Brien-Hale", D
    )


def test_a_bare_name_keeps_the_text_between_its_first_and_last_content_words_as_written() -> None:
    assert bare_name("Hale,  Mercer\tand Quill LLP", min_length=3) == "Hale,  Mercer\tand Quill"
    assert bare_name('"Grupo Peñalver Norte" S.A.', min_length=3) == "Grupo Peñalver Norte"
    assert bare_name("Grupo Peñalver", min_length=3) == "Grupo Peñalver"
    assert bare_name("The Ltd", min_length=3) is None
    assert bare_name("", min_length=3) is None


# Legal forms, connectors and lists -----------------------------------------------------------


@pytest.mark.parametrize(
    "word",
    ["S.L.", "S.L", "SL", "s.l.", "LTD", "Inc.", "S.Coop.", "S.L.N.E", "Cía.", "Co.", "GmbH"],
)
def test_legal_forms_count_with_and_without_dots_in_any_case(word: str) -> None:
    ((_, kind),) = _kinds(word)
    assert kind is L


def test_every_key_of_the_lists_gets_its_kind() -> None:
    for key in LEGAL_FORMS:
        assert _kinds(key) == [(key, L)], key
    for key in CONNECTORS:
        assert _kinds(key) == [(key, X)], key
    for key in GENERIC_WORDS:
        assert _kinds(key) == [(key, G)], key
    for key in sorted(COMMON_WORDS - CONNECTORS - LEGAL_FORMS):
        if len(key) >= 3:
            assert _kinds(key) == [(key, C)], key


@pytest.mark.parametrize("word", ["Grupon", "Incas", "Solutionsx", "Ltda2", "Slu-Co"])
def test_a_word_that_merely_contains_a_listed_word_is_distinctive(word: str) -> None:
    ((_, kind),) = _kinds(word)
    assert kind is D


def test_words_shorter_than_the_minimum_are_other() -> None:
    assert _kinds("Inversiones EY S.Coop.") == [("inversiones", G), ("ey", X), ("s.coop", L)]
    assert _kinds("Pitts Freight", min_length=6) == [("pitts", X), ("freight", G)]
    assert _kinds("Pitts Freight", min_length=5) == [("pitts", D), ("freight", G)]
    # A legal form is one whatever its length.
    assert _kinds("Pitts AG", min_length=5) == [("pitts", D), ("ag", L)]
    assert bare_name("Pitts Freight Ltd", min_length=6) is None


def test_keys_are_nfkc_and_case_folded() -> None:
    fullwidth = "".join(chr(ord(letter) - ord("A") + 0xFF21) for letter in "BRIGHTWATER")
    assert _kinds(f"{fullwidth} LOGISTICS") == [("brightwater", D), ("logistics", G)]
    assert org_words(fullwidth, min_length=3)[0].word == fullwidth


def test_an_empty_word_is_other() -> None:
    assert _kinds("Brightwater -- Ltd") == [("brightwater", D), ("", X), ("ltd", L)]


def test_the_lists_hold_what_the_slice_file_says() -> None:
    assert len(LEGAL_FORMS) == 34
    assert len(GENERIC_WORDS) == 210
    assert {"of", "the", "for", "und", "et"} | PARTICLES == CONNECTORS
    assert all("." not in key for key in LEGAL_FORMS)
    for key in GENERIC_WORDS:
        assert key == normalize_key(key), key
        assert key.split() == [key], key
        assert key not in COMMON_WORDS | LEGAL_FORMS | CONNECTORS, key
    for key in LEGAL_FORMS | CONNECTORS:
        assert key == normalize_key(key), key
    assert {"global", "hotel"} <= GENERIC_WORDS


def test_the_generic_words_hold_every_generic_word_of_the_organisation_corpus() -> None:
    for words in generate_org_corpus.GENERIC_WORDS.values():
        assert {normalize_key(word) for word in words} <= GENERIC_WORDS
    for forms in generate_org_corpus.LEGAL_FORMS.values():
        assert {normalize_key(form).replace(".", "") for form in forms} <= LEGAL_FORMS
