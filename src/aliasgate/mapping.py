"""Per-request mapping between placeholders and canonical surfaces, held in memory only."""

import unicodedata
from dataclasses import dataclass, field
from typing import Final

from aliasgate.placeholders import VARIANT_LETTERS, format_placeholder
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
    variant: str | None = None  # None for the first surface of a group and for unlinked entities

    def __getstate__(self) -> object:
        raise TypeError(_NOT_SERIALISABLE)

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError(_NOT_SERIALISABLE)


class Mapping:
    """Placeholder-to-surface table of one request; numbers start at 1 per entity type.

    Surfaces with the same link and type form a group: they share a number, and every surface
    after the first gets the next variant letter. Not thread-safe: use one instance per
    request. Its repr reveals only the entity count, and it cannot be pickled or copied.
    """

    __slots__ = ("_by_key", "_by_placeholder", "_counters", "_groups")

    def __init__(self) -> None:
        self._by_key: dict[str, Entity] = {}
        self._by_placeholder: dict[tuple[str, int, str | None], Entity] = {}
        self._counters: dict[str, int] = {}
        # (link, entity type) -> (number, variant letters used). Link ids live only here.
        self._groups: dict[tuple[str, str], tuple[int, int]] = {}

    def assign(
        self, entity_type: str, surface: str, *, key: str | None = None, link: str | None = None
    ) -> Entity:
        """Return the entity for the key (default: normalize_key(surface)), creating it if the
        key is new (architecture §4.2).

        For an existing key the stored entity is returned unchanged, even if `entity_type`,
        `link` or the surface's case differ. A new key without a link gets the next number for
        `entity_type`. A new key with a link joins the group (link, entity_type): the first
        surface of a group gets the next number, later ones the group's number and the next
        variant letter, and once all 25 letters are used, the next number and no letter.

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
        group_key = None if link is None else (link, entity_type)
        group = None if group_key is None else self._groups.get(group_key)
        variant: str | None = None
        if group is not None and group[1] < len(VARIANT_LETTERS):  # a group with a free letter
            number, used = group
            variant = VARIANT_LETTERS[used]
            updated_group: tuple[int, int] | None = (number, used + 1)
        else:  # no link, a new group, or a full group, which stays as it is
            number = self._counters.get(entity_type, 0) + 1
            updated_group = (number, 0) if group is None else None
        # Built before any state changes, so that a failure leaves the mapping as it was.
        entity = Entity(
            key=key,
            entity_type=entity_type,
            surface=surface,
            placeholder=format_placeholder(entity_type, number, variant),
            variant=variant,
        )
        if variant is None:
            self._counters[entity_type] = number
        if group_key is not None and updated_group is not None:
            self._groups[group_key] = updated_group
        self._by_key[key] = entity
        self._by_placeholder[(entity_type, number, variant)] = entity
        return entity

    def get(self, key: str) -> Entity | None:
        """Return the entity recorded for `key`, or None."""
        return self._by_key.get(key)

    def lookup(self, entity_type: str, number: int, variant: str | None = None) -> str | None:
        """Return the canonical surface for a placeholder, or None if it is not in the mapping."""
        entity = self._by_placeholder.get((entity_type, number, variant))
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
