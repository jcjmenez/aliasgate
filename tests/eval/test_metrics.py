from collections.abc import Collection, Mapping, Sequence
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aliasgate.config import Settings
from aliasgate.engine import PseudonymizedBatch, Pseudonymizer
from aliasgate.eval.corpus import Example, GoldSpan, load_corpus
from aliasgate.eval.metrics import (
    LinkScore,
    Miss,
    Report,
    TypeScore,
    WrongMerge,
    evaluate,
    expected_restoration,
)
from aliasgate.factory import build_pseudonymizer
from aliasgate.types import PRIORITY_ML, Span
from fakes import WordListDetector
from oracles import propagation_oracle

CORPUS = Path(__file__).parent / "corpus"


class _ScriptedDetector:
    """Returns, for each known text, the spans given as (start, end, type)."""

    name = "scripted"

    def __init__(self, spans: Mapping[str, Sequence[tuple[int, int, str]]]) -> None:
        self._spans = spans

    def detect(self, text: str) -> list[Span]:
        return [
            Span(start, end, entity_type, 0.9, self.name, PRIORITY_ML)
            for start, end, entity_type in self._spans.get(text, ())
        ]


def _example(
    text: str, *spans: tuple[int, int, str] | tuple[int, int, str, str], example_id: str = "x-1"
) -> Example:
    return Example(example_id, "en", text, tuple(GoldSpan(*span) for span in spans))


def _engine(
    spans: Mapping[str, Sequence[tuple[int, int, str]]], *, propagate: bool = True
) -> Pseudonymizer:
    return Pseudonymizer([_ScriptedDetector(spans)], propagate=propagate)


def _score(report: Report, entity_type: str) -> TypeScore:
    (score,) = [score for score in report.by_type if score.entity_type == entity_type]
    return score


EXAMPLES = [
    _example("Alice Johnson wrote to ACME.", (0, 13, "PERSON"), (23, 27, "ORG"), example_id="a"),
    _example("Mail juan.perez@example.com now", (5, 27, "EMAIL"), example_id="b"),
    _example("No entities here.", example_id="c"),
]


def test_a_perfect_detector_has_full_recall_and_precision_and_no_leak() -> None:
    gold = {
        example.text: [(s.start, s.end, s.entity_type) for s in example.spans]
        for example in EXAMPLES
    }
    report = evaluate(_engine(gold), EXAMPLES)
    assert [score.entity_type for score in report.by_type] == ["EMAIL", "ORG", "PERSON"]
    for score in report.by_type:
        assert (score.recall_exact, score.recall_overlap, score.precision_exact) == (1, 1, 1)
        assert score.gold == score.predicted == score.exact_tp == score.overlap_tp == 1
    assert (report.leaked_chars, report.gold_chars, report.leak_rate) == (0, 39, 0.0)
    assert report.round_trip_failures == 0
    assert report.misses == ()


def test_a_detector_that_finds_nothing_has_zero_recall_and_leaks_everything() -> None:
    report = evaluate(_engine({}), EXAMPLES)
    for score in report.by_type:
        assert (score.recall_exact, score.recall_overlap) == (0.0, 0.0)
        assert (score.predicted, score.precision_exact) == (0, 1.0)
    assert (report.leaked_chars, report.gold_chars, report.leak_rate) == (39, 39, 1.0)
    assert report.round_trip_failures == 0


def test_half_a_span_covered_counts_as_overlap_and_leaks_the_uncovered_part() -> None:
    text = "Signed by Alice Johnson today"
    example = _example(text, (10, 23, "PERSON"))
    report = evaluate(_engine({text: [(10, 15, "PERSON")]}), [example])
    score = _score(report, "PERSON")
    assert (score.gold, score.predicted, score.exact_tp, score.overlap_tp) == (1, 1, 0, 1)
    assert score.precision_exact == 0.0
    assert (report.leaked_chars, report.gold_chars) == (8, 13)
    assert report.leak_rate == pytest.approx(8 / 13)
    assert report.misses == (Miss("x-1", 10, 23, "PERSON", 8),)


def test_wrong_type_with_exact_boundaries_is_an_overlap_but_not_an_exact_match() -> None:
    text = "ACME called"
    report = evaluate(_engine({text: [(0, 4, "ORG")]}), [_example(text, (0, 4, "PERSON"))])
    person, org = _score(report, "PERSON"), _score(report, "ORG")
    assert (person.exact_tp, person.overlap_tp, person.recall_overlap) == (0, 1, 1.0)
    assert person.recall_exact == 0.0
    assert (org.gold, org.predicted, org.precision_exact, org.recall_exact) == (0, 1, 0.0, 1.0)
    assert report.leak_rate == 0.0


def test_an_empty_corpus_does_not_divide_by_zero() -> None:
    report = evaluate(_engine({}), [])
    assert report.by_type == ()
    assert (report.examples, report.leak_rate, report.entity_free_flag_rate) == (0, 0.0, 0.0)


def test_a_corpus_without_gold_spans_does_not_divide_by_zero() -> None:
    text = "Version 2.3 shipped"
    report = evaluate(_engine({text: [(8, 11, "ORG")]}), [_example(text)])
    org = _score(report, "ORG")
    assert (org.gold, org.recall_exact, org.recall_overlap, org.precision_exact) == (0, 1, 1, 0)
    assert (report.gold_chars, report.leak_rate) == (0, 0.0)


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (TypeScore("X", gold=0, predicted=0, exact_tp=0, overlap_tp=0), (1.0, 1.0, 1.0)),
        (TypeScore("X", gold=4, predicted=0, exact_tp=0, overlap_tp=0), (0.0, 0.0, 1.0)),
        (TypeScore("X", gold=4, predicted=2, exact_tp=1, overlap_tp=3), (0.25, 0.75, 0.5)),
    ],
)
def test_type_score_ratios(score: TypeScore, expected: tuple[float, float, float]) -> None:
    assert (score.recall_exact, score.recall_overlap, score.precision_exact) == expected


def test_propagated_spans_count_as_predictions() -> None:
    text = "ACME Corp and acme corp"
    example = _example(text, (0, 9, "ORG"), (14, 23, "ORG"))
    report = evaluate(_engine({text: [(0, 9, "ORG")]}), [example])
    assert _score(report, "ORG").exact_tp == 2
    assert report.leak_rate == 0.0
    without = evaluate(_engine({text: [(0, 9, "ORG")]}, propagate=False), [example])
    assert _score(without, "ORG").exact_tp == 1
    assert without.misses == (Miss("x-1", 14, 23, "ORG", 9),)


def test_types_restrict_gold_and_predictions_but_any_prediction_covers() -> None:
    text = "Mail juan.perez@example.com or Alice"
    example = _example(text, (5, 27, "EMAIL"), (31, 36, "PERSON"))
    engine = _engine({text: [(5, 27, "PERSON")]})
    report = evaluate(engine, [example], types={"EMAIL"})
    assert [score.entity_type for score in report.by_type] == ["EMAIL"]
    email = report.by_type[0]
    assert (email.gold, email.predicted, email.exact_tp, email.overlap_tp) == (1, 0, 0, 1)
    assert (report.leaked_chars, report.gold_chars) == (0, 22)


def test_examples_without_entities_are_counted_and_flagged_when_anything_is_predicted() -> None:
    clean, flagged = "Order 4521 shipped", "Version 2.3 shipped"
    examples = [_example(clean), _example(flagged), _example("Alice", (0, 5, "PERSON"))]
    report = evaluate(_engine({flagged: [(8, 11, "ORG")]}), examples)
    assert (report.examples, report.entity_free, report.entity_free_flagged) == (3, 2, 1)
    assert report.entity_free_flag_rate == 0.5
    assert (
        evaluate(
            _engine({flagged: [(8, 11, "ORG")]}), examples, types={"PERSON"}
        ).entity_free_flagged
        == 0
    )


class _TamperingPseudonymizer(Pseudonymizer):
    def pseudonymize(
        self, texts: Sequence[str], *, labels: Collection[int] = ()
    ) -> PseudonymizedBatch:
        batch = super().pseudonymize(texts, labels=labels)
        return PseudonymizedBatch(
            tuple(text + "!" for text in batch.texts), batch.mapping, batch.stats
        )


def test_a_restoration_that_differs_from_the_expected_text_is_a_round_trip_failure() -> None:
    engine = _TamperingPseudonymizer([_ScriptedDetector({})])
    assert evaluate(engine, EXAMPLES).round_trip_failures == len(EXAMPLES)


# expected_restoration -------------------------------------------------------------------------


def _expected(engine: Pseudonymizer, text: str) -> str | None:
    batch = engine.pseudonymize([text])
    return expected_restoration(text, engine.analyze([text])[0], batch.mapping)


def test_expected_restoration_renders_variants_as_the_canonical_surface() -> None:
    engine = Pseudonymizer([WordListDetector({"Alice": "PERSON", "ALICE": "PERSON"})])
    assert _expected(engine, "Alice, ALICE and alice") == "Alice, Alice and Alice"


def test_expected_restoration_keeps_placeholder_literals_and_untouched_text() -> None:
    engine = Pseudonymizer([WordListDetector({"Alice": "PERSON"})])
    assert _expected(engine, "[PERSON_1] is not Alice") == "[PERSON_1] is not Alice"


def test_expected_restoration_needs_an_entity_for_every_span() -> None:
    span = Span(0, 5, "PERSON", 0.9, "fake", PRIORITY_ML)
    propagated = Span(0, 5, "PERSON", 1.0, "propagation", 20)
    batch = Pseudonymizer([]).pseudonymize(["Alice"])
    assert expected_restoration("Alice", [span], batch.mapping) is None
    assert expected_restoration("Alice", [propagated], batch.mapping) is None


def test_expected_restoration_finds_the_entity_of_a_propagated_span_by_its_pattern() -> None:
    # "İ" (dotted capital I) matches "i" case-insensitively, but its key differs.
    engine = Pseudonymizer([WordListDetector({"Ivan": "PERSON"})])
    assert _expected(engine, "Ivan and İvan") == "Ivan and Ivan"


_TOKENS = st.sampled_from(
    ["Alice", "alice", "ALICE", "Bob", "bob", "ACME", "acme", "[", "]", "\\", "_", " ", "1", "x"]
)
_TEXTS = st.lists(st.lists(_TOKENS, max_size=10).map("".join), max_size=3)
_WORDS = {"Alice": "PERSON", "Bob": "PERSON", "ACME": "ORG"}


@settings(max_examples=300, deadline=None)
@given(_TEXTS, st.booleans())
def test_expected_restoration_agrees_with_the_slice_03_oracles(
    texts: list[str], propagate: bool
) -> None:
    engine = Pseudonymizer([WordListDetector(_WORDS)], propagate=propagate)
    batch = engine.pseudonymize(texts)
    expected = [
        expected_restoration(text, spans, batch.mapping)
        for text, spans in zip(texts, engine.analyze(texts), strict=True)
    ]
    assert expected == (propagation_oracle(texts, batch) if propagate else texts)


@pytest.mark.parametrize("lang", ["en", "es"])
def test_expected_restoration_agrees_with_the_slice_03_oracle_on_the_corpus(lang: str) -> None:
    engine = build_pseudonymizer(Settings())
    for example in load_corpus(CORPUS / f"synthetic.{lang}.jsonl"):
        batch = engine.pseudonymize([example.text])
        spans = engine.analyze([example.text])[0]
        assert [expected_restoration(example.text, spans, batch.mapping)] == propagation_oracle(
            [example.text], batch
        ), example.id


# Link scores ----------------------------------------------------------------------------------


def _link(report: Report, entity_type: str = "PERSON") -> LinkScore:
    (score,) = [score for score in report.linking if score.entity_type == entity_type]
    return score


def _counts(score: LinkScore) -> tuple[int, int, int, int, int]:
    return (score.mentions, score.placed, score.same_pairs, score.linked_pairs, score.wrong_pairs)


def test_a_corpus_without_entity_ids_has_no_link_scores_and_no_wrong_merges() -> None:
    report = evaluate(
        _engine({"Smith and Smith": [(0, 5, "PERSON"), (10, 15, "PERSON")]}),
        [_example("Smith and Smith", (0, 5, "PERSON"), (10, 15, "PERSON"))],
    )
    assert (report.linking, report.wrong_merges) == ((), ())


def test_mentions_whose_placeholders_differ_only_by_the_variant_letter_are_linked() -> None:
    text = "Alice Johnson wrote. Ally will call."
    words = {"Alice Johnson": "PERSON", "Ally": "PERSON"}
    links = {"Alice Johnson": "fake:1", "Ally": "fake:1"}
    engine = Pseudonymizer([WordListDetector(words, links=links)])
    assert engine.pseudonymize([text]).texts == ("[PERSON_1] wrote. [PERSON_1_B] will call.",)
    report = evaluate(engine, [_example(text, (0, 13, "PERSON", "p1"), (21, 25, "PERSON", "p1"))])
    assert report.linking == (LinkScore("PERSON", 2, 2, 1, 1, 0),)
    assert (report.linking[0].link_recall, report.linking[0].link_precision) == (1.0, 1.0)
    assert report.wrong_merges == ()


def test_mentions_of_one_entity_with_different_numbers_are_a_missed_link() -> None:
    text = "Alice Johnson wrote. Ally will call."
    engine = Pseudonymizer([WordListDetector({"Alice Johnson": "PERSON", "Ally": "PERSON"})])
    assert engine.pseudonymize([text]).texts == ("[PERSON_1] wrote. [PERSON_2] will call.",)
    report = evaluate(engine, [_example(text, (0, 13, "PERSON", "p1"), (21, 25, "PERSON", "p1"))])
    assert _counts(_link(report)) == (2, 2, 1, 0, 0)
    assert (_link(report).link_recall, _link(report).link_precision) == (0.0, 1.0)


def test_two_entities_whose_mentions_have_one_key_are_a_wrong_merge() -> None:
    text = "Smith called. Later Smith paid."
    engine = Pseudonymizer([WordListDetector({"Smith": "PERSON"})])
    report = evaluate(engine, [_example(text, (0, 5, "PERSON", "p1"), (20, 25, "PERSON", "p2"))])
    assert _counts(_link(report)) == (2, 2, 0, 0, 1)
    assert (_link(report).link_recall, _link(report).link_precision) == (1.0, 0.0)
    assert report.wrong_merges == (WrongMerge("x-1", (0, 5), (20, 25), "PERSON"),)


def test_a_span_without_an_entity_id_is_an_entity_of_its_own() -> None:
    text = "Smith called. Later Smith paid."
    engine = Pseudonymizer([WordListDetector({"Smith": "PERSON"})])
    report = evaluate(engine, [_example(text, (0, 5, "PERSON", "p1"), (20, 25, "PERSON"))])
    assert _counts(_link(report)) == (2, 2, 0, 0, 1)
    assert report.wrong_merges == (WrongMerge("x-1", (0, 5), (20, 25), "PERSON"),)


def test_two_spans_without_an_entity_id_are_two_entities() -> None:
    text = "Alice met Smith. Later Smith paid."
    engine = Pseudonymizer([WordListDetector({"Smith": "PERSON"})])
    gold = _example(text, (0, 5, "PERSON", "p1"), (10, 15, "PERSON"), (23, 28, "PERSON"))
    report = evaluate(engine, [gold])
    assert _counts(_link(report)) == (3, 2, 0, 0, 1)
    assert report.wrong_merges == (WrongMerge("x-1", (10, 15), (23, 28), "PERSON"),)


def test_a_gold_span_that_no_predicted_span_touches_is_not_placed_and_forms_no_pair() -> None:
    text = "Alice Johnson wrote. Alice will call."
    engine = Pseudonymizer([WordListDetector({"Alice Johnson": "PERSON"})])
    report = evaluate(engine, [_example(text, (0, 13, "PERSON", "p1"), (21, 26, "PERSON", "p1"))])
    assert _counts(_link(report)) == (2, 1, 0, 0, 0)
    assert report.leaked_chars == 5


def test_a_gold_span_takes_the_family_of_the_predicted_span_that_covers_most_of_it() -> None:
    text = "Alice Johnson wrote. Johnson paid."
    gold = _example(text, (0, 13, "PERSON", "p1"), (21, 28, "PERSON", "p1"))
    predicted = [(0, 5, "PERSON"), (6, 13, "PERSON"), (21, 28, "PERSON")]
    report = evaluate(_engine({text: predicted}), [gold])
    assert _counts(_link(report)) == (2, 2, 1, 1, 0)


def test_on_a_tie_a_gold_span_takes_the_family_of_the_earlier_predicted_span() -> None:
    first, last = "Ann Lee wrote. Ann paid.", "Ann Lee wrote. Lee paid."
    predicted = {
        first: [(0, 3, "PERSON"), (4, 7, "PERSON"), (15, 18, "PERSON")],
        last: [(0, 3, "PERSON"), (4, 7, "PERSON"), (15, 18, "PERSON")],
    }
    examples = [
        _example(first, (0, 7, "PERSON", "p1"), (15, 18, "PERSON", "p1"), example_id="first"),
        _example(last, (0, 7, "PERSON", "p1"), (15, 18, "PERSON", "p1"), example_id="last"),
    ]
    assert _counts(_link(evaluate(_engine(predicted), examples[:1]))) == (2, 2, 1, 1, 0)
    assert _counts(_link(evaluate(_engine(predicted), examples[1:]))) == (2, 2, 1, 0, 0)


def test_a_gold_span_covered_by_a_propagated_span_takes_the_family_propagation_used() -> None:
    # "İ" (dotted capital I) matches "i" case-insensitively, but its key differs from "ivan".
    text = "Ivan wrote. İvan paid."
    engine = Pseudonymizer([WordListDetector({"Ivan": "PERSON"})])
    assert [span.source for span in engine.analyze([text])[0]] == ["fake", "propagation"]
    report = evaluate(engine, [_example(text, (0, 4, "PERSON", "p1"), (12, 16, "PERSON", "p1"))])
    assert _counts(_link(report)) == (2, 2, 1, 1, 0)


def test_a_placeholder_literal_has_no_family() -> None:
    text = "[PERSON_1] wrote. [PERSON_1] paid."
    engine = Pseudonymizer([])
    assert [span.entity_type for span in engine.analyze([text])[0]] == ["LITERAL", "LITERAL"]
    report = evaluate(engine, [_example(text, (0, 10, "PERSON", "p1"), (18, 28, "PERSON", "p2"))])
    assert _counts(_link(report)) == (2, 0, 0, 0, 0)


def test_mentions_of_different_types_never_form_a_pair() -> None:
    text = "ACME wrote. ACME paid."
    engine = Pseudonymizer([WordListDetector({"ACME": "ORG"})])
    report = evaluate(engine, [_example(text, (0, 4, "PERSON", "p1"), (12, 16, "ORG", "o1"))])
    assert [_counts(score) for score in report.linking] == [(1, 1, 0, 0, 0), (1, 1, 0, 0, 0)]
    assert [score.entity_type for score in report.linking] == ["ORG", "PERSON"]
    assert report.wrong_merges == ()


def test_pairs_never_cross_examples() -> None:
    engine = Pseudonymizer([WordListDetector({"Smith": "PERSON"})])
    examples = [
        _example("Smith called.", (0, 5, "PERSON", "p1"), example_id="a"),
        _example("Smith paid.", (0, 5, "PERSON", "p2"), example_id="b"),
    ]
    assert _counts(_link(evaluate(engine, examples))) == (2, 2, 0, 0, 0)


def test_a_type_without_entity_ids_gets_no_link_score_and_one_with_ids_always_does() -> None:
    text = "Alice wrote to ACME and ACME"
    examples = [_example(text, (0, 5, "PERSON", "p1"), (15, 19, "ORG"), (24, 28, "ORG"))]
    report = evaluate(Pseudonymizer([WordListDetector({"ACME": "ORG"})]), examples)
    assert report.linking == (LinkScore("PERSON", 1, 0, 0, 0, 0),)
    assert report.wrong_merges == ()


def test_types_restrict_the_link_scores() -> None:
    text = "ACME wrote. Alice paid."
    example = _example(text, (0, 4, "ORG", "o1"), (12, 17, "PERSON", "p1"))
    engine = Pseudonymizer([WordListDetector({"ACME": "ORG", "Alice": "PERSON"})])
    assert [score.entity_type for score in evaluate(engine, [example]).linking] == [
        "ORG",
        "PERSON",
    ]
    assert evaluate(engine, [example], types={"ORG"}).linking == (LinkScore("ORG", 1, 1, 0, 0, 0),)


def test_three_mentions_of_one_entity_in_one_family_are_three_linked_pairs() -> None:
    text = "Alice wrote. Alice paid. Alice left."
    gold = _example(
        text, (0, 5, "PERSON", "p1"), (13, 18, "PERSON", "p1"), (25, 30, "PERSON", "p1")
    )
    report = evaluate(Pseudonymizer([WordListDetector({"Alice": "PERSON"})]), [gold])
    assert _counts(_link(report)) == (3, 3, 3, 3, 0)


def test_wrong_merges_are_ordered_by_example_then_first_then_second_span() -> None:
    engine = Pseudonymizer([WordListDetector({"Smith": "PERSON"})])
    examples = [
        _example(
            "Smith, Smith and Smith",
            (0, 5, "PERSON", "a"),
            (7, 12, "PERSON", "b"),
            (17, 22, "PERSON", "c"),
            example_id="z-first",
        ),
        _example("Smith or Smith", (0, 5, "PERSON", "a"), (9, 14, "PERSON", "b"), example_id="a-2"),
    ]
    report = evaluate(engine, examples)
    assert report.wrong_merges == (
        WrongMerge("z-first", (0, 5), (7, 12), "PERSON"),
        WrongMerge("z-first", (0, 5), (17, 22), "PERSON"),
        WrongMerge("z-first", (7, 12), (17, 22), "PERSON"),
        WrongMerge("a-2", (0, 5), (9, 14), "PERSON"),
    )
    assert _link(report).wrong_pairs == 4


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (
            LinkScore("X", mentions=0, placed=0, same_pairs=0, linked_pairs=0, wrong_pairs=0),
            (1.0, 1.0),
        ),
        (
            LinkScore("X", mentions=2, placed=2, same_pairs=0, linked_pairs=0, wrong_pairs=1),
            (1.0, 0.0),
        ),
        (
            LinkScore("X", mentions=5, placed=5, same_pairs=4, linked_pairs=1, wrong_pairs=3),
            (0.25, 0.25),
        ),
    ],
)
def test_link_score_ratios(score: LinkScore, expected: tuple[float, float]) -> None:
    assert (score.link_recall, score.link_precision) == expected
