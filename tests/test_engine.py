import re
import time
from collections import Counter

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aliasgate.common_words import COMMON_WORDS
from aliasgate.detectors.dictionary import DictionaryDetector, Term
from aliasgate.detectors.rules import DEFAULT_RULE_TYPES, RuleDetector
from aliasgate.engine import (
    DetectionError,
    PseudonymizedBatch,
    Pseudonymizer,
    count_unresolved,
    restore,
)
from aliasgate.eval.metrics import expected_restoration
from aliasgate.mapping import normalize_key
from aliasgate.names import is_person, name_parts, name_words
from aliasgate.terms import build_term_pattern
from aliasgate.types import (
    PRIORITY_DICTIONARY,
    PRIORITY_LITERAL,
    PRIORITY_ML,
    PRIORITY_PROPAGATION,
    Span,
)
from fakes import FailingDetector, WordListDetector
from oracles import propagation_oracle

WORDS = {"Alice": "PERSON", "Bob": "PERSON", "ACME": "ORG"}


def _engine(**kwargs: object) -> Pseudonymizer:
    return Pseudonymizer([WordListDetector(WORDS)], **kwargs)  # type: ignore[arg-type]


def _texts(texts: list[str], **kwargs: object) -> tuple[str, ...]:
    return _engine(**kwargs).pseudonymize(texts).texts


def _restored(batch: PseudonymizedBatch) -> list[str]:
    return [restore(text, batch.mapping) for text in batch.texts]


class _ScriptedDetector:
    def __init__(self, spans: list[object], name: str = "scripted") -> None:
        self.name = name
        self._spans = spans

    def detect(self, text: str) -> list[Span]:
        return self._spans  # type: ignore[return-value]


def _person(start: int, end: int) -> Span:
    return Span(start, end, "PERSON", 0.9, "scripted", PRIORITY_ML)


def test_detected_values_become_numbered_placeholders() -> None:
    assert _texts(["Alice met Bob at ACME."]) == ("[PERSON_1] met [PERSON_2] at [ORG_1].",)


def test_the_same_value_twice_in_one_text_gets_the_same_placeholder() -> None:
    assert _texts(["Alice called Alice."]) == ("[PERSON_1] called [PERSON_1].",)


def test_the_same_value_in_two_texts_gets_the_same_placeholder_numbered_in_batch_order() -> None:
    assert _texts(["Bob called.", "Alice called Bob."]) == (
        "[PERSON_1] called.",
        "[PERSON_2] called [PERSON_1].",
    )


def test_propagation_replaces_undetected_case_variants() -> None:
    assert _texts(["Alice called.", "alice called again."]) == (
        "[PERSON_1] called.",
        "[PERSON_1] called again.",
    )


def test_propagation_respects_word_boundaries() -> None:
    assert _texts(["Alice and Alicent", "ALICE_X, xalice"]) == (
        "[PERSON_1] and Alicent",
        "ALICE_X, xalice",
    )


def test_without_propagation_undetected_variants_stay() -> None:
    assert _texts(["Alice called.", "alice called again."], propagate=False) == (
        "[PERSON_1] called.",
        "alice called again.",
    )


def test_short_entities_are_replaced_where_detected_but_not_propagated() -> None:
    detector = WordListDetector({"Al": "PERSON"})
    assert Pseudonymizer([detector]).pseudonymize(["Al met al."]).texts == ("[PERSON_1] met al.",)
    assert Pseudonymizer([detector], min_propagation_length=2).pseudonymize(
        ["Al met al."]
    ).texts == ("[PERSON_1] met [PERSON_1].",)


@pytest.mark.parametrize("length", [0, -1])
def test_min_propagation_length_below_one_is_rejected(length: int) -> None:
    with pytest.raises(ValueError, match="min_propagation_length"):
        _engine(min_propagation_length=length)


def test_longest_canonical_surface_is_propagated_first() -> None:
    detector = WordListDetector({"Juan Pérez García": "PERSON", "Juan Pérez": "PERSON"})
    texts = ["Juan Pérez García firmó.", "Juan Pérez llamó.", "juan pérez garcía respondió."]
    assert Pseudonymizer([detector]).pseudonymize(texts).texts == (
        "[PERSON_1] firmó.",
        "[PERSON_2] llamó.",
        "[PERSON_1] respondió.",
    )


def test_longest_first_does_not_depend_on_numbering() -> None:
    detector = WordListDetector({"Juan Pérez García": "PERSON", "Juan Pérez": "PERSON"})
    texts = ["Juan Pérez llamó.", "Juan Pérez García firmó.", "juan  pérez\ngarcía respondió."]
    assert Pseudonymizer([detector]).pseudonymize(texts).texts == (
        "[PERSON_1] llamó.",
        "[PERSON_2] firmó.",
        "[PERSON_2] respondió.",
    )


def test_propagation_never_adds_entities_or_changes_numbering() -> None:
    texts = ["alice and bob", "Bob met Alice", "ALICE, BOB"]
    propagated = _engine().pseudonymize(texts)
    plain = _engine(propagate=False).pseudonymize(texts)
    assert propagated.mapping.entities() == plain.mapping.entities()
    assert [entity.placeholder for entity in plain.mapping.entities()] == [
        "[PERSON_1]",
        "[PERSON_2]",
    ]
    assert propagated.texts == (
        "[PERSON_2] and [PERSON_1]",
        "[PERSON_1] met [PERSON_2]",
        "[PERSON_2], [PERSON_1]",
    )


@pytest.mark.parametrize("allow_list", [["ACME"], ["  acme "], ["Acme", "ACME"]])
def test_allow_listed_terms_stay_in_place(allow_list: list[str]) -> None:
    assert _texts(["ACME and acme hired Alice."], allow_list=allow_list) == (
        "ACME and acme hired [PERSON_1].",
    )


def test_allow_list_never_drops_placeholder_literals() -> None:
    batch = _engine(allow_list=["[PERSON_1]", "[person_1]"]).pseudonymize(["[PERSON_1]"])
    assert batch.texts == ("[LITERAL_1]",)


def test_placeholder_literal_in_the_input_is_pseudonymised_and_restored() -> None:
    batch = _engine().pseudonymize(["Use [PERSON_1] for Alice"])
    assert batch.texts == ("Use [LITERAL_1] for [PERSON_1]",)
    assert _restored(batch) == ["Use [PERSON_1] for Alice"]


def test_literals_are_exact_and_restore_byte_for_byte() -> None:
    texts = ["[PERSON_1] and [person_1]", "\\[org 2\\] then [ ORG_2 ]"]
    batch = _engine().pseudonymize(texts)
    assert batch.texts == ("[LITERAL_1] and [LITERAL_2]", "[LITERAL_3] then [LITERAL_4]")
    assert _restored(batch) == texts


def test_literals_are_not_propagated() -> None:
    texts = ["[ PERSON_1 ]", "[\nPERSON_1\n]"]
    batch = _engine().pseudonymize(texts)
    assert batch.texts == ("[LITERAL_1]", "[\nPERSON_1\n]")


def test_propagation_does_not_reach_into_literals() -> None:
    batch = _engine().pseudonymize(["Alice", "[alice 1] and alice"])
    assert batch.texts == ("[PERSON_1]", "[LITERAL_1] and [PERSON_1]")
    assert _restored(batch) == ["Alice", "[alice 1] and Alice"]


@pytest.mark.parametrize(
    "text", ["\\Alice", "\\Alice\\", "\\[Alice\\]", "[Alice]", "\\\\[PERSON_1\\]"]
)
def test_backslashes_and_brackets_around_values_round_trip(text: str) -> None:
    batch = _engine().pseudonymize([text])
    assert _restored(batch) == [text]


def test_first_occurrence_decides_the_entity_type() -> None:
    detectors = [
        WordListDetector({"ACME": "ORG"}),
        WordListDetector({"Acme": "PERSON"}, name="other"),
    ]
    batch = Pseudonymizer(detectors).pseudonymize(["ACME", "Acme"])
    assert batch.texts == ("[ORG_1]", "[ORG_1]")
    assert _restored(batch) == ["ACME", "ACME"]


def test_whitespace_only_spans_are_dropped() -> None:
    detector = _ScriptedDetector([_person(5, 8)])
    assert Pseudonymizer([detector]).pseudonymize(["Alice   Bob"]).texts == ("Alice   Bob",)


def test_failing_detector_raises_detection_error_without_text() -> None:
    engine = Pseudonymizer([WordListDetector(WORDS), FailingDetector()])
    with pytest.raises(DetectionError) as excinfo:
        engine.pseudonymize(["Alice Johnson lives at juan.perez@example.com"])
    message = str(excinfo.value)
    assert "failing" in message
    assert "Alice" not in message
    assert "example.com" not in message
    assert excinfo.value.detector_name == "failing"
    assert isinstance(excinfo.value.__cause__, RuntimeError)


@pytest.mark.parametrize(
    "spans",
    [[_person(0, 99)], ["not a span"], [None]],
    ids=["out-of-range", "not-a-span", "none"],
)
def test_detector_returning_an_invalid_span_raises_detection_error(spans: list[object]) -> None:
    with pytest.raises(DetectionError, match="scripted") as excinfo:
        Pseudonymizer([_ScriptedDetector(spans)]).pseudonymize(["Alice"])
    assert "Alice" not in str(excinfo.value)


def test_empty_batch_and_empty_strings() -> None:
    empty = _engine().pseudonymize([])
    assert empty.texts == ()
    assert len(empty.mapping) == 0
    assert _texts(["", "Alice", ""]) == ("", "[PERSON_1]", "")


def test_pseudonymize_is_deterministic() -> None:
    texts = ["Alice met Bob at ACME.", "acme, alice, [PERSON_1]"]
    first, second = _engine().pseudonymize(texts), _engine().pseudonymize(texts)
    assert first.texts == second.texts
    assert first.mapping.entities() == second.mapping.entities()


def test_detectors_may_be_any_sequence_and_texts_any_sequence() -> None:
    engine = Pseudonymizer((WordListDetector(WORDS),))
    assert engine.pseudonymize(("Alice",)).texts == ("[PERSON_1]",)


# Property tests -------------------------------------------------------------------------------

_TOKENS = st.sampled_from(
    [
        "Alice", "Bob", "ACME", "alice", "ALICE", "bob", "acme", "Acme",
        "[", "]", "\\", "_", " ", "  ", "\n", "\t", "1", "0", "9",
        "PERSON", "person", "ORG", "LITERAL", "x", ".", "-",
    ]
)  # fmt: skip
_TEXT = st.lists(_TOKENS, max_size=14).map("".join)
_BATCH = st.lists(_TEXT, max_size=4)
_PROPERTY = settings(max_examples=300, deadline=None)


@_PROPERTY
@given(_BATCH)
def test_round_trip_without_propagation_restores_the_exact_input(texts: list[str]) -> None:
    batch = _engine(propagate=False).pseudonymize(texts)
    assert _restored(batch) == texts


@_PROPERTY
@given(_BATCH)
def test_round_trip_with_propagation_renders_variants_as_the_canonical_surface(
    texts: list[str],
) -> None:
    batch = _engine().pseudonymize(texts)
    assert _restored(batch) == propagation_oracle(texts, batch)


@_PROPERTY
@given(_BATCH)
def test_no_detected_surface_remains_in_the_output(texts: list[str]) -> None:
    batch = _engine().pseudonymize(texts)
    for entity in batch.mapping.entities():
        if entity.entity_type != "LITERAL" and len(entity.key) >= 3:
            pattern = build_term_pattern(entity.surface)
            assert not any(pattern.search(text) for text in batch.texts)


@_PROPERTY
@given(_BATCH, _BATCH)
def test_appending_texts_keeps_earlier_entities_and_texts(
    first: list[str], appended: list[str]
) -> None:
    short = _engine(propagate=False).pseudonymize(first)
    long = _engine(propagate=False).pseudonymize(first + appended)
    assert long.texts[: len(first)] == short.texts
    for entity in short.mapping.entities():
        assert long.mapping.get(entity.key) == entity
    with_propagation = _engine().pseudonymize(first + appended)
    for entity in _engine().pseudonymize(first).mapping.entities():
        assert with_propagation.mapping.get(entity.key) == entity


def test_stats_count_replaced_occurrences_by_entity_type_and_source() -> None:
    engine = Pseudonymizer([WordListDetector({"Alice": "PERSON"}, name="words")])
    batch = engine.pseudonymize(["Alice met alice.", "ALICE and [PERSON_1]", "nobody"])
    assert dict(batch.stats.entities_by_type_and_source) == {
        ("PERSON", "words"): 1,
        ("PERSON", "propagation"): 2,
        ("LITERAL", "literal"): 1,
    }


def test_stats_use_the_entity_type_of_the_first_occurrence() -> None:
    first = WordListDetector({"Alice": "PERSON"}, name="first")
    second = WordListDetector({"ALICE": "ORG"}, name="second")
    batch = Pseudonymizer([first, second], propagate=False).pseudonymize(["Alice", "x ALICE"])
    assert batch.texts == ("[PERSON_1]", "x [PERSON_1]")
    assert dict(batch.stats.entities_by_type_and_source) == {
        ("PERSON", "first"): 1,
        ("PERSON", "second"): 1,
    }


def test_stats_time_every_detector_including_the_literal_one() -> None:
    engine = Pseudonymizer(
        [WordListDetector(WORDS, name="words"), WordListDetector({}, name="empty")]
    )
    seconds = engine.pseudonymize(["Alice", "Bob"]).stats.detector_seconds
    assert set(seconds) == {"literal", "words", "empty"}
    assert all(value >= 0.0 for value in seconds.values())


def test_stats_of_an_empty_batch_are_empty_counts() -> None:
    batch = _engine().pseudonymize([])
    assert dict(batch.stats.entities_by_type_and_source) == {}
    assert set(batch.stats.detector_seconds) == {"literal", "fake"}


def test_the_detectors_are_exposed_in_the_order_they_run() -> None:
    words = WordListDetector(WORDS)
    detectors = Pseudonymizer([words]).detectors
    assert [detector.name for detector in detectors] == ["literal", "fake"]
    assert detectors[1] is words


# analyze (slice 11) ---------------------------------------------------------------------------


def test_analyze_returns_the_final_spans_of_each_text_without_replacing() -> None:
    engine = Pseudonymizer([WordListDetector({"Alice": "PERSON", "ACME": "ORG"}, name="words")])
    assert engine.analyze(["Alice joined ACME.", "alice left"]) == [
        [
            Span(0, 5, "PERSON", 0.9, "words", PRIORITY_ML),
            Span(13, 17, "ORG", 0.9, "words", PRIORITY_ML),
        ],
        [Span(0, 5, "PERSON", 1.0, "propagation", PRIORITY_PROPAGATION)],
    ]


def test_analyze_without_propagation_reports_only_detected_spans() -> None:
    assert _engine(propagate=False).analyze(["Alice and alice"]) == [
        [Span(0, 5, "PERSON", 0.9, "fake", PRIORITY_ML)]
    ]


def test_analyze_reports_placeholder_literals_and_drops_allow_listed_values() -> None:
    spans = _engine(allow_list=["ACME"]).analyze(["[PERSON_1] at ACME"])
    assert spans == [[Span(0, 10, "LITERAL", 1.0, "literal", PRIORITY_LITERAL)]]


def test_analyze_spans_carry_the_type_of_their_entity() -> None:
    first = WordListDetector({"Alice": "PERSON"}, name="first")
    second = WordListDetector({"ALICE": "ORG"}, name="second")
    engine = Pseudonymizer([first, second], propagate=False)
    assert engine.analyze(["Alice", "x ALICE"]) == [
        [Span(0, 5, "PERSON", 0.9, "first", PRIORITY_ML)],
        [Span(2, 7, "PERSON", 0.9, "second", PRIORITY_ML)],
    ]


def test_analyze_of_an_empty_batch_is_empty() -> None:
    assert _engine().analyze([]) == []
    assert _engine().analyze([""]) == [[]]


def test_analyze_fails_closed_like_pseudonymize() -> None:
    with pytest.raises(DetectionError):
        Pseudonymizer([FailingDetector()]).analyze(["Alice"])


@_PROPERTY
@given(_BATCH, st.booleans())
def test_pseudonymize_replaces_exactly_the_spans_that_analyze_reports(
    texts: list[str], propagate: bool
) -> None:
    engine = _engine(propagate=propagate)
    batch = engine.pseudonymize(texts)
    analyzed = engine.analyze(texts)
    for text, spans, output in zip(texts, analyzed, batch.texts, strict=True):
        assert spans == sorted(spans, key=lambda span: span.start)
        pattern, position = [], 0
        for span in spans:
            pattern.append(re.escape(text[position : span.start]))
            pattern.append(rf"\[{span.entity_type}_[1-9][0-9]*\]")
            position = span.end
        pattern.append(re.escape(text[position:]))
        assert re.fullmatch("".join(pattern), output)
    counts = Counter((span.entity_type, span.source) for spans in analyzed for span in spans)
    assert dict(batch.stats.entities_by_type_and_source) == dict(counts)


# Dictionary aliases and variant placeholders (slice 13) --------------------------------------

ALIASED = [Term("ACME Corporation", "ORG", ("ACME",)), Term("Globex S.L.", "ORG", ("Globex",))]


def _aliased(**kwargs: object) -> Pseudonymizer:
    return Pseudonymizer([DictionaryDetector(ALIASED)], **kwargs)  # type: ignore[arg-type]


def test_a_term_and_its_alias_share_a_number_and_restore_as_written() -> None:
    texts = ["ACME Corporation signed.", "ACME paid."]
    batch = _aliased().pseudonymize(texts)
    assert batch.texts == ("[ORG_1] signed.", "[ORG_1_B] paid.")
    assert _restored(batch) == texts


def test_the_first_surface_seen_has_no_letter_even_when_it_is_the_alias() -> None:
    texts = ["ACME paid.", "ACME Corporation signed."]
    batch = _aliased().pseudonymize(texts)
    assert batch.texts == ("[ORG_1] paid.", "[ORG_1_B] signed.")
    assert _restored(batch) == texts


def test_a_case_variant_of_an_alias_restores_as_the_first_form_of_that_alias() -> None:
    batch = _aliased().pseudonymize(["ACME Corporation and ACME.", "Acme again."])
    assert batch.texts == ("[ORG_1] and [ORG_1_B].", "[ORG_1_B] again.")
    assert _restored(batch) == ["ACME Corporation and ACME.", "ACME again."]


def test_propagation_gives_a_variants_placeholder_to_an_occurrence_the_detector_missed() -> None:
    detector = WordListDetector(
        {"ACME Corporation": "ORG", "ACME": "ORG"},
        links={"ACME Corporation": "fake:1", "ACME": "fake:1"},
    )
    batch = Pseudonymizer([detector]).pseudonymize(["ACME Corporation hired ACME.", "acme paid."])
    assert batch.texts == ("[ORG_1] hired [ORG_1_B].", "[ORG_1_B] paid.")
    assert _restored(batch) == ["ACME Corporation hired ACME.", "ACME paid."]


def test_two_entries_give_two_families() -> None:
    batch = _aliased().pseudonymize(["ACME Corporation and ACME met Globex.", "Globex S.L. left."])
    assert batch.texts == ("[ORG_1] and [ORG_1_B] met [ORG_2].", "[ORG_2_B] left.")


def test_an_allow_listed_alias_stays_in_place_while_its_term_is_replaced() -> None:
    assert _aliased(allow_list=["ACME"]).pseudonymize(["ACME Corporation and ACME."]).texts == (
        "[ORG_1] and ACME.",
    )


def test_a_variant_placeholder_in_the_input_becomes_a_literal_and_is_restored() -> None:
    texts = ["Use [ORG_1_B] for ACME", "and [org 1 c]"]
    batch = _aliased().pseudonymize(texts)
    assert batch.texts == ("Use [LITERAL_1] for [ORG_1]", "and [LITERAL_2]")
    assert _restored(batch) == texts


def test_a_model_answer_with_both_variants_restores_both_surfaces() -> None:
    batch = _aliased().pseudonymize(["ACME Corporation signed.", "ACME paid."])
    answer = "[ORG_1_B] paid [ORG_1], then [org 1 b] and [ORG_1_F]."
    assert restore(answer, batch.mapping) == (
        "ACME paid ACME Corporation, then ACME and [ORG_1_F]."
    )
    assert count_unresolved(answer, batch.mapping) == 1


def test_analyze_returns_dictionary_spans_with_their_link() -> None:
    assert _aliased().analyze(["ACME Corporation and ACME", "acme corporation"]) == [
        [
            Span(0, 16, "ORG", 1.0, "dictionary", PRIORITY_DICTIONARY, "dictionary:1"),
            Span(21, 25, "ORG", 1.0, "dictionary", PRIORITY_DICTIONARY, "dictionary:1"),
        ],
        [Span(0, 16, "ORG", 1.0, "dictionary", PRIORITY_DICTIONARY, "dictionary:1")],
    ]


def test_propagated_spans_carry_no_link() -> None:
    detector = WordListDetector({"ACME": "ORG"}, links={"ACME": "fake:1"})
    assert Pseudonymizer([detector]).analyze(["ACME", "acme"]) == [
        [Span(0, 4, "ORG", 0.9, "fake", PRIORITY_ML, "fake:1")],
        [Span(0, 4, "ORG", 1.0, "propagation", PRIORITY_PROPAGATION)],
    ]


def test_a_linked_surface_never_joins_an_unlinked_entity() -> None:
    detectors = [
        WordListDetector({"ACME": "ORG"}, name="plain"),
        WordListDetector(
            {"ACME Corporation": "ORG"}, name="linked", links={"ACME Corporation": "x:1"}
        ),
    ]
    batch = Pseudonymizer(detectors).pseudonymize(["ACME", "ACME Corporation"])
    assert batch.texts == ("[ORG_1]", "[ORG_2]")


_LINKED_WORDS = {
    "Alice": "PERSON",
    "Ally": "PERSON",
    "Alicia": "PERSON",
    "Bob": "PERSON",
    "ACME": "ORG",
    "Acme Corp": "ORG",
    "ACME Inc": "ORG",
    "Initech": "ORG",
}
_LINKS = {
    "Alice": "fake:1",
    "Ally": "fake:1",
    "Alicia": "fake:1",
    "ACME": "fake:2",
    "Acme Corp": "fake:2",
    "ACME Inc": "fake:2",
}
_LINKS_WITH_SHARED_ID = {**_LINKS, "Initech": "fake:1"}
# Whole words come with a trailing space, so that most texts hold several detected surfaces and
# the linked ones become variants; the other tokens build placeholder-shaped text and partial
# words.
_LINKED_WORD_TOKENS = st.sampled_from(
    [
        "Alice ", "Ally ", "Alicia ", "Bob ", "ACME ", "Acme Corp ", "ACME Inc ", "Initech ",
        "alice ", "ally ", "ALLY ", "acme ", "acme corp ", "initech ",
    ]
)  # fmt: skip
_LINKED_OTHER_TOKENS = st.sampled_from(
    [
        "Acme", "Corp", "Ally", "[", "]", "\\", "_", " ", "\n", "1", "2", "B", "b", "C",
        "PERSON", "ORG", "LITERAL", "x", ".",
    ]
)  # fmt: skip
_LINKED_TOKENS = st.one_of(
    _LINKED_WORD_TOKENS, _LINKED_WORD_TOKENS, _LINKED_WORD_TOKENS, _LINKED_OTHER_TOKENS
)
_LINKED_BATCH = st.lists(
    st.lists(_LINKED_TOKENS, min_size=2, max_size=16).map("".join), min_size=1, max_size=4
)


def _linked(**kwargs: object) -> Pseudonymizer:
    detector = WordListDetector(_LINKED_WORDS, links=_LINKS_WITH_SHARED_ID)
    return Pseudonymizer([detector], **kwargs)  # type: ignore[arg-type]


@_PROPERTY
@given(_LINKED_BATCH)
def test_round_trip_with_linked_spans_restores_the_exact_input(texts: list[str]) -> None:
    batch = _linked(propagate=False).pseudonymize(texts)
    assert _restored(batch) == texts


@_PROPERTY
@given(_LINKED_BATCH)
def test_no_linked_surface_remains_in_the_output(texts: list[str]) -> None:
    batch = _linked().pseudonymize(texts)
    for entity in batch.mapping.entities():
        if entity.entity_type != "LITERAL" and len(entity.key) >= 3:
            pattern = build_term_pattern(entity.surface)
            assert not any(pattern.search(text) for text in batch.texts)


@_PROPERTY
@given(_LINKED_BATCH, _LINKED_BATCH, st.booleans())
def test_appending_texts_keeps_every_placeholder_variant_and_surface(
    first: list[str], appended: list[str], propagate: bool
) -> None:
    short = _linked(propagate=propagate).pseudonymize(first)
    long = _linked(propagate=propagate).pseudonymize(first + appended)
    if not propagate:
        assert long.texts[: len(first)] == short.texts
    for entity in short.mapping.entities():
        kept = long.mapping.get(entity.key)
        assert kept == entity
        assert kept is not None
        assert (kept.placeholder, kept.variant, kept.surface) == (
            entity.placeholder,
            entity.variant,
            entity.surface,
        )


def test_with_url_enabled_a_url_holding_an_email_is_replaced_whole() -> None:
    text = (
        "Reset at https://example.com/r?u=juan.perez@example.com "
        "or write to juan.perez@example.com."
    )
    engine = Pseudonymizer([RuleDetector(enabled_types=DEFAULT_RULE_TYPES | {"URL"})])
    batch = engine.pseudonymize([text])
    # Propagation of the email does not reach inside the URL: the URL span has the higher
    # priority.
    assert batch.texts == ("Reset at [URL_1] or write to [EMAIL_1].",)
    assert restore(batch.texts[0], batch.mapping) == text


# Person name tokens (slice 16) ----------------------------------------------------------------

ALICE = "Alice Johnson opened the ticket. Ms. Johnson replied. Alice called."


def _people(*names: str, extra: dict[str, str] | None = None, **kwargs: object) -> Pseudonymizer:
    """An engine whose fake detector finds the given full names, case-sensitively, as PERSON."""
    words = dict.fromkeys(names, "PERSON") | (extra or {})
    options: dict[str, object] = {"person_tokens": True, **kwargs}
    return Pseudonymizer([WordListDetector(words)], **options)  # type: ignore[arg-type]


def test_shorter_forms_of_a_detected_name_become_its_variants_and_restore_as_written() -> None:
    batch = _people("Alice Johnson").pseudonymize([ALICE])
    assert batch.texts == (
        "[PERSON_1] opened the ticket. Ms. [PERSON_1_B] replied. [PERSON_1_C] called.",
    )
    assert _restored(batch) == [ALICE]
    # Neither word is a common word, so both are replaced in lower case too (slice 17).
    assert [(e.surface, e.capitalised) for e in batch.mapping.entities()] == [
        ("Alice Johnson", False),
        ("Johnson", False),
        ("Alice", False),
    ]


def test_the_spanish_first_surname_is_a_variant_of_the_full_name() -> None:
    text = "Juan Pérez García abrió la incidencia. Según el Sr. Pérez, falta la firma."
    batch = _people("Juan Pérez García").pseudonymize([text])
    assert batch.texts == (
        "[PERSON_1] abrió la incidencia. Según el Sr. [PERSON_1_B], falta la firma.",
    )
    assert _restored(batch) == [text]


@pytest.mark.parametrize(
    ("full", "shorter", "text"),
    [
        ("Juan Pérez García", "Pérez", "Juan Pérez García llamó. Pérez pagó."),
        ("Juan Pérez García", "Juan Pérez", "Juan Pérez García llamó. Juan Pérez pagó."),
        ("Alice Johnson", "Dr. Alice Johnson", "Alice Johnson called. Dr. Alice Johnson paid."),
    ],
    ids=["surname", "run", "title"],
)
def test_a_detected_shorter_form_links_instead_of_getting_its_own_number(
    full: str, shorter: str, text: str
) -> None:
    batch = _people(full, shorter).pseudonymize([text])
    entities = batch.mapping.entities()
    assert [(e.surface, e.placeholder, e.capitalised) for e in entities] == [
        (full, "[PERSON_1]", False),
        (shorter, "[PERSON_1_B]", False),
    ]
    assert _restored(batch) == [text]


def test_a_detected_form_that_skips_a_word_does_not_link() -> None:
    batch = _people("Juan Pérez García", "Juan García").pseudonymize(
        ["Juan Pérez García llamó. Juan García pagó."]
    )
    assert batch.texts == ("[PERSON_1] llamó. [PERSON_2] pagó.",)


def test_a_detected_word_of_two_persons_gets_its_own_number() -> None:
    batch = _people("Bob Smith", "Alice Smith", "Smith").pseudonymize(
        ["Bob Smith and Alice Smith called. Smith paid."]
    )
    assert batch.texts == ("[PERSON_1] and [PERSON_2] called. [PERSON_3] paid.",)


def test_a_dictionary_span_with_a_link_keeps_its_group_even_if_it_is_a_persons_word() -> None:
    terms = [Term("Alice Johnson", "PERSON"), Term("Alice", "PERSON", ("Ally",))]
    engine = Pseudonymizer([DictionaryDetector(terms)], person_tokens=True)
    batch = engine.pseudonymize(["Alice Johnson wrote. Alice and Ally called."])
    assert batch.texts == ("[PERSON_1] wrote. [PERSON_2] and [PERSON_2_B] called.",)


def test_a_detected_span_of_another_type_is_never_linked_to_a_person() -> None:
    batch = _people("Alice Johnson", extra={"Johnson": "ORG"}).pseudonymize(
        ["Alice Johnson met Johnson."]
    )
    assert batch.texts == ("[PERSON_1] met [ORG_1].",)


@pytest.mark.parametrize(
    ("full", "surname"),
    [
        ("Dr. Smith", "Smith"),
        ("J. Smith", "Smith"),
        ("Mr Smith", "Smith"),
        ("J. R. Smith", "Smith"),
        ("Sr. Pérez", "Pérez"),
    ],
)
def test_a_name_with_one_name_token_and_a_title_or_an_initial_is_a_person(
    full: str, surname: str
) -> None:
    # Slice 16 left the surname as written (a known gap); slice 17 makes it a variant.
    text = f"{full} called. {surname} wants it."
    batch = _people(full).pseudonymize([text])
    assert batch.texts == ("[PERSON_1] called. [PERSON_1_B] wants it.",)
    assert _restored(batch) == [text]


def test_a_detected_surname_after_a_title_and_surname_links_to_it() -> None:
    batch = _people("Dr. Smith", "Smith").pseudonymize(["Dr. Smith called. Smith wants it."])
    assert [(e.surface, e.placeholder, e.capitalised) for e in batch.mapping.entities()] == [
        ("Dr. Smith", "[PERSON_1]", False),
        ("Smith", "[PERSON_1_B]", False),
    ]


def test_a_name_that_is_also_a_common_word_is_replaced_only_where_capitalised() -> None:
    text = "Rose Brown sent it. Brown says the brown box is damaged."
    batch = _people("Rose Brown").pseudonymize([text])
    assert batch.texts == ("[PERSON_1] sent it. [PERSON_1_B] says the brown box is damaged.",)
    assert _restored(batch) == [text]


LARRY = "Larry Thompson wrote earlier about the refund. thanks larry, got it"


def test_a_lower_case_shorter_form_is_replaced_and_restored_as_written() -> None:
    # Slice 16 left "larry" as written; slice 17 replaces it, since it is no common word.
    batch = _people("Larry Thompson").pseudonymize([LARRY])
    assert batch.texts == (
        "[PERSON_1] wrote earlier about the refund. thanks [PERSON_1_B], got it",
    )
    assert _restored(batch) == [LARRY]
    assert [(e.surface, e.capitalised) for e in batch.mapping.entities()] == [
        ("Larry Thompson", False),
        ("larry", False),
    ]


def test_a_capitalised_common_word_equal_to_a_name_word_is_replaced_too() -> None:
    # The expected false positive of decision 1: "Will" starts a question here.
    batch = _people("Will Stone").pseudonymize(["Will Stone sent the file. Will you check it?"])
    assert batch.texts == ("[PERSON_1] sent the file. [PERSON_1_B] you check it?",)


def test_a_word_two_persons_share_in_one_text_gets_its_own_number() -> None:
    text = "Bob Smith and Alice Smith called. Smith paid; Bob and Alice left."
    batch = _people("Bob Smith", "Alice Smith").pseudonymize([text])
    assert batch.texts == (
        "[PERSON_1] and [PERSON_2] called. [PERSON_3] paid; [PERSON_1_B] and [PERSON_2_B] left.",
    )
    assert _restored(batch) == [text]


def test_a_person_in_a_later_text_never_changes_an_earlier_link() -> None:
    texts = ["Bob Smith called. Smith paid.", "Alice Smith called. Smith left."]
    batch = _people("Bob Smith", "Alice Smith").pseudonymize(texts)
    assert batch.texts == (
        "[PERSON_1] called. [PERSON_1_B] paid.",
        "[PERSON_2] called. [PERSON_1_B] left.",
    )


def test_a_shorter_form_before_the_full_name_in_the_same_text_is_linked() -> None:
    text = "Alice called on Monday. Later, Alice Johnson sent the form."
    batch = _people("Alice Johnson").pseudonymize([text])
    assert batch.texts == ("[PERSON_1_B] called on Monday. Later, [PERSON_1] sent the form.",)
    assert _restored(batch) == [text]


def test_a_shorter_form_in_an_earlier_text_is_replaced_once_a_later_text_names_the_person() -> None:
    engine = _people("Alice Johnson")
    assert engine.pseudonymize(["Johnson called.", "Alice Johnson sent the form."]).texts == (
        "[PERSON_1_B] called.",
        "[PERSON_1] sent the form.",
    )
    assert engine.pseudonymize(["Johnson called."]).texts == ("Johnson called.",)


@pytest.mark.parametrize(
    ("full", "after"),
    [
        ("Sr. Juan Pérez", " Sr. Ruiz did not."),
        ("María del Mar Rosa", " Del Valle did not."),
        ("Alice J. Johnson", " J. did not."),
        ("Wei Li Zhang", " Li did not."),
    ],
    ids=["title", "particle", "initial", "short"],
)
def test_titles_particles_initials_and_short_words_never_become_entities(
    full: str, after: str
) -> None:
    batch = _people(full).pseudonymize([f"{full} wrote.{after}"])
    assert batch.texts == (f"[PERSON_1] wrote.{after}",)
    assert len(batch.mapping) == 1


def test_an_allow_listed_name_word_is_not_replaced() -> None:
    text = "Alice Mary Johnson wrote. Johnson and Mary called."
    batch = _people("Alice Mary Johnson", allow_list=["johnson"]).pseudonymize([text])
    assert batch.texts == ("[PERSON_1] wrote. Johnson and [PERSON_1_B] called.",)


def test_a_name_word_whose_key_is_already_an_organisation_stays_that_entity() -> None:
    text = "JOHNSON shipped it. Alice Johnson called. Johnson paid."
    batch = _people("Alice Johnson", extra={"JOHNSON": "ORG"}).pseudonymize([text])
    assert batch.texts == ("[ORG_1] shipped it. [PERSON_1] called. [ORG_1] paid.",)
    assert len(batch.mapping) == 2


def test_without_propagation_only_detected_shorter_forms_are_linked() -> None:
    text = "Juan Pérez García llamó. Pérez pagó; Juan se fue."
    batch = _people("Juan Pérez García", "Pérez", propagate=False).pseudonymize([text])
    assert batch.texts == ("[PERSON_1] llamó. [PERSON_1_B] pagó; Juan se fue.",)
    assert len(batch.mapping) == 2


def test_a_common_word_that_is_a_name_word_is_replaced_only_where_capitalised() -> None:
    text = "Juan Para Gómez llamó. Es para mañana, dijo Para."
    batch = _people("Juan Para Gómez").pseudonymize([text])
    assert batch.texts == ("[PERSON_1] llamó. Es para mañana, dijo [PERSON_1_B].",)
    assert [(e.surface, e.capitalised) for e in batch.mapping.entities()][1] == ("Para", True)
    assert _restored(batch) == [text]


def test_a_name_detected_in_lower_case_gives_no_lower_case_replacement() -> None:
    text = "larry thompson wrote. thanks larry. Larry will call."
    batch = _people("larry thompson").pseudonymize([text])
    assert batch.texts == ("[PERSON_1] wrote. thanks larry. [PERSON_1_B] will call.",)
    assert [(e.surface, e.capitalised) for e in batch.mapping.entities()][1] == ("Larry", True)


def test_a_lower_case_form_before_the_capitalised_one_gives_one_entity_written_in_lower_case() -> (
    None
):
    text = "Larry Thompson wrote. thanks larry. Larry said yes."
    batch = _people("Larry Thompson").pseudonymize([text])
    assert batch.texts == ("[PERSON_1] wrote. thanks [PERSON_1_B]. [PERSON_1_B] said yes.",)
    assert [e.surface for e in batch.mapping.entities()] == ["Larry Thompson", "larry"]
    assert _restored(batch) == ["Larry Thompson wrote. thanks larry. larry said yes."]


def test_a_particle_alone_does_not_make_a_detected_name_a_person() -> None:
    text = "El cliente llamó. El cliente quiere el reembolso; Cliente nuevo, cliente feliz."
    batch = _people("El cliente").pseudonymize([text])
    assert batch.texts == (
        "[PERSON_1] llamó. [PERSON_1] quiere el reembolso; Cliente nuevo, cliente feliz.",
    )
    assert len(batch.mapping) == 1


def test_each_part_of_a_hyphenated_name_is_a_variant_but_not_a_word_of_a_run() -> None:
    text = "Anne-Marie Fowler called. Anne paid, Marie signed and Fowler left."
    batch = _people("Anne-Marie Fowler").pseudonymize([text])
    assert batch.texts == (
        "[PERSON_1] called. [PERSON_1_B] paid, [PERSON_1_C] signed and [PERSON_1_D] left.",
    )
    assert _restored(batch) == [text]
    detected = _people("Anne-Marie Fowler", "Anne Fowler").pseudonymize(
        ["Anne-Marie Fowler called. Anne Fowler paid."]
    )
    assert detected.texts == ("[PERSON_1] called. [PERSON_2] paid.",)


def test_a_detected_run_that_starts_with_a_part_is_not_linked() -> None:
    text = "María-José Romero llamó. José Romero pagó."
    batch = _people("María-José Romero", "José Romero").pseudonymize([text])
    assert batch.texts == ("[PERSON_1] llamó. [PERSON_2] pagó.",)


def test_a_detected_part_links_to_a_one_word_hyphenated_name() -> None:
    batch = _people("María-José", "José").pseudonymize(["María-José llamó. José pagó."])
    assert batch.texts == ("[PERSON_1] llamó. [PERSON_1_B] pagó.",)
    undetected = _people("María-José").pseudonymize(["María-José llamó. José pagó."])
    assert undetected.texts == ("[PERSON_1] llamó. [PERSON_1_B] pagó.",)


def test_don_is_a_first_name_before_one_other_name_word_but_never_in_dont() -> None:
    text = "Don Draper called. Don wants a copy, and Draper will sign it. Don't worry."
    batch = _people("Don Draper").pseudonymize([text])
    assert batch.texts == (
        "[PERSON_1] called. [PERSON_1_B] wants a copy, and [PERSON_1_C] will sign it. Don't worry.",
    )
    assert _restored(batch) == [text]


def test_don_before_two_name_words_stays_a_title() -> None:
    text = "Don Juan Pérez llamó. Don Pedro no sabe nada."
    batch = _people("Don Juan Pérez").pseudonymize([text])
    assert batch.texts == ("[PERSON_1] llamó. Don Pedro no sabe nada.",)


def test_without_propagation_no_lower_case_word_nobody_detected_is_replaced() -> None:
    batch = _people("Larry Thompson", propagate=False).pseudonymize([LARRY])
    assert batch.texts == ("[PERSON_1] wrote earlier about the refund. thanks larry, got it",)
    assert len(batch.mapping) == 1


def test_with_person_tokens_off_the_lower_case_form_stays() -> None:
    batch = _people("Larry Thompson", person_tokens=False).pseudonymize([LARRY])
    assert batch.texts == ("[PERSON_1] wrote earlier about the refund. thanks larry, got it",)


def test_an_allow_listed_word_never_decides_whether_a_name_is_a_person() -> None:
    # Decided with the maintainer for slice 17: the allow list removes forms, it does not make
    # a detected name less of a person. Slice 16 left "Michael" as written here.
    text = "Michael Jordan called. Michael paid; Jordan is a country."
    batch = _people("Michael Jordan", allow_list=["jordan"]).pseudonymize([text])
    assert batch.texts == ("[PERSON_1] called. [PERSON_1_B] paid; Jordan is a country.",)


def test_an_allow_listed_part_is_not_replaced() -> None:
    text = "Anne-Marie Fowler called. Anne paid, Marie signed."
    batch = _people("Anne-Marie Fowler", allow_list=["anne"]).pseudonymize([text])
    assert batch.texts == ("[PERSON_1] called. Anne paid, [PERSON_1_B] signed.",)


def test_with_person_tokens_off_the_output_is_that_of_slice_15() -> None:
    assert _people("Alice Johnson", person_tokens=False).pseudonymize([ALICE]).texts == (
        "[PERSON_1] opened the ticket. Ms. Johnson replied. Alice called.",
    )
    detected = _people("Juan Pérez García", "Pérez", person_tokens=False)
    assert detected.pseudonymize(["Juan Pérez García llamó. Pérez pagó."]).texts == (
        "[PERSON_1] llamó. [PERSON_2] pagó.",
    )


def test_person_tokens_are_off_by_default() -> None:
    engine = Pseudonymizer([WordListDetector({"Alice Johnson": "PERSON"})])
    assert engine.pseudonymize([ALICE]).texts == (
        "[PERSON_1] opened the ticket. Ms. Johnson replied. Alice called.",
    )


def test_a_dictionary_persons_words_take_letters_after_the_aliases_seen_so_far() -> None:
    dictionary = DictionaryDetector([Term("Alice Johnson", "PERSON", ("AJ", "A. Johnson"))])
    engine = Pseudonymizer([dictionary], person_tokens=True)
    texts = ["Alice Johnson wrote. AJ called. Johnson paid.", "A. Johnson replied."]
    batch = engine.pseudonymize(texts)
    assert batch.texts == (
        "[PERSON_1] wrote. [PERSON_1_B] called. [PERSON_1_C] paid.",
        "[PERSON_1_D] replied.",
    )
    assert _restored(batch) == texts


def test_placeholder_literals_next_to_a_full_name_stay_literals() -> None:
    text = "[PERSON_1] is not Alice Johnson. Johnson knows."
    batch = _people("Alice Johnson").pseudonymize([text])
    assert batch.texts == ("[LITERAL_1] is not [PERSON_1]. [PERSON_1_B] knows.",)
    assert _restored(batch) == [text]


def test_analyze_reports_undetected_shorter_forms_as_propagation() -> None:
    spans = _people("Alice Johnson").analyze([ALICE])[0]
    assert [(span.start, span.end, span.source) for span in spans] == [
        (0, 13, "fake"),
        (37, 44, "propagation"),
        (54, 59, "propagation"),
    ]


def test_a_name_words_entity_may_exist_without_a_replaced_occurrence() -> None:
    # Its only occurrence on its own lies inside a repeat of the full name that the detector
    # missed, and propagation gives that whole repeat to the full name first.
    text = "Alice Johnson wrote. ALICE JOHNSON again."
    batch = _people("Alice Johnson").pseudonymize([text])
    assert batch.texts == ("[PERSON_1] wrote. [PERSON_1] again.",)
    assert [e.placeholder for e in batch.mapping.entities()] == [
        "[PERSON_1]",
        "[PERSON_1_B]",
        "[PERSON_1_C]",
    ]


# Properties of person name tokens.

_BASE_NAMES = (
    "Alice Johnson",
    "Bob Smith",
    "Alice Smith",
    "Will Stone",
    "Juan Pérez García",
    "Juan Pérez",
    "Ana Pérez",
    "Dr. Lucía Romero",
    "Lucía Romero",
    "Mary Ann Lee",
    "Liam O'Connor",
)
# Slice 17: one name word with a title or an initial, hyphenated names and `Don`, with their
# shorter forms, drawn on their own so that most batches hold one of these persons and a form.
_FORM_NAMES = ("Dr. Holloway", "J. Fowler", "Anne-Marie Fowler", "María-José", "Don Draper",
               "Don Juan Pérez")  # fmt: skip
_FORMS = ("Holloway", "Fowler", "Anne", "Marie", "José", "Don", "Draper", "holloway", "anne",
          "josé", "draper", "Don't")  # fmt: skip
_NAMES = (*_BASE_NAMES, *_FORM_NAMES)
_NAME_WORDS = sorted(
    {word for name in _NAMES for word in name.split() if len(word) >= 3}
    | {part for name in _NAMES for word in name.split() for part in word.split("-")[1:]}
    | {word.split("-")[0] for name in _NAMES for word in name.split() if "-" in word}
)
_CASES = sorted(
    {form for word in _NAME_WORDS for form in (word, word.lower(), word.upper(), word.swapcase())}
)
_PERSON_TOKENS = st.one_of(
    st.sampled_from([f"{name} " for name in _NAMES]),
    st.sampled_from([f"{name} " for name in _FORM_NAMES]),
    st.sampled_from([f"{form} " for form in _FORMS]),
    st.sampled_from([f"{form} " for form in _CASES]),
    st.sampled_from([f"{form} " for form in _CASES]),
    st.sampled_from(
        [
            "the ", "said ", ". ", ", ", "\n", "'s ", "(", ") ", "Sr. ", "Ms. ", "de ", "x",
            "[", "]", "_", "1", "PERSON", "-", "", "'t ", "Don't ", "J. ", "Mr ",
        ]
    ),
)  # fmt: skip
_PERSON_BATCH = st.lists(
    st.lists(_PERSON_TOKENS, min_size=1, max_size=14).map("".join), min_size=1, max_size=4
)
_PERSON_PROPERTY = settings(max_examples=300, deadline=None)


def _named(*, propagate: bool = True) -> Pseudonymizer:
    return _people(*_NAMES, propagate=propagate)


def _starts_upper(text: str) -> bool:
    return text[:1].isupper() or text[:1].istitle()


@_PERSON_PROPERTY
@given(_PERSON_BATCH)
def test_property_round_trip_with_person_tokens_gives_the_expected_restoration(
    texts: list[str],
) -> None:
    engine = _named()
    batch = engine.pseudonymize(texts)
    spans = engine.analyze(texts)
    for text, done, found in zip(texts, batch.texts, spans, strict=True):
        expected = expected_restoration(text, found, batch.mapping)
        assert expected is not None
        assert restore(done, batch.mapping) == expected


def _one_word_forms(surface: str) -> dict[str, str]:
    """The keys of a person's name tokens and name parts, with each as written."""
    forms: dict[str, str] = {}
    for key, word in name_words(surface, min_length=3):
        forms.setdefault(key, word)
        for part_key, part in name_parts(word, min_length=3):
            forms.setdefault(part_key, part)
    return forms


@_PERSON_PROPERTY
@given(_PERSON_BATCH)
def test_property_no_name_word_of_a_person_is_left_on_its_own(texts: list[str]) -> None:
    # Capitalised, or in any case when it is lower-case eligible (slice 17): every name written
    # in `_NAMES` is capitalised, so a word is eligible unless it is a common word.
    batch = _named().pseudonymize(texts)
    written: dict[str, str] = {}
    for entity in batch.mapping.entities():
        if entity.entity_type == "PERSON" and is_person(entity.surface, min_length=3):
            for key, word in _one_word_forms(entity.surface).items():
                written.setdefault(key, word)
    for key, word in written.items():
        eligible = key not in COMMON_WORDS and _starts_upper(word)
        for text in batch.texts:
            for match in build_term_pattern(key).finditer(text):
                assert not (
                    (eligible or _starts_upper(match.group()))
                    and normalize_key(match.group()) == key
                ), "a name word was left in the output"


@_PERSON_PROPERTY
@given(_PERSON_BATCH, _PERSON_BATCH, st.booleans())
def test_property_appending_texts_keeps_every_entity_with_person_tokens(
    first: list[str], appended: list[str], propagate: bool
) -> None:
    short = _named(propagate=propagate).pseudonymize(first)
    long = _named(propagate=propagate).pseudonymize(first + appended)
    if not propagate:
        assert long.texts[: len(first)] == short.texts
    for entity in short.mapping.entities():
        kept = long.mapping.get(entity.key)
        assert kept is not None
        assert (kept.placeholder, kept.variant, kept.surface, kept.capitalised) == (
            entity.placeholder,
            entity.variant,
            entity.surface,
            entity.capitalised,
        )


@_PERSON_PROPERTY
@given(_PERSON_BATCH)
def test_property_person_tokens_are_deterministic(texts: list[str]) -> None:
    first = _named().pseudonymize(texts)
    second = _named().pseudonymize(texts)
    assert first.texts == second.texts
    assert first.mapping.entities() == second.mapping.entities()


class _RunDetector:
    """Finds every two-word name made of the letters this test generates, with one regex."""

    name = "names"
    _PATTERN = re.compile(r"\bXa[a-z]+ Yb[a-z]+\b")

    def detect(self, text: str) -> list[Span]:
        return [
            Span(match.start(), match.end(), "PERSON", 0.9, self.name, PRIORITY_ML)
            for match in self._PATTERN.finditer(text)
        ]


def _letters(number: int) -> str:
    digits = []
    for _ in range(4):
        number, digit = divmod(number, 26)
        digits.append(chr(ord("a") + digit))
    return "".join(digits)


def test_words_nobody_detected_are_searched_once_per_text() -> None:
    # 1,000 texts, each naming a new person whose words never stand alone, so every candidate
    # stays pending. Searching every pending candidate in every earlier text after each text
    # would be cubic and take far longer than the bound (slice 16 pull request).
    texts = [
        f"Ticket {index}: Xa{_letters(index)} Yb{_letters(index)} called." for index in range(1000)
    ]
    engine = Pseudonymizer([_RunDetector()], person_tokens=True)
    started = time.perf_counter()
    batch = engine.pseudonymize(texts)
    elapsed = time.perf_counter() - started
    assert len(batch.mapping) == 1000
    assert elapsed < 15
