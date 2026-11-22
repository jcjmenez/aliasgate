"""Name tokens: the words of a person's surface that can name the person on their own."""

import unicodedata
from typing import Final

from aliasgate.mapping import normalize_key

# Keys, as `normalize_key` makes them: NFKC turns the title "d" + U+00AA into "da".
TITLES: Final[frozenset[str]] = frozenset(
    normalize_key(title)
    for title in (
        "mr", "mrs", "ms", "miss", "mx", "dr", "prof", "sir", "dame", "lady", "lord", "sr",
        "sra", "srta", "sres", "sras", "don", "doña", "d", "d\u00aa", "dña", "dra", "señor",
        "señora", "señorita", "lic", "lcdo", "lcda", "ing",
    )
)  # fmt: skip
PARTICLES: Final[frozenset[str]] = frozenset(
    (
        "de", "del", "la", "las", "los", "y", "e", "da", "das", "do", "dos", "di", "du", "van",
        "von", "der", "den", "ter", "le", "el", "al", "bin", "ibn", "san", "santa",
    )
)  # fmt: skip


def name_tokens(surface: str, *, min_length: int) -> tuple[str, ...]:
    """The keys of the words of a person's surface that can name the person on their own, in
    order: no title, particle or initial, and no key shorter than `min_length`."""
    return tuple(key for key, _ in name_words(surface, min_length=min_length))


def name_words(surface: str, *, min_length: int) -> tuple[tuple[str, str], ...]:
    """The name tokens of `surface` as pairs of key and word as written, without the
    punctuation at its ends, in order (see `name_tokens`)."""
    words = []
    for written in surface.split():
        word = _strip_punctuation(written)
        key = normalize_key(word)
        undotted = key.removesuffix(".")
        if (
            not key
            or undotted in TITLES
            or key in PARTICLES
            or (len(undotted) == 1 and undotted.isalpha())
            or len(key) < min_length
        ):
            continue
        words.append((key, word))
    return tuple(words)


def _strip_punctuation(word: str) -> str:
    """The word without the characters of a Unicode punctuation category at its ends."""
    start, end = 0, len(word)
    while start < end and unicodedata.category(word[start]).startswith("P"):
        start += 1
    while end > start and unicodedata.category(word[end - 1]).startswith("P"):
        end -= 1
    return word[start:end]
