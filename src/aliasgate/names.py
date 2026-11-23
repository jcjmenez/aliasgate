"""Name tokens: the words of a person's surface that can name the person on their own."""

import re
import unicodedata
from enum import Enum, auto
from typing import Final

from aliasgate.mapping import normalize_key
from aliasgate.terms import starts_upper

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
        "von", "der", "den", "ter", "le", "el", "al", "bin", "ibn", "san", "santa", "and",
    )
)  # fmt: skip
# The English first name that is also the Spanish title (slice 17).
_DON: Final = "don"
_HYPHENS: Final = re.compile("[-\u2010\u2011]")


class _Kind(Enum):
    TOKEN = auto()
    TITLE = auto()
    INITIAL = auto()
    OTHER = auto()  # empty, a particle, or shorter than the minimum


def name_tokens(surface: str, *, min_length: int) -> tuple[str, ...]:
    """The keys of the words of a person's surface that can name the person on their own, in
    order: no title, particle or initial, and no key shorter than `min_length`."""
    return tuple(key for key, _ in name_words(surface, min_length=min_length))


def name_words(surface: str, *, min_length: int) -> tuple[tuple[str, str], ...]:
    """The name tokens of `surface` as pairs of key and word as written, without the
    punctuation at its ends, in order (see `name_tokens`)."""
    return tuple(
        (key, word) for key, word, kind in _words(surface, min_length) if kind is _Kind.TOKEN
    )


def name_parts(word: str, *, min_length: int) -> tuple[tuple[str, str], ...]:
    """The name parts of a hyphenated name token as pairs of key and part as written, in
    order; empty for a word without a hyphen."""
    parts = []
    for part in _HYPHENS.split(word) if _HYPHENS.search(word) else ():
        key = normalize_key(part)
        if len(key) >= min_length and key.removesuffix(".") not in TITLES and key not in PARTICLES:
            parts.append((key, part))
    return tuple(parts)


def is_person(surface: str, *, min_length: int) -> bool:
    """Whether a PERSON surface names a person: at least two name tokens, one name token with
    a title or an initial, or one name token with at least two name parts."""
    words = _words(surface, min_length)
    tokens = [word for _, word, kind in words if kind is _Kind.TOKEN]
    if len(tokens) != 1:
        return len(tokens) >= 2
    return any(kind in (_Kind.TITLE, _Kind.INITIAL) for _, _, kind in words) or (
        len(name_parts(tokens[0], min_length=min_length)) >= 2
    )


def _words(surface: str, min_length: int) -> list[tuple[str, str, _Kind]]:
    """Each word of `surface` as its key, the word without its end punctuation, and its kind.

    A capitalised `Don` without a final `.` is a name token when the other words give exactly
    one name token, and a title otherwise."""
    words: list[tuple[str, str, _Kind]] = []
    dons: list[int] = []
    for written in surface.split():
        start, end = unpunctuated(written)
        word = written[start:end]
        key = normalize_key(word)
        undotted = key.removesuffix(".")
        if not key:
            kind = _Kind.OTHER
        elif undotted in TITLES:
            kind = _Kind.TITLE
            if key == _DON and "." not in written[end:] and starts_upper(word):
                dons.append(len(words))
        elif key in PARTICLES:
            kind = _Kind.OTHER
        elif len(undotted) == 1 and undotted.isalpha():
            kind = _Kind.INITIAL
        elif len(key) < min_length:
            kind = _Kind.OTHER
        else:
            kind = _Kind.TOKEN
        words.append((key, word, kind))
    # Every `Don` is a title so far, so the other words of each give the tokens counted here.
    if sum(kind is _Kind.TOKEN for _, _, kind in words) == 1 and len(_DON) >= min_length:
        for index in dons:
            key, word, _ = words[index]
            words[index] = (key, word, _Kind.TOKEN)
    return words


def unpunctuated(word: str) -> tuple[int, int]:
    """The start and end of the word without the characters of a Unicode punctuation category
    at its ends."""
    start, end = 0, len(word)
    while start < end and unicodedata.category(word[start]).startswith("P"):
        start += 1
    while end > start and unicodedata.category(word[end - 1]).startswith("P"):
        end -= 1
    return start, end
