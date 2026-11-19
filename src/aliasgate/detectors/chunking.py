"""Overlapping word windows over long texts, for models with a limited input length."""

import re
from dataclasses import dataclass
from typing import Final

# A word as GLiNER counts it (gliner 0.2.30, WhitespaceTokenSplitter): a run of word characters,
# possibly joined by "-" or "_", or any other single non-space character. Counting the model's
# own unit makes `window_words` a bound on what the model sees, so it never truncates a window.
_WORD: Final = re.compile(r"\w+(?:[-_]\w+)*|\S")


@dataclass(frozen=True, slots=True)
class Window:
    """A range of the original text, as Python slice offsets."""

    start: int
    end: int


def windows(text: str, *, window_words: int, overlap_words: int) -> list[Window]:
    """Cover the text with windows of at most `window_words` words, each overlapping the
    previous one by exactly `overlap_words` words. Windows start and end on word boundaries,
    every non-whitespace character is inside at least one window, and a text with at most
    `window_words` words yields one window covering all of them.

    A word is a run of word characters joined by "-" or "_", or any other single non-space
    character, which is the unit GLiNER counts. Text without words yields no window.
    """
    if not 0 <= overlap_words < window_words:
        raise ValueError(
            "window_words and overlap_words must satisfy 0 <= overlap_words < window_words"
        )
    words = [match.span() for match in _WORD.finditer(text)]
    step = window_words - overlap_words
    result: list[Window] = []
    first = 0
    while first < len(words):
        last = min(first + window_words, len(words)) - 1
        result.append(Window(words[first][0], words[last][1]))
        if last == len(words) - 1:
            break
        first += step
    return result
