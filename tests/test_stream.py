import itertools

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aliasgate.engine import count_unresolved, restore
from aliasgate.mapping import Mapping
from aliasgate.placeholders import MAX_TOLERANT_LENGTH
from aliasgate.stream import StreamRestorer

TEXT = "Hi [PERSON_1], welcome to [ORG_1]."


def _mapping() -> Mapping:
    mapping = Mapping()
    mapping.assign("PERSON", 'Juan "JP" Pérez')
    mapping.assign("ORG", "ACME")
    return mapping


def _stream(chunks: list[str], *, json_escape: bool = False) -> str:
    restorer = StreamRestorer(_mapping(), json_escape=json_escape)
    return "".join(restorer.feed(chunk) for chunk in chunks) + restorer.flush()


def test_a_placeholder_in_one_chunk_is_restored() -> None:
    assert _stream([TEXT]) == 'Hi Juan "JP" Pérez, welcome to ACME.'


@pytest.mark.parametrize("cut", range(len(TEXT) + 1))
def test_two_chunks_cut_anywhere_give_the_same_result_as_one(cut: int) -> None:
    assert _stream([TEXT[:cut], TEXT[cut:]]) == _stream([TEXT])


def test_one_character_per_chunk_gives_the_same_result() -> None:
    assert _stream(list(TEXT)) == _stream([TEXT])


def test_text_after_an_unclosed_bracket_is_held_until_flush_and_then_unchanged() -> None:
    restorer = StreamRestorer(_mapping())
    assert restorer.feed("see [PERS") == "see "
    assert restorer.feed("ON and more") == ""
    assert restorer.flush() == "[PERSON and more"


def test_an_unclosed_bracket_is_released_once_it_cannot_be_a_placeholder() -> None:
    restorer = StreamRestorer(_mapping())
    assert restorer.feed("x [") == "x "
    tail = "a" * MAX_TOLERANT_LENGTH
    assert restorer.feed(tail) == "[" + tail
    assert restorer.flush() == ""


def test_a_tail_of_exactly_the_maximum_length_is_still_held() -> None:
    restorer = StreamRestorer(_mapping())
    held = "[" + "a" * (MAX_TOLERANT_LENGTH - 1)
    assert restorer.feed(held) == ""
    assert restorer.flush() == held


def test_array_like_text_passes_through_unchanged() -> None:
    restorer = StreamRestorer(_mapping())
    assert restorer.feed("[1, 2, 3]") == "[1, 2, 3]"
    assert restorer.flush() == ""


def test_the_escaped_form_split_between_backslash_and_bracket_is_restored() -> None:
    restorer = StreamRestorer(_mapping())
    assert restorer.feed("Dear \\") == "Dear "
    assert restorer.feed("[PERSON_1\\], hi") == 'Juan "JP" Pérez, hi'
    assert restorer.flush() == ""


def test_a_trailing_backslash_without_a_bracket_is_emitted_on_flush() -> None:
    restorer = StreamRestorer(_mapping())
    assert restorer.feed("path\\") == "path"
    assert restorer.flush() == "\\"


def test_json_escape_escapes_the_quotes_of_the_surface() -> None:
    assert _stream(['{"to":"[PERS', 'ON_1]"}'], json_escape=True) == '{"to":"Juan \\"JP\\" Pérez"}'


def test_unresolved_counts_an_unknown_placeholder_once() -> None:
    restorer = StreamRestorer(_mapping())
    restorer.feed("[PERSON_1] and [PERS")
    restorer.feed("ON_9] and [ORG_1]")
    restorer.flush()
    assert restorer.unresolved == 1


def test_flush_empties_the_buffer() -> None:
    restorer = StreamRestorer(_mapping())
    restorer.feed("[ORG")
    assert restorer.flush() == "[ORG"
    assert restorer.flush() == ""
    assert restorer.feed("_1]") == "_1]"


_TOKENS = [
    "[PERSON_1]",
    "[ORG_1]",
    "[PERSON_9]",
    "[LITERAL_1]",
    "[person 1]",
    "[ PERSON_1 ]",
    "[CREDIT\\_CARD\\_1]",
    "\\[PERSON_1\\]",
    "\\[ORG_1]",
    "[",
    "]",
    "\\",
    "_",
    "1",
    "7",
    "PERSON",
    "ORG",
    "a",
    "Z",
    " ",
    "\t",
    "\n",
    "é",
    "x" * 70,
]
_TEXTS = st.lists(st.sampled_from(_TOKENS), max_size=30).map("".join)


@st.composite
def _chunked(draw: st.DrawFn) -> tuple[str, list[str]]:
    text = draw(_TEXTS)
    cuts = sorted(set(draw(st.lists(st.integers(0, len(text)), max_size=12))))
    bounds = [0, *cuts, len(text)]
    return text, [text[start:end] for start, end in itertools.pairwise(bounds)]


@settings(max_examples=1000)
@given(_chunked(), st.booleans())
def test_any_chunking_gives_the_same_result_as_restore(
    case: tuple[str, list[str]], json_escape: bool
) -> None:
    text, chunks = case
    assert _stream(chunks, json_escape=json_escape) == restore(
        text, _mapping(), json_escape=json_escape
    )


@settings(max_examples=1000)
@given(_chunked())
def test_nothing_emitted_is_ever_retracted(case: tuple[str, list[str]]) -> None:
    text, chunks = case
    expected = restore(text, _mapping())
    restorer = StreamRestorer(_mapping())
    emitted = ""
    for chunk in chunks:
        emitted += restorer.feed(chunk)
        assert expected.startswith(emitted)
    assert emitted + restorer.flush() == expected


@settings(max_examples=300)
@given(_chunked())
def test_unresolved_matches_the_count_on_the_whole_text(case: tuple[str, list[str]]) -> None:
    text, chunks = case
    restorer = StreamRestorer(_mapping())
    for chunk in chunks:
        restorer.feed(chunk)
    restorer.flush()
    assert restorer.unresolved == count_unresolved(text, _mapping())
