"""Core vocabulary: entity types, detection priorities, spans and the detector protocol."""

import re
from dataclasses import dataclass
from typing import Final, Protocol

PRIORITY_LITERAL: Final = 100
PRIORITY_DICTIONARY: Final = 80
PRIORITY_RULE: Final = 60
PRIORITY_ML: Final = 40
PRIORITY_PROPAGATION: Final = 20

MAX_ENTITY_TYPE_LENGTH: Final = 40

PERSON: Final = "PERSON"
ORG: Final = "ORG"
ADDRESS: Final = "ADDRESS"
EMAIL: Final = "EMAIL"
PHONE: Final = "PHONE"
IBAN: Final = "IBAN"
CREDIT_CARD: Final = "CREDIT_CARD"
NATIONAL_ID: Final = "NATIONAL_ID"
TAX_ID: Final = "TAX_ID"
IP_ADDRESS: Final = "IP_ADDRESS"
URL: Final = "URL"
# An entity type name, not a credential.
SECRET: Final = "SECRET"  # noqa: S105
LITERAL: Final = "LITERAL"

_ENTITY_TYPE: Final = re.compile(r"[A-Z][A-Z_]*")


def validate_entity_type(name: str) -> str:
    """Return `name` if it matches [A-Z][A-Z_]* and is at most 40 chars; else raise ValueError."""
    if len(name) > MAX_ENTITY_TYPE_LENGTH or _ENTITY_TYPE.fullmatch(name) is None:
        # The value itself is not echoed: callers may pass arbitrary text.
        raise ValueError(
            f"entity type must match [A-Z][A-Z_]* and be at most {MAX_ENTITY_TYPE_LENGTH} "
            f"characters (got a string of length {len(name)})"
        )
    return name


@dataclass(frozen=True, slots=True)
class Span:
    """A detected range of one text; construction guarantees valid offsets, score and type."""

    start: int
    end: int
    entity_type: str
    score: float
    source: str
    priority: int

    def __post_init__(self) -> None:
        if self.start < 0:
            raise ValueError(f"span start must be non-negative (got {self.start})")
        if self.end < self.start:
            raise ValueError(f"span end must not precede start (got {self.start}..{self.end})")
        if not 0.0 <= self.score <= 1.0:  # also rejects NaN
            raise ValueError("span score must be between 0 and 1")
        validate_entity_type(self.entity_type)


class Detector(Protocol):
    """Finds spans in a text; the same text always yields the same spans, and failure raises."""

    name: str

    def detect(self, text: str) -> list[Span]: ...
