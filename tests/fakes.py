"""Test doubles for detectors."""

import re
from collections.abc import Mapping

from aliasgate.types import PRIORITY_ML, Span


class WordListDetector:
    """Test detector: reports every whole-word, case-sensitive occurrence of the given words.

    `links` maps some of the words to a link, which their spans carry.
    """

    def __init__(
        self,
        words: Mapping[str, str],
        *,
        name: str = "fake",
        priority: int = PRIORITY_ML,
        score: float = 0.9,
        links: Mapping[str, str] | None = None,
    ) -> None:
        self.name = name
        links = links or {}
        self._patterns = tuple(
            (re.compile(rf"(?<!\w){re.escape(word)}(?!\w)"), entity_type, links.get(word))
            for word, entity_type in words.items()
        )
        self._priority = priority
        self._score = score

    def detect(self, text: str) -> list[Span]:
        return [
            Span(
                match.start(),
                match.end(),
                entity_type,
                self._score,
                self.name,
                self._priority,
                link,
            )
            for pattern, entity_type, link in self._patterns
            for match in pattern.finditer(text)
        ]


class FailingDetector:
    """Test detector whose detect() always raises RuntimeError."""

    name = "failing"

    def detect(self, text: str) -> list[Span]:
        # The message carries the text on purpose: DetectionError must not repeat it.
        raise RuntimeError(f"cannot process {text!r}")
