"""Prometheus metrics of one gateway, in a registry of its own.

Every label value is chosen by the gateway: routes, status classes, outcomes, error codes,
detector names and entity types from the configuration. None is ever taken from a request.
"""

from collections.abc import Callable, Iterable, Sequence
from typing import Final, Literal

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from prometheus_client.metrics_core import CounterMetricFamily, Metric
from prometheus_client.registry import Collector

from aliasgate.engine import BatchStats

type Outcome = Literal["ok", "rejected", "upstream_error", "gateway_error"]
type Phase = Literal["pseudonymize", "restore"]

CONTENT_TYPE: Final = CONTENT_TYPE_LATEST
# Rules take well under a millisecond, the ML detector up to seconds.
_BUCKETS: Final = (0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)


class _CacheCollector(Collector):
    """Reads the detection caches' cumulative (hits, misses) at scrape time."""

    def __init__(self, sources: Sequence[Callable[[], tuple[int, int]]]) -> None:
        self._sources = tuple(sources)

    def collect(self) -> Iterable[Metric]:
        counts = [source() for source in self._sources]
        family = CounterMetricFamily(
            "aliasgate_detection_cache", "Detection cache lookups, by result.", labels=["result"]
        )
        family.add_metric(["hit"], sum(hits for hits, _ in counts))
        family.add_metric(["miss"], sum(misses for _, misses in counts))
        yield family


class GatewayMetrics:
    """The gateway's counters and histograms. `cache_stats` are the `stats` methods of the
    detection caches; without any, there is no cache metric."""

    def __init__(self, cache_stats: Sequence[Callable[[], tuple[int, int]]] = ()) -> None:
        self.registry = CollectorRegistry()
        registry = self.registry
        self._requests = Counter(
            "aliasgate_requests",
            "Requests answered, by route, status class and outcome.",
            ["route", "status_class", "outcome"],
            registry=registry,
        )
        self._entities = Counter(
            "aliasgate_entities",
            "Occurrences replaced by a placeholder, by entity type and the detector that found "
            "them.",
            ["entity_type", "source"],
            registry=registry,
        )
        self._unresolved = Counter(
            "aliasgate_unresolved_placeholders",
            "Placeholders in responses that are not in the request's mapping.",
            ["route"],
            registry=registry,
        )
        self._leaks = Counter(
            "aliasgate_leak_audit_failures",
            "Requests rejected by the leak audit, by route and leaked entity type.",
            ["route", "entity_type"],
            registry=registry,
        )
        self._rejections = Counter(
            "aliasgate_rejections",
            "Error responses produced by the gateway itself, by route and error code.",
            ["route", "code"],
            registry=registry,
        )
        self._detection = Histogram(
            "aliasgate_detection_seconds",
            "Time each detector spent on one request.",
            ["detector"],
            registry=registry,
            buckets=_BUCKETS,
        )
        self._overhead = Histogram(
            "aliasgate_request_overhead_seconds",
            "Time the gateway added to one request, by phase.",
            ["route", "phase"],
            registry=registry,
            buckets=_BUCKETS,
        )
        if cache_stats:
            registry.register(_CacheCollector(cache_stats))

    def request(self, route: str, status: int, outcome: Outcome) -> None:
        """One answered request."""
        self._requests.labels(route, f"{status // 100}xx", outcome).inc()

    def rejection(self, route: str, code: str) -> None:
        """One error response the gateway produced, with its fixed error code."""
        self._rejections.labels(route, code).inc()

    def batch(self, stats: BatchStats) -> None:
        """The replaced occurrences and detector timings of one pseudonymised request."""
        for (entity_type, source), count in stats.entities_by_type_and_source.items():
            self._entities.labels(entity_type, source).inc(count)
        for detector, seconds in stats.detector_seconds.items():
            self._detection.labels(detector).observe(seconds)

    def unresolved(self, route: str, count: int) -> None:
        """Placeholders left unresolved in one response."""
        self._unresolved.labels(route).inc(count)

    def leak(self, route: str, entity_types: Iterable[str]) -> None:
        """One request rejected by the leak audit, once per leaked entity type."""
        for entity_type in entity_types:
            self._leaks.labels(route, entity_type).inc()

    def overhead(self, route: str, phase: Phase, seconds: float) -> None:
        """Time the gateway spent in one phase of one request."""
        self._overhead.labels(route, phase).observe(seconds)

    def render(self) -> bytes:
        """The registry in Prometheus text format, served with CONTENT_TYPE."""
        return generate_latest(self.registry)
