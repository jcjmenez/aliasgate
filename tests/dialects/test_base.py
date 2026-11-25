import ast
import copy
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aliasgate.dialects.base import (
    MalformedRequestError,
    Policy,
    PreparedRequest,
    Slot,
    UnsupportedContentError,
    index_slot,
    key_slot,
    restore_all_strings,
    slots_for_json_strings,
    slots_for_key_strings,
    slots_for_keys_and_strings,
)
from aliasgate.mapping import Mapping

DIALECTS_DIR = Path(__file__).parents[2] / "src" / "aliasgate" / "dialects"

# Leaves of every JSON type, so that the properties also show which values are not slots.
_LEAVES = st.text() | st.integers() | st.floats(allow_nan=False) | st.booleans() | st.none()
_JSON = st.recursive(
    _LEAVES,
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(), children, max_size=4)
    ),
    max_leaves=25,
)
_CONTAINERS = st.lists(_JSON, max_size=4) | st.dictionaries(st.text(), _JSON, max_size=4)


def _strings_depth_first(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings_depth_first(item)]
    if isinstance(value, list):
        return [s for item in value for s in _strings_depth_first(item)]
    return []


def _keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [k for key, item in value.items() for k in [key, *_keys(item)]]
    if isinstance(value, list):
        return [k for item in value for k in _keys(item)]
    return []


def _mapping() -> Mapping:
    mapping = Mapping()
    mapping.assign("PERSON", "Juan Pérez")
    mapping.assign("EMAIL", "juan.perez@example.com")
    return mapping


def test_policy_defaults_are_the_safe_ones() -> None:
    assert Policy() == Policy(
        unsupported_content="block", strip_user_identifiers=True, tool_definitions=True
    )


def test_key_slot_reads_and_writes_a_dict_value() -> None:
    obj: dict[str, Any] = {"text": "Alice Johnson", "other": 1}
    slot = key_slot(obj, "text")
    assert slot.get() == "Alice Johnson"
    slot.set("[PERSON_1]")
    assert obj == {"text": "[PERSON_1]", "other": 1}


def test_index_slot_reads_and_writes_a_list_item() -> None:
    arr: list[Any] = ["a", "Alice Johnson", 3]
    slot = index_slot(arr, 1)
    assert slot.get() == "Alice Johnson"
    slot.set("[PERSON_1]")
    assert arr == ["a", "[PERSON_1]", 3]


def test_reading_a_slot_whose_value_is_no_longer_a_string_raises_without_the_value() -> None:
    obj: dict[str, Any] = {"juan.perez@example.com": "x"}
    slot = key_slot(obj, "juan.perez@example.com")
    obj["juan.perez@example.com"] = 12345
    with pytest.raises(TypeError) as excinfo:
        slot.get()
    assert "juan.perez" not in str(excinfo.value)
    assert "12345" not in str(excinfo.value)
    with pytest.raises(TypeError):
        index_slot([None], 0).get()


def test_prepared_request_exposes_texts_in_slot_order() -> None:
    obj: dict[str, Any] = {"a": "first", "b": ["second", "third"]}
    prepared = PreparedRequest([key_slot(obj, "a"), index_slot(obj["b"], 1)], [])
    assert prepared.texts() == ["first", "third"]


def test_apply_sets_every_slot_then_runs_finalizers_in_order() -> None:
    obj: dict[str, Any] = {"a": "first", "b": "second"}
    seen: list[tuple[str, Any]] = []
    prepared = PreparedRequest(
        [key_slot(obj, "a"), key_slot(obj, "b")],
        [lambda: seen.append(("one", dict(obj))), lambda: seen.append(("two", dict(obj)))],
    )
    prepared.apply(["[A_1]", "[B_1]"])
    assert obj == {"a": "[A_1]", "b": "[B_1]"}
    assert seen == [("one", {"a": "[A_1]", "b": "[B_1]"}), ("two", {"a": "[A_1]", "b": "[B_1]"})]


@pytest.mark.parametrize("new_texts", [[], ["one"], ["one", "two", "three"]])
def test_apply_with_the_wrong_number_of_texts_raises_and_changes_nothing(
    new_texts: list[str],
) -> None:
    obj: dict[str, Any] = {"a": "first", "b": "second"}
    ran: list[bool] = []
    prepared = PreparedRequest([key_slot(obj, "a"), key_slot(obj, "b")], [lambda: ran.append(True)])
    with pytest.raises(ValueError, match="expected 2 texts"):
        prepared.apply(new_texts)
    assert obj == {"a": "first", "b": "second"}
    assert ran == []


def test_apply_rejects_a_single_string_instead_of_a_sequence_of_texts() -> None:
    obj: dict[str, Any] = {"a": "x", "b": "y"}
    prepared = PreparedRequest([key_slot(obj, "a"), key_slot(obj, "b")], [])
    with pytest.raises(TypeError):
        prepared.apply("ab")
    assert obj == {"a": "x", "b": "y"}


def test_apply_rejects_a_non_string_text_before_setting_any_slot() -> None:
    obj: dict[str, Any] = {"a": "x", "b": "y"}
    prepared = PreparedRequest([key_slot(obj, "a"), key_slot(obj, "b")], [])
    with pytest.raises(TypeError):
        prepared.apply(["new", 1])  # type: ignore[list-item]
    assert obj == {"a": "x", "b": "y"}


def test_slots_are_plain_get_and_set_pairs() -> None:
    box = ["value"]
    slot = Slot(get=lambda: box[0], set=lambda value: box.__setitem__(0, value))
    slot.set("other")
    assert slot.get() == "other"


def test_unsupported_content_error_carries_a_kind_label() -> None:
    error = UnsupportedContentError("image_url")
    assert error.kind == "image_url"
    assert "image_url" in str(error)


@pytest.mark.parametrize("kind", ["Juan Pérez", "juan.perez@example.com", "x" * 41, "image\n"])
def test_unsupported_content_error_never_carries_text_that_is_not_a_label(kind: str) -> None:
    error = UnsupportedContentError(kind)
    assert error.kind == "unknown"
    assert str(error) == "unsupported content: unknown"


def test_an_empty_kind_is_reported_as_unknown() -> None:
    assert UnsupportedContentError("").kind == "unknown"


def test_malformed_request_error_is_an_exception() -> None:
    assert issubclass(MalformedRequestError, Exception)


def test_slots_for_json_strings_visits_values_depth_first_and_never_keys() -> None:
    value: dict[str, Any] = {
        "to": "juan.perez@example.com",
        "cc": ["a@example.org", {"name": "Alice Johnson", "age": 41}],
        "flags": [True, None, 1.5],
        "last": "end",
    }
    slots = slots_for_json_strings(value)
    assert [slot.get() for slot in slots] == [
        "juan.perez@example.com",
        "a@example.org",
        "Alice Johnson",
        "end",
    ]
    for number, slot in enumerate(slots):
        slot.set(f"[X_{number + 1}]")
    assert value == {
        "to": "[X_1]",
        "cc": ["[X_2]", {"name": "[X_3]", "age": 41}],
        "flags": [True, None, 1.5],
        "last": "[X_4]",
    }


def test_slots_for_json_strings_handles_deep_nesting_without_recursion() -> None:
    value: list[Any] = ["deep"]
    for _ in range(5000):
        value = [value]
    slots = slots_for_json_strings(value)
    assert [slot.get() for slot in slots] == ["deep"]


@given(_CONTAINERS)
def test_slots_for_json_strings_has_one_slot_per_string_value(container: Any) -> None:
    slots = slots_for_json_strings(container)
    assert [slot.get() for slot in slots] == _strings_depth_first(container)


@given(_CONTAINERS)
def test_applying_the_identity_through_json_slots_leaves_the_structure_equal(
    container: Any,
) -> None:
    original = copy.deepcopy(container)
    for slot in slots_for_json_strings(container):
        slot.set(slot.get())
    assert container == original


@given(_CONTAINERS)
def test_json_slots_write_back_in_depth_first_order_and_leave_keys_alone(container: Any) -> None:
    keys = _keys(container)
    slots = slots_for_json_strings(container)
    for number, slot in enumerate(slots):
        slot.set(f"#{number}")
    assert _strings_depth_first(container) == [f"#{number}" for number in range(len(slots))]
    assert _keys(container) == keys


def test_slots_for_key_strings_finds_string_values_under_the_key_anywhere() -> None:
    tools: list[Any] = [
        {
            "name": "send_email",
            "description": "top",
            "parameters": {
                "properties": {
                    "to": {"type": "string", "description": "inner", "enum": ["description"]},
                    "description": {"type": "string", "description": "property named description"},
                    "list": [{"description": "in a list"}],
                    "other": {"description": ["not a string value"]},
                }
            },
        },
        "description",
    ]
    slots = slots_for_key_strings(tools, "description")
    assert [slot.get() for slot in slots] == [
        "top",
        "inner",
        "property named description",
        "in a list",
    ]
    slots[0].set("[NEW_1]")
    assert tools[0]["description"] == "[NEW_1]"


def test_restore_all_strings_restores_nested_strings_and_leaves_other_values_alone() -> None:
    body: dict[str, Any] = {
        "error": {
            "message": "Invalid value for [PERSON_1] and [EMAIL_1]",
            "param": None,
            "code": 400,
            "retry": False,
            "ratio": 0.5,
            "details": ["[person_1]", {"[PERSON_1]": "[EMAIL_1]"}, "[PERSON_9]"],
        }
    }
    original = copy.deepcopy(body)
    restored = restore_all_strings(body, _mapping())
    assert restored == {
        "error": {
            "message": "Invalid value for Juan Pérez and juan.perez@example.com",
            "param": None,
            "code": 400,
            "retry": False,
            "ratio": 0.5,
            "details": ["Juan Pérez", {"Juan Pérez": "juan.perez@example.com"}, "[PERSON_9]"],
        }
    }
    assert body == original


@pytest.mark.parametrize("value", [None, True, 7, 2.5])
def test_restore_all_strings_returns_scalars_unchanged(value: Any) -> None:
    assert restore_all_strings(value, _mapping()) == value


def test_restore_all_strings_restores_a_bare_string() -> None:
    assert restore_all_strings("Hi [PERSON_1]", _mapping()) == "Hi Juan Pérez"


def test_restore_all_strings_never_merges_a_restored_key_with_an_existing_one() -> None:
    restored = restore_all_strings({"[PERSON_1]": 1, "Juan Pérez": 2}, _mapping())
    assert restored == {"[PERSON_1]": 1, "Juan Pérez": 2}
    assert list(restored) == ["[PERSON_1]", "Juan Pérez"]


def test_restore_all_strings_never_merges_two_keys_with_the_same_restored_form() -> None:
    restored = restore_all_strings({"[PERSON_1]": 1, "[person_1]": 2}, _mapping())
    assert restored == {"Juan Pérez": 1, "[person_1]": 2}
    assert list(restored) == ["Juan Pérez", "[person_1]"]


def test_restore_all_strings_restores_keys_in_order_at_every_depth() -> None:
    value = {"b": {"[EMAIL_1]": ["[PERSON_1]"]}, "[PERSON_1]": True}
    restored = restore_all_strings(value, _mapping())
    assert restored == {"b": {"juan.perez@example.com": ["Juan Pérez"]}, "Juan Pérez": True}
    assert list(restored) == ["b", "Juan Pérez"]


# Keys and strings of data objects (slice 21)


def _key_and_string_slots(
    container: dict[str, Any] | list[Any], *, skip_under: str | None = None
) -> tuple[list[Slot], list[Any]]:
    finalizers: list[Any] = []
    slots = slots_for_keys_and_strings(container, finalizers, "where", skip_under=skip_under)
    return slots, finalizers


def test_slots_for_keys_and_strings_gives_each_key_before_its_value_depth_first() -> None:
    value: dict[str, Any] = {
        "to": "juan.perez@example.com",
        "cc": ["a@example.org", {"name": "Alice Johnson", "age": 41}],
        "flags": [True, None, 1.5],
        "empty": {},
        "last": "end",
    }
    slots, _ = _key_and_string_slots(value)
    assert [slot.get() for slot in slots] == [
        "to",
        "juan.perez@example.com",
        "cc",
        "a@example.org",
        "name",
        "Alice Johnson",
        "age",
        "flags",
        "empty",
        "last",
        "end",
    ]
    assert [slot.label for slot in slots] == [
        True, False, True, False, True, False, True, True, True, True, False,
    ]  # fmt: skip


def test_a_list_container_gives_its_strings_and_the_keys_of_its_objects() -> None:
    slots, _ = _key_and_string_slots(["x", {"k": "v"}, [{"j": 1}]])
    assert [(slot.get(), slot.label) for slot in slots] == [
        ("x", False),
        ("k", True),
        ("v", False),
        ("j", True),
    ]


def test_skip_under_skips_the_string_value_and_keeps_its_key() -> None:
    value: dict[str, Any] = {
        "description": "already a slot",
        "to": "a@example.org",
        "nested": [{"description": "also skipped", "other": "kept"}],
        "list": {"description": ["a string in a list is kept"]},
    }
    slots, _ = _key_and_string_slots(value, skip_under="description")
    assert [slot.get() for slot in slots] == [
        "description",
        "to",
        "a@example.org",
        "nested",
        "description",
        "other",
        "kept",
        "list",
        "description",
        "a string in a list is kept",
    ]


def test_applying_key_and_string_slots_unchanged_changes_nothing() -> None:
    value: dict[str, Any] = {"a": {"b": ["c", {"d": "e"}]}, "f": 1}
    original = copy.deepcopy(value)
    slots, finalizers = _key_and_string_slots(value)
    prepared = PreparedRequest(slots, finalizers)
    prepared.apply(prepared.texts())
    assert value == original
    assert list(value) == ["a", "f"]


def test_renaming_keys_keeps_their_order_and_their_values() -> None:
    inner: dict[str, Any] = {"x": "1"}
    value: dict[str, Any] = {"Alice Johnson": "accepted", "b": inner, "c": 3}
    slots, finalizers = _key_and_string_slots(value)
    prepared = PreparedRequest(slots, finalizers)
    assert prepared.texts() == ["Alice Johnson", "accepted", "b", "x", "1", "c"]
    prepared.apply(["[PERSON_1]", "ok", "[B]", "x", "[ONE]", "c"])
    assert value == {"[PERSON_1]": "ok", "[B]": {"x": "[ONE]"}, "c": 3}
    assert list(value) == ["[PERSON_1]", "[B]", "c"]
    assert value["[B]"] is inner
    assert prepared.texts() == ["[PERSON_1]", "ok", "[B]", "x", "[ONE]", "c"]


class _Recording(dict[str, Any]):
    """A dict that records every call that would change it."""

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.changes: list[str] = []

    def __setitem__(self, key: str, value: Any) -> None:
        self.changes.append("setitem")
        super().__setitem__(key, value)

    def __delitem__(self, key: str) -> None:
        self.changes.append("delitem")
        super().__delitem__(key)

    def clear(self) -> None:
        self.changes.append("clear")
        super().clear()

    def pop(self, *args: Any) -> Any:
        self.changes.append("pop")
        return super().pop(*args)

    def update(self, *args: Any, **kwargs: Any) -> None:
        self.changes.append("update")
        super().update(*args, **kwargs)


def test_an_object_whose_keys_did_not_change_is_left_untouched_by_its_renaming_step() -> None:
    value = _Recording({"a": "x", "b": "y"})
    slots, finalizers = _key_and_string_slots(value)
    assert len(finalizers) == 1
    for finalize in finalizers:
        finalize()
    assert value.changes == []
    slots[0].set("a")
    for finalize in finalizers:
        finalize()
    assert value.changes == []
    assert value == {"a": "x", "b": "y"}


def test_two_keys_that_would_become_equal_raise_without_naming_them_and_change_nothing() -> None:
    inner: dict[str, Any] = {"ACME": "a", "Acme": "b"}
    value: dict[str, Any] = {"orgs": inner}
    finalizers: list[Any] = []
    slots = slots_for_keys_and_strings(value, finalizers, "metadata")
    prepared = PreparedRequest(slots, finalizers)
    with pytest.raises(MalformedRequestError) as excinfo:
        prepared.apply(["orgs", "[ORG_1]", "a", "[ORG_1]", "b"])
    assert str(excinfo.value) == "metadata: two keys of one object would become the same"
    assert "ACME" not in str(excinfo.value)
    assert "Acme" not in str(excinfo.value)
    assert "ORG" not in str(excinfo.value)
    assert list(inner) == ["ACME", "Acme"]


def test_slots_for_keys_and_strings_handles_deep_nesting_without_recursion() -> None:
    value: dict[str, Any] = {"leaf": "deep"}
    for _ in range(5000):
        value = {"k": value}
    slots, _ = _key_and_string_slots(value)
    assert len(slots) == 5002
    assert slots[-1].get() == "deep"


def test_a_key_slot_whose_key_is_not_a_string_raises_without_the_key() -> None:
    value: dict[Any, Any] = {7: "x"}
    slots, _ = _key_and_string_slots(value)
    with pytest.raises(TypeError) as excinfo:
        slots[0].get()
    assert "7" not in str(excinfo.value)


def test_labels_are_the_indexes_of_the_label_slots() -> None:
    value: dict[str, Any] = {"k": "v", "l": ["w"]}
    slots, finalizers = _key_and_string_slots(value)
    box = ["text"]
    text = Slot(lambda: box[0], lambda new: box.__setitem__(0, new))
    prepared = PreparedRequest([text, *slots], finalizers)
    assert prepared.labels() == frozenset({1, 3})
    assert isinstance(prepared.labels(), frozenset)
    assert PreparedRequest([text], []).labels() == frozenset()


def test_a_slot_is_a_text_slot_unless_it_says_it_is_a_label() -> None:
    assert Slot(get=lambda: "x", set=lambda value: None).label is False
    assert Slot(get=lambda: "x", set=lambda value: None, label=True).label is True


_KEYED_LEAVES = st.text() | st.integers() | st.booleans() | st.none()
_LEVEL_1 = st.dictionaries(
    st.text(), _KEYED_LEAVES | st.lists(_KEYED_LEAVES, max_size=3), max_size=4
)
_LEVEL_2 = st.dictionaries(st.text(), _KEYED_LEAVES | _LEVEL_1, max_size=4)
_LEVEL_3 = st.dictionaries(
    st.text(), _KEYED_LEAVES | _LEVEL_2 | st.lists(_LEVEL_1, max_size=2), max_size=4
)


def _keys_and_strings_depth_first(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [
            s for key, item in value.items() for s in [key, *_keys_and_strings_depth_first(item)]
        ]
    if isinstance(value, list):
        return [s for item in value for s in _keys_and_strings_depth_first(item)]
    return []


def _shape(value: Any) -> Any:
    if isinstance(value, str):
        return str
    if isinstance(value, dict):
        return ("object", [_shape(item) for item in value.values()])
    if isinstance(value, list):
        return ("array", [_shape(item) for item in value])
    return value


@given(_LEVEL_3)
def test_property_key_and_string_slots_round_trip_and_rename_every_key_in_order(
    value: dict[str, Any],
) -> None:
    original = copy.deepcopy(value)
    slots, finalizers = _key_and_string_slots(value)
    prepared = PreparedRequest(slots, finalizers)
    assert prepared.texts() == _keys_and_strings_depth_first(original)
    prepared.apply(prepared.texts())
    assert value == original
    assert _keys_and_strings_depth_first(value) == _keys_and_strings_depth_first(original)
    new_texts = [f"[SLOT_{number}]" for number in range(1, len(slots) + 1)]
    prepared.apply(new_texts)
    assert _keys_and_strings_depth_first(value) == new_texts
    assert _shape(value) == _shape(original)


_FORBIDDEN_IMPORTS = ("aliasgate.proxy", "httpx", "starlette", "socket", "urllib", "os", "io")


@pytest.mark.parametrize("path", sorted(DIALECTS_DIR.glob("*.py")), ids=lambda path: path.name)
def test_dialect_modules_import_nothing_from_proxy_and_do_no_io(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        for name in names:
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for forbidden in _FORBIDDEN_IMPORTS
            ), f"{path.name} imports {name}"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"open", "print", "input"}, (
                f"{path.name} calls {node.func.id}"
            )
