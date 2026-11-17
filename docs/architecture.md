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
| Core | `aliasgate.types`, `placeholders`, `mapping`, `resolve`, `engine`, `stream`, `detectors.*` | Strings and spans | JSON payloads, HTTP |
| Dialects | `aliasgate.dialects.*` | The JSON shape of one provider API | HTTP, sockets |
| Proxy | `aliasgate.proxy.*`, `config`, `cli`, `metrics` | HTTP, configuration, wiring | Detection internals |

The core has no third-party dependency except `phonenumbers` (and the optional ML extra). This
keeps it usable as a library and as a plugin for other gateways.

## 2. Vocabulary

| Term | Meaning |
|---|---|
| **Entity type** | Upper-case label matching `[A-Z][A-Z_]*` (letters and underscores, no digits). Built-in: `PERSON`, `ORG`, `ADDRESS`, `EMAIL`, `PHONE`, `IBAN`, `CREDIT_CARD`, `NATIONAL_ID`, `TAX_ID`, `IP_ADDRESS`, `URL`, `SECRET`, `LITERAL`. Users may add their own. |
| **Span** | A detected range in one text: `start`, `end` (Python slice offsets), `entity_type`, `score` (0–1), `source` (detector name), `priority` (integer). |
| **Surface** | The exact substring a span covers. |
| **Key** | The normalised surface used to decide that two surfaces are the same entity (section 4.2). |
| **Placeholder** | The replacement text, `[TYPE_N]`, with `N` starting at 1 per type. |
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

### 3.2 Layers and priorities

| Priority | Layer | Module | Source of truth |
|---|---|---|---|
| 100 | Placeholder literals | `detectors/literal.py` | Text that already looks like a placeholder (section 4.4) |
| 80 | Company dictionary | `detectors/dictionary.py` | User-provided terms |
| 60 | Rules | `detectors/rules.py` | Regex plus checksum or library validation |
| 40 | ML | `detectors/gliner.py` | Local NER model, optional extra |
| 20 | Propagation | inside `engine.py` | Values already detected elsewhere in the same request |

An **allow list** (terms never to pseudonymise, for example the deploying company's own name) is
applied after detection: any span whose key equals an allow-list key is dropped.

### 3.3 Overlap resolution (`resolve.py`)

Input: all spans for one text. Output: a list of non-overlapping spans sorted by `start`.

1. Drop spans where `start >= end`.
2. Sort by `priority` descending, then `score` descending, then length descending, then `start`
   ascending, then `entity_type` ascending, then `source` ascending (the last two keys only make
   the order total, so the result does not depend on the order of the input).
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

@dataclass(frozen=True, slots=True)
class PseudonymizedBatch:
    texts: tuple[str, ...]
    mapping: Mapping
```

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

### 4.3 Algorithm

Given `texts[0..n)`:

1. **Detect.** For each text, run every detector, concatenate the spans, drop allow-listed
   spans, resolve overlaps (section 3.3).
2. **Collect entities and assign placeholders.** Walk the texts in order and the resolved spans
   by `start`. For each span compute the key; the first time a key is seen, record the entity
   `(key, entity_type, surface)` and assign it `[TYPE_N]`, where `N` is the next unused number
   for that type. Numbering therefore follows the order of first **detection**, and propagation
   (next step) never influences it.
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
  same placeholder and canonical surface in the mapping of `A + B`. With propagation disabled,
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
without leading zeros, a closing square bracket. ASCII only. Maximum length is bounded:
`MAX_PLACEHOLDER_LENGTH = 64`; entity types longer than 40 characters are rejected at
configuration time.

### 5.2 Tolerant pattern

Models sometimes alter placeholders slightly. Restoration accepts these variants:

| Variant | Example |
|---|---|
| Letter case | `[person_1]` |
| A space instead of the last underscore | `[PERSON 1]` |
| Up to two spaces or tabs inside the brackets | `[ PERSON_1 ]` |
| Markdown-escaped underscores | `[CREDIT\_CARD\_1]` |
| Markdown-escaped brackets, **both** of them | `\[PERSON_1\]` |

```python
_BODY = r"[ \t]{0,2}([A-Za-z](?:\\?_|[A-Za-z]){0,39}?)(?:\\?_| )(\d{1,9})[ \t]{0,2}"
TOLERANT = re.compile(rf"\\\[{_BODY}\\\]|\[{_BODY}\]")
MAX_TOLERANT_LENGTH = 128   # no match of TOLERANT can be longer than this
```

The pattern has two alternatives (escaped brackets, plain brackets), so the type and number are
in groups 1–2 or 3–4. A backslash before the opening bracket is consumed only when the closing
bracket is escaped too. This matters: the input `\Alice` becomes `\[PERSON_1]`, and restoring it
must give back `\Alice`, not `Alice`. Every quantifier is bounded so that a match can never
exceed `MAX_TOLERANT_LENGTH`; the stream restorer relies on that bound.

A match is restored only if `(type, number)` exists in the mapping, where the type is the
matched text with backslashes removed and upper-cased. Any other match is left exactly as it is
and counted as an *unresolved placeholder* (a metric, section 8).

This pattern and the hold-back algorithm of §5.4 were prototyped and checked with 200,000 random
chunked inputs before this document was written. Do not "simplify" either without re-running
the property test.

### 5.3 `restore`

```python
def restore(text: str, mapping: Mapping, *, json_escape: bool = False) -> str: ...
```

Replaces every resolvable tolerant match with the canonical surface. With `json_escape=True` the
surface is inserted as `json.dumps(surface, ensure_ascii=False)[1:-1]`; this mode is for text
that is itself the inside of a JSON document (tool-call arguments).

Round-trip property: for any texts, restoring the pseudonymised texts yields the original texts,
except that case and whitespace variants of the same entity are rendered as its canonical
surface.

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

`flush` returns `restore(buffer)` and empties the buffer.

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

Any other path returns 404 unless it is listed in `passthrough_paths` (for example
`GET /v1/models`). Unknown endpoints are never forwarded by default, because an endpoint the
gateway does not understand cannot be pseudonymised.

## 7. Proxy

- Starlette application, `httpx.AsyncClient` for the upstream, `uvicorn` to serve.
- The gateway holds no provider credentials by default. `Authorization`, `x-api-key` and
  provider headers are forwarded as received. Hop-by-hop headers, `host` and `content-length`
  are not forwarded. `accept-encoding` is not forwarded; httpx negotiates its own.
- Request flow: parse JSON → `prepare_request` → engine (in a worker thread) → write slots →
  leak audit → forward → restore (whole body, or stream transformer) → respond.
- Upstream error responses (status ≥ 400) are passed through with `restore` applied to string
  values of the JSON body, because providers sometimes echo request content in error messages.
- **Fail closed.** Any exception between parsing and forwarding results in an error response in
  the dialect's error shape and nothing is forwarded. Any exception while restoring a
  non-streamed response results in a 502; the un-restored body is never returned as if it were
  correct.

### 7.1 Logging rule

No log line, metric label, exception message or trace attribute may contain request or response
text, a surface, or a mapping. Logs carry counts, entity types, durations, status codes and
request ids. This rule has a test: a request with known synthetic values is sent through the
proxy with DEBUG logging and the captured log output must not contain any of the values.

Third-party loggers are part of the rule. `python -m aliasgate` disables uvicorn's access log,
which would print every path and query string, and keeps the `httpx` and `httpcore` loggers at
WARNING or above, because they log upstream URLs (with the client's query string) and headers.
Exceptions are logged by class name only, and the JSON formatter never renders a traceback.

## 8. Safety checks (`audit.py`)

| Check | When | On failure |
|---|---|---|
| **Outbound leak audit**: for every entity in the mapping whose key length is ≥ `min_propagation_length`, the propagation pattern of §4.3 step 3 (word-bounded, case-insensitive) finds no match in any processed slot after replacement | before forwarding | reject with 500, metric `aliasgate_leak_audit_failures_total` |
| **Unresolved placeholders**: tolerant matches in the response not present in the mapping | after restoring | pass through unchanged, metric `aliasgate_unresolved_placeholders_total` |

## 9. Configuration

One YAML file plus environment overrides (`ALIASGATE_` prefix, `__` for nesting), validated with
pydantic. Invalid configuration stops the process at start-up. The complete schema is specified
in [plan/slice-04-config-and-dictionary.md](plan/slice-04-config-and-dictionary.md).

## 10. Decisions and rejected alternatives

| Decision | Rejected alternative | Reason |
|---|---|---|
| Per-request in-memory mapping | Persistent vault | Nothing reversible at rest; chat APIs resend history. Vault only needed for stateful APIs (backlog). |
| Numbered placeholders by first appearance | HMAC-derived ids | Shorter, readable for the model, no key to manage. HMAC ids become useful with a vault (backlog). |
| Typed placeholders | Realistic surrogates | Safe, auditable reversal. Surrogates opt-in later. |
| ASCII `[TYPE_N]` | `<TYPE_N>`, `{{TYPE_N}}`, `⟦TYPE_N⟧` | Angle brackets are stripped by HTML renderers, braces collide with templates, non-ASCII brackets tokenise badly and get normalised by models. |
| Python | Go or Rust | The detection ecosystem (GLiNER, Presidio, transformers) is Python. Detection dominates latency, not the proxy. |
| Starlette + httpx | LiteLLM plugin only | Independent correctness; adapter possible later. |
| Reject unknown endpoints and uninspectable content | Forward unchanged | Fail closed is the only safe default for this product. |
