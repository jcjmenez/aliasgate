"""Anthropic Messages, `POST /v1/messages` and `POST /v1/messages/count_tokens` (architecture §6).

Request, response and streaming shapes checked on 2026-11-18 against the Messages API
reference, the streaming guide and the errors page on platform.claude.com, and on 2026-11-24
against anthropic-sdk-python 2.13.0 for the request fields read since slice 20.

Slot order, which placeholder numbering follows:

1. `system`: the string, or each of its `text` blocks in order;
2. each message in order (any role): `content` as a string, or its blocks in order, where a
   `text` block is one slot, a `tool_use` block gives a slot per key and string value of its
   `input`, each key right before its value, and a `tool_result` block gives its `content`
   string, or its inner blocks by the same rules;
3. if the policy says so, every `description` string inside `tools`, then every key and every
   other string value of each tool's `input_examples`, then every `description` string inside
   `output_config.format`; then the schema values (`title`, `$comment`, `enum`, `const`,
   `default`, `examples`) of each tool's `input_schema`, then of `output_config.format.schema`;
4. each string of `stop_sequences`.

Keys of `tool_use` inputs and of `input_examples` are label slots, and are renamed in place. So
are the `enum`, `const`, `default` and `examples` strings of schemas; schema keys never are.

With the policy's `strip_user_location`, every tool loses its `user_location`.

`thinking` and `redacted_thinking` blocks carry a provider signature over their exact content
and are never slots, in either direction. Every other block type is uninspectable content.

Where the dialect expects a shape and finds another, it raises MalformedRequestError naming the
position (for example `messages[2].content[0].input`), never the content.
"""

import json
from collections.abc import Callable
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
    slots_for_schema_values,
    slots_for_string_list,
)
from aliasgate.dialects.sse import SSEEvent
from aliasgate.engine import count_unresolved, restore
from aliasgate.mapping import Mapping
from aliasgate.stream import StreamRestorer

_SIGNED_BLOCKS: Final = frozenset({"thinking", "redacted_thinking"})
_ERROR_TYPES: Final = {
    400: "invalid_request_error",
    422: "invalid_request_error",
    404: "not_found_error",
    413: "request_too_large",
}
_FALLBACK_ERROR_TYPE: Final = "api_error"


def _object(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MalformedRequestError(f"{where}: expected an object")
    return value


def _kind(block: dict[str, Any], where: str) -> str:
    kind = block.get("type")
    if not isinstance(kind, str):
        raise MalformedRequestError(f"{where}.type: expected a string")
    return kind


class _Collector:
    """Accumulates the slots and finalizers of one request while walking it."""

    __slots__ = ("finalizers", "policy", "slots")

    def __init__(self, policy: Policy) -> None:
        self.policy = policy
        self.slots: list[Slot] = []
        self.finalizers: list[Callable[[], None]] = []

    def system(self, body: dict[str, Any]) -> None:
        value = body.get("system")
        if value is None:
            return
        if isinstance(value, str):
            self.slots.append(key_slot(body, "system"))
            return
        if not isinstance(value, list):
            raise MalformedRequestError("system: expected a string, a list or null")
        for index, item in enumerate(value):
            where = f"system[{index}]"
            block = _object(item, where)
            kind = _kind(block, where)
            if kind == "text":
                self.required_string(block, "text", where)
            else:
                self.unsupported(kind)

    def content(self, holder: dict[str, Any], where: str) -> None:
        """The `content` of a message or a tool result: a string, a list of blocks or null."""
        value = holder.get("content")
        where = f"{where}.content"
        if value is None:
            return
        if isinstance(value, str):
            self.slots.append(key_slot(holder, "content"))
            return
        if not isinstance(value, list):
            raise MalformedRequestError(f"{where}: expected a string, a list or null")
        for index, item in enumerate(value):
            self.block(item, f"{where}[{index}]")

    def block(self, value: object, where: str) -> None:
        block = _object(value, where)
        kind = _kind(block, where)
        if kind == "text":
            self.required_string(block, "text", where)
        elif kind == "tool_use":
            tool_input = block.get("input")
            if not isinstance(tool_input, dict):
                raise MalformedRequestError(f"{where}.input: expected an object")
            self.slots.extend(
                slots_for_keys_and_strings(tool_input, self.finalizers, f"{where}.input")
            )
        elif kind == "tool_result":
            self.content(block, where)
        elif kind not in _SIGNED_BLOCKS:
            self.unsupported(kind)

    def required_string(self, holder: dict[str, Any], key: str, where: str) -> None:
        if not isinstance(holder.get(key), str):
            raise MalformedRequestError(f"{where}.{key}: expected a string")
        self.slots.append(key_slot(holder, key))

    def unsupported(self, kind: str) -> None:
        if self.policy.unsupported_content == "block":
            raise UnsupportedContentError(kind)

    def tool_definitions(self, body: dict[str, Any]) -> None:
        definitions = body.get("tools")
        if definitions is not None:
            if not isinstance(definitions, list):
                raise MalformedRequestError("tools: expected a list")
            self.slots.extend(slots_for_key_strings(definitions, "description"))
            self.input_examples(definitions)
        config = body.get("output_config")
        if config is not None:
            config = _object(config, "output_config")
            output_format = config.get("format")
            if output_format is not None:
                output_format = _object(output_format, "output_config.format")
                self.slots.extend(slots_for_key_strings(output_format, "description"))
        self.schema_values(body)

    def schema_values(self, body: dict[str, Any]) -> None:
        """The schema values of each tool's `input_schema`, then of the output format's
        schema. Server tools have none, and a schema of another shape gives none."""
        tools = body.get("tools")
        for tool in tools if isinstance(tools, list) else ():
            if isinstance(tool, dict):
                self.slots.extend(slots_for_schema_values(tool.get("input_schema")))
        config = body.get("output_config")
        if isinstance(config, dict) and isinstance(output_format := config.get("format"), dict):
            self.slots.extend(slots_for_schema_values(output_format.get("schema")))

    def input_examples(self, definitions: list[Any]) -> None:
        """Every key and string of each tool's `input_examples`, except the strings under a
        `description` key, which are already slots as tool descriptions."""
        for index, tool in enumerate(definitions):
            if not isinstance(tool, dict) or (examples := tool.get("input_examples")) is None:
                continue
            where = f"tools[{index}].input_examples"
            if not isinstance(examples, list):
                raise MalformedRequestError(f"{where}: expected a list")
            self.slots.extend(
                slots_for_keys_and_strings(
                    examples, self.finalizers, where, skip_under="description"
                )
            )

    def stop_sequences(self, body: dict[str, Any]) -> None:
        value = body.get("stop_sequences")
        if value is None:
            return
        if not isinstance(value, list):
            raise MalformedRequestError("stop_sequences: expected a list")
        self.slots.extend(slots_for_string_list(value, "stop_sequences"))


def _strip_user_location(body: dict[str, Any]) -> None:
    tools = body.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if isinstance(tool, dict):
                tool.pop("user_location", None)


def _strip_user_id(body: dict[str, Any]) -> None:
    metadata = body.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop("user_id", None)
        if not metadata:
            del body["metadata"]


class AnthropicMessagesDialect:
    """Anthropic Messages payloads: slots in a fixed order, restoration of responses."""

    name = "anthropic_messages"

    def prepare_request(self, body: dict[str, Any], policy: Policy) -> PreparedRequest:
        """Collect the slots of `body` in the documented order and strip `metadata.user_id`
        and the tools' `user_location`.

        Raises UnsupportedContentError for uninspectable content under policy `block`, and
        MalformedRequestError when a walked field has the wrong shape. Changes `body` only
        through the returned slots and finalizers, and by removing the identifier and the
        location. Thinking blocks are never read or written.
        """
        if not isinstance(body, dict):
            raise MalformedRequestError("body: expected an object")
        messages = body.get("messages")
        if not isinstance(messages, list):
            raise MalformedRequestError("messages: expected a list")
        collector = _Collector(policy)
        collector.system(body)
        for index, message in enumerate(messages):
            where = f"messages[{index}]"
            collector.content(_object(message, where), where)
        if policy.tool_definitions:
            collector.tool_definitions(body)
        collector.stop_sequences(body)
        if policy.strip_user_identifiers:
            _strip_user_id(body)
        if policy.strip_user_location:
            _strip_user_location(body)
        return PreparedRequest(collector.slots, collector.finalizers)

    def restore_response(self, body: dict[str, Any], mapping: Mapping) -> None:
        """Restore, in place, the `text` of text blocks, every key and string of `tool_use`
        inputs and the `stop_sequence` the model met.

        Everything else (thinking blocks, citations, server tool blocks, usage, ids) is left
        untouched, and so is a body without `content`; use `restore_all_strings` for errors.
        """
        if isinstance(stop_sequence := body.get("stop_sequence"), str):
            body["stop_sequence"] = restore(stop_sequence, mapping)
        content = body.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text" and isinstance(text := block.get("text"), str):
                block["text"] = restore(text, mapping)
            elif kind == "tool_use" and "input" in block:
                block["input"] = restore_all_strings(block["input"], mapping)

    def stream_transformer(self, mapping: Mapping) -> StreamTransformer:
        """A transformer that restores one streamed message with `mapping`."""
        return AnthropicStreamTransformer(mapping)

    def error_body(self, status: int, code: str, message: str) -> dict[str, Any]:
        """A gateway error in the Anthropic error shape, the gateway code in parentheses."""
        family = _ERROR_TYPES.get(status, _FALLBACK_ERROR_TYPE)
        return {"type": "error", "error": {"type": family, "message": f"{message} ({code})"}}


# The delta type each restorable block streams, and the delta field that carries its text.
_DELTA_FIELDS: Final = {"text_delta": "text", "input_json_delta": "partial_json"}


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _index(data: dict[str, Any]) -> int | None:
    index = data.get("index")
    return index if isinstance(index, int) and not isinstance(index, bool) else None


class AnthropicStreamTransformer:
    """Restores the placeholders of one streamed message, event by event.

    Each `text` and `tool_use` content block has its own `StreamRestorer`, keyed by the block
    `index`, so placeholders split across deltas are restored and blocks never mix. Text still
    held when a block stops, at `message_stop` or at `end()` is sent in a synthetic
    `content_block_delta` just before; nothing held is emitted any other way. The
    `stop_sequence` of a `message_delta` is restored whole, since it never arrives in parts.
    Thinking deltas, signatures, citations and every other event pass unchanged, as the
    original object.
    """

    def __init__(self, mapping: Mapping) -> None:
        self._mapping = mapping
        self._blocks: dict[int, tuple[str, StreamRestorer]] = {}  # index → (delta type, restorer)
        self._settled = 0  # unresolved placeholders of discarded restorers and block starts
        self.unresolved = 0

    def feed(self, event: SSEEvent) -> list[SSEEvent]:
        """The events to send in place of `event`: synthetic flushes first, then the event."""
        try:
            data = json.loads(event.data)
        except (ValueError, RecursionError):
            return [event]
        if not isinstance(data, dict):
            return [event]
        kind = data.get("type")
        if kind == "message_stop":
            return [*self._flush_all(), event]
        if kind == "message_delta":
            return [self._message_delta(event, data)]
        index = _index(data)
        if index is None:
            return [event]
        if kind == "content_block_start":
            return self._start(event, data, index)
        if kind == "content_block_delta":
            return [self._delta(event, data, index)]
        if kind == "content_block_stop":
            return [*self._flush(index), event]
        return [event]

    def end(self) -> list[SSEEvent]:
        """Synthetic deltas for any text still held; called when the upstream stream ends."""
        return self._flush_all()

    def _start(self, event: SSEEvent, data: dict[str, Any], index: int) -> list[SSEEvent]:
        flushed = self._flush(index)  # a repeated index: send what the earlier block held
        block = data.get("content_block")
        if not isinstance(block, dict):
            return [*flushed, event]
        changed = False
        kind = block.get("type")
        if kind == "text":
            restorer = StreamRestorer(self._mapping)
            self._blocks[index] = ("text_delta", restorer)
            text = block.get("text")
            if isinstance(text, str) and text:
                block["text"] = restorer.feed(text)
                changed = block["text"] != text
        elif kind == "tool_use":
            self._blocks[index] = (
                "input_json_delta",
                StreamRestorer(self._mapping, json_escape=True),
            )
            tool_input = block.get("input")
            if isinstance(tool_input, dict) and tool_input:
                self._settled += sum(
                    count_unresolved(slot.get(), self._mapping)
                    for slot in slots_for_keys_and_strings(tool_input, [], "input")
                )
                block["input"] = restore_all_strings(tool_input, self._mapping)
                changed = block["input"] != tool_input
        self._count()
        if not changed:
            return [*flushed, event]
        return [*flushed, SSEEvent(event.event, _dumps(data), event.raw_fields)]

    def _delta(self, event: SSEEvent, data: dict[str, Any], index: int) -> SSEEvent:
        delta = data.get("delta")
        block = self._blocks.get(index)
        if not isinstance(delta, dict) or block is None:
            return event
        delta_type, restorer = block
        if delta.get("type") != delta_type:
            return event
        key = _DELTA_FIELDS[delta_type]
        value = delta.get(key)
        if not isinstance(value, str):
            return event
        restored = restorer.feed(value)
        self._count()
        if restored == value:
            return event
        delta[key] = restored
        return SSEEvent(event.event, _dumps(data), event.raw_fields)

    def _message_delta(self, event: SSEEvent, data: dict[str, Any]) -> SSEEvent:
        """The event with `delta.stop_sequence` restored; the original event if it has none."""
        delta = data.get("delta")
        if not isinstance(delta, dict) or not isinstance(value := delta.get("stop_sequence"), str):
            return event
        self._settled += count_unresolved(value, self._mapping)
        self._count()
        restored = restore(value, self._mapping)
        if restored == value:
            return event
        delta["stop_sequence"] = restored
        return SSEEvent(event.event, _dumps(data), event.raw_fields)

    def _flush(self, index: int) -> list[SSEEvent]:
        """A synthetic delta with the held text of one block, which is then discarded."""
        block = self._blocks.pop(index, None)
        if block is None:
            return []
        delta_type, restorer = block
        text = restorer.flush()
        self._settled += restorer.unresolved
        self._count()
        if not text:
            return []
        delta = {"type": delta_type, _DELTA_FIELDS[delta_type]: text}
        data = {"type": "content_block_delta", "index": index, "delta": delta}
        return [SSEEvent("content_block_delta", _dumps(data))]

    def _flush_all(self) -> list[SSEEvent]:
        return [event for index in list(self._blocks) for event in self._flush(index)]

    def _count(self) -> None:
        active = sum(restorer.unresolved for _, restorer in self._blocks.values())
        self.unresolved = self._settled + active
