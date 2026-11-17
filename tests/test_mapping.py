import pytest
from hypothesis import given
from hypothesis import strategies as st

from aliasgate.mapping import Entity, Mapping, normalize_key
from aliasgate.placeholders import TOLERANT, parse_match


def _parse(placeholder: str) -> tuple[str, int]:
    match = TOLERANT.fullmatch(placeholder)
    assert match is not None
    return parse_match(match)


def test_normalize_key_casefolds_collapses_whitespace_and_strips() -> None:
    assert normalize_key("  Juan   PÉREZ ") == "juan pérez"


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("\uff21\uff23\uff2d\uff25", "ACME"),
        ("O\ufb03ce", "office"),
        ("Pe\u0301rez", "Pérez"),
        ("Juan\u00a0\tPérez\n", "juan pérez"),
        ("STRASSE", "Straße"),
    ],
    ids=["full-width", "ligature", "decomposed-accent", "unicode-whitespace", "casefold"],
)
def test_normalize_key_merges_equivalent_forms(left: str, right: str) -> None:
    assert normalize_key(left) == normalize_key(right)


def test_normalize_key_of_whitespace_is_empty() -> None:
    assert normalize_key(" \t\n\u00a0") == ""


def test_numbers_are_counted_per_entity_type_from_one() -> None:
    mapping = Mapping()
    alice = mapping.assign("PERSON", "Alice Johnson")
    juan = mapping.assign("PERSON", "Juan Pérez")
    acme = mapping.assign("ORG", "ACME Corporation")
    assert (alice.placeholder, juan.placeholder, acme.placeholder) == (
        "[PERSON_1]",
        "[PERSON_2]",
        "[ORG_1]",
    )


def test_assign_records_key_type_and_canonical_surface() -> None:
    entity = Mapping().assign("PERSON", "  Juan   Pérez ")
    assert entity == Entity(
        key="juan pérez", entity_type="PERSON", surface="  Juan   Pérez ", placeholder="[PERSON_1]"
    )


def test_case_variant_returns_the_first_entity_and_keeps_its_surface() -> None:
    mapping = Mapping()
    first = mapping.assign("ORG", "ACME")
    second = mapping.assign("ORG", "acme")
    assert second is first
    assert second.surface == "ACME"
    assert len(mapping) == 1


def test_same_key_with_a_different_type_returns_the_first_entity() -> None:
    mapping = Mapping()
    org = mapping.assign("ORG", "ACME")
    again = mapping.assign("PERSON", "Acme")
    assert again is org
    assert again.entity_type == "ORG"
    assert mapping.assign("PERSON", "Alice Johnson").placeholder == "[PERSON_1]"


def test_explicit_keys_keep_surfaces_apart_that_normalize_key_would_merge() -> None:
    mapping = Mapping()
    upper = mapping.assign("LITERAL", "[PERSON_1]", key="\x00literal:[PERSON_1]")
    lower = mapping.assign("LITERAL", "[person_1]", key="\x00literal:[person_1]")
    assert upper is not lower
    assert (upper.placeholder, lower.placeholder) == ("[LITERAL_1]", "[LITERAL_2]")
    assert upper.key == "\x00literal:[PERSON_1]"
    assert mapping.get("\x00literal:[person_1]") is lower
    assert mapping.get(normalize_key("[PERSON_1]")) is None


def test_lookup_returns_the_canonical_surface_or_none() -> None:
    mapping = Mapping()
    mapping.assign("PERSON", "Juan Pérez")
    mapping.assign("PERSON", "JUAN PÉREZ")
    assert mapping.lookup("PERSON", 1) == "Juan Pérez"
    assert mapping.lookup("PERSON", 99) is None
    assert mapping.lookup("PERSON", 0) is None
    assert mapping.lookup("ORG", 1) is None


def test_get_finds_an_entity_by_key() -> None:
    mapping = Mapping()
    entity = mapping.assign("EMAIL", "juan.perez@example.com")
    assert mapping.get("juan.perez@example.com") is entity
    assert mapping.get("a.b+tag@mail.example.org") is None


def test_entities_are_returned_in_order_of_assignment() -> None:
    mapping = Mapping()
    assert mapping.entities() == ()
    assert len(mapping) == 0
    globex = mapping.assign("ORG", "Globex S.L.")
    alice = mapping.assign("PERSON", "Alice Johnson")
    mapping.assign("ORG", "GLOBEX S.L.")
    acme = mapping.assign("ORG", "ACME Corporation")
    assert mapping.entities() == (globex, alice, acme)
    assert len(mapping) == 3


@pytest.mark.parametrize(
    ("surface", "key"),
    [("", None), (" \t\n", None), ("Alice Johnson", ""), ("", "\x00literal:")],
    ids=["empty-surface", "whitespace-surface", "empty-explicit-key", "empty-surface-with-key"],
)
def test_assign_rejects_an_empty_key_or_surface(surface: str, key: str | None) -> None:
    mapping = Mapping()
    with pytest.raises(ValueError, match="empty"):
        mapping.assign("PERSON", surface, key=key)
    assert len(mapping) == 0


def test_assign_with_an_invalid_entity_type_raises_without_changing_the_mapping() -> None:
    mapping = Mapping()
    with pytest.raises(ValueError, match="entity type") as excinfo:
        mapping.assign("person", "Alice Johnson")
    assert "Alice" not in str(excinfo.value)
    assert len(mapping) == 0
    assert mapping.get("alice johnson") is None
    assert mapping.assign("PERSON", "Alice Johnson").placeholder == "[PERSON_1]"


def test_reprs_do_not_reveal_surfaces_or_keys() -> None:
    mapping = Mapping()
    entity = mapping.assign("PERSON", "María-José O'Connor")
    mapping.assign("EMAIL", "juan.perez@example.com")
    for rendered in (repr(mapping), str(mapping)):
        assert rendered == "Mapping(entities=2)"
    for rendered in (repr(entity), str(entity)):
        assert "María" not in rendered
        assert "maría" not in rendered
        assert "PERSON" in rendered
        assert "[PERSON_1]" in rendered
    assert "juan.perez" not in repr(mapping.entities())


def test_entity_is_immutable() -> None:
    entity = Mapping().assign("PERSON", "Alice Johnson")
    with pytest.raises(AttributeError):
        entity.surface = "Juan Pérez"  # type: ignore[misc]


_TYPES = st.sampled_from(["PERSON", "ORG", "EMAIL"])
_SURFACES = st.text(alphabet="aAbBéÉ \uff21\uff22\t\n", max_size=5)


@given(st.lists(st.tuples(_TYPES, _SURFACES), max_size=30))
def test_placeholders_are_unique_and_resolve_to_their_canonical_surface(
    calls: list[tuple[str, str]],
) -> None:
    mapping = Mapping()
    first_seen: dict[str, tuple[str, str]] = {}
    for entity_type, surface in calls:
        key = normalize_key(surface)
        if not key:
            with pytest.raises(ValueError, match="empty"):
                mapping.assign(entity_type, surface)
            continue
        entity = mapping.assign(entity_type, surface)
        first_seen.setdefault(key, (entity_type, surface))
        assert (entity.key, entity.entity_type, entity.surface) == (key, *first_seen[key])

    entities = mapping.entities()
    assert len(entities) == len(mapping) == len(first_seen)
    placeholders = [entity.placeholder for entity in entities]
    assert len(set(placeholders)) == len(placeholders)
    for entity in entities:
        assert mapping.lookup(*_parse(entity.placeholder)) == entity.surface
        assert mapping.get(entity.key) is entity

    for entity_type in {entity.entity_type for entity in entities}:
        numbers = [_parse(e.placeholder)[1] for e in entities if e.entity_type == entity_type]
        assert numbers == list(range(1, len(numbers) + 1))
