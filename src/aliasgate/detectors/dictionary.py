"""Company dictionary detector: user-provided terms, matched as whole terms (architecture §3.2)."""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from aliasgate.mapping import normalize_key
from aliasgate.types import PRIORITY_DICTIONARY, Span, validate_entity_type

_SOURCE = "dictionary"


@dataclass(frozen=True, slots=True)
class Term:
    """One dictionary entry; its repr hides the term, which is confidential."""

    term: str = field(repr=False)
    entity_type: str


def _body(term: str) -> str:
    return r"\s+".join(re.escape(token) for token in term.split())


def _shape(text: str) -> tuple[int, ...]:
    return tuple(len(token) for token in text.split())


class DictionaryDetector:
    """Finds dictionary terms as whole terms, case-insensitively, with flexible whitespace.

    The same semantics as `build_term_pattern`; where several terms match at one position, the
    longest wins. Error messages name term positions (1-based), never term content.
    """

    name = "dictionary"

    def __init__(self, terms: Sequence[Term]) -> None:
        unique: dict[str, tuple[int, Term]] = {}
        for position, term in enumerate(terms, start=1):
            key = normalize_key(term.term)
            if not key:
                raise ValueError(f"term {position}: the term is empty")
            try:
                validate_entity_type(term.entity_type)
            except ValueError as exc:
                raise ValueError(f"term {position}: {exc}") from None
            first = unique.setdefault(key, (position, term))
            if first[1].entity_type != term.entity_type:
                raise ValueError(
                    f"terms {first[0]} and {position} are the same term with different entity types"
                )
        ordered = sorted(
            (term for _, term in unique.values()),
            key=lambda term: (-len(normalize_key(term.term)), normalize_key(term.term)),
        )
        self._types = {normalize_key(term.term): term.entity_type for term in ordered}
        self._by_shape: dict[tuple[int, ...], list[Term]] = {}
        for term in ordered:
            self._by_shape.setdefault(_shape(term.term), []).append(term)
        self._pattern = (
            re.compile(
                rf"(?<!\w)(?:{'|'.join(_body(term.term) for term in ordered)})(?!\w)",
                re.IGNORECASE,
            )
            if ordered
            else None
        )

    def detect(self, text: str) -> list[Span]:
        """One span per match, typed as its term, with priority 80 and score 1.0."""
        if self._pattern is None:
            return []
        return [
            Span(
                match.start(),
                match.end(),
                self._type_of(match.group()),
                1.0,
                _SOURCE,
                PRIORITY_DICTIONARY,
            )
            for match in self._pattern.finditer(text)
        ]

    def _type_of(self, surface: str) -> str:
        entity_type = self._types.get(normalize_key(surface))
        if entity_type is not None:
            return entity_type
        # re.IGNORECASE equates a few letters that casefold keeps apart (I with U+0130 and
        # U+0131), so the key lookup can miss. Case-insensitive matching is per character, so
        # the matching term has the same token lengths; take the first in alternation order.
        for term in self._by_shape.get(_shape(surface), ()):
            if re.fullmatch(_body(term.term), surface, re.IGNORECASE):
                return term.entity_type
        raise RuntimeError("a dictionary match corresponds to no term")  # pragma: no cover
