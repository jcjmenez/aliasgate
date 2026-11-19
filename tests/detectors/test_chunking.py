import re
from itertools import pairwise

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aliasgate.detectors.chunking import Window, windows

# The unit GLiNER counts (gliner 0.2.30, WhitespaceTokenSplitter). Windows count the same unit,
# so `window_words` bounds what the model sees and it never truncates a window.
MODEL_WORD = re.compile(r"\w+(?:[-_]\w+)*|\S")


def _words(text: str) -> list[tuple[int, int]]:
    return [(match.start(), match.end()) for match in MODEL_WORD.finditer(text)]


def _words_in(window: Window, words: list[tuple[int, int]]) -> list[int]:
    return [
        index
        for index, (start, end) in enumerate(words)
        if start >= window.start and end <= window.end
    ]


def test_short_text_yields_one_window_covering_it_all() -> None:
    text = "Juan Pérez works at ACME Corporation."
    assert windows(text, window_words=250, overlap_words=50) == [Window(0, len(text))]


def test_a_text_of_exactly_window_words_words_yields_one_window() -> None:
    text = " ".join(["word"] * 250)
    assert windows(text, window_words=250, overlap_words=50) == [Window(0, len(text))]


def test_surrounding_whitespace_is_left_outside_the_window() -> None:
    assert windows("  hola mundo \n", window_words=250, overlap_words=50) == [Window(2, 12)]


@pytest.mark.parametrize(
    "text",
    ["", " ", "\n\t \r", chr(0xA0) + chr(0x2003) + chr(0x3000)],
    ids=["empty", "spaces", "control-whitespace", "unicode-whitespace"],
)
def test_empty_or_whitespace_only_text_yields_no_window(text: str) -> None:
    assert windows(text, window_words=250, overlap_words=50) == []


def test_600_words_with_window_250_and_overlap_50_start_at_words_0_200_400() -> None:
    text = " ".join(f"w{index}" for index in range(600))
    words = _words(text)
    result = windows(text, window_words=250, overlap_words=50)
    assert [_words_in(window, words)[0] for window in result] == [0, 200, 400]
    assert [_words_in(window, words)[-1] for window in result] == [249, 449, 599]
    starts = {start for start, _ in words}
    ends = {end for _, end in words}
    assert all(window.start in starts and window.end in ends for window in result)
    assert result[0].start == 0
    assert result[-1].end == len(text)


def test_punctuation_counts_as_words_the_way_the_model_counts_them() -> None:
    # One whitespace-separated word, seven model words: juan . perez @ example . com
    assert len(windows("juan.perez@example.com", window_words=7, overlap_words=0)) == 1
    assert len(windows("juan.perez@example.com", window_words=6, overlap_words=0)) == 2


def test_hyphenated_and_underscored_words_count_once() -> None:
    text = "María-José O'Connor snake_case_name"
    # María-José, O, ', Connor, snake_case_name
    assert len(windows(text, window_words=5, overlap_words=0)) == 1
    assert len(windows(text, window_words=4, overlap_words=0)) == 2


def test_dense_csv_text_is_cut_into_windows_the_model_will_not_truncate() -> None:
    # 206 whitespace-separated words but 1,408 model words: one whitespace window would be
    # truncated by the model at 384 words and lose the names at the end.
    text = " ".join(["row,1,2,3"] * 200) + " Contact: Alice Johnson at ACME Corporation."
    words = _words(text)
    result = windows(text, window_words=250, overlap_words=50)
    assert len(result) > 1
    assert all(len(_words_in(window, words)) <= 250 for window in result)
    assert "Alice Johnson at ACME Corporation" in text[result[-1].start : result[-1].end]


@pytest.mark.parametrize(
    ("window_words", "overlap_words"),
    [(0, 0), (10, 10), (10, 11), (10, -1), (-1, 0)],
    ids=["zero-window", "overlap-equal", "overlap-larger", "negative-overlap", "negative-window"],
)
def test_invalid_parameters_raise(window_words: int, overlap_words: int) -> None:
    with pytest.raises(ValueError, match="overlap_words"):
        windows("some text", window_words=window_words, overlap_words=overlap_words)


@st.composite
def _cases(draw: st.DrawFn) -> tuple[str, int, int]:
    alphabet = st.sampled_from("ab_-.,'@ \n\t") | st.characters(codec="utf-8")
    text = draw(st.text(alphabet, max_size=300))
    window_words = draw(st.integers(min_value=1, max_value=12))
    overlap_words = draw(st.integers(min_value=0, max_value=window_words - 1))
    return text, window_words, overlap_words


@given(_cases())
def test_property_windows_cover_every_non_whitespace_character_on_word_boundaries(
    case: tuple[str, int, int],
) -> None:
    text, window_words, overlap_words = case
    words = _words(text)
    result = windows(text, window_words=window_words, overlap_words=overlap_words)
    covered = [False] * len(text)
    for window in result:
        for index in range(window.start, window.end):
            covered[index] = True
    assert all(covered[index] for index, char in enumerate(text) if not char.isspace())
    starts = {start for start, _ in words}
    ends = {end for _, end in words}
    for window in result:
        assert window.start in starts
        assert window.end in ends
        for start, end in words:
            # No word is cut: it is inside the window or entirely outside it.
            outside = end <= window.start or start >= window.end
            assert outside or (start >= window.start and end <= window.end)
        assert 1 <= len(_words_in(window, words)) <= window_words


@given(_cases())
def test_property_consecutive_windows_share_exactly_overlap_words_words(
    case: tuple[str, int, int],
) -> None:
    text, window_words, overlap_words = case
    words = _words(text)
    result = windows(text, window_words=window_words, overlap_words=overlap_words)
    inside = [_words_in(window, words) for window in result]
    for indices in inside:
        assert indices == list(range(indices[0], indices[-1] + 1))
    for previous, current in pairwise(inside):
        assert len(previous) == window_words
        assert len(set(previous) & set(current)) == overlap_words
        assert current[-1] > previous[-1]
    if inside:
        assert inside[0][0] == 0
        assert inside[-1][-1] == len(words) - 1
    else:
        assert words == []
