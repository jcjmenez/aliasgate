"""Per-request mapping between placeholders and canonical surfaces, held in memory only."""

import unicodedata
from dataclasses import dataclass, field
from typing import Final

from aliasgate.placeholders import format_placeholder
from aliasgate.types import validate_entity_type


def normalize_key(surface: str) -> str:
    """NFKC, casefold, collapse whitespace runs to one space, strip."""
    return " ".join(unicodedata.normalize("NFKC", surface).casefold().split())


_NOT_SERIALISABLE: Final = "the mapping and its entities are never serialised or copied"


@dataclass(frozen=True, slots=True)
class Entity:
    """One entity of a mapping; its repr shows only the type and the placeholder.

    It cannot be pickled or copied, and its state cannot be read, because it holds a surface.
    """

    key: str = field(repr=False)
    entity_type: str
    surface: str = field(repr=False)  # canonical surface: the first one seen
    placeholder: str

    def __getstate__(self) -> object:
        raise TypeError(_NOT_SERIALISABLE)

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError(_NOT_SERIALISABLE)


class Mapping:
    """Placeholder-to-surface table of one request; numbers start at 1 per entity type.

    Not thread-safe: use one instance per request. Its repr reveals only the entity count, and
    it cannot be pickled or copied.
    """

    __slots__ = ("_by_key", "_by_placeholder", "_counters")

    def __init__(self) -> None:
        self._by_key: dict[str, Entity] = {}
        self._by_placeholder: dict[tuple[str, int], Entity] = {}
        self._counters: dict[str, int] = {}

    def assign(self, entity_type: str, surface: str, *, key: str | None = None) -> Entity:
        """Return the entity for the key (default: normalize_key(surface)), creating it with the
        next number for `entity_type` if the key is new. For an existing key the stored entity
        is returned unchanged, even if `entity_type` or the surface's case differ.

        Raises ValueError for an invalid entity type, an empty key or an empty surface."""
        validate_entity_type(entity_type)
        if key is None:
            key = normalize_key(surface)
        if not key:
            raise ValueError("entity key must not be empty")
        if not surface:
            raise ValueError("entity surface must not be empty")
        existing = self._by_key.get(key)
        if existing is not None:
            return existing
        number = self._counters.get(entity_type, 0) + 1
        entity = Entity(
            key=key,
            entity_type=entity_type,
            surface=surface,
            placeholder=format_placeholder(entity_type, number),
        )
        self._counters[entity_type] = number
        self._by_key[key] = entity
        self._by_placeholder[(entity_type, number)] = entity
        return entity

    def get(self, key: str) -> Entity | None:
        """Return the entity recorded for `key`, or None."""
        return self._by_key.get(key)

    def lookup(self, entity_type: str, number: int) -> str | None:
        """Return the canonical surface for a placeholder, or None."""
        entity = self._by_placeholder.get((entity_type, number))
        return None if entity is None else entity.surface

    def entities(self) -> tuple[Entity, ...]:
        """All entities in order of assignment."""
        return tuple(self._by_key.values())

    def __len__(self) -> int:
        return len(self._by_key)

    def __repr__(self) -> str:
        return f"Mapping(entities={len(self._by_key)})"

    def __getstate__(self) -> object:
        raise TypeError(_NOT_SERIALISABLE)

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError(_NOT_SERIALISABLE)
