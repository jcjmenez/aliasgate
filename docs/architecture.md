# Architecture

This document is the technical contract for the implementation. The slice files in
[plan/](plan/README.md) refer to sections here by number. When a slice file and this document
disagree, this document wins and the slice file must be fixed.

The reasons behind the decisions are in [state-of-the-art.md](state-of-the-art.md).

## 1. Overview

```text
                 ┌──────────────────────────── aliasgate ────────────────────────────┐
 client          │                                                                    │      provider
 ──────  HTTP ──▶│ proxy ─▶ dialect.extract ─▶ engine.pseudonymize ─▶ audit ─▶ httpx ─┼────▶ ────────
                 │                                   │ mapping (memory only)          │
 ◀───── HTTP ────│ proxy ◀─ dialect.restore  ◀───────┘◀───────────────────────────────┼◀────
                 └────────────────────────────────────────────────────────────────────┘
```

Three layers, with dependencies pointing only downwards:

| Layer | Package | Knows about | Must not know about |
|---|---|---|---|
| Core | `aliasgate.types`, `placeholders`, `mapping`, `resolve`, `engine`, `stream`, `audit`, `detectors.*` | Strings and spans (`audit` walks any JSON value, without knowing its shape) | Provider JSON shapes, HTTP |
| Dialects | `aliasgate.dialects.*` | The JSON shape of one provider API | HTTP, sockets |
| Proxy | `aliasgate.proxy.*`, `config`, `cli`, `metrics` | HTTP, configuration, wiring | Detection internals |
| Evaluation | `aliasgate.eval.*` | The core, plus configuration and wiring (`config`, `factory`) to build the engine as the gateway does | HTTP, dialects |

The core has no third-party dependency except `phonenumbers` (and the optional ML extra). This
keeps it usable as a library and as a plugin for other gateways.

## 2. Vocabulary

| Term | Meaning |
|---|---|
| **Entity type** | Upper-case label matching `[A-Z][A-Z_]*` (letters and underscores, no digits). Built-in: `PERSON`, `ORG`, `ADDRESS`, `EMAIL`, `PHONE`, `IBAN`, `CREDIT_CARD`, `NATIONAL_ID`, `TAX_ID`, `IP_ADDRESS`, `URL`, `SECRET`, `LITERAL`. Users may add their own. |
| **Span** | A detected range in one text: `start`, `end` (Python slice offsets), `entity_type`, `score` (0–1), `source` (detector name), `priority` (integer), and an optional `link`. |
| **Surface** | The exact substring a span covers. |
| **Key** | The normalised surface used to decide that two surfaces are the same entity (section 4.2). |
| **Link** | An opaque id a detector puts on spans whose different surfaces name one entity, such as a dictionary term and its aliases (`dictionary:7`). It never contains text. |
| **Variant** | One surface of a link group after the first, marked by a letter from `B` to `Z` in its placeholder (`[ORG_1_B]`). |
| **Placeholder** | The replacement text, `[TYPE_N]` or `[TYPE_N_V]`, with `N` starting at 1 per type and `V` the variant letter. |
| **Mapping** | The per-request table placeholder ↔ canonical surface. |
| **Slot** | One string inside a JSON payload that the gateway processes, with a way to read and write it. |

## 3. Detection

### 3.1 Detector interface

```python
class Detector(Protocol):
    name: str
    def detect(self, text: str) -> list[Span]: ...
```

Detectors are synchronous, pure and stateless with respect to the text (the same input gives the
same output). They may be slow (the ML one is), so the proxy runs detection in a worker thread.
A detector that fails raises; it never returns a partial result silently.

```python
@dataclass(frozen=True, slots=True)
class Span:
    start: int
    end: int
    entity_type: str
    score: float
    source: str
    priority: int
    link: str | None = None
```

`link` groups the spans of different surfaces that name one entity (section 4.2). It is chosen
by the detector: the detector's name, a colon and a decimal number, as in `dictionary:7`. It must
match `[a-z][a-z0-9_]*:[0-9]{1,9}`, or construction raises `ValueError` without repeating the
value, so a link cannot carry text. Only the dictionary sets links, and only for entries with
aliases. Rule, ML and literal spans have none. The detection cache stores spans with their link.

### 3.2 Layers and priorities

| Priority | Layer | Module | Source of truth |
|---|---|---|---|
| 100 | Placeholder literals | `detectors/literal.py` | Text that already looks like a placeholder (section 4.4) |
| 80 | Company dictionary | `detectors/dictionary.py` | User-provided terms and their aliases |
| 60 | Rules | `detectors/rules.py` | Regex plus checksum or library validation |
| 40 | ML | `detectors/gliner.py` | Local NER model, optional extra |
| 20 | Propagation | inside `engine.py` | Values already detected elsewhere in the same request |

An **allow list** (terms never to pseudonymise, for example the deploying company's own name) is
applied after detection: any span whose key equals an allow-list key is dropped.

The **ML layer** (`detectors/gliner.py`) cuts long texts into overlapping windows
(`detectors/chunking.py`). A window holds at most `window_words` words as the model counts them
(a run of word characters, or one punctuation mark), because GLiNER silently drops every word
after its input limit. A window longer than the model accepts is an error, not a truncation,
and the factory runs one whole window at start-up to prove the configured size fits. Results
from overlapping windows are deduplicated; overlapping spans are left to the resolver.

### 3.3 Overlap resolution (`resolve.py`)

Input: all spans for one text. Output: a list of non-overlapping spans sorted by `start`.

1. Drop spans where `start >= end`.
2. Sort by `priority` descending, then `score` descending, then length descending, then `start`
   ascending, then `entity_type` ascending, then `source` ascending, then `link` ascending, no
   link first (the last three keys only make the order total, so the result does not depend on
   the order of the input).
3. Walk the sorted list; accept a span if it does not overlap any accepted span. Two spans
   overlap when `a.start < b.end and b.start < a.end`.
4. Return accepted spans sorted by `start`.

There is no merging and no trimming. A lower-priority span that partially overlaps a
higher-priority one is discarded entirely.

## 4. Pseudonymisation engine (`engine.py`)

### 4.1 Batch contract

The engine receives **all** the texts of one request as an ordered list and returns the
pseudonymised texts in the same order plus the mapping:

```python
class Pseudonymizer:
    def __init__(self, detectors: Sequence[Detector], *, allow_list: Iterable[str] = (),
                 propagate: bool = True, min_propagation_length: int = 3) -> None: ...
    def pseudonymize(self, texts: Sequence[str]) -> PseudonymizedBatch: ...
    def analyze(self, texts: Sequence[str]) -> list[list[Span]]: ...  # spans, not replaced
    @property
    def detectors(self) -> tuple[Detector, ...]: ...   # in run order, the literal one first

@dataclass(frozen=True, slots=True)
class PseudonymizedBatch:
    texts: tuple[str, ...]
    mapping: Mapping
    stats: BatchStats

@dataclass(frozen=True, slots=True)
class BatchStats:                       # for metrics: counts and timings, never text
    entities_by_type_and_source: Mapping[tuple[str, str], int]  # replaced occurrences
    detector_seconds: Mapping[str, float]                        # by detector name
```

`entities_by_type_and_source` counts every replaced occurrence by its entity's type and the
detector that found it, `"propagation"` for propagated ones.

`analyze` returns, for each text, the spans `pseudonymize` replaces, sorted by `start`, without
replacing them. Each span keeps its detector's score, source and priority but carries the type
of the entity it is replaced as (that of the entity's first occurrence); propagated spans have
source `"propagation"`. Both methods are views of one private analysis (detection, placeholder
assignment, propagation), so they cannot disagree. The evaluation harness uses `analyze`. The mapping and its entities
cannot be pickled or copied, and their state cannot be read (security invariant 3).

Processing the request as one batch is what makes consistency and determinism possible: the
numbering depends only on the content and order of the texts.

### 4.2 Keys

```python
def normalize_key(surface: str) -> str:
    # NFKC, then casefold, then collapse every whitespace run to one space, then strip.
```

Two surfaces with the same key are the same entity and share one placeholder. The entity type
and the canonical surface are those of the **first** occurrence in batch order. Consequence: if
"ACME" and "Acme" both occur, both become `[ORG_1]` and both are restored as the first form
seen. This loss of case variation is accepted and documented.

**Link groups.** Surfaces with different keys can name one entity: a dictionary term and its
aliases. Their spans carry the same link, and the mapping groups them by `(link, entity_type)`.
Each surface of a group is still its own entity, with its own key, canonical surface and
placeholder, but they share a number and are told apart by a variant letter:

```python
@dataclass(frozen=True, slots=True)
class Entity:
    key: str = field(repr=False)
    entity_type: str
    surface: str = field(repr=False)
    placeholder: str
    variant: str | None = None  # None for the first surface of a group and for unlinked entities

class Mapping:
    def assign(self, entity_type: str, surface: str, *, key: str | None = None,
               link: str | None = None) -> Entity: ...
    def lookup(self, entity_type: str, number: int,
               variant: str | None = None) -> str | None: ...
```

`assign` decides in this order:

1. **Known key.** The key already has an entity: return it unchanged, whatever `link` and
   `entity_type` are. The entity does not join the link's group.
2. **No link.** Create an entity with the next number for the type and no variant.
3. **New group.** The group `(link, entity_type)` has no entity yet: create an entity with the
   next number and no variant, and record the number for the group.
4. **Known group.** The group has used fewer than 25 variants: create an entity with the
   group's number and the next unused letter of `B` to `Z`.
5. **Full group.** The group has used all 25 letters: create an entity with the next number and
   no variant, outside the group. Nothing leaks; the surface is only not linked.

A link never joins entities of different types. `lookup` without a variant returns the surface
of the entity that has no letter, and with a variant the surface of that variant; both return
None when the placeholder is not in the mapping. `entities()` and `len()` include variants,
because every placeholder is one entity. Link ids are kept only as keys of the mapping's group
table and appear in no repr.

### 4.3 Algorithm

Given `texts[0..n)`:

1. **Detect.** For each text, run every detector, concatenate the spans, drop allow-listed
   spans, resolve overlaps (section 3.3).
2. **Collect entities and assign placeholders.** Walk the texts in order and the resolved spans
   by `start`. For each span compute the key; the first time a key is seen, record the entity
   `(key, entity_type, surface)` and assign it `[TYPE_N]`, where `N` is the next unused number
   for that type, or, for a span with a link, the placeholder the link's group gives it
   (section 4.2). Numbering and variant letters therefore follow the order of first
   **detection**, and propagation (next step) never influences them. `LITERAL` spans keep their
   exact key and ignore links.
3. **Propagate** (if enabled). For each recorded entity whose key length is at least
   `min_propagation_length`, build a pattern from its canonical surface with
   `build_term_pattern` (`terms.py`): split the surface on whitespace, `re.escape` each token,
   join with `\s+`, wrap in `(?<!\w)` and `(?!\w)`, compile with `re.IGNORECASE`. Search every
   text. Each match that does not overlap an already resolved span is added as a span with
   `source="propagation"`, `priority=20`, `score=1.0`, the entity's type, and a direct reference
   to the entity (do not re-derive the key from the matched text). Process entities longest
   canonical surface first, so "Juan Pérez García" wins over "Juan Pérez" where both match, and
   skip a match that overlaps a propagation span accepted earlier.
4. **Replace.** For each text, replace spans from the last to the first so offsets stay valid.

Determinism requirements, each covered by a property test:

- Calling `pseudonymize` twice with the same texts and the same detectors returns identical
  output.
- **Append stability.** For batches `A` and `A + B`, every entity in the mapping of `A` has the
  same placeholder, variant and canonical surface in the mapping of `A + B`. With propagation disabled,
  the pseudonymised texts of `A` are also identical. (With propagation enabled, a text of `A`
  can change only by having more values replaced, when `B` reveals an entity that `A` contained
  undetected.) This keeps provider prompt caches valid across the turns of a conversation.

### 4.4 Placeholder literals

If the input already contains text that looks like a placeholder (a user pastes `[PERSON_1]`),
restoration would wrongly replace it. `LiteralDetector` reports every match of the tolerant
placeholder pattern (section 5.2) as a span of type `LITERAL` with priority 100. It is then
pseudonymised like anything else (`[PERSON_1]` in the input becomes `[LITERAL_1]`, restored to
the original text afterwards). `LiteralDetector` is always enabled and cannot be configured off.

`LITERAL` entities are **exact**: their key is the surface itself, without normalisation
(`[PERSON_1]` and `[person_1]` in the input are two different literals), so they always restore
byte for byte. They are never propagated, never allow-listed, and skipped by the leak audit.

### 4.5 Detection cache

Conversations resend their whole history on every turn. `CachedDetector` (`detectors/cache.py`)
wraps an expensive detector with a bounded, thread-safe LRU cache keyed by
`sha256(fingerprint + text)`, where the fingerprint identifies the detector configuration. The
cache stores spans, never text, and lives in process memory only.

## 5. Placeholders and restoration

### 5.1 Canonical format (`placeholders.py`)

`[TYPE_N]`: an opening square bracket, the entity type, an underscore, a positive decimal number
without leading zeros, a closing square bracket. A variant (section 4.2) adds an underscore and
one upper-case letter from `B` to `Z` before the closing bracket: `[ORG_1_B]`. ASCII only.
Maximum length is bounded: `MAX_PLACEHOLDER_LENGTH = 64`; entity types longer than 40
characters are rejected at configuration time. The longest placeholder with a number of nine
digits and a variant has 54 characters.

```python
VARIANT_LETTERS = "BCDEFGHIJKLMNOPQRSTUVWXYZ"
def format_placeholder(entity_type: str, number: int, variant: str | None = None) -> str: ...
def parse_match(match: re.Match[str]) -> tuple[str, int, str | None]: ...  # type, number, variant
```

### 5.2 Tolerant pattern

Models sometimes alter placeholders slightly. Restoration accepts these variants:

| Variant | Example |
|---|---|
| Letter case | `[person_1]` |
| A space instead of the last underscore | `[PERSON 1]` |
| Up to two spaces or tabs inside the brackets | `[ PERSON_1 ]` |
| Markdown-escaped underscores | `[CREDIT\_CARD\_1]` |
| Markdown-escaped brackets, **both** of them | `\[PERSON_1\]` |
| The same forms for the variant letter | `[org_1 b]`, `[ORG\_1\_B]` |

```python
_BODY = (
    r"[ \t]{0,2}([A-Za-z](?:\\?_|[A-Za-z]){0,39}?)(?:\\?_| )(\d{1,9})"
    r"(?:(?:\\?_| )([A-Za-z]))?[ \t]{0,2}"
)
TOLERANT = re.compile(rf"\\\[{_BODY}\\\]|\[{_BODY}\]")
MAX_TOLERANT_LENGTH = 128   # no match of TOLERANT can be longer than this
```

The pattern has two alternatives (escaped brackets, plain brackets), so the type, number and
variant are in groups 1–3 or 4–6. The variant is optional and accepts an underscore, a
Markdown-escaped underscore or one space before it, and either case. It accepts every letter,
`A` included: `[ORG_1_A]` is then found and counted as unresolved, where an unmatched form would
pass unseen. Every match of the pattern without a variant is still a match, at the same offsets.
The pattern also finds bracketed text that ends in a separator and one letter after a number,
such as `[Step 1 a]`; in a request that text becomes a `LITERAL` like any other
placeholder-shaped text. The longest match is 101 characters. A backslash before the opening bracket is consumed only when the closing
bracket is escaped too. This matters: the input `\Alice` becomes `\[PERSON_1]`, and restoring it
must give back `\Alice`, not `Alice`. Every quantifier is bounded so that a match can never
exceed `MAX_TOLERANT_LENGTH`; the stream restorer relies on that bound.

A match is restored only if `(type, number, variant)` exists in the mapping, where the type is
the matched text with backslashes removed and upper-cased, and the variant is upper-cased. Any
other match, an unknown variant of a known number such as `[ORG_1_F]` included, is left exactly
as it is and counted as an *unresolved placeholder* (a metric, section 8).

This pattern and the hold-back algorithm of §5.4 were prototyped and checked with 200,000 random
chunked inputs before this document was written, and the any-chunking property was run again
with 20,000 examples when the variant was added (slice 13). Do not "simplify" either without
re-running the property test.

### 5.3 `restore`

```python
def restore(text: str, mapping: Mapping, *, json_escape: bool = False) -> str: ...
```

Replaces every resolvable tolerant match with the canonical surface. With `json_escape=True` the
surface is inserted as `json.dumps(surface, ensure_ascii=False)[1:-1]`; this mode is for text
that is itself the inside of a JSON document (tool-call arguments).

Round-trip property: for any texts, restoring the pseudonymised texts yields the original texts,
except that case and whitespace variants of the same entity are rendered as its canonical
surface. Each surface of a link group is its own entity, so a dictionary term and its aliases
are each restored as written.

### 5.4 Streaming (`stream.py`)

```python
class StreamRestorer:
    def __init__(self, mapping: Mapping, *, json_escape: bool = False) -> None: ...
    def feed(self, chunk: str) -> str: ...   # text that is safe to emit now (may be "")
    def flush(self) -> str: ...              # remaining text at end of stream
```

Algorithm for `feed`:

1. Append `chunk` to the internal buffer.
2. Find `i`, the index of the last `[` in the buffer such that no `]` occurs after it. If there
   is none, the hold position is the end of the buffer.
3. If `i` exists and `len(buffer) - i <= MAX_TOLERANT_LENGTH`, the hold position is `i`, or
   `i - 1` when the character before it is a backslash. If the tail is longer than the maximum,
   it cannot be a placeholder: the hold position is the end of the buffer.
4. If the hold position is the end of the buffer and the buffer ends with a backslash, the hold
   position moves back by one (it may be the start of `\[`).
5. Emit `restore(buffer[:hold])` and keep `buffer[hold:]`.

`flush` returns `restore(buffer)` and empties the buffer. `unresolved` counts the tolerant
matches left unrestored in the text emitted so far.

Required property (tested with Hypothesis): for every text and every way of cutting it into
chunks, the concatenation of all `feed` results plus `flush` equals `restore(text)`.

## 6. Dialects

A dialect translates one provider API into slots and back. It contains no HTTP.

```python
class Dialect(Protocol):
    name: str
    def prepare_request(self, body: dict[str, Any], policy: Policy) -> PreparedRequest: ...
    def restore_response(self, body: dict[str, Any], mapping: Mapping) -> None: ...
    def stream_transformer(self, mapping: Mapping) -> StreamTransformer: ...
    def error_body(self, status: int, code: str, message: str) -> dict[str, Any]: ...
```

`prepare_request` walks the request body in a fixed, documented order and returns a
`PreparedRequest`: the ordered slots plus finalisers (for example, re-serialising tool-call
arguments that were parsed from a JSON string). It raises `UnsupportedContentError` when it meets
content the gateway cannot inspect and the policy is `block`. It also removes end-user
identifiers when the policy says so. The proxy calls the engine with `prepared.texts()` and
writes the results back with `prepared.apply(new_texts)`, which sets every slot and then runs
the finalisers.

Slot order is part of the contract, because placeholder numbering follows it. The order is
always: system content first, then messages in array order, and inside a message its parts in
array order, then tool definitions last (so adding a tool does not renumber the conversation).

### 6.1 What is processed

| Content | Request (pseudonymise) | Response (restore) |
|---|---|---|
| System prompt and message text | yes | yes |
| Tool results | yes | n/a |
| Tool-call arguments | yes: parse the JSON, process every string value, serialise with `json.dumps(obj, ensure_ascii=False, separators=(",", ":"))`; if parsing fails treat the raw string as text | yes: `restore(..., json_escape=True)` on the raw JSON string, or plain `restore` on every string value when the API delivers a parsed object |
| Tool definitions | only values under a `description` key (policy `tool_definitions`, default on); names, parameter names and enum values are never changed | n/a |
| Model reasoning with provider signatures (Anthropic `thinking`, `redacted_thinking`) | passed through unchanged | passed through unchanged (the user sees placeholders in reasoning; changing it would break the signature on the next turn) |
| Images, audio, documents, files | rejected by default (policy `unsupported_content: block`); `pass` forwards them unchanged | n/a |
| End-user identifiers (`user`, `safety_identifier`, `metadata.user_id`) | removed by default (policy `strip_user_identifiers`) | n/a |
| Log probabilities, usage, ids | untouched | untouched |

### 6.2 Supported endpoints in v1

| Route | Dialect | Upstream default |
|---|---|---|
| `POST /v1/chat/completions` | `openai_chat` | `https://api.openai.com` |
| `POST /v1/messages` | `anthropic_messages` | `https://api.anthropic.com` |
| `POST /v1/messages/count_tokens` | `anthropic_messages` (request side only) | same |

`count_tokens` is request side only: its body is pseudonymised like a message request, a
`stream` field in it is not honoured, and a successful answer, which holds only token counts, is
returned unchanged. Its error answers are restored like those of any other route.

Any other path returns 404 unless it is listed in `passthrough_paths` as
`"<upstream> <METHOD> <path>"`, for example `"anthropic GET /v1/models"`, which forwards it
unchanged to that upstream. A method and path may appear in only one entry, so that the path
alone names the upstream. The path is made of non-empty segments of letters, digits and
`-._~!$&'()*+,;=:@`, with no `.` or `..` segment: requests match on their decoded path and are
forwarded with the entry's path, and only a path that needs no escaping is the same text in both. Unknown endpoints are never forwarded by default, because an endpoint
the gateway does not understand cannot be pseudonymised.

Errors the gateway answers itself use the error shape of the route's dialect; a passthrough
entry uses the dialect of its upstream. A request that matches no route gets the Anthropic shape
when it carries an `anthropic-version` or `x-api-key` header, and the OpenAI shape otherwise.

The route path is appended to the upstream's `base_url`, which may end in a base path (§7). The
routes the client sees do not change: with `base_url: https://<resource>.openai.azure.com/openai`,
`POST /v1/chat/completions` is forwarded to
`https://<resource>.openai.azure.com/openai/v1/chat/completions`. Likewise Claude in Microsoft
Foundry serves the Messages API under `/anthropic/v1/`, so its `base_url` is
`https://<resource>.services.ai.azure.com/anthropic`.

## 7. Proxy

- Starlette application, `httpx.AsyncClient` for the upstream, `uvicorn` to serve.
- The gateway holds no provider credentials by default. `Authorization`, `x-api-key` and
  provider headers are forwarded as received. Hop-by-hop headers, `host` and `content-length`
  are not forwarded. `accept-encoding` is not forwarded; httpx negotiates its own.
- Request flow: parse JSON → `prepare_request` → engine (in a worker thread) → write slots →
  leak audit (in a worker thread, §8) → forward → restore (whole body, or stream transformer)
  → respond.
- **Upstream URL.** A request for path `P` is forwarded to `{base_url}{P}`, followed by the
  client's query string. `base_url` may end in a base path, for providers that serve the API
  below a prefix (Azure OpenAI serves it only under `/openai/v1/`). Configuration validation
  makes plain concatenation exact, so that `{base_url}{P}` is the URL httpx sends: no query,
  fragment, credentials, whitespace or trailing slash; path segments non-empty, in RFC 3986
  path characters, with `%XX` escapes sent as written; no `.` or `..` segment, plain or
  percent-encoded, because httpx or the upstream would resolve it. The full rules are in
  [plan/slice-04-config-and-dictionary.md](plan/slice-04-config-and-dictionary.md).
- Upstream error responses (status ≥ 400) are passed through with `restore` applied to string
  values of the JSON body, because providers sometimes echo request content in error messages.
- **Fail closed.** Any exception between parsing and forwarding results in an error response in
  the dialect's error shape and nothing is forwarded. Any exception while restoring a
  non-streamed response results in a 502; the un-restored body is never returned as if it were
  correct. A failure in the middle of a stream ends the stream: the client sees it truncated,
  and text the stream restorer still holds is never sent.
- **Streams are always closed.** However a streamed response ends (completion, a failure, the
  client going away, or the request task being cancelled), the upstream response is closed, so
  the provider stops generating, and the request is logged once, when the stream ends.

### 7.1 Logging rule

No log line, metric label, exception message or trace attribute may contain request or response
text, a surface, or a mapping. Logs carry counts, entity types, durations, status codes and
request ids. This rule has a test: a request with known synthetic values is sent through the
proxy with DEBUG logging and the captured log output must not contain any of the values.

Third-party loggers are part of the rule. `aliasgate serve` disables uvicorn's access log,
which would print every path and query string, and keeps the `httpx` and `httpcore` loggers at
WARNING or above, because they log upstream URLs (with the client's query string) and headers.
Exceptions are logged by class name only, and the JSON formatter never renders a traceback.

## 8. Safety checks (`audit.py`) and metrics (`metrics.py`)

| Check | When | On failure |
|---|---|---|
| **Outbound leak audit**: for every non-`LITERAL` entity in the mapping whose key length is ≥ `min_propagation_length`, no **string value anywhere in the outbound body** (not only the slots; keys are not checked) contains it, either by the propagation pattern of §4.3 step 3 (word-bounded, case-insensitive) or by its key in the NFKC-normalised, case-folded string | after the slots are written, before forwarding | reject with 500 `leak_audit_failed`, nothing is forwarded, metric `aliasgate_leak_audit_failures_total` |
| **Unresolved placeholders**: tolerant matches in the response not present in the mapping | after restoring | pass through unchanged, metric `aliasgate_unresolved_placeholders_total` |

The audit searches both ways because the two notions of "the same value" differ. The term
pattern uses simple case folding, so it misses variants that share the entity's key: `STRASSE`
for `Straße`, a decomposed `é`, full-width letters. Propagation misses them too, so the audit
rejects such a request instead of forwarding it. The pattern also matches some values whose
keys differ, such as `İvan` for `Ivan`, and those are reported as well. A combined alternation
of all entities decides in one pass per string whether anything leaked; each entity is
searched on its own only to count a failure. The audit is on by default (`policy.leak_audit`).

Metrics live in a registry per application and are served at `GET /metrics` when
`metrics.enabled` (the default), on the gateway's port. Every label value is chosen by the
gateway: routes (`unknown` for an unrouted request), status classes, outcomes, error codes,
detector names and entity types from the configuration. None is taken from a request.

| Metric | Type | Labels |
|---|---|---|
| `aliasgate_requests_total` | counter | `route`, `status_class`, `outcome` |
| `aliasgate_entities_total` | counter | `entity_type`, `source` |
| `aliasgate_unresolved_placeholders_total` | counter | `route` |
| `aliasgate_leak_audit_failures_total` | counter | `route`, `entity_type` |
| `aliasgate_rejections_total` | counter | `route`, `code` |
| `aliasgate_detection_seconds` | histogram | `detector` |
| `aliasgate_request_overhead_seconds` | histogram | `route`, `phase` (`pseudonymize`, `restore`) |
| `aliasgate_detection_cache_total` | counter | `result` (`hit`, `miss`); only with a cached detector |

`outcome` is `ok`, `rejected` (the gateway refused the request: invalid input, policy, the
leak audit), `upstream_error` (the provider answered an error, failed or timed out) or
`gateway_error` (the gateway failed). `rejections_total` counts every error response the
gateway produced itself, by its error code. Every response carries `x-aliasgate-entities`,
the number of entities in the request's mapping, which is the number of distinct placeholders,
variants included (0 when nothing was pseudonymised).

## 9. Configuration

One YAML file plus environment overrides (`ALIASGATE_` prefix, `__` for nesting), validated with
pydantic. Invalid configuration stops the process at start-up. Sections merge key by key, the
environment winning over the file; any other value, lists and mappings such as `ml.labels`
included, is replaced as a whole. The complete schema is specified
in [plan/slice-04-config-and-dictionary.md](plan/slice-04-config-and-dictionary.md). Upstream
base URLs may carry a base path (§7).

## 10. Decisions and rejected alternatives

| Decision | Rejected alternative | Reason |
|---|---|---|
| Per-request in-memory mapping | Persistent vault | Nothing reversible at rest; chat APIs resend history. Vault only needed for stateful APIs (backlog). |
| Numbered placeholders by first appearance | HMAC-derived ids | Shorter, readable for the model, no key to manage. HMAC ids become useful with a vault (backlog). |
| Typed placeholders | Realistic surrogates | Safe, auditable reversal. Surrogates opt-in later. |
| ASCII `[TYPE_N]` | `<TYPE_N>`, `{{TYPE_N}}`, `⟦TYPE_N⟧` | Angle brackets are stripped by HTML renderers, braces collide with templates, non-ASCII brackets tokenise badly and get normalised by models. |
| Variant placeholders for linked surfaces (`[ORG_1]`, `[ORG_1_B]`), letters by first appearance | One placeholder per entity, restored as its first surface; `[ORG_1B]`; one fixed letter per alias | Each surface restores to itself, so the round trip stays lossless and tool arguments keep the user's wording. `[ORG_1B]` reads as another number. Fixed letters would reveal how many aliases the dictionary holds and break the first-appearance rule. |
| Python | Go or Rust | The detection ecosystem (GLiNER, Presidio, transformers) is Python. Detection dominates latency, not the proxy. |
| Starlette + httpx | LiteLLM plugin only | Independent correctness; adapter possible later. |
| Reject unknown endpoints and uninspectable content | Forward unchanged | Fail closed is the only safe default for this product. |
| A base path in `base_url`, followed by the request path unchanged | Rewriting paths per route, or one upstream URL per endpoint | One prefix covers the known case (Azure OpenAI's `/openai`), and `{base_url}{path}` can be checked at start-up. Per-route rewriting would be a second routing table to get wrong. |
