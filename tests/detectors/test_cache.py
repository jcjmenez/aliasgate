import hashlib
import threading
from collections.abc import Iterator

import pytest

from aliasgate.detectors.cache import CachedDetector
from aliasgate.types import PRIORITY_ML, Span

TEXTS = ["Juan Pérez called.", "Alice Johnson wrote.", "ACME Corporation replied."]


class CountingDetector:
    """Reports the whole text as one PERSON span and records every text it is asked about."""

    name = "counting"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def detect(self, text: str) -> list[Span]:
        self.calls.append(text)
        return [Span(0, len(text), "PERSON", 0.9, self.name, PRIORITY_ML)]


class FlakyDetector:
    """Raises on its first call, then works."""

    name = "flaky"

    def __init__(self) -> None:
        self.calls = 0

    def detect(self, text: str) -> list[Span]:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("model unavailable")
        return [Span(0, 1, "PERSON", 0.9, self.name, PRIORITY_ML)]


def _cache(
    inner: CountingDetector, max_entries: int = 16, fingerprint: str = "fp"
) -> CachedDetector:
    return CachedDetector(inner, fingerprint=fingerprint, max_entries=max_entries)


def test_second_call_with_the_same_text_hits_the_cache() -> None:
    inner = CountingDetector()
    cache = _cache(inner)
    first = cache.detect(TEXTS[0])
    second = cache.detect(TEXTS[0])
    assert first == second == [Span(0, len(TEXTS[0]), "PERSON", 0.9, "counting", PRIORITY_ML)]
    assert inner.calls == [TEXTS[0]]
    assert cache.stats() == (1, 1)


def test_different_texts_are_different_entries() -> None:
    inner = CountingDetector()
    cache = _cache(inner)
    for text in TEXTS:
        cache.detect(text)
    assert inner.calls == TEXTS
    assert cache.stats() == (0, 3)


def test_the_key_is_the_sha256_of_fingerprint_nul_and_text() -> None:
    cache = _cache(CountingDetector(), fingerprint="gliner-config")
    cache.detect(TEXTS[0])
    expected = hashlib.sha256(("gliner-config" + "\x00" + TEXTS[0]).encode()).digest()
    assert list(cache._entries) == [expected]


def test_a_different_fingerprint_gives_a_different_entry() -> None:
    first = _cache(CountingDetector(), fingerprint="threshold=0.4")
    second = _cache(CountingDetector(), fingerprint="threshold=0.5")
    first.detect(TEXTS[0])
    second.detect(TEXTS[0])
    assert set(first._entries).isdisjoint(second._entries)


def test_eviction_order_is_least_recently_used() -> None:
    inner = CountingDetector()
    cache = _cache(inner, max_entries=2)
    a, b, c = TEXTS
    cache.detect(a)
    cache.detect(b)
    cache.detect(a)  # a is now the most recently used
    cache.detect(c)  # evicts b
    inner.calls.clear()
    cache.detect(a)
    cache.detect(c)
    assert inner.calls == []
    cache.detect(b)
    assert inner.calls == [b]
    assert len(cache._entries) == 2


def test_returned_lists_are_independent() -> None:
    cache = _cache(CountingDetector())
    first = cache.detect(TEXTS[0])
    first.clear()
    second = cache.detect(TEXTS[0])
    assert len(second) == 1
    second.append(Span(0, 1, "ORG", 0.5, "other", PRIORITY_ML))
    assert len(cache.detect(TEXTS[0])) == 1


def test_exceptions_are_not_cached() -> None:
    inner = FlakyDetector()
    cache = CachedDetector(inner, fingerprint="fp", max_entries=16)
    with pytest.raises(RuntimeError, match="model unavailable"):
        cache.detect(TEXTS[0])
    assert len(cache._entries) == 0
    assert cache.detect(TEXTS[0]) == [Span(0, 1, "PERSON", 0.9, "flaky", PRIORITY_ML)]
    assert inner.calls == 2
    assert cache.stats() == (0, 2)


def test_zero_entries_disables_caching() -> None:
    inner = CountingDetector()
    cache = _cache(inner, max_entries=0)
    cache.detect(TEXTS[0])
    cache.detect(TEXTS[0])
    assert inner.calls == [TEXTS[0], TEXTS[0]]
    assert len(cache._entries) == 0
    assert cache.stats() == (0, 2)


def test_negative_max_entries_raises() -> None:
    with pytest.raises(ValueError, match="max_entries"):
        _cache(CountingDetector(), max_entries=-1)


def test_the_name_is_the_inner_detectors_name() -> None:
    assert _cache(CountingDetector()).name == "counting"


def _strings(value: object, seen: set[int]) -> Iterator[str | bytes]:
    """Every str and bytes reachable from `value` through containers and object attributes."""
    if id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, str | bytes):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key, seen)
            yield from _strings(item, seen)
    elif isinstance(value, list | tuple | set | frozenset):
        for item in value:
            yield from _strings(item, seen)
    elif isinstance(value, Span):
        yield from _strings(value.entity_type, seen)
        yield from _strings(value.source, seen)
    elif hasattr(value, "__dict__"):
        yield from _strings(vars(value), seen)


def test_the_cache_holds_no_input_text() -> None:
    cache = _cache(CountingDetector())
    for text in TEXTS:
        cache.detect(text)
    state = {name: value for name, value in vars(cache).items() if name != "_inner"}
    found = list(_strings(state, set()))
    assert found, "the walk must reach the cache's state"
    for text in TEXTS:
        for value in found:
            if isinstance(value, str):
                assert text not in value
            else:
                assert text.encode() not in value
    assert all(isinstance(key, bytes) and len(key) == 32 for key in cache._entries)
    assert all(isinstance(spans, tuple) for spans in cache._entries.values())


def test_texts_with_lone_surrogates_are_cached_and_kept_apart() -> None:
    # JSON allows unpaired surrogate escapes, so request text can contain them.
    pair = chr(0xD83D) + chr(0xDE00)
    emoji = chr(0x1F600)
    inner = CountingDetector()
    cache = _cache(inner)
    cache.detect(pair)
    cache.detect(emoji)
    cache.detect(pair)
    assert inner.calls == [pair, emoji]
    assert cache.stats() == (1, 2)


def test_concurrent_use_keeps_counts_and_results_consistent() -> None:
    inner = CountingDetector()
    cache = _cache(inner, max_entries=2)
    errors: list[BaseException] = []

    def work() -> None:
        try:
            for round_ in range(200):
                text = TEXTS[round_ % len(TEXTS)]
                assert cache.detect(text)[0].end == len(text)
        except BaseException as exc:  # pragma: no cover - only reached when the test fails
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    hits, misses = cache.stats()
    assert hits + misses == 8 * 200
    assert misses == len(inner.calls)
    assert len(cache._entries) <= 2
