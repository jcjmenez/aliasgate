# Slice 08: Anthropic Messages dialect

## Goal

`POST /v1/messages` (streaming and non-streaming) and `POST /v1/messages/count_tokens` work
through the gateway with the same guarantees as the OpenAI route. Anthropic SDKs and tools that
speak this API (including coding agents) can be pointed at the gateway.

## Depends on

Slice 07.

## Dependencies to add

None.

## Files

| File | Action |
|---|---|
| `src/aliasgate/dialects/anthropic_messages.py` | create |
| `src/aliasgate/proxy/app.py` | change: register routes, choose upstream per dialect |
| `tests/dialects/test_anthropic_messages.py`, `tests/dialects/test_anthropic_stream.py`, `tests/proxy/test_app_anthropic.py` | create |
| `tests/dialects/fixtures/anthropic/*.json` | create |

## Before you start

Read the current Anthropic API reference for the Messages API: request body, content block
types, response body and streaming events. Record the date in the pull request. The block types
listed below were correct when this plan was written; the API adds new ones regularly. Any type
not listed is handled by the "unknown block" rule, which is safe by default.

## Specification

### Request (`prepare_request`)

Slot order:

1. `body["system"]`: a string → one slot; a list of blocks → a slot per `text` block.
2. For each message in `body["messages"]`: `content` is a string → one slot; a list → for each
   block by `type`:

   | Block type | Handling |
   |---|---|
   | `text` | slot on `block["text"]` |
   | `tool_use` | `slots_for_json_strings(block["input"])` (it is already an object; no finalizer needed) |
   | `tool_result` | `content` string → one slot; list → apply this same table to each inner block |
   | `thinking`, `redacted_thinking` | no slot. Passed through untouched (architecture §6.1). |
   | `image`, `document` | unsupported: `block` → `UnsupportedContentError(kind=type)`; `pass` → skip |
   | anything else (server tool blocks, search results, future types) | unsupported, same rule |

3. If `policy.tool_definitions`: for each entry of `body["tools"]`, every string value under a
   key named `"description"`, depth-first. `slots_for_key_strings(body["tools"], "description")`
   in `base.py` (slice 05) does exactly this.

(Found in slice 05: `UnsupportedContentError` replaces a kind that is not a short lower-case
label with `"unknown"`, so the block `type` taken from the request never reaches an error
message or a log. The OpenAI dialect also raises `MalformedRequestError`, naming the position
only, whenever a field it walks has the wrong shape (a part without a string `type`, a `text`
that is not a string), so that nothing it cannot read is forwarded. Do the same here.)

Identifier stripping: delete `body["metadata"]["user_id"]` when `strip_user_identifiers`; if
`metadata` becomes empty, delete it.

**Changed in [slice 20](slice-20-request-fields.md).** Under `tool_definitions`, the strings of
each tool's `input_examples` and the descriptions inside `output_config.format` follow the tool
descriptions; `stop_sequences` are slots after them; the `stop_sequence` of a response and of a
`message_delta` event is restored; and every tool's `user_location` is removed under
`policy.strip_user_location`.

Why thinking blocks are not touched: they carry a provider signature over their exact content
and must be sent back unchanged on later turns. Since the model only ever saw placeholders, a
thinking block contains placeholders, not real values. The gateway does not restore them in the
response and does not alter them in the request. The leak audit of slice 10 still scans them.

`MalformedRequestError` when `messages` is missing or not a list.

### Response (`restore_response`)

For each block in `body["content"]`:

- `text` → `restore` on `text`. If the block has `citations`, leave them unchanged.
- `tool_use` → replace `input` with a copy where `restore` is applied to every string value
  (`restore_all_strings`).
- everything else, including `thinking` → unchanged.

### Streaming (`AnthropicStreamTransformer`)

(Found in slice 07: the proxy's `_RelayResponse` works with any `StreamTransformer`, so this
slice only needs the transformer. Reuse `SSEParser`, `SSEEvent` and `StreamRestorer`, and keep
each event's `event` field, which Anthropic streams rely on. The OpenAI transformer returns an
unchanged event as the original object; doing the same here keeps upstream bytes intact.)

Events are named (`event:` field) and carry a JSON `data` with a `type`. Keep one
`StreamRestorer` per content block `index`.

| Event type | Handling |
|---|---|
| `content_block_start` | Look at `content_block.type`. `text` → create a plain restorer; if `content_block.text` is a non-empty string, feed it and replace. `tool_use` → create a `json_escape=True` restorer; if `content_block.input` is a non-empty object, replace it with `restore_all_strings`. Other types → no restorer. |
| `content_block_delta` | By `delta.type`: `text_delta` → feed and replace `delta.text`; `input_json_delta` → feed and replace `delta.partial_json`; `thinking_delta`, `signature_delta`, `citations_delta` and unknown → unchanged. |
| `content_block_stop` | Flush the restorer of that index. If non-empty, emit **before** this event a synthetic `content_block_delta` (event name `content_block_delta`) with the same `index` and a `text_delta` or `input_json_delta` carrying the flushed text. Then discard the restorer. |
| `message_start`, `message_delta`, `message_stop`, `ping`, `error`, unknown | unchanged |

`end()` flushes any remaining restorer as synthetic `content_block_delta` events.

Re-serialise modified events with compact separators and `ensure_ascii=False`, keeping the
`event` field.

### `error_body`

```python
{"type": "error", "error": {"type": code_family, "message": message}}
```

Map gateway codes to Anthropic error types: 400 and 422 → `invalid_request_error`, 404 →
`not_found_error`, 413 → `request_too_large`, everything else → `api_error`. Append the gateway
code to the message in parentheses, for example `"Unsupported content: image (unsupported_content)"`.

### Proxy changes

- `POST /v1/messages` → dialect `anthropic_messages`, upstream
  `settings.upstreams.anthropic.base_url`. (Found in slice 06: routes are entries of
  `_Gateway._routes`, keyed by `(method, path)`, each a `_DialectRoute(dialect, base_url, path)`.
  Errors use the route's dialect, and dialect routes always win over passthrough entries.)
- `POST /v1/messages/count_tokens` → same request handling; the response is returned unchanged
  (it contains only numbers). A truthy `stream` is not applicable here.
- The 404 for unknown endpoints uses the Anthropic error shape when the request carries an
  `anthropic-version` or `x-api-key` header, otherwise the OpenAI shape.
- Passthrough paths need an upstream: extend the `passthrough_paths` entry format to
  `"<upstream> <METHOD> <path>"`, for example `"anthropic GET /v1/models"`. Update the
  validation in `config.py`, the example file and architecture §6.2 accordingly. This is a
  permitted contract change for this slice.

### Decisions made while implementing (slice 08)

- API check, 2026-11-18, on platform.claude.com (Messages API reference, streaming guide,
  errors page, Claude in Microsoft Foundry). Every block type and event listed above is still
  current. Newer request block types (`search_result`, `server_tool_use`, the `*_tool_result`
  blocks, `container_upload`, `compaction`, `fallback`) and the tool-result inner blocks
  `tool_reference` and `browser_state` fall under the unknown-block rule, so they are rejected
  under `block`. Streams also send `input_json_delta` for `server_tool_use` blocks; these blocks
  get no restorer and pass unchanged. Error bodies from the provider now carry a top-level
  `request_id`; the gateway's own errors have none. Mid-conversation `role: "system"` messages
  are walked like any other message, and their `content` may be `[]`.
- A `system` block that is not `text` follows the unsupported rule (the API accepts only text
  there). A `tool_use` whose `input` is not an object is malformed. A `tool_result` without
  `content`, or with `null`, has no slot. A `metadata` that is not an object is left for the
  provider to reject, and an empty `metadata` object is removed even when it held no `user_id`.
- Stream transformer:
  - A delta is restored only when its index has a restorer, created by a
    `content_block_start` of the matching kind (`text` with `text_delta`, `tool_use` with
    `input_json_delta`). Any other delta passes unchanged.
  - `message_stop` flushes blocks that were never stopped, as synthetic deltas just before it.
    Clients stop reading at `message_stop`, so text held until `end()` would be lost.
  - A second `content_block_start` for an index that is still open flushes the first block.
  - An `index` must be an integer, not a boolean.
  - `error` events pass unchanged, as specified. This differs from OpenAI streams, where error
    chunks are restored. They can show placeholders, never real values.
  - `unresolved` also counts restorers already discarded and the input of a `tool_use` start.
- `count_tokens` is a `_DialectRoute` with `request_only=True`. The body is forwarded as
  received, `stream` included, and is not treated as a stream. A successful answer is returned
  byte for byte, and an error is restored like on other routes.
- The 404 shape follows the headers, as specified. A passthrough entry's errors use the dialect
  of its upstream.
- Passthrough entries are `"<upstream> <METHOD> <path>"` with upstream `openai` or `anthropic`.
  A method and path may appear in only one entry, because the path alone must name the
  upstream. Both providers serve `GET /v1/models`, so only one of them can be passed through.
  The path, which used to be `/\S*`, must now be non-empty segments of letters, digits and
  `-._~!$&'()*+,;=:@`, with no `.` or `..` segment and no trailing slash (decided with the
  maintainer). Requests match on Starlette's decoded path and are forwarded with the entry's
  path. With no `%`, `?` or `#` allowed, the decoded path that matches is exactly the path
  that is sent. Before, `GET /v1/a?b` matched a request for `/v1/a%3Fb` and forwarded `b` as
  the query string.
- Manual check, 2026-11-18. The official Python SDK (`anthropic` 2.10.0) called the gateway,
  which forwarded to `claude-sonnet-5-5` on Claude in Microsoft Foundry, `base_url`
  `https://<resource>.services.ai.azure.com/anthropic`, with the key in `x-api-key`. A streamed
  turn with adaptive thinking (`display: "summarized"`) produced `thinking` (with a signature),
  `text` and `tool_use` blocks. The SDK parsed the restored tool input, and the provider only
  ever saw placeholders. The second turn sent the thinking block back unchanged with a
  `tool_result`, and the provider accepted it. `count_tokens` works on Foundry, and the Models
  API does not exist there. Intermittent TLS handshake failures from the test machine to Azure,
  which also happened without the gateway, came back as `502 upstream_unreachable`, and nothing
  was forwarded.
- Found while checking, not fixed in this slice:
  - Preserved thinking. Current models reject a replayed thinking block when anything before
    it has changed. Append stability keeps earlier turns identical, with three exceptions, each
    of which changes an earlier message on the next turn: propagation, when a later turn reveals
    an entity an earlier text contained undetected; a placeholder variant written by the model
    (`[person_1]`), which is restored and then re-pseudonymised as `[PERSON_1]`; and an
    unresolved placeholder in an assistant message (`[PERSON_9]`), which becomes `[LITERAL_1]`.
    Slice 12 documents this; the backlog has the fix.
  - Utility. In one run the model noticed the placeholders, refused to check the IBAN's
    country and called them "unfilled template placeholders". The user then saw that sentence
    with their real values restored into it (threat model, residual risk 6).

## Tests

`test_anthropic_messages.py`
- string and block-list `system`; string and block-list message content.
- `tool_use` input with nested strings → slots in depth-first order; values replaced in place.
- `tool_result` with string content, with a list of text blocks, and with an image inside
  (`block` → error with `kind == "image"`).
- `thinking` block with a `signature`: unchanged after a full pseudonymise cycle, byte for byte
  (compare JSON dumps).
- `image` and `document` blocks: `block` raises, `pass` skips.
- an unknown block type raises under `block`.
- tool descriptions are slots; tool names and `input_schema` property names are not.
- slot order test: system, then messages, then tools; assert `texts()` exactly.
- `metadata.user_id` removed; empty `metadata` removed.
- response: text restored; `tool_use.input` restored at every depth; `thinking` unchanged;
  `usage` and `stop_reason` unchanged.
- conversation round trip across two turns: the request of turn 2 contains the restored
  assistant text of turn 1; after pseudonymising, the earlier messages carry the same
  placeholders as in turn 1 (append stability at payload level).

`test_anthropic_stream.py`
- text block with a placeholder split across three `text_delta` events.
- held text at `content_block_stop` → synthetic delta before the stop, with the right index.
- `tool_use` block: `partial_json` fragments reassemble to JSON with restored, escaped values.
- interleaved indexes keep separate buffers.
- `thinking_delta` and `signature_delta` pass unchanged.
- `ping` and `error` events pass unchanged.
- property: random text, random chunking into `text_delta` events → concatenated output text
  equals `restore(text)`.

`test_app_anthropic.py`
- non-streaming happy path with respx; `x-api-key` and `anthropic-version` reach the upstream.
- streaming happy path with awkward byte cuts.
- image block → 422 in Anthropic error shape, zero upstream calls.
- `count_tokens` forwards a pseudonymised body and returns the numeric answer.
- unknown path with `anthropic-version` header → Anthropic-shaped 404.
- OpenAI and Anthropic routes go to their own upstream base URLs.

## Acceptance checklist

- [ ] API reference checked; date and any differences recorded in the pull request.
- [ ] Thinking blocks are provably untouched in both directions.
- [ ] Unknown block types are rejected under the default policy.
- [ ] Manual check with a real client in streaming mode with a tool call, synthetic data only.
- [ ] Definition of done in `docs/plan/README.md` is met.

## Out of scope

- Files API, Message Batches, provider-hosted tools' result blocks (rejected by default).
- OCR or parsing of images and documents (backlog).
- Bedrock and Vertex variants of the API (backlog).
