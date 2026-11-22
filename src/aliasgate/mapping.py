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
    variant: str | None = None  # None for the first surface of a family and for unlinked entities
    # Propagated and audited only where it starts with an upper-case letter (slice 16).
    capitalised: bool = field(default=False, repr=False)

    def __getstate__(self) -> object:
        raise TypeError(_NOT_SERIALISABLE)

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError(_NOT_SERIALISABLE)


class Mapping:
    """Placeholder-to-surface table of one request; numbers start at 1 per entity type.

    The entities that share a type and a number form a family: the first has no letter, and
    each later one gets the family's next variant letter. Surfaces with the same link and type
    join one family, and so does an entity assigned as a variant of another. Not thread-safe:
    use one instance per request. Its repr reveals only the entity count, and it cannot be
    pickled or copied.
    """

    __slots__ = ("_by_key", "_by_placeholder", "_counters", "_groups", "_letters", "_numbers")

    def __init__(self) -> None:
        self._by_key: dict[str, Entity] = {}
        self._by_placeholder: dict[tuple[str, int, str | None], Entity] = {}
        self._counters: dict[str, int] = {}
        # (entity type, number) -> variant letters the family has used.
        self._letters: dict[tuple[str, int], int] = {}
        # (link, entity type) -> the number of the link's family. Link ids live only here.
        self._groups: dict[tuple[str, str], int] = {}
        self._numbers: dict[str, int] = {}  # key -> number of the entity's family

    def assign(
        self,
        entity_type: str,
        surface: str,
        *,
        key: str | None = None,
        link: str | None = None,
        variant_of: Entity | None = None,
        capitalised: bool = False,
    ) -> Entity:
        """Return the entity for the key (default: normalize_key(surface)), creating it if the
        key is new (architecture §4.2).

        For an existing key the stored entity is returned unchanged, whatever `entity_type`,
        `link`, `variant_of`, `capitalised` or the surface's case are. A new key with
        `variant_of` joins the family of that entity. A new key without a link gets the next
        number for `entity_type`. A new key with a link joins the family of the group (link,
        entity_type): the first surface of a group gets the next number. Joining a family gives
        its number and the family's next variant letter, and once all 25 letters are used, the
        next number and no letter. `capitalised` is stored on a new entity.

        Raises ValueError for an invalid entity type, an empty key or an empty surface, and for
        a new key whose `variant_of` comes with a link, has another type or belongs to another
        mapping; messages never hold a value."""
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
        family = self._family(entity_type, link, variant_of, group_key)
        used = len(VARIANT_LETTERS) if family is None else self._letters[entity_type, family]
        variant: str | None = None
        if family is not None and used < len(VARIANT_LETTERS):
            number = family
            variant = VARIANT_LETTERS[used]
        else:  # no family to join, or a full one, which stays as it is
            number = self._counters.get(entity_type, 0) + 1
        # Built before any state changes, so that a failure leaves the mapping as it was.
        entity = Entity(
            key=key,
            entity_type=entity_type,
            surface=surface,
            placeholder=format_placeholder(entity_type, number, variant),
            variant=variant,
            capitalised=capitalised,
        )
        if variant is None:
            self._counters[entity_type] = number
            self._letters[entity_type, number] = 0
        else:
            self._letters[entity_type, number] += 1
        if group_key is not None and group_key not in self._groups:
            self._groups[group_key] = number
        self._by_key[key] = entity
        self._by_placeholder[(entity_type, number, variant)] = entity
        self._numbers[key] = number
        return entity

    def _family(
        self,
        entity_type: str,
        link: str | None,
        variant_of: Entity | None,
        group_key: tuple[str, str] | None,
    ) -> int | None:
        """The number of the family a new entity joins, or None for a new number."""
        if variant_of is not None:
            if link is not None:
                raise ValueError("variant_of cannot be combined with a link")
            if variant_of.entity_type != entity_type:
                raise ValueError("variant_of must have the same entity type")
            if self._by_key.get(variant_of.key) is not variant_of:
                raise ValueError("variant_of must be an entity of this mapping")
            return self._numbers[variant_of.key]
        return None if group_key is None else self._groups.get(group_key)

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
