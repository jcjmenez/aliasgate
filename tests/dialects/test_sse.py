import itertools

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aliasgate.dialects.sse import SSEEvent, SSEParser


def _parse(*chunks: bytes) -> list[SSEEvent]:
    parser = SSEParser()
    events = [event for chunk in chunks for event in parser.feed(chunk)]
    return events + parser.end()


def test_two_events_in_one_chunk() -> None:
    assert _parse(b"data: one\n\ndata: two\n\n") == [SSEEvent(None, "one"), SSEEvent(None, "two")]


def test_one_event_split_across_three_chunks() -> None:
    parser = SSEParser()
    assert parser.feed(b"da") == []
    assert parser.feed(b'ta: {"a":') == []
    assert parser.feed(b" 1}\n\n") == [SSEEvent(None, '{"a": 1}')]


@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b"\r"])
def test_every_line_ending_is_accepted(ending: bytes) -> None:
    stream = b"event: x" + ending + b"data: one" + ending + ending + b"data: two" + ending * 2
    assert _parse(stream) == [SSEEvent("x", "one"), SSEEvent(None, "two")]


def test_crlf_split_between_chunks_counts_as_one_line_ending() -> None:
    parser = SSEParser()
    assert parser.feed(b"data: one\r") == []
    assert parser.feed(b"\n\r") == [SSEEvent(None, "one")]
    assert parser.feed(b"\ndata: two\r\n\r\n") == [SSEEvent(None, "two")]


@pytest.mark.parametrize("text", ["é", "😀", "Pérez 😀 ñ"])
def test_multi_byte_characters_split_across_chunks_are_decoded(text: str) -> None:
    encoded = f"data: {text}\n\n".encode()
    for cut in range(len(encoded) + 1):
        assert _parse(encoded[:cut], encoded[cut:]) == [SSEEvent(None, text)]


def test_one_byte_per_chunk_works() -> None:
    encoded = "event: delta\ndata: Pérez\n\n".encode()
    assert _parse(*(encoded[i : i + 1] for i in range(len(encoded)))) == [
        SSEEvent("delta", "Pérez")
    ]


def test_multi_line_data_is_joined_and_re_encoded_as_several_data_lines() -> None:
    (event,) = _parse(b"data: first\ndata: second\ndata:third\n\n")
    assert event == SSEEvent(None, "first\nsecond\nthird")
    assert event.encode() == b"data: first\ndata: second\ndata: third\n\n"


def test_comments_are_dropped_and_the_event_field_is_kept() -> None:
    stream = b": keep-alive\n\n:another\nevent: message\n: inside\ndata: x\n\n"
    assert _parse(stream) == [SSEEvent("message", "x")]


def test_other_fields_are_kept_in_order() -> None:
    (event,) = _parse(b"id: 7\nretry: 1000\ndata: x\ncustom: y\n\n")
    assert event == SSEEvent(None, "x", (("id", "7"), ("retry", "1000"), ("custom", "y")))
    assert event.encode() == b"id: 7\nretry: 1000\ncustom: y\ndata: x\n\n"


def test_only_one_space_after_the_colon_is_removed() -> None:
    assert _parse(b"data:  two spaces\n\n") == [SSEEvent(None, " two spaces")]


def test_a_line_without_a_colon_is_a_field_with_an_empty_value() -> None:
    assert _parse(b"data\n\n") == [SSEEvent(None, "")]


def test_events_without_data_or_event_are_not_emitted() -> None:
    assert _parse(b"id: 1\n\nretry: 5\n\n\n\n") == []


def test_an_event_with_only_an_event_field_is_emitted() -> None:
    assert _parse(b"event: ping\n\n") == [SSEEvent("ping", "")]


def test_end_returns_an_event_left_without_a_blank_line() -> None:
    parser = SSEParser()
    assert parser.feed(b"data: one\n\ndata: [DONE]") == [SSEEvent(None, "one")]
    assert parser.end() == [SSEEvent(None, "[DONE]")]


def test_end_returns_nothing_after_a_complete_stream() -> None:
    parser = SSEParser()
    parser.feed(b"data: one\n\n")
    assert parser.end() == []


def test_a_leading_byte_order_mark_is_ignored() -> None:
    assert _parse("\ufeffdata: x\n\n".encode()) == [SSEEvent(None, "x")]


def test_invalid_utf8_raises() -> None:
    with pytest.raises(UnicodeDecodeError):
        _parse(b"data: \xff\n\n")


def test_a_truncated_multi_byte_character_at_the_end_raises() -> None:
    parser = SSEParser()
    parser.feed("data: é".encode()[:-1])
    with pytest.raises(UnicodeDecodeError):
        parser.end()


def test_encode_writes_event_then_fields_then_data() -> None:
    event = SSEEvent("delta", "a\nb", (("id", "3"),))
    assert event.encode() == b"event: delta\nid: 3\ndata: a\ndata: b\n\n"


_LINE = st.text(st.characters(codec="utf-8", exclude_characters="\r\n"))
_NAME = st.text(st.sampled_from("abcdefghijklmnopqrstuvwxyz-_"), min_size=1, max_size=8).filter(
    lambda name: name not in ("data", "event")
)
_EVENTS = st.builds(
    SSEEvent,
    st.none() | _LINE,
    st.lists(_LINE, min_size=1, max_size=4).map("\n".join),
    st.lists(st.tuples(_NAME, _LINE), max_size=3).map(tuple),
)


@given(_EVENTS)
def test_encode_then_parse_returns_an_equal_event(event: SSEEvent) -> None:
    assert _parse(event.encode()) == [event]


@given(st.lists(_EVENTS, min_size=1, max_size=5), st.data())
def test_a_stream_of_encoded_events_parses_back_at_any_chunking(
    events: list[SSEEvent], data: st.DataObject
) -> None:
    stream = b"".join(event.encode() for event in events)
    cuts = sorted(set(data.draw(st.lists(st.integers(0, len(stream)), max_size=8))))
    bounds = [0, *cuts, len(stream)]
    chunks = [stream[start:end] for start, end in itertools.pairwise(bounds)]
    assert _parse(*chunks) == events
