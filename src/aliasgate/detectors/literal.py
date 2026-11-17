"""Detector for input text that already looks like a placeholder (architecture §4.4)."""

from aliasgate.placeholders import TOLERANT
from aliasgate.types import LITERAL, PRIORITY_LITERAL, Span


class LiteralDetector:
    """Reports placeholder-shaped input so that restoration can never mistake it for a value."""

    name = "literal"

    def detect(self, text: str) -> list[Span]:
        """One span of type LITERAL, priority 100, score 1.0, per match of TOLERANT."""
        return [
            Span(match.start(), match.end(), LITERAL, 1.0, self.name, PRIORITY_LITERAL)
            for match in TOLERANT.finditer(text)
        ]
