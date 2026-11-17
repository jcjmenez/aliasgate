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
            "details": ["Juan Pérez", {"[PERSON_1]": "juan.perez@example.com"}, "[PERSON_9]"],
        }
    }
    assert body == original


@pytest.mark.parametrize("value", [None, True, 7, 2.5])
def test_restore_all_strings_returns_scalars_unchanged(value: Any) -> None:
    assert restore_all_strings(value, _mapping()) == value


def test_restore_all_strings_restores_a_bare_string() -> None:
    assert restore_all_strings("Hi [PERSON_1]", _mapping()) == "Hi Juan Pérez"


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
