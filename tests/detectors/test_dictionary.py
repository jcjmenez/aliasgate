import pytest
from hypothesis import given
from hypothesis import strategies as st

from aliasgate.detectors.dictionary import DictionaryDetector, Term
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
