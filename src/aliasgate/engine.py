"""Pseudonymisation engine (architecture §4) and placeholder restoration (§5.3)."""

import dataclasses
import json
import re
import time
from bisect import bisect_left
from collections import Counter, abc
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from aliasgate.common_words import COMMON_WORDS
from aliasgate.detectors.literal import LiteralDetector
from aliasgate.mapping import Entity, Mapping, normalize_key
from aliasgate.names import is_person, name_parts, name_words
from aliasgate.orgs import OrgWord, OrgWordKind, bare_name, org_words
from aliasgate.placeholders import TOLERANT, parse_match
from aliasgate.resolve import resolve_overlaps
from aliasgate.terms import build_term_pattern, starts_upper
from aliasgate.types import LITERAL, ORG, PERSON, PRIORITY_PROPAGATION, Detector, Span

# Literal keys are the exact surface behind a prefix, so that "[PERSON_1]" and "[person_1]"
# in the input stay two entities and both restore byte for byte (architecture §4.4).
_LITERAL_KEY_PREFIX = "\x00literal:"

_PROPAGATION_SOURCE = "propagation"

# A replaced range and its entity. The span carries the entity's type and the detector that
# found it ("propagation" for propagated ones).
type _Occurrence = tuple[Span, Entity]
type _Family = tuple[str, int]  # the type and number that an entity and its variants share


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
    With `person_tokens`, the shorter forms of a detected person's name become variants of
    that person, and with `org_tokens` those of a detected organisation's name become variants
    of that organisation (architecture §4.3).
    """

    def __init__(
        self,
        detectors: Sequence[Detector],
        *,
        allow_list: Iterable[str] = (),
        propagate: bool = True,
        min_propagation_length: int = 3,
        person_tokens: bool = False,
        org_tokens: bool = False,
    ) -> None:
        if min_propagation_length < 1:
            raise ValueError("min_propagation_length must be at least 1")
        self._detectors: tuple[Detector, ...] = (LiteralDetector(), *detectors)
        self._allow_keys = frozenset(normalize_key(term) for term in allow_list) - {""}
        self._propagate = propagate
        self._min_propagation_length = min_propagation_length
        self._person_tokens = person_tokens
        self._org_tokens = org_tokens

    @property
    def detectors(self) -> tuple[Detector, ...]:
        """The detectors in the order they run; the literal detector comes first."""
        return self._detectors

    def analyze(self, texts: Sequence[str]) -> list[list[Span]]:
        """The spans `pseudonymize` replaces in each text, sorted by start, without replacing.

        Detected spans keep their detector's score, source and priority but carry the type of
        the entity they are replaced as (that of its first occurrence); propagated spans have
        source "propagation". Fails closed like `pseudonymize`.
        """
        return [[span for span, _ in found] for found in self._analyze(tuple(texts))[1]]

    def pseudonymize(self, texts: Sequence[str]) -> PseudonymizedBatch:
        """Pseudonymise all texts of one request as a batch (architecture §4.3)."""
        batch = tuple(texts)
        mapping, occurrences, seconds = self._analyze(batch)
        counts = Counter(
            (span.entity_type, span.source) for found in occurrences for span, _ in found
        )
        return PseudonymizedBatch(
            tuple(_replace(text, found) for text, found in zip(batch, occurrences, strict=True)),
            mapping,
            BatchStats(dict(counts), seconds),
        )

    def _analyze(
        self, batch: tuple[str, ...]
    ) -> tuple[Mapping, list[list[_Occurrence]], dict[str, float]]:
        """Detect, assign placeholders and propagate: everything but the replacement.

        Returns the mapping, each text's occurrences sorted by start, and detector timings.
        `analyze` and `pseudonymize` are both views of this one result, so they cannot drift.
        """
        seconds = dict.fromkeys((detector.name for detector in self._detectors), 0.0)
        resolved = [self._detect(text, seconds) for text in batch]
        mapping = Mapping()
        linker = (
            _NameLinker(
                mapping,
                batch,
                resolved,
                self._allow_keys,
                self._min_propagation_length,
                persons=self._person_tokens,
                orgs=self._org_tokens,
            )
            if self._person_tokens or self._org_tokens
            else None
        )
        occurrences: list[list[_Occurrence]] = []
        for index, (text, spans) in enumerate(zip(batch, resolved, strict=True)):
            found = []
            for span in spans:
                surface = text[span.start : span.end]
                entity = (
                    _assign(mapping, span, surface)
                    if linker is None
                    else linker.assign(span, surface)
                )
                found.append((dataclasses.replace(span, entity_type=entity.entity_type), entity))
            occurrences.append(found)
            if linker is not None and self._propagate:
                linker.find_words(index)
        if self._propagate:
            self._propagate_entities(mapping, batch, occurrences)
        for found in occurrences:
            found.sort(key=lambda occurrence: occurrence[0].start)
        return mapping, occurrences, seconds

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
        occupied = [_Occupied((span.start, span.end) for span, _ in found) for found in occurrences]
        for entity in entities:
            pattern = build_term_pattern(entity.surface)
            for text, taken, found in zip(texts, occupied, occurrences, strict=True):
                for match in pattern.finditer(text):
                    if entity.capitalised and not starts_upper(match.group()):
                        continue
                    if taken.claim(match.start(), match.end()):
                        span = Span(
                            match.start(),
                            match.end(),
                            entity.entity_type,
                            1.0,
                            _PROPAGATION_SOURCE,
                            PRIORITY_PROPAGATION,
                        )
                        found.append((span, entity))


def _assign(mapping: Mapping, span: Span, surface: str) -> Entity:
    if span.entity_type == LITERAL:
        return mapping.assign(LITERAL, surface, key=_LITERAL_KEY_PREFIX + surface)
    return mapping.assign(span.entity_type, surface, link=span.link)


def _family(entity: Entity) -> _Family:
    entity_type, number, _ = parse_match(next(TOLERANT.finditer(entity.placeholder)))
    return entity_type, number


class _Bounds:
    """The resolved spans of one text, to tell whether a range overlaps any of them."""

    def __init__(self, spans: Sequence[Span]) -> None:  # sorted by start, disjoint
        self._starts = [span.start for span in spans]
        self._ends = [span.end for span in spans]

    def overlaps(self, start: int, end: int) -> bool:
        index = bisect_left(self._starts, end)
        return index > 0 and self._ends[index - 1] > start


@dataclass(slots=True)
class _Candidate:
    """A name word, name part or bare name nobody detected, pending until it occurs on its own."""

    key: str
    pattern: re.Pattern[str]
    lower: bool  # lower-case eligible: it also occurs on its own where it starts in lower case
    searched: int = 0  # texts before this index have been searched


# The two kinds of candidates of step 2b, in the order they are assigned on a tie.
_PERSON_WORD, _ORG_WORD = 0, 1


class _NameLinker:
    """Links the shorter forms of a person's or an organisation's name to it, for one batch
    (architecture §4.3, steps 2a and 2b).

    A person is a PERSON entity for which `names.is_person` holds; its forms are the contiguous
    runs of its name tokens and each name part of those tokens. An organisation is any ORG
    entity; its forms are the runs of at least two of its content words and each of its
    distinctive words, and in step 2b also its bare name. Allow-listed words are never forms.
    A word that is a one-word form of a person is never an organisation's (persons first).
    """

    def __init__(
        self,
        mapping: Mapping,
        texts: tuple[str, ...],
        resolved: Sequence[Sequence[Span]],
        allow_keys: frozenset[str],
        min_length: int,
        *,
        persons: bool,
        orgs: bool,
    ) -> None:
        self._mapping = mapping
        self._texts = texts
        self._bounds = [_Bounds(spans) for spans in resolved]
        self._allow_keys = allow_keys
        self._min_length = min_length
        self._persons = persons
        self._orgs = orgs
        self._first: dict[_Family, Entity] = {}  # in order of assignment
        # Every form of a person (a contiguous run of its name tokens, or one of their name
        # parts as a form of one word) -> the families of such persons.
        self._runs: dict[tuple[str, ...], set[_Family]] = {}
        self._pending: dict[str, _Candidate] = {}
        # Every form of an organisation for step 2a (a run of at least two of its content words,
        # or one of its distinctive words) -> the families of such organisations.
        self._org_runs: dict[tuple[str, ...], set[_Family]] = {}
        # The key of every distinctive word and bare name of an organisation -> its families.
        self._org_names: dict[str, set[_Family]] = {}
        self._org_pending: dict[str, _Candidate] = {}

    def assign(self, span: Span, surface: str) -> Entity:
        """Step 2a: assign a resolved span, as a variant of the one family of persons, or of
        organisations, that have its words as one of their forms, when it is an unlinked PERSON
        or ORG span with a new key."""
        family = None
        if span.link is None and self._mapping.get(normalize_key(surface)) is None:
            families = self._families(span.entity_type, surface)
            if len(families) == 1:
                (family,) = families
        known = len(self._mapping)
        if family is None:
            entity = _assign(self._mapping, span, surface)
        else:
            entity = self._mapping.assign(span.entity_type, surface, variant_of=self._first[family])
        if len(self._mapping) > known:
            self._record(entity)
        return entity

    def find_words(self, index: int) -> None:
        """Step 2b, after the walk of text `index`: assign every pending candidate that occurs on
        its own in texts 0..index. Each candidate searches each text at most once."""
        found: list[tuple[int, int, str, int, str]] = []
        for kind, pending in ((_PERSON_WORD, self._pending), (_ORG_WORD, self._org_pending)):
            for candidate in list(pending.values()):
                if self._mapping.get(candidate.key) is not None:
                    del pending[candidate.key]
                    continue
                for position in range(candidate.searched, index + 1):
                    occurrence = self._occurrence(candidate, position)
                    if occurrence is not None:
                        found.append((position, occurrence[0], candidate.key, kind, occurrence[1]))
                        break
                candidate.searched = index + 1
        for _, _, key, kind, surface in sorted(found):
            if kind == _PERSON_WORD:
                candidate = self._pending.pop(key)
                entity_type, families = PERSON, self._runs.get((key,), set())
            else:
                candidate = self._org_pending.pop(key)
                if (key,) in self._runs:
                    continue  # a person has the word: persons first
                entity_type, families = ORG, self._org_names[key]
            variant_of = self._first[next(iter(families))] if len(families) == 1 else None
            entity = self._mapping.assign(
                entity_type, surface, variant_of=variant_of, capitalised=not candidate.lower
            )
            self._record(entity)

    def _occurrence(self, candidate: _Candidate, position: int) -> tuple[int, str] | None:
        """The start and surface of the first match of the candidate on its own in a text:
        capitalised unless the candidate is lower-case eligible, with the candidate's key, and
        outside every span resolved for the text."""
        bounds = self._bounds[position]
        for match in candidate.pattern.finditer(self._texts[position]):
            surface = match.group()
            if (
                (candidate.lower or starts_upper(surface))
                and normalize_key(surface) == candidate.key
                and not bounds.overlaps(match.start(), match.end())
            ):
                return match.start(), surface
        return None

    def _families(self, entity_type: str, surface: str) -> set[_Family]:
        """The families of the persons, or organisations, that have the words of a PERSON, or
        ORG, surface as one of their forms."""
        if entity_type == PERSON and self._persons:
            return self._runs.get(tuple(key for key, _ in self._words(surface)), set())
        if entity_type == ORG and self._orgs:
            return self._org_runs.get(tuple(word.key for word in self._content(surface)), set())
        return set()

    def _words(self, surface: str) -> tuple[tuple[str, str], ...]:
        return tuple(
            (key, word)
            for key, word in name_words(surface, min_length=self._min_length)
            if key not in self._allow_keys
        )

    def _content(self, surface: str) -> list[OrgWord]:
        """The content words of an ORG surface, without the allow-listed ones."""
        return [
            word
            for word in org_words(surface, min_length=self._min_length)
            if word.content and word.key not in self._allow_keys
        ]

    def _record(self, entity: Entity) -> None:
        """Note a new entity's family and, for a person or an organisation, its forms and
        candidates. The allow list removes forms; it never decides whether an entity is a
        person."""
        family = _family(entity)
        self._first.setdefault(family, entity)
        if entity.entity_type == PERSON and self._persons:
            self._record_person(entity, family)
        elif entity.entity_type == ORG and self._orgs:
            self._record_org(entity, family)

    def _record_person(self, entity: Entity, family: _Family) -> None:
        if not is_person(entity.surface, min_length=self._min_length):
            return
        words = self._words(entity.surface)
        keys = tuple(key for key, _ in words)
        for start in range(len(keys)):
            for end in range(start + 1, len(keys) + 1):
                self._runs.setdefault(keys[start:end], set()).add(family)
        parts = [
            (key, part)
            for _, word in words
            for key, part in name_parts(word, min_length=self._min_length)
            if key not in self._allow_keys
        ]
        for key, _ in parts:
            self._runs.setdefault((key,), set()).add(family)
        for key, word in (*words, *parts):
            if key not in self._pending and self._mapping.get(key) is None:
                lower = key not in COMMON_WORDS and starts_upper(word)
                self._pending[key] = _Candidate(key, build_term_pattern(word), lower)

    def _record_org(self, entity: Entity, family: _Family) -> None:
        content = self._content(entity.surface)
        keys = tuple(word.key for word in content)
        for start in range(len(keys)):
            for end in range(start + 2, len(keys) + 1):
                self._org_runs.setdefault(keys[start:end], set()).add(family)
        # Each distinctive word and the bare name, as written, and whether it is lower-case
        # eligible.
        names = [
            (word.key, word.word, starts_upper(word.word))
            for word in content
            if word.kind is OrgWordKind.DISTINCTIVE
        ]
        for key, _, _ in names:
            self._org_runs.setdefault((key,), set()).add(family)
        bare = bare_name(entity.surface, min_length=self._min_length)
        if bare is not None and normalize_key(bare) not in {entity.key, *self._allow_keys}:
            names.append((normalize_key(bare), bare, False))
        for key, written, lower in names:
            self._org_names.setdefault(key, set()).add(family)
            if (
                key not in self._org_pending
                and self._mapping.get(key) is None
                and (key,) not in self._runs
            ):
                self._org_pending[key] = _Candidate(key, build_term_pattern(written), lower)


def _replace(text: str, occurrences: list[_Occurrence]) -> str:
    pieces: list[str] = []
    position = 0
    for span, entity in occurrences:  # sorted by start
        pieces.append(text[position : span.start])
        pieces.append(entity.placeholder)
        position = span.end
    pieces.append(text[position:])
    return "".join(pieces)


def restore(text: str, mapping: Mapping, *, json_escape: bool = False) -> str:
    """Replace every placeholder of `mapping` in `text` by its canonical surface.

    One left-to-right pass: restored values are not scanned again, and placeholders that are
    not in the mapping, an unknown variant of a known number included, are left exactly as they
    are. With `json_escape`, surfaces are inserted escaped for the inside of a JSON string.
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
