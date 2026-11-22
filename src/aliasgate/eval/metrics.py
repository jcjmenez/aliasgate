"""Detection quality of the whole engine on labelled examples: counts and ratios, never text.

The headline number is the leak rate: the share of gold characters that no predicted span
covers, whatever its type. Recall by overlap ignores the type on purpose, because a person
detected as an organisation is still protected.

Where gold spans carry entity ids, link scores count pairs of mentions within an example: two
mentions of one entity should share a placeholder family (type and number, the variant letter
ignored), and two mentions of different entities should not.
"""

from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field

from aliasgate.engine import Pseudonymizer, restore
from aliasgate.eval.corpus import Example, GoldSpan
from aliasgate.mapping import Entity, Mapping, normalize_key
from aliasgate.placeholders import TOLERANT, parse_match
from aliasgate.terms import build_term_pattern
from aliasgate.types import LITERAL, Span

_PROPAGATION_SOURCE = "propagation"

type _Family = tuple[str, int]  # placeholder type and number


def _ratio(numerator: int, denominator: int, *, empty: float) -> float:
    return numerator / denominator if denominator else empty


@dataclass(frozen=True, slots=True)
class TypeScore:
    """Counts for one entity type. Recall is 1.0 when there is no gold span of the type, and
    precision is 1.0 when nothing of the type is predicted."""

    entity_type: str
    gold: int
    predicted: int
    exact_tp: int  # gold spans predicted with the same start, end and type
    overlap_tp: int  # gold spans that any predicted span overlaps, of any type

    @property
    def recall_exact(self) -> float:
        """exact_tp / gold, or 1.0 without gold spans."""
        return _ratio(self.exact_tp, self.gold, empty=1.0)

    @property
    def recall_overlap(self) -> float:
        """overlap_tp / gold, or 1.0 without gold spans."""
        return _ratio(self.overlap_tp, self.gold, empty=1.0)

    @property
    def precision_exact(self) -> float:
        """exact_tp / predicted, or 1.0 when nothing is predicted."""
        return _ratio(self.exact_tp, self.predicted, empty=1.0)


@dataclass(frozen=True, slots=True)
class Miss:
    """A gold span with characters no predicted span covers: its position, never its text."""

    example_id: str
    start: int
    end: int
    entity_type: str
    leaked_chars: int


@dataclass(frozen=True, slots=True)
class LinkScore:
    """Pairwise link counts for one entity type, within examples. Recall and precision are 1.0
    when their denominator is 0."""

    entity_type: str
    mentions: int  # gold spans of the type
    placed: int  # of them, the ones with a family
    same_pairs: int  # pairs of placed mentions of one gold entity
    linked_pairs: int  # of those, the pairs in one family
    wrong_pairs: int  # pairs of placed mentions of two gold entities in one family

    @property
    def link_recall(self) -> float:
        """linked_pairs / same_pairs, or 1.0 without such pairs."""
        return _ratio(self.linked_pairs, self.same_pairs, empty=1.0)

    @property
    def link_precision(self) -> float:
        """linked_pairs / (linked_pairs + wrong_pairs), or 1.0 when both are 0."""
        return _ratio(self.linked_pairs, self.linked_pairs + self.wrong_pairs, empty=1.0)


@dataclass(frozen=True, slots=True)
class WrongMerge:
    """Two gold mentions of different entities in one placeholder family: positions only."""

    example_id: str
    first: tuple[int, int]
    second: tuple[int, int]
    entity_type: str


@dataclass(frozen=True, slots=True)
class Report:
    """Scores of one evaluation, by entity type, plus leak, round-trip and false-alarm counts.

    `entity_free` counts the examples without gold spans and `entity_free_flagged` those of
    them with at least one predicted span. With a type restriction, both count only spans of
    the selected types. `linking` has a score for each scored type with at least one entity
    id, sorted by type; `wrong_merges` lists the wrong pairs of those types, by example, then
    by first span, then by second span.
    """

    by_type: tuple[TypeScore, ...]
    leaked_chars: int  # characters of gold spans still present after pseudonymisation
    gold_chars: int
    round_trip_failures: int
    examples: int
    entity_free: int
    entity_free_flagged: int
    misses: tuple[Miss, ...]
    linking: tuple[LinkScore, ...] = ()
    wrong_merges: tuple[WrongMerge, ...] = ()

    @property
    def leak_rate(self) -> float:
        """leaked_chars / gold_chars, or 0.0 without gold spans."""
        return _ratio(self.leaked_chars, self.gold_chars, empty=0.0)

    @property
    def entity_free_flag_rate(self) -> float:
        """entity_free_flagged / entity_free, or 0.0 without such examples."""
        return _ratio(self.entity_free_flagged, self.entity_free, empty=0.0)


def evaluate(
    pseudonymizer: Pseudonymizer,
    examples: Sequence[Example],
    *,
    types: Collection[str] | None = None,
) -> Report:
    """Run the whole engine on each example as its own batch and score its final spans.

    With `types`, only gold and predicted spans of those types are scored, but a predicted
    span of any type still covers gold characters and gives a gold span its family.
    """
    gold_counts: Counter[str] = Counter()
    predicted_counts: Counter[str] = Counter()
    exact: Counter[str] = Counter()
    overlap: Counter[str] = Counter()
    leaked = gold_chars = failures = entity_free = flagged = 0
    misses: list[Miss] = []
    links = _LinkCounts()
    for example in examples:
        spans = pseudonymizer.analyze([example.text])[0]
        batch = pseudonymizer.pseudonymize([example.text])
        expected = expected_restoration(example.text, spans, batch.mapping)
        if restore(batch.texts[0], batch.mapping) != expected:
            failures += 1
        gold = [span for span in example.spans if types is None or span.entity_type in types]
        links.add(example, gold, spans, batch.mapping)
        predicted = [span for span in spans if types is None or span.entity_type in types]
        predicted_counts.update(span.entity_type for span in predicted)
        if not gold:
            entity_free += 1
            flagged += bool(predicted)
        for span in gold:
            covered = _covered(span, spans)
            gold_counts[span.entity_type] += 1
            gold_chars += span.end - span.start
            leaked += span.end - span.start - covered
            overlap[span.entity_type] += covered > 0
            exact[span.entity_type] += any(
                (found.start, found.end, found.entity_type)
                == (span.start, span.end, span.entity_type)
                for found in spans
            )
            if covered < span.end - span.start:
                misses.append(
                    Miss(
                        example.id,
                        span.start,
                        span.end,
                        span.entity_type,
                        span.end - span.start - covered,
                    )
                )
    by_type = tuple(
        TypeScore(name, gold_counts[name], predicted_counts[name], exact[name], overlap[name])
        for name in sorted(gold_counts.keys() | predicted_counts.keys())
    )
    return Report(
        by_type,
        leaked,
        gold_chars,
        failures,
        len(examples),
        entity_free,
        flagged,
        tuple(misses),
        *links.scores(),
    )


@dataclass(slots=True)
class _LinkCounts:
    """Accumulates pair counts over the examples of one evaluation."""

    mentions: Counter[str] = field(default_factory=Counter)
    placed: Counter[str] = field(default_factory=Counter)
    same: Counter[str] = field(default_factory=Counter)
    linked: Counter[str] = field(default_factory=Counter)
    wrong: Counter[str] = field(default_factory=Counter)
    with_ids: set[str] = field(default_factory=set)
    merges: list[WrongMerge] = field(default_factory=list)

    def add(
        self, example: Example, gold: Sequence[GoldSpan], spans: Sequence[Span], mapping: Mapping
    ) -> None:
        """Count the pairs of one example's scored gold spans; the example is one batch."""
        families = [_family(span, example.text, mapping) for span in spans]
        placed: list[tuple[GoldSpan, _Family]] = []
        for span in gold:
            self.mentions[span.entity_type] += 1
            if span.entity is not None:
                self.with_ids.add(span.entity_type)
            family = _gold_family(span, spans, families)
            if family is not None:
                self.placed[span.entity_type] += 1
                placed.append((span, family))
        for index, (first, first_family) in enumerate(placed):
            for second, second_family in placed[index + 1 :]:
                if first.entity_type != second.entity_type:
                    continue
                together = first_family == second_family
                if first.entity is not None and first.entity == second.entity:
                    self.same[first.entity_type] += 1
                    self.linked[first.entity_type] += together
                elif together:
                    self.wrong[first.entity_type] += 1
                    self.merges.append(
                        WrongMerge(
                            example.id,
                            (first.start, first.end),
                            (second.start, second.end),
                            first.entity_type,
                        )
                    )

    def scores(self) -> tuple[tuple[LinkScore, ...], tuple[WrongMerge, ...]]:
        """The link scores of the types with an entity id, and their wrong merges."""
        scores = tuple(
            LinkScore(
                name,
                self.mentions[name],
                self.placed[name],
                self.same[name],
                self.linked[name],
                self.wrong[name],
            )
            for name in sorted(self.with_ids)
        )
        return scores, tuple(m for m in self.merges if m.entity_type in self.with_ids)


def _family(span: Span, text: str, mapping: Mapping) -> _Family | None:
    """The type and number of the placeholder `span` is replaced with, as `restore` reads it
    back; None for a placeholder literal or a span without an entity."""
    entity = (
        None
        if span.entity_type == LITERAL
        else _entity_of(span, text[span.start : span.end], mapping)
    )
    if entity is None:
        return None
    entity_type, number, _ = parse_match(next(TOLERANT.finditer(entity.placeholder)))
    return entity_type, number


def _gold_family(
    gold: GoldSpan, spans: Sequence[Span], families: Sequence[_Family | None]
) -> _Family | None:
    """The family of the predicted span covering most of `gold`; the earlier one on a tie."""
    best, family = 0, None
    for span, candidate in zip(spans, families, strict=True):
        covered = min(gold.end, span.end) - max(gold.start, span.start)
        if covered > best:
            best, family = covered, candidate
    return family


def _covered(gold: GoldSpan, spans: Sequence[Span]) -> int:
    """Characters of `gold` inside predicted spans; these never overlap one another."""
    return sum(max(0, min(gold.end, span.end) - max(gold.start, span.start)) for span in spans)


def expected_restoration(text: str, spans: Sequence[Span], mapping: Mapping) -> str | None:
    """What `restore(pseudonymize(text))` must give: the text with every span `analyze`
    reports rendered as its entity's canonical surface, a placeholder literal as itself.

    A detected span's entity is the one with its key; a propagated span's is the longest
    entity of its type whose term pattern matches it, as propagation chose it (keys and
    patterns differ for a few characters, such as a dotted capital I). None if a span has no
    entity in the mapping.
    """
    pieces: list[str] = []
    position = 0
    for span in spans:
        surface = text[span.start : span.end]
        if span.entity_type == LITERAL:
            canonical: str | None = surface
        else:
            entity = _entity_of(span, surface, mapping)
            canonical = entity.surface if entity is not None else None
        if canonical is None:
            return None
        pieces.append(text[position : span.start])
        pieces.append(canonical)
        position = span.end
    pieces.append(text[position:])
    return "".join(pieces)


def _entity_of(span: Span, surface: str, mapping: Mapping) -> Entity | None:
    if span.source != _PROPAGATION_SOURCE:
        return mapping.get(normalize_key(surface))
    candidates = sorted(
        (entity for entity in mapping.entities() if entity.entity_type == span.entity_type),
        key=lambda entity: -len(entity.surface),
    )
    return next(
        (
            entity
            for entity in candidates
            if build_term_pattern(entity.surface).fullmatch(surface) is not None
        ),
        None,
    )
