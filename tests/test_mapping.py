import copy
import pickle
from collections.abc import Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aliasgate.mapping import Entity, Mapping, normalize_key
from aliasgate.placeholders import TOLERANT, parse_match


def _parse(placeholder: str) -> tuple[str, int, str | None]:
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
    assert entity.variant is None


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


# Link groups and variants (slice 13) ----------------------------------------------------------

LINK = "dictionary:1"


def test_linked_surfaces_share_a_number_and_get_letters_in_order() -> None:
    mapping = Mapping()
    first = mapping.assign("ORG", "ACME Corporation", link=LINK)
    second = mapping.assign("ORG", "ACME", link=LINK)
    third = mapping.assign("ORG", "ACME Corp.", link=LINK)
    assert (first.placeholder, first.variant) == ("[ORG_1]", None)
    assert (second.placeholder, second.variant) == ("[ORG_1_B]", "B")
    assert (third.placeholder, third.variant) == ("[ORG_1_C]", "C")
    assert (second.key, second.surface) == ("acme", "ACME")
    assert mapping.entities() == (first, second, third)
    assert len(mapping) == 3


def test_a_case_variant_of_a_linked_surface_returns_its_entity() -> None:
    mapping = Mapping()
    mapping.assign("ORG", "ACME Corporation", link=LINK)
    alias = mapping.assign("ORG", "ACME", link=LINK)
    assert mapping.assign("ORG", "acme", link=LINK) is alias
    assert len(mapping) == 2


def test_the_same_link_with_another_type_starts_its_own_group() -> None:
    mapping = Mapping()
    org = mapping.assign("ORG", "ACME Corporation", link=LINK)
    project = mapping.assign("PROJECT", "Project Falcon", link=LINK)
    falcon = mapping.assign("PROJECT", "Falcon", link=LINK)
    assert (org.placeholder, project.placeholder, falcon.placeholder) == (
        "[ORG_1]",
        "[PROJECT_1]",
        "[PROJECT_1_B]",
    )


def test_a_known_key_keeps_its_entity_and_does_not_join_another_group() -> None:
    mapping = Mapping()
    globex = mapping.assign("ORG", "Globex", link="dictionary:2")
    mapping.assign("ORG", "ACME Corporation", link=LINK)
    assert mapping.assign("ORG", "GLOBEX", link=LINK) is globex
    assert mapping.assign("ORG", "ACME", link=LINK).placeholder == "[ORG_2_B]"


def test_a_known_unlinked_key_does_not_join_a_group() -> None:
    mapping = Mapping()
    plain = mapping.assign("ORG", "ACME")
    assert mapping.assign("ORG", "ACME", link=LINK) is plain
    assert mapping.assign("ORG", "ACME Corporation", link=LINK).placeholder == "[ORG_2]"


def test_groups_and_unlinked_entities_are_numbered_in_order_of_assignment() -> None:
    mapping = Mapping()
    placeholders = [
        mapping.assign("ORG", "ACME Corporation", link=LINK).placeholder,
        mapping.assign("ORG", "ACME", link=LINK).placeholder,
        mapping.assign("ORG", "Initech").placeholder,
        mapping.assign("ORG", "ACME Corp.", link=LINK).placeholder,
        mapping.assign("ORG", "Globex S.L.", link="dictionary:2").placeholder,
    ]
    assert placeholders == ["[ORG_1]", "[ORG_1_B]", "[ORG_2]", "[ORG_1_C]", "[ORG_3]"]


def test_a_full_group_gives_every_later_surface_a_new_number_without_a_variant() -> None:
    mapping = Mapping()
    surfaces = [f"Name {index}" for index in range(29)]
    entities = [mapping.assign("ORG", surface, link=LINK) for surface in surfaces]
    assert [entity.placeholder for entity in entities[:26]] == ["[ORG_1]"] + [
        f"[ORG_1_{letter}]" for letter in "BCDEFGHIJKLMNOPQRSTUVWXYZ"
    ]
    assert [(entity.placeholder, entity.variant) for entity in entities[26:]] == [
        ("[ORG_2]", None),
        ("[ORG_3]", None),
        ("[ORG_4]", None),
    ]
    for entity, surface in zip(entities, surfaces, strict=True):
        assert mapping.lookup(*_parse(entity.placeholder)) == surface


def test_lookup_with_and_without_a_variant() -> None:
    mapping = Mapping()
    mapping.assign("ORG", "ACME Corporation", link=LINK)
    mapping.assign("ORG", "ACME", link=LINK)
    assert mapping.lookup("ORG", 1) == "ACME Corporation"
    assert mapping.lookup("ORG", 1, None) == "ACME Corporation"
    assert mapping.lookup("ORG", 1, "B") == "ACME"
    assert mapping.lookup("ORG", 1, "C") is None
    assert mapping.lookup("ORG", 1, "A") is None
    assert mapping.lookup("ORG", 2, "B") is None
    assert mapping.lookup("PERSON", 1, "B") is None


def test_reprs_do_not_reveal_surfaces_keys_or_links() -> None:
    mapping = Mapping()
    mapping.assign("ORG", "Globex S.L.", link="dictionary:42")
    alias = mapping.assign("ORG", "Globex", link="dictionary:42")
    assert repr(mapping) == "Mapping(entities=2)"
    for rendered in (repr(alias), str(alias), repr(mapping.entities())):
        assert "globex" not in rendered.lower()
        assert "dictionary" not in rendered
        assert "42" not in rendered
    assert "[ORG_1_B]" in repr(alias)


def test_a_mapping_with_groups_cannot_be_pickled_or_copied() -> None:
    mapping = Mapping()
    mapping.assign("ORG", "ACME Corporation", link=LINK)
    entity = mapping.assign("ORG", "ACME", link=LINK)
    for target in (mapping, entity):
        for operation in (pickle.dumps, copy.copy, copy.deepcopy):
            with pytest.raises(TypeError, match="never serialised"):
                operation(target)


def test_assign_with_an_invalid_link_group_type_raises_without_changing_the_mapping() -> None:
    mapping = Mapping()
    with pytest.raises(ValueError, match="entity type"):
        mapping.assign("org", "ACME", link=LINK)
    assert len(mapping) == 0
    assert mapping.assign("ORG", "ACME Corporation", link=LINK).placeholder == "[ORG_1]"


def test_entity_is_immutable() -> None:
    entity = Mapping().assign("PERSON", "Alice Johnson")
    with pytest.raises(AttributeError):
        entity.surface = "Juan Pérez"  # type: ignore[misc]


# Variants of an entity and capitalised entities (slice 16) ------------------------------------


def test_variant_of_an_unlinked_entity_continues_its_family() -> None:
    mapping = Mapping()
    person = mapping.assign("PERSON", "Alice Johnson")
    johnson = mapping.assign("PERSON", "Johnson", variant_of=person)
    alice = mapping.assign("PERSON", "Alice", variant_of=person)
    assert (johnson.placeholder, johnson.variant) == ("[PERSON_1_B]", "B")
    assert (alice.placeholder, alice.variant) == ("[PERSON_1_C]", "C")
    assert mapping.assign("PERSON", "Bob Smith").placeholder == "[PERSON_2]"
    assert mapping.lookup("PERSON", 1, "B") == "Johnson"
    assert mapping.lookup("PERSON", 1, "C") == "Alice"


def test_variant_of_a_group_entity_shares_the_groups_25_letters() -> None:
    mapping = Mapping()
    term = mapping.assign("PERSON", "Alice Johnson", link=LINK)
    alias = mapping.assign("PERSON", "AJ", link=LINK)
    token = mapping.assign("PERSON", "Johnson", variant_of=term)
    later_alias = mapping.assign("PERSON", "A. Johnson", link=LINK)
    assert [e.placeholder for e in (term, alias, token, later_alias)] == [
        "[PERSON_1]",
        "[PERSON_1_B]",
        "[PERSON_1_C]",
        "[PERSON_1_D]",
    ]
    rest = [
        mapping.assign("PERSON", f"Word {index}", variant_of=alias if index % 2 else term)
        if index % 3
        else mapping.assign("PERSON", f"Word {index}", link=LINK)
        for index in range(22)
    ]
    assert [entity.variant for entity in rest] == list("EFGHIJKLMNOPQRSTUVWXYZ")
    # The 26th distinct surface of the family, by either route, gets the next number.
    overflow_token = mapping.assign("PERSON", "Overflow", variant_of=token)
    overflow_alias = mapping.assign("PERSON", "Overflow Alias", link=LINK)
    assert [(e.placeholder, e.variant) for e in (overflow_token, overflow_alias)] == [
        ("[PERSON_2]", None),
        ("[PERSON_3]", None),
    ]


def test_variant_of_a_variant_joins_the_same_family() -> None:
    mapping = Mapping()
    person = mapping.assign("PERSON", "Juan Pérez García")
    perez = mapping.assign("PERSON", "Pérez", variant_of=person)
    juan = mapping.assign("PERSON", "Juan", variant_of=perez)
    assert (perez.placeholder, juan.placeholder) == ("[PERSON_1_B]", "[PERSON_1_C]")


def test_variant_of_a_full_family_gives_the_next_number_without_a_letter() -> None:
    mapping = Mapping()
    person = mapping.assign("PERSON", "Alice Johnson")
    for index in range(25):
        mapping.assign("PERSON", f"Word {index}", variant_of=person)
    extra = mapping.assign("PERSON", "Extra", variant_of=person)
    again = mapping.assign("PERSON", "Again", variant_of=person)
    assert [(e.placeholder, e.variant) for e in (extra, again)] == [
        ("[PERSON_2]", None),
        ("[PERSON_3]", None),
    ]
    assert mapping.assign("PERSON", "Next", variant_of=extra).placeholder == "[PERSON_2_B]"


def test_a_known_key_ignores_variant_of_and_capitalised() -> None:
    mapping = Mapping()
    person = mapping.assign("PERSON", "Alice Johnson")
    other = mapping.assign("PERSON", "Bob Smith")
    johnson = mapping.assign("PERSON", "Johnson", variant_of=person, capitalised=True)
    assert mapping.assign("PERSON", "JOHNSON", variant_of=other, capitalised=False) is johnson
    assert mapping.assign("ORG", "johnson", variant_of=other) is johnson
    plain = mapping.assign("PERSON", "Smith")
    assert mapping.assign("PERSON", "Smith", variant_of=other, capitalised=True) is plain
    assert plain.capitalised is False
    assert len(mapping) == 4


def test_variant_of_with_a_link_another_type_or_another_mapping_raises_without_values() -> None:
    mapping = Mapping()
    person = mapping.assign("PERSON", "Alice Johnson")
    stranger = Mapping().assign("PERSON", "Alice Johnson")
    calls: list[Callable[[], Entity]] = [
        lambda: mapping.assign("PERSON", "Johnson", link=LINK, variant_of=person),
        lambda: mapping.assign("ORG", "Johnson", variant_of=person),
        lambda: mapping.assign("PERSON", "Johnson", variant_of=stranger),
    ]
    for call in calls:
        with pytest.raises(ValueError, match="variant_of") as excinfo:
            call()
        message = str(excinfo.value)
        assert "Johnson" not in message
        assert "johnson" not in message
        assert "Alice" not in message
        assert "dictionary" not in message
    assert mapping.entities() == (person,)
    assert mapping.assign("PERSON", "Johnson", variant_of=person).placeholder == "[PERSON_1_B]"


def test_capitalised_defaults_to_false_is_stored_and_is_not_in_the_repr() -> None:
    mapping = Mapping()
    person = mapping.assign("PERSON", "Will Stone")
    will = mapping.assign("PERSON", "Will", variant_of=person, capitalised=True)
    unlinked = mapping.assign("PERSON", "Stone", capitalised=True)
    assert person.capitalised is False
    assert (will.capitalised, unlinked.capitalised) == (True, True)
    assert unlinked.placeholder == "[PERSON_2]"
    for rendered in (repr(will), str(will)):
        assert "capitalised" not in rendered
        assert "Will" not in rendered
        assert "[PERSON_1_B]" in rendered
    for operation in (pickle.dumps, copy.copy, copy.deepcopy):
        with pytest.raises(TypeError, match="never serialised"):
            operation(will)


_TYPES = st.sampled_from(["PERSON", "ORG", "EMAIL"])
_SURFACES = st.text(alphabet="aAbBéÉ \uff21\uff22\t\n", max_size=5)
_LINKS = st.sampled_from([None, None, "a:1", "a:2", "b:1"])
# An index into the entities assigned so far, to pass as variant_of when the call has no link.
_VARIANT_OF = st.one_of(st.none(), st.none(), st.integers(min_value=0, max_value=60))


@given(st.lists(st.tuples(_TYPES, _SURFACES, _LINKS, _VARIANT_OF), max_size=40))
def test_placeholders_are_unique_and_resolve_to_their_canonical_surface(
    calls: list[tuple[str, str, str | None, int | None]],
) -> None:
    mapping = Mapping()
    first_seen: dict[str, tuple[str, str]] = {}
    for entity_type, surface, link, index in calls:
        key = normalize_key(surface)
        if not key:
            with pytest.raises(ValueError, match="empty"):
                mapping.assign(entity_type, surface, link=link)
            continue
        assigned = mapping.entities()
        same_type = [entity for entity in assigned if entity.entity_type == entity_type]
        variant_of = (
            same_type[index % len(same_type)]
            if index is not None and link is None and same_type
            else None
        )
        entity = mapping.assign(entity_type, surface, link=link, variant_of=variant_of)
        first_seen.setdefault(key, (entity_type, surface))
        assert (entity.key, entity.entity_type, entity.surface) == (key, *first_seen[key])
        if variant_of is not None and entity not in assigned and entity.variant is not None:
            assert _parse(entity.placeholder)[:2] == _parse(variant_of.placeholder)[:2]

    entities = mapping.entities()
    assert len(entities) == len(mapping) == len(first_seen)
    placeholders = [entity.placeholder for entity in entities]
    assert len(set(placeholders)) == len(placeholders)
    for entity in entities:
        assert mapping.lookup(*_parse(entity.placeholder)) == entity.surface
        assert mapping.get(entity.key) is entity

    for entity in entities:
        assert _parse(entity.placeholder)[2] == entity.variant

    for entity_type in {entity.entity_type for entity in entities}:
        numbers = [
            _parse(e.placeholder)[1]
            for e in entities
            if e.entity_type == entity_type and e.variant is None
        ]
        assert numbers == list(range(1, len(numbers) + 1))


def test_a_mapping_cannot_be_pickled_copied_or_have_its_state_read() -> None:
    mapping = Mapping()
    mapping.assign("PERSON", "Juan Pérez")
    for operation in (pickle.dumps, copy.copy, copy.deepcopy, lambda m: m.__getstate__()):
        with pytest.raises(TypeError, match="never serialised") as excinfo:
            operation(mapping)
        assert "Juan" not in str(excinfo.value)


def test_an_entity_cannot_be_pickled_copied_or_have_its_state_read() -> None:
    entity = Mapping().assign("PERSON", "Juan Pérez")
    for operation in (pickle.dumps, copy.copy, copy.deepcopy, lambda e: e.__getstate__()):
        with pytest.raises(TypeError, match="never serialised") as excinfo:
            operation(entity)
        assert "Juan" not in str(excinfo.value)
