import copy
import json
from pathlib import Path
from typing import Any

import pytest

from aliasgate.detectors.dictionary import DictionaryDetector, Term
from aliasgate.detectors.rules import RuleDetector
from aliasgate.dialects.anthropic_messages import AnthropicMessagesDialect
from aliasgate.dialects.base import (
    Dialect,
    MalformedRequestError,
    Policy,
    UnsupportedContentError,
)
from aliasgate.engine import Pseudonymizer
from aliasgate.mapping import Mapping

FIXTURES = Path(__file__).parent / "fixtures" / "anthropic"
DIALECT = AnthropicMessagesDialect()
PASS = Policy(unsupported_content="pass")
SIGNATURE = "c2lnbmF0dXJlLWV4YW1wbGU="
IMAGE = {"type": "image", "source": {"type": "url", "url": "https://example.com/a.png"}}
DOCUMENT = {
    "type": "document",
    "source": {"type": "text", "media_type": "text/plain", "data": "Alice Johnson"},
}


def _load(name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return body


def _texts(body: dict[str, Any], policy: Policy | None = None) -> list[str]:
    return DIALECT.prepare_request(body, policy or Policy()).texts()


def _user(content: Any) -> dict[str, Any]:
    return {"model": "claude-example", "messages": [{"role": "user", "content": content}]}


def _mapping() -> Mapping:
    mapping = Mapping()
    mapping.assign("PERSON", "Juan Pérez")
    mapping.assign("EMAIL", "juan.perez@example.com")
    mapping.assign("ORG", "ACME Corporation")
    return mapping


def _engine() -> Pseudonymizer:
    return Pseudonymizer(
        [
            DictionaryDetector(
                [
                    Term("Juan Pérez", "PERSON"),
                    Term("Alice Johnson", "PERSON"),
                    Term("ACME Corporation", "ORG"),
                ]
            ),
            RuleDetector(),
        ]
    )


def _pseudonymize(body: dict[str, Any]) -> Mapping:
    prepared = DIALECT.prepare_request(body, Policy())
    batch = _engine().pseudonymize(prepared.texts())
    prepared.apply(batch.texts)
    return batch.mapping


def test_the_dialect_satisfies_the_dialect_protocol() -> None:
    dialect: Dialect = DIALECT
    assert dialect.name == "anthropic_messages"


# Request side


def test_a_string_system_prompt_is_one_slot() -> None:
    body = {"system": "You help Alice Johnson.", "messages": []}
    prepared = DIALECT.prepare_request(body, Policy())
    assert prepared.texts() == ["You help Alice Johnson."]
    prepared.apply(["You help [PERSON_1]."])
    assert body["system"] == "You help [PERSON_1]."


def test_a_block_list_system_prompt_gives_a_slot_per_text_block() -> None:
    body = _load("request_all_blocks.json")
    cache_control = copy.deepcopy(body["system"][0]["cache_control"])
    prepared = DIALECT.prepare_request(body, Policy())
    assert prepared.texts()[:2] == [
        "You are the assistant of ACME Corporation.",
        "Never reveal the address of Alice Johnson.",
    ]
    prepared.apply(["[A_1]", "[B_1]", *prepared.texts()[2:]])
    assert body["system"][0] == {"type": "text", "text": "[A_1]", "cache_control": cache_control}
    assert body["system"][1] == {"type": "text", "text": "[B_1]"}


def test_null_or_absent_system_gives_no_slot() -> None:
    assert _texts({"messages": [], "system": None}) == []
    assert _texts({"messages": []}) == []


def test_string_and_block_list_message_content_are_slots_written_by_apply() -> None:
    body: dict[str, Any] = {
        "messages": [
            {"role": "user", "content": "I am Alice Johnson."},
            {"role": "assistant", "content": [{"type": "text", "text": "Hello Alice Johnson."}]},
        ]
    }
    prepared = DIALECT.prepare_request(body, Policy())
    assert prepared.texts() == ["I am Alice Johnson.", "Hello Alice Johnson."]
    prepared.apply(["I am [PERSON_1].", "Hello [PERSON_1]."])
    assert body["messages"][0]["content"] == "I am [PERSON_1]."
    assert body["messages"][1]["content"] == [{"type": "text", "text": "Hello [PERSON_1]."}]


def test_an_empty_or_absent_content_gives_no_slot() -> None:
    body: dict[str, Any] = {
        "messages": [
            {"role": "user", "content": []},
            {"role": "system", "content": [], "output_config": {"effort": "low"}},
            {"role": "assistant"},
        ]
    }
    assert _texts(body) == []


def test_a_mid_conversation_system_message_is_handled_like_any_message() -> None:
    body = {
        "messages": [
            {"role": "user", "content": "Hi"},
            {"role": "system", "content": "Address Alice Johnson formally."},
        ]
    }
    assert _texts(body) == ["Hi", "Address Alice Johnson formally."]


def test_tool_use_input_strings_are_slots_in_depth_first_order_and_replaced_in_place() -> None:
    body = _load("request_all_blocks.json")
    prepared = DIALECT.prepare_request(body, Policy())
    texts = prepared.texts()
    assert texts[4:14] == [
        "to",
        "juan.perez@example.com",
        "body",
        "greeting",
        "Dear Juan Pérez",
        "lines",
        "Hello",
        "Regards",
        "urgent",
        "copies",
    ]
    new = [*texts]
    new[4:14] = ["T", "[EMAIL_1]", "B", "G", "Dear [PERSON_1]", "L", "Hi", "Bye", "U", "C"]
    prepared.apply(new)
    tool_input = body["messages"][1]["content"][3]["input"]
    assert tool_input == {
        "T": "[EMAIL_1]",
        "B": {"G": "Dear [PERSON_1]", "L": ["Hi", "Bye"]},
        "U": True,
        "C": 2,
    }
    assert list(tool_input) == ["T", "B", "U", "C"]
    assert list(tool_input["B"]) == ["G", "L"]


def test_tool_use_keys_are_label_slots_before_their_values() -> None:
    body = _user(
        [{"type": "tool_use", "id": "t", "name": "f", "input": {"Alice Johnson": "x", "y": {}}}]
    )
    prepared = DIALECT.prepare_request(body, Policy())
    assert prepared.texts() == ["Alice Johnson", "x", "y"]
    assert prepared.labels() == {0, 2}


def test_tool_result_with_string_content_is_one_slot() -> None:
    body = _user([{"type": "tool_result", "tool_use_id": "t", "content": "Sent to Alice Johnson"}])
    prepared = DIALECT.prepare_request(body, Policy())
    assert prepared.texts() == ["Sent to Alice Johnson"]
    prepared.apply(["Sent to [PERSON_1]"])
    assert body["messages"][0]["content"][0]["content"] == "Sent to [PERSON_1]"


def test_tool_result_with_a_list_of_text_blocks_gives_a_slot_per_block() -> None:
    body = _load("request_all_blocks.json")
    assert _texts(body)[14:17] == [
        "Sent to juan.perez@example.com",
        "Delivered to Juan Pérez",
        "Receipt 42",
    ]


def test_tool_result_without_content_gives_no_slot() -> None:
    assert _texts(_user([{"type": "tool_result", "tool_use_id": "t"}])) == []
    assert _texts(_user([{"type": "tool_result", "tool_use_id": "t", "content": None}])) == []


def test_an_image_inside_a_tool_result_is_rejected_under_block_and_skipped_under_pass() -> None:
    inner = [{"type": "text", "text": "Screenshot of Alice Johnson"}, IMAGE]
    body = _user([{"type": "tool_result", "tool_use_id": "t", "content": inner}])
    with pytest.raises(UnsupportedContentError) as excinfo:
        DIALECT.prepare_request(copy.deepcopy(body), Policy())
    assert excinfo.value.kind == "image"
    prepared = DIALECT.prepare_request(body, PASS)
    assert prepared.texts() == ["Screenshot of Alice Johnson"]
    prepared.apply(["Screenshot of [PERSON_1]"])
    assert body["messages"][0]["content"][0]["content"][1] == IMAGE


@pytest.mark.parametrize("block", [IMAGE, DOCUMENT], ids=lambda block: block["type"])
def test_image_and_document_blocks_are_rejected_under_block_and_skipped_under_pass(
    block: dict[str, Any],
) -> None:
    body = _user([{"type": "text", "text": "Look at this, Alice Johnson"}, block])
    with pytest.raises(UnsupportedContentError) as excinfo:
        DIALECT.prepare_request(copy.deepcopy(body), Policy())
    assert excinfo.value.kind == block["type"]
    prepared = DIALECT.prepare_request(body, PASS)
    assert prepared.texts() == ["Look at this, Alice Johnson"]
    prepared.apply(["Look at this, [PERSON_1]"])
    assert body["messages"][0]["content"][1] == block


@pytest.mark.parametrize(
    "block",
    [
        {"type": "server_tool_use", "id": "s", "name": "web_search", "input": {"query": "x"}},
        {"type": "web_search_tool_result", "tool_use_id": "s", "content": []},
        {"type": "search_result", "source": "https://example.com", "title": "t", "content": []},
        {"type": "container_upload", "file_id": "file_0001"},
        {"type": "compaction", "content": "Summary about Alice Johnson"},
        {"type": "future_block_type", "text": "Alice Johnson"},
    ],
    ids=lambda block: block["type"],
)
def test_unknown_and_provider_tool_block_types_are_rejected_under_block(
    block: dict[str, Any],
) -> None:
    body = _user([block])
    with pytest.raises(UnsupportedContentError) as excinfo:
        DIALECT.prepare_request(copy.deepcopy(body), Policy())
    assert excinfo.value.kind == block["type"]
    assert DIALECT.prepare_request(body, PASS).texts() == []
    assert body["messages"][0]["content"][0] == block


def test_a_block_type_that_is_not_a_label_is_reported_as_unknown() -> None:
    with pytest.raises(UnsupportedContentError) as excinfo:
        DIALECT.prepare_request(_user([{"type": "Alice Johnson"}]), Policy())
    assert excinfo.value.kind == "unknown"
    assert "Alice" not in str(excinfo.value)


def test_a_non_text_system_block_is_rejected_under_block() -> None:
    body = {"system": [{"type": "image", "source": {}}], "messages": []}
    with pytest.raises(UnsupportedContentError) as excinfo:
        DIALECT.prepare_request(body, Policy())
    assert excinfo.value.kind == "image"
    assert _texts(body, PASS) == []


def test_thinking_blocks_are_byte_for_byte_unchanged_after_a_full_cycle() -> None:
    body = _load("request_all_blocks.json")
    thinking_before = json.dumps(body["messages"][1]["content"][:2], ensure_ascii=False)
    mapping = _pseudonymize(body)
    assert json.dumps(body["messages"][1]["content"][:2], ensure_ascii=False) == thinking_before
    assert body["messages"][1]["content"][0]["signature"] == SIGNATURE
    # The placeholders in the thinking text would have become [LITERAL_n] had it been a slot.
    assert all(entity.entity_type != "LITERAL" for entity in mapping.entities())


def test_thinking_blocks_are_not_slots_even_when_they_hold_detectable_text() -> None:
    thinking = {"type": "thinking", "thinking": "Alice Johnson", "signature": SIGNATURE}
    body: dict[str, Any] = {"messages": [{"role": "assistant", "content": [thinking]}]}
    assert _texts(body) == []


def test_tool_descriptions_are_slots_and_names_and_schema_property_names_are_not() -> None:
    body = _load("request_all_blocks.json")
    tools_before = copy.deepcopy(body["tools"])
    prepared = DIALECT.prepare_request(body, Policy())
    texts = prepared.texts()
    assert texts[-2:] == [
        "Send an email for ACME Corporation.",
        "Recipient, e.g. juan.perez@example.com",
    ]
    prepared.apply([*texts[:-2], "[D_1]", "[D_2]"])
    tool = body["tools"][0]
    assert tool["name"] == tools_before[0]["name"]
    assert tool["description"] == "[D_1]"
    properties = tool["input_schema"]["properties"]
    assert list(properties) == ["to", "description"]
    assert properties["to"]["description"] == "[D_2]"
    assert properties["description"] == {"type": "string", "enum": ["Alice Johnson"]}
    assert tool["input_schema"]["required"] == ["to"]


def test_with_tool_definitions_off_no_tool_slot_exists() -> None:
    body = _load("request_all_blocks.json")
    texts = _texts(body, Policy(tool_definitions=False))
    assert texts[-1] == "Thanks."
    assert len(texts) == 18


def test_tool_definitions_are_not_inspected_when_the_policy_turns_them_off() -> None:
    body: dict[str, Any] = {"messages": [], "tools": "not a list"}
    assert _texts(body, Policy(tool_definitions=False)) == []


def test_slot_order_is_system_then_messages_then_tools() -> None:
    assert _texts(_load("request_all_blocks.json")) == [
        "You are the assistant of ACME Corporation.",
        "Never reveal the address of Alice Johnson.",
        "Please email Juan Pérez at juan.perez@example.com.",
        "Sending the email now.",
        "to",
        "juan.perez@example.com",
        "body",
        "greeting",
        "Dear Juan Pérez",
        "lines",
        "Hello",
        "Regards",
        "urgent",
        "copies",
        "Sent to juan.perez@example.com",
        "Delivered to Juan Pérez",
        "Receipt 42",
        "Thanks.",
        "Send an email for ACME Corporation.",
        "Recipient, e.g. juan.perez@example.com",
    ]


def test_fields_outside_the_slots_are_never_touched() -> None:
    body = _load("request_all_blocks.json")
    original = copy.deepcopy(body)
    prepared = DIALECT.prepare_request(body, Policy())
    prepared.apply([f"[X_{number}]" for number in range(1, len(prepared.texts()) + 1)])
    for key in ("model", "max_tokens", "stream"):
        assert body[key] == original[key]
    messages, before = body["messages"], original["messages"]
    assert [message["role"] for message in messages] == [message["role"] for message in before]
    tool_use, tool_use_before = messages[1]["content"][3], before[1]["content"][3]
    assert {k: v for k, v in tool_use.items() if k != "input"} == {
        k: v for k, v in tool_use_before.items() if k != "input"
    }
    results, results_before = messages[2]["content"][:2], before[2]["content"][:2]
    for result, result_before in zip(results, results_before, strict=True):
        assert result["tool_use_id"] == result_before["tool_use_id"]
        assert result.get("is_error") == result_before.get("is_error")


def test_applying_the_texts_unchanged_changes_nothing_but_the_stripped_identifiers() -> None:
    body = _load("request_all_blocks.json")
    original = copy.deepcopy(body)
    prepared = DIALECT.prepare_request(body, Policy())
    prepared.apply(prepared.texts())
    del original["metadata"]
    assert body == original


def test_metadata_user_id_is_removed_and_an_empty_metadata_with_it() -> None:
    body: dict[str, Any] = {"messages": [], "metadata": {"user_id": "user-0001"}}
    DIALECT.prepare_request(body, Policy())
    assert "metadata" not in body


def test_other_metadata_keys_are_kept_when_user_id_is_removed() -> None:
    body: dict[str, Any] = {"messages": [], "metadata": {"user_id": "u", "trace": "t-1"}}
    DIALECT.prepare_request(body, Policy())
    assert body["metadata"] == {"trace": "t-1"}


def test_an_empty_metadata_without_user_id_is_removed_too() -> None:
    body: dict[str, Any] = {"messages": [], "metadata": {}}
    DIALECT.prepare_request(body, Policy())
    assert "metadata" not in body


def test_metadata_is_kept_when_stripping_is_off() -> None:
    body: dict[str, Any] = {"messages": [], "metadata": {"user_id": "user-0001"}}
    DIALECT.prepare_request(body, Policy(strip_user_identifiers=False))
    assert body["metadata"] == {"user_id": "user-0001"}


def test_a_metadata_value_that_is_not_an_object_is_left_to_the_provider() -> None:
    body: dict[str, Any] = {"messages": [], "metadata": "user-0001"}
    DIALECT.prepare_request(body, Policy())
    assert body["metadata"] == "user-0001"


@pytest.mark.parametrize(
    "body",
    [
        {"model": "claude-example"},
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
        {"messages": [{"role": "assistant", "content": [{"type": "tool_use", "input": "x"}]}]},
        {"messages": [{"role": "assistant", "content": [{"type": "tool_use", "id": "t"}]}]},
        {"messages": [{"role": "assistant", "content": [{"type": "tool_use", "input": ["x"]}]}]},
        {"messages": [{"role": "user", "content": [{"type": "tool_result", "content": 12345}]}]},
        {
            "messages": [
                {"role": "user", "content": [{"type": "tool_result", "content": ["Alice Johnson"]}]}
            ]
        },
        {"messages": [], "system": 12345},
        {"messages": [], "system": {"type": "text", "text": "Alice Johnson"}},
        {"messages": [], "system": ["Alice Johnson"]},
        {"messages": [], "system": [{"type": "text", "text": 12345}]},
        {"messages": [], "system": [{"text": "Alice Johnson"}]},
        {"messages": [], "tools": {"name": "f", "description": "Alice Johnson"}},
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


def test_malformed_error_names_the_position() -> None:
    body: dict[str, Any] = {
        "messages": [
            {"role": "user", "content": "ok"},
            {"role": "user", "content": [{"type": "tool_result", "content": [{"type": "text"}]}]},
        ]
    }
    with pytest.raises(MalformedRequestError, match=r"messages\[1\]\.content\[0\]\.content\[0\]"):
        DIALECT.prepare_request(body, Policy())


def test_apply_with_the_wrong_number_of_texts_raises_value_error() -> None:
    prepared = DIALECT.prepare_request(_load("request_all_blocks.json"), Policy())
    with pytest.raises(ValueError, match="expected"):
        prepared.apply(prepared.texts()[:-1])


def test_end_to_end_the_same_name_gets_one_placeholder_everywhere() -> None:
    body = _load("request_all_blocks.json")
    mapping = _pseudonymize(body)
    assert body["system"][0]["text"] == "You are the assistant of [ORG_1]."
    assert body["messages"][0]["content"] == "Please email [PERSON_2] at [EMAIL_1]."
    assert body["messages"][1]["content"][3]["input"]["to"] == "[EMAIL_1]"
    assert body["messages"][2]["content"][1]["content"][0]["text"] == "Delivered to [PERSON_2]"
    outbound = json.dumps(body, ensure_ascii=False)
    for value in ("Juan", "juan.perez", "ACME", "user-0001"):
        assert value not in outbound
    # The enum value is a schema value, never a slot: the dictionary term stays.
    assert body["tools"][0]["input_schema"]["properties"]["description"]["enum"] == [
        "Alice Johnson"
    ]
    assert [entity.placeholder for entity in mapping.entities()] == [
        "[ORG_1]",
        "[PERSON_1]",
        "[PERSON_2]",
        "[EMAIL_1]",
    ]


# Request fields read since slice 20


def _fields_body() -> dict[str, Any]:
    schema = {"type": "object", "properties": {"x": {"type": "string", "description": "schema"}}}
    return {
        "system": "system text",
        "messages": [{"role": "user", "content": "message text"}],
        "tools": [
            {
                "name": "send",
                "description": "tool description",
                "input_schema": {"type": "object"},
                "input_examples": [{"to": "maria.lopez@example.org", "n": 2, "cc": ["a", "b"]}],
            }
        ],
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
        "stop_sequences": ["x", "y"],
    }


def test_examples_schema_descriptions_and_stop_sequences_follow_the_tool_descriptions() -> None:
    body = _fields_body()
    original = copy.deepcopy(body)
    prepared = DIALECT.prepare_request(body, Policy())
    assert prepared.texts() == [
        "system text",
        "message text",
        "tool description",
        "to",
        "maria.lopez@example.org",
        "n",
        "cc",
        "a",
        "b",
        "schema",
        "x",
        "y",
    ]
    prepared.apply([f"[S_{number}]" for number in range(1, 13)])
    example = body["tools"][0]["input_examples"][0]
    assert example == {"[S_4]": "[S_5]", "[S_6]": 2, "[S_7]": ["[S_8]", "[S_9]"]}
    assert list(example) == ["[S_4]", "[S_6]", "[S_7]"]
    assert body["stop_sequences"] == ["[S_11]", "[S_12]"]
    assert body["tools"][0]["input_schema"] == original["tools"][0]["input_schema"]
    assert body["output_config"]["format"]["type"] == "json_schema"


def test_a_description_inside_input_examples_is_one_slot_among_the_tool_descriptions() -> None:
    body: dict[str, Any] = {
        "messages": [],
        "tools": [
            {
                "name": "f",
                "description": "first",
                "input_examples": [{"description": "example description", "to": "to"}],
            },
            {"name": "g", "description": "second"},
        ],
    }
    assert _texts(body) == ["first", "example description", "second", "description", "to", "to"]


def test_with_tool_definitions_off_examples_and_schemas_are_not_read_but_stop_sequences_are() -> (
    None
):
    body = _fields_body()
    body["output_config"] = "not an object"
    texts = _texts(body, Policy(tool_definitions=False))
    assert texts == ["system text", "message text", "x", "y"]


def test_null_stop_sequences_and_output_config_add_no_slot() -> None:
    body: dict[str, Any] = {"messages": [], "stop_sequences": None, "output_config": None}
    assert _texts(body) == []


def test_an_output_config_without_a_format_adds_no_slot() -> None:
    body: dict[str, Any] = {"messages": [], "output_config": {"effort": "low"}}
    assert _texts(body) == []
    assert body["output_config"] == {"effort": "low"}


def test_tools_that_are_not_objects_are_skipped_by_the_new_walks() -> None:
    body: dict[str, Any] = {"messages": [], "tools": ["f", {"name": "g", "input_examples": []}]}
    assert _texts(body) == []
    assert body["tools"][0] == "f"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            {"messages": [], "tools": [{"name": "f", "input_examples": {"to": "maria"}}]},
            "tools[0].input_examples: expected a list",
        ),
        ({"messages": [], "output_config": "maria"}, "output_config: expected an object"),
        (
            {"messages": [], "output_config": {"format": "maria"}},
            "output_config.format: expected an object",
        ),
        ({"messages": [], "stop_sequences": "maria"}, "stop_sequences: expected a list"),
        ({"messages": [], "stop_sequences": ["x", 1]}, "stop_sequences[1]: expected a string"),
    ],
)
def test_request_fields_of_the_wrong_shape_are_malformed(
    body: dict[str, Any], message: str
) -> None:
    with pytest.raises(MalformedRequestError) as excinfo:
        DIALECT.prepare_request(body, Policy())
    assert str(excinfo.value) == message


def _web_search_tools() -> list[dict[str, Any]]:
    location = {"type": "approximate", "city": "Springfield", "country": "US"}
    return [
        {"type": "web_search_20250305", "name": "web_search", "user_location": location},
        {
            "type": "web_search_20260318",
            "name": "web_search",
            "max_uses": 2,
            "user_location": location,
        },
    ]


def test_web_search_tools_lose_their_user_location_by_default() -> None:
    body: dict[str, Any] = {"messages": [], "tools": _web_search_tools()}
    DIALECT.prepare_request(body, Policy())
    assert body["tools"] == [
        {"type": "web_search_20250305", "name": "web_search"},
        {"type": "web_search_20260318", "name": "web_search", "max_uses": 2},
    ]


def test_user_location_is_kept_when_its_stripping_is_off() -> None:
    body: dict[str, Any] = {"messages": [], "tools": _web_search_tools()}
    DIALECT.prepare_request(body, Policy(strip_user_location=False))
    assert body["tools"] == _web_search_tools()


def test_user_location_is_removed_with_tool_definitions_off_too() -> None:
    body: dict[str, Any] = {"messages": [], "tools": _web_search_tools()}
    DIALECT.prepare_request(body, Policy(tool_definitions=False))
    assert all("user_location" not in tool for tool in body["tools"])


# Response side


def test_the_stop_sequence_of_the_answer_is_restored() -> None:
    body: dict[str, Any] = {"content": [], "stop_sequence": "[PERSON_1]:"}
    DIALECT.restore_response(body, _mapping())
    assert body["stop_sequence"] == "Juan P\u00e9rez:"


def test_a_stop_sequence_that_is_null_or_not_a_string_is_left_as_it_is() -> None:
    for value in (None, 3):
        body: dict[str, Any] = {"content": [], "stop_sequence": value}
        DIALECT.restore_response(body, _mapping())
        assert body["stop_sequence"] == value


def test_text_blocks_are_restored_and_citations_left_unchanged() -> None:
    body = _load("response_mixed.json")
    citations = copy.deepcopy(body["content"][2]["citations"])
    DIALECT.restore_response(body, _mapping())
    text = body["content"][2]
    assert text["text"] == "I will write to Juan Pérez at juan.perez@example.com."
    assert text["citations"] == citations


def test_tool_use_input_is_restored_at_every_depth() -> None:
    body = _load("response_mixed.json")
    DIALECT.restore_response(body, _mapping())
    assert body["content"][3]["input"] == {
        "to": "juan.perez@example.com",
        "body": {"greeting": "Dear Juan Pérez", "lines": ["Juan Pérez", 7, None, True]},
    }
    assert body["content"][3]["id"] == "toolu_example01"
    assert body["content"][3]["name"] == "send_email"


def test_thinking_server_tool_blocks_usage_and_stop_reason_are_unchanged() -> None:
    body = _load("response_mixed.json")
    original = copy.deepcopy(body)
    DIALECT.restore_response(body, _mapping())
    for index in (0, 1, 4):
        assert json.dumps(body["content"][index]) == json.dumps(original["content"][index])
    for key in ("id", "type", "role", "model", "stop_reason", "stop_sequence", "usage"):
        assert body[key] == original[key]


def test_unresolvable_placeholders_in_the_response_are_left_as_they_are() -> None:
    body: dict[str, Any] = {"content": [{"type": "text", "text": "[PERSON_1] and [PERSON_7]"}]}
    DIALECT.restore_response(body, _mapping())
    assert body["content"][0]["text"] == "Juan Pérez and [PERSON_7]"


@pytest.mark.parametrize(
    "body",
    [
        {"type": "error", "error": {"message": "[PERSON_1]"}},
        {"input_tokens": 12},
        {"content": "[PERSON_1]"},
        {"content": ["[PERSON_1]", 7, None]},
        {"content": [{"text": "[PERSON_1]"}, {"type": "text", "text": ["[PERSON_1]"]}]},
        {"content": [{"type": "tool_use", "name": "[PERSON_1]"}]},
    ],
)
def test_parts_of_a_response_outside_the_documented_fields_are_left_alone(
    body: dict[str, Any],
) -> None:
    original = copy.deepcopy(body)
    DIALECT.restore_response(body, _mapping())
    assert body == original


def test_conversation_round_trip_keeps_the_placeholders_of_the_first_turn() -> None:
    turn_1: dict[str, Any] = {
        "model": "claude-example",
        "max_tokens": 100,
        "system": "You assist ACME Corporation.",
        "messages": [{"role": "user", "content": "Email Juan Pérez at juan.perez@example.com."}],
    }
    first = copy.deepcopy(turn_1)
    mapping_1 = _pseudonymize(first)
    thinking = {"type": "thinking", "thinking": "Mail [PERSON_1].", "signature": SIGNATURE}
    response = {
        "content": [
            thinking,
            {"type": "text", "text": "Done: [PERSON_1] got a mail at [EMAIL_1]."},
        ]
    }
    DIALECT.restore_response(response, mapping_1)
    assert (
        response["content"][1]["text"] == "Done: Juan Pérez got a mail at juan.perez@example.com."
    )

    turn_2 = copy.deepcopy(turn_1)
    turn_2["messages"] += [
        {"role": "assistant", "content": response["content"]},
        {"role": "user", "content": "Now copy Alice Johnson."},
    ]
    second = copy.deepcopy(turn_2)
    mapping_2 = _pseudonymize(second)

    assert second["system"] == first["system"]
    assert second["messages"][0] == first["messages"][0]
    assert second["messages"][1]["content"] == [
        thinking,
        {"type": "text", "text": "Done: [PERSON_1] got a mail at [EMAIL_1]."},
    ]
    assert second["messages"][2]["content"] == "Now copy [PERSON_2]."
    for entity in mapping_1.entities():
        assert mapping_2.get(entity.key) == entity


# Other members


@pytest.mark.parametrize(
    ("status", "family"),
    [
        (400, "invalid_request_error"),
        (422, "invalid_request_error"),
        (404, "not_found_error"),
        (413, "request_too_large"),
        (500, "api_error"),
        (502, "api_error"),
        (504, "api_error"),
    ],
)
def test_error_body_has_the_anthropic_error_shape(status: int, family: str) -> None:
    assert DIALECT.error_body(status, "some_code", "Something failed.") == {
        "type": "error",
        "error": {"type": family, "message": "Something failed. (some_code)"},
    }


def test_error_body_example_from_the_specification() -> None:
    body = DIALECT.error_body(422, "unsupported_content", "Unsupported content: image")
    assert body["error"]["message"] == "Unsupported content: image (unsupported_content)"


# Keys of data objects (slice 21)


def test_tool_use_input_and_input_example_keys_are_the_only_label_slots() -> None:
    body = _fields_body()
    body["messages"].append(
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t", "name": "f", "input": {"who": "x"}}],
        }
    )
    prepared = DIALECT.prepare_request(body, Policy())
    texts = prepared.texts()
    assert [texts[index] for index in sorted(prepared.labels())] == ["who", "to", "n", "cc"]
    assert texts.index("who") == 2
    assert prepared.labels() == {2, 5, 7, 8}


def test_a_full_request_marks_exactly_the_tool_use_keys_as_labels() -> None:
    prepared = DIALECT.prepare_request(_load("request_all_blocks.json"), Policy())
    texts = prepared.texts()
    assert [texts[index] for index in sorted(prepared.labels())] == [
        "to",
        "body",
        "greeting",
        "lines",
        "urgent",
        "copies",
    ]


def test_two_tool_use_input_keys_that_become_the_same_raise_at_apply() -> None:
    body = _user([{"type": "tool_use", "id": "t", "name": "f", "input": {"ACME": 1, "Acme": 2}}])
    prepared = DIALECT.prepare_request(body, Policy())
    with pytest.raises(MalformedRequestError) as excinfo:
        prepared.apply(["[ORG_1]", "[ORG_1]"])
    assert str(excinfo.value) == (
        "messages[0].content[0].input: two keys of one object would become the same"
    )
    assert list(body["messages"][0]["content"][0]["input"]) == ["ACME", "Acme"]


def test_two_input_example_keys_that_become_the_same_raise_at_apply() -> None:
    body: dict[str, Any] = {
        "messages": [],
        "tools": [{"name": "f"}, {"name": "g", "input_examples": [{"ACME": 1, "Acme": 2}]}],
    }
    prepared = DIALECT.prepare_request(body, Policy())
    with pytest.raises(MalformedRequestError) as excinfo:
        prepared.apply(["[ORG_1]", "[ORG_1]"])
    assert (
        str(excinfo.value)
        == "tools[1].input_examples: two keys of one object would become the same"
    )


def test_end_to_end_a_tool_use_key_and_an_example_key_get_the_placeholder_of_the_text() -> None:
    body = _user("Escribe a Juan Pérez sobre el pedido.")
    body["messages"].append(
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "t",
                    "name": "f",
                    "input": {"attendees": {"Juan Pérez": "accepted"}},
                }
            ],
        }
    )
    body["tools"] = [{"name": "f", "input_examples": [{"Juan Pérez": "accepted"}]}]
    _pseudonymize(body)
    assert body["messages"][0]["content"] == "Escribe a [PERSON_1] sobre el pedido."
    assert body["messages"][1]["content"][0]["input"] == {"attendees": {"[PERSON_1]": "accepted"}}
    assert body["tools"][0]["input_examples"] == [{"[PERSON_1]": "accepted"}]


def test_a_tool_use_input_key_of_the_answer_is_restored() -> None:
    body: dict[str, Any] = {
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_example01",
                "name": "f",
                "input": {"attendees": {"[PERSON_1]": "accepted", "[EMAIL_1]": "[PERSON_1]"}},
            }
        ]
    }
    DIALECT.restore_response(body, _mapping())
    assert body["content"][0]["input"] == {
        "attendees": {"Juan Pérez": "accepted", "juan.perez@example.com": "Juan Pérez"}
    }
