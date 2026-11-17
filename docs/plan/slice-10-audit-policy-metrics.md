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
  of the audit: it catches a field the dialect forgot. Keys are not checked.
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
   synthetic values. (Found in slice 06: the test's own httpx client logs the URLs the test
   calls. `_gateway_records` drops exactly those records; keep the filter that narrow.)
5. `test_unknown_endpoints_and_content_are_rejected_by_default`.
6. `test_restoration_uses_only_the_request_mapping`: two concurrent requests where request A's
   upstream answer contains request B's placeholder numbers that do not exist in A; A's response
   leaves them unresolved.

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
