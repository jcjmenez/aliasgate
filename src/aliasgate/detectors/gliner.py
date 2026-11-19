"""ML detector (architecture §3.2, priority 40): a local GLiNER model, from the optional extra.

The model is injected through `EntityModel`, so this module never imports `gliner` (or torch)
unless `load_gliner` is called.
"""

import importlib
import json
import math
import threading
from collections.abc import Callable, Iterator, Mapping
from typing import Final, Protocol, TypeGuard

from aliasgate.detectors.chunking import Window, windows
from aliasgate.types import PRIORITY_ML, Span, validate_entity_type

_SOURCE: Final = "gliner"
_MIN_SPAN_LENGTH: Final = 2
_INSTALL_HINT: Final = (
    "the ML detector needs the optional 'ml' extra: install it with "
    "pip install 'aliasgate[ml]' (or uv sync --extra ml in a checkout)"
)


class EntityModel(Protocol):
    """A named-entity model: character offsets into `text`, labels from `labels`."""

    def predict_entities(
        self, text: str, labels: list[str], threshold: float
    ) -> list[dict[str, object]]: ...


class GlinerDetector:
    """Finds people, organisations, addresses and other configured labels with a local model.

    Long texts are cut into overlapping windows of the model's own words. Results are mapped
    from model labels to entity types, trimmed of surrounding whitespace, deduplicated across
    windows (the highest score wins), and returned sorted by position. A malformed model result
    raises; errors name positions and types, never text. One inference runs at a time.
    """

    name = _SOURCE

    def __init__(
        self,
        model: EntityModel,
        *,
        model_id: str,
        revision: str | None = None,
        labels: Mapping[str, str],
        threshold: float,
        window_words: int = 250,
        overlap_words: int = 50,
    ) -> None:
        if not labels or not all(labels):
            raise ValueError("labels must map at least one non-empty model label")
        for entity_type in labels.values():
            validate_entity_type(entity_type)
        if not 0.0 <= threshold <= 1.0:  # also rejects NaN
            raise ValueError("threshold must be between 0 and 1")
        windows("", window_words=window_words, overlap_words=overlap_words)  # validates both
        self._model = model
        self._labels = dict(labels)
        self._label_names = list(labels)
        self._threshold = threshold
        self._window_words = window_words
        self._overlap_words = overlap_words
        self._lock = threading.Lock()
        self._fingerprint = json.dumps(
            {
                "detector": _SOURCE,
                "model": model_id,
                "revision": revision,
                "threshold": threshold,
                "labels": sorted(self._labels.items()),
                "window_words": window_words,
                "overlap_words": overlap_words,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def fingerprint(self) -> str:
        """Identifies the model and every setting that changes the output; used as cache key."""
        return self._fingerprint

    def detect(self, text: str) -> list[Span]:
        """Spans of priority 40 and source "gliner"; overlapping spans are left to the resolver."""
        best: dict[tuple[int, int, str], Span] = {}
        for window in windows(
            text, window_words=self._window_words, overlap_words=self._overlap_words
        ):
            chunk = text[window.start : window.end]
            # Inference is not guaranteed to be thread-safe: one call at a time.
            with self._lock:
                results = self._model.predict_entities(
                    chunk, list(self._label_names), threshold=self._threshold
                )
            for position, result in enumerate(results, start=1):
                span = self._span(result, position, window, text)
                if span is None:
                    continue
                key = (span.start, span.end, span.entity_type)
                current = best.get(key)
                if current is None or span.score > current.score:
                    best[key] = span
        return sorted(best.values(), key=lambda span: (span.start, span.end, span.entity_type))

    def _span(self, result: object, position: int, window: Window, text: str) -> Span | None:
        where = f"model result {position} of a window"
        if not isinstance(result, Mapping):
            raise ValueError(f"{where} is not a mapping")
        label = result.get("label")
        if not isinstance(label, str) or label not in self._labels:
            return None
        start, end, score = result.get("start"), result.get("end"), result.get("score")
        length = window.end - window.start
        if not (_is_int(start) and _is_int(end) and 0 <= start <= end <= length):
            raise ValueError(f"{where} has offsets outside the window (length {length})")
        if not isinstance(score, int | float) or isinstance(score, bool) or math.isnan(score):
            raise ValueError(f"{where} has a score that is not a number")
        start += window.start
        end += window.start
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if end - start < _MIN_SPAN_LENGTH:
            return None
        clamped = min(max(float(score), 0.0), 1.0)
        return Span(start, end, self._labels[label], clamped, _SOURCE, PRIORITY_ML)


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


class _GlinerLike(Protocol):
    def predict_entities(
        self, text: str, labels: list[str], *, threshold: float
    ) -> list[dict[str, object]]: ...


class _LoadedGliner:
    """A loaded GLiNER model that refuses any text it would truncate.

    GLiNER silently keeps only the first `max_len` of its words (with a warning). The words
    after that would never be scored, so a longer text is an error instead.
    """

    def __init__(
        self,
        model: _GlinerLike,
        splitter: Callable[[str], Iterator[object]],
        max_len: int,
    ) -> None:
        self._model = model
        self._splitter = splitter
        self._max_len = max_len

    def predict_entities(
        self, text: str, labels: list[str], threshold: float
    ) -> list[dict[str, object]]:
        words = sum(1 for _ in self._splitter(text))
        if words > self._max_len:
            raise ValueError(
                f"a window of {words} model words is over the model's limit of {self._max_len};"
                " lower detection.ml.window_words"
            )
        # By keyword: GLiNER's third positional parameter is flat_ner.
        return self._model.predict_entities(text, labels, threshold=threshold)


def load_gliner(model_id: str, revision: str | None) -> EntityModel:
    """Import gliner lazily and load the model. Raise a clear error if the extra is missing.

    The returned model refuses any text longer than the model's input limit instead of
    letting GLiNER truncate it.
    """
    try:
        gliner = importlib.import_module("gliner")
    except ImportError as exc:
        raise RuntimeError(_INSTALL_HINT) from exc
    model = gliner.GLiNER.from_pretrained(model_id, revision=revision)
    try:
        max_len = model.config.max_len
        splitter = model.data_processor.words_splitter
    except AttributeError:
        max_len = splitter = None
    if not _is_int(max_len) or max_len < 1 or not callable(splitter):
        raise RuntimeError(
            "cannot read the model's input limit; this gliner version is not supported"
        )
    return _LoadedGliner(model, splitter, max_len)
