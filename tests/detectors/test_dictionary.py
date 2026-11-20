import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aliasgate.detectors.dictionary import MAX_ALIASES, DictionaryDetector, Term
from aliasgate.mapping import normalize_key
from aliasgate.types import PRIORITY_DICTIONARY, Span


def _found(detector: DictionaryDetector, text: str) -> list[tuple[str, str]]:
    return [(text[span.start : span.end], span.entity_type) for span in detector.detect(text)]


ACME = DictionaryDetector([Term("ACME Corporation", "ORG")])


def test_term_is_found_case_insensitively_and_across_a_line_break() -> None:
    text = "We met acme\ncorporation and ACME  CORPORATION."
    assert ACME.detect(text) == [
        Span(7, 23, "ORG", 1.0, "dictionary", PRIORITY_DICTIONARY),
        Span(28, 45, "ORG", 1.0, "dictionary", PRIORITY_DICTIONARY),
    ]


@pytest.mark.parametrize("text", ["ACMEX", "MACME", "ACME_1", "1ACME"])
def test_only_whole_terms_match(text: str) -> None:
    assert _found(DictionaryDetector([Term("ACME", "ORG")]), text) == []


@pytest.mark.parametrize("text", ["(ACME)", "ACME.", "ACME, Inc", "-acme-"])
def test_terms_match_next_to_punctuation(text: str) -> None:
    assert [
        surface.upper() for surface, _ in _found(DictionaryDetector([Term("ACME", "ORG")]), text)
    ] == ["ACME"]


def test_longest_term_wins() -> None:
    detector = DictionaryDetector([Term("ACME", "ORG"), Term("ACME Corporation", "ORG")])
    assert _found(detector, "ACME Corporation and ACME") == [
        ("ACME Corporation", "ORG"),
        ("ACME", "ORG"),
    ]


def test_regex_metacharacters_are_matched_literally() -> None:
    detector = DictionaryDetector([Term("C++ Team (EU)", "ORG")])
    assert _found(detector, "Ask the C++ Team (EU) now") == [("C++ Team (EU)", "ORG")]
    assert _found(detector, "Ask the CCC Team EU now") == []


def test_custom_entity_type_is_reported_as_given() -> None:
    detector = DictionaryDetector([Term("Project Falcon", "PROJECT")])
    assert _found(detector, "Status of project falcon?") == [("project falcon", "PROJECT")]


def test_term_repr_does_not_reveal_the_term() -> None:
    rendered = repr(Term("Globex S.L.", "ORG"))
    assert "Globex" not in rendered
    assert "ORG" in rendered


def test_zero_terms_yield_no_spans() -> None:
    assert DictionaryDetector([]).detect("ACME Corporation") == []


def test_duplicate_terms_with_the_same_type_are_deduplicated() -> None:
    detector = DictionaryDetector(
        [Term("ACME Corporation", "ORG"), Term("acme  corporation", "ORG")]
    )
    assert _found(detector, "ACME Corporation") == [("ACME Corporation", "ORG")]


def test_duplicate_terms_with_different_types_raise_naming_positions_only() -> None:
    terms = [
        Term("Globex S.L.", "ORG"),
        Term("Alice Johnson", "PERSON"),
        Term("GLOBEX S.L.", "PERSON"),
    ]
    with pytest.raises(ValueError, match="1 and 3") as excinfo:
        DictionaryDetector(terms)
    assert "globex" not in str(excinfo.value).lower()


@pytest.mark.parametrize("term", ["", "   ", "\n\t"])
def test_term_with_an_empty_key_raises_naming_its_position(term: str) -> None:
    with pytest.raises(ValueError, match="term 2"):
        DictionaryDetector([Term("ACME", "ORG"), Term(term, "ORG")])


def test_invalid_entity_type_raises_naming_its_position() -> None:
    with pytest.raises(ValueError, match="term 1") as excinfo:
        DictionaryDetector([Term("Globex S.L.", "org")])
    assert "Globex" not in str(excinfo.value)


def test_dotted_and_dotless_i_variants_are_typed_like_their_term() -> None:
    # re.IGNORECASE matches "I" with U+0130 and U+0131, but normalize_key does not fold them to
    # "i", so the key lookup misses and the detector must still report the term's type.
    # "Alba Romero" has the same token lengths as "Ivan Petrov" and comes first in the
    # alternation, so the fallback has to skip it.
    detector = DictionaryDetector(
        [Term("Ivan Petrov", "PERSON"), Term("Alba Romero", "ORG"), Term("Iris Tech", "ORG")]
    )
    text = "\u0130van Petrov met \u0131ris tech"
    assert _found(detector, text) == [("\u0130van Petrov", "PERSON"), ("\u0131ris tech", "ORG")]


# Aliases (slice 13) ----------------------------------------------------------------------------


def _linked(detector: DictionaryDetector, text: str) -> list[tuple[str, str, str | None]]:
    return [
        (text[span.start : span.end], span.entity_type, span.link) for span in detector.detect(text)
    ]


ALIASED = DictionaryDetector(
    [
        Term("ACME Corporation", "ORG", ("ACME", "ACME Corp.")),
        Term("Globex S.L.", "ORG", ("Globex",)),
        Term("Project Falcon", "PROJECT"),
    ]
)


def test_an_alias_is_detected_with_its_entrys_type_and_link() -> None:
    text = "ACME Corporation, ACME and ACME Corp. met Globex S.L. and Globex on Project Falcon."
    assert _linked(ALIASED, text) == [
        ("ACME Corporation", "ORG", "dictionary:1"),
        ("ACME", "ORG", "dictionary:1"),
        ("ACME Corp.", "ORG", "dictionary:1"),
        ("Globex S.L.", "ORG", "dictionary:2"),
        ("Globex", "ORG", "dictionary:2"),
        ("Project Falcon", "PROJECT", None),
    ]


def test_spans_of_an_aliased_entry_keep_score_source_and_priority() -> None:
    assert ALIASED.detect("ACME") == [
        Span(0, 4, "ORG", 1.0, "dictionary", PRIORITY_DICTIONARY, "dictionary:1")
    ]


def test_an_entry_without_aliases_gives_spans_without_a_link() -> None:
    detector = DictionaryDetector([Term("Globex S.L.", "ORG"), Term("ACME", "ORG", ("ACME Inc",))])
    assert _linked(detector, "Globex S.L. and ACME") == [
        ("Globex S.L.", "ORG", None),
        ("ACME", "ORG", "dictionary:2"),
    ]


def test_at_one_position_the_longest_of_the_term_and_its_aliases_wins() -> None:
    detector = DictionaryDetector([Term("ACME", "ORG", ("ACME Corporation Europe",))])
    assert _linked(detector, "ACME Corporation Europe and ACME Corporation") == [
        ("ACME Corporation Europe", "ORG", "dictionary:1"),
        ("ACME", "ORG", "dictionary:1"),
    ]


def test_an_alias_is_matched_ignoring_case_with_any_whitespace_and_as_a_whole_term() -> None:
    assert _linked(ALIASED, "acme\ncorp. and acme corp and ACMEs and Globexes") == [
        ("acme\ncorp.", "ORG", "dictionary:1"),
        ("acme", "ORG", "dictionary:1"),
    ]


def test_an_alias_of_a_later_entry_is_linked_to_that_entry() -> None:
    detector = DictionaryDetector(
        [Term("Project Falcon", "PROJECT"), Term("Globex S.L.", "ORG", ("Globex",))]
    )
    assert _linked(detector, "Globex") == [("Globex", "ORG", "dictionary:2")]


def test_a_dotted_capital_i_in_an_alias_is_typed_and_linked_like_its_entry() -> None:
    # As for terms: the key lookup misses, and "Alba R" has the same token lengths as "Ivan P"
    # and comes first in the alternation, so the fallback has to skip it.
    detector = DictionaryDetector(
        [Term("Alba Romero", "ORG", ("Alba R",)), Term("Ivan Petrov", "PERSON", ("Ivan P",))]
    )
    assert _linked(detector, "\u0130van P") == [("\u0130van P", "PERSON", "dictionary:2")]


TERMS = (("ACME Corporation", "ORG"), ("Globex S.L.", "ORG"), ("Project Falcon", "PROJECT"))
SURFACES = ("ACME Corporation", "ACME", "Globex S.L.", "Globex", "Project Falcon", "Falcon")


@pytest.mark.parametrize(
    ("terms", "message"),
    [
        (
            [Term("ACME Corporation", "ORG", ("ACME", " \t "))],
            "term 1, alias 2: the alias is empty",
        ),
        ([Term("ACME Corporation", "ORG", ("",))], "term 1, alias 1: the alias is empty"),
        (
            [Term("Globex S.L.", "ORG"), Term("ACME", "ORG", ("ACME Corp.", "acme"))],
            "term 2, alias 2 repeats the term",
        ),
        (
            [Term("ACME Corporation", "ORG", ("ACME", "Falcon", "acme"))],
            "term 1, alias 3 repeats alias 1",
        ),
        (
            [Term("ACME Corporation", "ORG", ("ACME",)), Term("Globex", "ORG", ("Acme",))],
            "terms 1 and 2 share a term or alias",
        ),
        (
            [Term("ACME Corporation", "ORG", ("ACME",)), Term("acme", "ORG")],
            "terms 1 and 2 share a term or alias",
        ),
        (
            [Term("ACME", "ORG"), Term("ACME Corporation", "ORG", ("ACME",))],
            "terms 1 and 2 share a term or alias",
        ),
        (
            [
                Term("Project Falcon", "PROJECT"),
                Term("Globex S.L.", "ORG", ("Globex",)),
                Term("Globex S.L.", "ORG", ("Globex",)),
            ],
            "terms 2 and 3 share a term or alias",
        ),
        (
            [Term("Globex S.L.", "ORG"), Term("Globex  S.L.", "ORG", ("Globex",))],
            "terms 1 and 2 share a term or alias",
        ),
        (
            [Term("ACME", "ORG", ("ACME Corp.",)), Term("ACME", "PROJECT")],
            "terms 1 and 2 are the same term with different entity types",
        ),
    ],
    ids=[
        "whitespace-alias",
        "empty-alias",
        "alias-repeats-term",
        "alias-repeats-alias",
        "alias-in-two-entries",
        "alias-is-another-term",
        "term-is-another-alias",
        "duplicate-entry-with-aliases",
        "duplicate-term-one-side-with-aliases",
        "same-term-different-types",
    ],
)
def test_alias_errors_name_positions_and_never_a_term_or_alias(
    terms: list[Term], message: str
) -> None:
    with pytest.raises(ValueError, match=re.escape(message)) as excinfo:
        DictionaryDetector(terms)
    assert str(excinfo.value) == message
    surfaces = {surface for term in terms for surface in (term.term, *term.aliases)}
    for surface in surfaces | set(SURFACES):
        if surface.strip():
            assert surface.lower() not in str(excinfo.value).lower()


def test_25_aliases_are_accepted_and_26_are_rejected() -> None:
    assert MAX_ALIASES == 25
    aliases = tuple(f"Falcon {index}" for index in range(MAX_ALIASES + 1))
    detector = DictionaryDetector([Term("Project Falcon", "PROJECT", aliases[:MAX_ALIASES])])
    assert len(detector.detect(" ".join(aliases[:MAX_ALIASES]) + " Project Falcon")) == 26
    with pytest.raises(ValueError, match="more than 25 aliases") as excinfo:
        DictionaryDetector([Term("ACME", "ORG"), Term("Project Falcon", "PROJECT", aliases)])
    assert str(excinfo.value) == "term 2 has more than 25 aliases"


def test_a_duplicate_entry_without_aliases_is_still_ignored() -> None:
    detector = DictionaryDetector(
        [
            Term("Globex S.L.", "ORG"),
            Term("ACME", "ORG", ("ACME Corp.",)),
            Term("globex s.l.", "ORG"),
        ]
    )
    assert _linked(detector, "Globex S.L.") == [("Globex S.L.", "ORG", None)]


def test_term_repr_shows_neither_the_term_nor_the_aliases() -> None:
    rendered = repr(Term("Globex S.L.", "ORG", ("Globex", "GSL Holdings")))
    assert "Globex" not in rendered
    assert "GSL" not in rendered
    assert "ORG" in rendered


def test_term_aliases_default_to_an_empty_tuple() -> None:
    assert Term("ACME", "ORG").aliases == ()


_WORDS = ["ACME", "Corporation", "Globex", "S.L.", "Falcon", "C++", "Team"]
_TERMS = st.lists(
    st.lists(st.sampled_from(_WORDS), min_size=1, max_size=3).map(" ".join),
    min_size=1,
    max_size=6,
    unique_by=normalize_key,
)
_FILLER = st.sampled_from([" ", "\n", ", ", "x", "-", "(", ")", "  "])


_PIECES = st.lists(
    st.tuples(st.one_of(_FILLER, st.sampled_from(_WORDS)), st.booleans()), max_size=20
)


@given(_TERMS, _PIECES)
def test_spans_lie_within_the_text_and_cover_a_term(
    terms: list[str], pieces: list[tuple[str, bool]]
) -> None:
    detector = DictionaryDetector([Term(term, "ORG") for term in terms])
    text = "".join(piece.upper() if upper else piece for piece, upper in pieces)
    text += " " + " ".join(terms).lower()
    keys = {normalize_key(term) for term in terms}
    spans = detector.detect(text)
    assert spans
    for span in spans:
        assert 0 <= span.start < span.end <= len(text)
        assert normalize_key(text[span.start : span.end]) in keys
