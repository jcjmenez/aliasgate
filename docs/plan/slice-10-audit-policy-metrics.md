# Slice 10: leak audit, metrics, security invariants

## Goal

The gateway checks its own work before anything leaves the network, exposes metrics an operator
can alert on, and every security invariant in the threat model has an automated test. After
this slice the gateway is worth deploying.

## Depends on

Slice 08.

## Dependencies to add

```bash
uv add prometheus-client
```

## Files

| File | Action |
|---|---|
| `src/aliasgate/audit.py` | create |
| `src/aliasgate/metrics.py` | create |
| `src/aliasgate/proxy/app.py` | change: call the audit, record metrics, serve `/metrics` |
| `src/aliasgate/config.py` | change: `metrics.enabled` (default true), `policy.leak_audit` (default true) |
| `tests/test_audit.py`, `tests/proxy/test_metrics.py`, `tests/proxy/test_invariants.py` | create |

## Specification

### `audit.py`

```python
@dataclass(frozen=True, slots=True)
class AuditResult:
    leaked_entities: int           # number of entities found, never which ones
    leaked_types: tuple[str, ...]  # their entity types, sorted, deduplicated

def audit_outbound(payload: Any, mapping: Mapping, *, min_length: int) -> AuditResult:
    """Search every string value of the outbound JSON payload for the entities of the mapping."""
```

- Walk **every** string value in the whole outbound body, not only the slots. This is the point
  of the audit: it catches a field the dialect forgot. Keys are not checked. (**Changed in
  [slice 21](slice-21-object-keys.md):** every key is searched as well, the same two ways.)
- For each non-`LITERAL` entity whose key length is ≥ `min_length`, search with
  `build_term_pattern(entity.surface)`.
- Known blind spot (found in slices 01 to 04): entity keys use `normalize_key` (full
  casefold), but `build_term_pattern` uses `re.IGNORECASE` (simple case folding). For the
  entity `Straße`, the pattern does not match `STRASSE`, so neither propagation nor this audit
  catches that variant. The reverse also happens: the pattern for `Ivan` matches `İvan` and
  `ıvan`, whose keys differ. Decide whether to accept this or to fold both sides the same way,
  and add a test that pins the decision.
- There are no excluded fields.
- Tool names, parameter names and `enum` values are
  not pseudonymised (architecture §6.1), so a customer name used as an enum value would trip the
  audit. That is the correct outcome: the request is rejected and the operator learns that tool
  definitions carry sensitive values.

Proxy integration, between steps 6 and 7 of the request flow: if `policy.leak_audit` and the
result has `leaked_entities > 0` → respond `500`, code `leak_audit_failed`, do not forward,
increment the metric with the entity types as labels. Log the count and types only.

The audit costs one regex search per entity per string. Measure it on a 100-message synthetic
conversation with 200 entities and put the number in the pull request. If it exceeds 50 ms,
combine the entity patterns into one alternation (longest first), as the dictionary detector
does.

### `metrics.py`

Use a dedicated `CollectorRegistry` created in `create_app` (not the global default registry),
so tests and multiple app instances do not collide.

| Metric | Type | Labels |
|---|---|---|
| `aliasgate_requests_total` | counter | `route`, `status_class` (`2xx`, `4xx`, `5xx`), `outcome` (`ok`, `rejected`, `upstream_error`, `gateway_error`) |
| `aliasgate_entities_total` | counter | `entity_type`, `source` (detector name or `propagation`) |
| `aliasgate_unresolved_placeholders_total` | counter | `route` |
| `aliasgate_leak_audit_failures_total` | counter | `route`, `entity_type` |
| `aliasgate_rejections_total` | counter | `route`, `code` |
| `aliasgate_detection_seconds` | histogram | `detector` |
| `aliasgate_request_overhead_seconds` | histogram | `route`, `phase` (`pseudonymize`, `restore`) |
| `aliasgate_detection_cache_total` | counter | `result` (`hit`, `miss`) |

Label values are a closed set chosen by the gateway (routes, entity types from configuration,
detector names, fixed codes). **Never use request-derived text as a label value.**

(Found in slice 09: `CachedDetector.stats()` returns cumulative `(hits, misses)` and is the
source for `aliasgate_detection_cache_total`. A failed detection counts as a miss. The factory
wraps only the ML detector, so the cache counter exists only with ML on. The warm-up at start-up
calls the inner detector directly, not through the cache.)

To report `source` and per-detector timings, the engine needs to expose them. Extend
`PseudonymizedBatch` with a `stats` field:

```python
@dataclass(frozen=True, slots=True)
class BatchStats:
    entities_by_type_and_source: Mapping[tuple[str, str], int]
    detector_seconds: Mapping[str, float]
```

This is a permitted contract change; update architecture §4.1 in the same pull request.

`GET /metrics` serves the registry in Prometheus text format when `metrics.enabled`. It is
served on the same port; the usage documentation (slice 12) tells operators not to expose it
publicly.

### Response header

Add `x-aliasgate-entities: <total count>` to gateway responses so client developers can see that
pseudonymisation happened without reading logs. A count only.

### Invariant tests

`tests/proxy/test_invariants.py` has one test per security invariant in
[threat-model.md](../threat-model.md), named after it:

1. `test_no_request_is_forwarded_when_pseudonymisation_fails`: parametrised over every failure
   injected at every step before forwarding, for both dialects; assert zero upstream calls.
2. `test_no_detected_surface_reaches_the_upstream`: property test. Generate conversations from
   templates filled with synthetic entities (names from the dictionary, emails, IBANs) in
   message text, tool arguments and tool results, for both dialects; capture the upstream body
   and assert no entity pattern matches anywhere in its serialised form.
3. `test_mapping_is_never_serialised`: assert `Mapping` and `Entity` have no `to_json`,
   `model_dump`, `__getstate__` returning surfaces, and that `pickle.dumps(mapping)` raises
   (implement `__reduce__` to raise `TypeError` in `Mapping`). Note (found in slice 01):
   `Entity` is a frozen, slotted dataclass, so `dataclasses` already gives it a `__getstate__`
   that returns all its fields, surface included. Block `Entity` too (for example a
   `__reduce__` that raises), or this test fails as written.
4. `test_logs_and_metrics_contain_no_text`: extend the logging hygiene test of slice 06 to both
   dialects, streaming included, and assert that the `/metrics` output contains none of the
   synthetic values. (Found in slice 07: `tests/proxy/test_app_stream.py` already checks the
   log records of an OpenAI stream. The unresolved count comes from `transformer.unresolved`.) (Found in slice 06: the test's own httpx client logs the URLs the test
   calls. `_gateway_records` drops exactly those records; keep the filter that narrow.)
5. `test_unknown_endpoints_and_content_are_rejected_by_default`.
6. `test_restoration_uses_only_the_request_mapping`: two concurrent requests where request A's
   upstream answer contains request B's placeholder numbers that do not exist in A; A's response
   leaves them unresolved.

### Decisions made while implementing (slice 10)

- Checked on 2026-11-20 in the installed sources of `prometheus-client` 0.26.0 (PyPI,
  2026-07-24, licence Apache-2.0 AND BSD-2-Clause, no dependencies): `CollectorRegistry`,
  `Counter`, `Histogram`, `generate_latest`, `CONTENT_TYPE_LATEST`
  (`text/plain; version=1.0.0; charset=utf-8`) and `CounterMetricFamily` for a custom
  collector. Counters are declared without `_total`, and the client adds it. Every counter also
  exposes a `_created` timestamp sample. If `PROMETHEUS_MULTIPROC_DIR` is set, the client
  writes metric values to files in that directory. The values and labels hold no text.
- Folding, decided with the maintainer: the audit searches both ways. An entity is found when
  the term pattern of propagation matches the string, or when its key matches the string after
  NFKC normalisation and case folding. Tests pin both cases. `STRASSE` for `Straße`, a
  decomposed `é` and full-width `ＡＣＭＥ` share the entity's key, but the pattern misses them,
  and so does propagation, so these requests are now rejected instead of forwarded. `İvan` and
  `ıvan` for `Ivan` are found by the pattern. Propagation that folds the same way is
  backlog §16.
- `audit_outbound` takes `payload: object`, not `Any`, because AGENTS.md allows no `Any` in core
  modules. It walks values iteratively, so nesting depth is no limit. It runs in a worker
  thread. If the audit itself fails, the request gets 500 `internal_error`, logged with stage
  `audit`, and is not forwarded. `min_length` is `detection.min_propagation_length`.
- Audit latency, measured on an Apple M5 Pro: a conversation of 100 messages, about 40,000
  characters, with 200 entities. Searching each entity on its own took 112.6 ms, over the
  50 ms limit. One alternation per side (term patterns, folded keys), longest first, takes
  1.7 ms (median of 7). The audit searches each entity on its own only after the alternation
  found something, to count the leak. A property test checks that the result equals
  searching each entity on its own.
- Every string value of the body is audited, as specified, and that has consequences:
  - A detected value in a field the gateway forwards unchanged now rejects the request.
    These fields are OpenAI `metadata` (decided in slice 05), Anthropic `metadata` keys other
    than `user_id`, tool names, `enum` values, ids and `model`.
  - With `propagate: false`, a repeated entity that no detector finds the second time
    rejects the request.
  - Thinking blocks are string values too, so they are audited.
- Outcome: every `GatewayError` carries its outcome.
  - `rejected`: `invalid_json`, `request_too_large`, `unknown_endpoint`,
    `unsupported_content`, `malformed_request`, `leak_audit_failed`.
  - `upstream_error`: `upstream_timeout`, `upstream_unreachable`, and any error status the
    provider answered.
  - `gateway_error`: `pseudonymization_failed`, `restoration_failed`, `internal_error`.
  - Streams: a read error mid-stream is `upstream_error`. A transformer or send failure is
    `gateway_error`. A client that goes away is `ok`.
  - `rejections_total` counts every error response the gateway produced, by its code.
- `entities_total` counts replaced occurrences, so that `propagation` can be a source. The log
  line still counts unique entities by type. `BatchStats` counts by the entity's type (that of
  its first occurrence) and the span's source.
- `x-aliasgate-entities` is on every response. It is the number of entities in the request's
  mapping, `LITERAL` ones included, and 0 when nothing was pseudonymised. `GET /metrics` is
  counted like any request, under route `GET /metrics`. With metrics off, it answers 404
  `unknown_endpoint`.
- Overhead phases:
  - `pseudonymize`: `prepare_request`, the engine, writing the slots and the audit.
  - `restore`: restoring a non-streamed body, or the time spent in the stream transformer.
  - A phase that does not complete records nothing.
- The cache metric is a collector that reads `CachedDetector.stats()` at scrape time. To find
  the caches, `Pseudonymizer` gained a read-only `detectors` property (architecture §4.1).
- `Mapping` and `Entity` raise `TypeError` from `__getstate__` and `__reduce__`, so they cannot
  be pickled or copied either. For `Entity`, `__getstate__` alone already blocks both: a
  mutation check that removed its `__reduce__` passed. `__reduce__` is kept, because this slice
  asks for it.
- `terms.term_body` exposes the body of `build_term_pattern`, so that the audit's alternation
  uses exactly the propagation pattern. The log-hygiene helpers moved to
  `tests/proxy/hygiene.py`, shared by the slice 06 tests and the invariant tests.
- The invariant tests cover 11 failures per dialect before forwarding: too large, invalid
  JSON, not an object, malformed, unsupported content, `prepare_request` raising, a detector
  raising, `apply` raising, slots never written (caught by the audit), the audit raising, and
  an unencodable lone surrogate. The surface property test passed a stress run of 1,000
  conversations, and the audit property tests passed 20,000 examples each.

## Tests (other)

`test_audit.py`
- clean payload → zero.
- a surface left in a non-slot field (simulate a forgotten field) → counted, with its type.
- case-insensitive and whitespace-flexible match; whole-term only (`Ana` is not found in
  `Banana`).
- entities shorter than `min_length` and `LITERAL` entities are skipped.
- `AuditResult` and its `repr` contain no surface.

`test_metrics.py`
- after one happy-path request: `requests_total` has one `2xx/ok` sample;
  `entities_total{entity_type="EMAIL",source="rules"}` is 1.
- a rejected request increments `rejections_total{code="unsupported_content"}`.
- an unresolved placeholder increments its counter.
- leak audit failure (inject a dialect that "forgets" a field) → 500, zero upstream calls,
  counter incremented.
- `metrics.enabled: false` → `/metrics` is 404.
- two apps in one process have independent registries.

## Acceptance checklist

- [ ] Six invariant tests exist, named as above, and pass.
- [ ] The audit walks the whole outbound body, proven by the forgotten-field test.
- [ ] No metric label can carry request-derived text (review every `.labels(...)` call and list
      them in the pull request).
- [ ] Audit latency measured and reported.
- [ ] Definition of done in `docs/plan/README.md` is met.

## Out of scope

- OpenTelemetry tracing (backlog).
- An audit log of who sent what. The gateway does not know users and must not store content.
- Alerting rules and dashboards (can be contributed as examples later).
