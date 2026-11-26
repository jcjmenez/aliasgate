import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aliasgate.detectors.dictionary import DictionaryDetector, Term
from aliasgate.detectors.rules import RuleDetector
from aliasgate.dialects.base import (
    Dialect,
    MalformedRequestError,
    Policy,
    UnsupportedContentError,
)
from aliasgate.dialects.openai_chat import OpenAIChatDialect
from aliasgate.engine import Pseudonymizer
from aliasgate.mapping import Mapping

FIXTURES = Path(__file__).parent / "fixtures" / "openai_chat"
DIALECT = OpenAIChatDialect()
PASS = Policy(unsupported_content="pass")


def _load(name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return body


def _texts(body: dict[str, Any], policy: Policy | None = None) -> list[str]:
    return DIALECT.prepare_request(body, policy or Policy()).texts()


def _user(content: Any) -> dict[str, Any]:
    return {"model": "gpt-example", "messages": [{"role": "user", "content": content}]}


def _assistant_call(arguments: str) -> dict[str, Any]:
    call = {"id": "call_1", "type": "function", "function": {"name": "f", "arguments": arguments}}
    return {"messages": [{"role": "assistant", "content": None, "tool_calls": [call]}]}


def _arguments(body: dict[str, Any]) -> str:
    arguments: str = body["messages"][0]["tool_calls"][0]["function"]["arguments"]
    return arguments


def _mapping() -> Mapping:
    mapping = Mapping()
    mapping.assign("PERSON", "Juan Pérez")
    mapping.assign("EMAIL", "juan.perez@example.com")
    return mapping


def test_the_dialect_satisfies_the_dialect_protocol() -> None:
    dialect: Dialect = DIALECT
    assert dialect.name == "openai_chat"


# Request side


def test_string_content_in_system_user_and_assistant_messages_gives_one_slot_each() -> None:
    body: dict[str, Any] = {
        "messages": [
            {"role": "system", "content": "You help Alice Johnson."},
            {"role": "user", "content": "I am Alice Johnson."},
            {"role": "assistant", "content": "Hello Alice Johnson."},
        ]
    }
    assert _texts(body) == [
        "You help Alice Johnson.",
        "I am Alice Johnson.",
        "Hello Alice Johnson.",
    ]


def test_null_or_absent_content_gives_no_slot() -> None:
    body: dict[str, Any] = {
        "messages": [{"role": "assistant", "content": None}, {"role": "assistant"}]
    }
    assert _texts(body) == []


def test_list_content_with_two_text_parts_gives_two_slots_written_by_apply() -> None:
    body = _user([{"type": "text", "text": "Alice Johnson"}, {"type": "text", "text": "ACME"}])
    prepared = DIALECT.prepare_request(body, Policy())
    assert prepared.texts() == ["Alice Johnson", "ACME"]
    prepared.apply(["[PERSON_1]", "[ORG_1]"])
    assert body["messages"][0]["content"] == [
        {"type": "text", "text": "[PERSON_1]"},
        {"type": "text", "text": "[ORG_1]"},
    ]


def _image_body() -> dict[str, Any]:
    return _user(
        [
            {"type": "text", "text": "What is in this picture of Alice Johnson?"},
            {
                "type": "image_url",
                "image_url": {"url": "https://example.com/a.png", "detail": "low"},
            },
            {"type": "text", "text": "Answer briefly."},
        ]
    )


def test_image_part_is_rejected_under_block() -> None:
    with pytest.raises(UnsupportedContentError) as excinfo:
        DIALECT.prepare_request(_image_body(), Policy())
    assert excinfo.value.kind == "image_url"


def test_image_part_is_left_untouched_under_pass_and_text_slots_remain() -> None:
    body = _image_body()
    image_part = copy.deepcopy(body["messages"][0]["content"][1])
    prepared = DIALECT.prepare_request(body, PASS)
    assert prepared.texts() == ["What is in this picture of Alice Johnson?", "Answer briefly."]
    prepared.apply(["[A_1]", "[B_1]"])
    assert body["messages"][0]["content"][1] == image_part


@pytest.mark.parametrize(
    "part",
    [
        {"type": "input_audio", "input_audio": {"data": "AAAA", "format": "wav"}},
        {"type": "file", "file": {"file_id": "file-0001", "filename": "contract.pdf"}},
        {"type": "video_url", "video_url": {"url": "https://example.com/v.mp4"}},
    ],
    ids=lambda part: part["type"],
)
def test_other_and_unknown_part_types_behave_like_an_image_part(part: dict[str, Any]) -> None:
    body = _user([{"type": "text", "text": "Alice Johnson"}, part])
    with pytest.raises(UnsupportedContentError) as excinfo:
        DIALECT.prepare_request(copy.deepcopy(body), Policy())
    assert excinfo.value.kind == part["type"]
    prepared = DIALECT.prepare_request(body, PASS)
    assert prepared.texts() == ["Alice Johnson"]
    assert body["messages"][0]["content"][1] == part


def test_a_part_type_that_is_not_a_label_is_reported_as_unknown() -> None:
    body = _user([{"type": "Juan Pérez", "text": "x"}])
    with pytest.raises(UnsupportedContentError) as excinfo:
        DIALECT.prepare_request(body, Policy())
    assert excinfo.value.kind == "unknown"
    assert "Juan" not in str(excinfo.value)


def test_assistant_tool_call_arguments_become_slots_and_are_reserialised_compactly() -> None:
    body = _assistant_call('{"to": "juan.perez@example.com", "cc": ["a@example.org"]}')
    prepared = DIALECT.prepare_request(body, Policy())
    assert prepared.texts() == ["to", "juan.perez@example.com", "cc", "a@example.org"]
    prepared.apply(["to", "[EMAIL_1]", "cc", "[EMAIL_2]"])
    arguments = _arguments(body)
    assert arguments == '{"to":"[EMAIL_1]","cc":["[EMAIL_2]"]}'
    assert json.loads(arguments) == {"to": "[EMAIL_1]", "cc": ["[EMAIL_2]"]}


def test_escaped_strings_in_arguments_are_decoded_before_detection() -> None:
    body = _assistant_call('{"name": "Jos\\u00e9 \\"Pepe\\""}')
    assert _texts(body) == ["name", 'José "Pepe"']


@pytest.mark.parametrize(
    "arguments",
    ['{"to": "juan.perez@exa', "not json at all", '"juan.perez@example.com"', "42", "null", ""],
)
def test_arguments_that_are_not_a_json_object_or_array_are_one_raw_slot(arguments: str) -> None:
    body = _assistant_call(arguments)
    prepared = DIALECT.prepare_request(body, Policy())
    assert prepared.texts() == [arguments]
    prepared.apply([arguments.replace("juan.perez@example.com", "[EMAIL_1]") + " "])
    assert _arguments(body) == arguments.replace("juan.perez@example.com", "[EMAIL_1]") + " "


def test_non_ascii_arguments_are_written_back_unescaped() -> None:
    body = _assistant_call('{"name": "José", "city": "Málaga"}')
    prepared = DIALECT.prepare_request(body, Policy())
    prepared.apply(prepared.texts())
    assert _arguments(body) == '{"name":"José","city":"Málaga"}'


def test_custom_tool_call_input_is_one_slot() -> None:
    call = {"id": "c", "type": "custom", "custom": {"name": "q", "input": "find Alice Johnson"}}
    body: dict[str, Any] = {"messages": [{"role": "assistant", "tool_calls": [call]}]}
    prepared = DIALECT.prepare_request(body, Policy())
    assert prepared.texts() == ["find Alice Johnson"]
    prepared.apply(["find [PERSON_1]"])
    assert call["custom"] == {"name": "q", "input": "find [PERSON_1]"}


def test_unknown_tool_call_type_is_rejected_under_block_and_skipped_under_pass() -> None:
    call = {"id": "c", "type": "computer_action", "computer_action": {"text": "Alice Johnson"}}
    body: dict[str, Any] = {
        "messages": [{"role": "assistant", "content": "ok", "tool_calls": [call]}]
    }
    with pytest.raises(UnsupportedContentError) as excinfo:
        DIALECT.prepare_request(copy.deepcopy(body), Policy())
    assert excinfo.value.kind == "computer_action"
    assert _texts(body, PASS) == ["ok"]


def test_legacy_function_call_arguments_are_treated_like_tool_call_arguments() -> None:
    body: dict[str, Any] = {
        "messages": [
            {
                "role": "assistant",
                "content": None,
                "function_call": {"name": "lookup", "arguments": '{"who": "Alice Johnson"}'},
            }
        ]
    }
    prepared = DIALECT.prepare_request(body, Policy())
    assert prepared.texts() == ["who", "Alice Johnson"]
    prepared.apply(["who", "[PERSON_1]"])
    assert body["messages"][0]["function_call"] == {
        "name": "lookup",
        "arguments": '{"who":"[PERSON_1]"}',
    }


def test_assistant_message_refusal_field_is_a_slot() -> None:
    body: dict[str, Any] = {
        "messages": [{"role": "assistant", "content": None, "refusal": "Not about Alice."}]
    }
    prepared = DIALECT.prepare_request(body, Policy())
    assert prepared.texts() == ["Not about Alice."]
    prepared.apply(["Not about [PERSON_1]."])
    assert body["messages"][0]["refusal"] == "Not about [PERSON_1]."


def test_refusal_parts_in_assistant_content_are_slots() -> None:
    body: dict[str, Any] = {
        "messages": [
            {"role": "assistant", "content": [{"type": "refusal", "refusal": "No, Alice."}]}
        ]
    }
    assert _texts(body) == ["No, Alice."]


def test_tool_result_messages_with_string_and_list_content_are_slots() -> None:
    body: dict[str, Any] = {
        "messages": [
            {"role": "tool", "tool_call_id": "call_1", "content": "Sent to juan.perez@example.com"},
            {
                "role": "tool",
                "tool_call_id": "call_2",
                "content": [
                    {"type": "text", "text": "Alice Johnson"},
                    {"type": "text", "text": "ok"},
                ],
            },
        ]
    }
    prepared = DIALECT.prepare_request(body, Policy())
    assert prepared.texts() == ["Sent to juan.perez@example.com", "Alice Johnson", "ok"]
    prepared.apply(["Sent to [EMAIL_1]", "[PERSON_1]", "ok"])
    assert body["messages"][0]["content"] == "Sent to [EMAIL_1]"
    assert body["messages"][1]["content"][0]["text"] == "[PERSON_1]"
    assert body["messages"][0]["tool_call_id"] == "call_1"


def test_prediction_content_as_string_and_as_text_parts_is_a_slot() -> None:
    as_string: dict[str, Any] = {
        "messages": [],
        "prediction": {"type": "content", "content": "draft"},
    }
    as_parts: dict[str, Any] = {
        "messages": [],
        "prediction": {"type": "content", "content": [{"type": "text", "text": "draft part"}]},
    }
    assert _texts(as_string) == ["draft"]
    assert _texts(as_parts) == ["draft part"]


def test_tool_descriptions_then_schema_values_are_slots_and_names_and_property_names_are_not() -> (
    None
):
    body = _load("request_all_fields.json")
    body["messages"] = []
    for key in ("prediction", "stop", "metadata", "prompt_cache_key"):
        del body[key]
    assert _texts(body) == [
        "tool description",
        "to description",
        "nested description",
        "custom tool description",
        "function description",
        "query description",
        "response format description",
        "enum value",
    ]


def test_with_tool_definitions_off_no_tool_slot_exists() -> None:
    body = _load("request_all_fields.json")
    texts = _texts(body, Policy(tool_definitions=False))
    assert not any("description" in text for text in texts)
    assert texts[-5:] == ["prediction text", "stop text", "ticket", "T-0001", "cache key"]


def test_slot_order_is_messages_then_prediction_then_tools_then_request_fields() -> None:
    assert _texts(_load("request_all_fields.json")) == [
        "system text",
        "developer part 1",
        "developer part 2",
        "customer",
        "user part",
        "assistant part",
        "refusal part",
        "assistant refusal",
        "to",
        "to argument",
        "cc",
        "cc argument",
        "custom input",
        "tool result",
        "tool result part",
        "query",
        "function call argument",
        "function result",
        "prediction text",
        "tool description",
        "to description",
        "nested description",
        "custom tool description",
        "function description",
        "query description",
        "response format description",
        "enum value",
        "stop text",
        "ticket",
        "T-0001",
        "cache key",
    ]


def test_fields_outside_the_slots_are_never_touched() -> None:
    body = _load("request_all_fields.json")
    original = copy.deepcopy(body)
    prepared = DIALECT.prepare_request(body, Policy())
    texts = prepared.texts()
    new_texts = [f"[SLOT_{number}]" for number in range(1, len(texts) + 1)]
    prepared.apply(new_texts)
    written = dict(zip(texts, new_texts, strict=True))
    for key in ("model", "temperature"):
        assert body[key] == original[key]
    assert body["web_search_options"] == {"search_context_size": "low"}
    assert body["metadata"] == {written["ticket"]: written["T-0001"]}
    json_schema = body["response_format"]["json_schema"]
    assert json_schema["name"] == "answer"
    assert json_schema["schema"] == {"type": "object"}
    for message, before in zip(body["messages"], original["messages"], strict=True):
        assert message["role"] == before["role"]
        assert message.get("tool_call_id") == before.get("tool_call_id")
        assert message.get("audio") == before.get("audio")
    assert body["messages"][7]["name"] == "lookup"
    calls = body["messages"][3]["tool_calls"]
    assert [call["id"] for call in calls] == ["call_0001", "call_0002"]
    assert calls[0]["function"]["name"] == "send_email"
    assert json.loads(calls[0]["function"]["arguments"]) == {
        written["to"]: written["to argument"],
        written["cc"]: [written["cc argument"]],
    }
    assert calls[1]["custom"]["name"] == "run_query"
    assert json.loads(body["messages"][6]["function_call"]["arguments"]) == {
        written["query"]: written["function call argument"]
    }
    function = body["tools"][0]["function"]
    assert function["name"] == "send_email"
    assert function["strict"] is True
    properties = function["parameters"]["properties"]
    assert list(properties) == ["to", "description"]
    assert properties["to"]["enum"] == [written["enum value"]]
    assert properties["to"]["type"] == "string"
    assert properties["description"] == {
        "type": "string",
        "description": written["nested description"],
    }
    assert function["parameters"]["required"] == ["to"]
    assert body["tools"][1]["custom"]["format"] == original["tools"][1]["custom"]["format"]
    assert body["functions"][0]["name"] == "lookup"


def test_applying_the_texts_unchanged_changes_nothing_but_the_stripped_identifiers() -> None:
    body = _load("request_all_fields.json")
    original = copy.deepcopy(body)
    prepared = DIALECT.prepare_request(body, Policy())
    prepared.apply(prepared.texts())
    del original["user"], original["safety_identifier"]
    del original["web_search_options"]["user_location"]
    assert body == original


def test_user_identifiers_are_removed_by_default() -> None:
    body = _load("request_all_fields.json")
    DIALECT.prepare_request(body, Policy())
    assert "user" not in body
    assert "safety_identifier" not in body
    assert body["metadata"] == {"ticket": "T-0001"}


def test_user_identifiers_are_kept_when_stripping_is_off() -> None:
    body = _load("request_all_fields.json")
    DIALECT.prepare_request(body, Policy(strip_user_identifiers=False))
    assert body["user"] == "end-user-0001"
    assert body["safety_identifier"] == "hashed-end-user-0001"


def test_a_body_without_identifiers_is_fine() -> None:
    body: dict[str, Any] = {"messages": []}
    assert _texts(body) == []
    assert body == {"messages": []}


# Request fields read since slice 20


def test_a_message_name_is_a_slot_before_its_content() -> None:
    body: dict[str, Any] = {
        "messages": [
            {"role": "system", "name": "sys name", "content": "system text"},
            {"role": "user", "name": "user name", "content": "user text"},
            {"role": "assistant", "name": "assistant name", "content": "assistant text"},
            {"role": "developer", "name": "dev name", "content": "developer text"},
        ]
    }
    assert _texts(body) == [
        "sys name",
        "system text",
        "user name",
        "user text",
        "assistant name",
        "assistant text",
        "dev name",
        "developer text",
    ]


def test_the_name_of_a_function_message_is_not_a_slot() -> None:
    body: dict[str, Any] = {
        "messages": [{"role": "function", "name": "lookup", "content": "function result"}]
    }
    assert _texts(body) == ["function result"]


def test_a_null_message_name_adds_no_slot() -> None:
    body: dict[str, Any] = {"messages": [{"role": "user", "name": None, "content": "x"}]}
    assert _texts(body) == ["x"]


def test_stop_as_a_string_is_one_slot_and_as_a_list_one_slot_per_item() -> None:
    assert _texts({"messages": [], "stop": "END"}) == ["END"]
    assert _texts({"messages": [], "stop": ["a", "b", "c"]}) == ["a", "b", "c"]
    assert _texts({"messages": [], "stop": None}) == []


def test_metadata_keys_and_values_are_slots_in_key_order() -> None:
    body: dict[str, Any] = {"messages": [], "metadata": {"b": "second", "a": "first"}}
    prepared = DIALECT.prepare_request(body, Policy())
    assert prepared.texts() == ["b", "second", "a", "first"]
    prepared.apply(["[K_1]", "[X_1]", "[K_2]", "[X_2]"])
    assert body["metadata"] == {"[K_1]": "[X_1]", "[K_2]": "[X_2]"}
    assert list(body["metadata"]) == ["[K_1]", "[K_2]"]


def test_null_metadata_and_cache_key_add_no_slot() -> None:
    assert _texts({"messages": [], "metadata": None, "prompt_cache_key": None}) == []


def test_request_fields_come_after_tool_definitions_in_a_fixed_order() -> None:
    body: dict[str, Any] = {
        "messages": [{"role": "user", "content": "text"}],
        "prompt_cache_key": "key",
        "metadata": {"m": "meta"},
        "stop": "stop",
        "tools": [{"type": "function", "function": {"name": "f", "description": "tool"}}],
    }
    assert _texts(body) == ["text", "tool", "stop", "m", "meta", "key"]


def test_request_fields_are_slots_even_with_tool_definitions_off() -> None:
    body: dict[str, Any] = {"messages": [], "stop": "s", "metadata": {"m": "v"}}
    assert _texts(body, Policy(tool_definitions=False)) == ["s", "m", "v"]


def test_response_format_descriptions_are_slots_at_any_depth() -> None:
    schema = {
        "type": "object",
        "properties": {"to": {"type": "string", "description": "inner", "enum": ["x"]}},
    }
    body: dict[str, Any] = {
        "messages": [],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "reply", "description": "outer", "schema": schema},
        },
    }
    prepared = DIALECT.prepare_request(body, Policy())
    assert prepared.texts() == ["outer", "inner", "x"]
    prepared.apply(["[A]", "[B]", "[C]"])
    json_schema = body["response_format"]["json_schema"]
    assert json_schema["name"] == "reply"
    assert json_schema["description"] == "[A]"
    assert json_schema["schema"]["properties"]["to"] == {
        "type": "string",
        "description": "[B]",
        "enum": ["[C]"],
    }


def test_a_text_response_format_adds_no_slot() -> None:
    assert _texts({"messages": [], "response_format": {"type": "text"}}) == []


def test_response_format_is_not_read_with_tool_definitions_off() -> None:
    body: dict[str, Any] = {"messages": [], "response_format": "json"}
    assert _texts(body, Policy(tool_definitions=False)) == []
    assert body["response_format"] == "json"


# Schema values (slice 22)


def _schema(word: str) -> dict[str, Any]:
    owner = {"type": "string", "description": f"{word} description", "enum": [f"{word} enum"]}
    return {"type": "object", "title": f"{word} title", "properties": {"owner": owner}}


def _schema_body() -> dict[str, Any]:
    return {
        "messages": [{"role": "user", "content": "text"}],
        "tools": [
            {
                "type": "function",
                "function": {"name": "a", "description": "tool", "parameters": _schema("tool")},
            },
            {"type": "function", "function": {"name": "b", "parameters": _schema("second tool")}},
        ],
        "functions": [{"name": "c", "parameters": _schema("function")}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "r", "strict": True, "schema": _schema("response")},
        },
        "stop": "stop",
    }


def test_schema_values_come_after_every_description_and_before_the_request_fields() -> None:
    assert _texts(_schema_body()) == [
        "text",
        "tool",
        "tool description",
        "second tool description",
        "function description",
        "response description",
        "tool title",
        "tool enum",
        "second tool title",
        "second tool enum",
        "function title",
        "function enum",
        "response title",
        "response enum",
        "stop",
    ]


def test_with_tool_definitions_off_no_schema_value_is_a_slot() -> None:
    body = _schema_body()
    original = copy.deepcopy(body)
    assert _texts(body, Policy(tool_definitions=False)) == ["text", "stop"]
    assert body == original


def test_schema_enum_slots_are_labels_and_title_slots_are_not() -> None:
    prepared = DIALECT.prepare_request(_schema_body(), Policy())
    texts = prepared.texts()
    assert sorted(texts[index] for index in prepared.labels()) == [
        "function enum",
        "response enum",
        "second tool enum",
        "tool enum",
    ]


def test_schema_value_slots_write_back_into_each_schema() -> None:
    body = _schema_body()
    prepared = DIALECT.prepare_request(body, Policy())
    prepared.apply([text.upper() for text in prepared.texts()])
    assert body["tools"][1]["function"]["parameters"]["title"] == "SECOND TOOL TITLE"
    owner = body["functions"][0]["parameters"]["properties"]["owner"]
    assert owner == {
        "type": "string",
        "description": "FUNCTION DESCRIPTION",
        "enum": ["FUNCTION ENUM"],
    }
    json_schema = body["response_format"]["json_schema"]
    assert json_schema["schema"]["title"] == "RESPONSE TITLE"
    assert json_schema["name"] == "r"
    assert list(json_schema["schema"]["properties"]) == ["owner"]


@pytest.mark.parametrize(
    "tool",
    [
        "a tool",
        {"type": "function", "function": "f"},
        {"type": "function", "function": {"name": "f"}},
        {"type": "function", "function": {"name": "f", "parameters": True}},
        {"type": "custom", "custom": {"name": "c", "format": {"type": "text", "title": "t"}}},
    ],
)
def test_a_tool_without_a_parameters_object_gives_no_schema_slot(tool: Any) -> None:
    body: dict[str, Any] = {
        "messages": [],
        "tools": [tool],
        "functions": ["a function", {"name": "g"}, {"name": "h", "parameters": ["title"]}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "r"}},
    }
    original = copy.deepcopy(body)
    assert _texts(body) == []
    assert body == original


@pytest.mark.parametrize("json_schema", [None, "a schema", ["title"]])
def test_a_response_format_without_a_json_schema_object_gives_no_schema_slot(
    json_schema: Any,
) -> None:
    body: dict[str, Any] = {
        "messages": [],
        "response_format": {"type": "json_schema", "json_schema": json_schema},
    }
    assert _texts(body) == []


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            {"messages": [{"role": "user", "name": 7, "content": "x"}]},
            "messages[0].name: expected a string",
        ),
        ({"messages": [], "stop": 3}, "stop: expected a string or a list"),
        ({"messages": [], "stop": ["a", 1]}, "stop[1]: expected a string"),
        ({"messages": [], "metadata": []}, "metadata: expected an object"),
        (
            {"messages": [], "metadata": {"note": "x", "maria.lopez@example.org": 2}},
            "metadata: value 1: expected a string",
        ),
        ({"messages": [], "prompt_cache_key": 5}, "prompt_cache_key: expected a string"),
        ({"messages": [], "response_format": "json"}, "response_format: expected an object"),
    ],
)
def test_request_fields_of_the_wrong_shape_are_malformed(
    body: dict[str, Any], message: str
) -> None:
    with pytest.raises(MalformedRequestError) as excinfo:
        DIALECT.prepare_request(body, Policy())
    assert str(excinfo.value) == message
    assert "maria" not in str(excinfo.value)


def test_user_location_is_removed_by_default_and_web_search_options_are_kept() -> None:
    location = {"type": "approximate", "approximate": {"city": "Springfield"}}
    body: dict[str, Any] = {
        "messages": [],
        "web_search_options": {"search_context_size": "low", "user_location": location},
    }
    DIALECT.prepare_request(body, Policy())
    assert body["web_search_options"] == {"search_context_size": "low"}


def test_web_search_options_stay_when_the_location_was_their_only_key() -> None:
    body: dict[str, Any] = {
        "messages": [],
        "web_search_options": {"user_location": {"type": "approximate"}},
    }
    DIALECT.prepare_request(body, Policy())
    assert body["web_search_options"] == {}


def test_user_location_is_kept_when_its_stripping_is_off() -> None:
    body = _load("request_all_fields.json")
    original = copy.deepcopy(body["web_search_options"])
    DIALECT.prepare_request(body, Policy(strip_user_location=False))
    assert body["web_search_options"] == original
    assert "user" not in body


def test_location_and_identifier_stripping_are_independent() -> None:
    body = _load("request_all_fields.json")
    DIALECT.prepare_request(body, Policy(strip_user_identifiers=False))
    assert "user_location" not in body["web_search_options"]
    assert body["user"] == "end-user-0001"


def test_web_search_options_that_are_not_an_object_are_left_to_the_provider() -> None:
    body: dict[str, Any] = {"messages": [], "web_search_options": "on"}
    DIALECT.prepare_request(body, Policy())
    assert body["web_search_options"] == "on"


_FIELD_TEXT = st.text(max_size=12)


@given(
    names=st.lists(st.none() | _FIELD_TEXT, max_size=3),
    stop=st.none() | _FIELD_TEXT | st.lists(_FIELD_TEXT, max_size=4),
    metadata=st.none() | st.dictionaries(st.text(max_size=8), _FIELD_TEXT, max_size=4),
    cache_key=st.none() | _FIELD_TEXT,
)
def test_property_every_request_field_string_is_a_slot_and_round_trips(
    names: list[str | None],
    stop: str | list[str] | None,
    metadata: dict[str, str] | None,
    cache_key: str | None,
) -> None:
    messages = [{"role": "user", "name": name, "content": "c"} for name in names]
    body: dict[str, Any] = {
        "messages": messages,
        "stop": stop,
        "metadata": metadata,
        "prompt_cache_key": cache_key,
    }
    original = copy.deepcopy(body)
    prepared = DIALECT.prepare_request(body, Policy())
    prepared.apply(prepared.texts())
    assert body == original
    prepared.apply([f"[SLOT_{number}]" for number in range(1, len(prepared.texts()) + 1)])
    values = [message["name"] for message in body["messages"] if message["name"] is not None]
    if isinstance(body["stop"], str):
        values.append(body["stop"])
    elif body["stop"] is not None:
        values.extend(body["stop"])
    values.extend((body["metadata"] or {}).values())
    if body["prompt_cache_key"] is not None:
        values.append(body["prompt_cache_key"])
    assert all(re.fullmatch(r"\[SLOT_\d+\]", value) for value in values)
    assert len(set(values)) == len(values)


@pytest.mark.parametrize(
    "body",
    [
        {"model": "gpt-example"},
        {"messages": "Alice Johnson"},
        {"messages": {"role": "user", "content": "x"}},
        {"messages": ["Alice Johnson"]},
        {"messages": [{"role": "user", "content": 12345}]},
        {"messages": [{"role": "user", "content": {"text": "Alice Johnson"}}]},
        {"messages": [{"role": "user", "content": ["Alice Johnson"]}]},
        {"messages": [{"role": "user", "content": [{"text": "Alice Johnson"}]}]},
        {"messages": [{"role": "user", "content": [{"type": 7, "text": "Alice Johnson"}]}]},
        {"messages": [{"role": "user", "content": [{"type": "text", "text": ["Alice Johnson"]}]}]},
        {"messages": [{"role": "user", "content": [{"type": "text"}]}]},
        {"messages": [{"role": "assistant", "content": [{"type": "refusal", "refusal": None}]}]},
        {"messages": [{"role": "assistant", "refusal": ["Alice Johnson"]}]},
        {"messages": [{"role": "assistant", "tool_calls": {"type": "function"}}]},
        {"messages": [{"role": "assistant", "tool_calls": ["Alice Johnson"]}]},
        {"messages": [{"role": "assistant", "tool_calls": [{"function": {"arguments": "{}"}}]}]},
        {"messages": [{"role": "assistant", "tool_calls": [{"type": "function"}]}]},
        {
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [{"type": "function", "function": {"arguments": {"to": "x"}}}],
                }
            ]
        },
        {"messages": [{"role": "assistant", "tool_calls": [{"type": "custom", "custom": {}}]}]},
        {"messages": [{"role": "assistant", "function_call": "Alice Johnson"}]},
        {"messages": [{"role": "assistant", "function_call": {"name": "f"}}]},
        {"messages": [], "prediction": "Alice Johnson"},
        {"messages": [], "prediction": {"type": "content", "content": 12345}},
        {"messages": [], "tools": {"type": "function", "description": "Alice Johnson"}},
        {"messages": [], "functions": "Alice Johnson"},
    ],
)
def test_bodies_without_the_required_shape_are_malformed(body: dict[str, Any]) -> None:
    with pytest.raises(MalformedRequestError) as excinfo:
        DIALECT.prepare_request(body, PASS)
    assert "Alice" not in str(excinfo.value)
    assert "12345" not in str(excinfo.value)


def test_a_body_that_is_not_an_object_is_malformed() -> None:
    with pytest.raises(MalformedRequestError):
        DIALECT.prepare_request(["Alice Johnson"], Policy())  # type: ignore[arg-type]


def test_tool_definitions_are_not_inspected_when_the_policy_turns_them_off() -> None:
    body: dict[str, Any] = {"messages": [], "tools": "not a list"}
    assert _texts(body, Policy(tool_definitions=False)) == []


def test_malformed_error_names_the_position() -> None:
    body: dict[str, Any] = {
        "messages": [{"role": "user", "content": "ok"}, {"role": "user", "content": 1}]
    }
    with pytest.raises(MalformedRequestError, match=r"messages\[1\]\.content"):
        DIALECT.prepare_request(body, Policy())


def test_apply_with_the_wrong_number_of_texts_raises_value_error() -> None:
    prepared = DIALECT.prepare_request(_load("request_conversation.json"), Policy())
    with pytest.raises(ValueError, match="expected"):
        prepared.apply(prepared.texts()[:-1])


def _engine() -> Pseudonymizer:
    return Pseudonymizer([DictionaryDetector([Term("Juan Pérez", "PERSON")]), RuleDetector()])


def test_end_to_end_the_same_name_gets_one_placeholder_everywhere() -> None:
    body = _load("request_conversation.json")
    prepared = DIALECT.prepare_request(body, Policy())
    batch = _engine().pseudonymize(prepared.texts())
    prepared.apply(batch.texts)

    messages = body["messages"]
    assert messages[0]["content"] == "You are the support assistant of [PERSON_1]."
    assert messages[1]["content"] == "Please email [PERSON_1] at [EMAIL_1] about the invoice."
    assert json.loads(messages[2]["tool_calls"][0]["function"]["arguments"]) == {
        "to": "[EMAIL_1]",
        "greeting": "Dear [PERSON_1]",
    }
    assert messages[3]["content"] == "Email sent to [PERSON_1]."
    assert "user" not in body
    outbound = json.dumps(body, ensure_ascii=False)
    assert "Juan" not in outbound
    assert "juan.perez" not in outbound
    assert [entity.placeholder for entity in batch.mapping.entities()] == [
        "[PERSON_1]",
        "[EMAIL_1]",
    ]


def test_end_to_end_pseudonymisation_is_deterministic_for_the_same_body() -> None:
    def run() -> tuple[tuple[str, ...], list[tuple[str, str]]]:
        prepared = DIALECT.prepare_request(_load("request_conversation.json"), Policy())
        batch = _engine().pseudonymize(prepared.texts())
        entities = [(entity.placeholder, entity.surface) for entity in batch.mapping.entities()]
        return batch.texts, entities

    assert run() == run()


# Response side


def test_content_refusal_and_tool_call_arguments_are_restored_in_every_choice() -> None:
    body = _load("response_two_choices.json")
    DIALECT.restore_response(body, _mapping())
    first, second = (choice["message"] for choice in body["choices"])
    assert first["content"] == "I will write to Juan Pérez now."
    assert first["refusal"] is None
    assert (
        first["tool_calls"][0]["function"]["arguments"]
        == '{"to": "juan.perez@example.com", "greeting": "Dear Juan Pérez"}'
    )
    assert first["tool_calls"][1]["custom"]["input"] == "Note for Juan Pérez"
    assert second["content"] is None
    assert second["refusal"] == "I cannot share details about Juan Pérez."
    assert second["function_call"] == {"name": "lookup", "arguments": '{"name":"Juan Pérez"}'}


def test_audio_transcript_is_restored_and_audio_data_is_not_touched() -> None:
    body = _load("response_two_choices.json")
    DIALECT.restore_response(body, _mapping())
    audio = body["choices"][1]["message"]["audio"]
    assert audio == {
        "id": "audio_0001",
        "data": "AAAA",
        "expires_at": 1763384400,
        "transcript": "Hello Juan Pérez.",
    }


def test_restored_tool_call_argument_containing_a_quote_is_valid_json() -> None:
    mapping = Mapping()
    mapping.assign("PERSON", 'Ana "La Jefa" O\'Connor \\ Jr.')
    body: dict[str, Any] = {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "f", "arguments": '{"who":"[PERSON_1]"}'},
                        }
                    ],
                    "function_call": {"name": "f", "arguments": '{"who": "[PERSON_1]"}'},
                },
            }
        ]
    }
    DIALECT.restore_response(body, mapping)
    message = body["choices"][0]["message"]
    expected = {"who": 'Ana "La Jefa" O\'Connor \\ Jr.'}
    assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == expected
    assert json.loads(message["function_call"]["arguments"]) == expected


def test_logprobs_usage_annotations_and_ids_are_unchanged() -> None:
    body = _load("response_two_choices.json")
    original = copy.deepcopy(body)
    DIALECT.restore_response(body, _mapping())
    for key in ("id", "object", "created", "model", "system_fingerprint", "service_tier", "usage"):
        assert body[key] == original[key]
    for choice, before in zip(body["choices"], original["choices"], strict=True):
        assert choice["logprobs"] == before["logprobs"]
        assert choice["index"] == before["index"]
        assert choice["finish_reason"] == before["finish_reason"]
        assert choice["message"].get("annotations") == before["message"].get("annotations")
        for call, call_before in zip(
            choice["message"].get("tool_calls", []),
            before["message"].get("tool_calls", []),
            strict=True,
        ):
            assert call["id"] == call_before["id"]
    assert json.dumps(body["usage"]) == json.dumps(original["usage"])


def test_unresolvable_placeholders_in_the_response_are_left_as_they_are() -> None:
    body: dict[str, Any] = {
        "choices": [{"index": 0, "message": {"content": "[PERSON_1] and [PERSON_7]"}}]
    }
    DIALECT.restore_response(body, _mapping())
    assert body["choices"][0]["message"]["content"] == "Juan Pérez and [PERSON_7]"


@pytest.mark.parametrize(
    "body",
    [
        {"error": {"message": "[PERSON_1]"}},
        {"choices": "[PERSON_1]"},
        {"choices": ["[PERSON_1]", {"index": 0}, {"message": None}, {"message": "[PERSON_1]"}]},
        {"choices": [{"message": {"content": ["[PERSON_1]"], "tool_calls": "x"}}]},
        {
            "choices": [
                {"message": {"tool_calls": ["[PERSON_1]", {"function": "x"}, {"custom": 1}]}}
            ]
        },
        {"choices": [{"message": {"function_call": "[PERSON_1]", "audio": "[PERSON_1]"}}]},
    ],
)
def test_parts_of_a_response_outside_the_documented_fields_are_left_alone(
    body: dict[str, Any],
) -> None:
    original = copy.deepcopy(body)
    DIALECT.restore_response(body, _mapping())
    assert body == original


def test_end_to_end_a_response_is_restored_with_the_request_mapping() -> None:
    request = _load("request_conversation.json")
    prepared = DIALECT.prepare_request(request, Policy())
    batch = _engine().pseudonymize(prepared.texts())
    prepared.apply(batch.texts)
    response: dict[str, Any] = {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Done, [PERSON_1] was emailed at [EMAIL_1].",
                },
            }
        ]
    }
    DIALECT.restore_response(response, batch.mapping)
    assert response["choices"][0]["message"]["content"] == (
        "Done, Juan Pérez was emailed at juan.perez@example.com."
    )


# Other members


def test_error_body_has_the_openai_error_shape() -> None:
    assert DIALECT.error_body(422, "unsupported_content", "Unsupported content: image_url") == {
        "error": {
            "message": "Unsupported content: image_url",
            "type": "aliasgate_error",
            "param": None,
            "code": "unsupported_content",
        }
    }


# Keys of data objects (slice 21)


def test_argument_and_metadata_keys_and_schema_values_are_the_only_label_slots() -> None:
    prepared = DIALECT.prepare_request(_load("request_all_fields.json"), Policy())
    texts = prepared.texts()
    assert [texts[index] for index in sorted(prepared.labels())] == [
        "to",
        "cc",
        "query",
        "enum value",
        "ticket",
    ]


@pytest.mark.parametrize(
    ("body", "where"),
    [
        (
            _assistant_call('{"ACME": "a", "Acme": "b"}'),
            "messages[0].tool_calls[0].function.arguments",
        ),
        (
            {
                "messages": [
                    {
                        "role": "assistant",
                        "function_call": {"arguments": '[{"ACME": 1, "Acme": 2}]'},
                    }
                ]
            },
            "messages[0].function_call.arguments",
        ),
        ({"messages": [], "metadata": {"ACME": "a", "Acme": "b"}}, "metadata"),
    ],
    ids=["tool-call", "function-call", "metadata"],
)
def test_two_keys_that_become_the_same_raise_at_apply_naming_their_position(
    body: dict[str, Any], where: str
) -> None:
    prepared = DIALECT.prepare_request(body, Policy())
    new_texts = ["[ORG_1]" if text in {"ACME", "Acme"} else text for text in prepared.texts()]
    with pytest.raises(MalformedRequestError) as excinfo:
        prepared.apply(new_texts)
    assert str(excinfo.value) == f"{where}: two keys of one object would become the same"
    assert "ACME" not in str(excinfo.value)
    assert "Acme" not in str(excinfo.value)


def test_end_to_end_argument_and_metadata_keys_get_the_placeholder_of_the_text() -> None:
    body: dict[str, Any] = {
        "messages": [
            {"role": "user", "content": "Escribe a Juan Pérez sobre el pedido."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "f",
                            "arguments": '{"attendees": {"Juan Pérez": "accepted"}}',
                        },
                    }
                ],
            },
        ],
        "metadata": {"Juan Pérez": "vip", "maria.lopez@example.org": "x"},
    }
    engine = Pseudonymizer([DictionaryDetector([Term("Juan Pérez", "PERSON")]), RuleDetector()])
    prepared = DIALECT.prepare_request(body, Policy())
    prepared.apply(engine.pseudonymize(prepared.texts(), labels=prepared.labels()).texts)
    assert body["messages"][0]["content"] == "Escribe a [PERSON_1] sobre el pedido."
    arguments = body["messages"][1]["tool_calls"][0]["function"]["arguments"]
    assert arguments == '{"attendees":{"[PERSON_1]":"accepted"}}'
    assert body["metadata"] == {"[PERSON_1]": "vip", "[EMAIL_1]": "x"}


def test_argument_keys_of_the_answer_are_restored_on_the_raw_string() -> None:
    body = {
        "choices": [{"index": 0, "message": _assistant_call('{"[PERSON_1]": "ok"}')["messages"][0]}]
    }
    DIALECT.restore_response(body, _mapping())
    arguments = body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(arguments) == {"Juan Pérez": "ok"}
