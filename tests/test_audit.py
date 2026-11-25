import json
import re
import unicodedata
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aliasgate.audit import AuditResult, audit_outbound
from aliasgate.mapping import Entity, Mapping
from aliasgate.terms import TERM_END, TERM_START, build_term_pattern, term_body

SURFACES = ("Juan Pérez", "ACME Corporation", "juan.perez@example.com")


def _mapping(*entities: tuple[str, str]) -> Mapping:
    mapping = Mapping()
    for entity_type, surface in entities:
        mapping.assign(entity_type, surface)
    return mapping


MAPPING_ENTITIES = (("PERSON", "Juan Pérez"), ("ORG", "ACME Corporation"))


def _clean_body() -> dict[str, Any]:
    return {
        "model": "gpt-example",
        "messages": [
            {"role": "system", "content": "You help [PERSON_1]."},
            {"role": "user", "content": [{"type": "text", "text": "Write to [ORG_1] today."}]},
        ],
        "temperature": 0.2,
        "stream": False,
        "n": None,
    }


def test_a_clean_payload_has_no_leak() -> None:
    result = audit_outbound(_clean_body(), _mapping(*MAPPING_ENTITIES), min_length=3)
    assert result == AuditResult(leaked_entities=0, leaked_types=())


def test_a_surface_left_in_a_field_no_dialect_processes_is_found() -> None:
    body = _clean_body()
    body["metadata"] = {"notes": ["see the file of Juan Pérez"]}
    result = audit_outbound(body, _mapping(*MAPPING_ENTITIES), min_length=3)
    assert result == AuditResult(leaked_entities=1, leaked_types=("PERSON",))


def test_leaked_types_are_sorted_and_deduplicated() -> None:
    mapping = _mapping(("PERSON", "Juan Pérez"), ("ORG", "ACME Corporation"), ("PERSON", "Alice"))
    body = {"a": "Alice and Juan Pérez", "b": {"c": "ACME Corporation", "d": "Juan Pérez"}}
    assert audit_outbound(body, mapping, min_length=3) == AuditResult(3, ("ORG", "PERSON"))


@pytest.mark.parametrize(
    "text",
    ["JUAN PÉREZ", "juan pérez", "Juan\n  Pérez", "(Juan\tPérez)", "Juan Pérez."],
    ids=["upper", "lower", "newline", "tab-in-brackets", "full-stop"],
)
def test_matching_ignores_case_and_whitespace_runs(text: str) -> None:
    result = audit_outbound({"x": text}, _mapping(("PERSON", "Juan Pérez")), min_length=3)
    assert result.leaked_entities == 1


@pytest.mark.parametrize("text", ["Banana", "Anabel", "ana_lyst", "Ana1"])
def test_only_whole_terms_match(text: str) -> None:
    result = audit_outbound({"x": text}, _mapping(("PERSON", "Ana")), min_length=3)
    assert result.leaked_entities == 0


def test_a_detected_value_in_a_key_is_found() -> None:
    body = {"Juan Pérez": "[PERSON_1]", "nested": [{"ACME Corporation": 1}]}
    assert audit_outbound(body, _mapping(*MAPPING_ENTITIES), min_length=3).leaked_entities == 2


def test_a_key_holding_the_folded_form_of_an_entity_is_found() -> None:
    body = {"address": {"STRASSE": "[ADDRESS_1]"}}
    result = audit_outbound(body, _mapping(("ADDRESS", "Straße")), min_length=3)
    assert result == AuditResult(1, ("ADDRESS",))


def test_a_key_is_skipped_when_the_entity_is_shorter_than_the_minimum() -> None:
    mapping = _mapping(("PERSON", "Al"))
    assert audit_outbound({"Al": "x"}, mapping, min_length=3).leaked_entities == 0
    assert audit_outbound({"Al": "x"}, mapping, min_length=2).leaked_entities == 1


def test_entities_shorter_than_the_minimum_are_skipped() -> None:
    mapping = _mapping(("PERSON", "Al"), ("PERSON", "Juan Pérez"))
    assert audit_outbound({"x": "Al wrote"}, mapping, min_length=3).leaked_entities == 0
    assert audit_outbound({"x": "Al wrote"}, mapping, min_length=2).leaked_entities == 1


def test_literal_entities_are_skipped() -> None:
    mapping = Mapping()
    mapping.assign("LITERAL", "[PERSON_1]", key="\x00literal:[PERSON_1]")
    assert (
        audit_outbound({"x": "[PERSON_1] [LITERAL_1]"}, mapping, min_length=3).leaked_entities == 0
    )


def test_placeholders_never_match_an_entity_named_like_their_type() -> None:
    mapping = _mapping(("ORG", "Person"), ("PERSON", "Org"))
    body = {"x": "[PERSON_1] and [ORG_1] and [PERSON_12]"}
    assert audit_outbound(body, mapping, min_length=3).leaked_entities == 0


def test_a_variants_surface_anywhere_in_the_body_is_reported_with_its_type() -> None:
    mapping = Mapping()
    mapping.assign("ORG", "ACME Corporation", link="dictionary:1")
    mapping.assign("ORG", "ACME", link="dictionary:1")
    mapping.assign("PERSON", "Juan Pérez")
    clean = {"x": "[ORG_1] and [ORG_1_B] wrote to [PERSON_1]", "y": ["[org 1 b]"]}
    assert audit_outbound(clean, mapping, min_length=3) == AuditResult(0, ())
    leaked = {"x": "[ORG_1] and [ORG_1_B]", "deep": {"notes": ["paid by Acme"]}}
    assert audit_outbound(leaked, mapping, min_length=3) == AuditResult(1, ("ORG",))


def test_an_empty_mapping_finds_nothing() -> None:
    assert audit_outbound({"x": "Juan Pérez"}, Mapping(), min_length=3) == AuditResult(0, ())


@pytest.mark.parametrize("payload", ["Juan Pérez", ["Juan Pérez"], [[{"x": ["Juan Pérez"]}]]])
def test_any_json_value_is_walked(payload: object) -> None:
    result = audit_outbound(payload, _mapping(("PERSON", "Juan Pérez")), min_length=3)
    assert result.leaked_entities == 1


def test_a_deeply_nested_payload_is_walked_without_recursion() -> None:
    payload: Any = "Juan Pérez"
    for _ in range(5000):
        payload = {"x": [payload]}
    result = audit_outbound(payload, _mapping(("PERSON", "Juan Pérez")), min_length=3)
    assert result.leaked_entities == 1


# Folding (decided with the maintainer): a value is reported when the case-insensitive term
# pattern finds it, or when its key is found in the NFKC-normalised, case-folded text. Variants
# with the same key that propagation cannot find are rejected rather than forwarded.


def _nfd(text: str) -> str:
    return text.replace("é", "e" + chr(0x301))


FULL_WIDTH_ACME = "".join(chr(0xFF21 + ord(char) - ord("A")) for char in "ACME")
DOTTED_CAPITAL_I = chr(0x130)
DOTLESS_SMALL_I = chr(0x131)


@pytest.mark.parametrize(
    ("surface", "text"),
    [
        ("Straße", "STRASSE"),
        ("Straße", "strasse"),
        ("STRASSE", "Straße"),
        ("Juan Pérez", _nfd("Juan Pérez")),
        ("ACME", FULL_WIDTH_ACME),
        ("Ivan", DOTTED_CAPITAL_I + "van"),
        ("Ivan", DOTLESS_SMALL_I + "van"),
    ],
    ids=["eszett-upper", "eszett-lower", "eszett-reverse", "decomposed", "full-width",
         "dotted-capital-i", "dotless-small-i"],
)  # fmt: skip
def test_folding_variants_are_found_either_way(surface: str, text: str) -> None:
    result = audit_outbound({"x": f"Hello {text}."}, _mapping(("PERSON", surface)), min_length=3)
    assert result.leaked_entities == 1


def test_the_term_pattern_alone_misses_the_folded_variants() -> None:
    # Pins why the audit folds: propagation's pattern does not find these.
    assert build_term_pattern("Straße").search("STRASSE") is None
    assert build_term_pattern("Juan Pérez").search(_nfd("Juan Pérez")) is None
    assert build_term_pattern("ACME").search(FULL_WIDTH_ACME) is None


def test_the_result_and_its_repr_hold_no_surface() -> None:
    body = {"x": "Juan Pérez of ACME Corporation"}
    result = audit_outbound(body, _mapping(*MAPPING_ENTITIES), min_length=3)
    assert result.leaked_entities == 2
    for text in (repr(result), str(result)):
        for surface in ("Juan", "Pérez", "ACME", "Corporation"):
            assert surface not in text


_JSON: st.SearchStrategy[Any] = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(alphabet="abc []_1", max_size=12),
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=5), children, max_size=4)
    ),
    max_leaves=20,
)


# Capitalised entities (slice 16): a person's name word that nobody detected is replaced only
# where it starts with an upper-case letter, so the audit looks for it only there.


def _with_capitalised(*words: str) -> Mapping:
    mapping = Mapping()
    person = mapping.assign("PERSON", "Pedro Mesa Lucía")
    for word in words:
        mapping.assign("PERSON", word, variant_of=person, capitalised=True)
    return mapping


@pytest.mark.parametrize("text", ["Mesa dijo que no.", "Según MESA, no.", "(Mesa)"])
def test_a_capitalised_entity_is_reported_where_its_word_is_capitalised(text: str) -> None:
    result = audit_outbound({"metadata": {"note": text}}, _with_capitalised("Mesa"), min_length=3)
    assert result == AuditResult(1, ("PERSON",))


@pytest.mark.parametrize(
    "text",
    [
        "la mesa está rota",
        "mESA",
        "".join(chr(0xFF21 + ord(char) - ord("A")) for char in "MESA"),
        "Luci" + chr(0x301) + "a",
    ],
    ids=["lower-case", "lower-first", "full-width", "decomposed"],
)
def test_a_capitalised_entity_is_not_reported_for_a_lower_case_or_folded_form(text: str) -> None:
    mapping = _with_capitalised("Mesa", "Lucía")
    assert audit_outbound({"x": text}, mapping, min_length=3) == AuditResult(0, ())


def test_other_entities_are_audited_exactly_as_before_next_to_capitalised_ones() -> None:
    mapping = _with_capitalised("Mesa")
    mapping.assign("ADDRESS", "Straße")
    body = {"a": "la mesa", "b": "STRASSE 5", "c": "pedro mesa lucía"}
    assert audit_outbound(body, mapping, min_length=3) == AuditResult(2, ("ADDRESS", "PERSON"))


def test_a_mapping_of_capitalised_entities_only_is_audited_by_the_term_pattern() -> None:
    mapping = Mapping()
    mapping.assign("PERSON", "Mesa", capitalised=True)
    assert audit_outbound({"x": "la mesa"}, mapping, min_length=3) == AuditResult(0, ())
    assert audit_outbound({"x": "La Mesa"}, mapping, min_length=3) == AuditResult(1, ("PERSON",))


# Slice 17: the whole-term boundary excludes a following 't, and a person's name word that is
# replaced in lower case too (`capitalised=False`) is audited like any entity.


@pytest.mark.parametrize("capitalised", [False, True])
def test_dont_is_not_reported_for_an_entity_don_by_either_search(capitalised: bool) -> None:
    mapping = Mapping()
    mapping.assign("PERSON", "Don", capitalised=capitalised)
    for text in ("Don't close it", "DON'T", "don\u2019t", "Don\u2019t worry"):
        assert audit_outbound({"metadata": {"note": text}}, mapping, min_length=3) == AuditResult(
            0, ()
        )
    assert audit_outbound({"x": "Don's form"}, mapping, min_length=3) == AuditResult(1, ("PERSON",))


@pytest.mark.parametrize(
    "text", ["thanks lucía", "LUCÍA", "luci" + chr(0x301) + "a", "gracias Lucía"]
)
def test_a_lower_case_eligible_name_word_is_reported_in_any_case_and_by_its_folded_key(
    text: str,
) -> None:
    mapping = Mapping()
    person = mapping.assign("PERSON", "Lucía Romero")
    mapping.assign("PERSON", "Lucía", variant_of=person, capitalised=False)
    assert audit_outbound({"x": text}, mapping, min_length=3) == AuditResult(1, ("PERSON",))


def _insert(value: Any, path: list[int], surface: str) -> Any:
    """Put `surface` into one string, list or dict of `value`, chosen by `path`."""
    if isinstance(value, str):
        return f"{value} {surface} {value}"
    if isinstance(value, list) and value:
        index = path[0] % len(value) if path else 0
        copy = list(value)
        copy[index] = _insert(value[index], path[1:], surface)
        return copy
    if isinstance(value, dict) and value:
        key = sorted(value)[path[0] % len(value) if path else 0]
        return {**value, key: _insert(value[key], path[1:], surface)}
    return [value, surface]


@given(
    _JSON, st.lists(st.integers(min_value=0, max_value=10), max_size=6), st.sampled_from(SURFACES)
)
def test_property_a_surface_anywhere_in_a_payload_is_found(
    payload: Any, path: list[int], surface: str
) -> None:
    mapping = _mapping(("PERSON", "Juan Pérez"), ("ORG", "ACME Corporation"),
                       ("EMAIL", "juan.perez@example.com"))  # fmt: skip
    assert audit_outbound(payload, mapping, min_length=3).leaked_entities == 0
    leaked = _insert(payload, path, surface)
    assert surface in json.dumps(leaked, ensure_ascii=False)
    result = audit_outbound(leaked, mapping, min_length=3)
    assert result.leaked_entities >= 1
    assert all(re.fullmatch(r"[A-Z][A-Z_]*", kind) for kind in result.leaked_types)


def _reference(payload: list[str], mapping: Mapping, min_length: int) -> AuditResult:
    """Each entity searched on its own, both ways, or only by capitalised matches of its term
    pattern for a capitalised entity: what the audit must be equal to."""
    folded = [unicodedata.normalize("NFKC", text).casefold() for text in payload]

    def found(entity: Entity) -> bool:
        matches = [
            match.group()
            for text in payload
            for match in build_term_pattern(entity.surface).finditer(text)
        ]
        if entity.capitalised:
            return any(match[0].isupper() or match[0].istitle() for match in matches)
        return bool(matches) or any(
            re.search(rf"{TERM_START}(?:{term_body(entity.key)}){TERM_END}", text)
            for text in folded
        )

    leaked = [
        entity
        for entity in mapping.entities()
        if entity.entity_type != "LITERAL" and len(entity.key) >= min_length and found(entity)
    ]
    return AuditResult(len(leaked), tuple(sorted({entity.entity_type for entity in leaked})))


_WORDS = st.sampled_from(["Ana", "ana", "Banana", "Straße", "STRASSE", "Juan", "Pérez", "J.P.",
                          "a+b", "x", "[ORG_1]", FULL_WIDTH_ACME, "ACME", "acme-co", "Don",
                          "Don't", "DON'T", "don's"])  # fmt: skip
_PHRASE = st.lists(_WORDS, min_size=1, max_size=3).map(" ".join)


@given(
    st.lists(st.tuples(st.sampled_from(["PERSON", "ORG"]), _PHRASE, st.booleans()), max_size=8),
    st.lists(st.lists(_WORDS, max_size=6).map(" ".join), max_size=4),
    st.integers(min_value=1, max_value=6),
)
def test_property_the_combined_search_equals_searching_each_entity_alone(
    entities: list[tuple[str, str, bool]], payload: list[str], min_length: int
) -> None:
    mapping = Mapping()
    for entity_type, surface, capitalised in entities:
        mapping.assign(entity_type, surface, capitalised=capitalised)
    assert audit_outbound(payload, mapping, min_length=min_length) == _reference(
        payload, mapping, min_length
    )
