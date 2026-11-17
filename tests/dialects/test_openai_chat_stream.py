import itertools
import json
from pathlib import Path
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from aliasgate.dialects.base import StreamTransformer
from aliasgate.dialects.openai_chat import OpenAIChatDialect, OpenAIChatStreamTransformer
from aliasgate.dialects.sse import SSEEvent
from aliasgate.engine import restore
from aliasgate.mapping import Mapping

FIXTURES = Path(__file__).parent / "fixtures" / "openai_chat"
ENVELOPE = {
    "id": "chatcmpl-example0002",
    "object": "chat.completion.chunk",
    "created": 1763380800,
    "model": "gpt-example",
    "system_fingerprint": "fp_example",
}


def _mapping() -> Mapping:
    mapping = Mapping()
    mapping.assign("PERSON", 'Juan "JP" Pérez')
    mapping.assign("ORG", "ACME Corporation")
    mapping.assign("EMAIL", "juan.perez@example.com")
    return mapping


def _load(name: str) -> list[Any]:
    items: list[Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return items


def _event(item: Any) -> SSEEvent:
    return SSEEvent(None, item if isinstance(item, str) else json.dumps(item, ensure_ascii=False))


def _transformer(mapping: Mapping | None = None) -> StreamTransformer:
    return OpenAIChatDialect().stream_transformer(mapping or _mapping())


def _run(items: list[Any], *, end: bool = True) -> list[SSEEvent]:
    transformer = _transformer()
    out = [result for item in items for result in transformer.feed(_event(item))]
    return out + transformer.end() if end else out


def _decoded(events: list[SSEEvent]) -> list[Any]:
    return [event.data if event.data == "[DONE]" else json.loads(event.data) for event in events]


def _choices(chunks: list[Any], index: int) -> list[dict[str, Any]]:
    return [
        choice
        for chunk in chunks
        if isinstance(chunk, dict)
        for choice in chunk.get("choices", [])
        if choice["index"] == index
    ]


def _content(chunks: list[Any], index: int = 0) -> str:
    return "".join(choice["delta"].get("content") or "" for choice in _choices(chunks, index))


def _arguments(chunks: list[Any], tool: int, index: int = 0) -> str:
    return "".join(
        call["function"].get("arguments") or ""
        for choice in _choices(chunks, index)
        for call in choice["delta"].get("tool_calls") or []
        if call["index"] == tool and "function" in call
    )


def test_the_dialect_returns_an_openai_stream_transformer() -> None:
    assert isinstance(_transformer(), OpenAIChatStreamTransformer)


def test_content_split_across_chunks_is_restored_and_the_finish_chunk_comes_last() -> None:
    chunks = _decoded(_run(_load("stream_content_split.json")))
    assert _content(chunks) == 'Dear Juan "JP" Pérez of ACME Corporation.'
    assert chunks[-1] == "[DONE]"
    assert chunks[-2]["choices"][0]["finish_reason"] == "stop"
    assert [chunk["choices"][0]["finish_reason"] for chunk in chunks[:-2]] == [None] * 4


def test_held_back_deltas_are_emitted_empty_not_dropped() -> None:
    chunks = _decoded(_run(_load("stream_content_split.json")))
    deltas = [chunk["choices"][0]["delta"].get("content") for chunk in chunks[:-1]]
    assert deltas == ["", "Dear ", 'Juan "JP" Pérez', " of ACME Corporation.", None]


def test_text_still_held_at_finish_comes_in_a_synthetic_chunk_before_the_finish_chunk() -> None:
    chunks = _decoded(_run(_load("stream_held_at_finish.json")))
    synthetic, finish, done = chunks[-3:]
    assert synthetic == {
        **ENVELOPE,
        "choices": [{"index": 0, "delta": {"content": "[PERSON_1"}, "finish_reason": None}],
    }
    assert finish["choices"][0]["finish_reason"] == "length"
    assert done == "[DONE]"
    assert _content(chunks) == "Dear [PERSON_1"


def test_tool_call_arguments_in_fragments_are_restored_as_valid_json() -> None:
    chunks = _decoded(_run(_load("stream_tool_call.json")))
    assert json.loads(_arguments(chunks, tool=0)) == {"email": "juan.perez@example.com"}
    first = chunks[0]["choices"][0]["delta"]["tool_calls"][0]
    assert first["id"] == "call_0001"
    assert first["function"]["name"] == "send_email"


def test_restored_tool_arguments_escape_quotes_in_the_surface() -> None:
    items = [
        {
            **ENVELOPE,
            "choices": [
                {
                    "index": 0,
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": part}}]},
                    "finish_reason": None,
                }
            ],
        }
        for part in ['{"who":"[PER', 'SON_1]"}']
    ]
    chunks = _decoded(_run(items))
    assert json.loads(_arguments(chunks, tool=0)) == {"who": 'Juan "JP" Pérez'}


def test_two_interleaved_choices_keep_independent_buffers() -> None:
    chunks = _decoded(_run(_load("stream_two_choices.json")))
    assert _content(chunks, 0) == 'Hi Juan "JP" Pérez!'
    assert _content(chunks, 1) == 'Yo ACME Corporation? Juan "JP" Pérez'


def test_two_parallel_tool_calls_keep_independent_buffers() -> None:
    chunks = _decoded(_run(_load("stream_parallel_tools.json")))
    assert json.loads(_arguments(chunks, tool=0)) == {"who": 'Juan "JP" Pérez'}
    assert json.loads(_arguments(chunks, tool=1)) == {"who": "ACME Corporation"}


def test_a_usage_only_final_chunk_passes_through_unchanged() -> None:
    items = _load("stream_parallel_tools.json")
    usage = _event(items[-2])
    transformer = _transformer()
    for item in items[:-2]:
        transformer.feed(_event(item))
    assert transformer.feed(usage) == [usage]


def test_a_non_json_data_line_passes_through_unchanged() -> None:
    event = SSEEvent("message", "not json [PERSON_1]", (("id", "4"),))
    assert _transformer().feed(event) == [event]


def test_json_that_is_not_a_chunk_object_passes_through_unchanged() -> None:
    for data in ('["[PERSON_1]"]', '{"object": "other", "text": "[PERSON_1]"}', "42"):
        event = SSEEvent(None, data)
        assert _transformer().feed(event) == [event]


def test_an_error_object_in_the_stream_is_restored() -> None:
    event = SSEEvent(None, json.dumps({"error": {"message": "Bad input about [PERSON_1]"}}))
    (result,) = _transformer().feed(event)
    assert json.loads(result.data) == {"error": {"message": 'Bad input about Juan "JP" Pérez'}}


def test_a_chunk_that_changes_nothing_is_returned_as_it_was() -> None:
    event = SSEEvent(None, json.dumps({**ENVELOPE, "choices": [{"index": 0, "delta": {}}]}))
    assert _transformer().feed(event) == [event]


def test_modified_chunks_keep_the_event_name_and_other_fields() -> None:
    item = {**ENVELOPE, "choices": [{"index": 0, "delta": {"content": "[ORG_1]"}}]}
    (result,) = _transformer().feed(SSEEvent("chunk", json.dumps(item), (("id", "9"),)))
    assert result.event == "chunk"
    assert result.raw_fields == (("id", "9"),)
    assert json.loads(result.data)["choices"][0]["delta"]["content"] == "ACME Corporation"
    assert ", " not in result.data  # compact separators


def test_end_without_done_flushes_held_text() -> None:
    items = _load("stream_held_at_finish.json")[:2]
    out = _run(items, end=False)
    assert _content(_decoded(out)) == "Dear "
    flushed = _decoded(_transformer_after(items).end())
    assert flushed == [
        {
            **ENVELOPE,
            "choices": [{"index": 0, "delta": {"content": "[PERSON_1"}, "finish_reason": None}],
        }
    ]


def _transformer_after(items: list[Any]) -> StreamTransformer:
    transformer = _transformer()
    for item in items:
        transformer.feed(_event(item))
    return transformer


def test_done_flushes_every_channel_of_every_choice_before_itself() -> None:
    transformer = _transformer()
    deltas = [
        (0, {"content": "a [PER"}),
        (0, {"refusal": "no [ORG"}),
        (1, {"tool_calls": [{"index": 2, "function": {"arguments": '{"x":"[EMA'}}]}),
        (1, {"function_call": {"arguments": '{"y":"[PER'}}),
        (0, {"tool_calls": [{"index": 0, "custom": {"input": "find [ORG"}}]}),
        (1, {"audio": {"transcript": "Hello [PERSON"}}),
    ]
    for index, delta in deltas:
        transformer.feed(_event({**ENVELOPE, "choices": [{"index": index, "delta": delta}]}))
    done = SSEEvent(None, "[DONE]")
    out = transformer.feed(done)
    assert out[-1] == done
    assert [chunk["choices"][0] for chunk in _decoded(out[:-1])] == [
        {"index": 0, "delta": {"content": "[PER"}, "finish_reason": None},
        {"index": 0, "delta": {"refusal": "[ORG"}, "finish_reason": None},
        {
            "index": 1,
            "delta": {"tool_calls": [{"index": 2, "function": {"arguments": "[EMA"}}]},
            "finish_reason": None,
        },
        {"index": 1, "delta": {"function_call": {"arguments": "[PER"}}, "finish_reason": None},
        {
            "index": 0,
            "delta": {"tool_calls": [{"index": 0, "custom": {"input": "[ORG"}}]},
            "finish_reason": None,
        },
        {"index": 1, "delta": {"audio": {"transcript": "[PERSON"}}, "finish_reason": None},
    ]
    assert transformer.end() == []


def test_refusal_custom_input_and_audio_transcript_are_restored() -> None:
    delta = {
        "refusal": "Not about [PERSON_1].",
        "tool_calls": [{"index": 0, "custom": {"input": "lookup [ORG_1]"}}],
        "audio": {"id": "audio_1", "data": "AAAA", "transcript": "Hi [person_1]"},
    }
    (result,) = _transformer().feed(_event({**ENVELOPE, "choices": [{"index": 0, "delta": delta}]}))
    restored = json.loads(result.data)["choices"][0]["delta"]
    assert restored == {
        "refusal": 'Not about Juan "JP" Pérez.',
        "tool_calls": [{"index": 0, "custom": {"input": "lookup ACME Corporation"}}],
        "audio": {"id": "audio_1", "data": "AAAA", "transcript": 'Hi Juan "JP" Pérez'},
    }


def test_choices_without_an_index_or_a_delta_are_left_alone() -> None:
    item = {
        **ENVELOPE,
        "choices": [
            "[PERSON_1]",
            {"delta": {"content": "[PERSON_1]"}},
            {"index": 0, "delta": "[PERSON_1]"},
            {"index": 0, "delta": {"tool_calls": ["[PERSON_1]", {"function": {"arguments": "x"}}]}},
        ],
    }
    event = _event(item)
    assert _transformer().feed(event) == [event]


def test_unresolved_counts_unknown_placeholders_across_chunks() -> None:
    transformer = _transformer()
    for part in ["[PERSON_1] [PERS", "ON_7] [ORG_9", "]"]:
        transformer.feed(
            _event({**ENVELOPE, "choices": [{"index": 0, "delta": {"content": part}}]})
        )
    assert transformer.unresolved == 2


_TOKENS = [
    "[PERSON_1]",
    "[ORG_1]",
    "[PERSON_9]",
    "[person 1]",
    "\\[ORG_1\\]",
    "[",
    "]",
    "\\",
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
def test_any_chunking_of_content_deltas_gives_restore_of_the_whole_text(
    case: tuple[str, list[str]],
) -> None:
    text, parts = case
    items: list[Any] = [
        {**ENVELOPE, "choices": [{"index": 0, "delta": {"content": part}, "finish_reason": None}]}
        for part in parts
    ]
    items.append({**ENVELOPE, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    items.append("[DONE]")
    assert _content(_decoded(_run(items))) == restore(text, _mapping())
