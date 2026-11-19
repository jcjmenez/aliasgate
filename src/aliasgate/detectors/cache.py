"""Detection cache (architecture §4.5): conversations resend their history on every turn."""

import hashlib
import threading
from collections import OrderedDict

from aliasgate.types import Detector, Span


class CachedDetector:
    """Wraps a detector with a bounded, thread-safe LRU cache of its results.

    Entries are keyed by `sha256(fingerprint + "\\x00" + text)` and hold spans only, never
    text, in process memory only. Failures of the inner detector are not cached, and every
    call returns a new list. `max_entries == 0` disables caching.
    """

    def __init__(self, inner: Detector, *, fingerprint: str, max_entries: int) -> None:
        if max_entries < 0:
            raise ValueError("max_entries must not be negative")
        self.name: str = inner.name
        self._inner = inner
        self._fingerprint = fingerprint
        self._max_entries = max_entries
        self._entries: OrderedDict[bytes, tuple[Span, ...]] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def detect(self, text: str) -> list[Span]:
        """The inner detector's spans for `text`, from the cache when it has seen `text`."""
        # "surrogatepass" keeps the encoding injective for texts with unpaired surrogates,
        # which JSON allows; plain UTF-8 would refuse them.
        key = hashlib.sha256(
            (self._fingerprint + "\x00" + text).encode("utf-8", "surrogatepass")
        ).digest()
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                self._hits += 1
                return list(cached)
            self._misses += 1
        # Outside the lock, so that a slow detection does not hold up cache hits.
        spans = tuple(self._inner.detect(text))
        if self._max_entries:
            with self._lock:
                self._entries[key] = spans
                self._entries.move_to_end(key)
                while len(self._entries) > self._max_entries:
                    self._entries.popitem(last=False)
        return list(spans)

    def stats(self) -> tuple[int, int]:
        """(hits, misses) since construction; a failed detection counts as a miss."""
        with self._lock:
            return self._hits, self._misses
