import itertools
import json
from pathlib import Path
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from aliasgate.dialects.anthropic_messages import (
    AnthropicMessagesDialect,
    AnthropicStreamTransformer,
)
from aliasgate.dialects.base import StreamTransformer
from aliasgate.dialects.sse import SSEEvent
from aliasgate.engine import restore
from aliasgate.mapping import Mapping

FIXTURES = Path(__file__).parent / "fixtures" / "anthropic"


def _mapping() -> Mapping:
    mapping = Mapping()
    mapping.assign("PERSON", 'Juan "JP" Pérez')
    mapping.assign("ORG", "ACME Corporation")
    mapping.assign("EMAIL", "juan.perez@example.com")
    return mapping


def _load(name: str) -> list[SSEEvent]:
    items = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return [_event(data, name=event) for event, data in items]


def _event(data: dict[str, Any], *, name: str | None = None) -> SSEEvent:
    return SSEEvent(name or data["type"], json.dumps(data, ensure_ascii=False))


def _transformer(mapping: Mapping | None = None) -> StreamTransformer:
    return AnthropicMessagesDialect().stream_transformer(mapping or _mapping())


def _run(events: list[SSEEvent], *, end: bool = True) -> list[SSEEvent]:
    transformer = _transformer()
    out = [result for event in events for result in transformer.feed(event)]
    return out + transformer.end() if end else out


def _decoded(events: list[SSEEvent]) -> list[dict[str, Any]]:
    return [json.loads(event.data) for event in events]


def _text(events: list[SSEEvent], index: int, key: str = "text") -> str:
    parts = []
    for data in _decoded(events):
        if data.get("index") != index:
            continue
        if data["type"] == "content_block_start":
            parts.append(data["content_block"].get(key) or "")
        elif data["type"] == "content_block_delta":
            parts.append(data["delta"].get(key) or "")
    return "".join(parts)


def _start(index: int, block: dict[str, Any]) -> SSEEvent:
    return _event({"type": "content_block_start", "index": index, "content_block": block})


def _delta(index: int, text: str, kind: str = "text_delta") -> SSEEvent:
    key = "text" if kind == "text_delta" else "partial_json"
    return _event(
        {"type": "content_block_delta", "index": index, "delta": {"type": kind, key: text}}
    )


def _stop(index: int) -> SSEEvent:
    return _event({"type": "content_block_stop", "index": index})


TEXT_BLOCK = {"type": "text", "text": ""}
TOOL_BLOCK = {"type": "tool_use", "id": "toolu_x", "name": "f", "input": {}}


def test_the_dialect_returns_an_anthropic_stream_transformer() -> None:
    assert isinstance(_transformer(), AnthropicStreamTransformer)


def test_a_placeholder_split_across_three_text_deltas_is_restored() -> None:
    events = _load("stream_text_split.json")
    out = _run(events)
    assert _text(out, 0) == 'Dear Juan "JP" Pérez of ACME Corporation.'
    assert [event.event for event in out] == [event.event for event in events]
    deltas = [
        data["delta"]["text"] for data in _decoded(out) if data["type"] == "content_block_delta"
    ]
    assert deltas == ["Dear ", "", 'Juan "JP" Pérez of ACME Corporation.']


def test_events_that_carry_no_restorable_text_are_returned_as_the_original_objects() -> None:
    events = _load("stream_text_split.json")
    out = _run(events)
    for position in (0, 1, 2, 6, 7, 8):  # message_start, block start, ping, stop, delta, stop
        assert out[position] is events[position]


def test_held_text_at_content_block_stop_comes_in_a_synthetic_delta_before_the_stop() -> None:
    events = [_start(3, TEXT_BLOCK), _delta(3, "Dear [PERSON_1"), _stop(3)]
    out = _run(events, end=False)
    synthetic, stop = out[-2:]
    assert stop is events[-1]
    assert synthetic.event == "content_block_delta"
    assert json.loads(synthetic.data) == {
        "type": "content_block_delta",
        "index": 3,
        "delta": {"type": "text_delta", "text": "[PERSON_1"},
    }
    assert _text(out, 3) == "Dear [PERSON_1"


def test_a_stop_with_nothing_held_adds_no_event() -> None:
    events = [_start(0, TEXT_BLOCK), _delta(0, "Hello [ORG_1]"), _stop(0)]
    out = _run(events)
    assert len(out) == 3
    assert out[-1] is events[-1]


def test_tool_use_partial_json_reassembles_to_json_with_restored_escaped_values() -> None:
    out = _run(_load("stream_tool_use.json"))
    arguments = _text(out, 2, key="partial_json")
    assert json.loads(arguments) == {
        "to": "juan.perez@example.com",
        "greeting": 'Dear Juan "JP" Pérez',
    }
    start = _decoded(out)[8]
    assert start["content_block"] == {
        "type": "tool_use",
        "id": "toolu_example02",
        "name": "send_email",
        "input": {},
    }


def test_a_tool_use_start_with_a_non_empty_input_is_restored() -> None:
    block = {"type": "tool_use", "id": "t", "name": "f", "input": {"who": ["[PERSON_1]", 3]}}
    (result,) = _transformer().feed(_start(0, block))
    assert json.loads(result.data)["content_block"]["input"] == {"who": ['Juan "JP" Pérez', 3]}


def test_a_text_start_with_non_empty_text_is_fed_to_the_restorer() -> None:
    events = [_start(0, {"type": "text", "text": "Hi [PER"}), _delta(0, "SON_1]!"), _stop(0)]
    out = _run(events)
    assert json.loads(out[0].data)["content_block"]["text"] == "Hi "
    assert _text(out, 0) == 'Hi Juan "JP" Pérez!'


def test_interleaved_indexes_keep_separate_buffers() -> None:
    events = [
        _start(0, TEXT_BLOCK),
        _start(1, TOOL_BLOCK),
        _delta(0, "To [PER"),
        _delta(1, '{"org": "[OR', "input_json_delta"),
        _delta(0, "SON_1] at "),
        _delta(1, 'G_1]", "to": "[PERSON_1]"}', "input_json_delta"),
        _delta(0, "[ORG_1]"),
        _stop(1),
        _stop(0),
    ]
    out = _run(events)
    assert _text(out, 0) == 'To Juan "JP" Pérez at ACME Corporation'
    assert json.loads(_text(out, 1, key="partial_json")) == {
        "org": "ACME Corporation",
        "to": 'Juan "JP" Pérez',
    }


def test_thinking_and_signature_deltas_pass_unchanged() -> None:
    events = _load("stream_tool_use.json")
    out = _run(events)
    for position in range(5):  # message_start and the whole thinking block
        assert out[position] is events[position]
    assert json.loads(out[2].data)["delta"]["thinking"] == "Mail [PERSON_1] at [EMAIL_1]."


def test_ping_and_error_events_pass_unchanged() -> None:
    ping = _event({"type": "ping"})
    error = _event(
        {"type": "error", "error": {"type": "overloaded_error", "message": "About [PERSON_1]"}}
    )
    transformer = _transformer()
    assert transformer.feed(ping) == [ping]
    assert transformer.feed(error)[0] is error


def test_unknown_event_types_pass_unchanged_even_with_an_index() -> None:
    transformer = _transformer()
    transformer.feed(_start(0, TEXT_BLOCK))
    event = _event({"type": "content_block_note", "index": 0, "text": "[PERSON_1]"})
    assert transformer.feed(event) == [event]


def test_citations_deltas_and_unknown_delta_types_pass_unchanged() -> None:
    citation = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "citations_delta", "citation": {"cited_text": "[PERSON_1]"}},
    }
    unknown = {"type": "content_block_delta", "index": 0, "delta": {"type": "new_delta"}}
    transformer = _transformer()
    transformer.feed(_start(0, TEXT_BLOCK))
    for data in (citation, unknown):
        event = _event(data)
        assert transformer.feed(event)[0] is event


def test_server_tool_input_deltas_and_other_block_types_pass_unchanged() -> None:
    server = {"type": "server_tool_use", "id": "s", "name": "web_search", "input": {}}
    events = [
        _start(0, server),
        _delta(0, '{"query": "[ORG_1]"}', "input_json_delta"),
        _stop(0),
        _start(1, {"type": "web_search_tool_result", "tool_use_id": "s", "content": []}),
        _stop(1),
    ]
    out = _run(events)
    assert len(out) == len(events)
    assert all(result is event for result, event in zip(out, events, strict=True))


def test_a_delta_for_a_block_that_never_started_passes_unchanged() -> None:
    event = _delta(5, "[PERSON_1]")
    assert _transformer().feed(event)[0] is event


def test_a_delta_whose_type_does_not_match_its_block_passes_unchanged() -> None:
    transformer = _transformer()
    transformer.feed(_start(0, TEXT_BLOCK))
    transformer.feed(_start(1, TOOL_BLOCK))
    for event in (_delta(0, "[PERSON_1]", "input_json_delta"), _delta(1, "[PERSON_1]")):
        assert transformer.feed(event)[0] is event


def test_end_flushes_every_open_block_as_synthetic_deltas() -> None:
    transformer = _transformer()
    for event in (
        _start(0, TEXT_BLOCK),
        _delta(0, "a [PER"),
        _start(1, TOOL_BLOCK),
        _delta(1, '{"x": "[EMA', "input_json_delta"),
    ):
        transformer.feed(event)
    assert _decoded(transformer.end()) == [
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "[PER"},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": "[EMA"},
        },
    ]
    assert transformer.end() == []


def test_message_stop_flushes_blocks_that_were_never_stopped_before_itself() -> None:
    transformer = _transformer()
    transformer.feed(_start(0, TEXT_BLOCK))
    transformer.feed(_delta(0, "x [ORG"))
    message_stop = _event({"type": "message_stop"})
    synthetic, last = transformer.feed(message_stop)
    assert last is message_stop
    assert json.loads(synthetic.data)["delta"] == {"type": "text_delta", "text": "[ORG"}
    assert transformer.end() == []


def test_a_second_start_for_the_same_index_flushes_the_first_block_before_it() -> None:
    transformer = _transformer()
    transformer.feed(_start(0, TEXT_BLOCK))
    transformer.feed(_delta(0, "x [ORG"))
    restart = _start(0, TOOL_BLOCK)
    synthetic, start = transformer.feed(restart)
    assert json.loads(synthetic.data)["delta"] == {"type": "text_delta", "text": "[ORG"}
    assert start is restart
    (delta,) = transformer.feed(_delta(0, '{"o": "[ORG_1]"}', "input_json_delta"))
    assert json.loads(json.loads(delta.data)["delta"]["partial_json"]) == {"o": "ACME Corporation"}


def test_modified_events_keep_the_event_name_and_other_fields_and_are_compact() -> None:
    data = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "[ORG_1]"},
    }
    transformer = _transformer()
    transformer.feed(_start(0, TEXT_BLOCK))
    event = SSEEvent("content_block_delta", json.dumps(data), (("id", "9"),))
    (result,) = transformer.feed(event)
    assert result.event == "content_block_delta"
    assert result.raw_fields == (("id", "9"),)
    assert json.loads(result.data)["delta"]["text"] == "ACME Corporation"
    assert ", " not in result.data
    assert ": " not in result.data


def test_non_ascii_surfaces_are_written_unescaped() -> None:
    transformer = _transformer()
    transformer.feed(_start(0, TEXT_BLOCK))
    (result,) = transformer.feed(_delta(0, "[PERSON_1]"))
    assert "Pérez" in result.data


def test_data_that_is_not_a_json_object_passes_unchanged() -> None:
    transformer = _transformer()
    for data in ("not json [PERSON_1]", '["[PERSON_1]"]', "42", '{"type": 7}'):
        event = SSEEvent("content_block_delta", data)
        assert transformer.feed(event)[0] is event


def test_events_with_an_index_that_is_not_an_integer_pass_unchanged() -> None:
    transformer = _transformer()
    for index in ("0", None, True, 1.0):
        start = _event({"type": "content_block_start", "index": index, "content_block": TEXT_BLOCK})
        assert transformer.feed(start)[0] is start
    transformer.feed(_start(0, TEXT_BLOCK))
    stop = _event({"type": "content_block_stop", "index": True})
    assert transformer.feed(stop) == [stop]


def test_malformed_blocks_and_deltas_pass_unchanged() -> None:
    transformer = _transformer()
    transformer.feed(_start(0, TEXT_BLOCK))
    for data in (
        {"type": "content_block_start", "index": 1, "content_block": "[PERSON_1]"},
        {"type": "content_block_start", "index": 2, "content_block": {"type": "text", "text": 7}},
        {"type": "content_block_delta", "index": 0, "delta": "[PERSON_1]"},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": 7}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta"}},
    ):
        event = _event(data)
        assert transformer.feed(event)[0] is event


def _message_delta(delta: dict[str, Any]) -> SSEEvent:
    usage = {"output_tokens": 5}
    return _event({"type": "message_delta", "delta": delta, "usage": usage})


def test_the_stop_sequence_of_a_message_delta_is_restored() -> None:
    event = _message_delta({"stop_reason": "stop_sequence", "stop_sequence": "[PERSON_1]:"})
    out = _transformer().feed(event)
    assert len(out) == 1
    data = json.loads(out[0].data)
    assert data["delta"] == {
        "stop_reason": "stop_sequence",
        "stop_sequence": 'Juan "JP" P\u00e9rez:',
    }
    assert data["usage"] == {"output_tokens": 5}
    assert out[0].event == "message_delta"


def test_a_message_delta_without_a_stop_sequence_is_the_original_event() -> None:
    for delta in ({"stop_reason": "end_turn", "stop_sequence": None}, {"stop_reason": "end_turn"}):
        event = _message_delta(delta)
        assert _transformer().feed(event) == [event]
        assert _transformer().feed(event)[0] is event


def test_a_stop_sequence_without_placeholders_leaves_the_event_unchanged() -> None:
    event = _message_delta({"stop_reason": "stop_sequence", "stop_sequence": "\nUser:"})
    assert _transformer().feed(event)[0] is event


def test_malformed_message_deltas_pass_unchanged() -> None:
    for data in (
        {"type": "message_delta", "delta": "x"},
        {"type": "message_delta", "delta": {"stop_sequence": 3}},
    ):
        event = _event(data)
        assert _transformer().feed(event)[0] is event


def test_unknown_placeholders_in_a_stop_sequence_are_counted_as_unresolved() -> None:
    transformer = _transformer()
    transformer.feed(_message_delta({"stop_sequence": "[PERSON_9] and [PERSON_1]"}))
    assert transformer.unresolved == 1


def test_unresolved_counts_unknown_placeholders_across_blocks_and_starts() -> None:
    transformer = _transformer()
    events = [
        _start(0, TEXT_BLOCK),
        _delta(0, "[PERSON_1] [PERS"),
        _delta(0, "ON_7]"),
        _stop(0),
        _start(1, {"type": "tool_use", "id": "t", "name": "f", "input": {"a": "[ORG_9]"}}),
        _delta(1, '{"b": "[EMAIL_4', "input_json_delta"),
    ]
    for event in events:
        transformer.feed(event)
    assert transformer.unresolved == 2
    transformer.feed(_delta(1, ']"}', "input_json_delta"))
    transformer.end()
    assert transformer.unresolved == 3


_TOKENS = [
    "[PERSON_1]",
    "[ORG_1]",
    "[PERSON_9]",
    "[person 1]",
    "\\[ORG_1\\]",
    "[",
    "]",
    "\\",
    '"',
    "a",
    " ",
]


@st.composite
def _chunked_text(draw: st.DrawFn) -> tuple[str, list[str]]:
    text = "".join(draw(st.lists(st.sampled_from(_TOKENS), max_size=25)))
    cuts = sorted(set(draw(st.lists(st.integers(0, len(text)), max_size=10))))
    bounds = [0, *cuts, len(text)]
    return text, [text[start:end] for start, end in itertools.pairwise(bounds)]


@settings(max_examples=500)
@given(_chunked_text())
def test_any_chunking_of_text_deltas_gives_restore_of_the_whole_text(
    case: tuple[str, list[str]],
) -> None:
    text, parts = case
    events = [_start(0, TEXT_BLOCK), *(_delta(0, part) for part in parts), _stop(0)]
    assert _text(_run(events), 0) == restore(text, _mapping())


@settings(max_examples=300)
@given(_chunked_text())
def test_any_chunking_of_json_deltas_gives_restore_of_the_whole_json(
    case: tuple[str, list[str]],
) -> None:
    text, parts = case
    events = [
        _start(0, TOOL_BLOCK),
        *(_delta(0, part, "input_json_delta") for part in parts),
        _stop(0),
    ]
    expected = restore(text, _mapping(), json_escape=True)
    assert _text(_run(events), 0, key="partial_json") == expected
