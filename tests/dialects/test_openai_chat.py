import copy
import json
from pathlib import Path
from typing import Any

import pytest

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
    assert prepared.texts() == ["juan.perez@example.com", "a@example.org"]
    prepared.apply(["[EMAIL_1]", "[EMAIL_2]"])
    arguments = _arguments(body)
    assert arguments == '{"to":"[EMAIL_1]","cc":["[EMAIL_2]"]}'
    assert json.loads(arguments) == {"to": "[EMAIL_1]", "cc": ["[EMAIL_2]"]}


def test_escaped_strings_in_arguments_are_decoded_before_detection() -> None:
    body = _assistant_call('{"name": "Jos\\u00e9 \\"Pepe\\""}')
    assert _texts(body) == ['José "Pepe"']


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
    assert prepared.texts() == ["Alice Johnson"]
    prepared.apply(["[PERSON_1]"])
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


def test_tool_descriptions_are_slots_and_names_enums_and_property_names_are_not() -> None:
    body = _load("request_all_fields.json")
    body["messages"] = []
    del body["prediction"]
    assert _texts(body) == [
        "tool description",
        "to description",
        "nested description",
        "custom tool description",
        "function description",
        "query description",
    ]


def test_with_tool_definitions_off_no_tool_slot_exists() -> None:
    body = _load("request_all_fields.json")
    texts = _texts(body, Policy(tool_definitions=False))
    assert not any("description" in text for text in texts)
    assert texts[-1] == "prediction text"


def test_slot_order_is_messages_then_prediction_then_tools() -> None:
    assert _texts(_load("request_all_fields.json")) == [
        "system text",
        "developer part 1",
        "developer part 2",
        "user part",
        "assistant part",
        "refusal part",
        "assistant refusal",
        "to argument",
        "cc argument",
        "custom input",
        "tool result",
        "tool result part",
        "function call argument",
        "function result",
        "prediction text",
        "tool description",
        "to description",
        "nested description",
        "custom tool description",
        "function description",
        "query description",
    ]


def test_fields_outside_the_slots_are_never_touched() -> None:
    body = _load("request_all_fields.json")
    original = copy.deepcopy(body)
    prepared = DIALECT.prepare_request(body, Policy())
    prepared.apply([f"[SLOT_{number}]" for number in range(1, len(prepared.texts()) + 1)])
    for key in ("model", "metadata", "temperature", "stop", "response_format"):
        assert body[key] == original[key]
    for message, before in zip(body["messages"], original["messages"], strict=True):
        assert message["role"] == before["role"]
        assert message.get("name") == before.get("name")
        assert message.get("tool_call_id") == before.get("tool_call_id")
        assert message.get("audio") == before.get("audio")
    calls = body["messages"][3]["tool_calls"]
    assert [call["id"] for call in calls] == ["call_0001", "call_0002"]
    assert calls[0]["function"]["name"] == "send_email"
    assert calls[1]["custom"]["name"] == "run_query"
    function = body["tools"][0]["function"]
    assert function["name"] == "send_email"
    assert function["strict"] is True
    properties = function["parameters"]["properties"]
    assert list(properties) == ["to", "description"]
    assert properties["to"]["enum"] == ["enum value"]
    assert function["parameters"]["required"] == ["to"]
    assert body["tools"][1]["custom"]["format"] == original["tools"][1]["custom"]["format"]
    assert body["functions"][0]["name"] == "lookup"


def test_applying_the_texts_unchanged_changes_nothing_but_the_stripped_identifiers() -> None:
    body = _load("request_all_fields.json")
    original = copy.deepcopy(body)
    prepared = DIALECT.prepare_request(body, Policy())
    prepared.apply(prepared.texts())
    del original["user"], original["safety_identifier"]
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


def test_stream_transformer_is_not_implemented_yet() -> None:
    with pytest.raises(NotImplementedError):
        DIALECT.stream_transformer(_mapping())
