import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aliasgate.engine import DetectionError, PseudonymizedBatch, Pseudonymizer, restore
from aliasgate.placeholders import TOLERANT
from aliasgate.terms import build_term_pattern
from aliasgate.types import PRIORITY_ML, Span
from fakes import FailingDetector, WordListDetector

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


def _propagation_oracle(texts: list[str], batch: PseudonymizedBatch) -> list[str]:
    """Original texts with every propagatable case variant rendered as its canonical surface."""
    entities = sorted(
        (e for e in batch.mapping.entities() if e.entity_type != "LITERAL" and len(e.key) >= 3),
        key=lambda entity: len(entity.surface),
        reverse=True,
    )
    expected = []
    for text in texts:
        claimed = [(match.start(), match.end()) for match in TOLERANT.finditer(text)]
        replacements = []
        for entity in entities:
            for match in build_term_pattern(entity.surface).finditer(text):
                if all(match.end() <= start or end <= match.start() for start, end in claimed):
                    claimed.append((match.start(), match.end()))
                    replacements.append((match.start(), match.end(), entity.surface))
        for start, end, surface in sorted(replacements, reverse=True):
            text = text[:start] + surface + text[end:]
        expected.append(text)
    return expected


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
    assert _restored(batch) == _propagation_oracle(texts, batch)


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
