import json

import pytest

from aliasgate.engine import Pseudonymizer, count_unresolved, restore
from aliasgate.mapping import Mapping
from fakes import WordListDetector


def _mapping(*surfaces: tuple[str, str]) -> Mapping:
    mapping = Mapping()
    for entity_type, surface in surfaces:
        mapping.assign(entity_type, surface)
    return mapping


@pytest.mark.parametrize(
    "variant",
    [
        "[PERSON_1]",
        "[person_1]",
        "[PERSON 1]",
        "\\[PERSON_1\\]",
        "[ PERSON_1 ]",
        "[\tperson\\_1 ]",
    ],
)
def test_tolerant_variants_are_restored(variant: str) -> None:
    mapping = _mapping(("PERSON", "Juan Pérez"))
    assert restore(f"Dear {variant}, hello", mapping) == "Dear Juan Pérez, hello"


def test_multi_word_types_with_escaped_underscores_are_restored() -> None:
    mapping = _mapping(("CREDIT_CARD", "4111 1111 1111 1111"))
    assert restore("card [CREDIT\\_CARD\\_1]", mapping) == "card 4111 1111 1111 1111"


def test_unknown_placeholder_is_left_unchanged_and_counted() -> None:
    mapping = _mapping(("PERSON", "Alice Johnson"))
    text = "[PERSON_1] and [PERSON_9] and [ORG_1]"
    assert restore(text, mapping) == "Alice Johnson and [PERSON_9] and [ORG_1]"
    assert count_unresolved(text, mapping) == 2


def test_count_unresolved_ignores_known_placeholders_and_plain_text() -> None:
    mapping = _mapping(("PERSON", "Alice Johnson"))
    assert count_unresolved("[person 1] wrote [1] and [PERSON]", mapping) == 0


def test_restore_with_an_empty_mapping_returns_the_text_unchanged() -> None:
    text = "[PERSON_1] and \\[ORG_2\\]"
    assert restore(text, Mapping()) == text
    assert count_unresolved(text, Mapping()) == 2


def test_json_escape_produces_valid_json_string_content() -> None:
    surface = 'Ana "La Jefa"\nO\'Connor \\ ñ'
    mapping = _mapping(("PERSON", surface))
    restored = restore("[PERSON_1]", mapping, json_escape=True)
    assert restored == 'Ana \\"La Jefa\\"\\nO\'Connor \\\\ ñ'
    assert json.loads('"' + restored + '"') == surface


def test_json_escape_round_trips_inside_a_json_document() -> None:
    surface = 'Globex "S.L."\ttab'
    mapping = _mapping(("ORG", surface))
    document = json.dumps({"customer": "[ORG_1]", "note": "pay [ORG_1]"}, ensure_ascii=False)
    assert json.loads(restore(document, mapping, json_escape=True)) == {
        "customer": surface,
        "note": f"pay {surface}",
    }


def test_restored_values_are_not_scanned_again() -> None:
    engine = Pseudonymizer([WordListDetector({"Alice": "PERSON", "Bob": "PERSON"})])
    batch = engine.pseudonymize(["Alice and Bob", "see [PERSON_2]"])
    assert batch.texts == ("[PERSON_1] and [PERSON_2]", "see [LITERAL_1]")
    assert restore(batch.texts[1], batch.mapping) == "see [PERSON_2]"


def test_a_surface_that_looks_like_a_placeholder_is_inserted_once() -> None:
    mapping = _mapping(("PERSON", "[PERSON_1]"))
    assert restore("[PERSON_1] [PERSON_1]", mapping) == "[PERSON_1] [PERSON_1]"
