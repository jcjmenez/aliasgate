"""Overlap resolution between the spans that detectors report for one text."""

from bisect import bisect_left
from collections.abc import Iterable

from aliasgate.types import Span


def _resolution_order(span: Span) -> tuple[int, float, int, int, str, str]:
    # Architecture §3.3, step 2. The trailing keys only make the order total.
    return (
        -span.priority,
        -span.score,
        -(span.end - span.start),
        span.start,
        span.entity_type,
        span.source,
    )


def resolve_overlaps(spans: Iterable[Span]) -> list[Span]:
    """Return non-overlapping spans sorted by start, chosen as in architecture §3.3."""
    accepted: list[Span] = []  # pairwise disjoint, sorted by start
    starts: list[int] = []
    for span in sorted((span for span in spans if span.start < span.end), key=_resolution_order):
        # Only the accepted span with the greatest start before span.end can overlap it.
        index = bisect_left(starts, span.end)
        if index > 0 and accepted[index - 1].end > span.start:
            continue
        accepted.insert(index, span)
        starts.insert(index, span.start)
    return accepted
