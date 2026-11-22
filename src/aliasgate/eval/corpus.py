"""Labelled corpora in JSON Lines: one example per line, gold spans as Python string offsets.

    {"id": "en-0001", "lang": "en", "text": "...",
     "spans": [{"start": 10, "end": 20, "type": "PERSON", "entity": "p1"}]}

Each example is one line of the file. Offsets count code points, as Python slices do. The
optional `entity` names the entity a span mentions: spans of one example with the same id are
mentions of one entity. Errors name the file, the line, the example id and span numbers, never
the text or an entity id, which may be made from the value.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from aliasgate.types import validate_entity_type

_EXAMPLE_KEYS: Final = frozenset({"id", "lang", "text", "spans"})
_SPAN_KEYS: Final = frozenset({"start", "end", "type"})
_OPTIONAL_SPAN_KEYS: Final = frozenset({"entity"})
_ENTITY_ID: Final = re.compile(r"[A-Za-z0-9_.\-]{1,64}")


@dataclass(frozen=True, slots=True)
class GoldSpan:
    """A labelled range of one example: Python string offsets, an entity type and, optionally,
    the id of the entity it mentions, scoped to its example."""

    start: int
    end: int
    entity_type: str
    entity: str | None = None


@dataclass(frozen=True, slots=True)
class Example:
    """One labelled text; its spans are in range, sorted, non-overlapping and non-empty."""

    id: str
    lang: str
    text: str
    spans: tuple[GoldSpan, ...]


class CorpusError(ValueError):
    """A corpus file is malformed. The message gives a position, never text."""


def load_corpus(path: Path) -> list[Example]:
    """Validate offsets (in range, non-overlapping, sorted) and entity types; raise on error.

    Blank lines are skipped. Example ids must be unique within the file.
    """
    examples: list[Example] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            example = _example(f"{path}, line {number}", line)
            if example.id in seen:
                raise CorpusError(f"{path}, line {number}, example {example.id!r}: duplicate id")
            seen.add(example.id)
            examples.append(example)
    return examples


def _example(where: str, line: str) -> Example:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        raise CorpusError(f"{where}: not a JSON object")
    if set(data) != _EXAMPLE_KEYS:
        raise CorpusError(f"{where}: an example has exactly the keys id, lang, text and spans")
    example_id, lang, text, spans = data["id"], data["lang"], data["text"], data["spans"]
    if not isinstance(example_id, str) or not example_id:
        raise CorpusError(f"{where}: 'id' must be a non-empty string")
    where = f"{where}, example {example_id!r}"
    if not isinstance(lang, str) or not lang:
        raise CorpusError(f"{where}: 'lang' must be a non-empty string")
    if not isinstance(text, str):
        raise CorpusError(f"{where}: 'text' must be a string")
    if not isinstance(spans, list):
        raise CorpusError(f"{where}: 'spans' must be a list")
    gold: list[GoldSpan] = []
    first_of: dict[str, int] = {}  # entity id -> number of its first span
    for index, span in enumerate(spans, start=1):
        gold.append(_span(f"{where}: span {index}", span, len(text)))
        if index > 1 and gold[-1].start < gold[-2].end:
            raise CorpusError(f"{where}: span {index} overlaps or precedes span {index - 1}")
        entity = gold[-1].entity
        if entity is not None:
            first = first_of.setdefault(entity, index)
            if gold[first - 1].entity_type != gold[-1].entity_type:
                raise CorpusError(
                    f"{where}: span {index} has the entity id of span {first} with another type"
                )
    return Example(example_id, lang, text, tuple(gold))


def _span(where: str, span: object, length: int) -> GoldSpan:
    if (
        not isinstance(span, dict)
        or not _SPAN_KEYS <= set(span) <= _SPAN_KEYS | _OPTIONAL_SPAN_KEYS
    ):
        raise CorpusError(
            f"{where} must be an object with start, end and type, and optionally entity"
        )
    start, end, entity_type = span["start"], span["end"], span["type"]
    # bool is an int subclass; true and false are not offsets.
    if any(not isinstance(value, int) or isinstance(value, bool) for value in (start, end)):
        raise CorpusError(f"{where} offsets must be integers")
    if not 0 <= start < end <= length:
        raise CorpusError(f"{where} is out of range (text length {length})")
    try:
        if not isinstance(entity_type, str):
            raise ValueError
        validate_entity_type(entity_type)
    except ValueError:
        raise CorpusError(f"{where} has an invalid entity type") from None
    entity = span.get("entity")
    if "entity" in span and (not isinstance(entity, str) or not _ENTITY_ID.fullmatch(entity)):
        raise CorpusError(f"{where} has an invalid entity id")
    return GoldSpan(start, end, entity_type, entity)
