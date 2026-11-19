"""Outbound leak audit (architecture §8): the gateway checks its own work before forwarding."""

import re
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from aliasgate.mapping import Mapping
from aliasgate.terms import build_term_pattern, term_body
from aliasgate.types import LITERAL


@dataclass(frozen=True, slots=True)
class AuditResult:
    """How many entities of the mapping were found in the outbound body, and their types.

    Never which ones: it holds no surface.
    """

    leaked_entities: int
    leaked_types: tuple[str, ...]


def audit_outbound(payload: object, mapping: Mapping, *, min_length: int) -> AuditResult:
    """Search every string value of the outbound JSON payload for the entities of the mapping.

    Every string value is searched, wherever it is, so a field no dialect processes is caught
    too; keys are not. `LITERAL` entities and entities whose key is shorter than `min_length`
    are skipped. An entity is found when its whole-term pattern matches (case-insensitive,
    flexible whitespace, as propagation searches), or when its key matches the string after
    NFKC normalisation and case folding, which also catches variants with the same key that
    the pattern misses, such as "STRASSE" for "Straße".
    """
    entities = [
        entity
        for entity in mapping.entities()
        if entity.entity_type != LITERAL and len(entity.key) >= min_length
    ]
    if not entities:
        return AuditResult(0, ())
    strings = list(_string_values(payload))
    folded = [_fold(text) for text in strings]
    # One alternation per side finds whether any entity occurs, in one pass per string. Only
    # when one does is each entity searched on its own, to count them.
    longest_first = sorted(entities, key=lambda entity: -len(entity.surface))
    any_term = _whole_terms((term_body(entity.surface) for entity in longest_first), re.IGNORECASE)
    any_key = _whole_terms(term_body(entity.key) for entity in longest_first)
    if not _search(any_term, strings) and not _search(any_key, folded):
        return AuditResult(0, ())
    leaked = [
        entity
        for entity in entities
        if _search(build_term_pattern(entity.surface), strings)
        or _search(_whole_terms([term_body(entity.key)]), folded)
    ]
    return AuditResult(len(leaked), tuple(sorted({entity.entity_type for entity in leaked})))


def _whole_terms(bodies: Iterable[str], flags: int = 0) -> re.Pattern[str]:
    """Any of the bodies as a whole term. Folded text is matched by folded keys, without
    IGNORECASE."""
    return re.compile(rf"(?<!\w)(?:{'|'.join(bodies)})(?!\w)", flags)


def _search(pattern: re.Pattern[str], strings: list[str]) -> bool:
    return any(pattern.search(text) for text in strings)


def _fold(text: str) -> str:
    """The text as keys are made (architecture §4.2), without collapsing whitespace."""
    return unicodedata.normalize("NFKC", text).casefold()


def _string_values(payload: object) -> Iterator[str]:
    """Every string value of a JSON value, iteratively, so that nesting depth is no limit."""
    stack = [payload]
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
