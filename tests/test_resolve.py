import random

from hypothesis import given
from hypothesis import strategies as st

from aliasgate.resolve import resolve_overlaps
from aliasgate.types import Span


def _span(
    start: int,
    end: int,
    *,
    priority: int = 40,
    score: float = 0.9,
    entity_type: str = "PERSON",
    source: str = "fake",
) -> Span:
    return Span(start, end, entity_type, score, source, priority)


def _order_key(span: Span) -> tuple[int, float, int, int, str, str]:
    return (
        -span.priority,
        -span.score,
        -(span.end - span.start),
        span.start,
        span.entity_type,
        span.source,
    )


def _overlap(a: Span, b: Span) -> bool:
    return a.start < b.end and b.start < a.end


def test_no_spans_gives_an_empty_list() -> None:
    assert resolve_overlaps([]) == []


def test_one_span_is_returned() -> None:
    span = _span(0, 5)
    assert resolve_overlaps([span]) == [span]


def test_disjoint_spans_are_kept_and_sorted_by_start() -> None:
    first, second = _span(0, 3), _span(5, 9)
    assert resolve_overlaps([second, first]) == [first, second]


def test_higher_priority_wins_regardless_of_score_and_length() -> None:
    long_confident = _span(0, 10, priority=40, score=1.0)
    short_unsure = _span(2, 4, priority=60, score=0.1)
    assert resolve_overlaps([long_confident, short_unsure]) == [short_unsure]


def test_same_priority_higher_score_wins() -> None:
    long_unsure = _span(0, 10, score=0.5)
    short_confident = _span(2, 4, score=0.9)
    assert resolve_overlaps([long_unsure, short_confident]) == [short_confident]


def test_same_priority_and_score_longer_wins() -> None:
    short, long = _span(2, 4), _span(0, 10)
    assert resolve_overlaps([short, long]) == [long]


def test_full_tie_earlier_start_wins() -> None:
    early, late = _span(0, 4), _span(2, 6)
    assert resolve_overlaps([late, early]) == [early]


def test_same_range_tie_is_broken_by_entity_type() -> None:
    person, org = _span(0, 4, entity_type="PERSON"), _span(0, 4, entity_type="ORG")
    assert resolve_overlaps([person, org]) == [org]


def test_adjacent_spans_do_not_overlap() -> None:
    left, right = _span(0, 4), _span(4, 8)
    assert resolve_overlaps([right, left]) == [left, right]


def test_zero_length_spans_are_dropped_and_block_nothing() -> None:
    empty = _span(2, 2, priority=100)
    covering = _span(0, 5)
    assert resolve_overlaps([empty, covering]) == [covering]
    assert resolve_overlaps([_span(3, 3)]) == []


def test_partially_overlapping_lower_priority_span_is_discarded_entirely() -> None:
    winner, loser = _span(0, 5, priority=60), _span(3, 10, priority=40)
    assert resolve_overlaps([loser, winner]) == [winner]


def test_a_rejected_span_does_not_block_later_ones() -> None:
    first = _span(0, 5, priority=60)
    rejected = _span(4, 8, priority=40)
    third = _span(7, 10, priority=20)
    assert resolve_overlaps([third, rejected, first]) == [first, third]


def test_input_may_be_any_iterable() -> None:
    spans = [_span(5, 9), _span(0, 3)]
    assert resolve_overlaps(iter(spans)) == [spans[1], spans[0]]


_SPANS = st.lists(
    st.builds(
        lambda start, length, priority, score, entity_type, source: _span(
            start,
            start + length,
            priority=priority,
            score=score,
            entity_type=entity_type,
            source=source,
        ),
        st.integers(min_value=0, max_value=30),
        st.integers(min_value=0, max_value=10),
        st.sampled_from([20, 40, 60, 80, 100]),
        st.sampled_from([0.5, 0.9, 1.0]),
        st.sampled_from(["ORG", "PERSON"]),
        st.sampled_from(["a", "b"]),
    ),
    max_size=25,
)


@given(_SPANS)
def test_output_is_sorted_pairwise_disjoint_and_a_subset_of_the_input(spans: list[Span]) -> None:
    resolved = resolve_overlaps(spans)
    assert resolved == sorted(resolved, key=lambda span: span.start)
    assert all(not _overlap(a, b) for i, a in enumerate(resolved) for b in resolved[i + 1 :])
    assert all(span in spans and span.start < span.end for span in resolved)


@given(_SPANS)
def test_the_first_span_in_resolution_order_is_always_kept(spans: list[Span]) -> None:
    candidates = [span for span in spans if span.start < span.end]
    resolved = resolve_overlaps(spans)
    if candidates:
        assert min(candidates, key=_order_key) in resolved
        assert max(span.priority for span in resolved) == max(s.priority for s in candidates)


@given(_SPANS)
def test_every_dropped_span_overlaps_a_kept_one(spans: list[Span]) -> None:
    resolved = resolve_overlaps(spans)
    for span in spans:
        if span.start < span.end and span not in resolved:
            assert any(_overlap(span, kept) for kept in resolved)


@given(_SPANS, st.randoms(use_true_random=False))
def test_input_order_does_not_change_the_result(spans: list[Span], rng: random.Random) -> None:
    shuffled = list(spans)
    rng.shuffle(shuffled)
    assert resolve_overlaps(shuffled) == resolve_overlaps(spans)
