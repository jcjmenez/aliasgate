"""Pseudonymisation engine (architecture §4) and placeholder restoration (§5.3)."""

import json
import re
import time
from bisect import bisect_left
from collections import Counter, abc
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from aliasgate.detectors.literal import LiteralDetector
from aliasgate.mapping import Entity, Mapping, normalize_key
from aliasgate.placeholders import TOLERANT, parse_match
from aliasgate.resolve import resolve_overlaps
from aliasgate.terms import build_term_pattern
from aliasgate.types import LITERAL, Detector, Span

# Literal keys are the exact surface behind a prefix, so that "[PERSON_1]" and "[person_1]"
# in the input stay two entities and both restore byte for byte (architecture §4.4).
_LITERAL_KEY_PREFIX = "\x00literal:"

_PROPAGATION_SOURCE = "propagation"

# (start, end, entity, source): a replaced range, its entity, and the detector that found it.
type _Occurrence = tuple[int, int, Entity, str]


@dataclass(frozen=True, slots=True)
class BatchStats:
    """Counts and timings of one batch, for metrics: no text, no surface.

    `entities_by_type_and_source` counts replaced occurrences by the entity's type and the
    detector name that found them ("propagation" for propagated ones). `detector_seconds` is
    the time each detector spent on the batch, by detector name.
    """

    entities_by_type_and_source: abc.Mapping[tuple[str, str], int]
    detector_seconds: abc.Mapping[str, float]


@dataclass(frozen=True, slots=True)
class PseudonymizedBatch:
    """Pseudonymised texts in input order, the mapping that restores them, and their stats."""

    texts: tuple[str, ...]
    mapping: Mapping
    stats: BatchStats


class DetectionError(Exception):
    """A detector failed. Carries the detector name; never carries text."""

    def __init__(self, detector_name: str, reason: str = "raised an exception") -> None:
        super().__init__(f"detector {detector_name!r} {reason}")
        self.detector_name = detector_name


class _Occupied:
    """Disjoint [start, end) ranges of one text."""

    def __init__(self, ranges: Iterable[tuple[int, int]]) -> None:
        self._ranges = sorted(ranges)
        self._starts = [start for start, _ in self._ranges]

    def claim(self, start: int, end: int) -> bool:
        """Record the range and return True, or return False if it overlaps a recorded one."""
        index = bisect_left(self._starts, end)
        if index > 0 and self._ranges[index - 1][1] > start:
            return False
        self._ranges.insert(index, (start, end))
        self._starts.insert(index, start)
        return True


class Pseudonymizer:
    """Replaces detected values with numbered placeholders, consistently across one batch.

    Deterministic: numbering depends only on the content and order of the texts. Fails
    closed: if any detector fails, `pseudonymize` raises DetectionError and returns nothing.
    """

    def __init__(
        self,
        detectors: Sequence[Detector],
        *,
        allow_list: Iterable[str] = (),
        propagate: bool = True,
        min_propagation_length: int = 3,
    ) -> None:
        if min_propagation_length < 1:
            raise ValueError("min_propagation_length must be at least 1")
        self._detectors: tuple[Detector, ...] = (LiteralDetector(), *detectors)
        self._allow_keys = frozenset(normalize_key(term) for term in allow_list) - {""}
        self._propagate = propagate
        self._min_propagation_length = min_propagation_length

    @property
    def detectors(self) -> tuple[Detector, ...]:
        """The detectors in the order they run; the literal detector comes first."""
        return self._detectors

    def pseudonymize(self, texts: Sequence[str]) -> PseudonymizedBatch:
        """Pseudonymise all texts of one request as a batch (architecture §4.3)."""
        batch = tuple(texts)
        seconds = dict.fromkeys((detector.name for detector in self._detectors), 0.0)
        resolved = [self._detect(text, seconds) for text in batch]
        mapping = Mapping()
        occurrences: list[list[_Occurrence]] = [
            [
                (
                    span.start,
                    span.end,
                    _assign(mapping, span, text[span.start : span.end]),
                    span.source,
                )
                for span in spans
            ]
            for text, spans in zip(batch, resolved, strict=True)
        ]
        if self._propagate:
            self._propagate_entities(mapping, batch, occurrences)
        counts = Counter(
            (entity.entity_type, source) for found in occurrences for _, _, entity, source in found
        )
        return PseudonymizedBatch(
            tuple(_replace(text, found) for text, found in zip(batch, occurrences, strict=True)),
            mapping,
            BatchStats(dict(counts), seconds),
        )

    def _detect(self, text: str, seconds: dict[str, float]) -> list[Span]:
        spans: list[Span] = []
        for detector in self._detectors:
            started = time.perf_counter()
            try:
                found = list(detector.detect(text))
            except Exception as exc:
                raise DetectionError(detector.name) from exc
            finally:
                seconds[detector.name] += time.perf_counter() - started
            if not all(isinstance(span, Span) and span.end <= len(text) for span in found):
                raise DetectionError(detector.name, "returned a span outside the text")
            spans.extend(found)
        return resolve_overlaps(span for span in spans if self._keep(span, text))

    def _keep(self, span: Span, text: str) -> bool:
        if span.entity_type == LITERAL:
            return True
        key = normalize_key(text[span.start : span.end])
        return bool(key) and key not in self._allow_keys

    def _propagate_entities(
        self, mapping: Mapping, texts: tuple[str, ...], occurrences: list[list[_Occurrence]]
    ) -> None:
        entities = sorted(
            (
                entity
                for entity in mapping.entities()
                if entity.entity_type != LITERAL and len(entity.key) >= self._min_propagation_length
            ),
            key=lambda entity: -len(entity.surface),
        )
        occupied = [_Occupied((start, end) for start, end, _, _ in found) for found in occurrences]
        for entity in entities:
            pattern = build_term_pattern(entity.surface)
            for text, taken, found in zip(texts, occupied, occurrences, strict=True):
                for match in pattern.finditer(text):
                    if taken.claim(match.start(), match.end()):
                        found.append((match.start(), match.end(), entity, _PROPAGATION_SOURCE))


def _assign(mapping: Mapping, span: Span, surface: str) -> Entity:
    if span.entity_type == LITERAL:
        return mapping.assign(LITERAL, surface, key=_LITERAL_KEY_PREFIX + surface)
    return mapping.assign(span.entity_type, surface)


def _replace(text: str, occurrences: list[_Occurrence]) -> str:
    pieces: list[str] = []
    position = 0
    for start, end, entity, _ in sorted(occurrences, key=lambda occurrence: occurrence[0]):
        pieces.append(text[position:start])
        pieces.append(entity.placeholder)
        position = end
    pieces.append(text[position:])
    return "".join(pieces)


def restore(text: str, mapping: Mapping, *, json_escape: bool = False) -> str:
    """Replace every placeholder of `mapping` in `text` by its canonical surface.

    One left-to-right pass: restored values are not scanned again, and placeholders that are
    not in the mapping are left exactly as they are. With `json_escape`, surfaces are inserted
    escaped for the inside of a JSON string.
    """

    def replacement(match: re.Match[str]) -> str:
        surface = mapping.lookup(*parse_match(match))
        if surface is None:
            return match.group()
        return json.dumps(surface, ensure_ascii=False)[1:-1] if json_escape else surface

    return TOLERANT.sub(replacement, text)


def count_unresolved(text: str, mapping: Mapping) -> int:
    """Number of TOLERANT matches in `text` that are not in the mapping."""
    return sum(
        1 for match in TOLERANT.finditer(text) if mapping.lookup(*parse_match(match)) is None
    )
