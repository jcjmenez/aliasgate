"""OpenAI Chat Completions, `POST /v1/chat/completions`, non-streaming (architecture §6).

Request shapes checked on 2026-11-17 against the types of openai-python 3.52.0.

Slot order, which placeholder numbering follows:

1. each message in order: `content` (a string, or its `text` and `refusal` parts in order),
   then the assistant's `refusal`, then each of its `tool_calls`, then the legacy
   `function_call`;
2. `prediction.content`;
3. if the policy says so, every `description` string inside `tools`, then inside the legacy
   `functions`.

Where the dialect expects a shape and finds another, it raises MalformedRequestError naming the
position (for example `messages[2].content`), never the content. It validates nothing else;
the provider does that.
"""

import json
from collections.abc import Callable
from functools import partial
from typing import Any, Final

from aliasgate.dialects.base import (
    MalformedRequestError,
    Policy,
    PreparedRequest,
    Slot,
    StreamTransformer,
    UnsupportedContentError,
    key_slot,
    slots_for_json_strings,
    slots_for_key_strings,
)
from aliasgate.engine import restore
from aliasgate.mapping import Mapping

_USER_IDENTIFIERS: Final = ("user", "safety_identifier")
_TEXT_PARTS: Final = frozenset({"text", "refusal"})  # the part type is also the text's key
_TOOL_DEFINITIONS: Final = ("tools", "functions")


def _object(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MalformedRequestError(f"{where}: expected an object")
    return value


def _write_json(obj: dict[str, Any], key: str, value: dict[str, Any] | list[Any]) -> None:
    obj[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class _Collector:
    """Accumulates the slots and finalizers of one request while walking it."""

    __slots__ = ("finalizers", "policy", "slots")

    def __init__(self, policy: Policy) -> None:
        self.policy = policy
        self.slots: list[Slot] = []
        self.finalizers: list[Callable[[], None]] = []

    def message(self, value: object, where: str) -> None:
        message = _object(value, where)
        self.content(message, where)
        self.optional_string(message, "refusal", where)
        self.tool_calls(message.get("tool_calls"), f"{where}.tool_calls")
        function_call = message.get("function_call")
        if function_call is not None:
            where = f"{where}.function_call"
            self.arguments(_object(function_call, where), where)

    def content(self, holder: dict[str, Any], where: str) -> None:
        value = holder.get("content")
        where = f"{where}.content"
        if value is None:
            return
        if isinstance(value, str):
            self.slots.append(key_slot(holder, "content"))
            return
        if not isinstance(value, list):
            raise MalformedRequestError(f"{where}: expected a string, a list or null")
        for index, part in enumerate(value):
            self.part(part, f"{where}[{index}]")

    def part(self, value: object, where: str) -> None:
        part = _object(value, where)
        kind = part.get("type")
        if not isinstance(kind, str):
            raise MalformedRequestError(f"{where}.type: expected a string")
        if kind in _TEXT_PARTS:
            self.required_string(part, kind, where)
        else:
            self.unsupported(kind)

    def tool_calls(self, value: object, where: str) -> None:
        if value is None:
            return
        if not isinstance(value, list):
            raise MalformedRequestError(f"{where}: expected a list or null")
        for index, item in enumerate(value):
            at = f"{where}[{index}]"
            call = _object(item, at)
            kind = call.get("type")
            if kind == "function":
                self.arguments(_object(call.get("function"), f"{at}.function"), f"{at}.function")
            elif kind == "custom":
                custom = _object(call.get("custom"), f"{at}.custom")
                self.required_string(custom, "input", f"{at}.custom")
            elif isinstance(kind, str):
                self.unsupported(kind)
            else:
                raise MalformedRequestError(f"{at}.type: expected a string")

    def arguments(self, function: dict[str, Any], where: str) -> None:
        """JSON object or array arguments: a slot per string value, re-serialised afterwards.
        Anything else (a bare JSON scalar, invalid JSON): one slot on the raw string."""
        raw = function.get("arguments")
        if not isinstance(raw, str):
            raise MalformedRequestError(f"{where}.arguments: expected a string")
        try:
            parsed = json.loads(raw)
        except (ValueError, RecursionError):
            parsed = None
        if isinstance(parsed, dict | list):
            self.slots.extend(slots_for_json_strings(parsed))
            self.finalizers.append(partial(_write_json, function, "arguments", parsed))
        else:
            self.slots.append(key_slot(function, "arguments"))

    def required_string(self, holder: dict[str, Any], key: str, where: str) -> None:
        if not isinstance(holder.get(key), str):
            raise MalformedRequestError(f"{where}.{key}: expected a string")
        self.slots.append(key_slot(holder, key))

    def optional_string(self, holder: dict[str, Any], key: str, where: str) -> None:
        if holder.get(key) is not None:
            self.required_string(holder, key, where)

    def unsupported(self, kind: str) -> None:
        if self.policy.unsupported_content == "block":
            raise UnsupportedContentError(kind)

    def tool_definitions(self, body: dict[str, Any]) -> None:
        for key in _TOOL_DEFINITIONS:
            definitions = body.get(key)
            if definitions is None:
                continue
            if not isinstance(definitions, list):
                raise MalformedRequestError(f"{key}: expected a list")
            self.slots.extend(slots_for_key_strings(definitions, "description"))


def _restore_key(obj: object, key: str, mapping: Mapping, *, json_escape: bool = False) -> None:
    if isinstance(obj, dict) and isinstance(value := obj.get(key), str):
        obj[key] = restore(value, mapping, json_escape=json_escape)


def _restore_message(message: dict[str, Any], mapping: Mapping) -> None:
    _restore_key(message, "content", mapping)
    _restore_key(message, "refusal", mapping)
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            if isinstance(call, dict):
                _restore_key(call.get("function"), "arguments", mapping, json_escape=True)
                _restore_key(call.get("custom"), "input", mapping)
    _restore_key(message.get("function_call"), "arguments", mapping, json_escape=True)
    _restore_key(message.get("audio"), "transcript", mapping)


class OpenAIChatDialect:
    """OpenAI Chat Completions payloads: slots in a fixed order, restoration of responses."""

    name = "openai_chat"

    def prepare_request(self, body: dict[str, Any], policy: Policy) -> PreparedRequest:
        """Collect the slots of `body` in the documented order and strip end-user identifiers.

        Raises UnsupportedContentError for uninspectable content under policy `block`, and
        MalformedRequestError when a walked field has the wrong shape. Changes `body` only
        through the returned slots and finalizers, and by removing identifiers.
        """
        if not isinstance(body, dict):
            raise MalformedRequestError("body: expected an object")
        messages = body.get("messages")
        if not isinstance(messages, list):
            raise MalformedRequestError("messages: expected a list")
        collector = _Collector(policy)
        for index, message in enumerate(messages):
            collector.message(message, f"messages[{index}]")
        prediction = body.get("prediction")
        if prediction is not None:
            collector.content(_object(prediction, "prediction"), "prediction")
        if policy.tool_definitions:
            collector.tool_definitions(body)
        if policy.strip_user_identifiers:
            for key in _USER_IDENTIFIERS:
                body.pop(key, None)
        return PreparedRequest(collector.slots, collector.finalizers)

    def restore_response(self, body: dict[str, Any], mapping: Mapping) -> None:
        """Restore, in place, the model text of every choice: content, refusal, tool-call
        arguments and inputs, legacy function-call arguments and the audio transcript.

        Everything else (annotations, logprobs, usage, ids) is left untouched, and so is a body
        without `choices`; use `restore_all_strings` for error bodies.
        """
        choices = body.get("choices")
        if not isinstance(choices, list):
            return
        for choice in choices:
            if isinstance(choice, dict) and isinstance(message := choice.get("message"), dict):
                _restore_message(message, mapping)

    def stream_transformer(self, mapping: Mapping) -> StreamTransformer:
        """Not available until streaming is implemented (slice 07)."""
        raise NotImplementedError("streaming is implemented in slice 07")

    def error_body(self, status: int, code: str, message: str) -> dict[str, Any]:
        """A gateway error in the OpenAI error shape."""
        return {
            "error": {"message": message, "type": "aliasgate_error", "param": None, "code": code}
        }
