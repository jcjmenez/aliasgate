"""OpenAI Chat Completions, `POST /v1/chat/completions`, non-streaming (architecture §6).

Request shapes checked on 2026-11-17 against the types of openai-python 3.52.0, and on
2026-11-24 against openai-python 3.58.0 for the request fields read since slice 20.

Slot order, which placeholder numbering follows:

1. each message in order: its `name` unless its role is `function`, then `content` (a string,
   or its `text` and `refusal` parts in order), then the assistant's `refusal`, then each of
   its `tool_calls`, then the legacy `function_call`; the arguments of a call give every key
   and every string value, each key right before its value;
2. `prediction.content`;
3. if the policy says so, every `description` string inside `tools`, then inside the legacy
   `functions`, then inside `response_format`;
4. `stop`: the string, or each string of the list;
5. each `metadata` key, then its value, in key order;
6. `prompt_cache_key`.

Keys of tool-call arguments and of `metadata` are label slots, and are renamed in place.

With the policy's `strip_user_location`, `web_search_options.user_location` is removed.

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
    restore_all_strings,
    slots_for_key_strings,
    slots_for_keys_and_strings,
    slots_for_string_list,
)
from aliasgate.dialects.sse import SSEEvent
from aliasgate.engine import restore
from aliasgate.mapping import Mapping
from aliasgate.stream import StreamRestorer

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
        if message.get("role") != "function":  # a function message's name names the function
            self.optional_string(message, "name", where)
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
        """JSON object or array arguments: a slot per key and string value, keys renamed and
        the arguments re-serialised afterwards. Anything else (a bare JSON scalar, invalid
        JSON): one slot on the raw string."""
        raw = function.get("arguments")
        where = f"{where}.arguments"
        if not isinstance(raw, str):
            raise MalformedRequestError(f"{where}: expected a string")
        try:
            parsed = json.loads(raw)
        except (ValueError, RecursionError):
            parsed = None
        if isinstance(parsed, dict | list):
            self.slots.extend(slots_for_keys_and_strings(parsed, self.finalizers, where))
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
        response_format = body.get("response_format")
        if response_format is not None:
            response_format = _object(response_format, "response_format")
            self.slots.extend(slots_for_key_strings(response_format, "description"))

    def request_fields(self, body: dict[str, Any]) -> None:
        """`stop`, the `metadata` keys and values and `prompt_cache_key`, in that order."""
        stop = body.get("stop")
        if isinstance(stop, str):
            self.slots.append(key_slot(body, "stop"))
        elif isinstance(stop, list):
            self.slots.extend(slots_for_string_list(stop, "stop"))
        elif stop is not None:
            raise MalformedRequestError("stop: expected a string or a list")
        metadata = body.get("metadata")
        if metadata is not None:
            metadata = _object(metadata, "metadata")
            for index, key in enumerate(metadata):
                if not isinstance(metadata[key], str):  # the key is never named
                    raise MalformedRequestError(f"metadata: value {index}: expected a string")
            self.slots.extend(slots_for_keys_and_strings(metadata, self.finalizers, "metadata"))
        cache_key = body.get("prompt_cache_key")
        if cache_key is not None:
            if not isinstance(cache_key, str):
                raise MalformedRequestError("prompt_cache_key: expected a string")
            self.slots.append(key_slot(body, "prompt_cache_key"))


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
        """Collect the slots of `body` in the documented order and strip end-user identifiers
        and location.

        Raises UnsupportedContentError for uninspectable content under policy `block`, and
        MalformedRequestError when a walked field has the wrong shape. Changes `body` only
        through the returned slots and finalizers, and by removing identifiers and location.
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
        collector.request_fields(body)
        if policy.strip_user_identifiers:
            for key in _USER_IDENTIFIERS:
                body.pop(key, None)
        if policy.strip_user_location:
            options = body.get("web_search_options")
            if isinstance(options, dict):  # kept even when empty: it turns web search on
                options.pop("user_location", None)
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
        """A transformer that restores one streamed completion with `mapping`."""
        return OpenAIChatStreamTransformer(mapping)

    def error_body(self, status: int, code: str, message: str) -> dict[str, Any]:
        """A gateway error in the OpenAI error shape."""
        return {
            "error": {"message": message, "type": "aliasgate_error", "param": None, "code": code}
        }


# A restorer per (choice index, channel). The channel names the delta field the text belongs
# to: "content", "refusal", "function_call", "audio", or ("tool" | "custom", tool call index).
type _Channel = str | tuple[str, int]
_ENVELOPE: Final = ("id", "object", "created", "model", "system_fingerprint")
_JSON_CHANNELS: Final = frozenset({"function_call"})


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _channel_delta(channel: _Channel, text: str) -> dict[str, Any]:
    """A delta that carries `text` in the field `channel` stands for."""
    if isinstance(channel, tuple):
        kind, tool = channel
        call = {"function": {"arguments": text}} if kind == "tool" else {"custom": {"input": text}}
        return {"tool_calls": [{"index": tool, **call}]}
    if channel == "function_call":
        return {"function_call": {"arguments": text}}
    if channel == "audio":
        return {"audio": {"transcript": text}}
    return {channel: text}


class OpenAIChatStreamTransformer:
    """Restores the placeholders of one streamed chat completion, chunk by chunk.

    Each text field of each choice has its own `StreamRestorer`, so placeholders split across
    chunks are restored and choices or tool calls never mix. Text still held when a choice
    finishes, at `[DONE]` or at `end()` is sent in a synthetic chunk before it; nothing held
    is emitted any other way.
    """

    def __init__(self, mapping: Mapping) -> None:
        self._mapping = mapping
        self._restorers: dict[tuple[int, _Channel], StreamRestorer] = {}
        self._envelope: dict[str, Any] = {}
        self._event_name: str | None = None
        self.unresolved = 0

    def feed(self, event: SSEEvent) -> list[SSEEvent]:
        """The events to send in place of `event`: synthetic flushes first, then the chunk."""
        self._event_name = event.event
        if event.data == "[DONE]":
            return [*self._flush(None), event]
        try:
            chunk = json.loads(event.data)
        except (ValueError, RecursionError):
            return [event]
        if not isinstance(chunk, dict):
            return [event]
        choices = chunk.get("choices")
        if not isinstance(choices, list):
            if "error" not in chunk:
                return [event]
            # A provider error sent in the stream; it may echo placeholders back.
            return [SSEEvent(event.event, _dumps(restore_all_strings(chunk, self._mapping)))]
        self._envelope = {key: chunk[key] for key in _ENVELOPE if key in chunk}
        changed = False
        flushed: list[SSEEvent] = []
        for choice in choices:
            if not isinstance(choice, dict) or not isinstance(index := choice.get("index"), int):
                continue
            delta = choice.get("delta")
            if isinstance(delta, dict):
                changed |= self._restore_delta(index, delta)
            if choice.get("finish_reason") is not None:
                flushed += self._flush(index)
        self._count()
        if not changed and not flushed:
            return [event]
        return [*flushed, SSEEvent(event.event, _dumps(chunk), event.raw_fields)]

    def end(self) -> list[SSEEvent]:
        """Synthetic chunks for any text still held; called when the stream ends without [DONE]."""
        return self._flush(None)

    def _restore_delta(self, index: int, delta: dict[str, Any]) -> bool:
        changed = self._feed(delta, "content", (index, "content"))
        changed |= self._feed(delta, "refusal", (index, "refusal"))
        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list):
            for call in tool_calls:
                if not isinstance(call, dict) or not isinstance(tool := call.get("index"), int):
                    continue
                if isinstance(function := call.get("function"), dict):
                    changed |= self._feed(function, "arguments", (index, ("tool", tool)))
                if isinstance(custom := call.get("custom"), dict):
                    changed |= self._feed(custom, "input", (index, ("custom", tool)))
        if isinstance(function_call := delta.get("function_call"), dict):
            changed |= self._feed(function_call, "arguments", (index, "function_call"))
        if isinstance(audio := delta.get("audio"), dict):
            changed |= self._feed(audio, "transcript", (index, "audio"))
        return changed

    def _feed(self, holder: dict[str, Any], key: str, where: tuple[int, _Channel]) -> bool:
        value = holder.get(key)
        if not isinstance(value, str):
            return False
        restorer = self._restorers.get(where)
        if restorer is None:
            channel = where[1]
            json_escape = channel in _JSON_CHANNELS or (
                isinstance(channel, tuple) and channel[0] == "tool"
            )
            restorer = StreamRestorer(self._mapping, json_escape=json_escape)
            self._restorers[where] = restorer
        restored = restorer.feed(value)
        holder[key] = restored
        return restored != value

    def _flush(self, index: int | None) -> list[SSEEvent]:
        """Synthetic chunks with the held text of one choice, or of every choice for None."""
        events = []
        for (choice, channel), restorer in self._restorers.items():
            if index is not None and choice != index:
                continue
            text = restorer.flush()
            if text:
                delta = _channel_delta(channel, text)
                chunk = {
                    **self._envelope,
                    "choices": [{"index": choice, "delta": delta, "finish_reason": None}],
                }
                events.append(SSEEvent(self._event_name, _dumps(chunk)))
        self._count()
        return events

    def _count(self) -> None:
        self.unresolved = sum(restorer.unresolved for restorer in self._restorers.values())
