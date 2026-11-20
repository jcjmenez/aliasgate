"""Company dictionary detector: user-provided terms and their aliases, matched as whole terms
(architecture §3.2)."""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from aliasgate.mapping import normalize_key
from aliasgate.types import PRIORITY_DICTIONARY, Span, validate_entity_type

_SOURCE = "dictionary"

MAX_ALIASES: Final = 25  # the term and its aliases get 26 distinct placeholders: no letter, B-Z


@dataclass(frozen=True, slots=True)
class Term:
    """One dictionary entry; its repr hides the term and the aliases, which are confidential."""

    term: str = field(repr=False)
    entity_type: str
    aliases: tuple[str, ...] = field(default=(), repr=False)


@dataclass(frozen=True, slots=True)
class _Surface:
    """A term or an alias to match, with the type and the link its spans carry."""

    text: str = field(repr=False)
    key: str = field(repr=False)
    entity_type: str
    link: str | None


def _body(term: str) -> str:
    return r"\s+".join(re.escape(token) for token in term.split())


def _shape(text: str) -> tuple[int, ...]:
    return tuple(len(token) for token in text.split())


def _keys(position: int, term: Term) -> list[str]:
    """The keys of the term and of its aliases, in that order, after checking the entry alone."""
    key = normalize_key(term.term)
    if not key:
        raise ValueError(f"term {position}: the term is empty")
    try:
        validate_entity_type(term.entity_type)
    except ValueError as exc:
        raise ValueError(f"term {position}: {exc}") from None
    if len(term.aliases) > MAX_ALIASES:
        raise ValueError(f"term {position} has more than {MAX_ALIASES} aliases")
    keys = [key]
    for index, alias in enumerate(term.aliases, start=1):
        alias_key = normalize_key(alias)
        if not alias_key:
            raise ValueError(f"term {position}, alias {index}: the alias is empty")
        if alias_key == key:
            raise ValueError(f"term {position}, alias {index} repeats the term")
        if alias_key in keys:  # keys[n] is alias n
            raise ValueError(
                f"term {position}, alias {index} repeats alias {keys.index(alias_key)}"
            )
        keys.append(alias_key)
    return keys


class DictionaryDetector:
    """Finds dictionary terms and aliases as whole terms, case-insensitively, with flexible
    whitespace.

    The same semantics as `build_term_pattern`; where several surfaces match at one position,
    the longest wins. The spans of an entry with aliases carry the link "dictionary:<position>",
    so the term and its aliases share one entity number; an entry without aliases gives spans
    without a link, as before aliases existed. Error messages name entry positions (1-based),
    never terms or aliases.
    """

    name = "dictionary"

    def __init__(self, terms: Sequence[Term]) -> None:
        owners: dict[str, tuple[int, Term]] = {}  # key -> the entry that has it
        surfaces: list[_Surface] = []
        for position, term in enumerate(terms, start=1):
            keys = _keys(position, term)
            first = owners.get(keys[0])
            if first is not None and normalize_key(first[1].term) == keys[0]:
                if first[1].entity_type != term.entity_type:
                    raise ValueError(
                        f"terms {first[0]} and {position} are the same term with different "
                        "entity types"
                    )
                if not first[1].aliases and not term.aliases:
                    continue  # the same entry twice: kept from v0.1.0, it adds nothing
            for key in keys:
                if key in owners:
                    raise ValueError(f"terms {owners[key][0]} and {position} share a term or alias")
            link = f"{_SOURCE}:{position}" if term.aliases else None
            for key, text in zip(keys, (term.term, *term.aliases), strict=True):
                owners[key] = (position, term)
                surfaces.append(_Surface(text, key, term.entity_type, link))
        ordered = sorted(surfaces, key=lambda surface: (-len(surface.key), surface.key))
        self._by_key = {surface.key: surface for surface in ordered}
        self._by_shape: dict[tuple[int, ...], list[_Surface]] = {}
        for surface in ordered:
            self._by_shape.setdefault(_shape(surface.text), []).append(surface)
        self._pattern = (
            re.compile(
                rf"(?<!\w)(?:{'|'.join(_body(surface.text) for surface in ordered)})(?!\w)",
                re.IGNORECASE,
            )
            if ordered
            else None
        )

    def detect(self, text: str) -> list[Span]:
        """One span per match, with its entry's type and link, priority 80 and score 1.0."""
        if self._pattern is None:
            return []
        spans = []
        for match in self._pattern.finditer(text):
            surface = self._surface_of(match.group())
            spans.append(
                Span(
                    match.start(),
                    match.end(),
                    surface.entity_type,
                    1.0,
                    _SOURCE,
                    PRIORITY_DICTIONARY,
                    surface.link,
                )
            )
        return spans

    def _surface_of(self, matched: str) -> _Surface:
        surface = self._by_key.get(normalize_key(matched))
        if surface is not None:
            return surface
        # re.IGNORECASE equates a few letters that casefold keeps apart (I with U+0130 and
        # U+0131), so the key lookup can miss. Case-insensitive matching is per character, so
        # the matching surface has the same token lengths; take the first in alternation order.
        for candidate in self._by_shape.get(_shape(matched), ()):
            if re.fullmatch(_body(candidate.text), matched, re.IGNORECASE):
                return candidate
        raise RuntimeError("a dictionary match corresponds to no term")  # pragma: no cover
