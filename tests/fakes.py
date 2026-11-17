"""Test doubles for detectors."""

import re
from collections.abc import Mapping

from aliasgate.types import PRIORITY_ML, Span


class WordListDetector:
    """Test detector: reports every whole-word, case-sensitive occurrence of the given words."""

    def __init__(
        self,
        words: Mapping[str, str],
        *,
        name: str = "fake",
        priority: int = PRIORITY_ML,
        score: float = 0.9,
    ) -> None:
        self.name = name
        self._patterns = tuple(
            (re.compile(rf"(?<!\w){re.escape(word)}(?!\w)"), entity_type)
            for word, entity_type in words.items()
        )
        self._priority = priority
        self._score = score

    def detect(self, text: str) -> list[Span]:
        return [
            Span(match.start(), match.end(), entity_type, self._score, self.name, self._priority)
            for pattern, entity_type in self._patterns
            for match in pattern.finditer(text)
        ]


class FailingDetector:
    """Test detector whose detect() always raises RuntimeError."""

    name = "failing"

    def detect(self, text: str) -> list[Span]:
        # The message carries the text on purpose: DetectionError must not repeat it.
        raise RuntimeError(f"cannot process {text!r}")
